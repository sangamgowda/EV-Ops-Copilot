"""Langfuse tracing. Wraps every node as it runs.

Not added afterwards from logs — each node is wrapped so its input,
output, latency and token cost are pushed as it executes. Every lap
of the loop appears as its own span under one trace, and every trace
carries session_id.

This is what makes "how would you debug wrong tool selection across
a million requests" answerable: you never read traces one at a time.
You filter the trace table on a field — tool_selected, stop_reason,
groundedness_passed — cluster by failure signature, and read one
representative per cluster.

Every trace records the prompt-version hash (see settings.load_prompt).
Without it you cannot tell whether a past failure is still
reproducible after a prompt change, which makes the feedback loop
unfalsifiable.

Degrades cleanly: if Langfuse keys are absent, tracing is a no-op
and the system runs normally.

TODO(build): implement the decorator and span helpers.
"""

from __future__ import annotations


def traced(node_name: str):
    """Decorator. Wraps a graph node in a Langfuse span."""
    def decorator(fn):
        return fn
    return decorator


def record_generation(node: str, model: str, messages: list[dict], output: str,
                      usage: dict[str, int], latency_ms: int) -> None:
    """One LLM call. No-op until tracing is built; the LLM client already
    reports every call here, so tracing plugs in without touching it."""
