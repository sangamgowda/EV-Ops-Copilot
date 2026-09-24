"""Measure the EXPLAIN cost budget against the data actually loaded.

    python scripts/calibrate_cost_budget.py

A cost budget tuned on empty tables is wildly wrong: every plan is
cheap when there is nothing to scan. This asks the planner what
reference queries cost on the current data — questions the agent
must be able to answer, and scans it must never run — and reports
where the configured budget sits between them.

Re-run it whenever data volume changes by an order of magnitude.
Planner cost grows with table size, so a budget that separates the
two groups at 2M telemetry rows lets full scans through at 200M.

Exits non-zero if the configured budget rejects a legitimate query
or admits a scan, so it can gate a deploy.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ops_copilot.db.engine import readonly_sql_pool  # noqa: E402
from ops_copilot.settings import get_config, get_schema_config  # noqa: E402
from ops_copilot.sql.validator import SQLValidator  # noqa: E402


def window(days: int) -> str:
    return f"recorded_at >= now() - interval '{days} days'"


# Questions the agent must be able to answer. The heaviest of these
# sets the floor for the budget.
MUST_PASS = {
    "one vehicle vs baseline, 7d":
        "SELECT t.metric_name, avg(t.metric_value), avg(b.nominal_value) FROM vehicle_telemetry t "
        "JOIN vehicles v ON v.vehicle_id = t.vehicle_id JOIN vehicle_baseline_specs b "
        "ON b.model_code = v.model_code AND b.drive_mode = t.drive_mode AND b.metric_name = t.metric_name "
        "WHERE t.vehicle_id = 'V-042' AND t.recorded_at >= now() - interval '7 days' GROUP BY t.metric_name",
    "one vehicle, raw readings, 30d":
        f"SELECT recorded_at, metric_value FROM vehicle_telemetry WHERE vehicle_id = 'V-042' AND {window(30)}",
    "whole fleet, one metric, 7d":
        f"SELECT vehicle_id, avg(metric_value) FROM vehicle_telemetry "
        f"WHERE metric_name = 'current_draw' AND {window(7)} GROUP BY vehicle_id",
    "sales by region, this quarter":
        "SELECT region, count(*), sum(unit_price) FROM sales_transactions "
        "WHERE sold_on >= date_trunc('quarter', now()) GROUP BY region",
    "sales by month, all time":
        "SELECT date_trunc('month', sold_on), count(*) FROM sales_transactions GROUP BY 1",
}

# Structurally valid — they pass every AST rule — and ruinous at
# scale. The cheapest of these sets the ceiling.
MUST_REJECT = {
    "whole telemetry table, aggregated":
        "SELECT vehicle_id, metric_name, avg(metric_value) FROM vehicle_telemetry "
        "WHERE recorded_at > '1900-01-01' GROUP BY 1, 2",
    "whole telemetry table, sorted":
        "SELECT metric_value FROM vehicle_telemetry WHERE recorded_at > '1900-01-01' ORDER BY metric_value",
}


def main() -> int:
    cfg = get_config()
    validator = SQLValidator(get_schema_config(), cfg)
    budget = validator.explain_cost_budget
    costs: dict[str, dict[str, float]] = {"pass": {}, "reject": {}}

    with readonly_sql_pool().connection() as conn:
        for group, cases in (("pass", MUST_PASS), ("reject", MUST_REJECT)):
            for name, sql in cases.items():
                checked = validator.validate(sql)
                if not checked.ok:
                    print(f"  reference query rejected by the AST layer: {name}: {checked.reasons}")
                    return 2
                costs[group][name] = validator.explain_gate(conn, checked.sql).explain_cost or 0.0

    print(f"{'reference query':<40} {'planner cost':>13}  verdict at budget {budget:,.0f}")
    for group, label in (("pass", "must pass"), ("reject", "must reject")):
        print(f"-- {label}")
        for name, cost in costs[group].items():
            verdict = "pass" if cost <= budget else "REJECT"
            print(f"   {name:<37} {cost:>13,.0f}  {verdict}")

    floor, ceiling = max(costs["pass"].values()), min(costs["reject"].values())
    print(f"\nheaviest legitimate query: {floor:,.0f}   cheapest full scan: {ceiling:,.0f}")
    if floor >= ceiling:
        print("no budget separates them — add an index or narrow the reference queries")
        return 1
    if not floor < budget < ceiling:
        print(f"configured budget {budget:,.0f} is outside ({floor:,.0f}, {ceiling:,.0f}) — "
              "set sql_validation.explain_cost_budget inside that band")
        return 1
    print(f"configured budget {budget:,.0f} separates them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
