"""structured_query_tool — precise values from the database.

Takes SQL the model already wrote (Plan generates it against the
injected schema config, in the same call that selects the tool).
This tool does not generate anything.

Sequence, none of it involving an LLM:

  1. SQLValidator.validate  — AST checks, LIMIT injection
  2. SQLValidator.explain_gate — planner cost, before execution
  3. execute on the READ REPLICA as the READ-ONLY role, with a
     statement timeout

Three independent layers. AST catches queries that should not
exist. EXPLAIN catches queries that are valid and ruinous. The
read-only role catches whatever the first two missed, because it
is structurally incapable of writing.

Validation runs here even though the agent's execute node already
ran step 1: the server is the security boundary, and it cannot
assume every client validated. The agent-side check exists only to
fail fast without a round trip.

Returns rows plus provenance (the SQL that ran, the EXPLAIN cost,
latency) so the trace shows exactly what happened. Rejections come
back as a normal result with `status: rejected` and the reasons —
the reasons are written to be read by Plan on the next lap.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import decimal
import time
import uuid
from typing import Any

import psycopg

from ops_copilot.settings import get_config, get_schema_config
from ops_copilot.sql.validator import SQLValidator


def _json_safe(v: Any) -> Any:
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, dt.date | dt.datetime | dt.time):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, bytes | memoryview):
        return "<binary>"
    return v


def _run(sql: str) -> dict[str, Any]:
    from ops_copilot.db.engine import readonly_sql_pool

    cfg = get_config()
    validator = SQLValidator(get_schema_config(), cfg)
    started = time.perf_counter()

    def elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    checked = validator.validate(sql)
    if not checked.ok:
        return {"status": "rejected", "stage": "ast", "reasons": checked.reasons,
                "sql": sql, "latency_ms": elapsed()}

    try:
        with readonly_sql_pool().connection() as conn:
            gate = validator.explain_gate(conn, checked.sql)
            if not gate.ok:
                return {"status": "rejected", "stage": "explain", "reasons": gate.reasons,
                        "sql": checked.sql, "explain_cost": gate.explain_cost,
                        "latency_ms": elapsed()}
            with conn.cursor() as cur:
                cur.execute(checked.sql)
                columns = [d.name for d in cur.description or []]
                rows = cur.fetchmany(cfg["sql_validation"]["max_limit"])
    except psycopg.errors.QueryCanceled:
        return {"status": "failed", "error": "statement timeout — narrow the time window or add a filter",
                "sql": checked.sql, "latency_ms": elapsed()}
    except psycopg.Error as exc:
        # Database errors (a bad cast, a missing column the schema
        # config got wrong) go back to Plan as feedback, verbatim.
        return {"status": "failed", "error": str(exc).strip().splitlines()[0],
                "sql": checked.sql, "latency_ms": elapsed()}

    records = [{c: _json_safe(v) for c, v in zip(columns, r, strict=True)} for r in rows]
    return {
        "status": "ok" if records else "empty",
        "columns": columns,
        "rows": records,
        "row_count": len(records),
        "sql": checked.sql,
        "warnings": checked.warnings,
        "explain_cost": gate.explain_cost,
        "latency_ms": elapsed(),
    }


async def structured_query(sql: str) -> dict:
    # psycopg's sync pool, in a worker thread: EXPLAIN and the query
    # must share one connection, and this keeps the server's event
    # loop free while Postgres works.
    return await asyncio.to_thread(_run, sql)
