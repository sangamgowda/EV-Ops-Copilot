"""Failure signatures: why a flagged turn went wrong, read in code.

A thumbs-down says THAT an answer was bad, not WHY. The why is already
in the turn's recorded outcome — the same fields the trace carries — so
it is read here deterministically. No LLM: bucketing must be stable,
or the cluster sizes that decide what gets fixed first mean nothing.

Checked in this order; the first that matches names the cluster, and
every match is kept as a signal:

  wrong_domain       the router fell back, or a vehicle question was
                     routed as business-only
  wrong_tool         no tool ran, or a lookup ran without the SQL tool
  step_failed        another step errored and fell back
  sql_failed         the SQL tool was rejected or errored
  empty_retrieval    documents were searched and nothing usable came back
  grounding_failure  the answer failed its own grounding check
  hit_limit          stopped on the lap cap or the time ceiling
  slow               nothing wrong, but slower than `slow_ms`
  no_signal          the trace looks healthy: a person has to read it —
                     a wrong answer, a format the user did not want, or
                     a user who was mistaken
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ORDER = ("wrong_domain", "wrong_tool", "step_failed", "sql_failed", "empty_retrieval",
         "grounding_failure", "hit_limit", "slow", "no_signal")


@dataclass
class Signature:
    cluster: str
    signals: list[str] = field(default_factory=list)


def _statuses(outcome: dict[str, Any], tool: str) -> list[str]:
    out = []
    for item in outcome.get("evidence_statuses") or []:
        name, _, status = str(item).partition(":")
        if name == tool:
            out.append(status)
    return out


def signature(outcome: dict[str, Any] | None, entities: dict[str, Any] | None = None,
              slow_ms: int = 20000) -> Signature:
    """`outcome` is conversation_turns.outcome; `entities` its resolved
    entities. A turn with no recorded outcome (it crashed, or predates
    outcome recording) has nothing to read and is `no_signal`."""
    o = outcome or {}
    entities = entities or {}
    hits: list[str] = []

    node_errors = [str(e) for e in o.get("node_errors") or []]
    domains = o.get("domains") or []
    if any(e.startswith("router") for e in node_errors) or (entities.get("vehicle_id") and domains == ["business"]):
        hits.append("wrong_domain")

    tools = o.get("tools_selected") or []
    if (outcome is not None and not tools) or (o.get("query_type") == "lookup" and "structured_query_tool" not in tools):
        hits.append("wrong_tool")

    if any(not e.startswith("router") for e in node_errors):
        hits.append("step_failed")

    sql = _statuses(o, "structured_query_tool")
    if sql and all(s == "failed" for s in sql):
        hits.append("sql_failed")

    rag = _statuses(o, "rag_retrieval_tool")
    if rag and not any(s == "ok" for s in rag):
        hits.append("empty_retrieval")

    if o.get("grounded") is False:
        hits.append("grounding_failure")

    if o.get("stop_reason") in ("cap_reached", "timeout"):
        hits.append("hit_limit")

    if not hits and (o.get("latency_ms") or 0) > slow_ms:
        hits.append("slow")

    hits = [h for h in ORDER if h in hits] or ["no_signal"]
    return Signature(cluster=hits[0], signals=hits)
