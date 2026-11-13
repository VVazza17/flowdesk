"""Writing point-in-time macro observations (M2 slice 5, step 3). The only writer of macro_metrics.

Rows are only ever inserted (the table's triggers refuse anything else; ADR-0003 addendum). This
module is where "idempotent" is made true and where a history is committed: a re-run of an
identical payload inserts nothing, a fetched chain longer than the stored one is EXTENDED (this is
also how the daily poll appends), and a payload that disagrees with what is stored is an error and
never an overwrite (rule 6 of the ADR-0003 addendum, 2026-11-13).

The judging happens INSIDE the transaction, after the per-series lock and the read of what is
stored, so no other writer can change the baseline between the check and the write. The
`series_history_state` row is written in the same transaction: "captured" can never be true
without the rows, and a refused series writes neither.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.ingest.errors import SchemaMismatch
from app.ingest.macro import HistoryVerdict, Revision, available_at_for, vintage_for
from app.models.macro import MacroMetric, SeriesHistoryState

log = structlog.get_logger(__name__)

SOURCE = "fred"
# asyncpg allows 32767 bind parameters per statement; a row binds 8 of them.
_BATCH = 2000

Chains = Mapping[date, Sequence[Revision]]


@dataclass(frozen=True)
class HistoryState:
    """The row that says a series' history is fully captured."""

    series_id: str
    rule_version: int
    captured_through: date
    completed_at: datetime


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def read_state(engine: AsyncEngine, series_id: str) -> HistoryState | None:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(
                    SeriesHistoryState.series_id,
                    SeriesHistoryState.rule_version,
                    SeriesHistoryState.captured_through,
                    SeriesHistoryState.completed_at,
                ).where(SeriesHistoryState.series_id == series_id)
            )
        ).first()
    return None if row is None else HistoryState(*row)


async def read_chains(conn: AsyncConnection, metric: str) -> dict[date, tuple[Revision, ...]]:
    """What is stored for `metric`, as chains of (publication date, value) in revision order.

    The publication date is recovered from `available_at` by the inverse of the rule that wrote it
    (`vintage_for`). A chain with a hole in its revision numbers raises: the store never writes one,
    so finding one means the table was repaired by hand and must be looked at, not extended.
    """
    rows = await conn.execute(
        select(
            MacroMetric.effective_at,
            MacroMetric.revision,
            MacroMetric.value,
            MacroMetric.available_at,
        )
        .where(MacroMetric.metric == metric)
        .order_by(MacroMetric.effective_at, MacroMetric.revision)
    )
    chains: dict[date, list[Revision]] = {}
    for effective_at, revision, value, available_at in rows:
        chain = chains.setdefault(effective_at, [])
        if revision != len(chain):
            raise SchemaMismatch(f"stored revisions of {metric} are not contiguous from 0")
        chain.append(Revision(vintage_for(available_at), value))
    return {period: tuple(chain) for period, chain in chains.items()}


@dataclass(frozen=True)
class Committed:
    created: int  # rows inserted by this call: 0 on an identical re-run
    verdict: HistoryVerdict


async def commit_history(
    engine: AsyncEngine,
    metric: str,
    *,
    judge: Callable[[Chains], HistoryVerdict],
    captured_through: date,
    rule_version: int,
    ingestion_run_id: int | None,
    clock: Callable[[], datetime] = _utcnow,
) -> Committed:
    """Judge the fetched history against what is stored and, only if every rule holds, write it.

    `judge` gets the stored chains and returns the verdict of the six rules (it closes over what
    was fetched). A refusal raises SchemaMismatch naming rules, counts and dates, and nothing is
    written, including the state row. Otherwise each period's chain is extended from where the
    stored one ends (revision number = position), and the state row is upserted, in one
    transaction under an advisory lock on the metric.
    """
    now = clock()
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"macro_metrics:{metric}"}
        )
        stored = await read_chains(conn, metric)
        verdict = judge(stored)
        if not verdict.ok:
            # Rules, counts and dates only: the values came from a provider and stay out.
            raise SchemaMismatch(f"history of {metric} refused: {verdict.refusal_text()}")
        fresh: list[dict[str, object]] = []
        for period, chain in verdict.chains.items():
            for revision in range(len(stored.get(period, ())), len(chain)):
                fresh.append(
                    {
                        "metric": metric,
                        "effective_at": period,
                        "revision": revision,
                        "value": chain[revision].value,
                        "available_at": available_at_for(chain[revision].vintage),
                        "ingested_at": now,
                        "source": SOURCE,
                        "ingestion_run_id": ingestion_run_id,
                    }
                )
        created = 0
        for i in range(0, len(fresh), _BATCH):
            result = await conn.execute(
                pg_insert(MacroMetric).values(fresh[i : i + _BATCH]).on_conflict_do_nothing()
            )
            created += result.rowcount or 0
        state = {
            "rule_version": rule_version,
            "captured_through": captured_through,
            "completed_at": now,
            "ingestion_run_id": ingestion_run_id,
            "first_prints": verdict.first_prints,
            "revisions": verdict.revisions,
            "no_vintage_excluded": verdict.no_vintage_excluded,
            "gaps_without_trace_skipped": verdict.gaps_without_trace_skipped,
            "revised_pre_vintage_excluded": verdict.revised_pre_vintage_excluded,
            "duplicates_skipped": verdict.duplicates_skipped,
        }
        await conn.execute(
            pg_insert(SeriesHistoryState)
            .values(series_id=metric, **state)
            .on_conflict_do_update(index_elements=[SeriesHistoryState.series_id], set_=state)
        )
    return Committed(created, verdict)


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


__all__ = [
    "Chains",
    "Committed",
    "HistoryState",
    "commit_history",
    "count_rows",
    "read_chains",
    "read_state",
]
