"""Cross-encoder reranking.

BAAI/bge-reranker-base. Scores (query, chunk) pairs jointly, which
is far more accurate than comparing two independent embeddings —
and far slower, which is exactly why it only ever runs on a
shortlist.

Pull 50 candidates with hybrid search (cheap), rerank to 5
(expensive). Candidate depth costs almost nothing; rerank cost
scales with list length and sequence length.

Latency in proportion: ~200ms here against 2-4s for a full lap
dominated by LLM calls. Two controls keep it low:
truncate candidates before scoring, and skip reranking entirely when
the top hybrid hit leads the runner-up by rerank_skip_margin.

TODO(build): implement score(), rerank(), should_skip().
"""

from __future__ import annotations


def rerank(query: str, candidates: list[dict], top_k: int) -> list[dict]:
    raise NotImplementedError("see module docstring")
