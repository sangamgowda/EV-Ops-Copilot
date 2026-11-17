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
