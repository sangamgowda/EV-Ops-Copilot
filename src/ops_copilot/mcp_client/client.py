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

Reconnecting without failing calls in flight. Execute runs several
tool calls at once over one session. When one of them hits a transport
error:
  - a new connection is opened for every call from then on, and the
    old one is RETIRED, not closed: calls still running on it finish,
    and it closes when the last one does (or after a grace period);
  - every connection has a generation number. A call that fails on an
    older generation than the current one retries at once on the new
    connection, without using up an attempt — the connection it lost
    was already replaced. So one outage causes one reconnect, not one
    per call, and calls that were merely caught in it do not fail.
Retrying is safe because every tool is read-only.

Each connection is owned by its own task. The mcp transports are anyio
task groups, which must be entered and exited in the same task;
closing one from whichever call noticed the error breaks that rule.

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
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client

from ops_copilot.settings import get_config, get_settings

log = logging.getLogger(__name__)


class ToolTimeoutError(Exception):
    """The server did not answer within mcp.call_timeout_seconds."""


class _Connection:
    """One session, entered and exited inside its own task."""

    def __init__(self, gen: int) -> None:
        self.gen = gen
        self.session: ClientSession | None = None
        self.inflight = 0
        self.retiring = False
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def alive(self) -> bool:
        return self.session is not None and self._task is not None and not self._task.done()

    async def start(self, transport: Any) -> None:
        ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        async def own() -> None:
            try:
                async with AsyncExitStack() as stack:
                    read, write = await stack.enter_async_context(transport)
                    session = await stack.enter_async_context(ClientSession(read, write))
                    await session.initialize()
                    self.session = session
                    ready.set_result(None)
                    await self._stop.wait()
            except BaseException as exc:
                if not ready.done():
                    ready.set_exception(exc)
                else:
                    log.debug("MCP connection %d ended: %r", self.gen, exc)
            finally:
                self.session = None

        self._task = asyncio.create_task(own(), name=f"mcp-connection-{self.gen}")
        try:
            await ready
        except BaseException:
            await self.stop()
            raise

    async def stop(self, grace_s: float = 5.0) -> None:
        self._stop.set()
        if self._task is None or self._task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=grace_s)
        except (TimeoutError, Exception):
            self._task.cancel()


class MCPClient:
    def __init__(self, transport: str | None = None) -> None:
        self.transport = transport or get_settings().mcp_transport
        self._conn: _Connection | None = None
        self._retired: set[_Connection] = set()
        self._gen = 0
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._conn is not None and self._conn.alive

    @property
    def generation(self) -> int:
        return self._gen

    # ── lifecycle ────────────────────────────────────────────

    def _transport(self) -> AbstractAsyncContextManager[Any]:
        if self.transport == "http":
            s = get_settings()
            return sse_client(f"http://{s.mcp_server_host}:{s.mcp_server_port}/sse")
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "ops_copilot.mcp_server.server", "--transport", "stdio"],
            env=dict(os.environ),
        )
        return stdio_client(params)

    async def connect(self) -> None:
        async with self._lock:
            await self._connect_locked()

    async def _connect_locked(self) -> _Connection:
        if self._conn is not None and self._conn.alive:
            return self._conn
        if self._conn is not None:
            self._retire(self._conn)
        conn = _Connection(self._gen + 1)
        await conn.start(self._transport())
        self._gen, self._conn = conn.gen, conn
        log.info("MCP connected over %s (generation %d)", self.transport, conn.gen)
        return conn

    def _retire(self, conn: _Connection) -> None:
        """Stop giving out `conn`; close it once its calls are done."""
        conn.retiring = True
        if self._conn is conn:
            self._conn = None
        if conn.inflight == 0:
            asyncio.get_running_loop().create_task(conn.stop())
            return
        self._retired.add(conn)
        # A hung call must not keep a dead connection open for ever.
        grace = get_config()["mcp"]["call_timeout_seconds"] + 5

        async def close_later() -> None:
            await asyncio.sleep(grace)
            self._retired.discard(conn)
            await conn.stop()

        asyncio.get_running_loop().create_task(close_later())

    async def _replace(self, gen: int) -> None:
        """Reconnect, unless the connection of generation `gen` was
        already replaced by another call that hit the same outage."""
        async with self._lock:
            if self._conn is not None and self._conn.gen == gen:
                self._retire(self._conn)
            await self._connect_locked()

    async def close(self) -> None:
        async with self._lock:
            conns = [c for c in (self._conn, *self._retired) if c is not None]
            self._conn = None
            self._retired.clear()
        for c in conns:
            try:
                await c.stop()
            except Exception:
                log.debug("error while closing MCP session", exc_info=True)

    async def __aenter__(self) -> MCPClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # ── calls ────────────────────────────────────────────────

    async def _call_once(self, conn: _Connection, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if conn.session is None:
            raise ConnectionError(f"MCP connection {conn.gen} is closed")
        conn.inflight += 1
        try:
            result = await asyncio.wait_for(
                conn.session.call_tool(name, args),
                timeout=get_config()["mcp"]["call_timeout_seconds"],
            )
        finally:
            conn.inflight -= 1
            if conn.retiring and conn.inflight == 0:
                self._retired.discard(conn)
                asyncio.get_running_loop().create_task(conn.stop())
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
        attempt, free_retries = 1, attempts       # free: the connection was already replaced
        while True:
            async with self._lock:
                conn = await self._connect_locked()
            try:
                result = await self._call_once(conn, name, args)
                return {"turn_id": turn_id, "tool": name, "result": result}
            except TimeoutError as exc:
                raise ToolTimeoutError(f"{name} timed out after {cfg['call_timeout_seconds']}s") from exc
            except Exception:
                if conn.gen != self._gen and free_retries > 0:
                    free_retries -= 1
                    log.info("MCP %s lost connection %d, already replaced; retrying", name, conn.gen)
                    continue
                if attempt == attempts:
                    raise
                log.warning("MCP transport error on %s (attempt %d/%d); reconnecting",
                            name, attempt, attempts, exc_info=True)
                await asyncio.sleep(cfg["retry_backoff_seconds"] * 2 ** (attempt - 1))
                attempt += 1
                await self._replace(conn.gen)


_client: MCPClient | None = None


def get_client() -> MCPClient:
    """Process-wide client. The API lifespan connects and closes it."""
    global _client
    if _client is None:
        _client = MCPClient()
    return _client
