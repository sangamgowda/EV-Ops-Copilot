"""MCP server. Runs as its own process.

Why MCP rather than in-process tools:

  Process isolation. The database credentials live HERE, not in the
  agent. A prompt-injected agent cannot exfiltrate them; it can only
  request a tool call, which still passes validation.
  A single chokepoint for authz, rate limiting, and audit logging.
  Framework independence — swap LangGraph out, tools untouched.
  Multi-consumer reuse — a second internal agent becomes another
  client against the same contract, not a second copy of the
  credentials. The system is meant to serve several internal teams.

Transport: stdio by default (server spawned as a subprocess, one
less thing to run). Set MCP_TRANSPORT=http to run standalone; with
the pinned mcp version, "http" is served as SSE.

Under stdio, stdout IS the protocol channel. Every log line goes to
stderr, or the client reads it as a malformed message.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import threading
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from ops_copilot.mcp_server.tools.rag_retrieval import rag_retrieval, resolve_entity
from ops_copilot.mcp_server.tools.structured_query import structured_query
from ops_copilot.settings import get_settings

log = logging.getLogger("ops_copilot.mcp_server")


def _audit(tool: str, args: dict[str, Any], result: dict[str, Any], started: float) -> None:
    # One line per call: the chokepoint's audit trail.
    log.info(json.dumps({
        "tool": tool,
        "args": {k: (v[:300] if isinstance(v, str) else v) for k, v in args.items()},
        "status": result.get("status"),
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }))


def build_server() -> FastMCP:
    s = get_settings()
    # Bind every interface: mcp_server_host is the name CLIENTS use to
    # reach the server (e.g. the compose service name), not an address
    # this process should restrict itself to.
    server = FastMCP("ops-copilot-tools", host="0.0.0.0", port=s.mcp_server_port)

    @server.tool(
        name="structured_query_tool",
        description=(
            "Run ONE read-only SELECT against the operations database. "
            "The query is validated (AST rules, EXPLAIN cost gate) and executed "
            "as a read-only role. Returns rows, or status 'rejected'/'failed' with reasons."
        ),
    )
    async def structured_query_tool(sql: str) -> dict:
        started = time.perf_counter()
        result = await structured_query(sql)
        _audit("structured_query_tool", {"sql": sql}, result, started)
        return result

    @server.tool(
        name="rag_retrieval_tool",
        description=(
            "Search service documents and business reports. `domain` is "
            "'diagnostic' or 'business' and is a hard filter; `entity_id` "
            "(a vehicle or model id) boosts ranking but never filters. Returns "
            "chunks above the confidence threshold, or status 'empty'/'below_threshold'."
        ),
    )
    async def rag_retrieval_tool(query: str, domain: str, entity_id: str | None = None) -> dict:
        started = time.perf_counter()
        result = await rag_retrieval(query, domain, entity_id)
        _audit("rag_retrieval_tool", {"query": query, "domain": domain, "entity_id": entity_id},
               result, started)
        return result

    @server.tool(
        name="resolve_entity_tool",
        description=(
            "Check a vehicle id against the fleet before anything else uses it. "
            "Returns status exact | fuzzy (with the assumed id) | ambiguous (with "
            "suggestions) | not_found, and a note to show the user."
        ),
    )
    async def resolve_entity_tool(vehicle_id: str) -> dict:
        started = time.perf_counter()
        result = await resolve_entity(vehicle_id)
        _audit("resolve_entity_tool", {"vehicle_id": vehicle_id}, result, started)
        return result

    return server


def _warm_models() -> None:
    """Load both models off the request path.

    The first retrieval would otherwise pay for loading (and on a
    fresh volume, downloading) two models — tens of seconds — inside
    a user's request.
    """
    try:
        from ops_copilot.rag.embeddings import embed_query
        from ops_copilot.rag.rerank import score

        embed_query("warm up")
        score("warm up", ["warm up"])
        log.info("embedding and reranker models loaded")
    except Exception:
        log.exception("model warm-up failed; retrieval will load on first call")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=["stdio", "http"], default=get_settings().mcp_transport)
    args = parser.parse_args()

    logging.basicConfig(
        stream=sys.stderr,
        level=get_settings().log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if sys.platform == "win32":
        # psycopg's async driver cannot run on the Proactor loop.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    if get_settings().mcp_warm_models:
        threading.Thread(target=_warm_models, daemon=True).start()
    build_server().run(transport="sse" if args.transport == "http" else "stdio")


if __name__ == "__main__":
    main()
