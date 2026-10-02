"""copilot_sql role: model-written SQL cannot read embeddings.

Runs db/migrations/005_sql_tool_role.sql unchanged (idempotent, so a database created
by the old first-boot scripts upgrades cleanly).
"""

from __future__ import annotations

from ops_copilot.db.schema.sqlfile import run_sql_file

revision = "0005"
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("005_sql_tool_role.sql")


def downgrade() -> None:
    raise NotImplementedError("forward-only: a downgrade on real data is a restore")
