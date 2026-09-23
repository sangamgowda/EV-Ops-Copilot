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

TODO(build): implement.
  1. build evidence block with ids the model can cite
  2. stream tokens; accumulate for the groundedness check
  3. on retry (state["_grounding_retried"]) add the stricter
     instruction naming the specific unsupported claims
"""

from __future__ import annotations

from ops_copilot.agent.state import AgentState


async def synthesize_node(state: AgentState) -> dict:
    raise NotImplementedError("see module docstring")
