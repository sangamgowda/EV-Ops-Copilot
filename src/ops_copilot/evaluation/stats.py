"""Uncertainty for pass rates.

46 cases is a small sample: a 70% pass rate on it is compatible with
anything from about 56% to 81%. Reporting the interval keeps a
two-case swing between runs from being read as progress or regression.

Wilson score interval, not the normal approximation: the normal one
breaks down near 0% and 100% and on small n, which is exactly where
these sets live (a cluster of 3 promoted cases at 100%).
"""

from __future__ import annotations

import math
from statistics import NormalDist


def wilson(passed: int, n: int, confidence: float = 0.95) -> tuple[float, float] | None:
    """(low, high) in percent, or None for an empty sample."""
    if n == 0:
        return None
    z = NormalDist().inv_cdf(0.5 + confidence / 2)
    p = passed / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(100 * max(0.0, centre - half), 1), round(100 * min(1.0, centre + half), 1)


def overlaps(a: tuple[float, float] | None, b: tuple[float, float] | None) -> bool:
    """Do two intervals overlap? If so, the difference between the rates
    is not evidence of a change on its own."""
    return a is not None and b is not None and a[0] <= b[1] and b[0] <= a[1]


def mcnemar(fixed: int, broken: int) -> float:
    """Exact McNemar test for two runs scored on the same cases.

    Only the cases that changed carry information: `fixed` failed before
    and pass now, `broken` passed before and fail now. If the change did
    nothing, a changed case is equally likely to go either way, so the
    number of fixes among the changed cases is Binomial(fixed + broken, 1/2).
    Returns the two-sided p-value; 1.0 when nothing changed.

    Two overlapping Wilson intervals say a difference is not proven; this
    asks the better question for a before/after on the same exam: are the
    fixes more than the breaks by more than chance?
    """
    n = fixed + broken
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(fixed, broken) + 1)) / 2 ** n
    return round(min(1.0, 2 * tail), 4)
