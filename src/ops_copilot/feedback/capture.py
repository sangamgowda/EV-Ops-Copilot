"""Capture a rating against a turn.

Nothing special is recorded at this moment — the full trace already
exists. The rating just marks it.

The turn row is written when the turn STARTS (agent.turn), so a rating
can reference a turn whose stream the user abandoned halfway.

Also to capture, in the feedback phase: IMPLICIT negatives — a user
rephrasing the same question within implicit_negative_window_seconds.
There are far more of these than explicit thumbs-down, and they are the
same signal.
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy import insert, select

from ops_copilot.db.engine import owner_engine
from ops_copilot.db.models import conversation_turns, flagged_interactions

Rating = Literal["up", "down", "implicit_down"]


class UnknownTurnError(LookupError):
    pass


async def record(turn_id: str, rating: Rating, category: str | None = None,
                 comment: str | None = None) -> int:
    async with owner_engine().begin() as conn:
        exists = (await conn.execute(
            select(conversation_turns.c.turn_id).where(conversation_turns.c.turn_id == turn_id)
        )).first()
        if exists is None:
            raise UnknownTurnError(turn_id)
        row_id = (await conn.execute(
            insert(flagged_interactions)
            .values(turn_id=turn_id, rating=rating, category=category, comment=comment)
            .returning(flagged_interactions.c.id)
        )).scalar_one()
    return int(row_id)
