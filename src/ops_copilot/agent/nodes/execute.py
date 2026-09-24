"""Execute — no LLM. Validates, then calls tools through MCP.

reads   pending_tool_calls, turn_id
returns raw_results (handed to observe)
model   none
never   interprets a result, or lets one tool's failure abort another

Two things that matter here:

  Tools fail INDEPENDENTLY. If a lap calls both tools and the vector
  store is down, the SQL result still counts. Both outcomes — the
  success and the failure — become evidence.

  Results are tagged with turn_id. A result arriving for a turn the
  session has moved past is discarded, which is what stops message
  #1's answer from overwriting message #2's state.

Branching is by tool type, not domain:
  SQL  AST-validated here first (pure code, no credentials needed),
       so an obviously bad query fails without a round trip. The
       server validates again and runs the EXPLAIN gate — it is the
       security boundary and cannot trust any client to have checked.
  RAG  straight to the MCP client; there is no SQL to validate.

Retry policy: transport errors are retried with backoff inside the
MCP client. A timeout is NOT retried with the same query — it would
time out again. It is recorded as a failed result whose message tells
Plan to narrow the window, and the next lap does exactly that. A
narrower query has to be written by Plan; rewriting model SQL here
would mean code guessing at intent.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ops_copilot.agent.context import configurable, emit, guarded
from ops_copilot.agent.state import AgentState, ToolCall
from ops_copilot.mcp_client.client import ToolTimeoutError, get_client
from ops_copilot.observability.tracing import traced
from ops_copilot.settings import get_config, get_schema_config
from ops_copilot.sql.validator import SQLValidator


async def _run_one(mcp: Any, call: ToolCall, turn_id: str) -> dict[str, Any]:
    started = time.perf_counter()

    def done(result: dict[str, Any], tagged_turn: str = turn_id) -> dict[str, Any]:
        return {"turn_id": tagged_turn, "tool": call.tool, "args": call.args, "result": result,
                "latency_ms": int((time.perf_counter() - started) * 1000)}

    if call.tool == "structured_query_tool":
        sql = str(call.args.get("sql", ""))
        checked = SQLValidator(get_schema_config(), get_config()).validate(sql)
        if not checked.ok:
            return done({"status": "rejected", "stage": "ast", "reasons": checked.reasons,
                         "sql": sql})

    try:
        tagged = await mcp.call_tool(call.tool, call.args, turn_id)
    except ToolTimeoutError as exc:
        return done({"status": "failed", "error": f"{exc} — narrow the time window or add a filter"})
    except Exception as exc:
        return done({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
    return done(tagged["result"], tagged.get("turn_id", turn_id))


def _fallback(state: AgentState, exc: Exception) -> dict:
    return {"raw_results": [], "pending_tool_calls": []}

@traced("execute")
@guarded("execute", _fallback)
async def execute_node(state: AgentState, config: Any = None) -> dict:
    calls = state.get("pending_tool_calls", [])
    turn_id = state["turn_id"]
    if not calls:
        return {"raw_results": []}

    await emit(config, "status", {"node": "execute", "iteration": state.get("iteration"),
                                  "message": f"Running {len(calls)} tool call(s)"})
    mcp = configurable(config).get("mcp") or get_client()
    outcomes = await asyncio.gather(*(_run_one(mcp, c, turn_id) for c in calls),
                                    return_exceptions=True)

    results: list[dict[str, Any]] = []
    for call, out in zip(calls, outcomes, strict=True):
        if isinstance(out, BaseException):
            # _run_one catches tool errors; this is a bug in our own
            # code. Still evidence, never a crashed turn.
            out = {"turn_id": turn_id, "tool": call.tool, "args": call.args, "latency_ms": None,
                   "result": {"status": "failed", "error": f"{type(out).__name__}: {out}"}}
        if out["turn_id"] != turn_id:
            continue  # a stale answer for a turn this session has left
        results.append(out)
    return {"raw_results": results, "pending_tool_calls": []}
