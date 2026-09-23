"""Resolve entities BEFORE searching.

A hard metadata filter on a misspelled VIN returns zero chunks, and
zero chunks is dangerous: an empty context is where models start
inventing. So the VIN is checked against real rows first.

  exact match          use it
  one close match      use it, and SAY SO in the answer
                       ("assuming you meant VIN-9021")
  several close        ask which one
  nothing close        tell the user it does not exist — that is a
                       real answer, not a failure

The second protection is in retrieval, not here: entity id is a
ranking BOOST, not a hard filter. Most service documentation is
model-specific rather than VIN-specific, so a strict VIN filter
would discard exactly the documents worth retrieving.

TODO(build): implement with rapidfuzz against the vehicles table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass
class Resolution:
    status: Literal["exact", "fuzzy", "ambiguous", "not_found"]
    value: Optional[str] = None
    suggestions: list[str] = None
    note: Optional[str] = None


def resolve_vehicle_id(raw: str) -> Resolution:
    raise NotImplementedError("see module docstring")
