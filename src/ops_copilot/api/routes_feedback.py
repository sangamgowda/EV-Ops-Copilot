"""/feedback — a rating against a turn_id.

turn_id is the id returned in /chat's `done` event. The turn row is
written when the turn starts, so an abandoned stream can still be rated.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ops_copilot.api.schemas import FeedbackRequest, FeedbackResponse
from ops_copilot.feedback.capture import UnknownTurnError, record

router = APIRouter()


@router.post("/feedback", response_model=FeedbackResponse)
async def feedback(req: FeedbackRequest) -> FeedbackResponse:
    try:
        row_id = await record(req.turn_id, req.rating, req.category, req.comment)
    except UnknownTurnError as exc:
        raise HTTPException(404, f"no turn {req.turn_id}") from exc
    return FeedbackResponse(id=row_id, turn_id=req.turn_id, rating=req.rating)
