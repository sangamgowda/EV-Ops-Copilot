"""MCP client. Lives in the agent process; talks to the server.

The agent never imports the tools directly — that would defeat the
isolation the separate server exists to provide. Every tool call
goes over the protocol.

Handles: connection lifecycle for both transports, per-call
timeouts, retry with backoff on transport errors, and turn_id
tagging so a result arriving for a superseded turn is discarded.

Retry policy is split by failure type on purpose:
  transport error   reconnect and retry, with backoff — the call
                    probably never reached the server
  timeout           NOT retried here — the server may still be
                    running the query, and repeating it doubles the
                    load. Surfaced to execute, which records it as
                    evidence so Plan can narrow the query.

Under stdio the server is a subprocess of this one. It is given
this process's environment explicitly: the mcp library otherwise
passes only a minimal whitelist, and the server would start without
the database settings it exists to hold.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client

from ops_copilot.settings import get_config, get_settings

log = logging.getLogger(__name__)


class ToolTimeoutError(Exception):
    """The server did not answer within mcp.call_timeout_seconds."""


class MCPClient:
    def __init__(self, transport: str | None = None) -> None:
        self.transport = transport or get_settings().mcp_transport
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._session is not None

    # ── lifecycle ────────────────────────────────────────────

    async def connect(self) -> None:
        async with self._lock:
            if self._session is not None:
                return
            stack = AsyncExitStack()
            try:
                if self.transport == "http":
                    s = get_settings()
                    read, write = await stack.enter_async_context(
                        sse_client(f"http://{s.mcp_server_host}:{s.mcp_server_port}/sse")
                    )
                else:
                    params = StdioServerParameters(
                        command=sys.executable,
                        args=["-m", "ops_copilot.mcp_server.server", "--transport", "stdio"],
                        env=dict(os.environ),
                    )
                    read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
            except BaseException:
                await stack.aclose()
                raise
            self._stack, self._session = stack, session
            log.info("MCP connected over %s", self.transport)

    async def close(self) -> None:
        async with self._lock:
            if self._stack is not None:
                try:
                    await self._stack.aclose()
                except Exception:
                    log.debug("error while closing MCP session", exc_info=True)
            self._stack, self._session = None, None

    async def __aenter__(self) -> MCPClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # ── calls ────────────────────────────────────────────────

    async def _call_once(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if self._session is None:
            await self.connect()
        assert self._session is not None
        result = await asyncio.wait_for(
            self._session.call_tool(name, args),
            timeout=get_config()["mcp"]["call_timeout_seconds"],
        )
        text = "".join(getattr(c, "text", "") for c in result.content)
        if result.isError:
            return {"status": "failed", "error": text or "tool raised an error"}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"status": "failed", "error": f"tool returned non-JSON output: {text[:200]}"}

    async def call_tool(self, name: str, args: dict, turn_id: str) -> dict:
        """Returns {"turn_id", "tool", "result"}. Raises ToolTimeoutError, or
        the last transport error once retries are spent."""
        cfg = get_config()["mcp"]
        attempts = cfg["transport_retries"] + 1
        for attempt in range(1, attempts + 1):
            try:
                result = await self._call_once(name, args)
                return {"turn_id": turn_id, "tool": name, "result": result}
            except TimeoutError as exc:
                raise ToolTimeoutError(f"{name} timed out after {cfg['call_timeout_seconds']}s") from exc
            except Exception:
                if attempt == attempts:
                    raise
                log.warning("MCP transport error on %s (attempt %d/%d); reconnecting",
                            name, attempt, attempts, exc_info=True)
                await self.close()
                await asyncio.sleep(cfg["retry_backoff_seconds"] * 2 ** (attempt - 1))
        raise RuntimeError("unreachable")


_client: MCPClient | None = None


def get_client() -> MCPClient:
    """Process-wide client. The API lifespan connects and closes it."""
    global _client
    if _client is None:
        _client = MCPClient()
    return _client
