"""The golden set itself: well-formed, traceable, and not bloated.

Runs in CI with no database or model, so a hand edit to curated.yaml or
promoted.yaml that breaks the set is caught before anything runs it."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from rapidfuzz import fuzz

from ops_copilot.feedback.signature import ORDER

GOLDEN = Path(__file__).resolve().parents[1] / "src" / "ops_copilot" / "evaluation" / "golden"


def cases() -> list[dict]:
    return [json.loads(line) for line in (GOLDEN / "golden.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_every_case_is_complete_and_unique():
    rows = cases()
    ids = [c["id"] for c in rows]
    assert len(ids) == len(set(ids))
    for c in rows:
        assert c["question"].strip() and c["reference_answer"].strip(), c["id"]
        assert isinstance(c["expected"], dict), c["id"]
        for f in c["expected"].get("facts", []):
            assert f.get("value") is not None, f"{c['id']}: fact {f['label']} has no value"


def test_golden_jsonl_matches_its_sources():
    """promoted.yaml edited without rebuilding golden.jsonl would leave
    the gate testing stale cases."""
    promoted = yaml.safe_load((GOLDEN / "promoted.yaml").read_text(encoding="utf-8")) or []
    built = {c["id"] for c in cases() if c["source"] == "promoted"}
    assert {c["id"] for c in promoted} == built, "run: python scripts/build_golden.py --no-generate"


def test_promoted_cases_name_their_cluster_and_origin():
    for c in cases():
        if c["source"] != "promoted":
            continue
        assert c.get("cluster") in ORDER, f"{c['id']}: cluster {c.get('cluster')!r}"
        assert f"cluster:{c['cluster']}" in c["tags"], c["id"]
        assert c.get("failure"), f"{c['id']}: say which real failure it came from"


def test_no_near_duplicate_questions():
    """Duplicates slow every run without adding coverage."""
    rows = cases()
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            score = fuzz.token_set_ratio(a["question"].lower(), b["question"].lower())
            same_answer = a["reference_answer"] == b["reference_answer"]
            assert score < 95 or not same_answer, f"{a['id']} duplicates {b['id']}"
