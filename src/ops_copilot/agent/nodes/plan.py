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

TODO(build): implement.
  1. build context: question + resolved_entities + schema_config
     + evidence summaries + tool_history + next_question
  2. call LLM, parse PlanOutput
  3. dedupe tool_calls against tool_history before returning
"""

from __future__ import annotations

from ops_copilot.agent.state import AgentState


async def plan_node(state: AgentState) -> dict:
    raise NotImplementedError("see module docstring")
