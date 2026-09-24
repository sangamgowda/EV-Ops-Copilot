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

How a comparison is recognised: Plan is told (plan.md) to return
comparisons with columns named `actual` and `baseline` (plus
optional `metric`, `unit`, `tolerance_pct`). A few common synonyms
are accepted too (`nominal_value`, `avg_value`, ...). A row with
both halves becomes one Evidence entry with a computed verdict — so
"current draw 36% above baseline" is a fact code produced, and the
model only has to read it.

SQL rejections map to FAILED with the validator's reasons in
`error`; those reasons are written for Plan to act on next lap.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from typing import Any

from ops_copilot.agent import raw_store
from ops_copilot.agent.context import call_key, emit, guarded
from ops_copilot.agent.state import AgentState, Evidence, EvidenceStatus, Verdict
from ops_copilot.observability.tracing import traced
from ops_copilot.settings import get_config

ACTUAL_COLS = ("actual", "actual_value", "observed", "measured", "avg_value", "avg_actual")
BASELINE_COLS = ("baseline", "baseline_value", "nominal_value", "nominal", "expected")
METRIC_COLS = ("metric", "metric_name")


def _first(row: dict[str, Any], names: tuple[str, ...]) -> Any:
    lowered = {k.lower(): v for k, v in row.items()}
    for n in names:
        if n in lowered and lowered[n] is not None:
            return lowered[n]
    return None


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def compare(actual: float, baseline: float | None, tolerance_pct: float) -> tuple[float | None, Verdict]:
    """delta_pct and verdict. Pure; this is the arithmetic the model never does."""
    if baseline is None:
        return None, Verdict.NO_BASELINE
    if baseline == 0:
        return None, Verdict.NOT_APPLICABLE
    delta = round((actual - baseline) / abs(baseline) * 100, 1)
    if abs(delta) <= tolerance_pct:
        return delta, Verdict.NORMAL
    return delta, Verdict.ABOVE_NORMAL if delta > 0 else Verdict.BELOW_NORMAL


def _fmt_row(row: dict[str, Any]) -> str:
    def cell(v: Any) -> str:
        if isinstance(v, float):
            return f"{v:.4g}"
        s = str(v)
        return s if len(s) <= 60 else s[:57] + "..."
    return ", ".join(f"{k}={cell(v)}" for k, v in row.items())


def _sql_evidence(res: dict[str, Any], base: dict[str, Any]) -> list[Evidence]:
    cfg = get_config()["agent"]
    status = res.get("status")
    if status in ("rejected", "failed"):
        why = "; ".join(res.get("reasons", [])) or res.get("error", "unknown error")
        label = "rejected by validation" if status == "rejected" else "failed"
        return [Evidence(**base, status=EvidenceStatus.FAILED, summary=f"SQL {label}: {why}",
                         error=why)]
    rows: list[dict[str, Any]] = res.get("rows", [])
    if not rows:
        return [Evidence(**base, status=EvidenceStatus.EMPTY,
                         summary="Query ran and returned no rows.")]

    limit = cfg["summary_max_rows"]
    comparisons = [r for r in rows if _num(_first(r, ACTUAL_COLS)) is not None
                   and _first(r, BASELINE_COLS) is not None]
    if comparisons:
        out = []
        for r in comparisons[:limit]:
            actual = _num(_first(r, ACTUAL_COLS))
            baseline = _num(_first(r, BASELINE_COLS))
            tol = _num(_first(r, ("tolerance_pct",))) or cfg["default_tolerance_pct"]
            delta, verdict = compare(actual, baseline, tol)
            metric = _first(r, METRIC_COLS) or "value"
            unit = _first(r, ("unit",))
            context = {k: v for k, v in r.items()
                       if k.lower() not in (*ACTUAL_COLS, *BASELINE_COLS, *METRIC_COLS,
                                            "unit", "tolerance_pct")}
            summary = (f"{metric}: {actual:g}{' ' + unit if unit else ''} vs baseline "
                       f"{baseline:g} ({'n/a' if delta is None else f'{delta:+.1f}%'}, "
                       f"{verdict.value}, tolerance ±{tol:g}%)")
            if context:
                summary += f" [{_fmt_row(context)}]"
            out.append(Evidence(**base, summary=summary, metric=str(metric), actual=actual,
                                baseline=baseline, unit=unit, delta_pct=delta, verdict=verdict))
        return out

    shown = rows[:limit]
    lines = [_fmt_row(r) for r in shown]
    more = f" (+{len(rows) - len(shown)} more rows in raw store)" if len(rows) > len(shown) else ""
    summary = f"{len(rows)} row(s){more}:\n  " + "\n  ".join(lines)
    ev = Evidence(**base, summary=summary)
    # A single scalar ("what is the average current draw") is still a
    # measurement Reflect should be able to see as one.
    if len(rows) == 1 and len(rows[0]) == 1:
        (name, value), = rows[0].items()
        if _num(value) is not None:
            ev.metric, ev.actual = name, _num(value)
    return [ev]


def _coverage(chunk: dict[str, Any], args: dict[str, Any]) -> str:
    """Which models a document covers, stated in the evidence itself.

    Retrieval boosts documents for the asked-about vehicle's model but
    never filters, so a well-ranked document may not apply to that
    vehicle at all. Unless the evidence says so, the model reads a
    retrieved bulletin as the explanation — confident nonsense on
    exactly the vehicles the documentation does not cover.
    """
    models = chunk.get("applies_to_models") or []
    note = f" [applies to: {', '.join(models)}]" if models else ""
    if args.get("entity_id") and not chunk.get("boosted"):
        note += " [does NOT list the asked-about vehicle's model]"
    return note


def _rag_evidence(res: dict[str, Any], base: dict[str, Any]) -> list[Evidence]:
    threshold = get_config()["retrieval"]["confidence_threshold"]
    status = res.get("status")
    query = res.get("query") or base["tool_args"].get("query", "")
    if status == "failed":
        return [Evidence(**base, status=EvidenceStatus.FAILED,
                         summary=f"Document search failed: {res.get('error')}", error=res.get("error"))]

    # Re-checked here rather than trusted: the threshold is the one
    # thing standing between weak context and a confident answer.
    chunks = [c for c in res.get("chunks", []) if c.get("rerank_score", 0) >= threshold]
    if not chunks:
        best = res.get("best_score")
        if status == "below_threshold" or best is not None:
            return [Evidence(**base, status=EvidenceStatus.BELOW_THRESHOLD, rerank_score=best,
                             summary=(f"No usable documentation for '{query}': closest match "
                                      f"scored {best}, below the {threshold} threshold."))]
        return [Evidence(**base, status=EvidenceStatus.EMPTY,
                         summary=f"No documents in the {res.get('domain')} domain matched '{query}'.")]

    by_doc: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for c in chunks:
        by_doc.setdefault(c["doc_id"], []).append(c)
    out = []
    for doc_id, cs in by_doc.items():
        # Drop the context header line: the title is already in the
        # evidence head, and repeating it costs tokens every lap.
        bodies = [c["content"].split("\n\n", 1)[-1].strip() for c in cs[:2]]
        text = " … ".join(bodies)
        if len(text) > 700:
            text = text[:697] + "..."
        out.append(Evidence(**base, source_doc=doc_id,
                            chunk_ids=[c["chunk_id"] for c in cs],
                            rerank_score=max(c["rerank_score"] for c in cs),
                            summary=f"{cs[0]['title']} ({cs[0].get('section_path') or 'body'})"
                                    f"{_coverage(cs[0], base['tool_args'])}: {text}"))
    return out


def _fallback(state: AgentState, exc: Exception) -> dict:
    return {"raw_results": []}

@traced("observe")
@guarded("observe", _fallback)
async def observe_node(state: AgentState, config: Any = None) -> dict:
    iteration = state.get("iteration", 1)
    offset = len(state.get("evidence", []))
    new: list[Evidence] = []
    history: list[str] = []

    for raw in state.get("raw_results", []):
        res = raw["result"]
        history.append(call_key({"tool": raw["tool"], "args": raw["args"]}))
        base = {"id": "pending", "tool": raw["tool"], "iteration": iteration,
                "tool_args": raw["args"], "latency_ms": raw.get("latency_ms"),
                "raw_ref": await raw_store.put(state["turn_id"], raw) if res.get("status") == "ok" else None}
        entries = (_sql_evidence(res, base) if raw["tool"] == "structured_query_tool"
                   else _rag_evidence(res, base))
        new.extend(entries)

    for i, e in enumerate(new, start=offset + 1):
        e.id = f"e{i}"

    await emit(config, "evidence", {"iteration": iteration, "items": [
        {"id": e.id, "tool": e.tool, "status": e.status.value, "summary": e.summary[:300]}
        for e in new]})
    return {"evidence": new, "tool_history": history, "raw_results": []}
