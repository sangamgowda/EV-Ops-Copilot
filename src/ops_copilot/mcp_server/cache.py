"""Result cache for the tool server.

Two kinds of call repeat and do not change between ingests:
  - lookups of reference tables (error codes, baselines, physical
    ranges): "what does ERR_601 mean" is the same answer all day;
  - document searches: the same question, domain and vehicle retrieve
    the same chunks until the corpus changes. A search costs an
    embedding, a hybrid query and a cross-encoder pass over 50
    candidates — most of a second on CPU.

Telemetry and sales queries are never cached: their answer moves with
every new reading.

Invalidation without a message bus: every key includes the corpus
version (document count + latest ingest time), re-read at most every
`version_check_seconds`. An ingest through /ingest changes it, so a
stale search is served for at most that long. A TTL bounds everything
else (e.g. baselines re-seeded under the server).

Only successful results are cached; a failure is retried next time.
Per process and in memory: with several tool-server replicas each
keeps its own, which costs hit rate, not correctness.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

import sqlglot
from sqlglot import exp

from ops_copilot.settings import get_config

log = logging.getLogger(__name__)

CACHEABLE_STATUSES = {"ok", "empty", "below_threshold"}


class TTLCache:
    def __init__(self, max_entries: int, ttl_s: float) -> None:
        self.max_entries, self.ttl_s = max_entries, ttl_s
        self._data: OrderedDict[Any, tuple[float, Any]] = OrderedDict()
        self.hits = self.misses = 0

    def get(self, key: Any) -> Any | None:
        item = self._data.get(key)
        if item is None or item[0] < time.monotonic():
            self._data.pop(key, None)
            self.misses += 1
            return None
        self._data.move_to_end(key)
        self.hits += 1
        return item[1]

    def put(self, key: Any, value: Any) -> None:
        self._data[key] = (time.monotonic() + self.ttl_s, value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()


def _cfg() -> dict[str, Any]:
    return get_config()["cache"]


_cache: TTLCache | None = None
_version: tuple[float, Any] = (0.0, None)


def cache() -> TTLCache:
    global _cache
    if _cache is None:
        _cache = TTLCache(_cfg()["max_entries"], _cfg()["ttl_seconds"])
    return _cache


def _read_corpus_version() -> Any:
    from ops_copilot.db.engine import readonly_sql_pool

    with readonly_sql_pool().connection() as conn:
        return conn.execute("SELECT count(*), max(ingested_at) FROM documents").fetchone()


async def corpus_version() -> Any:
    """Changes whenever a document is added, replaced or removed."""
    global _version
    checked_at, value = _version
    if time.monotonic() - checked_at > _cfg()["version_check_seconds"]:
        value = await asyncio.to_thread(_read_corpus_version)
        _version = (time.monotonic(), value)
    return value


def sql_cache_key(sql: str) -> str | None:
    """A canonical form of `sql` if it reads only reference tables, else None."""
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except sqlglot.errors.SqlglotError:
        return None
    tables = {t.name.lower() for t in tree.find_all(exp.Table)}
    if not tables or not tables <= set(_cfg()["sql_cacheable_tables"]):
        return None
    # Same query, different spacing or keyword case -> same key.
    return tree.sql(dialect="postgres", normalize=True, comments=False)


def search_cache_key(query: str, domain: str, entity_id: str | None) -> tuple[str, str, str]:
    return " ".join(query.lower().split()), domain, (entity_id or "").strip().upper()


async def cached(kind: str, key: Any, compute: Callable[[], Awaitable[dict]]) -> dict:
    """Serve `compute()` from the cache when possible. The result says
    whether it was cached, so the API can count hits."""
    if not _cfg()["enabled"] or key is None:
        return await compute()
    try:
        version = await corpus_version()
    except Exception:
        # No version, no safe key: the cache steps aside, the call does not fail.
        log.warning("cache: corpus version unavailable; not caching", exc_info=True)
        return await compute()
    full_key = (kind, version, key)
    hit = cache().get(full_key)
    if hit is not None:
        return {**hit, "cached": True}
    result = await compute()
    if result.get("status") in CACHEABLE_STATUSES:
        cache().put(full_key, result)
    return {**result, "cached": False}
