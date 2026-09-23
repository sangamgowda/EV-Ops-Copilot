"""Validator tests. Pure functions, no external dependencies.

Each case below maps to a specific attack or failure mode.
"""

from __future__ import annotations

import pytest

from ops_copilot.settings import get_config, get_schema_config
from ops_copilot.sql.validator import SQLValidator


@pytest.fixture
def validator() -> SQLValidator:
    return SQLValidator(get_schema_config(), get_config())


class TestRejects:
    def test_stacked_statements(self, validator):
        """The classic injection. Dies on the statement count check,
        before any other analysis runs."""
        sql = "DROP TABLE vehicle_telemetry; SELECT * FROM sales_transactions;"
        assert not validator.validate(sql).ok

    def test_bare_drop(self, validator):
        assert not validator.validate("DROP TABLE vehicles").ok

    def test_delete_inside_cte(self, validator):
        """Destructive nodes are rejected anywhere in the tree, not
        just at the root."""
        sql = "WITH x AS (DELETE FROM vehicles RETURNING *) SELECT * FROM x"
        assert not validator.validate(sql).ok

    def test_cross_join(self, validator):
        """No ON clause -> rejected. We do not need a rule that names
        CROSS JOIN; requiring a join predicate covers it."""
        sql = ("SELECT * FROM vehicle_telemetry "
               "CROSS JOIN sales_transactions LIMIT 10")
        assert not validator.validate(sql).ok

    def test_unknown_table(self, validator):
        assert not validator.validate("SELECT * FROM secret_table LIMIT 1").ok

    def test_unknown_column(self, validator):
        sql = "SELECT nonexistent_col FROM vehicles LIMIT 1"
        assert not validator.validate(sql).ok

    def test_denied_embedding_column(self, validator):
        sql = "SELECT embedding FROM document_chunks LIMIT 1"
        assert not validator.validate(sql).ok

    def test_telemetry_without_time_filter(self, validator):
        """Unbounded scan of the biggest table — the realistic way to
        hurt the database with a valid SELECT."""
        sql = "SELECT metric_value FROM vehicle_telemetry LIMIT 10"
        assert not validator.validate(sql).ok

    def test_blocked_function(self, validator):
        sql = "SELECT pg_sleep(10)"
        assert not validator.validate(sql).ok


class TestAccepts:
    def test_simple_select_with_time_filter(self, validator):
        sql = ("SELECT metric_value FROM vehicle_telemetry "
               "WHERE vehicle_id = 'V1' AND recorded_at > now() - interval '7 days' "
               "LIMIT 10")
        assert validator.validate(sql).ok

    def test_declared_join(self, validator):
        sql = ("SELECT v.model_code, t.metric_value "
               "FROM vehicles v JOIN vehicle_telemetry t "
               "ON v.vehicle_id = t.vehicle_id "
               "WHERE t.recorded_at > now() - interval '1 day' LIMIT 10")
        assert validator.validate(sql).ok

    def test_jsonb_access(self, validator):
        sql = "SELECT config->>'battery_pack' FROM vehicles LIMIT 5"
        assert validator.validate(sql).ok


class TestRewrites:
    def test_limit_injected(self, validator):
        result = validator.validate("SELECT code FROM error_codes")
        assert result.ok
        assert "LIMIT" in result.sql.upper()

    def test_limit_clamped(self, validator):
        result = validator.validate("SELECT code FROM error_codes LIMIT 999999")
        assert result.ok
        assert "999999" not in result.sql

    def test_aggregate_not_limited(self, validator):
        """A LIMIT does not reduce an aggregate's work and can change
        the answer. Cost control for these is the EXPLAIN gate."""
        sql = "SELECT region, count(*) FROM sales_transactions GROUP BY region"
        result = validator.validate(sql)
        assert result.ok
