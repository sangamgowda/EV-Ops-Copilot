"""Plan — LLM call 2. Inside the loop; runs once per lap.

Tool selection and query generation are the SAME call. The model
decides it needs the SQL tool and writes the SQL in one response —
there is no separate "SQL generation" step downstream.

reads   question, entities, evidence[], open_gaps, next_question,
        iteration, tool_history
returns pending_tool_calls, plan_reasoning
model   strong tier — it writes SQL
never   invents a column, re-runs a query already in tool_history,
        or executes anything itself

On lap 2+ the prompt carries `next_question` from Reflect, so the
model is answering a narrower question than the original — that is
what stops later laps from repeating the first.

Plan also owns the lap counter: a lap begins here, so `iteration`
is incremented here and nowhere else.

Today's date is part of the context. "Last week" means nothing to a
model without it, and it would otherwise guess from training data.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from ops_copilot.agent.context import call_key, emit, evidence_block, guarded
from ops_copilot.agent.state import AgentState, PlanOutput, ToolCall
from ops_copilot.llm.client import complete_json
from ops_copilot.observability.tracing import traced
from ops_copilot.settings import get_config, load_prompt
from ops_copilot.sql.schema_loader import prompt_block


def build_context(state: AgentState, iteration: int) -> str:
    cfg = get_config()["agent"]
    parts = [
        f"## Question\n{state['question']}",
        f"## Today\n{date.today().isoformat()}",
        f"## Domains\n{', '.join(state.get('domains', []))}",
        f"## Resolved entities\n{json.dumps(state.get('resolved_entities', {}))}",
    ]
    if state.get("entity_notes"):
        parts.append("## Entity notes\n" + "\n".join(f"- {n}" for n in state["entity_notes"]))
    if state.get("hypothesis_to_test"):
        parts.append(f"## Claim to test against data\n{state['hypothesis_to_test']}")
    parts.append(f"## Database schema\n{prompt_block()}")
    parts.append(f"## Evidence so far\n{evidence_block(state.get('evidence', []))}")
    if state.get("tool_history"):
        parts.append("## Already run this turn — do not repeat\n"
                     + "\n".join(f"- {k}" for k in state["tool_history"]))
    if iteration > 1 and state.get("next_question"):
        parts.append(f"## This lap's narrower question\n{state['next_question']}")
    if state.get("open_gaps"):
        parts.append("## Still missing\n" + ", ".join(state["open_gaps"]))
    parts.append(f"## Lap\n{iteration} of {cfg['max_iterations']}")
    return "\n\n".join(parts)


def dedupe(calls: list[ToolCall], history: list[str], domains: list[str], cap: int) -> list[ToolCall]:
    seen = set(history)
    out: list[ToolCall] = []
    for call in calls:
        if call.tool == "rag_retrieval_tool":
            # Domain is a hard filter downstream; a missing or invented
            # one would silently match nothing.
            if call.args.get("domain") not in ("diagnostic", "business"):
                call.args["domain"] = domains[0] if domains else "diagnostic"
        key = call_key(call)
        if key in seen:
            continue
        seen.add(key)
        out.append(call)
    return out[:cap]


def _fallback(state: AgentState, exc: Exception) -> dict:
    # No tool calls: this lap adds no evidence, so Reflect stops the loop
    # with no_new_evidence and Synthesize answers from what exists.
    return {"iteration": state.get("iteration", 0) + 1, "pending_tool_calls": [],
            "plan_reasoning": f"planning failed: {type(exc).__name__}", "stop_reason": None}

@traced("plan")
@guarded("plan", _fallback)
async def plan_node(state: AgentState, config: Any = None) -> dict:
    iteration = state.get("iteration", 0) + 1
    await emit(config, "status", {"node": "plan", "iteration": iteration,
                                  "message": f"Planning lap {iteration}"})
    system, version = load_prompt("plan")
    out = await complete_json("plan", system, build_context(state, iteration), PlanOutput)

    calls = dedupe(out.tool_calls, state.get("tool_history", []), state.get("domains", []),
                   get_config()["agent"]["max_tool_calls_per_lap"])
    await emit(config, "plan", {"iteration": iteration, "reasoning": out.reasoning,
                                "tools": [c.model_dump() for c in calls]})
    return {
        "iteration": iteration,
        "pending_tool_calls": calls,
        "plan_reasoning": out.reasoning,
        "lap_log": [{"lap": iteration, "kind": "plan", "reasoning": out.reasoning,
                     "tools": [c.model_dump() for c in calls]}],
        # A new lap starts undecided; last lap's verdict must not leak.
        "stop_reason": None,
        "llm_calls": state.get("llm_calls", 0) + 1,
        "prompt_versions": {**state.get("prompt_versions", {}), "plan": version},
    }
