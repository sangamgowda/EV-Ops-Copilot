"""Units for the columns a query returns, traced from the schema config.

The schema config knows that sales_transactions.unit_price is in INR.
A query result does not: `SELECT avg(unit_price) AS average_price`
comes back as `average_price=136961.4`, a bare number. Shown that,
the model filled in a currency itself and answered "$137,000"
(eval case syn_sales_flagship_avg_price_2026). The unit has to travel
with the number, so it is traced here, in code, from the SQL.

A result column gets a unit only when that is certain:

  - every column it is computed from has the same unit in the schema
  - it is not a COUNT (a count of prices is not in INR)
  - it is not a product or ratio of two columns (INR x km is neither)

Anything else gets no unit, which is what the evidence said before.
A wrong unit would be worse than a missing one.
"""

from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from ops_copilot.sql.schema_loader import load


def _has_column(node: exp.Expression) -> bool:
    return isinstance(node, exp.Column) or node.find(exp.Column) is not None


def result_units(sql: str, schema: dict[str, Any] | None = None) -> dict[str, str]:
    """{result column name: unit} for the outermost SELECT of `sql`."""
    schema = schema or load()
    tables = schema.get("tables", {})
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except SqlglotError:
        return {}
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if select is None:
        return {}

    # Table aliases in this query -> table names, so `st.unit_price`
    # resolves to sales_transactions.unit_price.
    alias_to_table: dict[str, str] = {}
    for t in select.find_all(exp.Table):
        alias_to_table[t.alias_or_name] = t.name
        alias_to_table[t.name] = t.name
    in_query = set(alias_to_table.values())

    def unit_of(col: exp.Column) -> str | None:
        if col.table:
            names = [alias_to_table.get(col.table, col.table)]
        else:
            names = [t for t in in_query if col.name in tables.get(t, {}).get("columns", {})]
        units = {tables.get(t, {}).get("columns", {}).get(col.name, {}).get("unit") for t in names}
        return units.pop() if len(units) == 1 else None

    out: dict[str, str] = {}
    for proj in select.expressions:
        name = proj.alias_or_name
        if not name or proj.find(exp.Count):
            continue
        if any(_has_column(op.left) and _has_column(op.right)
               for op in proj.find_all(exp.Mul, exp.Div)):
            continue
        cols = list(proj.find_all(exp.Column))
        if not cols:
            continue
        units = {unit_of(c) for c in cols}
        if len(units) == 1 and (unit := units.pop()):
            out[name] = unit
    return out
