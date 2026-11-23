"""The evaluation machinery itself: agreement maths, leakage filter,
generated-expectation cleanup, and run-to-run diffing."""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

from ops_copilot.evaluation.judge import agreement, passes, weighted_kappa
from ops_copilot.evaluation.runner import (
    _RateLimitWatch,
    _timing,
    diff_runs,
    is_measured,
)
from ops_copilot.evaluation.stats import mcnemar

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


class TestMcNemar:
    def test_known_values(self):
        assert mcnemar(0, 0) == 1.0                  # nothing changed: nothing to show
        assert mcnemar(5, 5) == 1.0
        assert mcnemar(8, 4) == 0.3877               # A' run 1 against X+Y+Z: could be chance
        assert mcnemar(10, 0) == 0.002               # ten fixes, no breaks: not chance
        assert mcnemar(0, 6) == mcnemar(6, 0)        # two-sided

    def test_diff_reports_it(self):
        row = TestDiff.row
        d = diff_runs([row("a", True), row("b", True), row("c", False)],
                      [row("a", False), row("b", False), row("c", True)])
        assert (len(d["fixed"]), len(d["regressions"])) == (2, 1)
        assert d["mcnemar_p"] == mcnemar(2, 1)


class TestTimedOutTraps:
    @staticmethod
    def trap(stop_reason, abstained=True):
        return {"id": "t", "source": "trap", "status": "ok", "pass": True, "abstained": abstained,
                "outcome": {"stop_reason": stop_reason}, "checks": []}

    def test_a_trap_that_timed_out_is_not_measured(self):
        assert not is_measured(self.trap("timeout"))
        assert is_measured(self.trap("complete"))
        # only traps: a case that timed out is a failure, not an unmeasured case
        assert is_measured({"source": "adversarial", "outcome": {"stop_reason": "timeout"}})

    def test_it_is_neither_fixed_nor_broken(self):
        prev = {**self.trap("complete"), "pass": True}
        cur = {**self.trap("timeout"), "pass": False}
        assert diff_runs([cur], [prev]) == {"regressions": [], "fixed": [], "compared_cases": 1,
                                           "mcnemar_p": 1.0}


class TestTiming:
    @staticmethod
    def case(i, passed, latency, wait=None, stop="complete"):
        r = {"id": i, "pass": passed, "latency_s": latency, "outcome": {"stop_reason": stop}}
        if wait is not None:
            r["budget_wait_s"] = wait
        return r

    def test_pass_rate_at_the_app_limit_needs_the_answer_in_time(self):
        rows = [self.case("a", True, 20), self.case("b", True, 63), self.case("c", False, 10)]
        t = _timing(rows, 45)
        assert t["at_app_limit"]["passed"] == 1            # b passed, but only after 63 s
        assert t["excluding_budget_waits"] is None         # no wait data in this run

    def test_waiting_for_the_budget_is_not_counted_against_the_agent(self):
        rows = [self.case("fast", True, 20, wait=0),
                self.case("slow_pass", True, 63, wait=55),                  # 8 s of work
                self.case("waited_out", False, 120, wait=100, stop="timeout"),   # 20 s of work
                self.case("slow_agent", False, 120, wait=10, stop="timeout"),    # 110 s of work
                self.case("wrong", False, 15, wait=0)]
        x = _timing(rows, 45)["excluding_budget_waits"]
        assert x["left_out_waited_for_budget"] == ["waited_out"]    # not measured
        assert (x["passed"], x["n"]) == (2, 4)                      # slow_agent still counts as a failure

    def test_the_watch_adds_up_the_clients_sleeps(self):
        w = _RateLimitWatch()
        for msg in ("Retrying request to /chat/completions in 2.000000 seconds",
                    "Retrying request to /chat/completions in 12.500000 seconds"):
            w.emit(logging.LogRecord("openai._base_client", logging.INFO, "", 0, msg, None, None))
        assert (w.retries, w.retry_wait_s) == (2, 14.5)
