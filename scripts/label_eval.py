"""Score answers yourself, so the judge can be checked against you.

    python scripts/label_eval.py              # label answers from the latest run
    python scripts/label_eval.py --run 20260925-101500 --target 30

Shows one answer at a time: the question, the correct (reference)
answer, and what the system said. You give three scores from 1 to 4,
using the same rubric as the judge (printed below each answer).

The judge's own scores are never shown — seeing them first would pull
your scores towards them, and then the agreement number measures
nothing. Each label is saved as you go; quit any time with q and
carry on later. Aim for 30.

Then:  python scripts/run_eval.py --agreement
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from datetime import UTC, datetime
from itertools import zip_longest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ops_copilot.evaluation.agreement import labels_path, load_labels  # noqa: E402
from ops_copilot.evaluation.runner import load_cases, runs_dir  # noqa: E402
from ops_copilot.settings import get_config  # noqa: E402

RUBRIC = {
    "completeness": "4 every fact in the reference · 3 one minor detail missing · 2 a key fact missing · 1 mostly missing",
    "correctness": "4 nothing contradicts the reference · 3 small imprecision · 2 one clear error or wrong cause · 1 several errors",
    "hedging": "4 confident where the reference is, says 'unknown' where it is · 3 slightly off · "
               "2 claims a cause the reference says is unknown (or refuses a known answer) · 1 states something false as fact",
}


def wrap(text: str) -> str:
    return "\n".join(textwrap.fill(p, 88, initial_indent="    ", subsequent_indent="    ")
                     for p in (text or "(empty)").splitlines() if p.strip())


def ask(dim: str) -> int | None:
    while True:
        raw = input(f"  {dim} (1-4, s=skip, q=quit): ").strip().lower()
        if raw == "q":
            raise KeyboardInterrupt
        if raw == "s":
            return None
        if raw in {"1", "2", "3", "4"}:
            return int(raw)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", help="run id (default: the latest)")
    p.add_argument("--target", type=int, default=30)
    args = p.parse_args()

    runs = sorted(d.name for d in runs_dir().iterdir() if (d / "results.jsonl").exists()) \
        if runs_dir().exists() else []
    run_id = args.run or (runs[-1] if runs else None)
    if not run_id:
        print("No evaluation runs yet. Run: python scripts/run_eval.py --smoke")
        return 1
    cfg = get_config()["evaluation"]
    cases = {c["id"]: c for c in load_cases(cfg["golden_path"]) + load_cases(cfg["trap_path"])}
    results = {}
    for line in (runs_dir() / run_id / "results.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r["status"] == "ok":
            results[r["id"]] = r

    done = {(lab["id"], lab["answer_hash"]) for lab in load_labels()}
    todo = [r for r in results.values() if (r["id"], r["answer_hash"]) not in done and r["id"] in cases]
    # Round-robin across sources so 30 labels cover every kind of case,
    # not just the first 30 in file order.
    by_source: dict[str, list[dict]] = {}
    for r in sorted(todo, key=lambda r: r["id"]):
        by_source.setdefault(r["source"], []).append(r)
    todo = [r for group in zip_longest(*by_source.values()) for r in group if r is not None]
    have = len(load_labels())
    print(f"Run {run_id}: {len(todo)} answers not yet labelled; you have {have} labels "
          f"(target {args.target}).\n")

    try:
        for r in todo:
            if have >= args.target:
                print(f"Target of {args.target} reached.")
                break
            case = cases[r["id"]]
            print("=" * 92)
            print(f"[{have + 1}/{args.target}]  {r['id']}  ({r['source']})\n")
            print("  QUESTION\n" + wrap(case["question"]) + "\n")
            print("  CORRECT ANSWER (reference)\n" + wrap(case["reference_answer"]) + "\n")
            print("  WHAT THE SYSTEM SAID\n" + wrap(r["answer"]) + "\n")
            scores = {}
            for dim in ("completeness", "correctness", "hedging"):
                print(f"  · {RUBRIC[dim]}")
                v = ask(dim)
                if v is None:
                    break
                scores[dim] = v
            if len(scores) < 3:
                print("  skipped\n")
                continue
            note = input("  note (optional, Enter to skip): ").strip()
            label = {"id": r["id"], "answer_hash": r["answer_hash"], "answer": r["answer"], **scores,
                     "note": note, "run_id": run_id,
                     "labeled_at": datetime.now(UTC).isoformat(timespec="seconds")}
            with labels_path().open("a", encoding="utf-8") as f:
                f.write(json.dumps(label, ensure_ascii=False) + "\n")
            have += 1
            print("  saved\n")
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Your labels are saved; run this again to continue.")
    print(f"\n{have} labels in {labels_path().relative_to(ROOT)}. "
          "When you have enough: python scripts/run_eval.py --agreement")
    return 0


if __name__ == "__main__":
    sys.exit(main())
