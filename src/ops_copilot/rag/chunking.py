"""Structure-aware chunking.

One strategy across all content is wrong, and tables are why.
Recursive character splitting shreds a table across boundaries, so
an error-code table becomes a set of fragments that answer nothing.

Rules:

  tables      never split. One table = one chunk, with its caption
              and nearest heading prepended so it reads standalone.

  lookup      ALSO emit one chunk per row, headers repeated:
  tables      "ERR_401 | BMS | comms timeout | Action: reseat..."
              An exact code then lands on a small precise chunk,
              which is where BM25 is strongest.

  prose       recursive split with overlap, but never across a
              heading boundary — a chunk spanning two sections
              answers neither well.

  all chunks  get a one-line context header prepended before
              embedding (document title + section path). Small
              ingestion-time cost, meaningful recall gain.

The stronger move for error codes is upstream of this file
entirely: promote them out of RAG into real Postgres rows. See
rag/ingest.py. Semantic search is the wrong tool for exact-key
lookup.

TODO(build): implement parse_structure(), chunk_document().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ChunkType = Literal["prose", "table", "table_row", "list"]


@dataclass
class Chunk:
    content: str
    chunk_type: ChunkType
    section_path: str = ""
    error_codes: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def chunk_document(text: str, title: str, doc_type: str) -> list[Chunk]:
    raise NotImplementedError("see module docstring")
