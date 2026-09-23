"""Lane B — document ingestion. Rows and JSON do not come through here.

There are TWO pipelines, not two stages of one.

  Lane A  rows and JSON. Synced/replicated from the source database
          as-is. No chunking, no embedding, nothing to process — a
          row is already usable the moment it lands. The sync IS
          the whole operation. See scripts/ and db/migrations/.

  Lane B  documents. This module. The fetch and the processing are
          the same act; there is no raw copy sitting in between.

Lane B, in order:

  1. read document
  2. content_hash -> already in `documents`? STOP. Nothing else runs.
  3. parse structure (headings, tables, lists)
  4. PROMOTE error-code tables into the error_codes table — real
     rows, not chunks. An exact code lookup should be a SQL match.
     This is the step that makes "ERR_401" reliable instead of
     approximately right.
  5. chunk the rest, structure-aware
  6. embed
  7. tag with domain, doc_type, applies_to_models, effective_date
  8. write chunks + record the hash

TODO(build): implement ingest_document() and ingest_directory().
"""

from __future__ import annotations

from pathlib import Path


def ingest_document(path: Path, domain: str, doc_type: str) -> dict:
    raise NotImplementedError("see module docstring")
