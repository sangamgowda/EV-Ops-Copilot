"""LangGraph assembly.

The whole difference between this and a pipeline is one function:
`route_after_reflect`. It returns "plan" to go around again, or
"synthesize" to exit. That single backward edge is what makes the
system an agent.

    router ──► plan ──► execute ──► observe ──► reflect ─┐
                ▲                                        │
                └──────── not sufficient ────────────────┘
                                                         │
                         sufficient / exhausted / cap ───┘
                                    │
                                    ▼
                         synthesize ──► groundedness ──► END

Router runs once, before the loop. Synthesis runs once, after it.
Neither is part of the cycle.
"""

from __future__ import annotations

import logging

from langgraph.graph import END, StateGraph

from ops_copilot.agent.nodes import (
    execute_node,
    groundedness_node,
    observe_node,
    plan_node,
    reflect_node,
    router_node,
    synthesize_node,
)
from ops_copilot.agent.state import AgentState, StopReason
from ops_copilot.settings import get_config

log = logging.getLogger(__name__)


def route_after_router(state: AgentState) -> str:
    """Entry into the loop. Always Plan — the router only classifies."""
    return "plan"


def route_after_reflect(state: AgentState) -> str:
    """The decision that makes this an agent.

    Four ways out, one way around. All four exits are legitimate
    outcomes — `exhausted` in particular is not a failure, it is the
    agent correctly recognising that another lap cannot help.
    """
    cfg = get_config()
    max_iter = cfg["agent"]["max_iterations"]
    iteration = state.get("iteration", 0)
    stop_reason = state.get("stop_reason")

    # Reflect said it has what it needs (or that the gap is
    # unreachable and the answer should be partial).
    if stop_reason in (StopReason.COMPLETE.value, StopReason.EXHAUSTED.value):
        log.info("loop exit: %s after %d lap(s)", stop_reason, iteration)
        return "synthesize"

    # Guard: a lap that added nothing will not do better next time.
    if stop_reason == StopReason.NO_NEW_EVIDENCE.value:
        log.info("loop exit: no new evidence after lap %d", iteration)
        return "synthesize"

    # Guard: hard ceiling. Answer with what exists.
    if iteration >= max_iter:
        log.info("loop exit: iteration cap (%d) reached", max_iter)
        return "synthesize"

    log.info("loop continue: lap %d -> %d", iteration, iteration + 1)
    return "plan"


def route_after_groundedness(state: AgentState) -> str:
    """One retry on a grounding failure, then out.

    Note what this does NOT do: loop back to the router or to plan.
    A grounded-ness failure means the answer strayed from evidence
    already in hand. Re-classifying the domain or re-running
    retrieval fixes nothing — only rewriting the answer does.
    """
    grounding = state.get("groundedness")
    if grounding is None or grounding.passed:
        return END

    if state.get("_grounding_retried"):
        log.warning("grounding failed twice; returning hedged answer")
        return END

    log.info("grounding failed; one synthesis retry")
    return "synthesize"


def build_graph():
    """Wire the nodes. Returns a compiled LangGraph app."""
    g = StateGraph(AgentState)

    g.add_node("router", router_node)
    g.add_node("plan", plan_node)
    g.add_node("execute", execute_node)
    g.add_node("observe", observe_node)
    g.add_node("reflect", reflect_node)
    g.add_node("synthesize", synthesize_node)
    # Node id "grounding", not "groundedness": LangGraph forbids a node
    # sharing a name with a state key, and the state already holds the
    # `groundedness` result.
    g.add_node("grounding", groundedness_node)

    g.set_entry_point("router")

    g.add_conditional_edges("router", route_after_router, {"plan": "plan"})

    # The cycle.
    g.add_edge("plan", "execute")
    g.add_edge("execute", "observe")
    g.add_edge("observe", "reflect")

    # ── the backward edge ────────────────────────────────────
    g.add_conditional_edges(
        "reflect",
        route_after_reflect,
        {
            "plan": "plan",            # ← this is the loop
            "synthesize": "synthesize",
        },
    )

    g.add_edge("synthesize", "grounding")
    g.add_conditional_edges(
        "grounding",
        route_after_groundedness,
        {"synthesize": "synthesize", END: END},
    )

    return g.compile()


_graph = None


def get_graph():
    """Compiled once, reused. Compilation is not free."""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
