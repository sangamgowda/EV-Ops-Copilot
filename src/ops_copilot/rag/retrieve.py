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

Dense half is pgvector (cosine, HNSW). Sparse half is Postgres full
text over the generated `tsv` column — rather than rank_bm25 in
memory — so the keyword index lives next to the rows it indexes and
nothing has to be rebuilt when a document is ingested.

The keyword query is OR-ed, not AND-ed. A natural-language question
almost never has every one of its words in one chunk; AND semantics
would make the sparse half return nothing on exactly the questions
it exists to help with. ts_rank_cd still rewards chunks matching
more of the terms.

Result status mirrors EvidenceStatus: "ok", "empty" (nothing in the
domain matched at all), "below_threshold" (matched, too weak to
use). Both non-ok cases are reported, never papered over.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from sqlalchemy import text

from ops_copilot.rag import rerank as rr
from ops_copilot.rag.embeddings import embed_query
from ops_copilot.settings import get_config

_SELECT = """
    c.chunk_id, c.doc_id, c.chunk_type, c.section_path, c.content,
    c.error_codes, d.title, d.doc_type, d.applies_to_models, d.effective_date
"""

_DENSE_SQL = text(f"""
    SELECT {_SELECT}, 1 - (c.embedding <=> CAST(:qvec AS vector)) AS score
      FROM document_chunks c
      JOIN documents d ON d.doc_id = c.doc_id
     WHERE d.domain = :domain AND c.embedding IS NOT NULL
     ORDER BY c.embedding <=> CAST(:qvec AS vector)
     LIMIT :k
""")

_SPARSE_SQL = text(f"""
    WITH q AS (
        SELECT replace(plainto_tsquery('english', :query)::text, '&', '|')::tsquery AS tsq
    )
    SELECT {_SELECT}, ts_rank_cd(c.tsv, q.tsq) AS score
      FROM document_chunks c
      JOIN documents d ON d.doc_id = c.doc_id
      CROSS JOIN q
     WHERE d.domain = :domain AND c.tsv @@ q.tsq
     ORDER BY score DESC
     LIMIT :k
""")

_MODEL_OF_SQL = text("SELECT model_code FROM vehicles WHERE vehicle_id = :id")


# ── pure steps ───────────────────────────────────────────────

def rrf_fuse(
    dense: list[dict[str, Any]],
    sparse: list[dict[str, Any]],
    rrf_k: int,
    vector_weight: float,
    keyword_weight: float,
) -> list[dict[str, Any]]:
    """Weighted reciprocal rank fusion.

    Ranks, not scores, are fused: cosine similarity and ts_rank_cd
    live on unrelated scales, and normalising one onto the other is
    a guess. Rank position is the one thing they share.
    """
    fused: dict[int, dict[str, Any]] = {}
    for weight, hits, label in ((vector_weight, dense, "dense"), (keyword_weight, sparse, "sparse")):
        for rank, hit in enumerate(hits, start=1):
            entry = fused.setdefault(hit["chunk_id"], {**hit, "hybrid_score": 0.0})
            entry.pop("score", None)
            entry[f"{label}_rank"] = rank
            entry["hybrid_score"] += weight / (rrf_k + rank)
    return sorted(fused.values(), key=lambda c: c["hybrid_score"], reverse=True)


def apply_boost(candidates: list[dict[str, Any]], entity_terms: set[str], boost: float) -> list[dict[str, Any]]:
    """Reorder so entity-relevant chunks rank higher. Never removes any.

    A chunk is relevant if it names the entity, or its document
    applies to the entity's model — the usual case, since service
    documents are written per model, not per VIN.
    """
    terms = {t.casefold() for t in entity_terms if t}
    for c in candidates:
        models = {m.casefold() for m in (c.get("applies_to_models") or [])}
        content = c["content"].casefold()
        hit = bool(terms & models) or any(t in content for t in terms)
        c["boost"] = boost if hit else 0.0
    return sorted(candidates, key=lambda c: c["hybrid_score"] * (1.0 + c["boost"]), reverse=True)


def apply_threshold(reranked: list[dict[str, Any]], threshold: float) -> tuple[str, list[dict[str, Any]]]:
    if not reranked:
        return "empty", []
    kept = [c for c in reranked if c["rerank_score"] >= threshold]
    return ("ok", kept) if kept else ("below_threshold", [])


# ── IO ───────────────────────────────────────────────────────

def _rows(result: Any) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in result]


async def _dense(query: str, domain: str, k: int) -> list[dict[str, Any]]:
    from ops_copilot.db.engine import readonly_engine

    qvec = await asyncio.to_thread(embed_query, query)
    literal = "[" + ",".join(f"{x:.7f}" for x in qvec) + "]"
    async with readonly_engine().connect() as conn:
        # With a WHERE on domain, HNSW filters after the index scan
        # and can return fewer than k rows. Iterative scan (pgvector
        # >= 0.8) keeps searching until k survive; on older versions
        # this is an unused placeholder setting and does nothing.
        await conn.execute(text("SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true)"))
        return _rows(await conn.execute(_DENSE_SQL, {"qvec": literal, "domain": domain, "k": k}))


async def _sparse(query: str, domain: str, k: int) -> list[dict[str, Any]]:
    from ops_copilot.db.engine import readonly_engine

    async with readonly_engine().connect() as conn:
        return _rows(await conn.execute(_SPARSE_SQL, {"query": query, "domain": domain, "k": k}))


async def _entity_terms(entity_id: str | None) -> set[str]:
    if not entity_id:
        return set()
    from ops_copilot.db.engine import readonly_engine

    async with readonly_engine().connect() as conn:
        model = (await conn.execute(_MODEL_OF_SQL, {"id": entity_id})).scalar()
    # entity_id may itself be a model code; both go in as terms.
    return {entity_id, model} if model else {entity_id}


async def hybrid_search(query: str, domain: str, entity_id: str | None, k: int) -> list[dict]:
    """Steps 2-3: fused, domain-filtered, entity-boosted candidates."""
    cfg = get_config()["retrieval"]
    dense, sparse, terms = await asyncio.gather(
        _dense(query, domain, k),
        _sparse(query, domain, k),
        _entity_terms(entity_id),
    )
    fused = rrf_fuse(dense, sparse, cfg["rrf_k"], cfg["vector_weight"], cfg["keyword_weight"])
    return apply_boost(fused, terms, cfg["entity_boost"])[:k]


async def retrieve(query: str, domain: str, entity_id: str | None = None) -> dict[str, Any]:
    """The full pipeline. Always returns a result, never raises on 'nothing found'."""
    cfg = get_config()["retrieval"]
    started = time.perf_counter()

    candidates = await hybrid_search(query, domain, entity_id, cfg["candidate_k"])
    reranked = await asyncio.to_thread(rr.rerank, query, candidates, cfg["final_k"])
    status, kept = apply_threshold(reranked, cfg["confidence_threshold"])

    return {
        "status": status,
        "chunks": [
            {
                "chunk_id": c["chunk_id"],
                "doc_id": c["doc_id"],
                "title": c["title"],
                "doc_type": c["doc_type"],
                "section_path": c["section_path"],
                "chunk_type": c["chunk_type"],
                "content": c["content"],
                "error_codes": c["error_codes"] or [],
                "effective_date": c["effective_date"].isoformat() if c["effective_date"] else None,
                "rerank_score": round(c["rerank_score"], 4),
                "boosted": c.get("boost", 0.0) > 0,
            }
            for c in kept
        ],
        # Reported even when nothing passed, so "weak match at 0.41"
        # and "no match at all" stay distinguishable downstream.
        "best_score": round(max(c["rerank_score"] for c in reranked), 4) if reranked else None,
        "threshold": cfg["confidence_threshold"],
        "candidate_count": len(candidates),
        "rerank_skipped": bool(reranked and reranked[0].get("rerank_skipped")),
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }
