"""Capture a rating against a turn.

Nothing special is recorded at this moment — the full trace already
exists. The rating just marks it.

The turn row is written when the turn STARTS (agent.turn), so a rating
can reference a turn whose stream the user abandoned halfway.

IMPLICIT negatives are captured too: a user asking the same question
again, reworded, within implicit_negative_window_seconds of the last
answer. There are far more of these than explicit thumbs-down, and they
are the same signal — the previous answer did not do the job. The flag
goes on the PREVIOUS turn, whose trace is the one worth reading.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

from rapidfuzz import fuzz
from sqlalchemy import insert, select, update

from ops_copilot.db.engine import owner_engine
from ops_copilot.db.models import conversation_turns, flagged_interactions
from ops_copilot.settings import get_config

log = logging.getLogger(__name__)

Rating = Literal["up", "down", "implicit_down"]


class UnknownTurnError(LookupError):
    pass


async def record(turn_id: str, rating: Rating, category: str | None = None,
                 comment: str | None = None) -> int:
    """One explicit rating per turn. A second call UPDATES it — a user who
    clicks thumbs-down and then adds a comment, or changes their mind,
    produces one row, not two. A later comment never erases an earlier one
    by being absent."""
    f = flagged_interactions
    async with owner_engine().begin() as conn:
        exists = (await conn.execute(
            select(conversation_turns.c.turn_id).where(conversation_turns.c.turn_id == turn_id)
        )).first()
        if exists is None:
            raise UnknownTurnError(turn_id)
        current = (await conn.execute(
            select(f.c.id).where(f.c.turn_id == turn_id, f.c.rating.in_(("up", "down")))
            .order_by(f.c.id.desc()).limit(1)
        )).scalar()
        if current is not None and rating in ("up", "down"):
            values: dict[str, str] = {"rating": rating}
            if category is not None:
                values["category"] = category
            if comment is not None:
                values["comment"] = comment
            await conn.execute(update(f).where(f.c.id == current).values(**values))
            return int(current)
        row_id = (await conn.execute(
            insert(f).values(turn_id=turn_id, rating=rating, category=category, comment=comment)
            .returning(f.c.id)
        )).scalar_one()
    return int(row_id)


def is_rephrase(previous: str, current: str, gap_seconds: float, cfg: dict[str, Any]) -> bool:
    """Pure: does `current` look like the user asking `previous` again?

    Close in time AND close in wording. Word-set similarity ignores
    order and extra words, so "why is V-042's range so low" matches
    "Why did range drop on V-042?"; a short follow-up ("and V-017?")
    does not. Identical questions count: asking again is the clearest
    rephrase there is."""
    if gap_seconds < 0 or gap_seconds > cfg["implicit_negative_window_seconds"]:
        return False
    return fuzz.token_set_ratio(previous.lower(), current.lower()) >= cfg["implicit_negative_min_similarity"]


async def detect_implicit_negative(session_id: str, turn_id: str, question: str) -> int | None:
    """Called as a new turn starts. Flags the session's previous turn if
    this question rephrases it. Best effort: never costs the user their
    answer."""
    cfg = get_config()["feedback"]
    t, f = conversation_turns, flagged_interactions
    try:
        async with owner_engine().begin() as conn:
            prev = (await conn.execute(
                select(t.c.turn_id, t.c.question, t.c.created_at, t.c.ended_at)
                .where(t.c.session_id == session_id, t.c.turn_id != turn_id)
                .order_by(t.c.created_at.desc()).limit(1)
            )).first()
            if prev is None:
                return None
            now = datetime.now(tz=(prev.ended_at or prev.created_at).tzinfo)
            gap = (now - (prev.ended_at or prev.created_at)).total_seconds()
            if not is_rephrase(prev.question, question, gap, cfg):
                return None
            already = (await conn.execute(
                select(f.c.id).where(f.c.turn_id == prev.turn_id, f.c.rating == "implicit_down")
            )).first()
            if already:
                return int(already.id)
            row_id = (await conn.execute(
                insert(f).values(turn_id=prev.turn_id, rating="implicit_down", category="rephrased",
                                 comment=f"asked again {gap:.0f}s later: {question}"[:500])
                .returning(f.c.id)
            )).scalar_one()
            return int(row_id)
    except Exception as exc:
        log.warning("implicit-negative check failed for %s: %s", turn_id, exc)
        return None
