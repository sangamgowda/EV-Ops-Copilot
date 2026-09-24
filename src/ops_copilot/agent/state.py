"""The state object that travels around the agent loop.

This is the single most important type in the system. It is what
makes the loop a loop rather than a pipeline: each lap appends to
`evidence`, and the next lap's Plan node reads it. Without
accumulation, lap 2 would have no memory of lap 1 and would simply
repeat it.

Two things are kept deliberately separate:

  stored   — everything here, for tracing and for later laps
  sent     — a projection of this, built per node

`Evidence.summary` is what goes into a prompt. `Evidence.raw_ref`
points at the full rows, which stay in a side store. A SQL result
can be 20kb; its summary is three lines. Only the summary is ever
paid for in tokens.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal, Optional
from operator import add

from pydantic import BaseModel, Field
from typing_extensions import TypedDict


# ── enums ────────────────────────────────────────────────────

class Domain(str, Enum):
    DIAGNOSTIC = "diagnostic"
    BUSINESS = "business"


class QueryType(str, Enum):
    LOOKUP = "lookup"       # a value is wanted
    EXPLAIN = "explain"     # a cause is wanted
    COMPARE = "compare"     # two things set against each other


class StopReason(str, Enum):
    COMPLETE = "complete"            # Reflect was satisfied
    EXHAUSTED = "exhausted"          # what's missing is unreachable
    CAP_REACHED = "cap_reached"      # hit max_iterations
    NO_NEW_EVIDENCE = "no_new_evidence"  # a lap added nothing


class Verdict(str, Enum):
    """Computed in code, never by the model."""
    ABOVE_NORMAL = "above_normal"
    BELOW_NORMAL = "below_normal"
    NORMAL = "normal"
    NO_BASELINE = "no_baseline"
    NOT_APPLICABLE = "not_applicable"


class EvidenceStatus(str, Enum):
    OK = "ok"
    EMPTY = "empty"                    # ran fine, returned nothing
    BELOW_THRESHOLD = "below_threshold"  # retrieved, too weak to use
    FAILED = "failed"                  # tool errored or timed out


# ── evidence ─────────────────────────────────────────────────

class Evidence(BaseModel):
    """One thing the agent learned.

    Comparison fields are populated at write time, in code — the
    model never does the arithmetic. This matters twice: models are
    unreliable at arithmetic, and it lets Reflect check "do I have a
    verdict for this component" almost deterministically.

    A failed or empty result still becomes an Evidence entry. That
    is deliberate. An absence the agent can see is something it can
    report; an absence it cannot see is something it invents around.
    """

    id: str = Field(description="e1, e2, ... — what citations point at")
    tool: str
    status: EvidenceStatus = EvidenceStatus.OK
    iteration: int = 1

    summary: str = Field(description="What goes in the prompt. Short.")
    raw_ref: Optional[str] = Field(
        default=None,
        description="Key into the raw store. Full rows live there, not here.",
    )

    # Structured comparison, when the evidence is a measurement.
    metric: Optional[str] = None
    actual: Optional[float] = None
    baseline: Optional[float] = None
    unit: Optional[str] = None
    delta_pct: Optional[float] = None
    verdict: Optional[Verdict] = None

    # Retrieval provenance.
    source_doc: Optional[str] = None
    chunk_ids: list[int] = Field(default_factory=list)
    rerank_score: Optional[float] = None

    # Execution provenance — what was actually run.
    tool_args: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    latency_ms: Optional[int] = None

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def is_usable(self) -> bool:
        """Did this actually give the agent something to work with?"""
        return self.status == EvidenceStatus.OK

    def supports_causal_claim(self) -> bool:
        """Whether a 'because' may be attached to this.

        Empty and below-threshold retrievals never support a cause.
        This is what the groundedness check enforces.
        """
        return self.status == EvidenceStatus.OK and (
            self.source_doc is not None or self.verdict is not None
        )


# ── node outputs ─────────────────────────────────────────────

class RouterOutput(BaseModel):
    domains: list[Domain] = Field(
        description="A LIST. Cross-domain questions return both."
    )
    query_type: QueryType
    entities: dict[str, Optional[str]] = Field(default_factory=dict)
    complexity_hint: QueryType
    hypothesis_to_test: Optional[str] = Field(
        default=None,
        description="A checkable claim in the question, if present.",
    )


class ToolCall(BaseModel):
    tool: Literal["structured_query_tool", "rag_retrieval_tool"]
    args: dict[str, Any]


class PlanOutput(BaseModel):
    reasoning: str
    tool_calls: list[ToolCall]


class ReflectOutput(BaseModel):
    sufficient: bool
    partial: bool = False
    satisfied: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    next_question: Optional[str] = None
    stop_reason: Optional[StopReason] = None


class Citation(BaseModel):
    claim: str
    evidence_id: str


class SynthesisOutput(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    gaps: list[str] = Field(default_factory=list)


class GroundednessResult(BaseModel):
    passed: bool
    tier_failures: list[str] = Field(default_factory=list)
    unresolved_citations: list[str] = Field(default_factory=list)
    untraceable_numbers: list[str] = Field(default_factory=list)
    unsupported_causal_claims: list[str] = Field(default_factory=list)
    escalated_to_llm: bool = False


# ── the graph state ──────────────────────────────────────────

class AgentState(TypedDict, total=False):
    """What LangGraph passes node to node.

    `evidence` uses an additive reducer so each lap appends rather
    than replaces. That one annotation is what accumulates knowledge
    across iterations.
    """

    # Request
    question: str
    session_id: str
    turn_id: str          # tagged onto tool results; stale ones are dropped
    trace_id: Optional[str]

    # Router output
    domains: list[str]
    query_type: str
    entities: dict[str, Optional[str]]
    complexity_hint: str
    hypothesis_to_test: Optional[str]

    # Resolved entities — after fuzzy matching against real rows
    resolved_entities: dict[str, Any]
    entity_notes: list[str]   # "assuming you meant V-021"

    # Loop
    iteration: int
    evidence: Annotated[list[Evidence], add]
    tool_history: Annotated[list[str], add]
    # One entry per decision, appended: Plan's reasoning and tools, then
    # Reflect's verdict, lap by lap. It is what the UI's "how I got this"
    # panel shows; nothing in the loop reads it.
    lap_log: Annotated[list[dict[str, Any]], add]
    open_gaps: list[str]
    next_question: Optional[str]
    stop_reason: Optional[str]
    partial: bool

    # Planning
    plan_reasoning: Optional[str]
    pending_tool_calls: list[ToolCall]

    # Execute -> Observe hand-off. Replaced every lap, never
    # accumulated: only the Evidence built from it persists.
    raw_results: list[dict[str, Any]]

    # Output
    answer: Optional[str]
    citations: list[Citation]
    confidence: Optional[str]
    gaps: list[str]
    groundedness: Optional[GroundednessResult]
    # Set by synthesize when it is rewriting after a failed grounding
    # check; graph.route_after_groundedness reads it to allow exactly
    # one retry. Without a writer the retry path loops forever.
    _grounding_retried: bool

    # Bookkeeping
    prompt_versions: dict[str, str]  # node -> prompt hash, into the trace
    llm_calls: int
    started_at: Optional[datetime]


def new_state(question: str, session_id: str, turn_id: str) -> AgentState:
    return AgentState(
        question=question,
        session_id=session_id,
        turn_id=turn_id,
        iteration=0,
        evidence=[],
        tool_history=[],
        lap_log=[],
        open_gaps=[],
        entity_notes=[],
        resolved_entities={},
        citations=[],
        gaps=[],
        prompt_versions={},
        llm_calls=0,
        partial=False,
        pending_tool_calls=[],
        raw_results=[],
        _grounding_retried=False,
        started_at=datetime.now(timezone.utc),
    )


def next_evidence_id(state: AgentState) -> str:
    return f"e{len(state.get('evidence', [])) + 1}"
