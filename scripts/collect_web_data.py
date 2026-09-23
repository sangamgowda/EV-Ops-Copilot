"""Collect public web pages about EVs into a local raw dataset.

Give it a list of URLs (spec sheets, reviews, sales reports, owner
forums). For each page it:

  1. checks robots.txt and skips pages the site does not allow bots
     to fetch
  2. downloads the page politely (one request every --delay seconds)
  3. extracts the title, readable text and every HTML table
  4. pulls out numeric facts it recognises: battery kWh, range km,
     top speed, motor kW, torque, weight, price and units sold
  5. applies an optional find-and-replace file, e.g. to swap brand
     names for neutral model codes
  6. writes everything to data/raw/pages.jsonl, and optionally one
     markdown file per page that POST /ingest can take directly

Usage:
  python scripts/collect_web_data.py --sources data/sources.yaml
  python scripts/collect_web_data.py --url https://example.org/a --url https://example.org/b
  python scripts/collect_web_data.py --sources data/sources.yaml \\
      --anonymize data/anonymize.local.yaml --to-markdown data/raw/docs

Sources file format (YAML):
  - url: https://example.org/some-page
    tags: [specs]
    domain: diagnostic        # or business; used for --to-markdown

Sites whose robots.txt blocks crawlers, or whose terms forbid
scraping, are skipped. Use their official export or API instead.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
USER_AGENT = "EVOpsCopilotCollector/0.1 (research; respects robots.txt)"

# Numeric facts worth pulling out of free text. Each pattern captures
# one number; the unit tells which quantity it is.
FACT_PATTERNS: dict[str, str] = {
    "battery_kwh": r"(\d+(?:\.\d+)?)\s?kWh",
    "range_km": r"(\d{2,3})\s?(?:km|kms|kilometres|kilometers)\b(?:\s(?:of\s)?(?:range|IDC|certified|claimed|per charge|/charge))",
    "top_speed_kmph": r"(\d{2,3})\s?(?:km/h|kmph|kph)",
    "motor_kw": r"(\d+(?:\.\d+)?)\s?kW\b",
    "torque_nm": r"(\d{2,3})\s?Nm\b",
    "weight_kg": r"(\d{2,3})\s?kg\b",
    "price_inr": r"(?:Rs\.?|₹|INR)\s?([\d,]{5,9})",
    "units_sold": r"([\d,]{3,7})\s?units",
}


@dataclass
class Page:
    url: str
    fetched_at: str
    status: str                         # ok | skipped_robots | error
    http_status: Optional[int] = None
    title: str = ""
    text: str = ""
    tables: list[list[list[str]]] = field(default_factory=list)
    facts: dict[str, list[str]] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    domain: str = "diagnostic"
    content_hash: str = ""
    error: Optional[str] = None


# ── politeness ───────────────────────────────────────────────


class Robots:
    """robots.txt per host, fetched once and cached."""

    def __init__(self, client: httpx.Client):
        self.client = client
        self.cache: dict[str, Optional[RobotFileParser]] = {}

    def allowed(self, url: str) -> bool:
        parts = urlparse(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self.cache:
            rp = RobotFileParser()
            try:
                r = self.client.get(f"{host}/robots.txt", timeout=10)
                if r.status_code >= 400:
                    self.cache[host] = None          # no robots.txt: allowed
                else:
                    rp.parse(r.text.splitlines())
                    self.cache[host] = rp
            except httpx.HTTPError:
                self.cache[host] = None
        rp = self.cache[host]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)


# ── extraction ───────────────────────────────────────────────


def extract(html: str) -> tuple[str, str, list[list[list[str]]]]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "form", "aside"]):
        tag.decompose()

    title = soup.title.get_text(strip=True) if soup.title else ""

    tables = []
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append(rows)
        table.decompose()

    main = soup.find("main") or soup.find("article") or soup.body or soup
    lines = [ln.strip() for ln in main.get_text("\n").splitlines()]
    text = "\n".join(ln for ln in lines if len(ln) > 1)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return title, text, tables


def find_facts(text: str, tables: list[list[list[str]]]) -> dict[str, list[str]]:
    haystack = text + "\n" + "\n".join(" | ".join(r) for t in tables for r in t)
    facts: dict[str, list[str]] = {}
    for name, pattern in FACT_PATTERNS.items():
        found = [m.replace(",", "") for m in re.findall(pattern, haystack, flags=re.IGNORECASE)]
        if found:
            facts[name] = sorted(set(found), key=found.index)[:20]
    return facts


def load_replacements(path: Optional[Path]) -> list[tuple[re.Pattern[str], str]]:
    if not path:
        return []
    mapping = yaml.safe_load(path.read_text()) or {}
    # Longest first so "Brand Model X" is replaced before "Brand".
    return [
        (re.compile(re.escape(k), re.IGNORECASE), v)
        for k, v in sorted(mapping.items(), key=lambda kv: -len(kv[0]))
    ]


def apply_replacements(s: str, reps: list[tuple[re.Pattern[str], str]]) -> str:
    for pattern, new in reps:
        s = pattern.sub(new, s)
    return s


def anonymize(page: Page, reps: list[tuple[re.Pattern[str], str]]) -> None:
    if not reps:
        return
    page.title = apply_replacements(page.title, reps)
    page.text = apply_replacements(page.text, reps)
    page.tables = [[[apply_replacements(c, reps) for c in row] for row in t] for t in page.tables]


# ── output ───────────────────────────────────────────────────


def to_markdown(page: Page) -> str:
    title = page.title.replace('"', "'") or "Untitled"
    parts = [
        "---",
        f'title: "{title}"',
        "doc_type: kb_article",
        f"domain: {page.domain}",
        "applies_to_models: []",
        f"effective_date: {page.fetched_at[:10]}",
        "---",
        "",
        f"# {page.title or 'Untitled'}",
        "",
        page.text,
    ]
    for i, table in enumerate(page.tables, 1):
        width = max(len(r) for r in table)
        rows = [r + [""] * (width - len(r)) for r in table]
        parts += ["", f"## Table {i}", "",
                  "| " + " | ".join(rows[0]) + " |",
                  "|" + "---|" * width]
        parts += ["| " + " | ".join(c.replace("|", "/") for c in r) + " |" for r in rows[1:]]
    return "\n".join(parts) + "\n"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "page"


# ── main ─────────────────────────────────────────────────────


def load_sources(args: argparse.Namespace) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    if args.sources:
        sources += yaml.safe_load(args.sources.read_text()) or []
    sources += [{"url": u} for u in args.url or []]
    seen, unique = set(), []
    for s in sources:
        if s["url"] not in seen:
            seen.add(s["url"])
            unique.append(s)
    return unique


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--sources", type=Path, help="YAML list of {url, tags, domain}")
    p.add_argument("--url", action="append", help="a URL to fetch; repeatable")
    p.add_argument("--out", type=Path, default=RAW_DIR / "pages.jsonl")
    p.add_argument("--anonymize", type=Path, help="YAML map of text -> replacement")
    p.add_argument("--to-markdown", type=Path, help="also write one .md per page here")
    p.add_argument("--delay", type=float, default=2.0, help="seconds between requests")
    p.add_argument("--timeout", type=float, default=20.0)
    args = p.parse_args()

    sources = load_sources(args)
    if not sources:
        sys.exit("nothing to fetch: pass --sources or --url")

    reps = load_replacements(args.anonymize)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    html_dir = args.out.parent / "html"
    html_dir.mkdir(exist_ok=True)
    if args.to_markdown:
        args.to_markdown.mkdir(parents=True, exist_ok=True)

    counts = {"ok": 0, "skipped_robots": 0, "error": 0}
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en"}
    with httpx.Client(headers=headers, follow_redirects=True, timeout=args.timeout) as client, \
            open(args.out, "a", encoding="utf-8") as out:
        robots = Robots(client)
        for i, src in enumerate(sources):
            url = src["url"]
            page = Page(
                url=url,
                fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                status="ok",
                tags=src.get("tags", []),
                domain=src.get("domain", "diagnostic"),
            )
            if not robots.allowed(url):
                page.status = "skipped_robots"
            else:
                if i:
                    time.sleep(args.delay)
                try:
                    r = client.get(url)
                    page.http_status = r.status_code
                    r.raise_for_status()
                    digest = hashlib.sha1(url.encode()).hexdigest()[:16]
                    (html_dir / f"{digest}.html").write_text(r.text, encoding="utf-8")
                    page.title, page.text, page.tables = extract(r.text)
                    anonymize(page, reps)
                    page.facts = find_facts(page.text, page.tables)
                    page.content_hash = hashlib.sha256(page.text.encode()).hexdigest()
                    if args.to_markdown:
                        name = f"{slug(page.title)}-{digest[:6]}.md"
                        (args.to_markdown / name).write_text(to_markdown(page), encoding="utf-8")
                except httpx.HTTPError as e:
                    page.status, page.error = "error", str(e)[:300]

            counts[page.status] += 1
            out.write(json.dumps(asdict(page), ensure_ascii=False) + "\n")
            facts = ", ".join(f"{k}={v[0]}" for k, v in list(page.facts.items())[:4])
            print(f"[{page.status:<14}] {url}  {facts}", flush=True)

    print(f"\n{counts['ok']} fetched, {counts['skipped_robots']} skipped by robots.txt, "
          f"{counts['error']} failed -> {args.out}")


if __name__ == "__main__":
    main()
