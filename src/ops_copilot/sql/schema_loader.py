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
Not needed now; injecting everything is correct at this scale and
retrieval would be solving a problem that does not exist yet.

TODO(build): implement load(), merge(), to_prompt_block().
"""

from __future__ import annotations
