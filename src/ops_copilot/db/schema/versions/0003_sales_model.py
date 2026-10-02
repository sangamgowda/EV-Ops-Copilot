"""Model and city on sales transactions.

Runs db/migrations/003_sales_model.sql unchanged (idempotent, so a database created
by the old first-boot scripts upgrades cleanly).
"""

from __future__ import annotations

from ops_copilot.db.schema.sqlfile import run_sql_file

revision = "0003"
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("003_sales_model.sql")


def downgrade() -> None:
    raise NotImplementedError("forward-only: a downgrade on real data is a restore")
