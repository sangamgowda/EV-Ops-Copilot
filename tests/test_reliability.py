"""Phase 4 reliability: model fail-over with a circuit breaker, the
request queue in front of model calls, MCP reconnection, and the
data-quality guard. No network: the provider is a fake."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import openai
import pytest

from ops_copilot.llm import client as llm
from ops_copilot.llm.resilience import CircuitBreaker, ModelQueue, QueueTimeout


def _rate_limited() -> openai.RateLimitError:
    req = httpx.Request("POST", "https://provider/v1/chat/completions")
    return openai.RateLimitError("Rate limit reached", response=httpx.Response(429, request=req), body=None)


class _FakeProvider:
    """Answers per model: an exception to raise, or text to return."""

    def __init__(self, behaviour: dict[str, object]):
        self.behaviour, self.calls = behaviour, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, *, model, messages, **kw):
        self.calls.append((model, kw.get("extra_body")))
        out = self.behaviour[model]
        if isinstance(out, BaseException):
            raise out
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=out))], usage=usage)


@pytest.fixture
def provider(monkeypatch):
    """Strong = 'big', fallback = 'small' (a model without reasoning)."""
    fake = _FakeProvider({})
    settings = SimpleNamespace(llm_model_strong="big", llm_model_cheap="small-cheap", llm_model_judge="judge",
                               llm_fallback_model_strong="small", llm_fallback_model_cheap="")
    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    monkeypatch.setattr(llm, "model_for", lambda node: {"plan": "big", "router": "small-cheap",
                                                         "judge": "judge"}[node])
    monkeypatch.setattr(llm, "tier_for", lambda node: {"plan": "strong", "router": "cheap",
                                                        "judge": "judge"}[node])
    monkeypatch.setattr(llm, "_client", lambda role="primary": fake)
    monkeypatch.setattr(llm, "record_generation", lambda *a, **k: None)
    monkeypatch.setattr(llm, "_breakers", {})
    monkeypatch.setattr(llm, "_queues", {})
    return fake


class TestFailover:
    async def test_rate_limited_primary_falls_back(self, provider):
        provider.behaviour = {"big": _rate_limited(), "small": "from fallback"}
        assert await llm.complete("plan", "s", "u") == "from fallback"
        assert [m for m, _ in provider.calls] == ["big", "small"]

    async def test_fallback_is_not_sent_reasoning_effort_it_does_not_accept(self, provider, monkeypatch):
        cfg = llm.get_config()
        monkeypatch.setitem(cfg["llm"], "reasoning_effort_models", ["big"])
        provider.behaviour = {"big": _rate_limited(), "small": "ok"}
        await llm.complete("plan", "s", "u")
        assert provider.calls[0][1] == {"reasoning_effort": "low"} and provider.calls[1][1] is None

    async def test_breaker_opens_and_skips_the_failing_model(self, provider):
        provider.behaviour = {"big": _rate_limited(), "small": "ok"}
        threshold = llm.get_config()["llm"]["circuit_breaker"]["failure_threshold"]
        for _ in range(threshold):
            await llm.complete("plan", "s", "u")
        provider.calls.clear()
        await llm.complete("plan", "s", "u")
        assert [m for m, _ in provider.calls] == ["small"]          # 'big' not even tried
        assert llm.breaker("big").state == "open"

    async def test_bad_request_is_not_failed_over(self, provider):
        req = httpx.Request("POST", "https://provider")
        bad = openai.BadRequestError("bad", response=httpx.Response(400, request=req), body=None)
        provider.behaviour = {"big": bad, "small": "ok"}
        with pytest.raises(openai.BadRequestError):
            await llm.complete("plan", "s", "u")
        assert llm.breaker("big").failures == 0

    async def test_evaluation_never_uses_the_fallback(self, provider):
        provider.behaviour = {"big": _rate_limited(), "small": "ok"}
        with llm.primary_only(), pytest.raises(openai.RateLimitError):
            await llm.complete("plan", "s", "u")              # the provider's own error, for the rate-limit watch
        assert [m for m, _ in provider.calls] == ["big"]

    async def test_no_fallback_left_is_reported_as_unavailable(self, provider):
        provider.behaviour = {"small-cheap": _rate_limited()}
        with pytest.raises(llm.LLMUnavailable):
            await llm.complete("router", "s", "u")              # cheap tier has no fallback configured


class TestCircuitBreaker:
    def test_half_open_lets_one_trial_through(self, monkeypatch):
        from ops_copilot.llm import resilience
        now = [100.0]
        monkeypatch.setattr(resilience.time, "monotonic", lambda: now[0])
        cb = CircuitBreaker(failure_threshold=2, cooldown_s=30)
        cb.failure()
        cb.failure()
        assert cb.state == "open" and not cb.allow()
        now[0] += 31
        assert cb.allow() and not cb.allow()                     # one trial at a time
        cb.success()
        assert cb.state == "closed" and cb.allow()

    def test_failed_trial_reopens(self, monkeypatch):
        from ops_copilot.llm import resilience
        now = [0.0]
        monkeypatch.setattr(resilience.time, "monotonic", lambda: now[0])
        cb = CircuitBreaker(failure_threshold=1, cooldown_s=10)
        cb.failure()
        now[0] = 11
        assert cb.allow()
        cb.failure()
        assert cb.state == "open"


class TestQueue:
    async def test_concurrency_is_capped(self):
        q, running, peak = ModelQueue(max_concurrent=2, tokens_per_minute=10**6), 0, 0

        async def work():
            nonlocal running, peak
            async with q.slot(10, max_wait_s=5):
                running += 1
                peak = max(peak, running)
                await asyncio.sleep(0.01)
                running -= 1

        await asyncio.gather(*(work() for _ in range(6)))
        assert peak == 2

    async def test_full_token_budget_times_out_instead_of_calling_the_provider(self):
        q = ModelQueue(max_concurrent=4, tokens_per_minute=1000)
        async with q.slot(900, max_wait_s=1):
            pass
        with pytest.raises(QueueTimeout):
            async with q.slot(500, max_wait_s=0.05):             # 900 + 500 > 1000 for the next minute
                pass

    async def test_real_usage_replaces_the_estimate(self):
        q = ModelQueue(max_concurrent=4, tokens_per_minute=1000)
        async with q.slot(900, max_wait_s=1) as entry:
            entry[1] = 100                                       # the call actually used 100
        async with q.slot(800, max_wait_s=0.05):
            pass


# ── MCP reconnection ─────────────────────────────────────────

class _FakeServer:
    """Sessions numbered from 1. `broken` sessions fail every call;
    `slow` calls take a while and then succeed."""

    def __init__(self):
        self.opened, self.closed, self.broken = 0, 0, set()
        server = self

        class Session:
            def __init__(self, read, write):
                self.n = read

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                server.closed += 1

            async def initialize(self):
                pass

            async def call_tool(self, name, args):
                await asyncio.sleep(args.get("delay", 0))
                if self.n in server.broken:
                    raise ConnectionError(f"session {self.n} is gone")
                return SimpleNamespace(isError=False, content=[SimpleNamespace(text=f'{{"session": {self.n}}}')])

        self.Session = Session

    def transport(self):
        server = self

        class Transport:
            async def __aenter__(self):
                server.opened += 1
                return server.opened, None

            async def __aexit__(self, *exc):
                pass

        return Transport()


@pytest.fixture
def mcp(monkeypatch):
    from ops_copilot.mcp_client import client as mc
    server = _FakeServer()
    monkeypatch.setattr(mc, "ClientSession", server.Session)
    monkeypatch.setattr(mc.MCPClient, "_transport", lambda self: server.transport())
    cfg = mc.get_config()
    monkeypatch.setitem(cfg["mcp"], "retry_backoff_seconds", 0.01)
    c = mc.MCPClient(transport="http")
    return c, server


class TestMCPReconnect:
    async def test_one_outage_one_reconnect_no_failed_calls(self, mcp):
        client, server = mcp
        await client.connect()
        server.broken.add(1)                     # the connection dies under four concurrent calls
        outs = await asyncio.gather(*(client.call_tool("t", {}, "turn") for _ in range(4)))
        assert all(o["result"] == {"session": 2} for o in outs)
        assert server.opened == 2                # one reconnect, not four
        await client.close()

    async def test_call_in_flight_on_healthy_old_connection_finishes(self, mcp):
        client, server = mcp
        await client.connect()
        slow = asyncio.create_task(client.call_tool("t", {"delay": 0.2}, "turn"))
        await asyncio.sleep(0.05)
        await client._replace(client.generation)  # e.g. another call saw a transport error
        assert client.generation == 2
        assert (await slow)["result"] == {"session": 1}   # not cut off by the reconnect
        await asyncio.sleep(0.05)
        assert server.closed == 1                 # the retired connection closed after it finished
        await client.close()

    async def test_persistent_failure_still_raises(self, mcp):
        client, server = mcp
        server.broken.update(range(1, 20))
        with pytest.raises(ConnectionError):
            await client.call_tool("t", {}, "turn")
        await client.close()


# ── data-quality guard ───────────────────────────────────────

class TestDataQuality:
    @pytest.fixture
    def validator(self):
        from ops_copilot.settings import get_config, get_schema_config
        from ops_copilot.sql.validator import SQLValidator
        return SQLValidator(get_schema_config(), get_config())

    def test_flagged_readings_are_left_out(self, validator):
        r = validator.validate("SELECT avg(t.metric_value) FROM vehicle_telemetry t WHERE t.vehicle_id = 'V-1' "
                               "AND (t.metric_name = 'speed' OR t.metric_name = 'payload') "
                               "AND t.recorded_at >= now() - interval '7 days'")
        assert r.ok and "t.quality_flag IS NULL" in r.sql
        assert "(t.metric_name = 'speed' OR t.metric_name = 'payload')" in r.sql   # OR stays bracketed

    def test_outer_join_keeps_its_meaning(self, validator):
        r = validator.validate("SELECT v.vehicle_id, count(t.id) FROM vehicles v LEFT JOIN vehicle_telemetry t "
                               "ON t.vehicle_id = v.vehicle_id AND t.recorded_at >= now() - interval '1 day' "
                               "GROUP BY v.vehicle_id LIMIT 5")
        assert r.ok and "AND t.quality_flag IS NULL GROUP BY" in r.sql             # in ON, not WHERE

    def test_asking_about_faults_is_left_as_written(self, validator):
        r = validator.validate("SELECT t.quality_flag, count(*) FROM vehicle_telemetry t WHERE t.vehicle_id = 'V-1' "
                               "AND t.recorded_at >= now() - interval '7 days' GROUP BY 1")
        assert r.ok and "quality_flag IS NULL" not in r.sql

    def test_impossible_value_is_a_sensor_fault_not_evidence(self):
        from ops_copilot.agent.context import render_evidence
        from ops_copilot.agent.nodes.observe import _sql_evidence
        res = {"status": "ok", "rows": [{"metric": "motor_temp", "actual": -40, "baseline": 48, "unit": "C"}]}
        (ev,) = _sql_evidence(res, {"id": "e1", "tool": "structured_query_tool", "iteration": 1, "tool_args": {}})
        assert ev.quality_warning and not ev.supports_causal_claim()
        assert "SENSOR FAULT" in render_evidence(ev)

    def test_plausible_value_passes(self):
        from ops_copilot.agent.nodes.observe import physical_check
        assert physical_check("motor_temp", 66) is None
        assert physical_check("speed", 0) is None                                # parked, not broken
        assert physical_check("pack_voltage", 0)
