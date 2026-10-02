"""Scores the live judge gives sampled answers (evaluation/live_judge.py).

Kept per turn and judge version, so a change of judge never mixes into
an old trend. Not readable by the tool roles: these are about the
service, not the fleet.
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

SQL = """
CREATE TABLE IF NOT EXISTS live_judgements (
    id            BIGSERIAL PRIMARY KEY,
    turn_id       TEXT NOT NULL REFERENCES conversation_turns(turn_id),
    judge_version TEXT NOT NULL,
    faithfulness  INT NOT NULL CHECK (faithfulness BETWEEN 1 AND 4),
    hedging       INT NOT NULL CHECK (hedging BETWEEN 1 AND 4),
    helpfulness   INT NOT NULL CHECK (helpfulness BETWEEN 1 AND 4),
    notes         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_live_judgements_time ON live_judgements (created_at DESC);
"""


def upgrade() -> None:
    op.get_bind().connection.driver_connection.execute(SQL)


def downgrade() -> None:
    raise NotImplementedError("forward-only: a downgrade on real data is a restore")
