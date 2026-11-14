"""series_history_state says, on its own columns, which vintage of truth each one carries (0009).

The row mixes numbers that advance with every poll and numbers frozen at the last full capture. A
reader who sees them side by side will take them as contemporaneous, so the distinction lives on the
database objects, where the numbers are read. Needs DATABASE_URL (a scratch database from conftest).
"""

import asyncio
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

CURRENT = {"captured_through", "completed_at", "ingestion_run_id", "first_prints", "revisions"}
FROZEN = {
    "no_vintage_excluded",
    "gaps_without_o4_first_print_skipped",
    "gap_first_prints_without_o4_witness",
    "revised_pre_vintage_excluded",
    "duplicates_skipped",
}


def comments(scratch_url: str) -> tuple[str | None, dict[str, str | None]]:
    async def fn() -> tuple[str | None, dict[str, str | None]]:
        engine = create_async_engine(scratch_url)
        try:
            async with engine.connect() as conn:
                table = (
                    await conn.execute(
                        text("SELECT obj_description('series_history_state'::regclass, 'pg_class')")
                    )
                ).scalar()
                rows: list[Any] = list(
                    await conn.execute(
                        text(
                            "SELECT column_name, "
                            "col_description('series_history_state'::regclass, ordinal_position) "
                            "FROM information_schema.columns "
                            "WHERE table_name = 'series_history_state'"
                        )
                    )
                )
                return table, {r[0]: r[1] for r in rows}
        finally:
            await engine.dispose()

    return asyncio.run(fn())


def test_every_column_says_which_vintage_of_truth_it_carries(scratch_url: str) -> None:
    table, cols = comments(scratch_url)
    # A column added later without a comment fails here: it must say which vintage it belongs to.
    assert [c for c, text_ in cols.items() if not text_] == []
    assert table is not None and "TWO vintages" in table


def test_the_frozen_counts_are_marked_not_current_and_the_advancing_ones_are_current(
    scratch_url: str,
) -> None:
    _table, cols = comments(scratch_url)
    assert FROZEN | CURRENT <= set(cols)
    for column in FROZEN:
        assert "NOT CURRENT" in (cols[column] or ""), column
        assert "LAST FULL CAPTURE" in (cols[column] or ""), column
    for column in CURRENT:
        assert "CURRENT" in (cols[column] or "") and "NOT CURRENT" not in (cols[column] or "")


def test_the_two_sets_are_exactly_the_columns_the_poll_does_and_does_not_update() -> None:
    # The comments claim the poll never touches the FROZEN set: pin it against the deciding code.
    import inspect

    from app.services import macro_store

    source = inspect.getsource(macro_store.commit_history)
    updated_always, _, frozen_block = source.partition("if exclusion_counts:")
    for column in CURRENT - {"captured_through", "completed_at", "ingestion_run_id"}:
        assert f'"{column}"' in updated_always  # first_prints and revisions are in the always-block
    for column in FROZEN:
        assert f'"{column}"' in frozen_block and f'"{column}"' not in updated_always
