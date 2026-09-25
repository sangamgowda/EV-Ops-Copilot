"""Turn real failures into permanent tests — with a person in the loop.

  1. collect flagged turns: thumbs-down, and implicit ones (a rephrase)
  2. bucket each by FAILURE SIGNATURE, read from the turn's recorded
     outcome in code (signature.py). No LLM.
  3. sort the queue by cluster size: fix what is frequent, not what
     was loudest
  4. a PERSON writes what should have happened. This is not
     automatable, and pretending otherwise is the mistake: a
     thumbs-down can mean the user was wrong, or wanted another
     format. Auto-promoting a raw failure teaches the test set to
     expect the same mistake. So promotion here REQUIRES a reference
     answer typed by the reviewer, and a dismissal requires a reason.
  5. the case is written to golden/promoted.yaml tagged with its
     cluster; scripts/build_golden.py merges it into golden.jsonl
  6. `run_eval.py --promoted` runs these on every prompt or config
     change and exits non-zero when one regresses

And the number that matters is cluster resolution, not case count:
did a fix clear the whole cluster, or only the one case that was
promoted? `cluster_status` answers that — including flags that keep
arriving in a cluster after its cases were promoted.

scripts/review_feedback.py is the interface; this module holds the
logic so it can be tested.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from rapidfuzz import fuzz
from sqlalchemy import select, update

from ops_copilot.db.engine import owner_engine
from ops_copilot.db.models import conversation_turns, flagged_interactions
from ops_copilot.feedback.signature import ORDER, signature
from ops_copilot.settings import get_config

ROOT = Path(__file__).resolve().parents[3]
PROMOTED = ROOT / "src" / "ops_copilot" / "evaluation" / "golden" / "promoted.yaml"
NEGATIVE = ("down", "implicit_down")


# ── the queue ────────────────────────────────────────────────

async def flagged_turns() -> list[dict[str, Any]]:
    """Every negatively-rated turn with what its trace recorded, and its
    cluster recomputed and saved (signatures are cheap and the code that
    computes them may have improved since the flag arrived)."""
    t, f = conversation_turns, flagged_interactions
    slow_ms = get_config()["feedback"]["slow_ms"]
    async with owner_engine().begin() as conn:
        rows = (await conn.execute(
            select(f.c.id, f.c.turn_id, f.c.rating, f.c.category, f.c.comment, f.c.created_at,
                   f.c.review_status, f.c.review_note, f.c.golden_case_id,
                   t.c.question, t.c.answer, t.c.entities, t.c.outcome, t.c.session_id)
            .join(t, t.c.turn_id == f.c.turn_id)
            .where(f.c.rating.in_(NEGATIVE))
            .order_by(f.c.created_at)
        )).mappings().all()
        items = []
        for r in rows:
            sig = signature(r["outcome"], r["entities"], slow_ms)
            await conn.execute(update(f).where(f.c.id == r["id"]).values(cluster_id=sig.cluster))
            items.append({**dict(r), "cluster": sig.cluster, "signals": sig.signals})
    return items


def queue(items: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Open items grouped by cluster, biggest cluster first. Several
    flags on one turn (a thumbs-down AND a rephrase) are one item."""
    by_cluster: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for it in items:
        if it["review_status"] == "new":
            by_cluster[it["cluster"]].setdefault(it["turn_id"], it)
    groups = [(c, list(turns.values())) for c, turns in by_cluster.items()]
    return sorted(groups, key=lambda g: (-len(g[1]), ORDER.index(g[0]) if g[0] in ORDER else 99))


# ── promotion ────────────────────────────────────────────────

def load_promoted() -> list[dict[str, Any]]:
    if not PROMOTED.exists():
        return []
    return yaml.safe_load(PROMOTED.read_text(encoding="utf-8")) or []


def build_case(item: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    """A golden case from a flagged turn plus what the reviewer wrote.
    The reviewer's reference answer is required: without it this would
    be auto-promotion, which codifies the failure as the expectation."""
    reference = (review.get("reference_answer") or "").strip()
    if not reference:
        raise ValueError("a reference answer written by the reviewer is required to promote")
    expected: dict[str, Any] = {"max_iterations": 3}
    for key in ("must_contain", "must_not_contain", "domains"):
        if review.get(key):
            expected[key] = review[key]
    if review.get("vehicle_id"):
        expected["vehicle_id"] = review["vehicle_id"]
    if review.get("abstain"):
        expected["abstain"] = True
    day = datetime.now(UTC).date().isoformat()
    return {
        "id": f"prm_{item['cluster']}_{item['turn_id'][:8]}",
        "source": "promoted",
        "cluster": item["cluster"],
        "tags": ["promoted", f"cluster:{item['cluster']}"],
        "feedback_turn_id": item["turn_id"],
        "failure": f"{day}: {item['rating']} — signals {', '.join(item['signals'])}"
                   + (f"; user said: {item['comment']}" if item.get("comment") else ""),
        "question": " ".join(item["question"].split()),
        "expected": expected,
        "reference_answer": " ".join(reference.split()),
    }


def coverage_warnings(case: dict[str, Any], existing: list[dict[str, Any]]) -> list[str]:
    """A golden set nobody prunes grows duplicates that slow every run
    without adding coverage. Warn before adding one."""
    cfg = get_config()["feedback"]
    same = [c for c in existing if c.get("cluster") == case["cluster"]]
    warnings = []
    if len(same) >= cfg["max_promoted_per_cluster"]:
        warnings.append(f"cluster '{case['cluster']}' already has {len(same)} promoted cases "
                        f"({', '.join(c['id'] for c in same)}); add this only if it tests something they don't")
    for c in existing:
        if fuzz.token_set_ratio(c["question"].lower(), case["question"].lower()) >= 85:
            warnings.append(f"very similar to existing case {c['id']}: \"{c['question']}\"")
    return warnings


def append_promoted(case: dict[str, Any]) -> None:
    existing = load_promoted()
    if any(c["id"] == case["id"] for c in existing):
        raise ValueError(f"case {case['id']} already exists")
    # Keep the file's explanatory header; yaml.safe_dump drops comments.
    text = PROMOTED.read_text(encoding="utf-8") if PROMOTED.exists() else ""
    header = "".join(line + "\n" for line in text.splitlines() if line.startswith("#"))
    PROMOTED.write_text(header + yaml.safe_dump([*existing, case], sort_keys=False, allow_unicode=True,
                                                width=100), encoding="utf-8")


async def mark(turn_id: str, status: str, note: str | None = None, case_id: str | None = None) -> None:
    """Record the review decision on every flag for this turn."""
    f = flagged_interactions
    async with owner_engine().begin() as conn:
        await conn.execute(update(f).where(f.c.turn_id == turn_id, f.c.rating.in_(NEGATIVE)).values(
            review_status=status, review_note=note, golden_case_id=case_id,
            promoted=status == "promoted", reviewed_at=datetime.now(UTC)))


# ── cluster resolution ───────────────────────────────────────

def latest_eval_results() -> dict[str, bool]:
    """Pass/fail per case from the most recent evaluation run."""
    runs = ROOT / get_config()["evaluation"]["runs_dir"]
    files = sorted(runs.glob("*/results.jsonl")) if runs.exists() else []
    if not files:
        return {}
    out = {}
    for line in files[-1].read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r.get("status") == "ok":
            out[r["id"]] = bool(r["pass"])
    return out


def cluster_status(items: list[dict[str, Any]], promoted: list[dict[str, Any]],
                   results: dict[str, bool]) -> list[dict[str, Any]]:
    """Per cluster: how many flags, how many reviewed, which promoted
    cases guard it and whether they pass — and whether flags kept
    arriving after the last promotion (the fix did not clear the
    cluster, only the promoted case)."""
    clusters = sorted({it["cluster"] for it in items} | {c.get("cluster") for c in promoted if c.get("cluster")},
                      key=lambda c: ORDER.index(c) if c in ORDER else 99)
    rows = []
    for c in clusters:
        flags = [it for it in items if it["cluster"] == c]
        cases = [p for p in promoted if p.get("cluster") == c]
        passing = [p["id"] for p in cases if results.get(p["id"]) is True]
        failing = [p["id"] for p in cases if results.get(p["id"]) is False]
        promoted_dates = [p["failure"][:10] for p in cases if str(p.get("failure", ""))[:4].isdigit()]
        last = max(promoted_dates) if promoted_dates else None
        new_since = [it for it in flags if last and it["created_at"].date().isoformat() > last]
        open_ = sum(it["review_status"] == "new" for it in flags)
        all_pass = bool(cases) and len(passing) == len(cases)
        if all_pass and not new_since:
            state = "resolved"
        elif all_pass:
            state = "recurring"          # its cases pass, yet it still happens: the fix was too narrow
        elif failing:
            state = "fix in progress"
        elif cases:
            state = "awaiting eval run"  # promoted, not yet run
        else:
            state = "unreviewed" if open_ else "dismissed only"
        rows.append({"cluster": c, "flags": len({it["turn_id"] for it in flags}), "open": open_,
                     "dismissed": sum(it["review_status"] == "dismissed" for it in flags),
                     "promoted_cases": len(cases), "passing": len(passing), "failing": len(failing),
                     "not_yet_run": len(cases) - len(passing) - len(failing),
                     "new_flags_since_promotion": len(new_since), "state": state})
    return rows
