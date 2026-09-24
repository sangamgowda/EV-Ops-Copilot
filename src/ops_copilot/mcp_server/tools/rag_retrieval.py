"""rag_retrieval_tool — written explanation from documents.

Fixed pipeline, same every call: entity resolution -> hybrid search ->
filter/boost -> rerank -> threshold. Nothing here decides anything; the
decision happened in Plan.

Returns chunks with rerank scores and source documents — or an
explicit EMPTY result when nothing clears the confidence threshold.
The empty result is the important case: it becomes evidence, which
is what lets the agent say "I have no documentation on this"
instead of inventing a mechanism.

Entity resolution runs here, before the search, because this process
is the one holding database access. A mistyped VIN from Plan is
resolved to the real vehicle before it is used as a boost; an
unresolvable one boosts nothing and the reason is returned — it never
becomes a filter that silently empties the result.
"""

from __future__ import annotations

from dataclasses import asdict

from ops_copilot.rag.entity_resolution import resolve_vehicle_id
from ops_copilot.rag.ingest import DOMAINS
from ops_copilot.rag.retrieve import retrieve
from ops_copilot.settings import get_schema_config


def _known_models() -> set[str]:
    column = get_schema_config()["tables"]["vehicles"]["columns"]["model_code"]
    return {m.lower() for m in column.get("allowed_values", [])}


async def rag_retrieval(query: str, domain: str, entity_id: str | None = None) -> dict:
    if domain not in DOMAINS:
        # Domain is the hard filter; an unknown one would silently
        # match nothing and look like "no documentation exists".
        return {"status": "failed", "error": f"domain must be one of {DOMAINS}, got {domain!r}",
                "query": query}
    if not query.strip():
        return {"status": "failed", "error": "empty query", "query": query}

    resolution = None
    boost_id = None
    if entity_id:
        # Model names ("Volt 1 Gen 2") are not vehicles; they boost as
        # given. Anything else is a vehicle id and is resolved first.
        # Judged against the known models, not an id format, so the
        # check survives a change of id scheme.
        if entity_id.strip().lower() not in _known_models():
            r = await resolve_vehicle_id(entity_id)
            resolution = asdict(r)
            boost_id = r.value if r.status in ("exact", "fuzzy") else None
        else:
            boost_id = entity_id

    result = await retrieve(query, domain, boost_id)
    return {"query": query, "domain": domain, "entity_id": entity_id,
            "entity_resolution": resolution, **result}


async def resolve_entity(vehicle_id: str) -> dict:
    """Check a vehicle id against real rows: exact / fuzzy / ambiguous /
    not_found, with a note meant to be shown to the user."""
    return asdict(await resolve_vehicle_id(vehicle_id))
