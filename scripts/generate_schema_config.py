"""Regenerate the GENERATED half of config/schema_config.yaml.

Reads information_schema — tables, columns, types, nullability,
foreign keys — and emits the structural skeleton. Merges the
existing CURATED half (descriptions, units, ranges) back in by
table.column so hand-written meaning survives.

New columns appear with an empty description and are printed as a
warning, so the gap is visible rather than silent.

Two accelerants worth knowing about:
  - Postgres COMMENT ON COLUMN can hold descriptions in the
    database itself, making it the source of truth
  - an LLM can draft descriptions from column names plus sample
    values for a human to review. Drafting is cheap; review is the
    part that matters.

Usage:  python scripts/generate_schema_config.py [--dry-run]

TODO(build): implement.
"""

from __future__ import annotations
