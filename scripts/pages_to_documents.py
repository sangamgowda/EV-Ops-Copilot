"""Turn rendered page text into documents ingestion can take.

    python scripts/pages_to_documents.py data/raw/pages data/raw/docs \\
        --sources data/sources.yaml --anonymize data/anonymize.local.yaml
    python scripts/ingest_documents.py data/raw/docs

Input is what ui/scripts/render-pages.mjs writes: one .txt per page,
URL on the first line, title on the second, then the text. For each
page this:

  1. drops site furniture — reading-time badges, "Read More" links,
     table-of-contents and FAQ question lists that repeat headings
  2. applies the find-and-replace map (brand and product names to
     neutral ones). Whole words, case-sensitive, longest first: a
     case-insensitive "OneS" would also rewrite "phones" and "ones".
  3. refuses to write a page that still contains a term listed under
     `must_not_remain`, so a missed name fails loudly
  4. writes Markdown with the front matter ingestion needs; domain,
     models and doc_type come from the page's entry in the sources file.
     The file name becomes the document id shown in citations, so an
     entry's `name` replaces the page slug (which can carry a brand).

Output belongs under data/raw/ (git-ignored). The text is the site's
content; it is loaded into the local database, never committed.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

FURNITURE = re.compile(
    r"^(\d+\s*min(\s*read)?|read more:.*|share|table of contents|faqs?|"
    r"frequently asked questions|home|blogs?)$",
    re.IGNORECASE,
)


def whole(term: str) -> re.Pattern[str]:
    """Match `term` as whole words: no letter may touch a word
    character at its start or end. A phrase that ends in punctuation
    needs no boundary there."""
    head = r"(?<!\w)" if term[:1].isalnum() else ""
    tail = r"(?!\w)" if term[-1:].isalnum() else ""
    return re.compile(head + re.escape(term) + tail)


def load_map(path: Path | None) -> tuple[list[tuple[re.Pattern[str], str]], list[str]]:
    if path is None:
        return [], []
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    guard = [str(t) for t in raw.pop("must_not_remain", [])]
    pairs = sorted(raw.items(), key=lambda kv: -len(str(kv[0])))
    return [(whole(str(k)), str(v)) for k, v in pairs], guard


def clean(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines()]
    body = [ln for ln in lines if ln and not FURNITURE.match(ln)]
    # A table of contents (and an FAQ question list) repeats headings
    # that appear again in the article. Keep each line only at its last
    # occurrence, which is the in-article copy.
    last = {ln: i for i, ln in enumerate(body)}
    return "\n\n".join(ln for i, ln in enumerate(body) if last[ln] == i)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("pages", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--sources", type=Path, required=True)
    p.add_argument("--anonymize", type=Path)
    args = p.parse_args()

    sources = {s["url"].rstrip("/"): s for s in yaml.safe_load(args.sources.read_text(encoding="utf-8"))}
    reps, guard = load_map(args.anonymize)
    args.out.mkdir(parents=True, exist_ok=True)

    written = failed = 0
    for page in sorted(args.pages.glob("*.txt")):
        url, title, text = [*page.read_text(encoding="utf-8").split("\n", 2), "", ""][:3]
        meta = sources.get(url.strip().rstrip("/"))
        if meta is None or meta.get("skip"):
            continue
        body = clean(text)
        for pattern, repl in reps:
            title, body = pattern.sub(repl, title), pattern.sub(repl, body)
        left = [t for t in guard if whole(t).search(title + body)]
        if left:
            print(f"refused  {page.stem}: still contains {left}")
            failed += 1
            continue
        models = ", ".join(meta.get("applies_to_models", []))
        front = (f"---\ntitle: {title.strip()!r}\ndoc_type: {meta.get('doc_type', 'kb_article')}\n"
                 f"domain: {meta['domain']}\napplies_to_models: [{models}]\n"
                 f"effective_date: {meta.get('effective_date', '2026-01-01')}\n---\n\n")
        name = meta.get("name", page.stem)
        (args.out / f"{name}.md").write_text(front + f"# {title.strip()}\n\n{body}\n", encoding="utf-8")
        print(f"written  {name}  ({len(body):,} chars)")
        written += 1
    print(f"\n{written} documents written, {failed} refused")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
