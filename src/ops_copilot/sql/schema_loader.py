"""Loads and merges the schema config.

The file has two halves maintained differently:

  GENERATED  structure, from information_schema. Overwritten on
             every run of scripts/generate_schema_config.py.
  CURATED    description, unit, expected_range, allowed_values.
             Hand-written, preserved across regeneration by merging
             on table.column.

Nobody maintains 200 tables by hand. The generator handles
structure; a human writes one description line per column that is
actually queried, which is far fewer than the column count suggests.

Scale note: at this size the whole config is injected into the Plan
prompt. Past ~30 tables you switch to retrieving the relevant
subset per query — embed each table description once, search it at
query time, inject the top matches plus anything joined by foreign
key. Same RAG pattern, pointed at the schema instead of documents.
"""

from __future__ import annotations

import copy
import functools
from typing import Any

from ops_copilot.settings import get_schema_config

# Keys a human writes. Everything else on a column is structural and
# belongs to the generator.
CURATED_COLUMN_KEYS = ("description", "unit", "expected_range", "allowed_values", "json_keys")
CURATED_TABLE_KEYS = ("description",)

# Tables that exist in the schema (the validator needs to know them)
# but that Plan should never write SQL against. Document chunks are
# searched by the RAG tool; offering them to SQL invites the model to
# do retrieval with LIKE.
HIDDEN_FROM_PLAN = {"document_chunks"}


def load() -> dict[str, Any]:
    return get_schema_config()


def merge(generated: dict[str, Any], curated: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Structure from `generated`, meaning from `curated`.

    Returns the merged config and a list of columns that have no
    description — printed by the generator so the gap is visible.
    Columns that vanished from the database are dropped with them;
    stale meaning is worse than none.
    """
    out = copy.deepcopy(generated)
    undocumented: list[str] = []
    old_tables = curated.get("tables", {})

    for tname, table in out.get("tables", {}).items():
        old = old_tables.get(tname, {})
        for key in CURATED_TABLE_KEYS:
            if key in old:
                table[key] = old[key]
        old_cols = old.get("columns", {})
        for cname, col in table.get("columns", {}).items():
            for key in CURATED_COLUMN_KEYS:
                if key in old_cols.get(cname, {}):
                    col[key] = old_cols[cname][key]
            if not col.get("description"):
                undocumented.append(f"{tname}.{cname}")

    # Policy lives with the curated half; the database cannot know it.
    for key in ("denied_columns", "join_keys", "version"):
        if key in curated:
            out[key] = curated[key]
    return out, undocumented


def _column_line(name: str, col: dict[str, Any]) -> str:
    parts = [f"    {name} ({col.get('type', '?')})"]
    if col.get("description"):
        parts.append(" — " + " ".join(str(col["description"]).split()))
    if col.get("unit"):
        parts.append(f" [unit: {col['unit']}]")
    if col.get("allowed_values"):
        parts.append(f" [values: {', '.join(map(str, col['allowed_values']))}]")
    line = "".join(parts)
    for key, meaning in (col.get("json_keys") or {}).items():
        line += f"\n      ->>'{key}': {meaning}"
    return line


def to_prompt_block(schema: dict[str, Any] | None = None) -> str:
    """The schema as the Plan prompt sees it: meaning, not just names."""
    schema = schema or load()
    denied = set(schema.get("denied_columns", []))
    lines: list[str] = []
    for tname, table in schema.get("tables", {}).items():
        if tname in HIDDEN_FROM_PLAN:
            continue
        desc = " ".join(str(table.get("description", "")).split())
        lines.append(f"{tname}: {desc}" if desc else tname)
        for cname, col in table.get("columns", {}).items():
            if f"{tname}.{cname}" not in denied:
                lines.append(_column_line(cname, col))
        lines.append("")
    keys = [f"  {a} = {b}" for a, b in schema.get("join_keys", [])]
    if keys:
        lines.append("Join keys (every JOIN must use one of these in its ON clause):")
        lines.extend(keys)
    return "\n".join(lines).strip()


@functools.lru_cache(maxsize=1)
def prompt_block() -> str:
    return to_prompt_block()
