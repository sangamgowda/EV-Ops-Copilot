"""Record which embedding model the document corpus was embedded with.

Vectors from two different models live in unrelated spaces: a query
embedded with one and compared against chunks embedded with another
returns confident nonsense, with no error anywhere. Switching
EMBEDDING_MODEL without re-embedding is exactly that mistake, so the
corpus now says which model made it, and search and ingest refuse to
run against a different one (rag/corpus.py). scripts/reembed.py is the
way to switch.

An existing corpus is recorded as what it was built with: 384-d
vectors here have only ever come from BAAI/bge-small-en-v1.5.
"""

from __future__ import annotations

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

SQL = """
CREATE TABLE IF NOT EXISTS corpus_meta (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO corpus_meta (key, value)
SELECT 'embedding_model', 'BAAI/bge-small-en-v1.5'
WHERE EXISTS (SELECT 1 FROM document_chunks)
  AND (SELECT format_type(atttypid, atttypmod) FROM pg_attribute
       WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding') = 'vector(384)'
ON CONFLICT (key) DO NOTHING;

GRANT SELECT ON corpus_meta TO copilot_readonly;
"""


def upgrade() -> None:
    op.get_bind().connection.driver_connection.execute(SQL)


def downgrade() -> None:
    raise NotImplementedError("forward-only: a downgrade on real data is a restore")
