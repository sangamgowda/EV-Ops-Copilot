"""Feedback loop: turn outcomes and review status.

Runs db/migrations/006_feedback_loop.sql unchanged (idempotent, so a database created
by the old first-boot scripts upgrades cleanly).
"""

from __future__ import annotations

from ops_copilot.db.schema.sqlfile import run_sql_file

revision = "0006"
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("006_feedback_loop.sql")


def downgrade() -> None:
    raise NotImplementedError("forward-only: a downgrade on real data is a restore")
