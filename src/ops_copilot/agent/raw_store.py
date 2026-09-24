"""Side store for full tool payloads.

Evidence carries a three-line summary into prompts; the full rows go
here, keyed by raw_ref. Nothing on the request path reads them back —
they exist for the trace, for debugging, and for evaluation.

Best effort by design: if the write fails, the turn continues with
raw_ref=None. Losing a debugging copy must never cost a user their
answer.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import text

log = logging.getLogger(__name__)


async def put(turn_id: str, payload: dict[str, Any]) -> str | None:
    from ops_copilot.db.engine import owner_engine

    ref = f"raw_{uuid.uuid4().hex[:16]}"
    try:
        async with owner_engine().begin() as conn:
            await conn.execute(
                text("INSERT INTO evidence_raw (raw_ref, turn_id, payload) "
                     "VALUES (:r, :t, CAST(:p AS jsonb))"),
                {"r": ref, "t": turn_id, "p": json.dumps(payload, default=str)},
            )
    except Exception as exc:
        log.warning("evidence_raw write failed (%s); continuing without raw_ref", exc)
        return None
    return ref
