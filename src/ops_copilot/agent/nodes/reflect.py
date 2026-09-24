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

TODO(build): implement.
  1. if complexity_hint in config skip_reflect_on and iteration == 1
     -> return sufficient immediately, no LLM call
  2. if no new evidence since last lap -> NO_NEW_EVIDENCE
  3. if iteration >= max_iterations -> CAP_REACHED, partial=True
  4. else call LLM, parse ReflectOutput
"""

from __future__ import annotations

import logging

from ops_copilot.agent.state import AgentState, StopReason

log = logging.getLogger(__name__)

# Skeleton only: pretend the evidence becomes sufficient on this lap.
SKELETON_SUFFICIENT_ON_LAP = 3


# PHASE 3 SKELETON — a fixed stand-in that proves the loop's shape.
# Replaced with the real logic described above in a later phase.
async def reflect_node(state: AgentState) -> dict:
    iteration = state.get("iteration", 1)
    if iteration < SKELETON_SUFFICIENT_ON_LAP:
        log.info("reflect: lap %d -> not sufficient, go again (skeleton)", iteration)
        return {"stop_reason": None, "partial": False, "open_gaps": ["mechanism"],
                "next_question": f"fake narrower question after lap {iteration}"}
    log.info("reflect: lap %d -> sufficient (skeleton)", iteration)
    return {"stop_reason": StopReason.COMPLETE.value, "partial": False,
            "open_gaps": [], "next_question": None}
