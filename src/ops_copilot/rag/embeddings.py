"""Bi-encoder. Local, CPU, no API key.

BAAI/bge-small-en-v1.5 — 384 dimensions, ~130MB, runs comfortably
on CPU. Chosen over larger models deliberately: at this corpus size
the embedder is not the quality bottleneck, and keeping it small
means no GPU, no API cost, and a container that starts in seconds.

If retrieval quality ever does become the bottleneck, the upgrade
path is Qwen3-Embedding-0.6B — but only after an eval says so, and
switching means re-embedding the whole corpus.

One gotcha: BGE models want a query prefix for retrieval
("Represent this sentence for searching relevant passages: ") but
NOT for documents. Getting this backwards quietly degrades recall.

TODO(build): implement with sentence-transformers, batched, cached.
"""

from __future__ import annotations

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def embed_documents(texts: list[str]) -> list[list[float]]:
    raise NotImplementedError("see module docstring")


def embed_query(text: str) -> list[float]:
    raise NotImplementedError("see module docstring")
