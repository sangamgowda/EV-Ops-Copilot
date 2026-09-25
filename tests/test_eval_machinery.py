"""The evaluation machinery itself: agreement maths, leakage filter,
generated-expectation cleanup, and run-to-run diffing."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from ops_copilot.evaluation.judge import agreement, passes, weighted_kappa
from ops_copilot.evaluation.runner import diff_runs

_spec = importlib.util.spec_from_file_location(
    "build_golden", Path(__file__).resolve().parents[1] / "scripts" / "build_golden.py")
build_golden = importlib.util.module_from_spec(_spec)
sys.modules["build_golden"] = build_golden   # pydantic resolves its types here
_spec.loader.exec_module(build_golden)


class TestKappa:
    def test_perfect_agreement_is_one(self):
        assert weighted_kappa([1, 2, 3, 4], [1, 2, 3, 4]) == 1.0

    def test_near_misses_cost_less_than_far_ones(self):
        near = weighted_kappa([1, 2, 3, 4, 4], [2, 2, 3, 4, 3])
        far = weighted_kappa([1, 2, 3, 4, 4], [4, 2, 3, 1, 3])
        assert near > far

    def test_undefined_cases(self):
        assert weighted_kappa([3], [3]) is None
        assert weighted_kappa([3, 3], [3, 3]) is None      # no variation: kappa undefined

    def test_agreement_report(self):
        h = {"completeness": 4, "correctness": 4, "hedging": 3, "pass": True}
        j = {"completeness": 3, "correctness": 4, "hedging": 3, "pass": True}
        out = agreement([(h, j), (h, h)])
        assert out["completeness"]["exact_pct"] == 50.0
        assert out["completeness"]["within_one_pct"] == 100.0
        assert out["pass"]["exact_pct"] == 100.0

    def test_pass_needs_every_dimension(self):
        assert passes({"completeness": 4, "correctness": 3, "hedging": 3}, 3)
        assert not passes({"completeness": 4, "correctness": 4, "hedging": 2}, 3)


class TestLeakage:
    def test_overlap_measures_shared_content_words(self):
        chunk = "Sustained overload raises drive current and reduces range."
        assert build_golden.overlap("Does sustained overload raise drive current?", chunk) > 0.6
        assert build_golden.overlap("Why does carrying too much weight use more power?", chunk) < 0.3

    def test_sanitize_moves_numbers_to_facts_and_drops_question_numbers(self):
        g = build_golden.GeneratedQuestion(
            question="The charger shows only 350 W. Why?",
            reference_answer="...",
            must_contain=["750 W", "output stage", "failing output stage"],
            facts=[{"label": "shown", "value": 350, "tolerance": 0},
                   {"label": "date", "value": 0, "tolerance": 0}])
        phrases, facts = build_golden.sanitize(g)
        assert phrases == ["output stage"]          # three-word phrases break on rewording
        assert [f["value"] for f in facts] == [750]


class TestDiff:
    @staticmethod
    def row(i, ok, status="ok"):
        return {"id": i, "pass": ok, "status": status, "checks": [{"name": "facts", "passed": ok}]}

    def test_regressions_and_fixes(self):
        prev = [self.row("a", True), self.row("b", False), self.row("c", True)]
        cur = [self.row("a", False), self.row("b", True), self.row("c", True)]
        d = diff_runs(cur, prev)
        assert [r["id"] for r in d["regressions"]] == ["a"]
        assert d["regressions"][0]["now_failing"] == ["facts"]
        assert d["fixed"] == ["b"]

    def test_rate_limited_cases_are_not_regressions(self):
        d = diff_runs([self.row("a", False, "infra")], [self.row("a", True)])
        assert d["regressions"] == []
