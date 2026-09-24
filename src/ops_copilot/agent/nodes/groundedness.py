"""Groundedness — tiered. Mostly code; an LLM call only on a flag.

reads   answer, citations[], evidence[]
returns groundedness (GroundednessResult)
model   none for tiers 1-3; strong tier for tier 4, rarely

  Tier 1  every citation's evidence_id resolves to a real entry
  Tier 2  every number in the answer appears in some evidence entry
          (within numeric_tolerance_pct)
  Tier 3  no causal language on a claim whose only support is an
          empty or below-threshold result
  Tier 4  LLM entailment check — fires only when a tier above flags

Tiers 1-3 are free and catch the overwhelming majority. Tier 3 is
the specific one protecting the partial-answer case: it is a
mechanical check that the model did not assert causation while
citing nothing that supports causation.

This does NOT catch the harder failure — a right answer produced
from pretrained knowledge rather than from evidence. That needs
entailment checking, ablation runs, and trap cases, and it lives in
evaluation/, offline, where it can be afforded.

TODO(build): implement tiers 1-3 as pure functions, then tier 4.
"""

from __future__ import annotations

import logging

from ops_copilot.agent.state import AgentState, GroundednessResult

log = logging.getLogger(__name__)


# PHASE 3 SKELETON — a fixed stand-in that proves the loop's shape.
# Replaced with the real logic described above in a later phase.
async def groundedness_node(state: AgentState) -> dict:
    log.info("grounding: always passes (skeleton)")
    return {"groundedness": GroundednessResult(passed=True)}
