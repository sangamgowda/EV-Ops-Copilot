"""Request/response models for the API surface.

ChatResponse carries turn_id, and that id is the SAME one used as
the Langfuse trace id. Keeping them identical (rather than mapping
between two id spaces) is what lets a thumbs-down be joined back to
the full trace with no lookup table.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    stream: bool = True


class EvidenceView(BaseModel):
    id: str
    tool: str
    status: str
    summary: str
    source_doc: str | None = None


class ChatResponse(BaseModel):
    turn_id: str          # == trace id, deliberately
    session_id: str
    answer: str
    citations: list[dict] = Field(default_factory=list)
    confidence: str = "medium"
    gaps: list[str] = Field(default_factory=list)
    iterations: int = 1
    stop_reason: str | None = None
    partial: bool = False
    grounded: bool | None = None
    evidence: list[EvidenceView] = Field(default_factory=list)


class FeedbackRequest(BaseModel):
    turn_id: str
    rating: Literal["up", "down"]
    category: str | None = None
    comment: str | None = Field(default=None, max_length=2000)


class FeedbackResponse(BaseModel):
    id: int
    turn_id: str
    rating: str


class IngestResponse(BaseModel):
    doc_id: str
    status: str
    chunks: int = 0
    error_codes_promoted: int = 0
    title: str | None = None
    domain: str | None = None


class EvalRequest(BaseModel):
    golden_path: str | None = None
    limit: int | None = Field(default=None, ge=1)
    include_traps: bool = True
    judge: bool = False
