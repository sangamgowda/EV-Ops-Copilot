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

Streaming and structured output at once: the model returns the JSON
object synthesize.md asks for, and `AnswerStreamer` decodes the
"answer" string field incrementally as the JSON arrives, so the user
sees prose while citations and confidence are still validated as a
whole object at the end. Nothing is parsed from free text.

On a grounding retry the prompt names the exact claims that failed,
and the client is told to discard the first draft ("answer_reset").
"""

from __future__ import annotations

import json
import re
from typing import Any

from ops_copilot.agent.context import emit, evidence_block, guarded
from ops_copilot.agent.state import AgentState, Citation, SynthesisOutput
from ops_copilot.llm.client import StreamResult, complete_json, extract_json, stream
from ops_copilot.observability.tracing import traced
from ops_copilot.settings import load_prompt

_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


class AnswerStreamer:
    """Incrementally decodes the "answer" string from streaming JSON."""

    def __init__(self) -> None:
        self.buf = ""
        self.pos: int | None = None   # index into buf of the next undecoded char
        self.done = False

    def feed(self, delta: str) -> str:
        self.buf += delta
        if self.done:
            return ""
        if self.pos is None:
            m = re.search(r'"answer"\s*:\s*"', self.buf)
            if not m:
                return ""
            self.pos = m.end()
        out: list[str] = []
        i = self.pos
        while i < len(self.buf):
            ch = self.buf[i]
            if ch == '"':
                self.done = True
                i += 1
                break
            if ch != "\\":
                out.append(ch)
                i += 1
                continue
            if i + 1 >= len(self.buf):
                break  # escape split across chunks; wait for more
            nxt = self.buf[i + 1]
            if nxt == "u":
                if i + 6 > len(self.buf):
                    break
                try:
                    out.append(chr(int(self.buf[i + 2:i + 6], 16)))
                except ValueError:
                    pass
                i += 6
            else:
                out.append(_ESCAPES.get(nxt, nxt))
                i += 2
        self.pos = i
        return "".join(out)


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


def _fallback(state: AgentState, exc: Exception) -> dict:
    # No model available to write prose: return the evidence itself,
    # each line cited, so the user still gets what was found.
    evidence = [e for e in state.get("evidence", []) if e.tool != "entity_resolution"]
    lines = [f"- {e.summary} [{e.id}]" for e in evidence] or ["- nothing was gathered"]
    answer = ("I could not write a full answer (the writing step failed). "
              "Here is what was found:\n" + "\n".join(lines))
    return {"answer": answer,
            "citations": [Citation(claim=e.summary[:120], evidence_id=e.id) for e in evidence],
            "confidence": "low", "gaps": ["answer could not be written"],
            # Never retried: a second failure would only repeat this.
            "_grounding_retried": True}

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
    streamer = AnswerStreamer()
    async for delta in stream("synthesize", system, user, result, json_mode=True):
        text = streamer.feed(delta)
        if text:
            await emit(config, "token", {"text": text})

    calls = 1
    try:
        out = SynthesisOutput.model_validate(json.loads(extract_json(result.text)))
    except Exception:
        # The stream was not valid JSON for the schema. One
        # non-streaming repair call; the client already has the prose.
        out = await complete_json("synthesize", system, user, SynthesisOutput)
        calls += 1

    gaps = list(dict.fromkeys([*out.gaps, *(state.get("open_gaps") or [])])) if state.get("partial") else out.gaps
    return {
        "answer": out.answer,
        "citations": out.citations,
        "confidence": out.confidence,
        "gaps": gaps,
        "_grounding_retried": bool(feedback) or state.get("_grounding_retried", False),
        "llm_calls": state.get("llm_calls", 0) + calls,
        "prompt_versions": {**state.get("prompt_versions", {}), "synthesize": version},
    }
