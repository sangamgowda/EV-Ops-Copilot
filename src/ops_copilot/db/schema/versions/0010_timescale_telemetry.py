"""TimescaleDB: telemetry as a hypertable, with hourly rollups.

vehicle_telemetry is by far the largest table and grows with every
vehicle, every few minutes. Three things change:

  - It becomes a hypertable, split into 7-day chunks by recorded_at. A
    query for "the last 7 days" now reads one or two chunks instead of
    scanning an index over the whole table (chunk exclusion).
  - telemetry_hourly, a continuous aggregate: per vehicle, metric and
    ride mode, the hourly count, sum, average, min and max. Questions
    over weeks or the whole fleet read a few thousand hourly rows
    instead of millions of readings. Readings flagged outside physical
    ranges are excluded here too. Real-time aggregation is on, so the
    current hour is included before the next refresh.
  - Chunks older than 30 days are compressed (segmented by vehicle and
    metric), typically cutting their size by an order of magnitude.

A hypertable's unique indexes must include its time column, so the
primary key becomes (id, recorded_at). Nothing references it.

The aggregate is created WITH NO DATA because a continuous aggregate
cannot be filled inside a transaction; migrate.py refreshes it right
after the upgrade, outside one, and a background policy keeps it fresh.
"""

from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

SQL = """
CREATE EXTENSION IF NOT EXISTS timescaledb;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM timescaledb_information.hypertables
                   WHERE hypertable_name = 'vehicle_telemetry') THEN
        ALTER TABLE vehicle_telemetry DROP CONSTRAINT IF EXISTS vehicle_telemetry_pkey;
        ALTER TABLE vehicle_telemetry ADD PRIMARY KEY (id, recorded_at);
        PERFORM create_hypertable('vehicle_telemetry', by_range('recorded_at', INTERVAL '7 days'),
                                  migrate_data => true);
    END IF;
END
$$;

CREATE MATERIALIZED VIEW IF NOT EXISTS telemetry_hourly
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 hour', recorded_at) AS hour,
       vehicle_id,
       metric_name,
       drive_mode,
       count(*)          AS readings,
       sum(metric_value) AS sum_value,
       avg(metric_value) AS avg_value,
       min(metric_value) AS min_value,
       max(metric_value) AS max_value,
       max(unit)         AS unit
FROM vehicle_telemetry
WHERE quality_flag IS NULL
GROUP BY hour, vehicle_id, metric_name, drive_mode
WITH NO DATA;

-- Keep the last three days refreshed every 30 minutes; older hours
-- change only on backfill, which invalidates and re-refreshes them.
SELECT add_continuous_aggregate_policy('telemetry_hourly',
    start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes', if_not_exists => true);

ALTER TABLE vehicle_telemetry SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'vehicle_id, metric_name',
    timescaledb.compress_orderby = 'recorded_at DESC'
);
SELECT add_compression_policy('vehicle_telemetry', INTERVAL '30 days', if_not_exists => true);

GRANT SELECT ON telemetry_hourly TO copilot_readonly, copilot_sql;
"""


def upgrade() -> None:
    op.get_bind().connection.driver_connection.execute(SQL)


def downgrade() -> None:
    raise NotImplementedError("forward-only: a downgrade on real data is a restore")
