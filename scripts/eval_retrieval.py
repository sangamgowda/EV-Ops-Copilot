"""Retrieval-only evaluation: no language model, runs in a minute.

    python scripts/eval_retrieval.py              # the configured models, on the live corpus
    python scripts/eval_retrieval.py --json out.json

Runs every question in golden/retrieval.yaml through the production
search path (hybrid search -> rerank, rag/retrieve.py) and reports:

  candidate recall   expected document among the hybrid candidates —
                     the embedding model's (and keyword search's) job
  hit@final_k        expected document in the final reranked list
  accepted           ... and scored above confidence_threshold
  false accepts      a question no document answers scored above it

by language, plus the threshold that best separates the two groups on
this set. That threshold is a suggestion to review, not a setting the
script changes: retrieval.confidence_threshold lives in app_config.yaml.

To compare models: run, switch EMBEDDING_MODEL / RERANKER_MODEL in .env,
run scripts/reembed.py, run this again.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ops_copilot.rag import rerank as rr  # noqa: E402
from ops_copilot.rag.retrieve import hybrid_search  # noqa: E402
from ops_copilot.settings import get_config, get_settings  # noqa: E402

SET = ROOT / "src" / "ops_copilot" / "evaluation" / "golden" / "retrieval.yaml"


async def run_one(item: dict[str, Any]) -> dict[str, Any]:
    cfg = get_config()["retrieval"]
    candidates = await hybrid_search(item["q"], item["domain"], None, cfg["candidate_k"])
    candidate_docs = [c["doc_id"] for c in candidates]
    shortlist = candidates[: cfg.get("rerank_candidates", len(candidates))]   # as rag/retrieve.py does
    ranked = await asyncio.to_thread(rr.rerank, item["q"], shortlist, cfg["final_k"])
    best = max((c["rerank_score"] for c in ranked), default=0.0)
    out = {**item, "best_score": round(best, 4), "top_doc": ranked[0]["doc_id"] if ranked else None}
    if "doc" in item:
        hits = [c["rerank_score"] for c in ranked if c["doc_id"] == item["doc"]]
        out.update(in_candidates=item["doc"] in candidate_docs, hit=bool(hits),
                   doc_score=round(max(hits), 4) if hits else None)
    return out


def summarise(rows: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    pos = [r for r in rows if "doc" in r]
    neg = [r for r in rows if "doc" not in r]
    by_lang: dict[str, dict[str, Any]] = {}
    for lang in sorted({r["lang"] for r in pos}):
        p = [r for r in pos if r["lang"] == lang]
        by_lang[lang] = {
            "n": len(p),
            "candidate_recall": sum(r["in_candidates"] for r in p) / len(p),
            "hit": sum(r["hit"] for r in p) / len(p),
            "accepted": sum(r["hit"] and r["doc_score"] >= threshold for r in p) / len(p),
        }

    def separation(t: float) -> float:
        accepted = sum(r["hit"] and r["doc_score"] >= t for r in pos)
        rejected = sum(r["best_score"] < t for r in neg)
        return (accepted + rejected) / (len(pos) + len(neg))

    grid = [round(0.05 * i, 2) for i in range(1, 20)]
    best_sep = max(separation(t) for t in grid)
    # Of the thresholds that separate best, the highest: when in doubt,
    # say "no documentation" rather than explain from a weak match.
    suggested = max(t for t in grid if separation(t) == best_sep)
    return {
        "embedding_model": get_settings().embedding_model,
        "reranker_model": get_settings().reranker_model,
        "threshold": threshold,
        "by_language": by_lang,
        "false_accept_rate": sum(r["best_score"] >= threshold for r in neg) / len(neg) if neg else None,
        "negative_best_scores": sorted(r["best_score"] for r in neg),
        "suggested_threshold": suggested,
        "separation_at_suggested": round(best_sep, 3),
        "separation_at_current": round(separation(threshold), 3),
    }


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", type=Path, help="also write the full results here")
    args = p.parse_args()

    data = yaml.safe_load(SET.read_text(encoding="utf-8"))
    items = data["positive"] + data["negative"]
    rows = [await run_one(i) for i in items]          # one at a time: the reranker is CPU-bound
    threshold = get_config()["retrieval"]["confidence_threshold"]
    summary = summarise(rows, threshold)

    print(f"embedding {summary['embedding_model']} · reranker {summary['reranker_model']} · "
          f"threshold {threshold}\n")
    print(f"{'lang':<5}{'n':>3}  {'candidates':>10}  {'hit@k':>6}  {'accepted':>8}")
    for lang, s in summary["by_language"].items():
        print(f"{lang:<5}{s['n']:>3}  {s['candidate_recall']:>10.0%}  {s['hit']:>6.0%}  {s['accepted']:>8.0%}")
    print(f"\nquestions no document answers: false accepts {summary['false_accept_rate']:.0%}, "
          f"best scores {summary['negative_best_scores']}")
    print(f"threshold separating the two best on this set: {summary['suggested_threshold']} "
          f"({summary['separation_at_suggested']:.0%} correct; at {threshold}: "
          f"{summary['separation_at_current']:.0%})")
    misses = [r for r in rows if "doc" in r and not r["hit"]]
    if misses:
        print("\nmissed: " + "; ".join(f"[{r['lang']}] {r['doc']} (got {r['top_doc']})" for r in misses))
    if args.json:
        args.json.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2, ensure_ascii=False),
                             encoding="utf-8")
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(asyncio.run(main()))
