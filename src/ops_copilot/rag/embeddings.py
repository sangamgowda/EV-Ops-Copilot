"""Bi-encoder. Local, CPU, no API key.

BAAI/bge-small-en-v1.5 — 384 dimensions, ~130MB, runs comfortably
on CPU. Chosen over larger models deliberately: at this corpus size
the embedder is not the quality bottleneck, and keeping it small
means no GPU, no API cost, and a container that starts in seconds.

If retrieval quality ever does become the bottleneck, the upgrade
path is Qwen3-Embedding-0.6B — but only after an eval says so, and
switching means re-embedding the whole corpus.

Note: BGE models want a query prefix for retrieval
("Represent this sentence for searching relevant passages: ") but
NOT for documents. Getting this backwards quietly degrades recall.

These functions are synchronous and CPU-bound. Async callers wrap
them in asyncio.to_thread so the event loop keeps serving.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING

from ops_copilot.settings import get_config, get_settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


@functools.lru_cache(maxsize=1)
def _model() -> SentenceTransformer:
    # Imported here so that importing this module (tests, the API
    # process) does not load torch until an embedding is needed.
    from sentence_transformers import SentenceTransformer

    s = get_settings()
    model = SentenceTransformer(s.embedding_model, device="cpu")
    dim = model.get_sentence_embedding_dimension()
    if dim != s.embedding_dim:
        # The vector column is fixed-width; a mismatched model would
        # fail at insert time with a far less useful message.
        raise RuntimeError(
            f"{s.embedding_model} produces {dim}-d vectors but "
            f"EMBEDDING_DIM is {s.embedding_dim}; the schema expects the latter"
        )
    return model


def _encode(texts: list[str]) -> list[list[float]]:
    vectors = _model().encode(
        texts,
        batch_size=get_config()["embeddings"]["batch_size"],
        # Unit vectors: cosine distance in pgvector is then a plain
        # dot product, and scores are comparable across queries.
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return vectors.tolist()


def embed_documents(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    return _encode(texts)


@functools.lru_cache(maxsize=get_config()["embeddings"]["query_cache_size"])
def _embed_query_cached(text: str) -> tuple[float, ...]:
    return tuple(_encode([QUERY_PREFIX + text])[0])


def embed_query(text: str) -> list[float]:
    return list(_embed_query_cached(text.strip()))
