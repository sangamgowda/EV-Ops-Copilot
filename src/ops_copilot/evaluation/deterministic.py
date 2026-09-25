"""Evaluators that need no LLM. Fast, free, never flaky.

  domains          did the router get domains[] right (set equality)
  entity           did the vehicle resolve to the right id — or, for a
                   vehicle that does not exist, to nothing
  tools            every required tool was used
  key_args         some call to a tool carried the key arguments
                   (a vehicle id, a metric name, an error code)
  forbidden_args   no call carried something it must not (e.g. SQL
                   against a vehicle that does not exist)
  iterations       did it loop more than the case needs
  stop_reason      did it stop for an acceptable reason
  facts            numbers present in the answer, within tolerance
  must_contain     phrases (or any-of groups) the answer needs
  must_not_contain phrases the answer must not assert
  abstain          when the right answer is "I can't tell", did it say so
  grounded         the turn's own grounding check passed
  citations        every cited id resolves to gathered evidence

Anything checkable in code is checked in code. The judge only gets
what genuinely needs reading, which shrinks the surface for judge
bias considerably.

Everything here is a pure function of the turn's final state (reduced
to an Outcome) and the case's `expected` block. A check a case does
not ask for is skipped, not passed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ops_copilot.agent.nodes.groundedness import extract_numbers

_TAG = re.compile(r"\[\s*(e\d+)\s*\]")
_DASHES = str.maketrans({c: "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"})
_SPACES = str.maketrans({c: " " for c in "\u00a0\u202f\u2009"})

# How an answer says "I don't know" when it is honest about it. Used
# only when a case expects abstention; a case that expects an answer
# is not penalised for hedging here — that is the judge's call.
ABSTAIN_MARKERS = (
    "cannot", "can't", "could not", "couldn't", "unable", "no data", "not found",
    "does not exist", "doesn't exist", "no vehicle", "no such", "no record",
    "no document", "no documentation", "not available", "not in the", "no information",
    "not recorded", "not determine", "not be determined", "undetermined", "unknown",
    "no evidence", "not covered", "isn't covered", "not provided", "no usable",
)


@dataclass
class Outcome:
    """What a turn did, reduced to the fields the checks read."""

    answer: str
    domains: list[str] = field(default_factory=list)
    query_type: str | None = None
    vehicle_id: str | None = None
    vehicle_unresolved: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)   # {tool, args, lap}
    iterations: int = 0
    stop_reason: str | None = None
    partial: bool = False
    confidence: str | None = None
    cited: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    tool_evidence_statuses: list[str] = field(default_factory=list)
    grounded: bool | None = None
    entity_notes: list[str] = field(default_factory=list)


def outcome_from_state(state: dict[str, Any]) -> Outcome:
    calls = [{"tool": t.get("tool"), "args": t.get("args", {}), "lap": e.get("lap")}
             for e in state.get("lap_log", []) if e.get("kind") == "plan"
             for t in e.get("tools", [])]
    resolved = state.get("resolved_entities") or {}
    g = state.get("groundedness")
    answer = state.get("answer") or ""
    return Outcome(
        answer=answer,
        domains=list(state.get("domains") or []),
        query_type=state.get("query_type"),
        vehicle_id=resolved.get("vehicle_id"),
        vehicle_unresolved=resolved.get("vehicle_id_unresolved"),
        tool_calls=calls,
        iterations=int(state.get("iteration") or 0),
        stop_reason=state.get("stop_reason"),
        partial=bool(state.get("partial")),
        confidence=state.get("confidence"),
        cited=sorted({c.evidence_id for c in state.get("citations", [])} | set(_TAG.findall(answer))),
        evidence_ids=[e.id for e in state.get("evidence", [])],
        tool_evidence_statuses=[getattr(e.status, "value", str(e.status)) for e in state.get("evidence", [])
                                if e.tool in ("structured_query_tool", "rag_retrieval_tool")],
        grounded=None if g is None else bool(g.passed),
        entity_notes=list(state.get("entity_notes") or []),
    )


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


def _norm(text: str) -> str:
    return " ".join(text.translate(_DASHES).translate(_SPACES).lower().split())


def contains(answer: str, phrase: str) -> bool:
    return _norm(phrase) in _norm(answer)


# ── individual checks ────────────────────────────────────────

def check_domains(o: Outcome, expected: list[str]) -> Check:
    ok = set(o.domains) == set(expected)
    return Check("domains", ok, f"got {sorted(o.domains)}, expected {sorted(expected)}")


def check_entity(o: Outcome, expected: str) -> Check:
    """`expected` is a vehicle id, or "not_found" for one that must not
    resolve to anything."""
    if expected == "not_found":
        ok = o.vehicle_id is None
        return Check("entity", ok, f"resolved to {o.vehicle_id!r}; expected nothing")
    ok = o.vehicle_id == expected
    return Check("entity", ok, f"resolved to {o.vehicle_id!r}, expected {expected!r}")


def check_tools(o: Outcome, required: list[str]) -> Check:
    used = {c["tool"] for c in o.tool_calls}
    missing = sorted(set(required) - used)
    return Check("tools", not missing, f"used {sorted(used)}" + (f"; missing {missing}" if missing else ""))


def _args_text(call: dict[str, Any]) -> str:
    return _norm(json.dumps(call.get("args", {}), ensure_ascii=False))


def check_key_args(o: Outcome, specs: list[dict[str, Any]]) -> Check:
    """Each spec: {"tool": name, "contains": [...]}. Some single call to
    that tool must carry every listed fragment."""
    missing = []
    for spec in specs:
        want = [_norm(x) for x in spec["contains"]]
        if not any(c["tool"] == spec["tool"] and all(w in _args_text(c) for w in want)
                   for c in o.tool_calls):
            missing.append(f"{spec['tool']}∋{spec['contains']}")
    return Check("key_args", not missing, "; ".join(missing) or "all present")


def check_forbidden_args(o: Outcome, specs: list[dict[str, Any]]) -> Check:
    hits = []
    for spec in specs:
        want = [_norm(x) for x in spec["contains"]]
        for c in o.tool_calls:
            if (spec.get("tool") in (None, c["tool"])) and all(w in _args_text(c) for w in want):
                hits.append(f"{c['tool']}∋{spec['contains']}")
    return Check("forbidden_args", not hits, "; ".join(hits) or "none")


def check_iterations(o: Outcome, max_iterations: int) -> Check:
    return Check("iterations", o.iterations <= max_iterations, f"{o.iterations} of max {max_iterations}")


def check_stop_reason(o: Outcome, allowed: list[str]) -> Check:
    return Check("stop_reason", o.stop_reason in allowed, f"{o.stop_reason!r}, allowed {allowed}")


def check_facts(o: Outcome, facts: list[dict[str, Any]]) -> Check:
    """Each fact: {"label", "value", "tolerance"}. Present if any number
    in the answer is within tolerance of the value (sign ignored: "38%
    above" and "+38%" are the same claim)."""
    numbers = [abs(v) for _, v in extract_numbers(o.answer.translate(_SPACES))]
    missing = [f"{f['label']}≈{f['value']}±{f['tolerance']}" for f in facts
               if not any(abs(n - abs(f["value"])) <= f["tolerance"] for n in numbers)]
    return Check("facts", not missing, "; ".join(missing) or f"{len(facts)} present")


def check_must_contain(o: Outcome, items: list[str | list[str]]) -> Check:
    """Each item is a phrase, or a list of alternatives (any one will do)."""
    missing = []
    for item in items:
        options = [item] if isinstance(item, str) else item
        if not any(contains(o.answer, p) for p in options):
            missing.append(" | ".join(options))
    return Check("must_contain", not missing, "; ".join(missing) or "all present")


def check_must_not_contain(o: Outcome, phrases: list[str]) -> Check:
    found = [p for p in phrases if contains(o.answer, p)]
    return Check("must_not_contain", not found, "; ".join(found) or "none present")


def says_it_cannot_tell(o: Outcome) -> bool:
    return o.partial or any(contains(o.answer, m) for m in ABSTAIN_MARKERS)


def check_abstain(o: Outcome) -> Check:
    ok = says_it_cannot_tell(o)
    return Check("abstain", ok, "abstained" if ok else "answered as if it knew")


def check_grounded(o: Outcome) -> Check:
    return Check("grounded", o.grounded is True, f"grounding {o.grounded}")


def check_citations(o: Outcome) -> Check:
    dangling = sorted(set(o.cited) - set(o.evidence_ids))
    return Check("citations", not dangling, f"unresolved {dangling}" if dangling else f"{len(o.cited)} resolve")


# ── a whole case ─────────────────────────────────────────────

def run_checks(o: Outcome, expected: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []
    if "domains" in expected:
        checks.append(check_domains(o, expected["domains"]))
    if "vehicle_id" in expected:
        checks.append(check_entity(o, expected["vehicle_id"]))
    if expected.get("tools"):
        checks.append(check_tools(o, expected["tools"]))
    if expected.get("key_args"):
        checks.append(check_key_args(o, expected["key_args"]))
    if expected.get("forbidden_args"):
        checks.append(check_forbidden_args(o, expected["forbidden_args"]))
    if "max_iterations" in expected:
        checks.append(check_iterations(o, expected["max_iterations"]))
    if expected.get("stop_reasons"):
        checks.append(check_stop_reason(o, expected["stop_reasons"]))
    if expected.get("facts"):
        checks.append(check_facts(o, expected["facts"]))
    if expected.get("must_contain"):
        checks.append(check_must_contain(o, expected["must_contain"]))
    if expected.get("must_not_contain"):
        checks.append(check_must_not_contain(o, expected["must_not_contain"]))
    if expected.get("abstain"):
        checks.append(check_abstain(o))
    if expected.get("grounded"):
        checks.append(check_grounded(o))
    checks.append(check_citations(o))
    return checks
