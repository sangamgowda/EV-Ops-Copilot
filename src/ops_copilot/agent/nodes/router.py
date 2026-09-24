"""Router — LLM call 1. Runs once, outside the loop.

reads   question, session history
returns domains[], query_type, entities, complexity_hint,
        hypothesis_to_test, resolved_entities, entity_notes
model   cheap tier — this is constrained classification, not
        generation, and on a free tier the cheap model has a far
        larger daily budget
never   answers the question, calls a tool, or corrects a VIN
        (resolution happens against real data, downstream)

The vehicle id the model extracted is checked against real rows
here, before any search — through the MCP server's
resolve_entity_tool, because the agent process holds no database
credentials. The outcome is recorded as an Evidence
entry (tool "entity_resolution") as well as a note, so "VIN-9999 does
not exist" is something Synthesize can cite rather than something it
has to take on faith.

An empty `domains` list from the model is widened to both domains:
searching everything is recoverable, searching nothing is not.
"""

from __future__ import annotations

import logging
from typing import Any

from ops_copilot.agent.context import configurable, emit, guarded
from ops_copilot.agent.state import (
    AgentState,
    Domain,
    Evidence,
    EvidenceStatus,
    RouterOutput,
)
from ops_copilot.llm.client import complete_json
from ops_copilot.observability.tracing import traced
from ops_copilot.mcp_client.client import get_client
from ops_copilot.settings import load_prompt

log = logging.getLogger(__name__)


async def _resolve(entities: dict[str, Any], evidence_offset: int, mcp: Any,
                   turn_id: str) -> tuple[dict, list[str], list[Evidence]]:
    resolved: dict[str, Any] = {k: v for k, v in entities.items() if v}
    notes: list[str] = []
    evidence: list[Evidence] = []
    raw = entities.get("vehicle_id")
    if not raw:
        return resolved, notes, evidence

    try:
        tagged = await mcp.call_tool("resolve_entity_tool", {"vehicle_id": raw}, turn_id)
        r = tagged["result"]
        if r.get("status") not in ("exact", "fuzzy", "ambiguous", "not_found"):
            raise RuntimeError(r.get("error") or f"unexpected result {r}")
    except Exception as exc:
        # Resolution is a safety net, not a gate. If the database is
        # unreachable the tools will fail too, and say so as evidence.
        log.warning("entity resolution failed for %r: %s", raw, exc)
        return resolved, notes, evidence

    eid = f"e{evidence_offset + 1}"
    if r["status"] in ("exact", "fuzzy"):
        resolved["vehicle_id"] = r["value"]
        if r.get("note"):
            notes.append(r["note"])
            evidence.append(Evidence(id=eid, tool="entity_resolution", summary=r["note"],
                                     tool_args={"raw": raw}, iteration=0))
    else:
        # Unresolved: drop it so Plan cannot query a VIN that does
        # not exist, and keep the absence visible.
        resolved.pop("vehicle_id", None)
        resolved["vehicle_id_unresolved"] = raw
        notes.append(r.get("note") or f"Vehicle '{raw}' could not be resolved.")
        evidence.append(Evidence(id=eid, tool="entity_resolution", status=EvidenceStatus.EMPTY,
                                 summary=notes[-1], tool_args={"raw": raw,
                                 "suggestions": r.get("suggestions") or []}, iteration=0))
    return resolved, notes, evidence


def _fallback(state: AgentState, exc: Exception) -> dict:
    # Classification failed: search both domains rather than guess one,
    # and treat it as a "why" question so Reflect still judges the lap.
    return {"domains": [d.value for d in Domain], "query_type": "explain",
            "complexity_hint": "explain", "entities": {}, "resolved_entities": {},
            "entity_notes": [], "hypothesis_to_test": None}


@traced("router")
@guarded("router", _fallback)
async def router_node(state: AgentState, config: Any = None) -> dict:
    await emit(config, "status", {"node": "router", "message": "Understanding the question"})
    system, version = load_prompt("router")
    out = await complete_json("router", system, state["question"], RouterOutput)

    domains = list(dict.fromkeys(d.value for d in out.domains)) or [d.value for d in Domain]
    mcp = configurable(config).get("mcp") or get_client()
    resolved, notes, evidence = await _resolve(out.entities, len(state.get("evidence", [])),
                                               mcp, state.get("turn_id", ""))

    await emit(config, "routed", {"domains": domains, "query_type": out.query_type.value,
                                  "entities": resolved, "notes": notes})
    return {
        "domains": domains,
        "query_type": out.query_type.value,
        "entities": out.entities,
        "complexity_hint": out.complexity_hint.value,
        "hypothesis_to_test": out.hypothesis_to_test,
        "resolved_entities": resolved,
        "entity_notes": notes,
        "evidence": evidence,
        "llm_calls": state.get("llm_calls", 0) + 1,
        "prompt_versions": {**state.get("prompt_versions", {}), "router": version},
    }
