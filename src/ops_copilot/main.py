"""FastAPI application — the only door into the system.

Four endpoints, one job each:

  /chat      the agentic path. Streams.
  /ingest    Lane B document ingestion
  /eval      run the golden set, return a regression report
  /feedback  capture a rating against a turn_id

/eval is deliberately an endpoint and not part of any request path.
LLM-as-judge runs there, offline, never live. Keeping the two
separate is itself a design decision worth defending.

TODO(build): wire routers, lifespan (warm the embedding model on
startup so the first request does not pay for the download), CORS,
structured logging, /health.
"""

from __future__ import annotations

from fastapi import FastAPI

from ops_copilot.settings import get_settings

app = FastAPI(
    title="EV Ops Copilot",
    description="Agentic assistant for EV operations teams",
    version="0.1.0",
)


@app.get("/health")
async def health() -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "env": s.app_env,
        "tracing": s.tracing_configured,
    }
