"""Loop behaviour. These are the tests that prove it is an agent.

Phase 3: run against the skeleton nodes (fixed values, no LLM, no
database, no MCP). They prove the SHAPE — the backward edge loops,
state accumulates, the cap stops a runaway, and grounding failures
retry synthesis exactly once. Tests that need real judgement stay
skipped until the phase that makes them meaningful.
"""

from __future__ import annotations

import pytest

from ops_copilot.agent import graph as graph_module
from ops_copilot.agent.nodes import reflect as reflect_mod
from ops_copilot.agent.state import AgentState, GroundednessResult, new_state
from ops_copilot.settings import get_config

# Generous but finite: a wiring bug fails the test instead of hanging it.
RECURSION_LIMIT = 50


async def run(question: str = "Why did range drop on VIN-1042?") -> AgentState:
    app = graph_module.build_graph()
    state: AgentState = await app.ainvoke(new_state(question, "s1", "t1"),
                             config={"recursion_limit": RECURSION_LIMIT})
    return state


def count_calls(monkeypatch, name: str) -> list[int]:
    """Wrap a node as the graph module sees it, and count its runs."""
    calls: list[int] = []
    original = getattr(graph_module, name)

    async def wrapped(state):
        calls.append(state.get("iteration", 0))
        return await original(state)

    monkeypatch.setattr(graph_module, name, wrapped)
    return calls


class TestLoopControl:
    async def test_loops_until_reflect_is_satisfied(self):
        state = await run()
        assert state["iteration"] == 3
        assert state["stop_reason"] == "complete"
        assert state["answer"]

    async def test_stops_at_iteration_cap(self, monkeypatch):
        """Reflect never satisfied: the cap, not Reflect, must end it."""
        monkeypatch.setattr(reflect_mod, "SKELETON_SUFFICIENT_ON_LAP", 999)
        state = await run()
        assert state["iteration"] == get_config()["agent"]["max_iterations"]
        assert state["answer"]

    async def test_never_loops_back_to_router(self, monkeypatch):
        router_calls = count_calls(monkeypatch, "router_node")
        plan_calls = count_calls(monkeypatch, "plan_node")
        await run()
        assert len(router_calls) == 1
        assert len(plan_calls) == 3

    async def test_grounding_failure_retries_synthesis_exactly_once(self, monkeypatch):
        """The other backward path. Without the retry flag being written,
        a check that keeps failing loops forever."""
        async def always_fail(state):
            return {"groundedness": GroundednessResult(passed=False, tier_failures=["tier1"])}

        monkeypatch.setattr(graph_module, "groundedness_node", always_fail)
        synth_calls = count_calls(monkeypatch, "synthesize_node")
        state = await run()
        assert len(synth_calls) == 2
        assert state["_grounding_retried"] is True

    @pytest.mark.skip(reason="needs the real Reflect (lap count follows the question)")
    async def test_lookup_exits_after_one_lap(self): ...

    @pytest.mark.skip(reason="needs the real Reflect")
    async def test_stops_when_no_new_evidence(self): ...

    @pytest.mark.skip(reason="needs the real Reflect")
    async def test_exhausted_exits_with_partial(self): ...


class TestEvidenceAccumulation:
    async def test_evidence_persists_across_laps(self):
        """The one-character bug: a wrong reducer overwrites instead of
        appending, and lap 2 has no memory of lap 1."""
        state = await run()
        assert [(e.id, e.iteration) for e in state["evidence"]] == [("e1", 1), ("e2", 2), ("e3", 3)]
        assert len(state["tool_history"]) == 3

    @pytest.mark.skip(reason="needs real tools (failures come from MCP)")
    async def test_failed_tool_becomes_evidence(self): ...

    @pytest.mark.skip(reason="needs real retrieval")
    async def test_empty_retrieval_becomes_evidence(self): ...


class TestNodeOrdering:
    async def test_every_node_survives_a_bare_state(self):
        """A node reading a field no earlier node wrote must default, not
        raise KeyError — that failure only shows on an unusual path."""
        from ops_copilot.agent import nodes

        bare = AgentState(question="q", turn_id="t")
        for fn in (nodes.router_node, nodes.plan_node, nodes.execute_node, nodes.observe_node,
                   nodes.reflect_node, nodes.synthesize_node, nodes.groundedness_node):
            assert isinstance(await fn(AgentState(**bare)), dict), fn.__name__
