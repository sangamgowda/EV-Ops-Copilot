"""Synthesize — LLM call 4. Runs once, after the loop exits.

reads   question, evidence[], partial, open_gaps
returns answer, citations[], confidence, gaps[]
model   strong tier
never   states a cause not backed by an evidence id

Output streams to the client via SSE — first words under a second
even when the full answer takes longer. That is the single biggest
perceived-latency win available and it changes nothing structural.

The partial-answer path is explicit in the prompt, because without
it models reliably smooth over a gap with plausible reasoning. When
partial is set the answer must: report every measurement it has,
name what it cannot establish, identify the strongest signal WITHOUT
asserting it as the cause, and say what would confirm it.

Plain text, not JSON — a deliberate exception to "Pydantic for every
structured LLM output" (see CLAUDE.md). The provider delivers JSON-mode
output in one piece, so a JSON answer cannot stream at all. The model
therefore writes prose with inline [eN] tags, and the structure is
built in code:

  citations   from the inline tags — a strict token format, and every
              one is checked against real evidence by grounding tier 1
  confidence  from facts the loop already has (how it stopped, whether
              it is partial, how much distinct evidence is cited) —
              not from the model grading itself
  gaps        from Reflect's open gaps when the answer is partial

Nothing is interpreted from the prose; only the tags are read.

On a grounding retry the prompt names the exact claims that failed,
and the client is told to discard the first draft ("answer_reset").
"""

from __future__ import annotations

import re
from typing import Any, Literal

from ops_copilot.agent.context import emit, evidence_block, guarded
from ops_copilot.agent.state import (
    AgentState,
    Citation,
    EvidenceStatus,
    SynthesisOutput,
)
from ops_copilot.llm.client import StreamResult, stream
from ops_copilot.observability.tracing import traced
from ops_copilot.settings import load_prompt

_TAG = re.compile(r"\[(e\d+)\]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def build_context(state: AgentState, retry_feedback: str | None) -> str:
    parts = [f"## Question\n{state['question']}"]
    if state.get("entity_notes"):
        parts.append("## Entity notes (state these in the answer)\n"
                     + "\n".join(f"- {n}" for n in state["entity_notes"]))
    if state.get("hypothesis_to_test"):
        parts.append(f"## Claim in the question — confirm or refute it from evidence\n"
                     f"{state['hypothesis_to_test']}")
    parts.append(f"## partial: {str(bool(state.get('partial'))).lower()}")
    if state.get("partial"):
        parts.append(f"## Why the investigation stopped\n{state.get('stop_reason')}")
        if state.get("open_gaps"):
            parts.append("## Missing components\n" + ", ".join(state["open_gaps"]))
    parts.append(f"## Evidence\n{evidence_block(state.get('evidence', []))}")
    if retry_feedback:
        parts.append("## Your previous answer failed the grounding check\n" + retry_feedback
                     + "\nRewrite it. Remove or re-attribute every item above. Do not add new claims.")
    return "\n\n".join(parts)


def _retry_feedback(state: AgentState) -> str | None:
    g = state.get("groundedness")
    if g is None or g.passed:
        return None
    lines = []
    if g.unresolved_citations:
        lines.append(f"- Citations to evidence ids that do not exist: {', '.join(g.unresolved_citations)}")
    if g.untraceable_numbers:
        lines.append(f"- Numbers that appear in no evidence: {', '.join(g.untraceable_numbers)}")
    if g.unsupported_causal_claims:
        lines.append("- Causal claims with no supporting evidence:\n"
                     + "\n".join(f"    \"{c}\"" for c in g.unsupported_causal_claims))
    return "\n".join(lines) or "- " + "; ".join(g.tier_failures)


# ── structure, built in code ─────────────────────────────────

def citations_from_tags(answer: str) -> list[Citation]:
    """One citation per (sentence, tag): the claim is the sentence the
    tag sits in, with the tags themselves removed."""
    out: list[Citation] = []
    seen: set[tuple[str, str]] = set()
    for sentence in (s.strip() for s in _SENTENCE.split(answer)):
        claim = " ".join(_TAG.sub("", sentence).split())
        for eid in _TAG.findall(sentence):
            if (claim, eid) not in seen:
                seen.add((claim, eid))
                out.append(Citation(claim=claim or sentence, evidence_id=eid))
    return out


def derive_confidence(state: AgentState, citations: list[Citation]) -> Literal["high", "medium", "low"]:
    """From how the loop ended and what the answer rests on.

    high    the loop completed and the answer cites at least two
            distinct usable pieces of evidence
    medium  completed, but thinner support
    low     a partial answer, or one that cites nothing usable
    """
    usable = {e.id for e in state.get("evidence", []) if e.status == EvidenceStatus.OK}
    cited = {c.evidence_id for c in citations} & usable
    if state.get("partial") or not cited:
        return "low"
    if state.get("stop_reason") == "complete" and len(cited) >= 2:
        return "high"
    return "medium"


def evidence_answer(state: AgentState, reason: str) -> dict[str, Any]:
    """An answer built from the evidence alone, no model involved.

    Used when the writing step fails and when a turn runs out of time:
    either way the user gets everything that was found, each line cited.
    """
    evidence = [e for e in state.get("evidence", []) if e.tool != "entity_resolution"]
    lines = [f"- {e.summary.splitlines()[0]} [{e.id}]" for e in evidence] or ["- nothing was gathered"]
    answer = f"I could not write a full answer ({reason}). Here is what was found:\n" + "\n".join(lines)
    return {"answer": answer,
            "citations": [Citation(claim=e.summary.splitlines()[0][:120], evidence_id=e.id)
                          for e in evidence],
            "confidence": "low",
            "gaps": [reason],
            # Never retried: a second attempt would only repeat this.
            "_grounding_retried": True}


def _fallback(state: AgentState, exc: Exception) -> dict:
    return evidence_answer(state, "the writing step failed")


@traced("synthesize")
@guarded("synthesize", _fallback)
async def synthesize_node(state: AgentState, config: Any = None) -> dict:
    system, version = load_prompt("synthesize")
    feedback = _retry_feedback(state)
    user = build_context(state, feedback)

    await emit(config, "status", {"node": "synthesize", "message": "Writing the answer"})
    if feedback:
        await emit(config, "answer_reset", {"reason": "grounding check failed; rewriting"})

    result = StreamResult()
    async for delta in stream("synthesize", system, user, result):
        await emit(config, "token", {"text": delta})

    answer = result.text.strip()
    if not answer:
        raise ValueError("the model returned an empty answer")
    citations = citations_from_tags(answer)
    # Validated as a whole, exactly as a JSON reply would have been; the
    # difference is only who assembled it.
    out = SynthesisOutput(
        answer=answer,
        citations=citations,
        confidence=derive_confidence(state, citations),
        gaps=list(state.get("open_gaps") or []) if state.get("partial") else [],
    )
    return {
        "answer": out.answer,
        "citations": out.citations,
        "confidence": out.confidence,
        "gaps": out.gaps,
        "_grounding_retried": bool(feedback) or state.get("_grounding_retried", False),
        "llm_calls": state.get("llm_calls", 0) + 1,
        "prompt_versions": {**state.get("prompt_versions", {}), "synthesize": version},
    }
