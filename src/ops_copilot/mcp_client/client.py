"""MCP client. Lives in the agent process; talks to the server.

The agent never imports the tools directly — that would defeat the
isolation the separate server exists to provide. Every tool call
goes over the protocol.

Handles: connection lifecycle for both transports, per-call
timeouts, retry with backoff on transport errors, and turn_id
tagging so a result arriving for a superseded turn is discarded.

TODO(build): implement.
"""

from __future__ import annotations


class MCPClient:
    async def call_tool(self, name: str, args: dict, turn_id: str) -> dict:
        raise NotImplementedError("see module docstring")
