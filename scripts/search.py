"""Run one retrieval by hand and show what comes back.

The full pipeline, exactly as the agent's document tool will run it:
hybrid search (keyword + vector, fused) -> domain filter + entity
boost -> rerank -> confidence threshold. An answer below the threshold
comes back as an explicit status, never as weak chunks.

Usage:
  python scripts/search.py "why does range drop in cold weather"
  python scripts/search.py "fleet discount" --domain business
  python scripts/search.py "range dropped" --vehicle VIN-1O42
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ops_copilot.db.engine import dispose_all  # noqa: E402
from ops_copilot.rag.entity_resolution import resolve_vehicle_id  # noqa: E402
from ops_copilot.rag.retrieve import retrieve  # noqa: E402


async def run(args: argparse.Namespace) -> None:
    try:
        entity = None
        if args.vehicle:
            r = await resolve_vehicle_id(args.vehicle)
            print(f"vehicle '{args.vehicle}': {r.status}"
                  + (f" -> {r.value}" if r.value else "")
                  + (f" | {r.note}" if r.note else ""))
            entity = r.value
        result = await retrieve(args.query, args.domain, entity)
    finally:
        await dispose_all()

    print(f"\nstatus: {result['status']}   best score: {result['best_score']}   "
          f"threshold: {result['threshold']}   candidates: {result['candidate_count']}   "
          f"{result['latency_ms']} ms")
    for c in result["chunks"]:
        body = c["content"].split("\n\n", 1)[-1].replace("\n", " ")
        print(f"\n  {c['rerank_score']:.3f}  {c['title']}  >  {c['section_path'] or '(top)'}  [{c['chunk_type']}]")
        print(f"         {body[:160]}{'...' if len(body) > 160 else ''}")
    if result["status"] != "ok":
        print("\n  (no chunks returned — this is the explicit empty result, not a failure)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("query")
    p.add_argument("--domain", default="diagnostic", choices=["diagnostic", "business"])
    p.add_argument("--vehicle", help="a vehicle id to resolve and boost, e.g. VIN-1042")
    args = p.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
