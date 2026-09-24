"""Deterministic SQL validation. No LLM anywhere in this file.

The model writes SQL. This decides whether it runs.

Design premise: the model is not a security boundary. It does not
matter how well-behaved generation looks, or that the schema config
gives it no reason to write a DROP. A poisoned document retrieved
earlier in the same turn can steer generation. So every query is
treated as hostile, regardless of where it came from.

Three layers, in order:

  1. AST checks (this module, sqlglot). Structural. Fast. Catches
     the whole class of "this query should not exist". Never regex:
     regex fails on comments, nested quotes and odd whitespace, and
     is not a security control.

  2. EXPLAIN cost gate (`explain_gate`). Catches the class AST rules
     cannot reason about — queries that are structurally perfect and
     ruinously expensive. The planner already knows; we ask it.

  3. The database itself: a role that can only read, cannot see the
     denied column, and dies at a statement timeout, reached through
     a small pool. If something gets past layers 1 and 2, it still
     cannot write and still cannot run for long.

Every column is resolved to the REAL table it reads before it is
checked, using sqlglot's scope analysis. Checking names as written
is the most common bug in hand-written validators: an alias
(`FROM document_chunks vehicles`), a `*`, or a CTE wrapper
(`WITH x AS (SELECT * FROM document_chunks) SELECT x.embedding ...`)
each walk a denied column straight past a name-based check.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

log = logging.getLogger(__name__)

# Node types that must never appear anywhere in the tree — not at
# the root, not inside a CTE, not inside a subquery. Into is
# SELECT ... INTO (creates a table); Lock is FOR UPDATE / FOR SHARE
# (takes row locks on the primary). Command is sqlglot's fallback
# for statements it does not model (DO, CALL, EXPLAIN ANALYZE).
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    t for t in (
        exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Drop, exp.Alter,
        exp.Create, exp.TruncateTable, exp.Grant, exp.Command, exp.Transaction,
        exp.Commit, exp.Rollback, exp.Set, exp.Into, exp.Lock,
        getattr(exp, "Copy", None), getattr(exp, "Use", None),
    ) if t is not None
)

# Functions that read files, sleep, or reach outside the database.
# Named separately from the allowlist so the rejection says why; the
# allowlist would refuse them anyway.
ALWAYS_BLOCKED_FUNCTIONS = {
    "pg_sleep", "pg_sleep_for", "pg_sleep_until", "pg_read_file",
    "pg_read_binary_file", "pg_ls_dir", "pg_stat_file", "lo_import", "lo_export",
    "lo_get", "dblink", "dblink_exec", "dblink_connect", "pg_terminate_backend",
    "pg_cancel_backend", "set_config", "current_setting", "pg_reload_conf",
    "query_to_xml", "pg_advisory_lock", "txid_current", "nextval", "setval", "copy",
}

# sqlglot models some SQL syntax as function nodes that render
# without a call: CASE, CURRENT_TIMESTAMP, `->>`, `^`. These are
# operators, not callable functions, and are allowed. Any OTHER node
# that renders without a name is unfamiliar and refused.
_SYNTAX_NODES = {
    "Case", "If", "CurrentTimestamp", "CurrentDate", "CurrentTime", "Pow",
    "JSONExtract", "JSONExtractScalar", "JSONBExtract", "JSONBExtractScalar",
}
_CALL_NAME = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(")

_TIME_TYPES = ("timestamp", "timestamptz", "date")


class ValidationError(Exception):
    """Raised when a query is rejected. The message is shown to the
    agent as tool feedback, so it must say what was wrong."""


@dataclass
class ValidationResult:
    ok: bool
    sql: str                      # possibly rewritten (LIMIT injected)
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    explain_cost: float | None = None
    explain_rows: float | None = None


def _conjuncts(node: exp.Expression | None) -> list[exp.Expression]:
    """Top-level AND terms. A predicate buried under an OR does not
    constrain anything: `key = key OR true` joins everything."""
    if node is None:
        return []
    node = node.unnest()
    if isinstance(node, exp.And):
        return [t for side in (node.left, node.right) for t in _conjuncts(side)]
    return [node]


def _is_constant(node: exp.Expression) -> bool:
    return node.find(exp.Column, exp.Subquery, exp.Select) is None


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
        self.allowed_functions: set[str] = {f.lower() for f in cfg["allowed_functions"]}

        tables = schema_config.get("tables", {})
        self.allowed_tables: set[str] = set(tables.keys())
        self.allowed_columns: dict[str, set[str]] = {
            t: set(meta.get("columns", {}).keys()) for t, meta in tables.items()
        }
        self.column_types: dict[str, dict[str, str]] = {
            t: {c: str(m.get("type", "text")).split("(")[0].strip()
                for c, m in meta.get("columns", {}).items()}
            for t, meta in tables.items()
        }
        self.denied_columns: set[str] = {c.lower() for c in schema_config.get("denied_columns", [])}
        self.join_keys: set[frozenset[str]] = {
            frozenset(pair) for pair in schema_config.get("join_keys", [])
        }

    # ── entry point ──────────────────────────────────────────

    def validate(self, sql: str) -> ValidationResult:
        """Run every AST check. Returns possibly-rewritten SQL.

        Does NOT run EXPLAIN — that needs a live connection and is
        done by `explain_gate` in the tool, right before execution.
        """
        # ── 1. exactly one statement ─────────────────────────
        # Catches stacked-statement injection before any other
        # analysis. "SELECT 1; DROP TABLE x" dies here, comments and
        # all, because the parser — not a regex — finds the boundary.
        try:
            statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
        except SqlglotError as e:
            return self._reject(sql, f"unparseable SQL: {str(e).splitlines()[0]}")
        if not statements:
            return self._reject(sql, "empty query")
        if len(statements) > 1:
            return self._reject(sql, f"expected exactly 1 statement, found {len(statements)}")
        tree = statements[0]

        # ── 2. root must be a SELECT ─────────────────────────
        if not isinstance(tree, exp.Select | exp.Union):
            return self._reject(sql, f"root expression is {type(tree).__name__}, expected SELECT")

        # ── 3. no destructive node anywhere in the tree ──────
        for node in tree.walk():
            if isinstance(node, FORBIDDEN_NODES):
                return self._reject(sql, f"forbidden construct in query tree: {type(node).__name__}")
            if isinstance(node, exp.With) and node.args.get("recursive"):
                # A recursive CTE is a loop the planner cannot cost.
                return self._reject(sql, "WITH RECURSIVE is not allowed")
            if isinstance(node, exp.Lateral):
                return self._reject(sql, "LATERAL is not allowed")
            if isinstance(node, exp.Join) and (node.method or "").upper() == "NATURAL":
                # Joins on whatever columns happen to share a name.
                return self._reject(sql, "NATURAL JOIN is not allowed — every join needs an ON condition")

        # ── 4. table whitelist ───────────────────────────────
        # Tables first: scope analysis below needs a schema for every
        # table it meets, and an unknown table is reason enough.
        reasons = self._check_tables(tree)
        # ── 5. function allowlist ────────────────────────────
        reasons += self._check_functions(tree)
        if reasons:
            return self._reject(sql, *reasons)

        # ── 6-9. checks that need every column resolved ──────
        try:
            resolved = qualify(tree.copy(), schema=self.column_types, dialect="postgres",
                               validate_qualify_columns=True, quote_identifiers=False)
            scopes = traverse_scope(resolved)
        except (OptimizeError, SqlglotError) as e:
            # Unknown or ambiguous columns land here: the query
            # names something the schema does not have.
            return self._reject(sql, f"column check failed: {str(e).splitlines()[0]}")

        reasons += self._check_columns(scopes)         # 6. columns, after alias resolution
        join_reasons, join_count = self._check_joins(scopes)
        reasons += join_reasons                         # 7. every join on a declared key
        if join_count > self.max_joins:
            reasons.append(f"{join_count} joins exceeds max_joins={self.max_joins}")
        depth = self._subquery_depth(tree)              # 8. nesting depth
        if depth > self.max_subquery_depth:
            reasons.append(f"subquery depth {depth} exceeds max={self.max_subquery_depth}")
        reasons += self._check_time_filter(scopes)      # 9. bounded scans on big tables
        if reasons:
            return self._reject(sql, *reasons)

        # ── 10. LIMIT ────────────────────────────────────────
        return self._ensure_limit(sql, tree)

    @staticmethod
    def _reject(sql: str, *reasons: str) -> ValidationResult:
        return ValidationResult(False, sql, list(dict.fromkeys(reasons)))

    # ── tables and functions (raw tree) ──────────────────────

    def _check_tables(self, tree: exp.Expression) -> list[str]:
        cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
        reasons: list[str] = []
        for table in tree.find_all(exp.Table):
            name = (table.name or "").lower()
            if not name:
                # FROM generate_series(...) and other table functions.
                reasons.append(f"table functions are not allowed: {table.sql(dialect='postgres')[:60]}")
                continue
            if table.catalog or (table.db and table.db.lower() != "public"):
                # secret.vehicles passes a bare-name check; only the
                # public schema holds the whitelisted tables.
                reasons.append(f"schema-qualified table not allowed: {table.sql(dialect='postgres')}")
            elif name not in self.allowed_tables and not (name in cte_names and not table.db):
                reasons.append(f"unknown table: {name}")
        return reasons

    def _function_name(self, fn: exp.Func) -> str | None:
        """The name Postgres will call, or None for syntax nodes.

        sqlglot's own names are dialect-neutral (TIMESTAMP_TRUNC for
        date_trunc), so the node is rendered as Postgres and the call
        name read back — the allowlist is written in Postgres terms.
        """
        if isinstance(fn, exp.Anonymous):
            return (fn.name or "").lower()
        m = _CALL_NAME.match(fn.sql(dialect="postgres"))
        return m.group(1).lower() if m else None

    def _check_functions(self, tree: exp.Expression) -> list[str]:
        reasons: list[str] = []
        for fn in tree.find_all(exp.Func):
            name = self._function_name(fn)
            if name is None:
                if type(fn).__name__ not in _SYNTAX_NODES:
                    reasons.append(f"expression not allowed: {type(fn).__name__}")
                continue
            if isinstance(fn.parent, exp.Dot):
                # pg_catalog.pg_sleep(...) — no qualified calls.
                reasons.append(f"schema-qualified function not allowed: {name}")
            elif name in ALWAYS_BLOCKED_FUNCTIONS:
                reasons.append(f"blocked function: {name}")
            elif name not in self.allowed_functions:
                reasons.append(f"function not on allowlist: {name}")
        return reasons

    # ── resolution (qualified tree) ──────────────────────────

    @staticmethod
    def _source(scope: Scope, alias: str) -> tuple[Scope, Any] | None:
        """Find what an alias refers to, walking out through enclosing
        scopes for correlated references."""
        s: Scope | None = scope
        while s is not None:
            if alias in s.sources:
                return s, s.sources[alias]
            s = s.parent
        return None

    def _real_column(self, scope: Scope, col: exp.Column, _depth: int = 0) -> tuple[str, str] | None:
        """(real_table, column) this column reads, followed through
        CTEs and derived tables. None when it is computed."""
        found = self._source(scope, col.table)
        if found is None or _depth > 10:
            return None
        _, source = found
        if isinstance(source, exp.Table):
            return source.name.lower(), col.name.lower()
        if isinstance(source, Scope):
            for proj in source.expression.selects:
                if proj.alias_or_name.lower() == col.name.lower():
                    inner = proj.unalias()
                    if isinstance(inner, exp.Column):
                        return self._real_column(source, inner, _depth + 1)
                    return None
        return None

    def _check_columns(self, scopes: list[Scope]) -> list[str]:
        reasons: list[str] = []
        for scope in scopes:
            for col in scope.columns:
                found = self._source(scope, col.table)
                if found is None or not isinstance(found[1], exp.Table):
                    # Reads a CTE or derived table: that scope's own
                    # columns are checked where they read real tables.
                    continue
                table, name = found[1].name.lower(), col.name.lower()
                if f"{table}.{name}" in self.denied_columns:
                    reasons.append(f"denied column: {table}.{name}")
                elif name not in self.allowed_columns.get(table, set()):
                    reasons.append(f"unknown column: {table}.{name}")
            # A star that survived qualification could not be expanded
            # and would hide what it reads.
            for proj in scope.expression.selects if isinstance(scope.expression, exp.Select) else []:
                if isinstance(proj, exp.Star) or (isinstance(proj, exp.Column) and proj.is_star):
                    reasons.append("SELECT * over an unresolved source is not allowed")
        return reasons

    def _check_joins(self, scopes: list[Scope]) -> tuple[list[str], int]:
        reasons: list[str] = []
        count = 0
        for scope in scopes:
            if not isinstance(scope.expression, exp.Select):
                continue
            for join in scope.expression.args.get("joins") or []:
                count += 1
                on = join.args.get("on")
                if on is None or (join.method or "").upper() == "NATURAL":
                    # This is what rejects CROSS JOIN, comma joins and
                    # NATURAL JOIN, without a rule naming any of them.
                    reasons.append("every join needs an ON condition — cross joins are not allowed")
                    continue
                if self.join_keys and not self._joins_on_declared_key(scope, on):
                    reasons.append(
                        "join condition must equate a declared join key "
                        "(as its own AND term, not under an OR)"
                    )
        return reasons, count

    def _joins_on_declared_key(self, scope: Scope, on: exp.Expression) -> bool:
        for term in _conjuncts(on):
            if isinstance(term, exp.EQ) and isinstance(term.left, exp.Column) \
                    and isinstance(term.right, exp.Column):
                left = self._real_column(scope, term.left)
                right = self._real_column(scope, term.right)
                if left and right and frozenset({".".join(left), ".".join(right)}) in self.join_keys:
                    return True
        return False

    def _subquery_depth(self, tree: exp.Expression) -> int:
        def depth(node: exp.Expression, current: int = 0) -> int:
            best = current
            for sub in node.find_all(exp.Subquery, exp.Select):
                if sub is node:
                    continue
                best = max(best, depth(sub, current + 1))
            return best
        return depth(tree)

    def _time_columns(self, table: str) -> set[str]:
        return {c for c, t in self.column_types.get(table, {}).items()
                if t.lower().startswith(_TIME_TYPES)}

    def _check_time_filter(self, scopes: list[Scope]) -> list[str]:
        """Every scope that reads a large table must bound its time
        column from below, as a top-level AND term, against a value
        computed from constants.

        `recorded_at IS NOT NULL`, `... OR true`, and a filter that
        sits in a different subquery all mention the column without
        bounding the scan. How wide the window is, is the EXPLAIN
        gate's call: a static rule cannot know what "too old" means
        for this data, the planner can.
        """
        reasons: list[str] = []
        for scope in scopes:
            if not isinstance(scope.expression, exp.Select):
                continue
            terms = _conjuncts(scope.expression.args.get("where") and scope.expression.args["where"].this)
            for join in scope.expression.args.get("joins") or []:
                terms += _conjuncts(join.args.get("on"))
            for alias, source in scope.selected_sources.items():
                node = source[1] if isinstance(source, tuple) else source
                if not isinstance(node, exp.Table) or node.name.lower() not in self.require_time_filter_on:
                    continue
                table = node.name.lower()
                time_cols = self._time_columns(table)
                if time_cols and not any(self._lower_bound(t, alias, time_cols) for t in terms):
                    reasons.append(
                        f"query against {table} must bound {sorted(time_cols)} from below "
                        "(e.g. recorded_at >= now() - interval '7 days') as its own AND term"
                    )
        return reasons

    @staticmethod
    def _lower_bound(term: exp.Expression, alias: str, time_cols: set[str]) -> bool:
        def is_time_col(node: exp.Expression) -> bool:
            return isinstance(node, exp.Column) and node.table == alias and node.name in time_cols

        if isinstance(term, exp.GT | exp.GTE):
            return is_time_col(term.left) and _is_constant(term.right)
        if isinstance(term, exp.LT | exp.LTE):
            return is_time_col(term.right) and _is_constant(term.left)
        if isinstance(term, exp.Between):
            return is_time_col(term.this) and _is_constant(term.args["low"])
        return False

    # ── LIMIT ────────────────────────────────────────────────

    def _ensure_limit(self, sql: str, tree: exp.Expression) -> ValidationResult:
        """Inject or clamp LIMIT on the outermost SELECT only.

        Skipped for aggregate queries: a LIMIT does not reduce the
        work an aggregate does (it still scans everything), and
        putting one inside a CTE or subquery silently changes the
        answer. Being wrong is worse than being slow. Cost control
        for those is the EXPLAIN gate.
        """
        warnings: list[str] = []
        existing = tree.args.get("limit")
        if existing is not None:
            value = self._literal_limit(existing)
            if value is None:
                # LIMIT NULL, LIMIT ALL, LIMIT (SELECT ...), FETCH FIRST:
                # each can mean "no limit". Only a plain integer is
                # something this check can reason about.
                return self._reject(sql, "LIMIT must be a plain integer, e.g. LIMIT 100")
            if value > self.max_limit:
                tree.set("limit", exp.Limit(expression=exp.Literal.number(self.max_limit)))
                warnings.append(f"LIMIT clamped from {value} to {self.max_limit}")
        elif not self._is_aggregate(tree):
            tree.set("limit", exp.Limit(expression=exp.Literal.number(self.default_limit)))
            warnings.append(f"LIMIT {self.default_limit} injected")
        # Comments are dropped from what runs: they carry no meaning
        # to Postgres and could carry text into traces and prompts.
        return ValidationResult(True, tree.sql(dialect="postgres", comments=False), [], warnings)

    @staticmethod
    def _literal_limit(node: exp.Expression) -> int | None:
        if not isinstance(node, exp.Limit):
            return None
        value = node.expression
        if isinstance(value, exp.Literal) and value.is_int:
            return int(value.name)
        return None

    def _is_aggregate(self, node: exp.Expression) -> bool:
        """Aggregate at THIS level — not in a subquery, not a window.

        A plain SELECT filtered by `value > (SELECT avg(...))` returns
        every matching row; treating it as an aggregate would ship it
        unlimited. `count(*) OVER ()` keeps every row too.
        """
        if isinstance(node, exp.Union):
            return self._is_aggregate(node.left) and self._is_aggregate(node.right)
        if not isinstance(node, exp.Select):
            return False
        if node.args.get("group") or node.args.get("having"):
            return True
        return any(agg.find_ancestor(exp.Window, exp.Subquery) is None
                   for proj in node.selects for agg in proj.find_all(exp.AggFunc))

    # ── layer 2: cost gate ───────────────────────────────────

    def explain_gate(self, conn, sql: str) -> ValidationResult:
        """Ask the planner what this will cost, before running it.

        This is the check that AST rules cannot replace. A query can
        reference only whitelisted tables, join correctly, bound its
        time column, and still plan a sequential scan over years of
        telemetry. The planner already knows that; we just have to ask.
        """
        try:
            with conn.cursor() as cur:
                cur.execute(f"EXPLAIN (FORMAT JSON) {sql}")
                row = cur.fetchone()
        except Exception as e:
            return ValidationResult(False, sql, [f"EXPLAIN failed: {str(e).strip().splitlines()[0]}"])

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
