"""Bi-encoder. Local, CPU, no API key.

BAAI/bge-m3 — 1024 dimensions, ~2.3GB, multilingual. It replaced
bge-small-en-v1.5 (384-d, English) so that questions in Hindi, Kannada
and Tamil find English documents. Measured with
scripts/eval_retrieval.py: on its own the embedding swap changed
nothing, because the English reranker scored every non-English pair
near zero; with the multilingual reranker (rag/rerank.py) Hindi went
from 1 of 7 accepted to 6 of 7 and English from 9 of 11 to 11 of 11.

Note: English BGE models want a query prefix for retrieval
("Represent this sentence for searching relevant passages: ") but
NOT for documents; bge-m3 wants none. Getting this backwards quietly
degrades recall, so the prefix is per model (embeddings.query_prefixes).

Changing the model means re-embedding the corpus (scripts/reembed.py);
search refuses to mix the two (rag/corpus.py).

These functions are synchronous and CPU-bound. Async callers wrap
them in asyncio.to_thread so the event loop keeps serving.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING

from ops_copilot.settings import get_config, get_settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

def query_prefix() -> str:
    """The retrieval instruction the configured model expects on queries
    (never on documents). English BGE models want one; bge-m3 wants none."""
    return get_config()["embeddings"]["query_prefixes"].get(get_settings().embedding_model, "")


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
    return tuple(_encode([query_prefix() + text])[0])


def embed_query(text: str) -> list[float]:
    return list(_embed_query_cached(text.strip()))
