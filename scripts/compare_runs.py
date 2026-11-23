"""Compare two finished runs of the same case set, case by case.

    python scripts/compare_runs.py 20261121-120508 20261123-145910

Prints how many cases each run passed, which cases were fixed and which
broke, and the exact McNemar p-value: are the fixes more than the breaks
by more than chance? Non-trap cases only. Use it for the baseline against
the final run; a report only compares a run with the one before it.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ops_copilot.evaluation.runner import compare_runs  # noqa: E402


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    c = compare_runs(sys.argv[1], sys.argv[2])
    n = c["cases"]
    print(f"{c['before']} -> {c['after']}, {n} non-trap cases scored in both")
    print(f"  passed before {c['passed_before']} ({100 * c['passed_before'] / n:.1f}%), "
          f"after {c['passed_after']} ({100 * c['passed_after'] / n:.1f}%)")
    print(f"  fixed {len(c['fixed'])}: {', '.join(c['fixed']) or '-'}")
    print(f"  broken {len(c['broken'])}: {', '.join(c['broken']) or '-'}")
    print(f"  exact McNemar p = {c['mcnemar_p']:g} "
          + ("(more than chance)" if c["mcnemar_p"] < 0.05 else "(could be chance)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
