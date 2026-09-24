"""FastAPI application — the only door into the system.

Four endpoints, one job each:

  POST /chat      ask a question; the answer streams (SSE)
  POST /ingest    add a document (Lane B ingestion)
  POST /eval      run the test set, return a regression report
  POST /feedback  rate an answer, by the turn_id /chat returned

/eval is deliberately an endpoint and not part of any request path.
LLM-as-judge runs there, offline, never live.

Startup connects the MCP client, which under stdio spawns the tool
server; the server warms the embedding and reranker models itself,
off the request path. A failed connection does not stop the API from
starting — /health reports it, and the client reconnects on the
first tool call.

/health reports each dependency separately. "ok" with a dead database
behind it is worse than no health check, because it is believed.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from ops_copilot.api import routes_chat, routes_eval, routes_feedback, routes_ingest
from ops_copilot.db.engine import dispose_all, owner_engine
from ops_copilot.mcp_client.client import get_client
from ops_copilot.settings import get_settings

log = logging.getLogger("ops_copilot")

if sys.platform == "win32":
    # psycopg's async driver cannot run on the Proactor loop. Set here
    # so `uvicorn ops_copilot.main:app` works on a Windows dev machine.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logging.basicConfig(level=get_settings().log_level,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if sys.platform == "win32" and get_settings().mcp_transport == "stdio":
        # The Selector loop psycopg needs cannot spawn subprocesses on
        # Windows. Linux (the container) has no such conflict.
        log.warning("MCP stdio transport cannot spawn the tool server on Windows; run "
                    "`python -m ops_copilot.mcp_server.server --transport http` and set "
                    "MCP_TRANSPORT=http")
    try:
        await get_client().connect()
    except Exception as exc:
        log.warning("MCP not connected at startup (%s); will retry on first call", exc)
    yield
    await get_client().close()
    await dispose_all()


app = FastAPI(
    title="EV Ops Copilot",
    description="Agentic assistant for EV operations teams",
    version="0.1.0",
    lifespan=lifespan,
)

# An explicit origin list (CORS_ORIGINS), never "*": the chat stream
# carries operational data, and a wildcard lets any site a user visits
# read it from their browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

app.include_router(routes_chat.router, tags=["chat"])
app.include_router(routes_ingest.router, tags=["ingest"])
app.include_router(routes_feedback.router, tags=["feedback"])
app.include_router(routes_eval.router, tags=["eval"])


@app.get("/health")
async def health() -> dict:
    s = get_settings()
    try:
        async with owner_engine().connect() as conn:
            await asyncio.wait_for(conn.execute(text("SELECT 1")), timeout=3)
        database = "ok"
    except Exception as exc:
        database = f"unavailable: {type(exc).__name__}"
    return {
        "status": "ok" if database == "ok" else "degraded",
        "env": s.app_env,
        "database": database,
        "mcp": "connected" if get_client().connected else "not connected",
        "llm_configured": bool(s.groq_api_key),
        "tracing": s.tracing_configured,
    }
