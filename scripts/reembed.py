"""Re-embed every document chunk with the configured embedding model.

    EMBEDDING_MODEL=BAAI/bge-m3 EMBEDDING_DIM=1024 python scripts/reembed.py

The way to switch embedding models. In one transaction:
  1. resize document_chunks.embedding to EMBEDDING_DIM if it differs
     (dropping and rebuilding the HNSW index around it),
  2. embed every chunk's stored text with EMBEDDING_MODEL,
  3. record the model in corpus_meta, which search and ingest check.
If anything fails, nothing changes: the old vectors and model stay.

Chunk text is stored, so no document is re-read or re-chunked, and
chunk ids (and so the feedback and trace references to them) survive.
Afterwards, restart the tool server with the same EMBEDDING_MODEL and
check retrieval with scripts/eval_retrieval.py.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import create_engine, text  # noqa: E402

from ops_copilot.db.migrate import database_url  # noqa: E402
from ops_copilot.rag.embeddings import embed_documents  # noqa: E402
from ops_copilot.settings import get_config, get_settings  # noqa: E402

INDEX = "idx_chunks_embedding"


def column_type(conn) -> str:
    return conn.execute(text(
        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
        "WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding'")).scalar()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    args = p.parse_args()

    s = get_settings()
    engine = create_engine(database_url())
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT chunk_id, content FROM document_chunks ORDER BY chunk_id")).all()
        current = conn.execute(text("SELECT value FROM corpus_meta WHERE key = 'embedding_model'")).scalar()
        col = column_type(conn)
    target = f"vector({s.embedding_dim})"
    print(f"{len(rows)} chunks: {current or 'unrecorded'} [{col}] -> {s.embedding_model} [{target}]")
    if not args.yes and input("re-embed now? [y/N] ").strip().lower() != "y":
        return 1

    started = time.perf_counter()
    batch = get_config()["embeddings"]["batch_size"]
    vectors: list[list[float]] = []
    for i in range(0, len(rows), batch):
        vectors += embed_documents([r.content for r in rows[i:i + batch]])
        print(f"  embedded {min(i + batch, len(rows))}/{len(rows)}", end="\r", flush=True)
    print()
    if vectors and len(vectors[0]) != s.embedding_dim:
        sys.exit(f"{s.embedding_model} makes {len(vectors[0])}-d vectors, EMBEDDING_DIM is {s.embedding_dim}")

    with engine.begin() as conn:
        if col != target:
            conn.execute(text(f"DROP INDEX IF EXISTS {INDEX}"))
            # Old vectors cannot be cast to a new width; they are replaced below.
            conn.execute(text(f"ALTER TABLE document_chunks ALTER COLUMN embedding TYPE {target} "
                              f"USING NULL::{target}"))
        conn.execute(
            text("UPDATE document_chunks SET embedding = CAST(:v AS vector) WHERE chunk_id = :id"),
            [{"id": r.chunk_id, "v": "[" + ",".join(f"{x:.7f}" for x in v) + "]"}
             for r, v in zip(rows, vectors, strict=True)])
        if col != target:
            conn.execute(text(f"CREATE INDEX {INDEX} ON document_chunks USING hnsw (embedding vector_cosine_ops)"))
        conn.execute(text("""
            INSERT INTO corpus_meta (key, value, updated_at) VALUES ('embedding_model', :m, now())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """), {"m": s.embedding_model})
    engine.dispose()
    print(f"done in {time.perf_counter() - started:.0f}s; restart the tool server with "
          f"EMBEDDING_MODEL={s.embedding_model} EMBEDDING_DIM={s.embedding_dim}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
