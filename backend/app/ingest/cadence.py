"""How often a series publishes a NEW period, and how long without one is too long.

(M2 slice 5, step 5.)

Pure: no database, no clock. The numbers it produces are written into
`settings.FRED_DATA_STALE_AFTER` by a person after running `python -m app.measure_cadence`; the
API never computes them per request.

THE QUANTITY IS THE GAP BETWEEN PUBLICATIONS, NOT THE LAG (this departs from the 2026-11-11 plan,
which said "lag", on the strength of the measured data). A series is stale when no NEW period has
appeared for too long, and that is a question about the interval between publication days. Lag
(`available_at - effective_at`) is how old a period is when it appears, and the two differ a great
deal: DRCCLACBS publishes about every 91 days with a lag of about 143; HOUST publishes about
monthly with a lag of 48; IORB publishes values for dates that have not happened yet (lag 0, or
negative). Lag stays on the response as information; it does not judge anything.

THE RULE: stale_after = ceil(MULTIPLE x p95 of the gaps between consecutive publication days over
the last WINDOW_YEARS years), never less than FLOOR. p95 and not the maximum, because one outlier
(a US government shutdown made CPI's longest gap 55 days against a median of 29) must not set the
threshold for the next five years; MULTIPLE is generous on purpose, "not a tight fit": a release-
schedule change is not predicted by history. A shutdown that exceeds the threshold reading `stale`
is TRUE (no new data is arriving) and harmless (this is /health/data; nothing pages on it).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

MULTIPLE = 3
FLOOR = timedelta(days=2)
WINDOW_YEARS = 5


@dataclass(frozen=True)
class Cadence:
    """Gaps, in days, between consecutive publication days."""

    n: int
    p50: float
    p95: float
    longest: int


def gaps_between(publication_days: Sequence[date]) -> list[int]:
    """Days between consecutive distinct publication days, oldest first."""
    days = sorted(set(publication_days))
    return [(b - a).days for a, b in zip(days, days[1:], strict=False)]


def percentile(values: Sequence[float], q: float) -> float:
    """Linear interpolation between closest ranks (the same as Postgres `percentile_cont`)."""
    if not values:
        raise ValueError("percentile of nothing")
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def cadence(publication_days: Sequence[date]) -> Cadence | None:
    """None when there are fewer than two publication days (no gap to measure)."""
    gaps = gaps_between(publication_days)
    if not gaps:
        return None
    return Cadence(len(gaps), percentile(gaps, 0.5), percentile(gaps, 0.95), max(gaps))


def suggest_stale_after(measured: Cadence) -> timedelta:
    """MULTIPLE x p95, rounded UP to whole days, never below FLOOR."""
    return max(FLOOR, timedelta(days=math.ceil(MULTIPLE * measured.p95)))
