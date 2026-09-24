"""Router — LLM call 1. Runs once, outside the loop.

reads   question, session history
returns domains[], query_type, entities, complexity_hint,
        hypothesis_to_test, resolved_entities, entity_notes
model   cheap tier — this is constrained classification, not
        generation, and on a free tier the cheap model has a far
        larger daily budget
never   answers the question, calls a tool, or corrects a VIN
        (resolution happens against real data, downstream)

TODO(build): implement.
  1. load_prompt("router") -> body, version_hash
  2. call LLM with response_format json_object, parse RouterOutput
  3. run entity resolution on any extracted vehicle_id/model_code
     (rag.entity_resolution.resolve) and record entity_notes
  4. record prompt_versions["router"] = version_hash
"""

from __future__ import annotations

import logging

from ops_copilot.agent.state import AgentState

log = logging.getLogger(__name__)


# PHASE 3 SKELETON — a fixed stand-in that proves the loop's shape.
# Replaced with the real logic described above in a later phase.
async def router_node(state: AgentState) -> dict:
    log.info("router: fixed classification (skeleton, no LLM)")
    return {
        "domains": ["diagnostic"],
        "query_type": "explain",
        "complexity_hint": "explain",
        "entities": {"vehicle_id": "VIN-1042"},
        "resolved_entities": {"vehicle_id": "VIN-1042"},
        "entity_notes": [],
        "hypothesis_to_test": None,
    }
