"""Hybrid retrieval: BM25 + vector, fused, filtered, reranked.

Order matters and is fixed — this pipeline runs the same way every
time rag_retrieval_tool is called. Nothing decides anything at
runtime; the decision already happened in Plan.

  1. entity resolution (upstream, in the router/plan path)
  2. hybrid search — keyword and vector in parallel, fused with RRF
     Keyword alone misses paraphrases; vector alone misses exact
     IDs and codes. Neither is sufficient.
  3. filter and boost — domain is a HARD filter; entity id is a
     boost. See entity_resolution for why.
  4. rerank 50 -> 5 with the cross-encoder
  5. threshold — below retrieval.confidence_threshold, return an
     EMPTY result rather than weak context. Returning weak context
     is worse than returning none: weak context is what confident
     nonsense gets built on.

TODO(build): implement. pgvector for dense, Postgres full-text or
rank_bm25 for sparse, RRF to fuse.
"""

from __future__ import annotations


def hybrid_search(query: str, domain: str, entity_id: str | None, k: int) -> list[dict]:
    raise NotImplementedError("see module docstring")
