"""Phase 6, operations and scale: evaluation statistics and the
held-out set, caching, metrics, and the live-answer judge."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ops_copilot.evaluation.stats import overlaps, wilson

GOLDEN = Path(__file__).resolve().parents[1] / "src" / "ops_copilot" / "evaluation" / "golden"


class TestIntervals:
    def test_wilson_matches_known_values(self):
        assert wilson(33, 46) == (57.5, 82.7)
        assert wilson(3, 3) == (43.9, 100.0)          # all passing, but only three cases
        assert wilson(0, 5)[0] == 0.0
        assert wilson(0, 0) is None

    def test_more_cases_narrow_the_interval(self):
        small, large = wilson(7, 10), wilson(70, 100)
        assert (small[1] - small[0]) > (large[1] - large[0])

    def test_overlap_means_noise(self):
        assert overlaps((40, 70), (55, 80))
        assert not overlaps((40, 50), (60, 80))


class TestHeldOut:
    @pytest.fixture
    def heldout(self):
        path = GOLDEN / "heldout.jsonl"
        if not path.exists():
            pytest.skip("held-out set not built (build_golden.py --heldout)")
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_disjoint_from_the_golden_set(self, heldout):
        golden = [json.loads(line) for line in (GOLDEN / "golden.jsonl").read_text(encoding="utf-8").splitlines()]
        assert not {c["id"] for c in heldout} & {c["id"] for c in golden}
        assert not {c["question"] for c in heldout} & {c["question"] for c in golden}

    def test_never_about_a_vehicle_tuned_against(self, heldout):
        tuned = {"V-042", "V-007", "V-055", "V-088", "V-091", "V-036", "V-118", "V-017", "V-064", "V-029"}
        assert not {c["expected"].get("vehicle_id") for c in heldout} & tuned

    def test_large_enough_to_mean_something(self, heldout):
        assert len(heldout) >= 45


class TestRunsCompareWithinTheirSet:
    def test_heldout_run_is_not_compared_with_a_golden_run(self, tmp_path, monkeypatch):
        from ops_copilot.evaluation import runner
        monkeypatch.setattr(runner, "runs_dir", lambda: tmp_path)
        for rid, case_set in (("20260101-000000", "golden"), ("20260102-000000", "heldout"),
                              ("20260103-000000", "golden"), ("20260104-000000", "heldout")):
            (tmp_path / rid).mkdir()
            (tmp_path / rid / "meta.json").write_text(json.dumps({"case_set": case_set}))
            (tmp_path / rid / "report.json").write_text("{}")
        assert runner.previous_run("20260104-000000") == "20260102-000000"
        assert runner.previous_run("20260103-000000") == "20260101-000000"


class TestToolCache:
    @pytest.fixture
    def cache_mod(self, monkeypatch):
        from ops_copilot.mcp_server import cache as c
        monkeypatch.setattr(c, "_cache", None)
        version = {"v": (12, "2026-10-01")}

        async def fake_version():
            return version["v"]

        monkeypatch.setattr(c, "corpus_version", fake_version)
        return c, version

    def test_only_reference_tables_are_cacheable(self, cache_mod):
        c, _ = cache_mod
        assert c.sql_cache_key("SELECT meaning FROM error_codes WHERE code = 'ERR_601'")
        assert c.sql_cache_key("select   meaning from ERROR_CODES where code='ERR_601'") == \
            c.sql_cache_key("SELECT meaning FROM error_codes WHERE code = 'ERR_601'")
        assert c.sql_cache_key("SELECT avg(metric_value) FROM vehicle_telemetry") is None
        assert c.sql_cache_key("SELECT e.meaning FROM error_codes e JOIN service_events s "
                               "ON s.error_code = e.code") is None          # one live table is enough

    async def test_repeat_is_served_from_cache(self, cache_mod):
        c, _ = cache_mod
        calls = []

        async def compute():
            calls.append(1)
            return {"status": "ok", "rows": [1]}

        first = await c.cached("sql", "k", compute)
        second = await c.cached("sql", "k", compute)
        assert len(calls) == 1 and first["cached"] is False and second["cached"] is True

    async def test_ingest_invalidates(self, cache_mod):
        c, version = cache_mod
        calls = []

        async def compute():
            calls.append(1)
            return {"status": "ok"}

        await c.cached("search", ("q", "diagnostic", ""), compute)
        version["v"] = (13, "2026-10-02")                              # a document was ingested
        await c.cached("search", ("q", "diagnostic", ""), compute)
        assert len(calls) == 2

    async def test_failures_are_not_cached(self, cache_mod):
        c, _ = cache_mod
        calls = []

        async def compute():
            calls.append(1)
            return {"status": "failed", "error": "boom"}

        await c.cached("sql", "k2", compute)
        await c.cached("sql", "k2", compute)
        assert len(calls) == 2

    async def test_no_database_means_no_cache_not_a_failure(self, monkeypatch):
        from ops_copilot.mcp_server import cache as c

        async def broken():
            raise ConnectionError("no database")

        monkeypatch.setattr(c, "corpus_version", broken)

        async def compute():
            return {"status": "ok"}

        assert (await c.cached("sql", "k", compute))["status"] == "ok"


class TestLiveJudge:
    def test_sampling_is_uniform_and_reproducible(self):
        from ops_copilot.evaluation.live_judge import sampled
        ids = [f"turn-{i}" for i in range(4000)]
        share = sum(sampled(t, 0.10) for t in ids) / len(ids)
        assert 0.08 < share < 0.12
        assert [sampled(t, 0.10) for t in ids[:50]] == [sampled(t, 0.10) for t in ids[:50]]

    def test_partial_or_bare_answers_are_not_judged(self):
        from ops_copilot.agent.state import Evidence
        from ops_copilot.evaluation.live_judge import eligible
        ev = [Evidence(id="e1", tool="structured_query_tool", summary="x")]
        assert eligible({"answer": "x" * 80, "evidence": ev})
        assert not eligible({"answer": "x" * 80, "evidence": ev, "partial": True})
        assert not eligible({"answer": "x" * 80, "evidence": []})
        assert not eligible({"answer": "ok", "evidence": ev})

    @pytest.mark.live_judge_on
    async def test_low_score_is_stored_measured_and_flagged(self, monkeypatch):
        from ops_copilot.evaluation import live_judge
        from ops_copilot.observability import metrics

        async def fake_judge(question, evidence_text, answer):
            assert "[e1]" in evidence_text                 # judged against the turn's own evidence
            return live_judge.LiveScore(faithfulness=2, hedging=4, helpfulness=3, notes="38% is not in e1")

        writes = []

        class Conn:
            async def execute(self, stmt):
                writes.append((stmt.table.name, dict(stmt.compile().params)))

        class Begin:
            async def __aenter__(self):
                return Conn()

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(live_judge, "judge_answer", fake_judge)
        monkeypatch.setattr("ops_copilot.db.engine.owner_engine", lambda: type("E", (), {"begin": lambda s: Begin()})())
        from ops_copilot.agent.state import Evidence
        before = metrics.LIVE_JUDGE.labels("faithfulness")._sum.get()
        await live_judge.review({"turn_id": "t9", "question": "q", "answer": "a" * 50,
                                 "evidence": [Evidence(id="e1", tool="structured_query_tool", summary="s")]})
        tables = [t for t, _ in writes]
        assert tables == ["live_judgements", "flagged_interactions"]
        assert writes[1][1]["rating"] == "judge_low" and "faithfulness" in writes[1][1]["comment"]
        assert metrics.LIVE_JUDGE.labels("faithfulness")._sum.get() == before + 2


class TestMetrics:
    def test_cost_is_priced_per_model(self):
        from ops_copilot.observability.metrics import price
        assert price("openai/gpt-oss-120b", {"input": 1_000_000, "output": 0}) == pytest.approx(0.15)
        assert price("unknown/model", {"input": 10**6}) == 0.0

    def test_turn_cost_sums_every_call_in_the_turn(self):
        from ops_copilot.observability import metrics
        metrics.start_turn()
        metrics.record_llm_call("openai/gpt-oss-120b", {"input": 10_000, "output": 1_000})
        metrics.record_llm_call("openai/gpt-oss-20b", {"input": 10_000, "output": 1_000})
        assert metrics.turn_cost() == pytest.approx(0.0021 + 0.00105)

    def test_turn_outcome_is_exported(self):
        from ops_copilot.observability import metrics
        metrics.record_turn({"stop_reason": "timeout", "partial": True, "latency_ms": 45_000,
                             "grounded": False, "node_errors": [], "cost_usd": 0.004})
        text = metrics.render().decode()
        assert 'copilot_turns_total{stop_reason="timeout"}' in text
        assert 'copilot_turn_errors_total{kind="timeout"}' in text
        assert 'copilot_grounding_checks_total{result="failed"}' in text

    def test_metrics_endpoint_has_no_question_text(self):
        from fastapi.testclient import TestClient

        from ops_copilot import main
        from ops_copilot.observability import metrics
        metrics.record_turn({"stop_reason": "complete", "latency_ms": 1000, "grounded": True, "cost_usd": 0.001})
        with TestClient(main.app) as c:
            r = c.get("/metrics")
        assert r.status_code == 200 and "copilot_turn_latency_seconds_bucket" in r.text
