"""Alembic environment: one synchronous connection, owner role.

Two API replicas starting together would both try to migrate. A
Postgres advisory lock makes the second wait until the first is done,
then find nothing left to apply.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, text

from ops_copilot.db.migrate import database_url

MIGRATION_LOCK = 72_026_001   # any constant shared by every migrator


def run() -> None:
    engine = create_engine(database_url())
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK})
        conn.commit()
        try:
            context.configure(connection=conn, target_metadata=None)
            with context.begin_transaction():
                context.run_migrations()
            conn.commit()
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK})
            conn.commit()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("offline (SQL script) mode is not supported; run against a database")
run()
