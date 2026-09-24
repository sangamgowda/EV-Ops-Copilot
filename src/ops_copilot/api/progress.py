"""Turn the loop's internal events into progress lines a person reads.

Nodes emit structured events ("plan" with the SQL it wrote, "evidence"
with statuses, ...). Streaming those raw would show a user SQL and
evidence ids; streaming nothing would leave six seconds of silence.
This maps each to one plain sentence — "Checking current_draw and
payload against normal values", "Found SB-114 — Sustained overload" —
and drops what a reader does not need.

Pure functions, no IO: `to_progress(event, data)` returns the lines to
send (possibly none).
"""

from __future__ import annotations

import functools
import re
from typing import Any

from ops_copilot.settings import get_schema_config


@functools.lru_cache(maxsize=1)
def _metric_names() -> tuple[str, ...]:
    cols = get_schema_config()["tables"]["vehicle_telemetry"]["columns"]
    return tuple(cols["metric_name"].get("allowed_values", []))


def _describe_sql(sql: str) -> str:
    low = sql.lower()
    metrics = [m for m in _metric_names() if re.search(rf"\b{re.escape(m)}\b", low)]
    if "error_codes" in low:
        codes = re.findall(r"\b[a-z]{2,5}_\d{2,4}\b", low)
        return f"Looking up error code {codes[0].upper()}" if codes else "Looking up error codes"
    if "sales_transactions" in low:
        return "Querying sales records"
    if "service_events" in low:
        return "Checking service history"
    if metrics:
        what = " and ".join(metrics[:3]) if len(metrics) <= 3 else f"{', '.join(metrics[:3])} and more"
        if "vehicle_baseline_specs" in low:
            return f"Checking {what} against normal values"
        return f"Reading {what}"
    return "Querying the database"


def _first_line(text: str, limit: int = 140) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"


def to_progress(event: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    """Progress payloads for one internal event: {stage, lap, message}."""
    lap = data.get("iteration")

    def p(stage: str, message: str) -> dict[str, Any]:
        return {"stage": stage, "lap": lap, "message": message}

    if event == "routed":
        subject = data.get("entities", {}).get("vehicle_id")
        about = f" about {subject}" if subject else ""
        out = [p("router", f"Understood: a {' and '.join(data['domains'])} question{about}")]
        out += [p("router", note) for note in data.get("notes", [])]
        return out
    if event == "plan":
        msgs = []
        for t in data.get("tools", []):
            if t["tool"] == "structured_query_tool":
                msgs.append(p("plan", _describe_sql(str(t["args"].get("sql", "")))))
            elif t["tool"] == "rag_retrieval_tool":
                msgs.append(p("plan", f"Searching documents: “{t['args'].get('query', '')}”"))
        return msgs or [p("plan", "Nothing new to look up")]
    if event == "evidence":
        out = []
        for e in data.get("items", []):
            status = e["status"]
            if status == "ok":
                out.append(p("observe", f"Found: {_first_line(e['summary'])}"))
            elif status == "empty":
                out.append(p("observe", "That lookup returned nothing"))
            elif status == "below_threshold":
                out.append(p("observe", "No documentation matched closely enough"))
            else:
                out.append(p("observe", "A lookup failed; continuing without it"))
        return out
    if event == "reflect":
        reason = data.get("stop_reason")
        if reason is None:
            nxt = data.get("next_question")
            return [p("reflect", f"Need more: {nxt}" if nxt else "Need more evidence")]
        return [p("reflect", {
            "complete": "Enough evidence — writing the answer",
            "exhausted": "Nothing more can be found — writing a partial answer",
            "cap_reached": "Lap limit reached — writing with what was found",
            "no_new_evidence": "No new evidence — writing with what was found",
        }.get(reason, "Writing the answer"))]
    if event == "groundedness":
        return [p("check", "Answer checked against the evidence" if data.get("passed")
                  else "Answer did not match the evidence — correcting it")]
    if event == "node_error":
        return [p(data.get("node", "agent"), f"The {data.get('node', 'agent')} step failed; continuing")]
    if event == "status" and data.get("node") == "router":
        return [p("router", "Reading the question")]
    return []
