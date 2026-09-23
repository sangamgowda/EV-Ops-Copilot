"""Execute — no LLM. Validates, then calls tools through MCP.

reads   pending_tool_calls, turn_id
returns raw tool results (handed to observe)
model   none
never   interprets a result, or lets one tool's failure abort another

Two things that matter here:

  Tools fail INDEPENDENTLY. If a lap calls both tools and the vector
  store is down, the SQL result still counts. Both outcomes — the
  success and the failure — become evidence.

  Results are tagged with turn_id. A result arriving for a turn the
  session has moved past is discarded, which is what stops message
  #1's answer from overwriting message #2's state.

TODO(build): implement.
  1. split calls by tool type
  2. SQL path: SQLValidator.validate -> explain_gate -> MCP call
     RAG path: MCP call directly (no SQL to validate)
  3. asyncio.gather with return_exceptions=True
  4. retry policy: timeout -> one narrower retry (shorter window);
     connection error -> two retries with backoff
  5. drop any result whose turn_id != state["turn_id"]
"""

from __future__ import annotations

from ops_copilot.agent.state import AgentState


async def execute_node(state: AgentState) -> dict:
    raise NotImplementedError("see module docstring")
