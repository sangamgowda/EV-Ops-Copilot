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

"Skip" still scores the leader alone. confidence_threshold is
defined on the cross-encoder's scale, and a hybrid RRF score cannot
stand in for it — so the decisive leader is checked, the rest of the
shortlist is not.

Scores are sigmoid-normalised to 0-1 so confidence_threshold means
the same thing regardless of the model's raw logit range.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any

from ops_copilot.settings import get_config, get_settings

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder


@functools.lru_cache(maxsize=1)
def _model() -> CrossEncoder:
    import torch
    from sentence_transformers import CrossEncoder

    return CrossEncoder(
        get_settings().reranker_model,
        max_length=get_config()["retrieval"]["rerank_max_length"],
        device="cpu",
        default_activation_function=torch.nn.Sigmoid(),
    )


def score(query: str, texts: list[str]) -> list[float]:
    """Scores in the same order as `texts`.

    Pairs are scored shortest-first in small batches. A batch is padded
    to its longest member, so one whole-table chunk in a batch of 32
    makes every short chunk cost as much as it does; measured on CPU,
    sorting plus batches of 8 cut 43 candidates from ~14s to ~4s with
    identical scores (padding is masked, so it never changes a score).
    """
    if not texts:
        return []
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    raw = _model().predict(
        [(query, texts[i]) for i in order],
        batch_size=get_config()["retrieval"]["rerank_batch_size"],
        show_progress_bar=False,
    )
    scores = [0.0] * len(texts)
    for i, s in zip(order, raw, strict=True):
        scores[i] = float(s)
    return scores


def should_skip(candidates: list[dict[str, Any]], margin: float) -> bool:
    """True when the hybrid leader is decisively ahead.

    RRF scores are tiny absolute numbers (~1/60), so the lead is
    measured relative to the leader rather than as a raw gap.
    """
    if len(candidates) < 2:
        return len(candidates) == 1
    top, second = candidates[0]["hybrid_score"], candidates[1]["hybrid_score"]
    if top <= 0:
        return False
    return bool((top - second) / top >= margin)


def rerank(query: str, candidates: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    """Attach `rerank_score` and return the best top_k, best first.

    Candidates must be in hybrid order. The entity boost (if any,
    in `boost`) reorders but is never folded into rerank_score —
    the threshold check downstream reads the unboosted score.
    """
    if not candidates:
        return []
    cfg = get_config()["retrieval"]

    if should_skip(candidates, cfg["rerank_skip_margin"]):
        shortlist = candidates[:1]
        skipped = True
    else:
        shortlist = candidates
        skipped = False

    for c, s in zip(shortlist, score(query, [c["content"] for c in shortlist]), strict=False):
        c["rerank_score"] = s
        c["rerank_skipped"] = skipped

    ordered = sorted(
        shortlist,
        key=lambda c: c["rerank_score"] * (1.0 + c.get("boost", 0.0)),
        reverse=True,
    )
    return ordered[:top_k]
