"""The code checks: each must pass what it should and fail what it
should, or every score built on them is noise."""

from __future__ import annotations

from ops_copilot.evaluation.deterministic import Outcome, run_checks


def outcome(answer: str = "", **kw) -> Outcome:
    base = dict(domains=["diagnostic"], vehicle_id="V-042", iterations=2, stop_reason="complete",
                tool_calls=[{"tool": "structured_query_tool", "lap": 1,
                             "args": {"sql": "SELECT ... WHERE t.vehicle_id = 'V-042' AND metric_name "
                                             "IN ('current_draw', 'payload')"}},
                            {"tool": "rag_retrieval_tool", "lap": 1,
                             "args": {"query": "overload", "domain": "diagnostic", "entity_id": "V-042"}}],
                cited=["e1", "e2"], evidence_ids=["e1", "e2", "e3"])
    base.update(kw)
    return Outcome(answer=answer, **base)


def results(o: Outcome, expected: dict) -> dict[str, bool]:
    return {c.name: c.passed for c in run_checks(o, expected)}


class TestRouting:
    def test_domains_are_a_set(self):
        o = outcome(domains=["business", "diagnostic"])
        assert results(o, {"domains": ["diagnostic", "business"]})["domains"]
        assert not results(o, {"domains": ["diagnostic"]})["domains"]

    def test_nonexistent_vehicle_must_resolve_to_nothing(self):
        assert results(outcome(vehicle_id=None), {"vehicle_id": "not_found"})["entity"]
        assert not results(outcome(), {"vehicle_id": "not_found"})["entity"]


class TestTools:
    def test_key_args_need_one_call_carrying_all_fragments(self):
        exp = {"key_args": [{"tool": "structured_query_tool", "contains": ["V-042", "current_draw"]}]}
        assert results(outcome(), exp)["key_args"]
        exp = {"key_args": [{"tool": "structured_query_tool", "contains": ["V-042", "charge_power"]}]}
        assert not results(outcome(), exp)["key_args"]

    def test_forbidden_args(self):
        exp = {"forbidden_args": [{"tool": "structured_query_tool", "contains": ["V-999"]}]}
        assert results(outcome(), exp)["forbidden_args"]
        bad = outcome(tool_calls=[{"tool": "structured_query_tool", "lap": 1,
                                   "args": {"sql": "SELECT 1 FROM vehicles WHERE vehicle_id = 'V-999'"}}])
        assert not results(bad, exp)["forbidden_args"]

    def test_required_tools(self):
        assert results(outcome(), {"tools": ["rag_retrieval_tool"]})["tools"]
        assert not results(outcome(tool_calls=[]), {"tools": ["rag_retrieval_tool"]})["tools"]


class TestAnswer:
    def test_facts_within_tolerance_sign_ignored(self):
        o = outcome("Current draw is 38.1% above baseline; payload averaged 190.4 kg.")
        exp = {"facts": [{"label": "current", "value": 37.0, "tolerance": 3},
                         {"label": "payload", "value": 190, "tolerance": 8}]}
        assert results(o, exp)["facts"]
        assert not results(outcome("Current draw is 12% above."), exp)["facts"]

    def test_vehicle_ids_are_not_facts(self):
        """V-042 must not satisfy a fact of 42."""
        o = outcome("V-042 is overloaded.")
        assert not results(o, {"facts": [{"label": "x", "value": 42, "tolerance": 0.5}]})["facts"]

    def test_must_contain_any_of_and_typographic_dashes(self):
        o = outcome("See SB\u2011114: the scooter is overloaded.")
        assert results(o, {"must_contain": [["overload", "over-load"], "SB-114"]})["must_contain"]
        assert not results(o, {"must_contain": ["SB-121"]})["must_contain"]

    def test_must_not_contain(self):
        exp = {"must_not_contain": ["caused by overload"]}
        assert results(outcome("Overload was ruled out."), exp)["must_not_contain"]
        assert not results(outcome("It is caused by overload."), exp)["must_not_contain"]

    def test_abstain(self):
        assert results(outcome("The cause cannot be determined from the data."), {"abstain": True})["abstain"]
        assert results(outcome("Some text.", partial=True), {"abstain": True})["abstain"]
        assert not results(outcome("It is caused by a faulty motor."), {"abstain": True})["abstain"]

    def test_citations_always_checked(self):
        assert results(outcome("x [e1]"), {})["citations"]
        assert not results(outcome("x [e9]", cited=["e9"]), {})["citations"]

    def test_unasked_checks_are_skipped_not_passed(self):
        assert set(results(outcome("x"), {})) == {"citations"}
