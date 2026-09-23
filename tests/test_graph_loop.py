"""Loop behaviour. These are the tests that prove it is an agent.

TODO(build): implement with fakes for the LLM and MCP client.
"""

from __future__ import annotations

import pytest


@pytest.mark.skip(reason="TODO: implement with agent/graph.py")
class TestLoopControl:
    def test_lookup_exits_after_one_lap(self):
        """Simple questions must not burn laps they do not need."""

    def test_causal_question_loops(self):
        """A 'why' needs measurement + baseline + mechanism, so one
        lap cannot be sufficient."""

    def test_stops_at_iteration_cap(self): ...

    def test_stops_when_no_new_evidence(self):
        """A lap that added nothing will not do better next time."""

    def test_exhausted_exits_with_partial(self):
        """Missing-but-unreachable is a stop condition, not a reason
        to keep looping."""

    def test_never_loops_back_to_router(self):
        """Grounding failures retry synthesis only. Re-classifying the
        domain fixes nothing."""


@pytest.mark.skip(reason="TODO: implement with agent/graph.py")
class TestEvidenceAccumulation:
    def test_evidence_persists_across_laps(self):
        """Without accumulation, lap 2 repeats lap 1."""

    def test_failed_tool_becomes_evidence(self): ...
    def test_empty_retrieval_becomes_evidence(self): ...
