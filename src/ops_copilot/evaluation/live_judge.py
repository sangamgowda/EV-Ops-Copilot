"""A judge that reviews a random sample of LIVE answers.

The golden and held-out sets measure questions someone thought to ask.
Real traffic drifts away from them. This samples real answers as they
are given and scores them against the evidence each one was built on
(config/prompts/judge_live.md) — no reference answer exists for live
traffic, so the question is "is it faithful to what it found", not
"is it right".

Runs after the answer has been sent, as a background task: it never
adds latency to a request and never changes an answer. It goes through
the same model queue as everything else, on the judge's own model,
with no fallback (a score from a different judge is a different
measurement).

Sampling is by a hash of the turn id, not a random draw: uniform across
traffic, and whether a turn was sampled can be recomputed later.

Every score is stored (live_judgements) and exported as a metric. An
answer scoring at or below `flag_at_or_below` on any dimension is
flagged into the feedback queue as `judge_low`, where a person reviews
it like a thumbs-down. Nothing is promoted to the test set without
that review.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import insert

from ops_copilot.observability import metrics
from ops_copilot.settings import get_config, load_prompt, model_for

log = logging.getLogger(__name__)

DIMENSIONS = ("faithfulness", "hedging", "helpfulness")
_tasks: set[asyncio.Task[None]] = set()     # strong refs: a dropped task can be collected mid-run


class LiveScore(BaseModel):
    faithfulness: int = Field(ge=1, le=4)
    hedging: int = Field(ge=1, le=4)
    helpfulness: int = Field(ge=1, le=4)
    notes: str = ""


def _cfg() -> dict[str, Any]:
    return get_config()["live_judge"]


def judge_version() -> str:
    llm = get_config()["llm"]
    parts = [load_prompt("judge_live")[1], model_for("judge_live"),
             str(llm["temperature"].get("judge_live")), str(llm["max_tokens"].get("judge_live"))]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]


def sampled(turn_id: str, rate: float | None = None) -> bool:
    rate = _cfg()["sample_rate"] if rate is None else rate
    bucket = int(hashlib.sha256(turn_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return bucket < rate


def eligible(state: dict[str, Any]) -> bool:
    """Worth a judge's tokens: a complete answer with evidence behind it.
    Partial answers (time limit) are already known to be incomplete."""
    answer = state.get("answer") or ""
    return (not state.get("partial") and len(answer) >= _cfg()["min_answer_chars"]
            and bool(state.get("evidence")))


async def judge_answer(question: str, evidence_text: str, answer: str) -> LiveScore:
    from ops_copilot.llm.client import complete_json, primary_only

    system, _ = load_prompt("judge_live")
    user = f"## Question\n{question}\n\n## Evidence\n{evidence_text}\n\n## Answer\n{answer}"
    with primary_only():
        return await complete_json("judge_live", system, user, LiveScore)


async def review(state: dict[str, Any]) -> LiveScore | None:
    """Judge one finished turn, store the score, flag it if low."""
    from ops_copilot.agent.context import evidence_block
    from ops_copilot.db.engine import owner_engine
    from ops_copilot.db.models import flagged_interactions, live_judgements

    score = await judge_answer(state["question"], evidence_block(state.get("evidence", [])), state["answer"])
    values = score.model_dump()
    for d in DIMENSIONS:
        metrics.LIVE_JUDGE.labels(d).observe(values[d])
    low = [d for d in DIMENSIONS if values[d] <= _cfg()["flag_at_or_below"]]
    async with owner_engine().begin() as conn:
        await conn.execute(insert(live_judgements).values(
            turn_id=state["turn_id"], judge_version=judge_version(), notes=score.notes,
            faithfulness=score.faithfulness, hedging=score.hedging, helpfulness=score.helpfulness))
        if low:
            await conn.execute(insert(flagged_interactions).values(
                turn_id=state["turn_id"], rating="judge_low", category="live_judge",
                comment=f"low {', '.join(low)}: {score.notes}"[:500]))
    return score


def schedule(state: dict[str, Any]) -> bool:
    """Called at the end of a recorded turn. True if a review was started."""
    if not _cfg()["enabled"] or not eligible(state) or not sampled(state["turn_id"]):
        return False

    async def run() -> None:
        try:
            await review(state)
        except Exception:
            # A judge failure is a missing data point, never a user-facing error.
            log.warning("live judge failed for turn %s", state.get("turn_id"), exc_info=True)

    task = asyncio.get_running_loop().create_task(run(), name=f"live-judge-{state['turn_id']}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True
