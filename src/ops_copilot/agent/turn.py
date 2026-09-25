"""One turn, end to end: record it, run the graph under a deadline,
finish the record.

/chat (and, later, evaluation) call `run_turn`. There is one path
through the system, so what is measured is what users get.

Three production concerns live here rather than in the graph:

  Record first. The conversation_turns row is written when the turn
  STARTS and updated when it ends. A user whose connection drops
  mid-stream can still rate the turn, and the row shows what was asked
  even if nothing else survived.

  A ceiling on total time (api.turn_timeout_seconds). A runaway turn
  otherwise holds a connection indefinitely. The graph is run with
  stream_mode="values", so the latest full state is always in hand;
  on the ceiling the turn stops and answers from the evidence gathered
  so far, marked partial with stop_reason "timeout".

  Recording is best effort. A database hiccup while writing the record
  must never cost the user their answer.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from ops_copilot.agent.context import Emit
from ops_copilot.agent.graph import get_graph
from ops_copilot.agent.nodes.synthesize import evidence_answer
from ops_copilot.agent.state import AgentState, new_state
from ops_copilot.observability.tracing import turn_trace, update_turn
from ops_copilot.settings import get_config

log = logging.getLogger(__name__)

# 7 nodes, up to max_iterations laps, one synthesis retry: well under
# this. It only guards against a wiring bug looping.
RECURSION_LIMIT = 60


async def _record_start(turn_id: str, session_id: str, question: str) -> None:
    from ops_copilot.db.engine import owner_engine
    from ops_copilot.db.models import conversation_turns

    try:
        async with owner_engine().begin() as conn:
            await conn.execute(insert(conversation_turns).values(
                turn_id=turn_id, session_id=session_id, question=question,
            ).on_conflict_do_nothing())
    except Exception as exc:
        log.warning("could not record start of turn %s: %s", turn_id, exc)


async def _record_end(state: AgentState) -> None:
    from ops_copilot.db.engine import owner_engine
    from ops_copilot.db.models import conversation_turns

    try:
        async with owner_engine().begin() as conn:
            await conn.execute(update(conversation_turns)
                               .where(conversation_turns.c.turn_id == state["turn_id"])
                               .values(answer=state.get("answer"),
                                       domains=state.get("domains"),
                                       entities=state.get("resolved_entities"),
                                       iterations=state.get("iteration"),
                                       stop_reason=state.get("stop_reason"),
                                       partial=state.get("partial", False),
                                       prompt_versions=state.get("prompt_versions")))
    except Exception as exc:
        log.warning("could not record end of turn %s: %s", state.get("turn_id"), exc)


async def _notify(emit: Emit | None, event: str, data: dict[str, Any]) -> None:
    if emit is None:
        return
    out = emit(event, data)
    if hasattr(out, "__await__"):
        await out


async def run_turn(question: str, session_id: str | None = None, *, turn_id: str | None = None,
                   emit: Emit | None = None, mcp: Any = None, record: bool = True,
                   timeout_s: float | None = None) -> AgentState:
    session_id = session_id or uuid.uuid4().hex
    turn_id = turn_id or uuid.uuid4().hex
    # Evaluation may pass a longer ceiling (see evaluation.turn_timeout_s);
    # every live request uses the configured one.
    timeout = timeout_s or get_config()["api"]["turn_timeout_seconds"]

    configurable: dict[str, Any] = {}
    if emit is not None:
        configurable["emit"] = emit
    if mcp is not None:
        configurable["mcp"] = mcp

    if record:
        await _record_start(turn_id, session_id, question)

    initial = new_state(question, session_id, turn_id)
    latest: AgentState = initial
    started = time.perf_counter()
    error: BaseException | None = None
    with turn_trace(turn_id, session_id, question):
        try:
            async with asyncio.timeout(timeout):
                async for snapshot in get_graph().astream(
                    initial,
                    config={"configurable": configurable, "recursion_limit": RECURSION_LIMIT},
                    stream_mode="values",
                ):
                    latest = snapshot
        except TimeoutError:
            log.warning("turn %s hit the %ss ceiling after %s lap(s)", turn_id, timeout,
                        latest.get("iteration", 0))
            # Anything already streamed of an unfinished answer is discarded:
            # the client is told to reset before the replacement arrives.
            await _notify(emit, "answer_reset", {"reason": f"time limit ({timeout}s) reached"})
            latest = cast(AgentState, {
                **latest, **evidence_answer(latest, f"the {timeout}s time limit was reached"),
                "stop_reason": "timeout", "partial": True})
            await _notify(emit, "token", {"text": latest["answer"]})
        except BaseException as exc:
            error = exc
            raise
        finally:
            update_turn(output={"answer": latest.get("answer"),
                                "citations": latest.get("citations", [])},
                        **outcome(latest, int((time.perf_counter() - started) * 1000), error))

    if record:
        await _record_end(latest)
    return latest


def outcome(state: AgentState, latency_ms: int, error: BaseException | None = None) -> dict[str, Any]:
    """The turn's filterable fields: what a failure is searched by."""
    g = state.get("groundedness")
    evidence = state.get("evidence", [])
    return {
        "stop_reason": state.get("stop_reason"),
        "iterations": state.get("iteration", 0),
        "partial": bool(state.get("partial")),
        "confidence": state.get("confidence"),
        "grounded": None if g is None else g.passed,
        "grounding_failures": [] if g is None else g.tier_failures,
        "grounding_retried": bool(state.get("_grounding_retried")),
        "tools_selected": sorted({e.tool for e in evidence if e.tool != "entity_resolution"}),
        "evidence_statuses": [f"{e.tool}:{e.status.value}" for e in evidence],
        "domains": state.get("domains"),
        "query_type": state.get("query_type"),
        "entity_notes": state.get("entity_notes", []),
        "llm_calls": state.get("llm_calls", 0),
        "latency_ms": latency_ms,
        "prompt_versions": state.get("prompt_versions", {}),
        "error": f"{type(error).__name__}: {error}" if error else None,
    }
