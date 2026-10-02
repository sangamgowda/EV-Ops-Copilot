"""Document uploader and trust level.

Runs db/migrations/007_document_trust.sql unchanged (idempotent, so a database created
by the old first-boot scripts upgrades cleanly).
"""

from __future__ import annotations

from ops_copilot.db.schema.sqlfile import run_sql_file

revision = "0007"
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("007_document_trust.sql")


def downgrade() -> None:
    raise NotImplementedError("forward-only: a downgrade on real data is a restore")
