"""Prometheus metrics: the numbers dashboards and alerts are built on.

Traces (tracing.py) answer "what happened in THIS question". Metrics
answer "how is the service doing": p95 latency, error rate, cost per
question, grounding failures, how turns end. They are aggregates with
low-cardinality labels only — never a question, a vehicle id or a
turn id — so they are safe to scrape and cheap to store.

Exposed at GET /metrics. Dashboards and alert rules are in monitoring/.

Cost is computed from token counts and the list prices in
config/app_config.yaml (llm.pricing_usd_per_million_tokens). On a free
tier nothing is billed; the number is what the same traffic would cost
on a paid plan, which is the figure to plan capacity with.
"""

from __future__ import annotations

import contextvars
from typing import Any

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from ops_copilot.settings import get_config

REGISTRY = CollectorRegistry(auto_describe=True)

TURN_LATENCY = Histogram(
    "copilot_turn_latency_seconds", "Time to answer one question.", ["result"],
    buckets=(1, 2.5, 5, 10, 15, 20, 30, 45, 60, 90, 120), registry=REGISTRY)
TURNS = Counter("copilot_turns_total", "Questions answered, by how the turn ended.",
                ["stop_reason"], registry=REGISTRY)
TURN_ERRORS = Counter("copilot_turn_errors_total",
                      "Turns that raised, timed out, or had a step fall back.", ["kind"], registry=REGISTRY)
GROUNDING = Counter("copilot_grounding_checks_total", "Grounding check results.", ["result"],
                    registry=REGISTRY)
TURN_COST = Histogram(
    "copilot_turn_cost_usd", "Model cost of one question at list price.",
    buckets=(0.0005, 0.001, 0.002, 0.003, 0.005, 0.0075, 0.01, 0.02, 0.05), registry=REGISTRY)
LLM_TOKENS = Counter("copilot_llm_tokens_total", "Tokens sent and received.", ["model", "direction"],
                     registry=REGISTRY)
LLM_COST = Counter("copilot_llm_cost_usd_total", "Model cost at list price.", ["model"], registry=REGISTRY)
LLM_FAILOVERS = Counter("copilot_llm_failovers_total", "Calls moved off a model, and why.",
                        ["model", "reason"], registry=REGISTRY)
BREAKER_OPEN = Gauge("copilot_llm_breaker_open", "1 while a model's circuit breaker is open.", ["model"],
                     registry=REGISTRY)
TOOL_CALLS = Counter("copilot_tool_calls_total", "Tool calls by result, and whether served from cache.",
                     ["tool", "status", "cached"], registry=REGISTRY)
LIVE_JUDGE = Histogram("copilot_live_judge_score", "Scores the judge gave sampled live answers (1-4).",
                       ["dimension"], buckets=(1, 2, 3, 4), registry=REGISTRY)

# Cost of the current turn, summed across every model call in it. A
# one-element list so that tasks the graph spawns (which get a COPY of
# the context) add to the same total.
_turn_cost: contextvars.ContextVar[list[float] | None] = contextvars.ContextVar("turn_cost", default=None)


def start_turn() -> None:
    _turn_cost.set([0.0])


def turn_cost() -> float:
    acc = _turn_cost.get()
    return round(acc[0], 6) if acc else 0.0


def price(model: str, usage: dict[str, int]) -> float:
    prices = get_config()["llm"].get("pricing_usd_per_million_tokens", {}).get(model)
    if not prices:
        return 0.0
    return (usage.get("input", 0) * prices["input"] + usage.get("output", 0) * prices["output"]) / 1e6


def record_llm_call(model: str, usage: dict[str, int]) -> None:
    for direction in ("input", "output"):
        if usage.get(direction):
            LLM_TOKENS.labels(model, direction).inc(usage[direction])
    cost = price(model, usage)
    if cost:
        LLM_COST.labels(model).inc(cost)
        acc = _turn_cost.get()
        if acc is not None:
            acc[0] += cost


def record_turn(fields: dict[str, Any]) -> None:
    """Called once per turn with turn.outcome()'s fields."""
    stop = fields.get("stop_reason") or ("error" if fields.get("error") else "unknown")
    TURNS.labels(stop).inc()
    result = "error" if fields.get("error") else "partial" if fields.get("partial") else "complete"
    TURN_LATENCY.labels(result).observe(fields.get("latency_ms", 0) / 1000)
    if fields.get("error"):
        TURN_ERRORS.labels("exception").inc()
    if stop == "timeout":
        TURN_ERRORS.labels("timeout").inc()
    if fields.get("node_errors"):
        TURN_ERRORS.labels("node_fallback").inc()
    grounded = fields.get("grounded")
    GROUNDING.labels("not_run" if grounded is None else "passed" if grounded else "failed").inc()
    TURN_COST.observe(fields.get("cost_usd", 0.0))


def record_tool_result(tool: str, result: dict[str, Any]) -> None:
    TOOL_CALLS.labels(tool, str(result.get("status", "unknown")), str(bool(result.get("cached"))).lower()).inc()


def render() -> bytes:
    return generate_latest(REGISTRY)
