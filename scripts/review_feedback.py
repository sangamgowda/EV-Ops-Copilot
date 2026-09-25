"""Review flagged answers: promote real failures to tests, dismiss the rest.

    python scripts/review_feedback.py            # the queue, biggest cluster first
    python scripts/review_feedback.py --status   # cluster resolution only

Flags come from a thumbs-down in the app (POST /feedback) and from
users asking the same thing again within 30 seconds. Each is bucketed by
what its trace shows went wrong (wrong domain, empty retrieval, ...).

For each flagged answer you see the question, the answer, the signals
and a link to its full trace. Then:

  p  promote  — you write what SHOULD have happened (a reference answer,
                and optionally words it must or must not contain). It is
                added to the test set tagged with its cluster.
  d  dismiss  — with a reason: the user was wrong, wanted a different
                format, or it cannot be reproduced.
  s  skip     — decide later.

Nothing is promoted without your reference answer: a thumbs-down alone
does not say what the right answer was, and copying the failure in
would teach the tests to expect it.

After promoting, rebuild and gate:
    python scripts/build_golden.py --no-generate
    python scripts/run_eval.py --promoted
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import textwrap
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ops_copilot.feedback import promote  # noqa: E402

DISMISS_REASONS = {"1": "user was wrong", "2": "wanted a different format", "3": "cannot reproduce"}


def wrap(text: str, width: int = 88) -> str:
    return "\n".join(textwrap.fill(p, width, initial_indent="    ", subsequent_indent="    ")
                     for p in (text or "(empty)").splitlines() if p.strip())


def trace_link(turn_id: str) -> str:
    """The turn id IS the trace id (Phase 10), so the full trace is one
    click from the flag."""
    try:
        import httpx

        from ops_copilot.settings import get_settings

        s = get_settings()
        r = httpx.get(f"{s.langfuse_host}/api/public/projects",
                      auth=(s.langfuse_public_key, s.langfuse_secret_key), timeout=10)
        project = r.json()["data"][0]["id"]
        return f"{s.langfuse_host}/project/{project}/traces/{turn_id}"
    except Exception:
        return f"trace id {turn_id}"


def print_status(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No flagged answers yet.")
        return
    print(f"{'cluster':<18} {'answers':>7} {'open':>5} {'dismissed':>9} {'cases':>6} {'passing':>8} "
          f"{'new since':>10}  state")
    for r in rows:
        print(f"{r['cluster']:<18} {r['flags']:>7} {r['open']:>5} {r['dismissed']:>9} {r['promoted_cases']:>6} "
              f"{r['passing']:>4}/{r['promoted_cases']:<3} {r['new_flags_since_promotion']:>10}  {r['state']}")
    print("\n'resolved' = its promoted cases pass and no new flags arrived after promotion. "
          "'recurring' = the cases pass but the cluster still happens: the fix was too narrow.")


def ask(prompt: str) -> str:
    return input(prompt).strip()


async def review_one(item: dict[str, Any], promoted: list[dict[str, Any]]) -> str | None:
    print("=" * 92)
    print(f"cluster {item['cluster']}  ·  signals {', '.join(item['signals'])}  ·  {item['rating']}"
          + (f" ({item['category']})" if item.get("category") else ""))
    print(f"trace   {trace_link(item['turn_id'])}\n")
    print("  QUESTION\n" + wrap(item["question"]) + "\n")
    print("  ANSWER GIVEN\n" + wrap((item.get("answer") or "")[:1500]) + "\n")
    if item.get("comment"):
        print("  USER SAID\n" + wrap(item["comment"]) + "\n")
    while True:
        choice = ask("  p = promote, d = dismiss, s = skip, q = quit: ").lower()
        if choice in ("q", "s"):
            return choice
        if choice == "d":
            reason = ask("  why? 1 user was wrong · 2 wanted a different format · 3 cannot reproduce · "
                         "or type a reason: ")
            await promote.mark(item["turn_id"], "dismissed", DISMISS_REASONS.get(reason, reason))
            print("  dismissed\n")
            return "d"
        if choice == "p":
            print("\n  Write what SHOULD have happened. This becomes the test's reference answer.")
            reference = ask("  correct answer: ")
            if not reference:
                print("  a reference answer is required to promote; nothing saved")
                continue
            review = {
                "reference_answer": reference,
                "must_contain": [x.strip() for x in ask("  words the answer must contain (comma-separated, "
                                                        "Enter for none): ").split(",") if x.strip()],
                "must_not_contain": [x.strip() for x in ask("  words it must NOT contain (comma-separated, "
                                                            "Enter for none): ").split(",") if x.strip()],
                "abstain": ask("  should the right answer be 'cannot tell / does not exist'? y/N: ").lower() == "y",
            }
            vehicle = ask("  expected vehicle id (e.g. V-042), 'none' if it does not exist, Enter to skip: ")
            if vehicle:
                review["vehicle_id"] = "not_found" if vehicle.lower() == "none" else vehicle
            dom = ask("  domain: d = diagnostic, b = business, db = both, Enter to skip: ").lower()
            review["domains"] = {"d": ["diagnostic"], "b": ["business"], "db": ["diagnostic", "business"]}.get(dom)
            case = promote.build_case(item, review)
            for w in promote.coverage_warnings(case, promoted):
                print(f"  ! {w}")
            if ask(f"  save as {case['id']}? Y/n: ").lower() == "n":
                continue
            promote.append_promoted(case)
            promoted.append(case)
            await promote.mark(item["turn_id"], "promoted", "promoted with reviewer reference", case["id"])
            print(f"  promoted as {case['id']} (cluster {case['cluster']})\n")
            return "p"


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--status", action="store_true", help="show cluster resolution and exit")
    args = p.parse_args()

    items = await promote.flagged_turns()
    promoted = promote.load_promoted()
    print_status(promote.cluster_status(items, promoted, promote.latest_eval_results()))
    if args.status:
        return 0

    groups = promote.queue(items)
    if not groups:
        print("\nNothing waiting for review.")
        return 0
    print("\nQueue, biggest cluster first: " + ", ".join(f"{c} ({len(v)})" for c, v in groups) + "\n")
    done = {"p": 0, "d": 0}
    try:
        for _cluster, group in groups:
            for item in group:
                outcome = await review_one(item, promoted)
                if outcome == "q":
                    raise KeyboardInterrupt
                if outcome in done:
                    done[outcome] += 1
    except (KeyboardInterrupt, EOFError):
        print("\nStopped; decisions so far are saved.")
    print(f"\n{done['p']} promoted, {done['d']} dismissed.")
    if done["p"]:
        print("Next: python scripts/build_golden.py --no-generate  then  python scripts/run_eval.py --promoted")
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    async def run() -> int:
        from ops_copilot.db.engine import dispose_all

        try:
            return await main()
        finally:
            await dispose_all()

    sys.exit(asyncio.run(run()))
