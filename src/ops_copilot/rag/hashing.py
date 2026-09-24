"""Content hashing — what makes re-ingestion idempotent.

Normalise, hash, look up. If the hash is already in `documents`,
skip the document entirely: no chunking, no embedding, no writes.
A daily re-sync of the same folder costs one SELECT per file.

Normalisation matters more than the hash function. Without it,
trailing whitespace or a line-ending change produces a different
digest and the same document gets ingested twice.

Case is NOT folded: "ERR_401" and "err_401" are different content,
and an edit that only changes case should still re-ingest.
"""

from __future__ import annotations

import hashlib
import re


def normalise(text: str) -> str:
    """Normalise line endings, collapse runs of spaces and blank lines, strip."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def content_hash(text: str) -> str:
    """SHA-256 of normalised content. Same content -> same digest."""
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()
