"""Runs the golden set through the real pipeline, end to end.

Not a mock. `run_turn` is the same function that serves /chat — same
graph, same MCP tool server, same database — so what is scored is what
users get.

Each case is scored twice:
  code   every check `deterministic.run_checks` can make: routing,
         tools, arguments, iterations, numbers, citations, forbidden
         claims, abstention. Free and never flaky.
  judge  completeness / correctness / hedging against the reference,
         by a model from a different family (judge.py).

Results are written one line per case as they finish, into
evaluation_runs/<run_id>/results.jsonl. A run can therefore stop and
resume: the free tier's daily token allowance runs out partway through
a full run, and a case cut short by the provider's rate limit says
nothing about the system. Those cases are marked `infra` and re-run on
resume instead of being scored as failures.

A pass rate with nothing to compare to is not a regression test, so
every report is diffed against the previous run: cases that passed
before and fail now are listed as regressions, and the CLI exits
non-zero on any.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import subprocess
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ops_copilot.evaluation import judge as judging
from ops_copilot.evaluation.deterministic import (
    contains,
    outcome_from_state,
    run_checks,
    says_it_cannot_tell,
)
from ops_copilot.settings import get_config

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
HANDWRITTEN = ("adversarial", "promoted")


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    p = p if p.is_absolute() else ROOT / p
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def answer_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


def _models() -> dict[str, str]:
    from ops_copilot.settings import get_settings

    st = get_settings()
    return {"cheap": st.llm_model_cheap, "strong": st.llm_model_strong, "judge": st.llm_model_judge}


# ── rate limits are not system failures ──────────────────────

class _RateLimitWatch(logging.Handler):
    """Notices the provider refusing requests while a case runs.

    The graph survives a refused call (a node falls back), so the turn
    still "completes" — with a worse answer that says nothing about the
    system. This tells the runner not to score it."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.per_minute = False      # a request was refused and retried
        self.rate_failed = False     # a step gave up on a refusal and fell back
        self.per_day = False
        self.seen: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if record.exc_info and record.exc_info[1] is not None:
            text += " " + str(record.exc_info[1])
        if "tokens per day" in text or "(TPD)" in text:
            self.per_day = True
            self.seen.append(text[:300])
        elif any(m in text for m in ("RateLimitError", "Rate limit reached", "Error code: 429",
                                     "rate_limit_exceeded", "Request too large")):
            # The exception's text, not its class name, is what gets logged:
            # "Error code: 429 - {... 'code': 'rate_limit_exceeded'}".
            self.rate_failed = True
            self.seen.append(text[:200])
        elif "429 Too Many Requests" in text:
            # Exact provider wording: a bare "429" also matches ids and hashes.
            self.per_minute = True
            self.seen.append(text[:200])


# ── one case ─────────────────────────────────────────────────

async def run_case(case: dict[str, Any], run_id: str, *, use_judge: bool, judge_threshold: int) -> dict[str, Any]:
    from ops_copilot.agent.turn import run_turn

    watch = _RateLimitWatch()
    root = logging.getLogger()
    root.addHandler(watch)
    # The client's "429 ... retrying" messages are INFO. Under a default
    # WARNING root level they are never created, and a case slowed by the
    # provider's per-minute limit gets scored as the system timing out.
    noisy = {name: logging.getLogger(name).level for name in ("openai", "httpx")}
    for name in noisy:
        logging.getLogger(name).setLevel(logging.INFO)
    started = time.perf_counter()
    state: dict[str, Any]
    try:
        state = dict(await run_turn(case["question"], f"eval-{run_id}", turn_id=uuid.uuid4().hex,
                                    record=False, timeout_s=get_config()["evaluation"]["turn_timeout_s"]))
    except Exception as exc:     # a crash IS a system failure; score it
        log.exception("case %s crashed", case["id"])
        state = {"answer": "", "stop_reason": f"crashed: {type(exc).__name__}: {exc}"}
    finally:
        root.removeHandler(watch)
        for name, level in noisy.items():
            logging.getLogger(name).setLevel(level)
    latency = round(time.perf_counter() - started, 1)

    o = outcome_from_state(state)
    checks = run_checks(o, case["expected"])
    code_pass = all(c.passed for c in checks)
    result: dict[str, Any] = {
        "id": case["id"], "source": case["source"], "tags": case.get("tags", []),
        "question": case["question"], "answer": o.answer, "answer_hash": answer_hash(o.answer),
        "turn_id": state.get("turn_id"),
        "outcome": {k: v for k, v in asdict(o).items() if k != "answer"},
        "checks": [asdict(c) for c in checks], "code_pass": code_pass,
        "latency_s": latency, "status": "ok", "throttled": False,
        "prompt_versions": state.get("prompt_versions", {}),
    }
    if case["source"] == "trap":
        result["abstained"] = says_it_cannot_tell(o)
        result["ungrounded_truth"] = any(contains(o.answer, t) for t in case["known_answer"])

    result["throttled"] = watch.per_minute
    tool_statuses = o.tool_evidence_statuses
    if watch.per_day:
        result["status"] = "budget"      # nothing more will run today
        result["rate_limit_evidence"] = watch.seen[-2:]
    elif tool_statuses and all(st == "failed" for st in tool_statuses):
        result["status"] = "infra"       # every tool call failed: the stack, not the agent
    elif watch.rate_failed or (watch.per_minute and o.stop_reason == "timeout"):
        # A step gave up on a refusal, or throttling ate the whole time
        # allowance: the provider decided this case, not the agent.
        result["status"] = "infra"
        result["rate_limit_evidence"] = watch.seen[:3]

    if use_judge and result["status"] == "ok":
        try:
            result["judge"] = await judging.score(case, o.answer)
            result["judge_pass"] = judging.passes(result["judge"], judge_threshold)
        except Exception as exc:
            log.warning("judge failed on %s: %s", case["id"], exc)
            result["judge"] = None
    result["pass"] = code_pass and result.get("judge_pass", True) is not False
    return result


# ── a run ────────────────────────────────────────────────────

def runs_dir() -> Path:
    d = Path(get_config()["evaluation"]["runs_dir"])
    return d if d.is_absolute() else ROOT / d


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


async def preflight() -> str | None:
    """One tool call, no model: can this process reach the tools and
    the database? Returns a reason if not.

    Without it, an unreachable tool server looks like an agent that
    refuses every question — and gets scored as one."""
    from ops_copilot.mcp_client.client import get_client

    try:
        tagged = await get_client().call_tool("resolve_entity_tool", {"vehicle_id": "V-001"}, "eval-preflight")
        status = tagged["result"].get("status")
    except Exception as exc:
        return f"tool server unreachable: {type(exc).__name__}: {str(exc)[:200]}"
    if status not in ("exact", "fuzzy", "ambiguous", "not_found"):
        return f"tool server answered but the database lookup failed: {tagged['result']}"
    return None


async def run_eval(cases: list[dict[str, Any]], *, run_id: str | None = None, use_judge: bool = True,
                   pause_s: float | None = None,
                   on_result: Callable[[dict[str, Any]], None] | None = None) -> tuple[str, dict[str, Any]]:
    """Run (or resume) `cases`. Returns the run id and its report."""
    cfg = get_config()["evaluation"]
    pause = cfg["pause_between_cases_s"] if pause_s is None else pause_s
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = runs_dir() / run_id
    folder.mkdir(parents=True, exist_ok=True)
    results_path = folder / "results.jsonl"

    meta_file = folder / "meta.json"
    if meta_file.exists():
        started_on = json.loads(meta_file.read_text(encoding="utf-8")).get("models")
        if started_on and started_on != _models():
            # Finishing a run on other models would mix two systems in
            # one report. Start a new run instead.
            raise RuntimeError(f"run {run_id} was started on {started_on}; the current models are "
                               f"{_models()}. Start a new run, or switch the models back to resume.")
    done = {r["id"]: r for r in _read(results_path) if r["status"] == "ok"}
    todo = [c for c in cases if c["id"] not in done]
    meta_path = folder / "meta.json"
    if not meta_path.exists():
        meta_path.write_text(json.dumps({
            "run_id": run_id, "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "git_commit": _git_commit(), "case_ids": [c["id"] for c in cases],
            "judge_version": judging.judge_version() if use_judge else None,
            # A pass-rate change between runs on different models is the
            # models, not the code; the report says so.
            "models": _models(),
        }, indent=2) + "\n", encoding="utf-8")

    stopped_for_budget = False
    for i, case in enumerate(todo):
        if i:
            await asyncio.sleep(pause)
        result = await run_case(case, run_id, use_judge=use_judge, judge_threshold=cfg["judge_pass_score"])
        with results_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
        if on_result:
            on_result(result)
        if result["status"] == "budget":
            stopped_for_budget = True
            break

    latest = {r["id"]: r for r in _read(results_path)}      # last write per case wins
    report = build_report(run_id, cases, list(latest.values()), stopped_for_budget)
    (folder / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                        encoding="utf-8")
    (folder / "report.md").write_text(render_markdown(report), encoding="utf-8")
    return run_id, report


# ── the report ───────────────────────────────────────────────

def _rate(rows: list[dict[str, Any]], key: str = "pass") -> float | None:
    return None if not rows else round(100 * sum(bool(r.get(key)) for r in rows) / len(rows), 1)


def _by_cluster(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        for tag in r.get("tags", []):
            if tag.startswith("cluster:"):
                out[tag].append(r)
    return out


def previous_run(run_id: str) -> str | None:
    older = sorted(p.name for p in runs_dir().iterdir()
                   if p.is_dir() and p.name < run_id and (p / "report.json").exists()) \
        if runs_dir().exists() else []
    return older[-1] if older else None


def diff_runs(current: list[dict[str, Any]], previous: list[dict[str, Any]]) -> dict[str, Any]:
    prev = {r["id"]: r for r in previous if r["status"] == "ok"}
    regressions, fixed = [], []
    for r in current:
        p = prev.get(r["id"])
        if r["status"] != "ok" or p is None:
            continue
        if p["pass"] and not r["pass"]:
            failing = [c["name"] for c in r["checks"] if not c["passed"]]
            if r.get("judge_pass") is False:
                failing.append("judge")
            regressions.append({"id": r["id"], "now_failing": failing})
        elif not p["pass"] and r["pass"]:
            fixed.append(r["id"])
    return {"regressions": regressions, "fixed": fixed, "compared_cases": len(prev)}


def build_report(run_id: str, cases: list[dict[str, Any]], results: list[dict[str, Any]],
                 stopped_for_budget: bool) -> dict[str, Any]:
    ok = [r for r in results if r["status"] == "ok"]
    scored = [r for r in ok if r["source"] != "trap"]
    traps = [r for r in ok if r["source"] == "trap"]
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in scored:
        by_source[r["source"]].append(r)
    synthetic = [r for r in scored if r["source"].startswith("synthetic")]
    handwritten = [r for r in scored if r["source"] in HANDWRITTEN]

    check_failures: dict[str, int] = defaultdict(int)
    for r in scored:
        for c in r["checks"]:
            if not c["passed"]:
                check_failures[c["name"]] += 1

    judged = [r for r in scored if r.get("judge")]
    judge_means = {d: round(sum(r["judge"][d] for r in judged) / len(judged), 2)
                   for d in judging.DIMENSIONS} if judged else {}
    judge_versions = sorted({r["judge"]["judge_version"] for r in judged})

    rates = {"synthetic": _rate(synthetic), "handwritten": _rate(handwritten)}
    present = [v for v in rates.values() if v is not None]
    prev_id = previous_run(run_id)
    prev_results = list({r["id"]: r for r in _read(runs_dir() / prev_id / "results.jsonl")}.values()) \
        if prev_id else []
    prev_meta = json.loads((runs_dir() / prev_id / "meta.json").read_text(encoding="utf-8")) if prev_id else {}
    this_meta_path = runs_dir() / run_id / "meta.json"
    this_meta = json.loads(this_meta_path.read_text(encoding="utf-8")) if this_meta_path.exists() else {}

    return {
        "run_id": run_id,
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_commit": this_meta.get("git_commit"),
        "complete": len(ok) == len(cases) and not stopped_for_budget,
        "counts": {"cases": len(cases), "scored": len(ok),
                   "infra": sum(r["status"] == "infra" for r in results),
                   "not_yet_run": len(cases) - len(results)},
        # When synthetic scores 90 and hand-written 60, the number to
        # believe is 60: the headline is the LOWER of the two.
        "headline_pass_rate": min(present) if present else None,
        "pass_rate": {**rates, "all": _rate(scored), "code_checks": _rate(scored, "code_pass"),
                      **{f"source:{k}": _rate(v) for k, v in sorted(by_source.items())},
                      # Promoted cases by the failure cluster they guard: a
                      # fix is judged by whether its whole cluster passes.
                      **{k: _rate(v) for k, v in sorted(_by_cluster(scored).items())}},
        "traps": {"n": len(traps), "abstention_rate": _rate(traps, "abstained"),
                  "ungrounded_truth_rate": _rate(traps, "ungrounded_truth")},
        "check_failures": dict(sorted(check_failures.items(), key=lambda kv: -kv[1])),
        "judge": {"means": judge_means, "versions": judge_versions, "n": len(judged),
                  "agreement": latest_agreement(judge_versions)},
        "mean_iterations": round(sum(r["outcome"]["iterations"] for r in scored) / len(scored), 2) if scored else None,
        "mean_latency_s": round(sum(r["latency_s"] for r in ok) / len(ok), 1) if ok else None,
        "turn_timeout_s": get_config()["evaluation"]["turn_timeout_s"],
        "app_turn_timeout_s": get_config()["api"]["turn_timeout_seconds"],
        "throttled_cases": sum(bool(r.get("throttled")) for r in ok),
        "compared_to": prev_id,
        "judge_comparable": bool(prev_id) and prev_meta.get("judge_version") == this_meta.get("judge_version"),
        "models": this_meta.get("models"),
        "models_changed": bool(prev_id) and prev_meta.get("models") != this_meta.get("models"),
        "diff": diff_runs(results, prev_results) if prev_id else None,
        "failures": [{"id": r["id"], "source": r["source"],
                      "failed": [c["name"] + ": " + c["detail"] for c in r["checks"] if not c["passed"]]
                      + ([f"judge: {r['judge']}"] if r.get("judge_pass") is False else [])}
                     for r in scored if not r["pass"]],
    }


def latest_agreement(judge_versions: list[str]) -> dict[str, Any] | None:
    """Judge-human agreement, only if measured for the judge version
    that produced these scores — an old agreement number describes a
    different judge."""
    path = runs_dir() / "agreement.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if data.get("judge_version") in judge_versions else {
        "stale": True, "measured_for": data.get("judge_version")}


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:g}%"


def render_markdown(rep: dict[str, Any]) -> str:
    pr, tr, jd = rep["pass_rate"], rep["traps"], rep["judge"]
    lines = [
        f"# Evaluation run {rep['run_id']}",
        "",
        f"Commit {rep['git_commit']} · models {rep.get('models')} · "
        f"{rep['counts']['scored']} of {rep['counts']['cases']} cases scored"
        + ("" if rep["complete"] else " · **incomplete — resume to finish**"),
        "",
        "| Headline | |",
        "|---|---|",
        f"| Pass rate (lower of synthetic / hand-written) | **{_pct(rep['headline_pass_rate'])}** |",
        f"| Ungrounded-truth rate on traps (lower is better) | **{_pct(tr['ungrounded_truth_rate'])}** |",
        f"| Trap abstention rate | {_pct(tr['abstention_rate'])} |",
        "",
        f"Time limit per question: {rep['turn_timeout_s']} s in this evaluation "
        f"(the app uses {rep['app_turn_timeout_s']} s). {rep['throttled_cases']} of "
        f"{rep['counts']['scored']} scored cases were slowed by the provider's rate limit"
        + (f" and would likely have hit the app's {rep['app_turn_timeout_s']} s limit."
           if rep["throttled_cases"] else "."),
        "",
        "| Pass rate by source | |",
        "|---|---|",
        *[f"| {k} | {_pct(v)} |" for k, v in pr.items()],
        "",
    ]
    if jd["n"]:
        ag = jd.get("agreement")
        if ag and not ag.get("stale"):
            ag_text = (f"n={ag['n']}, pass agreement {ag['pass']['exact_pct']}% (κ={ag['pass']['kappa']}); "
                       + ", ".join(f"{d} κ={ag[d]['kappa']}" for d in judging.DIMENSIONS))
        elif ag:
            ag_text = f"stale — measured for judge {ag['measured_for']}; re-run --agreement"
        else:
            ag_text = "not measured yet — label answers, then run --agreement"
        lines += [f"Judge {', '.join(jd['versions'])}: " + ", ".join(f"{k} {v}" for k, v in jd["means"].items())
                  + f" (1-4, n={jd['n']})", "", f"Judge-human agreement: {ag_text}", ""]
    if rep["check_failures"]:
        lines += ["Failed checks: " + ", ".join(f"{k} x{v}" for k, v in rep["check_failures"].items()), ""]
    if rep["diff"] is not None:
        d = rep["diff"]
        lines += [f"## Compared with {rep['compared_to']}"
                  + ("" if rep["judge_comparable"] else " (judge changed — judge scores not comparable)")
                  + (" (MODELS CHANGED — differences may be the models, not the code)"
                     if rep.get("models_changed") else ""), ""]
        lines += [f"- **REGRESSION** {x['id']}: now failing {', '.join(x['now_failing'])}" for x in d["regressions"]]
        lines += [f"- fixed: {x}" for x in d["fixed"]]
        if not d["regressions"] and not d["fixed"]:
            lines.append("- no case changed from pass to fail or back")
        lines.append("")
    if rep["failures"]:
        lines += ["## Failing cases", ""]
        for f in rep["failures"]:
            lines.append(f"- `{f['id']}` ({f['source']}): " + "; ".join(f["failed"]))
    return "\n".join(lines) + "\n"
