"""Reflect — LLM call 3. The decision point; the only node with two exits.

reads   question, evidence[], iteration, tool_history
returns sufficient, partial, missing[], next_question, stop_reason
model   cheap tier — structured judgement against a checklist
never   writes the answer, or calls a tool

Its prompt is a three-step procedure, not a vague "are you done?":

  1. decompose what the question requires (measurement / baseline /
     mechanism / alternative / recommendation)
  2. match evidence to each component
  3. judge whether anything missing is REACHABLE

Step 3 is what most implementations skip and what makes the
difference. If a component is missing because a tool already
returned nothing for it, another lap returns nothing again. That is
`exhausted` — stop, answer partially, say so. Without step 3 the
agent spins until the cap on every question it cannot fully answer.

Lap count is never set in advance. A lookup exits after lap 1
because after lap 1 the evidence genuinely IS sufficient; a causal
question does not, because it requires more components. The
adaptiveness falls out of the question's own structure.

Order of checks, cheapest first:
  1. lookup on lap 1 with usable evidence -> complete, no LLM call.
     (A lookup whose lap-1 query FAILED still goes to the model: a
     rejected query is fixable on the next lap.)
  2. the lap produced no evidence at all -> NO_NEW_EVIDENCE
  3. the model judges
  4. at the cap, an insufficient verdict becomes CAP_REACHED + partial.
     The model still runs at the cap, deliberately: the evidence may
     already be complete, and marking a complete answer "partial"
     would make Synthesize hedge for no reason. One cheap-tier call
     buys that correctness.

Two code-level guards on the model's verdict: "not sufficient" with
no next_question is treated as exhausted — a lap with no direction
cannot find anything — and stop_reason is always set by code, from
the booleans, never copied from the model's own field.
"""

from __future__ import annotations

from typing import Any

from ops_copilot.agent.context import emit, evidence_block, guarded
from ops_copilot.agent.state import AgentState, ReflectOutput, StopReason
from ops_copilot.llm.client import complete_json
from ops_copilot.observability.tracing import traced
from ops_copilot.settings import get_config, load_prompt


def _context(state: AgentState, iteration: int, max_iter: int) -> str:
    parts = [f"## Question\n{state['question']}",
             f"## Question type\n{state.get('query_type', 'explain')}"]
    if state.get("hypothesis_to_test"):
        parts.append(f"## Claim to confirm or refute\n{state['hypothesis_to_test']}")
    if state.get("entity_notes"):
        parts.append("## Entity notes\n" + "\n".join(f"- {n}" for n in state["entity_notes"]))
    parts.append(f"## Evidence\n{evidence_block(state.get('evidence', []))}")
    if state.get("tool_history"):
        parts.append("## Tool calls already made\n" + "\n".join(f"- {k}" for k in state["tool_history"]))
    parts.append(f"## Lap\n{iteration} of {max_iter}")
    return "\n\n".join(parts)


def decide(out: ReflectOutput, iteration: int, max_iter: int) -> dict[str, Any]:
    """Map the model's judgement onto loop control. Pure."""
    exhausted = out.stop_reason == StopReason.EXHAUSTED or (
        not out.sufficient and not out.next_question)
    if out.sufficient and not out.partial and not exhausted:
        stop, partial = StopReason.COMPLETE, False
    elif out.sufficient or exhausted:
        stop, partial = StopReason.EXHAUSTED, True
    elif iteration >= max_iter:
        stop, partial = StopReason.CAP_REACHED, True
    else:
        stop, partial = None, False
    return {
        "stop_reason": stop.value if stop else None,
        "partial": partial,
        "open_gaps": out.missing,
        "next_question": out.next_question if stop is None else None,
    }


def _fallback(state: AgentState, exc: Exception) -> dict:
    # The guide's rule: a failed Reflect defaults to "stop and answer with
    # what exists". Partial, because nobody judged the evidence complete.
    return {"stop_reason": StopReason.EXHAUSTED.value, "partial": True,
            "open_gaps": state.get("open_gaps") or ["evidence review failed"],
            "next_question": None}

@traced("reflect")
@guarded("reflect", _fallback)
async def reflect_node(state: AgentState, config: Any = None) -> dict:
    cfg = get_config()["agent"]
    iteration = state.get("iteration", 1)
    max_iter = cfg["max_iterations"]
    this_lap = [e for e in state.get("evidence", []) if e.iteration == iteration]

    if (state.get("complexity_hint") in cfg["skip_reflect_on"] and iteration == 1
            and any(e.is_usable() for e in this_lap)):
        result = {"stop_reason": StopReason.COMPLETE.value, "partial": False,
                  "open_gaps": [], "next_question": None}
    elif cfg["stop_if_no_new_evidence"] and not this_lap:
        result = {"stop_reason": StopReason.NO_NEW_EVIDENCE.value, "partial": True,
                  "next_question": None}
    else:
        system, version = load_prompt("reflect")
        out = await complete_json("reflect", system, _context(state, iteration, max_iter),
                                  ReflectOutput)
        result = {**decide(out, iteration, max_iter),
                  "llm_calls": state.get("llm_calls", 0) + 1,
                  "prompt_versions": {**state.get("prompt_versions", {}), "reflect": version}}

    await emit(config, "reflect", {"iteration": iteration, "stop_reason": result["stop_reason"],
                                   "missing": result.get("open_gaps", []),
                                   "next_question": result.get("next_question")})
    return result
