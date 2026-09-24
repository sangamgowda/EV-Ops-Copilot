"""Tracing: what a trace contains, what it must never contain, and that
it can never break a request.

Traces are captured with a real OpenTelemetry pipeline writing to an
in-memory exporter — the same spans and attributes that would be sent
to Langfuse, without the network. The end-to-end shape through the real
graph is covered in test_graph_loop.py::TestTracing.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ops_copilot.agent.state import Evidence, ToolCall
from ops_copilot.observability import tracing
from ops_copilot.settings import get_config

TURN = "0123456789abcdef0123456789abcdef"      # a real turn id: 32 hex characters


class Captured:
    """The spans one test produced, with helpers to read them."""

    def __init__(self, exporter: InMemorySpanExporter) -> None:
        self.exporter = exporter

    @property
    def spans(self) -> list[Any]:
        return list(self.exporter.get_finished_spans())

    def named(self, name: str) -> list[Any]:
        return [s for s in self.spans if s.name == name]

    def one(self, name: str) -> Any:
        (span,) = self.named(name)
        return span

    @staticmethod
    def attr_json(span: Any, key: str) -> Any:
        return json.loads(span.attributes[key])


@pytest.fixture
def captured(monkeypatch) -> Captured:
    exporter = InMemorySpanExporter()
    otel = tracing.make_otel(exporter, batch=False)
    monkeypatch.setattr(tracing, "_otel", lambda: otel)
    return Captured(exporter)


def with_obs_config(monkeypatch, **overrides: Any) -> None:
    cfg = {**get_config(), "observability": {**get_config()["observability"], **overrides}}
    monkeypatch.setattr(tracing, "get_config", lambda: cfg)


# Minimal nodes, traced exactly as the real ones are.

@tracing.traced("plan")
async def plan_node(state, config=None):
    # The LLM call happens inside the node, as it does for real.
    tracing.record_generation("plan", "model-x", [{"role": "system", "content": "s" * 500},
                                                  {"role": "user", "content": "q"}],
                              "{}", {"input": 120, "output": 40}, 300)
    return {"iteration": state.get("iteration", 0) + 1, "plan_reasoning": "need readings",
            "pending_tool_calls": [ToolCall(tool="structured_query_tool", args={"sql": "SELECT 1"})],
            "prompt_versions": {"plan": "abc123def456"}}


@tracing.traced("execute")
async def execute_node(state, config=None):
    rows = [{"vehicle_id": f"VIN-{i}", "value": i} for i in range(500)]
    return {"raw_results": [{"tool": "structured_query_tool", "args": {"sql": "SELECT 1"},
                             "latency_ms": 12,
                             "result": {"status": "ok", "rows": rows, "row_count": 500,
                                        "sql": "SELECT 1 LIMIT 100", "explain_cost": 10.6}}]}


@tracing.traced("router")
async def router_node(state, config=None):
    return {"domains": ["diagnostic"], "query_type": "explain",
            "entities": {"vehicle_id": "V-042"}}


async def run_turn_like(stop_reason: str = "complete", partial: bool = False) -> None:
    with tracing.turn_trace(TURN, "session-1", "why did range drop?"):
        state: dict[str, Any] = {"iteration": 0}
        await router_node(state)
        state.update(await plan_node(state))
        await execute_node(state)
        tracing.update_turn(output={"answer": "a"}, stop_reason=stop_reason, partial=partial,
                            grounded=True, iterations=1, tools_selected=["structured_query_tool"],
                            prompt_versions={"plan": "abc123def456"})


class TestShape:
    async def test_one_trace_per_turn_whose_id_is_the_turn_id(self, captured):
        await run_turn_like()
        root = captured.one("turn")
        assert root.context.trace_id == int(TURN, 16)          # feedback joins on this
        assert {s.context.trace_id for s in captured.spans} == {int(TURN, 16)}
        assert root.attributes["langfuse.session.id"] == "session-1"

    async def test_laps_are_spans_and_nodes_nest_inside_them(self, captured):
        await run_turn_like()
        root, lap = captured.one("turn"), captured.one("lap 1")
        plan, execute, router = captured.one("plan"), captured.one("execute"), captured.one("router")
        assert router.parent.span_id == root.context.span_id
        assert lap.parent.span_id == root.context.span_id
        assert plan.parent.span_id == lap.context.span_id
        assert execute.parent.span_id == lap.context.span_id
        gen = captured.one("plan.llm")
        assert gen.parent.span_id == plan.context.span_id
        assert gen.attributes["langfuse.observation.type"] == "generation"
        assert json.loads(gen.attributes["langfuse.observation.usage_details"]) == {"input": 120, "output": 40}

    async def test_records_decisions_not_payloads(self, captured):
        await run_turn_like()
        plan_out = Captured.attr_json(captured.one("plan"), "langfuse.observation.output")
        assert plan_out["tool_calls"][0]["args"]["sql"] == "SELECT 1"
        assert captured.one("plan").attributes["langfuse.observation.metadata.prompt_version"] == "abc123def456"
        (result,) = Captured.attr_json(captured.one("execute"), "langfuse.observation.output")["results"]
        assert result["row_count"] == 500 and result["explain_cost"] == 10.6
        assert result["sql_ran"] == "SELECT 1 LIMIT 100"
        assert "rows" not in result                                   # the 500 rows stay out
        gen_in = Captured.attr_json(captured.one("plan.llm"), "langfuse.observation.input")
        assert len(gen_in[0]["content"]) < 250                        # versioned prompt, not resent

    async def test_filterable_fields_and_config_snapshot(self, captured):
        await run_turn_like(stop_reason="cap_reached", partial=True)
        root = captured.one("turn")
        tags = set(root.attributes["langfuse.trace.tags"])
        assert {"stop:cap_reached", "partial:true", "tool:structured_query_tool", "failure"} <= tags
        assert root.attributes["langfuse.trace.metadata.stop_reason"] == "cap_reached"
        cfg = json.loads(root.attributes["langfuse.trace.metadata.config"])
        assert len(cfg["app_config_hash"]) == 12
        assert cfg["confidence_threshold"] == get_config()["retrieval"]["confidence_threshold"]
        assert json.loads(root.attributes["langfuse.trace.metadata.prompt_versions"]) == {"plan": "abc123def456"}
        assert json.loads(root.attributes["langfuse.trace.metadata.tokens"]) == {"input": 120, "output": 40}


class TestSampling:
    async def test_successes_can_be_sampled_out(self, captured, monkeypatch):
        with_obs_config(monkeypatch, success_sample_rate=0.0)
        await run_turn_like(stop_reason="complete")
        assert captured.spans == []

    async def test_failures_are_always_kept(self, captured, monkeypatch):
        with_obs_config(monkeypatch, success_sample_rate=0.0)
        await run_turn_like(stop_reason="cap_reached")
        assert len(captured.named("turn")) == 1


class TestNeverBreaksARequest:
    async def test_export_failure_is_swallowed(self, captured, monkeypatch, caplog):
        def broken(*a, **k):
            raise ConnectionError("tracing backend is down")
        monkeypatch.setattr(tracing, "_export", broken)
        with caplog.at_level(logging.WARNING):
            await run_turn_like()                                   # must not raise
        assert any("request is unaffected" in r.message for r in caplog.records)

    async def test_node_errors_still_propagate_to_the_graph(self, captured):
        @tracing.traced("reflect")
        async def broken(state, config=None):
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError), tracing.turn_trace(TURN, "s", "q"):
            await broken({"iteration": 1})
        assert captured.one("reflect").attributes["langfuse.observation.level"] == "ERROR"

    async def test_no_tracing_configured_means_plain_passthrough(self):
        # The autouse fixture leaves no exporter: nodes just run.
        assert (await router_node({}))["domains"] == ["diagnostic"]
        await run_turn_like()


class TestSummaryLog:
    async def test_one_json_line_per_turn_even_without_a_backend(self, caplog):
        with caplog.at_level(logging.INFO, logger="ops_copilot.turns"):
            await run_turn_like()
        (line,) = [r.message for r in caplog.records if r.name == "ops_copilot.turns"]
        data = json.loads(line)
        assert data["turn_id"] == TURN and data["stop_reason"] == "complete"
        assert data["failure"] is False and data["config_hash"]


class TestScrubbing:
    def test_connection_string_passwords(self):
        out = tracing.scrub("could not connect: postgresql+psycopg://copilot:s3cret-pw@postgres:5432/db")
        assert "s3cret-pw" not in out and "copilot:***@postgres" in out

    def test_api_key_shapes(self):
        out = tracing.scrub({"err": ["key gsk_abcdefghij1234567890 rejected", "sk-lf-1234abcd-ef56"]})
        assert "gsk_" not in json.dumps(out) and "sk-lf-1234" not in json.dumps(out)

    def test_configured_secret_values(self, monkeypatch):
        monkeypatch.setattr(tracing, "_secret_values", lambda: ("MyDbPassw0rd",))
        assert tracing.scrub("FATAL: password MyDbPassw0rd rejected") == "FATAL: password *** rejected"

    async def test_nothing_exported_carries_a_secret(self, captured, monkeypatch):
        monkeypatch.setattr(tracing, "_secret_values", lambda: ("MyDbPassw0rd",))
        with tracing.turn_trace(TURN, "s", "q"):
            @tracing.traced("observe")
            async def observe(state, config=None):
                return {"evidence": [Evidence(id="e1", tool="structured_query_tool", summary="x",
                                              status="failed",
                                              error="connect to postgresql://ro:MyDbPassw0rd@db failed")]}
            await observe({"iteration": 1})
        everything = json.dumps([dict(s.attributes) for s in captured.spans], default=str)
        assert "MyDbPassw0rd" not in everything and "ro:***@db" in everything
