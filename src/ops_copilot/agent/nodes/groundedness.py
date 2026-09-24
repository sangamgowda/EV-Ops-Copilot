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

Details that decide whether this is usable or just noisy:

  Tier 2 ignores identifiers (V-042, ERR_401, SB-114, Q2, [e3]),
  dates and version strings — digits inside a name are not claims.
  A number also counts as traced when it is an evidence number
  ROUNDED to the precision written: "36%" traces to a delta of 36.4.
  Small integers are ignored ("two laps", "3 vehicles" in a count
  the model derived by reading the list) below a configured bound.

  Tier 3 exempts sentences that state a gap ("the cause cannot be
  established because no documentation exists") — that "because" is
  the honest partial answer this system wants, not a causal claim.

  Unresolved citations are never escalated to tier 4. An id that does
  not exist cannot be rescued by an entailment check.

After the single synthesis retry has also failed, the answer is
returned with an explicit note listing what could not be verified,
and confidence forced to low. The user sees the hedge; nothing is
silently shipped as if it had passed.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from ops_copilot.agent.context import emit, evidence_block
from ops_copilot.agent.state import AgentState, Citation, Evidence, GroundednessResult
from ops_copilot.llm.client import complete_json
from ops_copilot.observability.tracing import traced
from ops_copilot.settings import get_config, load_prompt

log = logging.getLogger(__name__)

_TAG = re.compile(r"\[(e\d+)\]")
_IDENT = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+\b")   # V-042, ERR_401
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ][\d:.+Z-]+)?\b")
_VERSION = re.compile(r"\b\d+\.\d+\.\d+\b")
_NUMBER = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


# ── tier 1 ───────────────────────────────────────────────────

def unresolved_citations(answer: str, citations: list[Citation], evidence_ids: set[str]) -> list[str]:
    cited = {c.evidence_id for c in citations} | set(_TAG.findall(answer))
    return sorted(cited - evidence_ids, key=lambda s: (len(s), s))


# ── tier 2 ───────────────────────────────────────────────────

# Models write IDs and ranges with typographic dashes as often as with
# "-": "VIN‑1042" (U+2011), "25–40%" (U+2013), "−3.1%" (U+2212). Unless
# they are folded to ASCII first, the ID pattern misses them and the
# digits inside an ID get flagged as an invented number.
_DASHES = str.maketrans({c: "-" for c in "‐‑‒–—―−﹘﹣－"})


def _strip_non_claims(text: str) -> str:
    text = text.translate(_DASHES)
    for pat in (_TAG, _DATE, _VERSION, _IDENT):
        text = pat.sub(" ", text)
    return text


def extract_numbers(text: str) -> list[tuple[str, float]]:
    out = []
    for m in _NUMBER.finditer(_strip_non_claims(text)):
        raw = m.group(0).rstrip(",")
        try:
            out.append((raw, float(raw.replace(",", ""))))
        except ValueError:
            continue
    return out


def _evidence_numbers(evidence: list[Evidence], extra_text: str) -> set[float]:
    pool: set[float] = set()
    for e in evidence:
        for _, v in extract_numbers(e.summary):
            pool |= {v, abs(v)}
        for v in (e.actual, e.baseline, e.delta_pct, e.rerank_score):
            if v is not None:
                pool |= {v, abs(v)}
    for _, v in extract_numbers(extra_text):
        pool |= {v, abs(v)}
    return pool


def _traces(raw: str, value: float, pool: set[float], tol_pct: float) -> bool:
    decimals = len(raw.split(".", 1)[1]) if "." in raw else 0
    for p in pool:
        if round(p, decimals) == round(value, decimals):
            return True
        if p and abs(value - p) / abs(p) * 100 <= tol_pct:
            return True
    return False


def untraceable_numbers(answer: str, evidence: list[Evidence], extra_text: str = "") -> list[str]:
    cfg = get_config()["groundedness"]
    pool = _evidence_numbers(evidence, extra_text)
    floor = cfg["ignore_integers_below"]
    bad = []
    for raw, value in extract_numbers(answer):
        if value.is_integer() and abs(value) < floor:
            continue
        if not _traces(raw, abs(value), pool, cfg["numeric_tolerance_pct"]):
            bad.append(raw)
    return list(dict.fromkeys(bad))


# ── tier 3 ───────────────────────────────────────────────────

def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def unsupported_causal_claims(answer: str, citations: list[Citation], evidence: list[Evidence]) -> list[str]:
    cfg = get_config()["groundedness"]
    by_id = {e.id: e for e in evidence}
    markers = [re.compile(rf"\b{re.escape(m)}\b", re.I) for m in cfg["causal_markers"]]
    exempt = [re.compile(rf"\b{re.escape(m)}\b", re.I) for m in cfg["causal_exempt_markers"]]
    flagged = []
    for sentence in (s.strip() for s in _SENTENCE.split(answer)):
        if not sentence or not any(m.search(sentence) for m in markers):
            continue
        if any(x.search(sentence) for x in exempt):
            continue
        ids = set(_TAG.findall(sentence))
        # Citations are matched to a sentence by wording when the model
        # did not tag inline.
        words = _words(sentence)
        for c in citations:
            cw = _words(c.claim)
            if cw and len(cw & words) / len(cw) >= 0.6:
                ids.add(c.evidence_id)
        if not any(by_id[i].supports_causal_claim() for i in ids if i in by_id):
            flagged.append(sentence)
    return flagged


# ── tier 4 ───────────────────────────────────────────────────

class ClaimCheck(BaseModel):
    claim: str
    supported: bool
    evidence_id: str | None = None


class EntailmentOutput(BaseModel):
    checks: list[ClaimCheck] = Field(default_factory=list)


async def entailment_check(claims: list[str], evidence: list[Evidence]) -> dict[str, bool]:
    system, _ = load_prompt("groundedness")
    user = ("## Evidence\n" + evidence_block(evidence) + "\n\n## Claims to check\n"
            + "\n".join(f"- {c}" for c in claims))
    out = await complete_json("groundedness", system, user, EntailmentOutput)
    verdicts = {c.claim.strip(): c.supported for c in out.checks}
    # A claim the model skipped is unsupported, not forgiven.
    return {c: verdicts.get(c.strip(), False) for c in claims}


# ── node ─────────────────────────────────────────────────────

def check(state: AgentState) -> GroundednessResult:
    """Tiers 1-3. Pure; no network."""
    cfg = get_config()["groundedness"]
    answer = state.get("answer") or ""
    citations = state.get("citations", [])
    evidence = state.get("evidence", [])
    extra = " ".join([state.get("question", ""), *state.get("entity_notes", [])])

    unresolved = unresolved_citations(answer, citations, {e.id for e in evidence}) \
        if cfg["require_citation_ids_resolve"] else []
    numbers = untraceable_numbers(answer, evidence, extra) \
        if cfg["require_numbers_trace_to_evidence"] else []
    causal = unsupported_causal_claims(answer, citations, evidence) \
        if cfg["block_causal_language_without_support"] else []

    failures = [name for name, hit in (("tier1_citations", unresolved), ("tier2_numbers", numbers),
                                        ("tier3_causal", causal)) if hit]
    return GroundednessResult(passed=not failures, tier_failures=failures,
                              unresolved_citations=unresolved, untraceable_numbers=numbers,
                              unsupported_causal_claims=causal)


def _hedge(answer: str, g: GroundednessResult) -> str:
    items = [*g.untraceable_numbers, *(f'"{c}"' for c in g.unsupported_causal_claims),
             *(f"citation {c}" for c in g.unresolved_citations)]
    return (answer.rstrip() + "\n\n**Note:** parts of this answer could not be verified against "
            "the gathered evidence and should be treated as unconfirmed: " + "; ".join(items) + ".")


@traced("grounding")
async def groundedness_node(state: AgentState, config: Any = None) -> dict:
    cfg = get_config()["groundedness"]
    result = check(state)
    calls = state.get("llm_calls", 0)

    escalatable = result.untraceable_numbers + result.unsupported_causal_claims
    if not result.passed and cfg["escalate_to_llm_on_flag"] and escalatable and not result.unresolved_citations:
        try:
            verdicts = await entailment_check(escalatable, state.get("evidence", []))
        except Exception as exc:
            # The model is only a second opinion on flags code already
            # raised. Without it, the code tiers' verdict stands.
            log.warning("tier-4 entailment check failed (%s); keeping code-tier result", exc)
            verdicts = {c: False for c in escalatable}
        calls += 1
        result.escalated_to_llm = True
        result.untraceable_numbers = [n for n in result.untraceable_numbers if not verdicts[n]]
        result.unsupported_causal_claims = [c for c in result.unsupported_causal_claims if not verdicts[c]]
        result.tier_failures = [t for t, left in (("tier2_numbers", result.untraceable_numbers),
                                                  ("tier3_causal", result.unsupported_causal_claims))
                                if left]
        result.passed = not result.tier_failures

    await emit(config, "groundedness", result.model_dump())
    out: dict[str, Any] = {"groundedness": result, "llm_calls": calls}
    if not result.passed and state.get("_grounding_retried"):
        # Second failure: the graph ends here. Ship the hedge, not a
        # silent pass.
        out["answer"] = _hedge(state.get("answer") or "", result)
        out["confidence"] = "low"
    return out
