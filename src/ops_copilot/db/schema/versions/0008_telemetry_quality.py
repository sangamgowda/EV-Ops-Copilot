"""Data-quality guard: flag telemetry readings outside physical ranges.

Real telemetry contains readings that cannot be true: a temperature
probe stuck at -40, a pack reading 0 V, a current spike far beyond what
the controller can deliver. Averaged in, they move the numbers the
diagnosis rests on.

Every reading now carries `quality_flag`: NULL when plausible,
'below_physical_range' / 'above_physical_range' when not. A trigger
sets it on insert and update, so no writer can skip it. The ranges are
rows in metric_physical_ranges, synced from config/app_config.yaml on
every migrate run (migrate.py), which also re-flags existing readings.

Flagged readings are kept, not deleted: a sensor fault is itself a
finding. The SQL tool leaves them out of model-written queries unless
the query asks about quality_flag (sql/validator.py).
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

SQL = """
CREATE TABLE IF NOT EXISTS metric_physical_ranges (
    metric_name TEXT PRIMARY KEY,
    unit        TEXT,
    min_value   DOUBLE PRECISION NOT NULL,
    max_value   DOUBLE PRECISION NOT NULL,
    note        TEXT,
    CHECK (min_value < max_value)
);

ALTER TABLE vehicle_telemetry ADD COLUMN IF NOT EXISTS quality_flag TEXT;

-- One definition of "out of range", used by the trigger and by the
-- re-flagging in migrate.py. A metric with no range is never flagged.
CREATE OR REPLACE FUNCTION telemetry_quality_flag(metric TEXT, value DOUBLE PRECISION)
RETURNS TEXT LANGUAGE sql STABLE AS $$
    SELECT CASE
        WHEN value < r.min_value THEN 'below_physical_range'
        WHEN value > r.max_value THEN 'above_physical_range'
    END
    FROM metric_physical_ranges r WHERE r.metric_name = metric
$$;

CREATE OR REPLACE FUNCTION flag_telemetry_quality() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.quality_flag := telemetry_quality_flag(NEW.metric_name, NEW.metric_value);
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_telemetry_quality ON vehicle_telemetry;
CREATE TRIGGER trg_telemetry_quality
    BEFORE INSERT OR UPDATE OF metric_name, metric_value ON vehicle_telemetry
    FOR EACH ROW EXECUTE FUNCTION flag_telemetry_quality();

-- Flagged readings are rare; a partial index finds them cheaply.
CREATE INDEX IF NOT EXISTS idx_telemetry_flagged
    ON vehicle_telemetry (vehicle_id, recorded_at) WHERE quality_flag IS NOT NULL;

-- The tools may read both; table-level grants do not cover new tables.
GRANT SELECT ON metric_physical_ranges TO copilot_readonly, copilot_sql;
"""


def upgrade() -> None:
    op.get_bind().connection.driver_connection.execute(SQL)


def downgrade() -> None:
    raise NotImplementedError("forward-only: a downgrade on real data is a restore")
