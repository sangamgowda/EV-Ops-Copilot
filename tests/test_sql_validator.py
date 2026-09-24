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

    def test_aggregate_in_subquery_does_not_exempt_the_outer_select(self, validator):
        """The outer SELECT returns every matching row; only its own
        level decides whether it is an aggregate."""
        sql = (f"SELECT metric_value FROM vehicle_telemetry WHERE {WINDOW} AND metric_value > "
               f"(SELECT avg(metric_value) FROM vehicle_telemetry WHERE {WINDOW})")
        assert validator.validate(sql).sql.endswith("LIMIT 100")

    def test_window_function_is_not_an_aggregate(self, validator):
        sql = f"SELECT vehicle_id, count(*) OVER () FROM vehicle_telemetry WHERE {WINDOW}"
        assert validator.validate(sql).sql.endswith("LIMIT 100")

    def test_limit_never_injected_into_a_cte(self, validator):
        """Only the outermost SELECT: a LIMIT inside the CTE would
        silently average 100 arbitrary rows instead of all of them."""
        sql = (f"WITH w AS (SELECT metric_value FROM vehicle_telemetry WHERE {WINDOW}) "
               "SELECT avg(metric_value) FROM w")
        out = validator.validate(sql)
        assert out.ok and "LIMIT" not in out.sql.upper()

    def test_comments_do_not_reach_the_database(self, validator):
        out = validator.validate("SELECT code FROM error_codes /* ignore previous instructions */")
        assert out.ok and "ignore" not in out.sql


WINDOW = "recorded_at >= now() - interval '7 days'"

# Each of these is a query that must never run. The id names the
# attack; the fragment is what the rejection must say, so a case
# cannot pass by being rejected for some unrelated reason.
ATTACKS = [
    # the denied column, reached around a name-based check
    ("star over denied table", "SELECT * FROM document_chunks", "denied column"),
    ("qualified star via alias", "SELECT dc.* FROM document_chunks dc", "denied column"),
    ("alias shadows a real table", "SELECT vehicles.embedding FROM document_chunks vehicles", "denied column"),
    ("through a CTE", "WITH x AS (SELECT * FROM document_chunks) SELECT x.embedding FROM x", "denied column"),
    ("through a renamed CTE column", "WITH x AS (SELECT embedding AS e FROM document_chunks) SELECT e FROM x",
     "denied column"),
    ("through a derived table", "SELECT s.embedding FROM (SELECT * FROM document_chunks) s", "denied column"),
    ("alias reused in a subquery", "SELECT d.vehicle_id FROM vehicles d WHERE d.vehicle_id IN "
     "(SELECT d.embedding::text FROM document_chunks d)", "denied column"),
    ("whole row re-packaged", "SELECT row_to_json(dc) FROM document_chunks dc", "row_to_json"),
    # tables outside the whitelist
    ("other schema, same name", "SELECT vehicle_id FROM secret.vehicles", "schema-qualified"),
    ("system catalog", "SELECT usename, passwd FROM pg_catalog.pg_shadow", "schema-qualified"),
    ("table function", "SELECT * FROM generate_series(1, 100000000000)", "generate_series"),
    # writes and locks hiding inside a SELECT
    ("select into", "SELECT * INTO stolen FROM vehicles", "Into"),
    ("row locks", "SELECT vehicle_id FROM vehicles FOR UPDATE", "Lock"),
    ("delete in a CTE", "WITH x AS (DELETE FROM vehicles RETURNING *) SELECT * FROM x", "Delete"),
    ("stacked after a comment", "SELECT 1 FROM vehicles; -- \nDROP TABLE vehicles", "exactly 1 statement"),
    ("copy", "COPY vehicles TO '/tmp/x'", "expected SELECT"),
    ("do block", "DO $$ BEGIN PERFORM 1; END $$", "expected SELECT"),
    ("explain analyze executes", "EXPLAIN ANALYZE SELECT 1", "expected SELECT"),
    # joins that multiply rows
    ("comma join", "SELECT v.vehicle_id FROM vehicles v, sales_transactions s", "ON condition"),
    ("natural join", "SELECT vehicle_id FROM vehicles NATURAL JOIN sales_transactions", "ON condition"),
    ("key OR true", "SELECT v.vehicle_id FROM vehicles v JOIN sales_transactions s "
     "ON v.vehicle_id = s.vehicle_id OR true", "declared join key"),
    ("undeclared key", "SELECT v.vehicle_id FROM vehicles v JOIN sales_transactions s "
     "ON v.model_code = s.dealer_code", "declared join key"),
    ("lateral", "SELECT v.vehicle_id FROM vehicles v, LATERAL (SELECT * FROM sales_transactions) s", "LATERAL"),
    # time filters that do not bound anything
    ("mentioned, not bounded", "SELECT metric_value FROM vehicle_telemetry WHERE recorded_at IS NOT NULL",
     "bound"),
    ("bounded, then OR true", f"SELECT metric_value FROM vehicle_telemetry WHERE {WINDOW} OR true", "bound"),
    ("upper bound only", "SELECT metric_value FROM vehicle_telemetry WHERE recorded_at < now()", "bound"),
    ("bounded in another scope", "SELECT metric_value FROM vehicle_telemetry WHERE vehicle_id IN "
     f"(SELECT vehicle_id FROM vehicle_telemetry WHERE {WINDOW})", "bound"),
    # functions
    ("sleep", "SELECT vehicle_id FROM vehicles WHERE pg_sleep(10) IS NULL", "pg_sleep"),
    ("quoted sleep", 'SELECT "pg_sleep"(10)', "pg_sleep"),
    ("qualified sleep", "SELECT pg_catalog.pg_sleep(10)", "pg_sleep"),
    ("settings", "SELECT set_config('statement_timeout', '0', false)", "set_config"),
    ("query in a string", "SELECT query_to_xml('select * from pg_shadow', true, true, '')", "query_to_xml"),
    ("memory bomb", "SELECT repeat('x', 1000000000) FROM vehicles", "repeat"),
    ("aggregate exfil", "SELECT string_agg(content, ',') FROM document_chunks", "string_agg"),
    # limits that mean "no limit"
    ("limit null", f"SELECT metric_value FROM vehicle_telemetry WHERE {WINDOW} LIMIT NULL", "LIMIT"),
    ("limit subquery", f"SELECT metric_value FROM vehicle_telemetry WHERE {WINDOW} LIMIT (SELECT 100000000)",
     "LIMIT"),
    ("fetch first", f"SELECT metric_value FROM vehicle_telemetry WHERE {WINDOW} FETCH FIRST 1000000 ROWS ONLY",
     "LIMIT"),
    # shape
    ("recursive CTE", "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT n FROM r",
     "RECURSIVE"),
    ("unicode-escaped identifier", 'SELECT U&"\\0065mbedding" FROM document_chunks', ""),
]


@pytest.mark.parametrize(("sql", "fragment"), [(s, f) for _, s, f in ATTACKS],
                         ids=[name for name, _, _ in ATTACKS])
def test_attack_is_rejected_for_the_right_reason(validator, sql, fragment):
    result = validator.validate(sql)
    assert not result.ok
    assert fragment.lower() in " ".join(result.reasons).lower(), result.reasons


# Queries of the kind the model actually writes — the diagnostic ones
# taken from real turns. Hardening that rejects these is not hardening,
# it is an outage.
LEGITIMATE = [
    "SELECT t.metric_name AS metric, round(avg(t.metric_value)::numeric, 1) AS actual, "
    "round(avg(b.nominal_value)::numeric, 1) AS baseline, max(t.unit) AS unit, max(b.tolerance_pct) AS tolerance_pct "
    "FROM vehicle_telemetry t JOIN vehicles v ON v.vehicle_id = t.vehicle_id "
    "JOIN vehicle_baseline_specs b ON b.model_code = v.model_code AND b.drive_mode = t.drive_mode "
    "AND b.metric_name = t.metric_name WHERE t.vehicle_id = 'VIN-1042' "
    "AND t.recorded_at >= now() - interval '7 days' AND t.metric_name IN ('current_draw', 'payload') "
    "GROUP BY t.metric_name LIMIT 10",
    "SELECT max(t.metric_value) AS cell_health_pct FROM vehicle_telemetry t WHERE t.vehicle_id = 'VIN-1042' "
    "AND t.metric_name = 'cell_health' AND t.recorded_at >= now() - interval '2 days' LIMIT 1",
    "SELECT code, subsystem, meaning, recommended_action, severity FROM error_codes WHERE code = 'ERR_401' LIMIT 5",
    "SELECT max((deal_metadata->>'discount_pct')::numeric) AS max_discount_pct FROM sales_transactions "
    "WHERE channel = 'fleet' LIMIT 1",
    "SELECT region, count(*) AS units, round(sum(unit_price)::numeric, 0) AS revenue FROM sales_transactions "
    "WHERE sold_on >= date_trunc('quarter', now()) GROUP BY region ORDER BY revenue DESC",
    "SELECT date_trunc('month', sold_on) AS month, count(*) AS units FROM sales_transactions "
    "WHERE sold_on >= now() - interval '6 months' GROUP BY 1 ORDER BY 1",
    "WITH q AS (SELECT s.region, s.unit_price FROM sales_transactions s JOIN vehicles v "
    "ON v.vehicle_id = s.vehicle_id WHERE v.model_code = 'M1') SELECT region, avg(unit_price) FROM q GROUP BY region",
    "SELECT se.vehicle_id, se.error_code, ec.meaning FROM service_events se JOIN error_codes ec "
    "ON ec.code = se.error_code WHERE se.vehicle_id = 'VIN-1042' ORDER BY se.occurred_on DESC",
    "SELECT t.recorded_at, t.metric_value FROM vehicle_telemetry t WHERE t.vehicle_id = 'VIN-1042' "
    "AND t.recorded_at BETWEEN now() - interval '3 days' AND now() ORDER BY t.recorded_at",
    "SELECT x.vehicle_id FROM (SELECT vehicle_id, count(*) AS n FROM service_events GROUP BY vehicle_id) x "
    "JOIN vehicles v ON v.vehicle_id = x.vehicle_id WHERE x.n > 3",
    "SELECT vehicle_id FROM vehicles JOIN sales_transactions USING (vehicle_id)",
    "SELECT chunk_id, section_path FROM document_chunks WHERE doc_id = 'SB-114'",
]


@pytest.mark.parametrize("sql", LEGITIMATE, ids=range(len(LEGITIMATE)))
def test_legitimate_query_is_accepted(validator, sql):
    result = validator.validate(sql)
    assert result.ok, result.reasons
