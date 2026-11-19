"""The evaluation refuses to start on a broken stack.

A run once scored every document and trap case as a failure because the
search tool rejected every call (EMBEDDING_MODEL did not match the stored
vectors) while the tool server itself was reachable. The preflight now
exercises each tool and both models; a run also stops when cases keep
failing on infrastructure.
"""

from __future__ import annotations

from typing import Any

from ops_copilot.evaluation import runner
from ops_copilot.llm import client as llm_client
from ops_copilot.mcp_client import client as mcp_client


class FakeMCP:
    def __init__(self, results: dict[str, dict[str, Any]]) -> None:
        self.results = results

    async def call_tool(self, name: str, args: dict, turn_id: str) -> dict:
        return {"turn_id": turn_id, "tool": name, "result": self.results[name]}


HEALTHY = {
    "resolve_entity_tool": {"status": "exact"},
    "structured_query_tool": {"status": "ok", "rows": [{"n": 100}]},
    "rag_retrieval_tool": {"status": "below_threshold"},
}


def _stack(monkeypatch, results: dict, model_down: str | None = None) -> None:
    monkeypatch.setattr(mcp_client, "get_client", lambda: FakeMCP(results))

    async def complete(node: str, system: str, user: str, **kw: Any) -> str:
        if node == model_down:
            raise ConnectionError("model not found")
        return "OK"

    monkeypatch.setattr(llm_client, "complete", complete)


async def test_healthy_stack_passes(monkeypatch):
    _stack(monkeypatch, HEALTHY)
    assert await runner.preflight() is None


async def test_failing_search_blocks_the_run(monkeypatch):
    broken = {**HEALTHY, "rag_retrieval_tool": {"status": "failed", "stage": "retrieve",
                                                "error": "documents embedded with bge-m3"}}
    _stack(monkeypatch, broken)
    problem = await runner.preflight()
    assert problem and "rag_retrieval_tool failed" in problem and "bge-m3" in problem


async def test_dead_judge_model_blocks_the_run(monkeypatch):
    _stack(monkeypatch, HEALTHY, model_down="judge")
    problem = await runner.preflight()
    assert problem and "judge model did not answer" in problem


async def test_run_stops_after_consecutive_infra_failures(monkeypatch, tmp_path):
    calls: list[str] = []

    async def run_case(case: dict, *a: Any, **kw: Any) -> dict:
        calls.append(case["id"])
        return {"id": case["id"], "status": "infra", "pass": False, "checks": [], "tags": [],
                "source": "x", "latency_s": 0}

    monkeypatch.setattr(runner, "run_case", run_case)
    monkeypatch.setattr(runner, "runs_dir", lambda: tmp_path)
    cases = [{"id": f"c{i}", "source": "synthetic_data", "tags": []} for i in range(10)]
    await runner.run_eval(cases, run_id="r1", use_judge=False, pause_s=0)
    assert len(calls) == runner.INFRA_STREAK_LIMIT


def test_a_lost_connection_is_infrastructure_not_a_failure():
    import logging

    watch = runner._RateLimitWatch()
    watch.emit(logging.LogRecord("x", logging.WARNING, __file__, 1,
                                 "judge: qwen/qwen3.8-27b failed (APIConnectionError); no fallback left",
                                 None, None))
    assert watch.connection_failed and not watch.rate_failed


async def test_resume_refuses_when_the_code_changed(monkeypatch, tmp_path):
    import json

    monkeypatch.setattr(runner, "runs_dir", lambda: tmp_path)
    monkeypatch.setattr(runner, "_git_commit", lambda: "bbb2222")
    (tmp_path / "r1").mkdir()
    (tmp_path / "r1" / "meta.json").write_text(json.dumps(
        {"git_commit": "aaa1111", "models": runner._models(), "case_ids": []}), encoding="utf-8")
    import pytest

    with pytest.raises(RuntimeError, match="aaa1111"):
        await runner.run_eval([], run_id="r1", use_judge=False, pause_s=0)
    # the explicit override still works
    await runner.run_eval([], run_id="r1", use_judge=False, pause_s=0, allow_commit_change=True)


def test_the_evaluation_pins_today_and_the_app_does_not():
    from datetime import date

    from ops_copilot.agent import calendar

    real = calendar.today()
    with calendar.pinned(date(2026, 10, 2)):
        assert calendar.today() == date(2026, 10, 2)
        # "this week" is the week of data the golden answers were computed on
        assert calendar.ranges()["this week"] == (date(2026, 9, 26), date(2026, 10, 2))
    assert calendar.today() == real       # unpinned again outside the block


def test_the_configured_evaluation_date_is_the_golden_build_day():
    import json
    from datetime import date

    from ops_copilot.settings import get_config

    as_of = date.fromisoformat(str(get_config()["evaluation"]["as_of"]))
    meta = json.loads((runner.ROOT / "src/ops_copilot/evaluation/golden/golden_meta.json").read_text(encoding="utf-8"))
    assert as_of.isoformat() == meta["built_at"][:10]
    assert date.fromisoformat(meta["data_ends"][:10]) <= as_of


def test_a_vehicle_only_question_does_not_carry_the_sales_table():
    from ops_copilot.sql.schema_loader import prompt_block

    both, diagnostic = prompt_block(("business", "diagnostic")), prompt_block(("diagnostic",))
    assert "sales_transactions:" in both and "sales_transactions" in prompt_block(())
    assert "sales_transactions" not in diagnostic          # the table and its join key
    assert "vehicle_telemetry:" in diagnostic and "vehicles:" in diagnostic
    assert len(diagnostic) < len(both)
