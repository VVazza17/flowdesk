"""Entry point: `python -m app.measure_cadence`. Measures each stored series' publication cadence
and compares it with `settings.FRED_DATA_STALE_AFTER`.

    docker compose run --rm worker python -m app.measure_cadence

Read-only. It prints, per series, the gaps between publication days over the last
`cadence.WINDOW_YEARS` years, the threshold the rule would choose, and the one in the settings. The
settings are updated by a person from this output (and the measurement date written beside them):
they are config, like `FRED_SERIES`' boundaries, and the API never computes them per request.
Run it after a release-schedule change, or when the live-data check says a threshold has drifted.
"""

import asyncio
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app import settings
from app.ingest.cadence import WINDOW_YEARS, Cadence, cadence, suggest_stale_after


async def publication_days(
    engine: AsyncEngine, now: datetime, *, years: int = WINDOW_YEARS
) -> dict[str, list[date]]:
    """Per stored metric, the New York calendar days on which a NEW period (revision 0) was
    published, over the last `years` years. First prints only: a revision proves the source is alive
    but not that new periods are arriving, and a series like DRCCLACBS revises nearly every week."""
    since = now - timedelta(days=365 * years)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                # available_at is the START of the day after publication (ADR-0003 addendum), so
                # one microsecond earlier is still the publication day (macro.vintage_for).
                "SELECT DISTINCT metric, "
                "((available_at - interval '1 microsecond') AT TIME ZONE 'America/New_York')::date "
                "FROM macro_metrics WHERE revision = 0 AND available_at >= :since "
                "ORDER BY 1, 2"
            ),
            {"since": since},
        )
        out: dict[str, list[date]] = {}
        for metric, day in rows:
            out.setdefault(metric, []).append(day)
    return out


async def measure(engine: AsyncEngine, now: datetime) -> dict[str, Cadence | None]:
    return {m: cadence(days) for m, days in (await publication_days(engine, now)).items()}


def render(measured: dict[str, Cadence | None]) -> list[str]:
    configured = settings.data_stale_after_all()
    lines = [
        f"{'metric':14s} {'gaps':>5s} {'p50':>6s} {'p95':>6s} {'max':>5s} "
        f"{'suggested':>10s} {'configured':>11s}"
    ]
    for metric in sorted(set(measured) | set(configured)):
        c = measured.get(metric)
        have = configured.get(metric)
        have_text = f"{have.days} d" if have else "none"
        if c is None:
            lines.append(
                f"{metric:14s} {'-':>5s} {'-':>6s} {'-':>6s} {'-':>5s} {'-':>10s} {have_text:>11s}"
            )
            continue
        lines.append(
            f"{metric:14s} {c.n:5d} {c.p50:6.1f} {c.p95:6.1f} {c.longest:5d} "
            f"{suggest_stale_after(c).days:>8d} d {have_text:>11s}"
        )
    return lines


async def run() -> int:
    url = settings.database_url()
    if url is None:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    engine = create_async_engine(url)
    try:
        for line in render(await measure(engine, datetime.now(UTC))):
            print(line)
    finally:
        await engine.dispose()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
