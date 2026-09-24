"""Observe — no LLM. Shapes raw results into Evidence.

reads   raw tool results
returns evidence[] (appended), tool_history (appended)
model   none
never   discards an empty or failed result

The comparison fields (actual, baseline, delta_pct, verdict) are
computed HERE, in code. The model never does this arithmetic — it is
unreliable at it, and doing it here means Reflect can check "do I
have a verdict for this component" almost deterministically.

Empty and failed results become Evidence entries with status EMPTY
or FAILED. That is deliberate: an absence the agent can see is
something it can report; an absence it cannot see is something it
invents around.

TODO(build): implement.
  1. for SQL results: match rows against baseline rows where
     present, compute delta_pct and Verdict using tolerance_pct
  2. for RAG results: attach rerank_score; if below
     retrieval.confidence_threshold, status = BELOW_THRESHOLD
  3. write raw payload to the side store, keep only raw_ref
  4. assign sequential evidence ids via next_evidence_id()
"""

from __future__ import annotations

import logging

from ops_copilot.agent.state import AgentState, Evidence

log = logging.getLogger(__name__)


# PHASE 3 SKELETON — a fixed stand-in that proves the loop's shape.
# Replaced with the real logic described above in a later phase.
async def observe_node(state: AgentState) -> dict:
    iteration = state.get("iteration", 1)
    offset = len(state.get("evidence", []))
    raw = state.get("raw_results", [])
    new = [
        Evidence(id=f"e{offset + i}", tool=r["tool"], iteration=iteration,
                 summary=f"fake result from lap {iteration}", tool_args=r["args"])
        for i, r in enumerate(raw, start=1)
    ]
    log.info("observe: lap %d added %d evidence (total now %d)",
             iteration, len(new), offset + len(new))
    # Only the NEW entries are returned: the state's `add` reducer appends
    # them. Returning the whole list here would duplicate every lap.
    return {
        "evidence": new,
        "tool_history": [f"{r['tool']}:{r['args']}" for r in raw],
        "raw_results": [],
    }
