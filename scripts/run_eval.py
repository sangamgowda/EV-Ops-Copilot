"""Run the golden set through the real system and print a report.

    python scripts/run_eval.py                    # everything: golden + traps, judged
    python scripts/run_eval.py --smoke            # 8 varied cases, ~10 minutes
    python scripts/run_eval.py --resume 20260925-101500
    python scripts/run_eval.py --only syn_overload_V-042,adv_typo_vehicle
    python scripts/run_eval.py --agreement        # judge vs your labels

Needs the running stack (MCP server and database, as for /chat).

Free tier: a full run needs more than one day's allowance on the
larger model. When the allowance runs out the run stops cleanly; the
next day, `--resume <run id>` finishes it. Cases slowed by the
provider's per-minute limit are re-run on resume, not scored.

Exits 1 when a case that passed in the previous run fails now, so a
change can be gated on it.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ops_copilot.evaluation.runner import load_cases, run_eval, runs_dir  # noqa: E402
from ops_copilot.settings import get_config  # noqa: E402

# One of each kind, for a quick check that fits the free tier easily.
SMOKE = [
    "syn_overload_V-042", "syn_speed_cap_V-055", "syn_code_ERR_205", "syn_sales_top_region_q3",
    "adv_nonexistent_vehicle", "adv_refute_claim", "prm_undocumented_overclaimed", "trap_cell_chemistry",
]


def show(r: dict[str, Any]) -> None:
    mark = {"ok": "PASS" if r["pass"] else "FAIL", "infra": "RATE", "budget": "STOP"}[r["status"]]
    extra = ""
    if r["status"] == "ok" and not r["pass"]:
        extra = "  " + "; ".join(c["name"] for c in r["checks"] if not c["passed"])
        if r.get("judge_pass") is False:
            extra += "  judge " + "/".join(str(r["judge"][d]) for d in ("completeness", "correctness", "hedging"))
    if r.get("ungrounded_truth"):
        extra += "  ANSWERED FROM TRAINING"
    print(f"  {mark}  {r['id']:<36} {r['latency_s']:>5}s{extra}", flush=True)


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--resume", metavar="RUN_ID", help="finish an earlier run")
    p.add_argument("--only", help="comma-separated case ids")
    p.add_argument("--smoke", action="store_true", help="a quick, varied subset")
    p.add_argument("--limit", type=int)
    p.add_argument("--no-judge", action="store_true")
    p.add_argument("--no-traps", action="store_true")
    p.add_argument("--pause", type=float, help="seconds between cases (default from config)")
    p.add_argument("--agreement", action="store_true", help="measure judge-human agreement and exit")
    args = p.parse_args()

    if args.agreement:
        from ops_copilot.evaluation.agreement import measure

        res = await measure(lambda i, h, j: print(f"  {i:<36} you {h['completeness']}/{h['correctness']}/"
                                                  f"{h['hedging']}  judge {j['completeness']}/"
                                                  f"{j['correctness']}/{j['hedging']}", flush=True))
        if not res["n"]:
            print("No labels yet. Run: python scripts/label_eval.py")
            return 1
        print(f"\nJudge {res['judge_version']} vs you, n={res['n']}: pass/fail agreement "
              f"{res['pass']['exact_pct']}% (kappa {res['pass']['kappa']})")
        for d in ("completeness", "correctness", "hedging"):
            print(f"  {d:<13} exact {res[d]['exact_pct']}%  within one {res[d]['within_one_pct']}%  "
                  f"kappa {res[d]['kappa']}")
        return 0

    cfg = get_config()["evaluation"]
    cases = load_cases(cfg["golden_path"])
    if not args.no_traps:
        cases += load_cases(cfg["trap_path"])
    if args.smoke:
        cases = [c for c in cases if c["id"] in SMOKE]
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    if args.limit:
        cases = cases[: args.limit]
    if args.resume:
        import json

        ids = json.loads((runs_dir() / args.resume / "meta.json").read_text(encoding="utf-8"))["case_ids"]
        everything = {c["id"]: c for c in load_cases(cfg["golden_path"]) + load_cases(cfg["trap_path"])}
        cases = [everything[i] for i in ids if i in everything]

    from ops_copilot.evaluation.runner import preflight

    problem = await preflight()
    if problem:
        print(f"Not running: {problem}")
        print("\nThe evaluation must reach the same tool server /chat uses. From Windows with the")
        print("Docker stack up, set  MCP_TRANSPORT=http  MCP_SERVER_HOST=localhost")
        print("(or run it inside the api container).")
        return 2

    print(f"Running {len(cases)} cases through the real system"
          + ("" if args.no_judge else ", judged") + " …", flush=True)
    run_id, rep = await run_eval(cases, run_id=args.resume, use_judge=not args.no_judge,
                                 pause_s=args.pause, on_result=show)
    print()
    print((runs_dir() / run_id / "report.md").read_text(encoding="utf-8"))
    if not rep["complete"]:
        print(f"Incomplete. Finish later with:  python scripts/run_eval.py --resume {run_id}")
    regressions = (rep["diff"] or {}).get("regressions", [])
    return 1 if regressions else 0


async def run() -> int:
    from ops_copilot.mcp_client.client import get_client
    from ops_copilot.observability import tracing

    try:
        return await main()
    finally:
        # Close the tool-server connection inside the loop that opened
        # it, and send any buffered traces, before the process exits.
        await get_client().close()
        tracing.shutdown()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(asyncio.run(run()))
