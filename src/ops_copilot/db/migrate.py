"""Versioned database migrations (Alembic).

    python -m ops_copilot.db.migrate                 # upgrade to the latest
    python -m ops_copilot.db.migrate current         # which revision the database is at
    python -m ops_copilot.db.migrate history
    python -m ops_copilot.db.migrate new "add a column"

Before this, the SQL files ran once, when the Postgres container first
created its data volume. A change made after that never reached an
existing database except by hand. Now every database records the
revision it is at (table alembic_version), and an upgrade applies only
what it has not seen. The `migrate` service in docker-compose runs this
before the API and the tool server start.

Revisions live in db/schema/versions. The early ones run the original
SQL files in db/migrations unchanged; those files are idempotent, so an
older database created by the first-boot scripts upgrades cleanly with
no manual stamping.

Two things are synced on EVERY upgrade, not once, because their source
is outside the database and can change:
  - the read-only role's password, from DB_READONLY_PASSWORD (.env is
    the only place it is set; rotate it there and re-run);
  - the physical ranges the data-quality guard checks telemetry against,
    from config/app_config.yaml `data_quality.physical_ranges`;
  - the hourly telemetry rollups (TimescaleDB), refreshed incrementally;
  - the width of the embedding column, while the corpus is still empty.
Revisions are forward-only: a downgrade on production data is a restore.
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from ops_copilot.settings import get_config, get_settings

HERE = Path(__file__).resolve().parent
SQL_DIR = HERE / "migrations"


def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(HERE / "schema"))
    cfg.set_main_option("path_separator", "os")
    return cfg


def database_url() -> str:
    url = get_settings().database_url
    if not url or "${" in url:
        sys.exit("DATABASE_URL (the owner connection) must be set to run migrations")
    return url


def sync_readonly_password(conn) -> bool:
    password = get_settings().db_readonly_password
    if not password:
        return False
    raw = conn.connection.driver_connection
    # If the statement fails Postgres logs it verbatim, password included.
    raw.execute("SET LOCAL log_min_error_statement = 'PANIC'")
    literal = "'" + password.replace("'", "''") + "'"
    raw.execute(f"ALTER ROLE copilot_readonly PASSWORD {literal}")
    return True


def sync_physical_ranges(conn) -> int:
    """Upsert the configured ranges, then re-flag the readings of every
    metric whose range CHANGED. Returns the number of flagged readings.

    Only changed metrics: re-checking every reading on every run means
    decompressing every compressed TimescaleDB chunk (revision 0010),
    which the extension refuses past a limit, and is wasted work when
    nothing changed — the usual case. New readings are flagged by the
    trigger as they arrive."""
    ranges = get_config()["data_quality"]["physical_ranges"]
    stored = {m: (lo, hi) for m, lo, hi in conn.execute(text(
        "SELECT metric_name, min_value, max_value FROM metric_physical_ranges")).all()}
    wanted = {m: (float(r["min"]), float(r["max"])) for m, r in ranges.items()}
    changed = sorted(m for m in stored.keys() | wanted.keys() if stored.get(m) != wanted.get(m))
    conn.execute(text("DELETE FROM metric_physical_ranges WHERE NOT (metric_name = ANY(:names))"),
                 {"names": list(ranges)})
    for metric, r in ranges.items():
        conn.execute(text("""
            INSERT INTO metric_physical_ranges (metric_name, unit, min_value, max_value, note)
            VALUES (:m, :unit, :lo, :hi, :note)
            ON CONFLICT (metric_name) DO UPDATE SET unit = EXCLUDED.unit, min_value = EXCLUDED.min_value,
                max_value = EXCLUDED.max_value, note = EXCLUDED.note
        """), {"m": metric, "unit": r.get("unit"), "lo": r["min"], "hi": r["max"], "note": r.get("note")})
    if changed:
        # A deliberate range change may touch compressed chunks: lift the
        # decompression cap for this transaction only. set_config, not SET,
        # so it is harmless where the extension is absent.
        conn.execute(text("SELECT set_config('timescaledb.max_tuples_decompressed_per_dml_transaction', "
                          "'0', true)"))
        conn.execute(text("""
            UPDATE vehicle_telemetry t
            SET quality_flag = telemetry_quality_flag(t.metric_name, t.metric_value)
            WHERE t.metric_name = ANY(:changed)
              AND t.quality_flag IS DISTINCT FROM telemetry_quality_flag(t.metric_name, t.metric_value)
        """), {"changed": changed})
    return int(conn.execute(text(
        "SELECT count(*) FROM vehicle_telemetry WHERE quality_flag IS NOT NULL")).scalar() or 0)


def sync_embedding_column(conn) -> str:
    """Size document_chunks.embedding for the configured model.

    With no chunks yet (a fresh install) the column is simply resized,
    so EMBEDDING_MODEL/EMBEDDING_DIM in .env decide the vector width
    before the first ingest. With chunks already embedded by another
    model this changes nothing and says so: re-embedding is a deliberate
    step (scripts/reembed.py), not a side effect of a migration."""
    s = get_settings()
    target = f"vector({s.embedding_dim})"
    col = conn.execute(text(
        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
        "WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding'")).scalar()
    stored = conn.execute(text("SELECT value FROM corpus_meta WHERE key = 'embedding_model'")).scalar()
    has_chunks = conn.execute(text("SELECT EXISTS (SELECT 1 FROM document_chunks)")).scalar()
    if not has_chunks:
        if col != target:
            conn.execute(text("DROP INDEX IF EXISTS idx_chunks_embedding"))
            conn.execute(text(f"ALTER TABLE document_chunks ALTER COLUMN embedding TYPE {target}"))
            conn.execute(text("CREATE INDEX idx_chunks_embedding ON document_chunks "
                              "USING hnsw (embedding vector_cosine_ops)"))
        conn.execute(text("DELETE FROM corpus_meta WHERE key = 'embedding_model'"))
        return f"empty corpus, ready for {s.embedding_model} [{target}]"
    if stored != s.embedding_model or col != target:
        return (f"WARNING: corpus is {stored} [{col}] but EMBEDDING_MODEL is {s.embedding_model} [{target}]; "
                "search refuses to run until scripts/reembed.py")
    return f"corpus embedded with {stored}"


def refresh_rollups(engine) -> bool:
    """Bring telemetry_hourly up to date (revision 0010). A continuous
    aggregate refresh cannot run inside a transaction, hence AUTOCOMMIT.
    Incremental: only hours whose readings changed are recomputed."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        exists = conn.execute(text("SELECT to_regclass('telemetry_hourly') IS NOT NULL")).scalar()
        if exists:
            conn.execute(text("CALL refresh_continuous_aggregate('telemetry_hourly', NULL, NULL)"))
        return bool(exists)


def upgrade(target: str = "head") -> None:
    command.upgrade(alembic_config(), target)
    engine = create_engine(database_url())
    with engine.begin() as conn:
        pw = sync_readonly_password(conn)
        flagged = sync_physical_ranges(conn)
        embeddings = sync_embedding_column(conn)
    refresh_rollups(engine)
    engine.dispose()
    print(f"migrated to {target}; read-only password {'synced' if pw else 'NOT set (DB_READONLY_PASSWORD empty)'}; "
          f"{flagged} telemetry readings flagged outside physical ranges; {embeddings}")


def main(argv: list[str]) -> None:
    cmd, *rest = argv or ["upgrade"]
    cfg = alembic_config()
    if cmd == "upgrade":
        upgrade(rest[0] if rest else "head")
    elif cmd == "current":
        command.current(cfg, verbose=True)
    elif cmd == "history":
        command.history(cfg)
    elif cmd == "new" and rest:
        command.revision(cfg, message=rest[0])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
