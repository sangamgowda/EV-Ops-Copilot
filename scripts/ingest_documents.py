"""Ingest a folder of documents (Lane B) into Postgres.

Idempotent: unchanged files are skipped by content hash, edited
files replace their previous version. Safe to run on a schedule —
a second run over the same folder reports 0 new documents and does
no chunking or embedding at all.

Usage:
  python scripts/ingest_documents.py                  # data/documents
  python scripts/ingest_documents.py path/to/dir --domain business
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ops_copilot.db.engine import dispose_all  # noqa: E402
from ops_copilot.rag.ingest import DOMAINS, ingest_directory  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    try:
        results = await ingest_directory(Path(args.directory), domain=args.domain)
    finally:
        await dispose_all()

    for r in results:
        line = f"{r['status']:<18} {r['doc_id']}"
        if r["status"] in ("ingested", "replaced"):
            line += f"  chunks={r['chunks']} codes={r['error_codes_promoted']}"
        if r.get("error"):
            line += f"  error={r['error']}"
        print(line)

    n = Counter(r["status"] for r in results)
    print(f"\n{n['ingested']} new documents, {n['replaced']} updated, "
          f"{n['skipped_duplicate']} unchanged, {n['failed']} failed")
    return 1 if n["failed"] else 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("directory", nargs="?", default=str(ROOT / "data" / "documents"))
    p.add_argument("--domain", choices=DOMAINS, help="override front matter (required for PDFs)")
    args = p.parse_args()

    if sys.platform == "win32":
        # psycopg's async driver cannot run on the Proactor loop.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
