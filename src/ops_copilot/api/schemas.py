"""Request/response models for the API surface.

ChatResponse carries turn_id, and that id is the SAME one used as
the Langfuse trace id. Keeping them identical (rather than mapping
between two id spaces) is what lets a thumbs-down be joined back to
the full trace with no lookup table.

TODO(build): flesh out.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str
    session_id: Optional[str] = None
    stream: bool = True


class ChatResponse(BaseModel):
    turn_id: str          # == trace id, deliberately
    session_id: str
    answer: str
    citations: list[dict] = Field(default_factory=list)
    confidence: str = "medium"
    gaps: list[str] = Field(default_factory=list)
    iterations: int = 1
    stop_reason: Optional[str] = None
    partial: bool = False


class FeedbackRequest(BaseModel):
    turn_id: str
    rating: str           # "up" | "down"
    category: Optional[str] = None
    comment: Optional[str] = None
