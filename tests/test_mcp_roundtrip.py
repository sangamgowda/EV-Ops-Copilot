"""The real MCP server, as a real subprocess, over the real protocol.

Only calls that are decided before any database access are made, so
this runs without Postgres: a destructive query must come back
rejected by the server's own validation, and a bad domain must come
back failed. That proves the whole path — client spawn, env hand-off,
tool registration, JSON round trip — and that the server validates
independently of the agent.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from ops_copilot.mcp_client.client import MCPClient

SRC = str(Path(__file__).resolve().parents[1] / "src")

@pytest.fixture
def event_loop_policy():
    # Spawning the server needs a subprocess-capable loop. On Windows
    # that is Proactor — and importing the API app elsewhere in the
    # suite switches the process to Selector for psycopg.
    if sys.platform == "win32":
        return asyncio.WindowsProactorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture
def env(monkeypatch):
    # The server subprocess imports ops_copilot; pytest's pythonpath
    # setting applies only to this process.
    monkeypatch.setenv("PYTHONPATH", SRC)
    monkeypatch.setenv("TRACING_ENABLED", "false")
    # Loading two models in the background competes with the first
    # request on a cold disk; these calls never need them.
    monkeypatch.setenv("MCP_WARM_MODELS", "false")


async def test_server_rejects_destructive_sql_itself(env):
    async with MCPClient(transport="stdio") as client:
        out = await client.call_tool("structured_query_tool", {"sql": "DROP TABLE vehicles"}, "t1")
    assert out["turn_id"] == "t1"
    assert out["result"]["status"] == "rejected"
    assert out["result"]["stage"] == "ast"


async def test_server_rejects_unknown_domain(env):
    async with MCPClient(transport="stdio") as client:
        out = await client.call_tool("rag_retrieval_tool",
                                     {"query": "range", "domain": "marketing"}, "t2")
    assert out["result"]["status"] == "failed"
    assert "domain" in out["result"]["error"]
