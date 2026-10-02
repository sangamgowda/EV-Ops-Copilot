"""Shared test setup."""

from __future__ import annotations

import pytest

from ops_copilot.observability import tracing


@pytest.fixture(autouse=True)
def _never_send_real_traces(monkeypatch):
    """Tests must never write to a real tracing account. With Langfuse
    keys in .env, any test that runs a turn would otherwise export it.
    Tests that inspect traces install an in-memory exporter instead."""
    monkeypatch.setattr(tracing, "_otel", lambda: None)


@pytest.fixture(autouse=True)
def _never_judge_live_answers(monkeypatch, request):
    """A test turn must never start a background judge (a real model call
    and a database write). Tests of the live judge opt back in."""
    if "live_judge_on" in request.keywords:
        return
    from ops_copilot.evaluation import live_judge
    monkeypatch.setattr(live_judge, "schedule", lambda state: False)
