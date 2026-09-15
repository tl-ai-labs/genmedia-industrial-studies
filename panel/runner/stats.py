"""
The three statistics the correlation reports, in the standard library.

No numpy or scipy: the panel must run on a laptop with nothing installed
beyond PyYAML, and the numbers are small enough (tens of scenarios) that a
closed form is both sufficient and easier to check by hand.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Sequence


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson score interval for a proportion k/n. None when n == 0."""
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _ranks(xs: Sequence[float]) -> list[float]:
    """Average ranks, so ties do not bias the coefficient."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Spearman's rho as Pearson on average ranks. None below 3 points or
    when either side is constant (the coefficient is undefined, not zero)."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    return sxy / math.sqrt(sxx * syy)


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float | None:
    """Chance-corrected agreement between two labellings of the same items.
    None when there are no items or when both raters are constant and
    identical (expected agreement is 1, kappa undefined)."""
    if len(a) != len(b) or not a:
        return None
    n = len(a)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[l] * cb[l] for l in set(ca) | set(cb)) / (n * n)
    if pe == 1.0:
        return None
    return (po - pe) / (1 - pe)
