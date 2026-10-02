"""Which embedding model the corpus belongs to (revision 0011).

Search and ingest call `check` before using vectors. A mismatch between
the configured EMBEDDING_MODEL and the model the stored chunks were
embedded with is refused with a message naming the fix
(scripts/reembed.py), instead of returning results from two unrelated
vector spaces.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import text

from ops_copilot.settings import get_settings

RECHECK_SECONDS = 60
_seen: tuple[float, str | None] = (0.0, None)


class EmbeddingModelMismatchError(RuntimeError):
    pass


async def stored_model(conn: Any) -> str | None:
    """The model the corpus was embedded with; None for an empty corpus."""
    row = (await conn.execute(text("SELECT value FROM corpus_meta WHERE key = 'embedding_model'"))).first()
    return row[0] if row else None


async def check(conn: Any) -> None:
    """Raise EmbeddingModelMismatchError if the corpus and the configuration
    disagree. Re-read at most once a minute: a re-embed in another
    process is noticed without a restart."""
    global _seen
    checked_at, model = _seen
    if time.monotonic() - checked_at > RECHECK_SECONDS:
        model = await stored_model(conn)
        _seen = (time.monotonic(), model)
    configured = get_settings().embedding_model
    if model is not None and model != configured:
        raise EmbeddingModelMismatchError(
            f"the documents were embedded with {model} but EMBEDDING_MODEL is {configured}; "
            "run scripts/reembed.py (or set EMBEDDING_MODEL back) before searching or ingesting")


def forget() -> None:
    """Drop the cached answer (after a re-embed in this process)."""
    global _seen
    _seen = (0.0, None)
