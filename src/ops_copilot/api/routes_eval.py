"""/eval — run the test set, return a regression report.

Deliberately an endpoint and not part of any request path: the judge
runs here, offline, never live.

The endpoint exists now so the API surface is complete; the runner it
calls is built in the evaluation phase. Until then it answers 501 with
a plain explanation rather than pretending to evaluate.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ops_copilot.api.schemas import EvalRequest

router = APIRouter()


@router.post("/eval")
async def evaluate(req: EvalRequest) -> dict:
    raise HTTPException(501, "Evaluation is not built yet: the golden set and runner arrive "
                             "in the evaluation phase.")
