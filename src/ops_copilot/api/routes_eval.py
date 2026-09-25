"""/eval — run the test set, return a regression report.

Deliberately an endpoint and not part of any request path: the judge
runs here, offline, never live.

POST /eval runs cases through the same graph /chat uses and waits for
the report, so it is for small runs (`limit`). A full run takes most of
an hour and more than a day's free-tier allowance; that belongs to
`python scripts/run_eval.py`, which can stop and resume. GET
/eval/latest returns the most recent report either way.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from ops_copilot.api.schemas import EvalRequest
from ops_copilot.evaluation.runner import load_cases, run_eval, runs_dir
from ops_copilot.settings import get_config

router = APIRouter()


@router.post("/eval")
async def evaluate(req: EvalRequest) -> dict:
    cfg = get_config()["evaluation"]
    try:
        cases = load_cases(req.golden_path or cfg["golden_path"])
        if req.include_traps:
            cases += load_cases(cfg["trap_path"])
    except FileNotFoundError as exc:
        raise HTTPException(404, f"golden set not found: {exc.filename}") from exc
    if req.limit:
        cases = cases[: req.limit]
    run_id, report = await run_eval(cases, use_judge=req.judge)
    return {"run_id": run_id, **report}


@router.get("/eval/latest")
async def latest() -> dict:
    reports = sorted(runs_dir().glob("*/report.json")) if runs_dir().exists() else []
    if not reports:
        raise HTTPException(404, "no evaluation has been run yet")
    return json.loads(reports[-1].read_text(encoding="utf-8"))
