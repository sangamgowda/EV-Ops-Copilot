"""Core schema: vehicles, telemetry, sales, documents, operational tables.

Runs db/migrations/001_core_schema.sql unchanged (idempotent, so a database created
by the old first-boot scripts upgrades cleanly).
"""

from __future__ import annotations

from ops_copilot.db.schema.sqlfile import run_sql_file

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("001_core_schema.sql")


def downgrade() -> None:
    raise NotImplementedError("forward-only: a downgrade on real data is a restore")
