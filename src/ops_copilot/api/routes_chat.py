"""/chat — ask a question; the answer streams as it is written.

With `stream: true` (the default) the response is Server-Sent Events:

  progress  one plain sentence per step — "Checking current_draw and
            payload against normal values", "Found: SB-114 ..."
  token     the answer, piece by piece, as the model writes it
  reset     discard the answer shown so far; a corrected one follows
            (after a failed grounding check, or at the time ceiling)
  done      the final record: answer, citations, confidence, gaps,
            evidence, turn_id — the id feedback is given against
  error     the turn failed; the message says why

A three-lap question takes several seconds. Streaming does not make it
faster; it makes the first words arrive in under a second and shows
the investigation while it happens, which is what makes the wait
readable — and lets an engineer judge the answer, not just trust it.

If the client disconnects mid-stream, the turn still runs to the end
(bounded by the turn ceiling) and is recorded, so it can still be rated.

The response carries `X-Accel-Buffering: no`: a buffering reverse
proxy such as nginx would otherwise hold the whole stream and deliver
it at the end, silently turning streaming into a single slow reply.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from ops_copilot.agent.state import AgentState
from ops_copilot.agent.turn import run_turn
from ops_copilot.api.progress import to_progress
from ops_copilot.api.schemas import ChatRequest, ChatResponse, EvidenceView, LapView
from ops_copilot.llm.client import LLMNotConfigured

log = logging.getLogger(__name__)
router = APIRouter()

# Turns outlive a disconnected client; the event loop holds only weak
# references to tasks, so these keep them alive until they finish.
_background: set[asyncio.Task[Any]] = set()


def _laps(state: AgentState) -> list[LapView]:
    laps: dict[int, LapView] = {}
    for entry in state.get("lap_log", []):
        lap = laps.setdefault(entry["lap"], LapView(lap=entry["lap"]))
        if entry["kind"] == "plan":
            lap.reasoning = entry.get("reasoning")
            lap.tools = entry.get("tools", [])
        else:
            lap.decision = entry.get("decision")
            lap.missing = entry.get("missing", [])
            lap.next_question = entry.get("next_question")
    for e in state.get("evidence", []):
        if e.iteration in laps:
            laps[e.iteration].found.append(e.id)
    return [laps[k] for k in sorted(laps)]


def to_response(state: AgentState) -> ChatResponse:
    g = state.get("groundedness")
    return ChatResponse(
        turn_id=state["turn_id"], session_id=state["session_id"],
        answer=state.get("answer") or "",
        citations=[c.model_dump() for c in state.get("citations", [])],
        confidence=state.get("confidence") or "low",
        gaps=state.get("gaps", []), iterations=state.get("iteration", 0),
        stop_reason=state.get("stop_reason"), partial=state.get("partial", False),
        grounded=None if g is None else g.passed,
        evidence=[EvidenceView(id=e.id, tool=e.tool, status=e.status.value, summary=e.summary,
                               lap=e.iteration, source_doc=e.source_doc,
                               sql=e.tool_args.get("sql"), query=e.tool_args.get("query"),
                               score=e.rerank_score)
                  for e in state.get("evidence", [])],
        laps=_laps(state),
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> Any:
    session_id = req.session_id or uuid.uuid4().hex
    turn_id = uuid.uuid4().hex

    if not req.stream:
        try:
            state = await run_turn(req.question, session_id, turn_id=turn_id)
        except LLMNotConfigured as exc:
            raise HTTPException(503, str(exc)) from exc
        return to_response(state)

    queue: asyncio.Queue[dict[str, str] | None] = asyncio.Queue()

    def send(event: str, data: dict[str, Any]) -> None:
        queue.put_nowait({"event": event, "data": json.dumps(data, default=str)})

    def emit(event: str, data: dict[str, Any]) -> None:
        if event == "token":
            send("token", {"text": data["text"]})
        elif event == "answer_reset":
            send("reset", {"reason": data.get("reason", "")})
        else:
            for item in to_progress(event, data):
                send("progress", item)

    async def worker() -> None:
        try:
            state = await run_turn(req.question, session_id, turn_id=turn_id, emit=emit)
            send("done", to_response(state).model_dump())
        except Exception as exc:
            log.exception("turn %s failed", turn_id)
            send("error", {"turn_id": turn_id, "message": f"{type(exc).__name__}: {exc}"})
        finally:
            queue.put_nowait(None)

    async def events():
        task = asyncio.create_task(worker())
        _background.add(task)
        task.add_done_callback(_background.discard)
        yield {"event": "progress", "data": json.dumps(
            {"stage": "start", "lap": None, "message": "Starting", "turn_id": turn_id,
             "session_id": session_id})}
        while (item := await queue.get()) is not None:
            yield item

    return EventSourceResponse(events(), headers={"X-Accel-Buffering": "no"})
