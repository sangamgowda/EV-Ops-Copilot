"""Retrieval pipeline tests — the pure steps, no database or model.

Hybrid fusion, the entity boost and the threshold are where the
retrieval guarantees actually live; the SQL around them only fetches.
"""

from __future__ import annotations

import pytest

from ops_copilot.rag import rerank as rr
from ops_copilot.rag.retrieve import apply_boost, apply_threshold, rrf_fuse


def hit(cid: int, content: str = "", models: list[str] | None = None) -> dict:
    return {"chunk_id": cid, "content": content or f"chunk {cid}",
            "applies_to_models": models or [], "score": 0.0}


class TestFusion:
    def test_agreement_beats_either_half_alone(self):
        dense = [hit(1), hit(2), hit(3)]
        sparse = [hit(4), hit(2), hit(5)]
        fused = rrf_fuse(dense, sparse, rrf_k=60, vector_weight=0.6, keyword_weight=0.4)
        assert fused[0]["chunk_id"] == 2
        assert fused[0]["dense_rank"] == 2 and fused[0]["sparse_rank"] == 2

    def test_keyword_only_hit_survives(self):
        """An exact code the embedder misses must still come through
        the keyword half — that is why hybrid exists."""
        dense = [hit(i) for i in range(1, 11)]
        sparse = [hit(99, "ERR_401 | BMS communication timeout")]
        fused = rrf_fuse(dense, sparse, 60, 0.6, 0.4)
        ids = [c["chunk_id"] for c in fused]
        assert 99 in ids
        assert "dense_rank" not in fused[ids.index(99)]

    def test_no_duplicates(self):
        fused = rrf_fuse([hit(1), hit(2)], [hit(2), hit(1)], 60, 0.6, 0.4)
        assert sorted(c["chunk_id"] for c in fused) == [1, 2]


class TestBoost:
    def _fused(self):
        return rrf_fuse(
            [hit(1, "general range advice"), hit(2, "overload bulletin", ["SC-F50"])],
            [], 60, 0.6, 0.4,
        )

    def test_boost_reorders_by_model(self):
        out = apply_boost(self._fused(), {"VIN-1042", "SC-F50"}, 0.15)
        assert [c["chunk_id"] for c in out] == [2, 1]

    def test_boost_never_filters(self):
        """A VIN appears in almost no documents. A filter would return
        nothing; a boost returns everything, reordered."""
        out = apply_boost(self._fused(), {"VIN-9999"}, 0.15)
        assert len(out) == 2
        assert all(c["boost"] == 0.0 for c in out)

    def test_boost_matches_entity_in_content(self):
        fused = rrf_fuse([hit(1), hit(2, "Seen on vin-1042 during trial")], [], 60, 0.6, 0.4)
        out = apply_boost(fused, {"VIN-1042"}, 0.15)
        assert out[0]["chunk_id"] == 2


class TestThreshold:
    def test_nothing_retrieved_is_empty(self):
        assert apply_threshold([], 0.55) == ("empty", [])

    def test_all_weak_is_below_threshold_not_weak_context(self):
        status, kept = apply_threshold([{"rerank_score": 0.41}, {"rerank_score": 0.2}], 0.55)
        assert (status, kept) == ("below_threshold", [])

    def test_only_strong_chunks_kept(self):
        status, kept = apply_threshold([{"rerank_score": 0.9}, {"rerank_score": 0.3}], 0.55)
        assert status == "ok" and kept == [{"rerank_score": 0.9}]


class TestRerank:
    @pytest.fixture
    def fake_scores(self, monkeypatch):
        calls: list[list[str]] = []

        def score(query, texts):
            calls.append(texts)
            return [0.5 if "weak" in t else 0.9 for t in texts]

        monkeypatch.setattr(rr, "score", score)
        return calls

    def test_skip_when_leader_is_decisive(self):
        cands = [{"hybrid_score": 0.02}, {"hybrid_score": 0.01}]
        assert rr.should_skip(cands, 0.25)
        cands = [{"hybrid_score": 0.0160}, {"hybrid_score": 0.0158}]
        assert not rr.should_skip(cands, 0.25)

    def test_skip_still_scores_the_leader(self, fake_scores):
        cands = [{"content": "a", "hybrid_score": 0.02}, {"content": "b", "hybrid_score": 0.005}]
        out = rr.rerank("q", cands, top_k=5)
        assert fake_scores == [["a"]]
        assert len(out) == 1 and out[0]["rerank_score"] == 0.9

    def test_boost_reorders_but_does_not_inflate_score(self, fake_scores):
        cands = [
            {"content": "strong", "hybrid_score": 0.0160, "boost": 0.0},
            {"content": "weak but boosted", "hybrid_score": 0.0158, "boost": 0.15},
        ]
        out = rr.rerank("q", cands, top_k=5)
        weak = next(c for c in out if c["content"].startswith("weak"))
        # 0.5 * 1.15 would clear 0.55; the stored score must not.
        assert weak["rerank_score"] == 0.5
        assert apply_threshold(out, 0.55)[1] == [out[0]]
