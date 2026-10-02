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
