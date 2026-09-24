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
