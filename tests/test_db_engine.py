"""The two engines against a real database.

These need the compose database running (`docker compose up`). They
skip, rather than fail, when it is not reachable.

They connect over the network from outside the database container, on
purpose: inside it, the postgres image trusts local connections without
a password, so a login test run there proves nothing about passwords.

From a Windows or macOS host the compose hostname "postgres" does not
resolve; the URL is pointed at localhost:5433, where compose publishes
the database. Set TEST_DB_HOST / TEST_DB_PORT to override.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from ops_copilot.db.engine import make_owner_engine, make_readonly_engine
from ops_copilot.settings import get_settings


@pytest.fixture
def event_loop_policy():
    # psycopg's async driver cannot run on Windows' default Proactor loop.
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


def _local(url: str) -> str:
    u = make_url(url)
    if u.host != "postgres" or Path("/.dockerenv").exists():
        return url
    host = os.environ.get("TEST_DB_HOST", "localhost")
    port = int(os.environ.get("TEST_DB_PORT", "5433"))
    return u.set(host=host, port=port).render_as_string(hide_password=False)


@pytest.fixture
async def engines():
    s = get_settings()
    if not s.database_url or not s.database_url_readonly:
        # A clean checkout (CI) has no .env: no database is configured,
        # which is the same situation as one that is not reachable.
        pytest.skip("no database configured")
    owner = make_owner_engine(_local(s.database_url))
    readonly = make_readonly_engine(_local(s.database_url_readonly))
    try:
        async with owner.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        await owner.dispose()
        await readonly.dispose()
        pytest.skip(f"database not reachable: {type(exc).__name__}")
    yield owner, readonly
    await owner.dispose()
    await readonly.dispose()


async def test_engines_log_in_as_different_roles(engines):
    owner, readonly = engines
    async with owner.connect() as conn:
        owner_user = (await conn.execute(text("SELECT current_user"))).scalar()
    async with readonly.connect() as conn:
        ro_user = (await conn.execute(text("SELECT current_user"))).scalar()
    assert owner_user != ro_user
    assert ro_user == "copilot_readonly"


async def test_readonly_can_read(engines):
    _, readonly = engines
    async with readonly.connect() as conn:
        count = (await conn.execute(text("SELECT count(*) FROM vehicles"))).scalar()
    assert count >= 0


async def test_readonly_cannot_write(engines):
    _, readonly = engines
    with pytest.raises(DBAPIError) as err:
        async with readonly.begin() as conn:
            await conn.execute(text(
                "INSERT INTO vehicles (vehicle_id, model_code) VALUES ('V-TEST', 'Volt 1 Gen 2')"))
    # Refused by the read-only session, before the role's grants are
    # even consulted — the second lock.
    assert "read-only transaction" in str(err.value)


async def test_readonly_session_is_locked_and_time_limited(engines):
    _, readonly = engines
    async with readonly.connect() as conn:
        read_only = (await conn.execute(text("SHOW default_transaction_read_only"))).scalar()
        timeout = (await conn.execute(text("SHOW statement_timeout"))).scalar()
    assert read_only == "on"
    assert timeout == "5s"


async def test_owner_can_write(engines):
    owner, _ = engines
    # Inside a transaction that is rolled back: proves the permission,
    # leaves the table as it was.
    async with owner.connect() as conn:
        trans = await conn.begin()
        await conn.execute(text(
            "INSERT INTO vehicles (vehicle_id, model_code) VALUES ('V-TEST', 'Volt 1 Gen 2')"))
        assert (await conn.execute(text(
            "SELECT count(*) FROM vehicles WHERE vehicle_id = 'V-TEST'"))).scalar() == 1
        await trans.rollback()
