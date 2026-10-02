"""Phase 6, operations and scale: evaluation statistics and the
held-out set, caching, metrics, and the live-answer judge."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ops_copilot.evaluation.stats import overlaps, wilson

GOLDEN = Path(__file__).resolve().parents[1] / "src" / "ops_copilot" / "evaluation" / "golden"


class TestIntervals:
    def test_wilson_matches_known_values(self):
        assert wilson(33, 46) == (57.5, 82.7)
        assert wilson(3, 3) == (43.9, 100.0)          # all passing, but only three cases
        assert wilson(0, 5)[0] == 0.0
        assert wilson(0, 0) is None

    def test_more_cases_narrow_the_interval(self):
        small, large = wilson(7, 10), wilson(70, 100)
        assert (small[1] - small[0]) > (large[1] - large[0])

    def test_overlap_means_noise(self):
        assert overlaps((40, 70), (55, 80))
        assert not overlaps((40, 50), (60, 80))


class TestHeldOut:
    @pytest.fixture
    def heldout(self):
        path = GOLDEN / "heldout.jsonl"
        if not path.exists():
            pytest.skip("held-out set not built (build_golden.py --heldout)")
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_disjoint_from_the_golden_set(self, heldout):
        golden = [json.loads(line) for line in (GOLDEN / "golden.jsonl").read_text(encoding="utf-8").splitlines()]
        assert not {c["id"] for c in heldout} & {c["id"] for c in golden}
        assert not {c["question"] for c in heldout} & {c["question"] for c in golden}

    def test_never_about_a_vehicle_tuned_against(self, heldout):
        tuned = {"V-042", "V-007", "V-055", "V-088", "V-091", "V-036", "V-118", "V-017", "V-064", "V-029"}
        assert not {c["expected"].get("vehicle_id") for c in heldout} & tuned

    def test_large_enough_to_mean_something(self, heldout):
        assert len(heldout) >= 45


class TestRunsCompareWithinTheirSet:
    def test_heldout_run_is_not_compared_with_a_golden_run(self, tmp_path, monkeypatch):
        from ops_copilot.evaluation import runner
        monkeypatch.setattr(runner, "runs_dir", lambda: tmp_path)
        for rid, case_set in (("20260101-000000", "golden"), ("20260102-000000", "heldout"),
                              ("20260103-000000", "golden"), ("20260104-000000", "heldout")):
            (tmp_path / rid).mkdir()
            (tmp_path / rid / "meta.json").write_text(json.dumps({"case_set": case_set}))
            (tmp_path / rid / "report.json").write_text("{}")
        assert runner.previous_run("20260104-000000") == "20260102-000000"
        assert runner.previous_run("20260103-000000") == "20260101-000000"
