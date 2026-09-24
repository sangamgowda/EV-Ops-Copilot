"""SQLAlchemy Core tables for the operational tables the API writes.

Kept in sync by hand; the SQL files in migrations/ are the source of
truth because they are what actually runs on container boot.

Only the tables this process writes are mirrored. Lane A and Lane B
tables are written by the seed script and by ingestion with explicit
SQL, and read through the tools — mirroring them would be a second
definition to keep in sync for no caller.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

metadata = MetaData()

conversation_turns = Table(
    "conversation_turns", metadata,
    Column("turn_id", Text, primary_key=True),       # == trace id, deliberately
    Column("session_id", Text, nullable=False),
    Column("question", Text, nullable=False),
    Column("answer", Text),
    Column("domains", ARRAY(Text)),
    Column("entities", JSONB),
    Column("iterations", Integer),
    Column("stop_reason", Text),
    Column("partial", Boolean, default=False),
    Column("prompt_versions", JSONB),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

flagged_interactions = Table(
    "flagged_interactions", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("turn_id", Text, ForeignKey("conversation_turns.turn_id"), nullable=False),
    Column("rating", Text, nullable=False),          # up | down | implicit_down
    Column("category", Text),
    Column("comment", Text),
    Column("cluster_id", Text),                      # failure signature
    Column("promoted", Boolean, default=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)
