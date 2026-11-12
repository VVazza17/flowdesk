"""Writing point-in-time macro observations (M2 slice 5, step 3). The only writer of macro_metrics.

Rows are only ever inserted (the table's triggers refuse anything else; ADR-0003 addendum). This
module is where "idempotent" is made true: a re-run of an identical payload inserts nothing, and a
payload that disagrees with what is stored is an error and not an overwrite.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ingest.errors import SchemaMismatch
from app.models.macro import MacroMetric

log = structlog.get_logger(__name__)

SOURCE = "fred"
# asyncpg allows 32767 bind parameters per statement; a row binds 7 of them.
_BATCH = 2000


@dataclass(frozen=True)
class FirstPrint:
    """A first print ready to store: its publication instant is already computed."""

    effective_at: date
    value: Decimal | None
    available_at: datetime


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def insert_first_prints(
    engine: AsyncEngine,
    metric: str,
    rows: Sequence[FirstPrint],
    *,
    ingestion_run_id: int | None,
    clock: Callable[[], datetime] = _utcnow,
) -> int:
    """Store `rows` as revision 0 of `metric`, in one transaction. Returns how many were created.

    A first print already stored is skipped, so a repeat run returns 0 (the observable evidence of
    idempotency, app.ingest.runner). One that is stored with a DIFFERENT value or publication
    time raises SchemaMismatch and writes nothing: the stored row is the record of what was known,
    and a source that now disagrees about its own first print is a finding to look at, never
    something to resolve by overwriting (the triggers would refuse anyway).

    An advisory transaction lock on the metric serialises two writers of one series (a backfill
    and, later, the daily poll), so neither reads a stale baseline.
    """
    now = clock()
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"macro_metrics:{metric}"}
        )
        stored = {
            r.effective_at: (r.value, r.available_at)
            for r in (
                await conn.execute(
                    select(
                        MacroMetric.effective_at, MacroMetric.value, MacroMetric.available_at
                    ).where(MacroMetric.metric == metric, MacroMetric.revision == 0)
                )
            )
        }
        fresh: list[dict[str, object]] = []
        disagreements = 0
        for row in rows:
            if row.effective_at in stored:
                if stored[row.effective_at] != (row.value, row.available_at):
                    disagreements += 1
                continue
            fresh.append(
                {
                    "metric": metric,
                    "effective_at": row.effective_at,
                    "revision": 0,
                    "value": row.value,
                    "available_at": row.available_at,
                    "ingested_at": now,
                    "source": SOURCE,
                    "ingestion_run_id": ingestion_run_id,
                }
            )
        if disagreements:
            # The count only: the values came from a provider and stay out of the message.
            raise SchemaMismatch(
                f"{disagreements} stored first prints of {metric} disagree with the source"
            )
        created = 0
        for i in range(0, len(fresh), _BATCH):
            result = await conn.execute(
                pg_insert(MacroMetric).values(fresh[i : i + _BATCH]).on_conflict_do_nothing()
            )
            created += result.rowcount or 0
    return created


async def count_rows(engine: AsyncEngine, metric: str) -> int:
    async with engine.connect() as conn:
        return int(
            (
                await conn.execute(
                    select(func.count())
                    .select_from(MacroMetric)
                    .where(MacroMetric.metric == metric)
                )
            ).scalar_one()
        )
