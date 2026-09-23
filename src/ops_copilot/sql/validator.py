"""Deterministic SQL validation. No LLM anywhere in this file.

The model writes SQL. This decides whether it runs.

Design premise: the model is not a security boundary. It does not
matter how well-behaved generation looks, or that the schema config
gives it no reason to write a DROP. A poisoned document retrieved
earlier in the same turn can steer generation. So every query is
treated as hostile, regardless of where it came from.

Two layers, in order:

  1. AST checks (this module, sqlglot). Structural. Fast. Catches
     the whole class of "this query should not exist".

  2. EXPLAIN cost gate. Catches the class AST rules cannot reason
     about — queries that are structurally perfect and ruinously
     expensive. This is the check that actually protects the
     database.

Beneath both: a read-only role on a read replica with a statement
timeout. If something gets past layers 1 and 2, it still cannot
write, and it still cannot run for more than five seconds.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import sqlglot
from sqlglot import exp

log = logging.getLogger(__name__)

# Node types that must never appear anywhere in the tree — not at
# the root, not inside a CTE, not inside a subquery.
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter,
    exp.Create, exp.TruncateTable, exp.Grant, exp.Command,
    exp.Transaction, exp.Commit, exp.Rollback, exp.Set,
)

# Functions that read files, sleep, or reach outside the database.
# Blocked by name regardless of the allowlist.
ALWAYS_BLOCKED_FUNCTIONS = {
    "pg_sleep", "pg_read_file", "pg_read_binary_file", "pg_ls_dir",
    "lo_import", "lo_export", "dblink", "dblink_exec",
    "pg_terminate_backend", "pg_cancel_backend", "set_config",
    "current_setting", "pg_reload_conf", "copy",
}


class ValidationError(Exception):
    """Raised when a query is rejected. The message is shown to the
    agent as tool feedback, so it must say what was wrong."""


@dataclass
class ValidationResult:
    ok: bool
    sql: str                      # possibly rewritten (LIMIT injected)
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    explain_cost: Optional[float] = None
    explain_rows: Optional[float] = None


class SQLValidator:
    """Validates a generated SELECT against the schema whitelist."""

    def __init__(self, schema_config: dict[str, Any], app_config: dict[str, Any]):
        self.schema = schema_config
        cfg = app_config["sql_validation"]

        self.max_joins: int = cfg["max_joins"]
        self.max_subquery_depth: int = cfg["max_subquery_depth"]
        self.default_limit: int = cfg["default_limit"]
        self.max_limit: int = cfg["max_limit"]
        self.explain_cost_budget: float = cfg["explain_cost_budget"]
        self.explain_max_rows: float = cfg["explain_max_rows"]
        self.require_time_filter_on: set[str] = set(cfg["require_time_filter_on"])
        self.allowed_functions: set[str] = {
            f.lower() for f in cfg["allowed_functions"]
        }

        tables = schema_config.get("tables", {})
        self.allowed_tables: set[str] = set(tables.keys())
        self.allowed_columns: dict[str, set[str]] = {
            t: set(meta.get("columns", {}).keys()) for t, meta in tables.items()
        }
        self.denied_columns: set[str] = {
            c.lower() for c in schema_config.get("denied_columns", [])
        }
        self.join_keys: set[frozenset[str]] = {
            frozenset(pair) for pair in schema_config.get("join_keys", [])
        }

    # ── entry point ──────────────────────────────────────────

    def validate(self, sql: str) -> ValidationResult:
        """Run every AST check. Returns possibly-rewritten SQL.

        Does NOT run EXPLAIN — that needs a live connection and is
        done by `explain_gate` in the tool, right before execution.
        """
        reasons: list[str] = []
        warnings: list[str] = []

        # ── 1. exactly one statement ─────────────────────────
        # Catches stacked-statement injection before any other
        # analysis. "DROP TABLE x; SELECT * FROM y" dies here.
        try:
            statements = sqlglot.parse(sql, read="postgres")
        except Exception as e:
            return ValidationResult(False, sql, [f"unparseable SQL: {e}"])

        statements = [s for s in statements if s is not None]
        if len(statements) == 0:
            return ValidationResult(False, sql, ["empty query"])
        if len(statements) > 1:
            return ValidationResult(
                False, sql,
                [f"expected exactly 1 statement, found {len(statements)}"],
            )

        tree = statements[0]

        # ── 2. root must be a SELECT ─────────────────────────
        if not isinstance(tree, (exp.Select, exp.Union, exp.With)):
            return ValidationResult(
                False, sql,
                [f"root expression is {type(tree).__name__}, expected SELECT"],
            )

        # ── 3. no destructive node anywhere in the tree ──────
        for node_type in FORBIDDEN_NODES:
            found = list(tree.find_all(node_type))
            if found:
                return ValidationResult(
                    False, sql,
                    [f"forbidden statement type in query tree: {node_type.__name__}"],
                )

        # ── 4. table whitelist ───────────────────────────────
        cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
        referenced = self._referenced_tables(tree)
        unknown = {
            t for t in referenced
            if t not in self.allowed_tables and t not in cte_names
        }
        if unknown:
            reasons.append(f"unknown table(s): {sorted(unknown)}")

        # ── 5. column whitelist + denied columns ─────────────
        col_reasons = self._check_columns(tree, referenced, cte_names)
        reasons.extend(col_reasons)

        # ── 6. joins must carry an ON predicate ──────────────
        # This is what rejects CROSS JOIN. A cross join has no ON
        # clause by definition, so it fails here without needing a
        # rule that names it specifically.
        join_reasons, join_count = self._check_joins(tree)
        reasons.extend(join_reasons)
        if join_count > self.max_joins:
            reasons.append(f"{join_count} joins exceeds max_joins={self.max_joins}")

        # ── 7. subquery depth ────────────────────────────────
        depth = self._subquery_depth(tree)
        if depth > self.max_subquery_depth:
            reasons.append(
                f"subquery depth {depth} exceeds max={self.max_subquery_depth}"
            )

        # ── 8. function allowlist ────────────────────────────
        reasons.extend(self._check_functions(tree))

        # ── 9. time filter on large tables ───────────────────
        # An unbounded scan of the telemetry table is the realistic
        # way to hurt the database with a valid SELECT.
        reasons.extend(self._check_time_filter(tree, referenced))

        if reasons:
            return ValidationResult(False, sql, reasons, warnings)

        # ── 10. LIMIT injection ──────────────────────────────
        # Only on the outermost non-aggregate SELECT. Injecting into
        # a CTE or an aggregate silently changes the answer, which is
        # worse than being slow.
        rewritten, limit_note = self._ensure_limit(tree)
        if limit_note:
            warnings.append(limit_note)

        return ValidationResult(True, rewritten, [], warnings)

    # ── AST helpers ──────────────────────────────────────────

    def _referenced_tables(self, tree: exp.Expression) -> set[str]:
        return {
            t.name.lower() for t in tree.find_all(exp.Table) if t.name
        }

    def _alias_map(self, tree: exp.Expression) -> dict[str, str]:
        """alias -> real table name.

        Without this, every table-qualified check is bypassable by
        aliasing: `SELECT dc.embedding FROM document_chunks dc`
        reads as table "dc", which is on no list, so the denied
        column slips through. Join-key checking has the same
        problem in reverse — a legitimate join written with aliases
        looks undeclared.

        Aliases resolve to themselves too, so lookups are uniform.
        """
        mapping: dict[str, str] = {}
        for table in tree.find_all(exp.Table):
            real = (table.name or "").lower()
            if not real:
                continue
            mapping[real] = real
            alias = (table.alias or "").lower()
            if alias:
                mapping[alias] = real
        return mapping

    def _resolve(self, ref: str, aliases: dict[str, str]) -> str:
        return aliases.get(ref.lower(), ref.lower())

    def _check_columns(
        self, tree: exp.Expression, referenced: set[str], cte_names: set[str]
    ) -> list[str]:
        reasons: list[str] = []
        aliases = self._alias_map(tree)
        known = set()
        for t in referenced:
            known |= {f"{t}.{c}" for c in self.allowed_columns.get(t, set())}

        bare_columns: set[str] = set()
        for t in referenced:
            bare_columns |= self.allowed_columns.get(t, set())

        for col in tree.find_all(exp.Column):
            name = (col.name or "").lower()
            raw_table = (col.table or "").lower()
            # Resolve the alias before any check, or all of them are
            # bypassable by aliasing the table.
            table = self._resolve(raw_table, aliases) if raw_table else ""

            if not name or name == "*":
                continue

            # Denied columns are configured qualified
            # ("document_chunks.embedding"), but a query can
            # reference them bare when only one table is in scope.
            # Check both forms, or the whole rule is bypassable by
            # dropping the table prefix.
            if table:
                if f"{table}.{name}" in self.denied_columns:
                    reasons.append(f"denied column: {table}.{name}")
                    continue
            else:
                bare_denied = {d.split(".")[-1] for d in self.denied_columns}
                if name in bare_denied:
                    owning = {
                        d for d in self.denied_columns
                        if d.split(".")[-1] == name
                        and d.split(".")[0] in referenced
                    }
                    if owning:
                        reasons.append(f"denied column: {sorted(owning)[0]}")
                        continue

            # Skip CTE-qualified references; their shape is defined
            # inside the query, not by our schema.
            if raw_table and raw_table in cte_names:
                continue

            if table:
                if table in self.allowed_tables:
                    if name not in self.allowed_columns.get(table, set()):
                        reasons.append(f"unknown column: {table}.{name}")
            else:
                # Unqualified — accept if any referenced table has it.
                if bare_columns and name not in bare_columns:
                    reasons.append(f"unknown column: {name}")

        return reasons

    def _check_joins(self, tree: exp.Expression) -> tuple[list[str], int]:
        reasons: list[str] = []
        aliases = self._alias_map(tree)
        joins = list(tree.find_all(exp.Join))

        for join in joins:
            kind = (join.side or "") + (join.kind or "")
            on = join.args.get("on")
            using = join.args.get("using")

            if on is None and not using:
                reasons.append(
                    "join without an ON condition "
                    f"({kind.strip() or 'JOIN'}) — cross joins are not allowed"
                )
                continue

            if on is not None and not self._join_uses_declared_key(on, aliases):
                # A warning-level concern in practice; treated as a
                # rejection here because an undeclared join key is
                # usually the model inventing a relationship.
                reasons.append(
                    "join predicate does not reference a declared join key"
                )

        return reasons, len(joins)

    def _join_uses_declared_key(
        self, on: exp.Expression, aliases: dict[str, str]
    ) -> bool:
        if not self.join_keys:
            return True
        pairs: set[frozenset[str]] = set()
        for eq in on.find_all(exp.EQ):
            left, right = eq.this, eq.expression
            if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                lt = (f"{self._resolve(left.table or '', aliases)}"
                      f".{(left.name or '').lower()}")
                rt = (f"{self._resolve(right.table or '', aliases)}"
                      f".{(right.name or '').lower()}")
                pairs.add(frozenset({lt, rt}))
        return any(p in self.join_keys for p in pairs)

    def _subquery_depth(self, tree: exp.Expression) -> int:
        def depth(node: exp.Expression, current: int = 0) -> int:
            best = current
            for sub in node.find_all(exp.Subquery, exp.Select):
                if sub is node:
                    continue
                best = max(best, depth(sub, current + 1))
            return best
        return depth(tree)

    def _check_functions(self, tree: exp.Expression) -> list[str]:
        reasons: list[str] = []
        for fn in tree.find_all(exp.Anonymous, exp.Func):
            name = (
                fn.name if hasattr(fn, "name") and fn.name
                else type(fn).__name__
            ).lower()
            if not name:
                continue
            if name in ALWAYS_BLOCKED_FUNCTIONS:
                reasons.append(f"blocked function: {name}")
            elif isinstance(fn, exp.Anonymous) and name not in self.allowed_functions:
                reasons.append(f"function not on allowlist: {name}")
        return reasons

    def _check_time_filter(
        self, tree: exp.Expression, referenced: set[str]
    ) -> list[str]:
        reasons: list[str] = []
        for table in referenced & self.require_time_filter_on:
            time_cols = self._time_columns(table)
            if not time_cols:
                continue
            where_clauses = list(tree.find_all(exp.Where))
            found = False
            for where in where_clauses:
                for col in where.find_all(exp.Column):
                    if (col.name or "").lower() in time_cols:
                        found = True
                        break
            if not found:
                reasons.append(
                    f"query against {table} must filter on a time column "
                    f"({sorted(time_cols)}) — unbounded scans are rejected"
                )
        return reasons

    def _time_columns(self, table: str) -> set[str]:
        cols = self.schema.get("tables", {}).get(table, {}).get("columns", {})
        return {
            name for name, meta in cols.items()
            if str(meta.get("type", "")).lower().startswith(
                ("timestamp", "date", "timestamptz")
            )
        }

    def _ensure_limit(self, tree: exp.Expression) -> tuple[str, Optional[str]]:
        """Inject or clamp LIMIT on the outermost SELECT only.

        Skipped for aggregate queries: a LIMIT does not reduce the
        work an aggregate does (it still scans everything), and it
        can change the result. Cost control for those is the EXPLAIN
        gate, not a limit.
        """
        outer = tree
        if isinstance(tree, exp.With):
            outer = tree.this

        if self._is_aggregate(outer):
            return tree.sql(dialect="postgres"), None

        existing = outer.args.get("limit")
        if existing is not None:
            try:
                value = int(existing.expression.name)
            except Exception:
                return tree.sql(dialect="postgres"), None
            if value > self.max_limit:
                outer.set("limit", exp.Limit(expression=exp.Literal.number(self.max_limit)))
                return (
                    tree.sql(dialect="postgres"),
                    f"LIMIT clamped from {value} to {self.max_limit}",
                )
            return tree.sql(dialect="postgres"), None

        outer.set("limit", exp.Limit(expression=exp.Literal.number(self.default_limit)))
        return (
            tree.sql(dialect="postgres"),
            f"LIMIT {self.default_limit} injected",
        )

    def _is_aggregate(self, node: exp.Expression) -> bool:
        if list(node.find_all(exp.Group)):
            return True
        agg = (exp.Sum, exp.Avg, exp.Count, exp.Min, exp.Max)
        return any(list(node.find_all(a)) for a in agg)

    # ── layer 2: cost gate ───────────────────────────────────

    def explain_gate(self, conn, sql: str) -> ValidationResult:
        """Ask the planner what this will cost, before running it.

        This is the check that AST rules cannot replace. A query can
        reference only whitelisted tables, join correctly, and still
        plan a sequential scan over a hundred million rows. The
        planner already knows that; we just have to ask.
        """
        try:
            with conn.cursor() as cur:
                cur.execute(f"EXPLAIN (FORMAT JSON) {sql}")
                row = cur.fetchone()
        except Exception as e:
            return ValidationResult(False, sql, [f"EXPLAIN failed: {e}"])

        plan = row[0][0]["Plan"] if row else {}
        cost = float(plan.get("Total Cost", 0.0))
        rows = float(plan.get("Plan Rows", 0.0))

        reasons: list[str] = []
        if cost > self.explain_cost_budget:
            reasons.append(
                f"estimated cost {cost:,.0f} exceeds budget "
                f"{self.explain_cost_budget:,.0f} — narrow the time window "
                f"or add a filter"
            )
        if rows > self.explain_max_rows:
            reasons.append(
                f"estimated {rows:,.0f} rows exceeds max "
                f"{self.explain_max_rows:,.0f}"
            )

        return ValidationResult(
            ok=not reasons,
            sql=sql,
            reasons=reasons,
            explain_cost=cost,
            explain_rows=rows,
        )
