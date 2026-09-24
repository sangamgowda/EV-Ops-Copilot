"""Two engines, deliberately.

  owner     migrations, ingestion, operational writes
  readonly  the SQL tool, and nothing else

They are separate objects so it is impossible to accidentally run a
generated query on the owner connection. The separation is
structural, not a convention someone has to remember.

The readonly pool is kept small on purpose: the agent should never
be able to exhaust connections the rest of the system needs.

The readonly engine has two locks, not one. The role itself can only
SELECT (002_readonly_role.sql). On top of that, every connection it
opens is marked read-only for the session, so even a table the role
was wrongly granted write access to cannot be written through it.
The statement timeout is also set per connection, so the limit holds
against a database where the role-level setting was never applied.

`make_*` build a fresh engine from a URL (tests point them at a
different host); `owner_engine()` / `readonly_engine()` are the
process-wide ones everything else uses.
"""

from __future__ import annotations

import functools

from psycopg_pool import ConnectionPool
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ops_copilot.settings import get_config, get_settings


class DatabaseNotConfiguredError(RuntimeError):
    pass


def _require(url: str, name: str) -> str:
    # An unset URL, or one whose ${...} placeholders were never filled
    # in, would otherwise fail on connect with a confusing DNS error.
    if not url or "${" in url:
        raise DatabaseNotConfiguredError(f"{name} is not set (check .env)")
    return url


def make_owner_engine(url: str | None = None) -> AsyncEngine:
    cfg = get_config()["db"]
    return create_async_engine(
        _require(url or get_settings().database_url, "DATABASE_URL"),
        pool_size=cfg["owner_pool_size"],
        max_overflow=cfg["owner_max_overflow"],
        pool_pre_ping=True,
    )


def make_readonly_engine(url: str | None = None) -> AsyncEngine:
    cfg = get_config()
    timeout_ms = cfg["sql_validation"]["statement_timeout_ms"]
    return create_async_engine(
        _require(url or get_settings().database_url_readonly, "DATABASE_URL_READONLY"),
        pool_size=cfg["db"]["readonly_pool_size"],
        max_overflow=cfg["db"]["readonly_max_overflow"],
        pool_pre_ping=True,
        connect_args={
            "options": f"-c statement_timeout={timeout_ms} -c default_transaction_read_only=on"
        },
    )


@functools.lru_cache(maxsize=1)
def owner_engine() -> AsyncEngine:
    return make_owner_engine()


@functools.lru_cache(maxsize=1)
def readonly_engine() -> AsyncEngine:
    return make_readonly_engine()


@functools.lru_cache(maxsize=1)
def readonly_sql_pool() -> ConnectionPool:
    """Plain psycopg pool, same read-only role, for the SQL tool.

    The validator's EXPLAIN gate works on a DB-API cursor, and EXPLAIN
    and the query itself must run on the SAME connection so the cost
    check describes what actually executes. A synchronous pool, used
    from a worker thread, guarantees both.
    """
    cfg = get_config()
    timeout_ms = cfg["sql_validation"]["statement_timeout_ms"]
    url = _require(get_settings().database_url_readonly, "DATABASE_URL_READONLY")
    return ConnectionPool(
        url.replace("postgresql+psycopg://", "postgresql://"),
        min_size=1,
        max_size=cfg["db"]["readonly_pool_size"],
        kwargs={
            "options": (f"-c statement_timeout={timeout_ms} -c default_transaction_read_only=on "
                        f"-c role={cfg['db']['sql_tool_role']}"),
            "autocommit": True,
        },
        open=True,
    )


async def dispose_all() -> None:
    """Close every pool. Called once, at shutdown."""
    for factory in (owner_engine, readonly_engine):
        if factory.cache_info().currsize:
            await factory().dispose()
    if readonly_sql_pool.cache_info().currsize:
        readonly_sql_pool().close()
