"""Resolve entities BEFORE searching.

A hard metadata filter on a misspelled VIN returns zero chunks, and
zero chunks is dangerous: an empty context is where models start
inventing. So the VIN is checked against real rows first.

  exact match          use it
  one close match      use it, and SAY SO in the answer
                       ("assuming you meant V-021")
  several close        ask which one
  nothing close        tell the user it does not exist — that is a
                       real answer, not a failure

The second protection is in retrieval, not here: entity id is a
ranking BOOST, not a hard filter. Most service documentation is
model-specific rather than VIN-specific, so a strict VIN filter
would discard exactly the documents worth retrieving.

Split in two so the decision is testable without a database:
  resolve_against(raw, known_ids)   pure; all the rules live here
  resolve_vehicle_id(raw)           fetches candidates, then the above

"Exact" is judged after normalisation — case, spaces and separators
are formatting, not a different vehicle. "v 42" is V-042.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from rapidfuzz import fuzz
from sqlalchemy import text

from ops_copilot.settings import get_config


@dataclass
class Resolution:
    status: Literal["exact", "fuzzy", "ambiguous", "not_found"]
    value: str | None = None
    suggestions: list[str] = field(default_factory=list)
    note: str | None = None


def _norm(s: str) -> str:
    # Leading zeros are padding, not identity: "V42", "v 42" and
    # "V-042" are the same vehicle.
    return re.sub(r"(?<!\d)0+(?=\d)", "", re.sub(r"[^A-Z0-9]", "", s.upper()))


def _digits(s: str) -> str:
    return re.sub(r"(?<!\d)0+(?=\d)", "", re.sub(r"\D", "", s))


def resolve_against(raw: str, known_ids: list[str], cfg: dict[str, Any] | None = None) -> Resolution:
    cfg = cfg or get_config()["entity_resolution"]
    query = raw.strip()
    q = _norm(query)
    if not q:
        return Resolution("not_found", note="No vehicle id was given.")

    exact = [k for k in known_ids if _norm(k) == q]
    # A bare number ("1042") is how people actually type ids; treat
    # it as exact when it maps onto exactly one vehicle.
    if not exact and q.isdigit():
        exact = [k for k in known_ids if _digits(k) == q]
    if len(exact) == 1:
        return Resolution("exact", value=exact[0])
    if len(exact) > 1:
        options = sorted(exact)[: cfg["max_suggestions"]]
        return Resolution(
            "ambiguous", suggestions=options,
            note=f"'{query}' matches several vehicles: {', '.join(options)}. Which one?",
        )

    scored = sorted(
        ((fuzz.ratio(q, _norm(k)), k) for k in known_ids),
        key=lambda t: (-t[0], t[1]),
    )
    strong = [k for s, k in scored if s >= cfg["auto_accept_score"]]
    close = [k for s, k in scored if s >= cfg["disambiguate_score"]]

    if len(strong) == 1:
        return Resolution(
            "fuzzy", value=strong[0], suggestions=[strong[0]],
            note=f"No vehicle '{query}' exists; assuming you meant {strong[0]}.",
        )
    if close:
        # Several strong matches is still ambiguous: in a dense id
        # space one mistyped digit sits next to many real vehicles,
        # and guessing between them is inventing a fact.
        options = close[: cfg["max_suggestions"]]
        return Resolution(
            "ambiguous", suggestions=options,
            note=f"No vehicle '{query}' exists. Did you mean {' or '.join(options)}?",
        )
    return Resolution("not_found", note=f"No vehicle matching '{query}' exists.")


# Each arm is index-backed (idx_vehicles_id_trgm serves both % and
# ILIKE), so this stays cheap on a large fleet.
#
# The ORDER BY is load-bearing. In a fleet of any size more than :lim
# ids clear the loose trigram prefilter, and an unordered LIMIT keeps an
# arbitrary subset — which can drop the exact match itself, turning
# "V-042" into "ambiguous" with the right answer missing.
_CANDIDATES_SQL = text("""
    SELECT vehicle_id FROM (
        SELECT vehicle_id FROM vehicles WHERE vehicle_id ILIKE :raw
        UNION
        SELECT vehicle_id FROM vehicles WHERE vehicle_id % :raw
        UNION
        SELECT vehicle_id FROM vehicles
         WHERE :digits <> '' AND vehicle_id ILIKE '%' || :digits || '%'
    ) c
    ORDER BY (vehicle_id ILIKE :raw) DESC, similarity(vehicle_id, :raw) DESC, vehicle_id
    LIMIT :lim
""")


async def resolve_vehicle_id(raw: str) -> Resolution:
    from ops_copilot.db.engine import readonly_engine

    cfg = get_config()["entity_resolution"]
    stripped = raw.strip()
    # Any digits typed pull candidates in: "V42" shares too few
    # trigrams with "V-042" to clear the prefilter on its own.
    digits = _digits(stripped)
    async with readonly_engine().connect() as conn:
        await conn.execute(
            text("SELECT set_config('pg_trgm.similarity_threshold', :t, true)"),
            {"t": str(cfg["trigram_prefilter"])},
        )
        rows = await conn.execute(
            _CANDIDATES_SQL,
            {"raw": stripped, "digits": digits, "lim": cfg["max_candidates"]},
        )
        known = [r[0] for r in rows]
    return resolve_against(stripped, known, cfg)
