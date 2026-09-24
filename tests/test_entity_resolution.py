"""Entity resolution tests.

The misspelled-VIN case is why this module exists: a hard filter on
a typo returns zero chunks, and zero chunks is where hallucination
starts.
"""

from __future__ import annotations

from ops_copilot.rag.entity_resolution import resolve_against

# Dense, like the seeded fleet: one wrong digit lands on a real vehicle.
FLEET = [f"VIN-{n}" for n in range(1001, 1301)]
# Sparse: a typo has only one plausible target.
SPARSE = ["VIN-1042", "VIN-2077", "VIN-3150"]


class TestResolution:
    def test_exact_match(self):
        r = resolve_against("VIN-1042", FLEET)
        assert (r.status, r.value) == ("exact", "VIN-1042")

    def test_formatting_is_not_a_typo(self):
        for raw in ("vin 1042", "VIN1042", " vin-1042 ", "1042"):
            r = resolve_against(raw, FLEET)
            assert (r.status, r.value) == ("exact", "VIN-1042"), raw

    def test_single_fuzzy_match_accepted_with_note(self):
        r = resolve_against("VIN-10422", SPARSE)
        assert (r.status, r.value) == ("fuzzy", "VIN-1042")
        # The assumption must be visible so the answer can say it.
        assert "VIN-1042" in r.note and "VIN-10422" in r.note

    def test_multiple_close_matches_ask(self):
        r = resolve_against("VIN-104", FLEET)
        assert r.status == "ambiguous"
        assert r.value is None
        # VIN-1004, VIN-1040, VIN-1041 ... are all one edit away;
        # there is no principled way to pick, so it must ask.
        assert len(r.suggestions) == 3
        assert all(s in FLEET for s in r.suggestions)

    def test_single_close_but_not_certain_asks(self):
        # Close enough to suggest, not close enough to assume.
        r = resolve_against("VIN-1O42", SPARSE)
        assert r.status == "ambiguous"
        assert r.suggestions == ["VIN-1042"]

    def test_nothing_close_says_not_found(self):
        r = resolve_against("TRUCK-99", FLEET)
        assert r.status == "not_found"
        assert r.value is None and r.suggestions == []
        assert "TRUCK-99" in r.note

    def test_empty_input(self):
        assert resolve_against("  ", FLEET).status == "not_found"
