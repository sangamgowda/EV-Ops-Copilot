"""Entity resolution tests.

The misspelled-VIN case is why this module exists: a hard filter on
a typo returns zero chunks, and zero chunks is where hallucination
starts.

TODO(build): implement alongside rag/entity_resolution.py.
"""

from __future__ import annotations

import pytest


@pytest.mark.skip(reason="TODO: implement with rag/entity_resolution.py")
class TestResolution:
    def test_exact_match(self): ...
    def test_single_fuzzy_match_accepted_with_note(self): ...
    def test_multiple_close_matches_ask(self): ...
    def test_nothing_close_says_not_found(self): ...
