"""Mutation check: break the code in a copy, and the named tests must fail.

Rule 9 (CLAUDE.md) says a keyed test is checked by mutation: delete the key or filter and watch the
test fail. This runs that, for a named suite, without touching the working tree:

    python -m tools.mutate m2-slice6-step2      (from backend/, with DATABASE_URL set)
    python -m tools.mutate heartbeat-clock

For each mutation: copy backend/ to a temporary directory, replace one exact snippet in one file
(the snippet must occur exactly once, so a stale mutation fails loudly instead of testing nothing),
run only the tests named for it, and require pytest's exit code 1 (tests failed). Exit code 0 means
the mutation SURVIVED: the tests did not notice the code was broken. The unmutated copy is run first
and must pass, or every "caught" mutation would mean nothing.

Only the suite's own test file runs, never test_migrations.py: `alembic check` would flag almost any
schema mutation as model drift, and that would say nothing about whether these tests catch it.
"""

import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

M10 = "alembic/versions/0010_price_tables.py"
M11 = "alembic/versions/0011_bar_gaps.py"
M12 = "alembic/versions/0012_universe_membership_close_reason.py"
BARS = "app/ingest/bars.py"
CLIENT = "app/ingest/yfinance_client.py"
STORE = "app/services/bars_store.py"
FEED_PY = "app/ingest/bars_feed.py"  # the backfill and the polls (step 6 moved the run here)
CELERY = "app/celery_app.py"
SETTINGS = "app/settings.py"
READ = "app/services/bars_read.py"
M13 = "alembic/versions/0013_distributions_drop_total_return.py"
# The trigger-creating loop in 0010 and 0011 (each file has it once).
TRIGGER_LOOP = (
    'for event, scope in (("UPDATE", "ROW"), ("DELETE", "ROW"), ("TRUNCATE", "STATEMENT")):'
)


@dataclass(frozen=True)
class Mutation:
    name: str
    file: str
    old: str
    new: str
    tests: str  # a pytest -k expression: the tests that must catch it
    test_file: str | None = None  # when the catching tests live outside the suite's file


SUITES: dict[str, tuple[str, list[Mutation]]] = {
    "m2-slice6-step6": (
        "tests/test_bars_feed.py",
        [
            # --- the same-run split check (Kyle, 2026-11-18: every 15m write) ---
            Mutation(
                "the 15m run skips its own split check",
                FEED_PY,
                "            if fetch and interval == INTRADAY_15M:",
                "            if False:",
                "test_no_15m_bar_is_accepted_before_a_split",
            ),
            Mutation(
                "the split check fetches 15m instead of daily actions",
                FEED_PY,
                "                check = await client.fetch(list(fetch), DAILY, today=today, "
                "start=start)",
                "                check = await client.fetch(list(fetch), INTRADAY_15M, "
                "today=today, start=start)",
                "test_the_15m_backfill_checks_splits_first",
            ),
            Mutation(
                "the split check's events are not written",
                FEED_PY,
                "                    {p: [] for p in fetch},\n",
                "                    {},\n",
                "test_no_15m_bar_is_accepted_before_a_split",
            ),
            Mutation(
                "a symbol whose check failed still gets 15m bars",
                FEED_PY,
                "                fetch = {p: s for p, s in fetch.items() if p not in held}",
                "                fetch = dict(fetch)",
                "test_a_symbol_whose_split_check_fails",
            ),
            Mutation(
                "the second request takes no rate-budget slot",
                FEED_PY,
                "                await limiter.acquire(bars_store.PROVIDER)\n",
                "",
                "test_the_15m_backfill_checks_splits_first",
            ),
            # --- the poll's keyed start (rule 9) ---
            Mutation(
                "the poll starts after the last stored session",
                FEED_PY,
                "                    own = got[0]  # inclusive: the last stored session is fetch"
                "ed again",
                "                    own = got[0] + timedelta(days=1)",
                "test_the_daily_poll_starts_at_the_last_stored_session or test_the_15m_poll_starts",
            ),
            Mutation(
                "a symbol with no stored bar is polled anyway",
                FEED_PY,
                "                    if got is None:\n                        refused.append(",
                "                    if got is None and False:\n"
                "                        refused.append(",
                "test_a_symbol_with_no_stored_bar",
            ),
            Mutation(
                "every symbol's last bar is the newest of any symbol",
                STORE,
                '        select(t.symbol_id, func.max(t.effective_at).label("at"))\n'
                "        .where(t.symbol_id.in_(ids))\n"
                "        .group_by(t.symbol_id)\n",
                '        select(t.symbol_id, func.max(t.effective_at).over().label("at"))\n'
                "        .where(t.symbol_id.in_(ids))\n"
                "        .distinct()\n",
                "test_the_15m_poll_records_a_gap",
            ),
            Mutation(
                "daily bars count as 15m history",
                STORE,
                "    return BarDaily if interval == DAILY else BarIntraday",
                "    return BarDaily",
                "test_daily_bars_do_not_count_as_15m_history",
            ),
            Mutation(
                "15m re-fetches are compared with bars_daily",
                STORE,
                "    t = _table(interval)\n    instants",
                "    t = BarDaily\n    instants",
                "test_the_15m_poll_starts_at_its_last_stored_session_and_counts",
            ),
            # --- the gap record ---
            Mutation(
                "no gap is recorded beyond the look-back",
                FEED_PY,
                "                    if interval == INTRADAY_15M and own < floor:",
                "                    if False:",
                "test_the_15m_poll_records_a_gap",
            ),
            Mutation(
                "the gap starts at the last stored bar itself",
                FEED_PY,
                "                            got[1] + timedelta(minutes=15),",
                "                            got[1],",
                "test_the_15m_poll_records_a_gap",
            ),
            Mutation(
                "the gap ends a session late",
                FEED_PY,
                "s for s in calendar.sessions if s.session_date >= floor",
                "s for s in calendar.sessions if s.session_date > floor",
                "test_the_15m_poll_records_a_gap",
            ),
            # --- provenance and counts ---
            Mutation(
                "polled rows are labelled backfill",
                FEED_PY,
                "    feed_value = BACKFILL if mode == BACKFILL else POLL",
                "    feed_value = BACKFILL",
                "test_the_daily_poll_starts_at_the_last_stored_session",
            ),
            Mutation(
                "a batch reports none of its bars as written",
                FEED_PY,
                "                written += counts.rows_written\n                report.unsettled",
                "                report.unsettled",
                "test_the_daily_poll_starts_at_the_last_stored_session",
            ),
            Mutation(
                "not backfilled reads as an empty batch",
                BARS,
                "    if unbackfilled:\n        return NotBackfilled(text)\n",
                "",
                "test_a_symbol_with_no_stored_bar or test_run_failure_names",
            ),
            Mutation(
                "an unknown failure is dropped",
                BARS,
                "    other = sorted(f.ticker for f in failures if not isinstance(f.error, known))",
                "    other: list[str] = []",
                "test_run_failure_names",
            ),
            # --- the wait bound (MEASURED 2026-11-19: a 60 s bound refused the second slot) ---
            Mutation(
                "the wait bound is the floor, not two spacings",
                "app/ingest/budget.py",
                "    return max(floor_seconds, 2 * slowest)",
                "    return max(floor_seconds, slowest)",
                "test_a_second_slot_in_a_row or test_the_wait_bound",
                "tests/test_rate_budget.py",
            ),
            Mutation(
                "the bars limiter uses the default bound",
                FEED_PY,
                "        redis, settings.RATE_BUDGETS, max_wait_seconds=wait_bound(settings.RATE_B"
                "UDGETS)\n",
                "        redis, settings.RATE_BUDGETS\n",
                "test_the_wait_bound",
                "tests/test_rate_budget.py",
            ),
            # --- the schedule and health ---
            Mutation(
                "the polls are scheduled with the flag off",
                CELERY,
                "    if settings.bars_poll_enabled():",
                "    if True:",
                "test_beat_does_not_schedule_the_polls_unless_enabled",
            ),
            Mutation(
                "any non-empty flag enables the polls",
                SETTINGS,
                '    return os.environ.get("BARS_POLL_ENABLED", "").strip().lower() == "true"',
                '    return os.environ.get("BARS_POLL_ENABLED", "").strip() != ""',
                "test_beat_does_not_schedule_the_polls_unless_enabled",
            ),
            Mutation(
                "the polls run at weekends",
                CELERY,
                "            day_of_week=settings.BARS_POLL_DAYS,\n",
                "",
                "test_enabled_both_polls_run_at_0905",
            ),
            Mutation(
                "stale after three days",
                SETTINGS,
                "BARS_POLL_STALE_AFTER = timedelta(days=4)",
                "BARS_POLL_STALE_AFTER = timedelta(days=3)",
                "test_both_bars_feeds_go_stale_after_four_days",
            ),
        ],
    ),
    "m2-slice6-step5b": (
        "tests/test_bars_read.py",
        [
            # --- the distributions table (0013) ---
            Mutation(
                "the distributions key loses kind",
                M13,
                'sa.PrimaryKeyConstraint("symbol_id", "effective_at", "kind"),',
                'sa.PrimaryKeyConstraint("symbol_id", "effective_at"),',
                "test_distribution_key",
                "tests/test_price_tables.py",
            ),
            Mutation(
                "the distributions key loses symbol_id",
                M13,
                'sa.PrimaryKeyConstraint("symbol_id", "effective_at", "kind"),',
                'sa.PrimaryKeyConstraint("effective_at", "kind"),',
                "test_distribution_key",
                "tests/test_price_tables.py",
            ),
            Mutation(
                "any distribution kind is accepted",
                M13,
                "\"kind IN ('dividend', 'capital_gain')\"",
                '"true"',
                "test_distribution_checks",
                "tests/test_price_tables.py",
            ),
            Mutation(
                "a zero distribution is accepted",
                M13,
                '"amount > 0"',
                '"amount >= 0"',
                "test_distribution_checks",
                "tests/test_price_tables.py",
            ),
            Mutation(
                "distributions have no insert-only triggers",
                M13,
                '            f"CREATE TRIGGER distributions_no_{event.lower()} BEFORE {event} ON '
                'distributions "\n'
                '            f"FOR EACH {scope} EXECUTE FUNCTION insert_only_observation()"\n',
                '            "SELECT 1"\n',
                "test_no_stored_column_can_be_updated and distributions",
                "tests/test_price_tables.py",
            ),
            Mutation(
                "bar_total_return is not dropped",
                M13,
                '    op.drop_table("bar_total_return")\n',
                "",
                "test_bar_total_return_is_gone",
                "tests/test_price_tables.py",
            ),
            # --- the parser ---
            Mutation(
                "distributions are not read",
                BARS,
                "            if amount is not None and not pd.isna(amount) and amount != 0:",
                "            if amount is not None and not pd.isna(amount) and amount != 0 and "
                "False:",
                "test_a_dividend_before_a_split_is_stored_as_paid",
                "tests/test_yfinance_bars.py",
            ),
            Mutation(
                "distributions are read only from settled rows",
                BARS,
                "                frame_distributions[(day, kind)] = _decimal(amount)\n",
                "                if (sp := calendar.settle_point(day, interval)) is not None and "
                "sp <= now:\n"
                "                    frame_distributions[(day, kind)] = _decimal(amount)\n",
                "test_a_dividend_on_an_unsettled_session_is_still_read",
                "tests/test_yfinance_bars.py",
            ),
            Mutation(
                "an amount is not multiplied back by later splits",
                BARS,
                "                as_paid = provider_amount * factor",
                "                as_paid = provider_amount",
                "test_a_dividend_before_a_split_is_stored_as_paid",
                "tests/test_yfinance_bars.py",
            ),
            Mutation(
                "a disagreeing stored distribution is ignored",
                BARS,
                "            if stored_distributions[(ex_date, kind)] != as_paid:",
                "            if False:",
                "test_a_stored_distribution_that_agrees",
                "tests/test_yfinance_bars.py",
            ),
            Mutation(
                "an inexact amount is rounded",
                BARS,
                "            exact.traps[Inexact] = True",
                "            exact.traps[Inexact] = False",
                "test_a_rebuilt_amount_that_would_have_to_round",
                "tests/test_yfinance_bars.py",
            ),
            Mutation(
                "capital gains are not read",
                BARS,
                'DISTRIBUTION_COLUMNS = {"Dividends": "dividend", "Capital Gains": "capital_gain"}',
                'DISTRIBUTION_COLUMNS = {"Dividends": "dividend"}',
                "test_a_capital_gain_is_a_distribution",
                "tests/test_yfinance_bars.py",
            ),
            # --- the store ---
            Mutation(
                "a distribution is knowable at its ex-date's close",
                STORE,
                "                        # Decided 2026-11-18: knowable at its ex-date's open "
                "(late, never early).\n"
                "                        available_at=session.opens_at,",
                "                        available_at=session.closes_at,",
                "test_distributions_are_written_as_paid",
                "tests/test_bars_backfill.py",
            ),
            Mutation(
                "backfilled distributions are labelled poll",
                STORE,
                "                        feed=feed,\n"
                "                        # Decided 2026-11-18: knowable at its ex-date's open",
                '                        feed="poll",\n'
                "                        # Decided 2026-11-18: knowable at its ex-date's open",
                "test_distributions_are_written_as_paid",
                "tests/test_bars_backfill.py",
            ),
            # --- the read ---
            Mutation(
                "the distributions read loses its symbol filter",
                READ,
                "                    Distribution.symbol_id == symbol_id,\n",
                "",
                "test_a_second_symbols_distributions_never_reach_the_first",
            ),
            Mutation(
                "a distribution is applied before it was knowable",
                READ,
                "                    Distribution.available_at <= as_of,\n",
                "",
                "test_a_distribution_is_invisible_until_its_available_at",
            ),
            Mutation(
                "an ex-date at start is applied",
                READ,
                "                    Distribution.effective_at > start,",
                "                    Distribution.effective_at >= start,",
                "test_the_window_is_start_exclusive_end_inclusive",
            ),
            Mutation(
                "an ex-date at end is not applied",
                READ,
                "                    Distribution.effective_at <= end,",
                "                    Distribution.effective_at < end,",
                "test_the_window_is_start_exclusive_end_inclusive",
            ),
            Mutation(
                "strict mode silently drops a backfill-only distribution",
                READ,
                "        if not include_backfill and any(r.feed == BACKFILL for r in rows):",
                "        if False:",
                "test_strict_mode_refuses_a_window_with_a_backfill_only_distribution",
            ),
            Mutation(
                "an amount is not put on the basis at T",
                READ,
                "        amount_adj = r.amount / ratio_after",
                "        amount_adj = r.amount",
                "test_the_total_return_is_a_ratio_by_yahoos_convention",
            ),
            Mutation(
                "a split on the ex-date itself adjusts the amount",
                READ,
                "            if effective > r.effective_at:",
                "            if effective >= r.effective_at:",
                "test_an_ex_date_on_the_split_day",
            ),
            Mutation(
                "CRSP's convention instead of Yahoo's",
                READ,
                "        ratio /= a.factor",
                "        ratio *= 2 - a.factor",
                "test_the_total_return_is_a_ratio_by_yahoos_convention",
            ),
            Mutation(
                "a distribution with no bar before it is applied anyway",
                READ,
                "        if not isinstance(prev, Bar):",
                "        if prev is None:",
                "test_a_distribution_whose_previous_session_has_no_bar",
            ),
        ],
    ),
    "m2-slice6-step5": (
        "tests/test_bars_read.py",
        [
            # --- rule 9: every keyed read keeps to its symbol ---
            Mutation(
                "the bars read loses its symbol filter",
                READ,
                "        .where(t.symbol_id == symbol_id, t.available_at <= as_of)",
                "        .where(t.available_at <= as_of)",
                "test_latest_bar_is_this_symbols_newest",
            ),
            Mutation(
                "the splits read loses its symbol filter",
                READ,
                "            Split.symbol_id == symbol_id, Split.available_at <= as_of",
                "            Split.available_at <= as_of",
                "test_adjustment_applies_only_splits_knowable_at_t",
            ),
            # --- the point-in-time rule ---
            Mutation(
                "bars are filtered on effective_at, not available_at",
                READ,
                "        .where(t.symbol_id == symbol_id, t.available_at <= as_of)",
                "        .where(t.symbol_id == symbol_id, t.effective_at <= as_of)",
                "test_a_bar_is_invisible_until_its_settle_point",
            ),
            Mutation(
                "a split is applied before it was knowable",
                READ,
                "            Split.symbol_id == symbol_id, Split.available_at <= as_of",
                "            Split.symbol_id == symbol_id",
                "test_adjustment_applies_only_splits_knowable_at_t",
            ),
            Mutation(
                "latest_bar_as_of returns the oldest bar",
                READ,
                "                .order_by(t.effective_at.desc())",
                "                .order_by(t.effective_at)",
                "test_latest_bar_is_this_symbols_newest",
            ),
            # --- strict mode ---
            Mutation(
                "strict mode is ignored",
                READ,
                "        query = query.where(BarIntraday.interval == INTRADAY_15M)\n"
                "    if not include_backfill:\n"
                "        query = query.where(t.feed != BACKFILL)\n",
                "        query = query.where(BarIntraday.interval == INTRADAY_15M)\n",
                "test_strict_mode_sees_only_poll_rows",
            ),
            Mutation(
                "strict mode's record starts at backfill rows",
                READ,
                "        .where(t.symbol_id == symbol_id)\n"
                "    )\n"
                "    if not include_backfill:\n"
                "        query = query.where(t.feed != BACKFILL)\n",
                "        .where(t.symbol_id == symbol_id)\n    )\n",
                "test_each_way_to_have_no_answer and BEFORE_RECORD",
            ),
            # --- adjusted on read (D3 b) ---
            Mutation(
                "a split adjusts its own session",
                READ,
                "        if effective > row.session_date:",
                "        if effective >= row.session_date:",
                "test_a_split_does_not_adjust_its_own_session",
            ),
            Mutation(
                "adjusted price multiplies instead of divides",
                READ,
                "        c_adj=row.c_raw / factor,",
                "        c_adj=row.c_raw * factor,",
                "test_adjustment_applies_only_splits_knowable_at_t",
            ),
            Mutation(
                "adjusted volume divides instead of multiplies",
                READ,
                "        v_adj=Decimal(row.v_raw) * factor,",
                "        v_adj=Decimal(row.v_raw) / factor,",
                "test_adjustment_applies_only_splits_knowable_at_t",
            ),
            # --- the reasons ---
            Mutation(
                "BEFORE_RECORD is never given",
                READ,
                "    if first is not None and as_of < first[1]:",
                "    if False:",
                "test_each_way_to_have_no_answer and BEFORE_RECORD",
            ),
            Mutation(
                "BACKFILL_EXCLUDED is decided after PREDATES_RECORD",
                READ,
                "    if stored is not None and not include_backfill and stored.feed == BACKFILL:",
                "    if False:",
                "test_each_way_to_have_no_answer and BACKFILL_EXCLUDED",
            ),
            Mutation(
                "PREDATES_RECORD is never given",
                READ,
                "    if first is not None and session_date < first[0]:",
                "    if False:",
                "test_each_way_to_have_no_answer and PREDATES_RECORD",
            ),
        ],
    ),
    "m2-slice6-step4": (
        "tests/test_bars_backfill.py",
        [
            # --- rule 9: the re-fetch comparison is keyed by symbol ---
            Mutation(
                "the stored-bar comparison loses its symbol filter",
                STORE,
                "        t.symbol_id == symbol_id, t.effective_at.in_(instants)\n",
                "        t.effective_at.in_(instants)\n",
                "test_a_price_difference_fails_its_symbol",
            ),
            Mutation(
                "a re-fetched price that differs is accepted (D3)",
                STORE,
                "                and stored_raw[b.effective_at][:n_price]\n"
                "                != tuple(getattr(b, f) for f in PRICE_FIELDS)",
                "                and False",
                "test_a_price_difference_fails_its_symbol",
            ),
            Mutation(
                "a volume-only difference fails the symbol (the frozen-symbol bug)",
                STORE,
                'PRICE_FIELDS = ("o_raw", "h_raw", "l_raw", "c_raw")',
                'PRICE_FIELDS = ("o_raw", "h_raw", "l_raw", "c_raw", "v_raw")',
                "test_a_price_difference_fails_its_symbol or test_a_volume_revision_alone",
            ),
            Mutation(
                "the seeded universe drifts from the recorded fixture",
                "app/settings.py",
                '    "ARKK",\n',
                '    "XLC",\n',
                "test_the_universe_fixture or test_the_recorders_universe",
                "tests/test_fixture_universe.py",
            ),
            Mutation(
                "a volume revision is not counted",
                STORE,
                "            if revised:\n",
                "            if False:\n",
                "test_a_volume_revision_alone",
            ),
            # --- idempotence ---
            Mutation(
                "bars_daily insert without ON CONFLICT DO NOTHING",
                STORE,
                "                .on_conflict_do_nothing()\n                .returning(table.effe"
                "ctive_at)",
                "                .returning(table.effective_at)",
                "test_a_second_run_writes_zero_rows",
            ),
            # --- what is written ---
            Mutation(
                "backfilled rows are labelled poll",
                FEED_PY,
                "    feed_value = BACKFILL if mode == BACKFILL else POLL",
                "    feed_value = POLL",
                "test_bars_and_splits_are_written",
            ),
            Mutation(
                "a split is knowable at its effective close",
                STORE,
                "                        # Approved 2026-11-18: knowable at the open of its "
                "effective session.\n"
                "                        available_at=session.opens_at,",
                "                        available_at=session.closes_at,",
                "test_bars_and_splits_are_written",
            ),
            # --- coverage ---
            Mutation(
                "a young symbol is not reported as young",
                STORE,
                "first > window_first, missing))",
                "False, missing))",
                "test_coverage_reports",
            ),
            Mutation(
                "a hole inside a span is not reported",
                STORE,
                "        missing = tuple(d for d in expected if first <= d <= last and d not in p"
                "resent)",
                "        missing: tuple[date, ...] = ()",
                "test_coverage_reports",
            ),
            # --- batches and D4 ---
            Mutation(
                "every symbol in one batch, past max_batch",
                FEED_PY,
                "    batches = [symbols[i : i + max_batch] for i in range(0, len(symbols), max_ba"
                "tch)]",
                "    batches = [symbols]",
                "test_a_dead_symbol_fails_the_run_by_name",
            ),
            Mutation(
                "likely_gone is judged within the batch only",
                FEED_PY,
                "                    engine, [m.symbol_id for m in symbols], interval\n",
                "                    engine, list(fetch.values()), interval\n",
                "test_likely_gone_is_judged_against_every_symbol",
            ),
            Mutation(
                "a failed batch does not fail the run",
                FEED_PY,
                "    if errors:\n        # DECISION",
                "    if False:\n        # DECISION",
                "test_a_dead_symbol_fails_the_run_by_name",
            ),
        ],
    ),
    "m2-slice6-step3": (
        "tests/test_yfinance_bars.py",
        [
            # --- rule 9: values keyed by ticker ---
            Mutation(
                "every symbol gets every symbol's known splits",
                BARS,
                "                known_splits.get(ticker, ()),",
                "                [s for v in known_splits.values() for s in v],",
                "test_a_known_split_applies_to_its_own_symbol_only",
            ),
            Mutation(
                "likely_gone reads the oldest last bar of any symbol",
                BARS,
                "        last = last_stored.get(ticker)",
                "        last = min(v for v in last_stored.values() if v)",
                "test_likely_gone_reads_each_symbols_own_last_bar",
            ),
            # --- the settle point (S, MEASURED 2026-11-18) ---
            Mutation(
                "daily settles on D+1",
                BARS,
                "SETTLE_SESSIONS_AFTER = {DAILY: 2, INTRADAY_15M: 1}",
                "SETTLE_SESSIONS_AFTER = {DAILY: 1, INTRADAY_15M: 1}",
                "test_daily_bars_settle",
            ),
            Mutation(
                "15m settles on its own session",
                BARS,
                "SETTLE_SESSIONS_AFTER = {DAILY: 2, INTRADAY_15M: 1}",
                "SETTLE_SESSIONS_AFTER = {DAILY: 2, INTRADAY_15M: 0}",
                "test_intraday_bars_settle",
            ),
            Mutation(
                "a bar is stored a moment before its settle point",
                BARS,
                "        if settle is None or settle > now:",
                "        if settle is None or settle >= now + timedelta(seconds=1):",
                "test_one_microsecond_before",
            ),
            Mutation(
                "the clock no longer excludes unsettled bars",
                BARS,
                "        if settle is None or settle > now:",
                "        if settle is None:",
                "test_daily_bars_settle",
            ),
            Mutation(
                "an unsettled row's contents are judged before the clock drops it",
                BARS,
                "        settle = calendar.settle_point(day, interval)\n"
                "        if settle is None or settle > now:\n"
                "            result.unsettled += 1\n"
                "            continue\n"
                "        if any(pd.isna(v) for v in prices):\n"
                '            raise IncompleteBar(f"{ticker}: a bar has missing fields")\n',
                "        if any(pd.isna(v) for v in prices):\n"
                '            raise IncompleteBar(f"{ticker}: a bar has missing fields")\n'
                "        settle = calendar.settle_point(day, interval)\n"
                "        if settle is None or settle > now:\n"
                "            result.unsettled += 1\n"
                "            continue\n",
                "test_an_unsettled_row_is_dropped_by_the_clock",
            ),
            Mutation(
                "bars outside an early close are kept",
                BARS,
                "                or key + INTERVAL_LENGTH[interval] > session.closes_at",
                "                or key > session.closes_at",
                "test_intraday_bars_outside",
            ),
            # --- as traded (D3) ---
            Mutation(
                "a split applies to its own session too",
                BARS,
                "        if s.effective_at > session_date:",
                "        if s.effective_at >= session_date:",
                "test_xlk_rebuilds",
            ),
            Mutation(
                "splits are read only from settled rows",
                BARS,
                "        if ratio is not None and not pd.isna(ratio) and ratio != 0:",
                "        if ratio is not None and not pd.isna(ratio) and ratio != 0 and False:",
                "test_a_split_on_an_unsettled",
            ),
            Mutation(
                "a disagreeing split is ignored",
                BARS,
                "        if d in stored and stored[d] != ratio:",
                "        if False:",
                "test_a_stored_split_that_disagrees",
            ),
            Mutation(
                "a non-whole rebuilt volume is truncated",
                BARS,
                "        if raw_volume != raw_volume.to_integral_value():",
                "        if False:",
                "test_a_rebuilt_volume_that_is_not_whole",
            ),
            # --- shape ---
            Mutation(
                "Capital Gains is not a known column",
                BARS,
                'ACTION_COLUMNS = frozenset({"Dividends", "Stock Splits", "Capital Gains"})',
                'ACTION_COLUMNS = frozenset({"Dividends", "Stock Splits"})',
                "test_capital_gains",
            ),
            Mutation(
                "any extra column is accepted",
                BARS,
                "    extra = cols - set(PRICE_COLUMNS) - ACTION_COLUMNS",
                "    extra: set[str] = set()",
                "test_an_unknown_column",
            ),
            Mutation(
                "a naive intraday index is accepted",
                BARS,
                '    if getattr(index, "tz", None) is None:',
                "    if False:",
                "test_a_naive_intraday",
            ),
            Mutation(
                "a daily date that is not a session is accepted",
                BARS,
                "        if calendar.get(d) is None:",
                "        if False:",
                "test_a_daily_date_that_is_not_a_session",
            ),
            Mutation(
                "a missing field is not a failure",
                BARS,
                "        if any(pd.isna(v) for v in prices):",
                "        if False:",
                "test_a_bar_with_a_missing_field",
            ),
            # --- duplicates (D5 iv) ---
            Mutation(
                "a differing duplicate: last write wins",
                BARS,
                "            if rows[key] != stamp:",
                "            if False:",
                "test_a_differing_duplicate",
            ),
            Mutation(
                "an identical duplicate fails the symbol",
                BARS,
                "            continue  # an identical duplicate is deduplicated (D5 iv)",
                '            raise DuplicateBar("identical")',
                "test_an_identical_duplicate",
            ),
            # --- yfinance's swallowed errors ---
            Mutation(
                "a rate limit is read as transient",
                BARS,
                '            raise RateLimited("yfinance rate-limited the batch") from None',
                '            raise Transient("yfinance rate-limited the batch") from None',
                "test_a_rate_limit_or_network_error_on_any_ticker_fails_the_batch and RateLimit",
            ),
            Mutation(
                "an unknown error class is counted as a dead symbol",
                BARS,
                '        raise Transient("yfinance could not fetch part of the batch") from None',
                "        return None",
                "test_a_rate_limit_or_network_error_on_any_ticker_fails_the_batch and Timeout",
            ),
            # --- D4 ---
            Mutation(
                "likely_gone needs more than 5 sessions",
                BARS,
                "        if behind >= LIKELY_GONE_SESSIONS:",
                "        if behind > LIKELY_GONE_SESSIONS:",
                "test_likely_gone_at_five",
            ),
            Mutation(
                "broken data no longer comes first",
                BARS,
                "    if broken or other:\n        return SymbolsFailed(text)",
                "    if broken or other:\n        return DeadSymbols(text)",
                "test_the_run_names_every_failed_symbol",
            ),
            # --- the client ---
            Mutation(
                "the 15m start limit is 58 days",
                CLIENT,
                "            and start < today - timedelta(days=INTRADAY_START_LIMIT_DAYS)",
                "            and start <= today - timedelta(days=INTRADAY_START_LIMIT_DAYS)",
                "test_a_15m_start_beyond_59_days",
                "tests/test_yfinance_client.py",
            ),
            Mutation(
                "downloads are threaded again (the cold-cache lock)",
                CLIENT,
                "        threads=False,\n",
                "        threads=True,\n",
                "test_the_real_call_shape",
                "tests/test_yfinance_client.py",
            ),
            Mutation(
                "intraday asks for actions",
                CLIENT,
                "                actions=interval == DAILY,",
                "                actions=True,",
                "test_daily_asks_for_actions",
                "tests/test_yfinance_client.py",
            ),
            Mutation(
                "a library rate limit becomes transient",
                CLIENT,
                '        return RateLimited("yfinance reported a rate limit")',
                '        return Transient("yfinance reported a rate limit")',
                "test_library_exceptions_become_ours and rate_limit",
                "tests/test_yfinance_client.py",
            ),
            Mutation(
                "the library exception is chained (its text travels)",
                CLIENT,
                "            raise mapped from None",
                "            raise mapped from exc",
                "test_library_exceptions_become_ours",
                "tests/test_yfinance_client.py",
            ),
            Mutation(
                "auto_adjust is left to the library default",
                CLIENT,
                "        auto_adjust=False,\n",
                "",
                "test_the_real_call_shape",
                "tests/test_yfinance_client.py",
            ),
            Mutation(
                "the yfinance budget is not one batch a minute",
                "app/settings.py",
                '"yfinance": RateBudget(min_interval_seconds=60.0, max_batch=24),',
                '"yfinance": RateBudget(min_interval_seconds=900.0, max_batch=24),',
                "test_the_yfinance_budget",
                "tests/test_yfinance_client.py",
            ),
            # --- nothing the library says is stored (Kyle, 2026-11-18) ---
            Mutation(
                "run_error stores any exception's text",
                "app/ingest/errors.py",
                "    if not isinstance(exc, ProviderError):\n        return health_error(exc)\n",
                "",
                "test_nothing_a_library_says_is_ever_stored",
                "tests/test_ingest_errors.py",
            ),
            # --- the seed: insert-only, per symbol ---
            Mutation(
                "a disagreeing mapping is not reported",
                "app/services/reference_seed.py",
                "            elif have != wanted_symbol:",
                "            elif False:",
                "test_a_disagreeing_mapping",
                "tests/test_reference_tables.py",
            ),
            Mutation(
                "the seed inserts a mapping even when one is active",
                "app/services/reference_seed.py",
                "            if have is None:",
                "            if True:",
                "test_seed_maps_every_symbol",
                "tests/test_reference_tables.py",
            ),
            # --- the licence gate (IDEA-027 addendum 2026-11-18) ---
            Mutation(
                "peewee's exception is not keyed to its version",
                "tools/license_audit.py",
                "VERSION_EXCEPTIONS.get((name.lower(), version), frozenset())",
                'VERSION_EXCEPTIONS.get((name.lower(), "4.5.3"), frozenset())',
                "test_peewee_unknown",
                "tests/test_license_audit.py",
            ),
        ],
    ),
    "heartbeat-clock": (
        "tests/test_task_heartbeat.py",
        [
            Mutation(
                "heartbeat held at the maximum (GREATEST)",
                "app/tasks/runs.py",
                ".values(heartbeat_at=now)",
                ".values(heartbeat_at=func.greatest(TaskRun.heartbeat_at, now))",
                "test_a_clock_step_backwards",
            ),
        ],
    ),
    "m2-slice6-step2": (
        "tests/test_price_tables.py",
        [
            # --- keys (rule 9) ---
            Mutation(
                "bars_daily key loses symbol_id",
                M10,
                'sa.PrimaryKeyConstraint("symbol_id", "effective_at"),\n'
                '        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"]),\n'
                "        _run_fk(),\n"
                "        *_bar_checks(),\n"
                '        comment=TABLE_COMMENTS["bars_daily"],',
                'sa.PrimaryKeyConstraint("effective_at"),\n'
                '        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"]),\n'
                "        _run_fk(),\n"
                "        *_bar_checks(),\n"
                '        comment=TABLE_COMMENTS["bars_daily"],',
                "test_bar_key_is_per_symbol and bars_daily",
            ),
            # NOT HERE, on purpose: bars_intraday's key losing `interval`. Only '15m' is allowed, so
            # no test can collide two intervals. Whoever widens ck_bars_intraday_interval adds that
            # test and this mutation in the same commit (BUILD-LOG, 2026-11-18).
            Mutation(
                "bars_intraday key loses symbol_id",
                M10,
                'sa.PrimaryKeyConstraint("symbol_id", "interval", "effective_at"),',
                'sa.PrimaryKeyConstraint("interval", "effective_at"),',
                "test_bar_key_is_per_symbol and bars_intraday",
            ),
            Mutation(
                "splits key loses symbol_id",
                M10,
                'sa.PrimaryKeyConstraint("symbol_id", "effective_at"),\n'
                '        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"]),\n'
                "        _run_fk(),\n"
                '        sa.CheckConstraint("ratio > 0"',
                'sa.PrimaryKeyConstraint("effective_at"),\n'
                '        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"]),\n'
                "        _run_fk(),\n"
                '        sa.CheckConstraint("ratio > 0"',
                "test_splits_key",
            ),
            Mutation(
                "bar_gaps uniqueness loses interval",
                M11,
                'sa.UniqueConstraint("symbol_id", "interval", "missing_from"),',
                'sa.UniqueConstraint("symbol_id", "missing_from"),',
                "test_gap_uniqueness",
            ),
            Mutation(
                "bar_gaps uniqueness loses symbol_id",
                M11,
                'sa.UniqueConstraint("symbol_id", "interval", "missing_from"),',
                'sa.UniqueConstraint("interval", "missing_from"),',
                "test_gap_uniqueness",
            ),
            Mutation(
                "bar_gaps has no uniqueness at all",
                M11,
                'sa.UniqueConstraint("symbol_id", "interval", "missing_from"),',
                "",
                "test_gap_uniqueness",
            ),
            # --- CHECKs ---
            Mutation(
                "raw_basis check accepts anything",
                M10,
                "\"raw_basis IN ('live', 'reconstructed', 'unreconstructed')\"",
                '"true"',
                "test_bar_checks_refuse_bad_values and raw_basis",
            ),
            Mutation(
                "feed check accepts anything",
                M10,
                "sa.CheckConstraint(\"feed IN ('poll', 'backfill')\"",
                'sa.CheckConstraint("true"',
                "test_bar_checks_refuse_bad_values and feed",
            ),
            Mutation(
                "feed check is case-insensitive",
                M10,
                "sa.CheckConstraint(\"feed IN ('poll', 'backfill')\"",
                "sa.CheckConstraint(\"lower(feed) IN ('poll', 'backfill')\"",
                "test_bar_checks_refuse_bad_values and feed",
            ),
            Mutation(
                "venue_scope check accepts consolidated",
                M10,
                "\"venue_scope IN ('provider_default')\"",
                "\"venue_scope IN ('provider_default', 'consolidated')\"",
                "test_bar_checks_refuse_bad_values and venue_scope",
            ),
            Mutation(
                "a bar may be available at its own open",
                M10,
                'sa.CheckConstraint("available_at > effective_at", name="settled_after_open"),\n'
                "    ]",
                'sa.CheckConstraint("available_at >= effective_at", name="settled_after_open"),\n'
                "    ]",
                "test_bar_checks_refuse_bad_values and available_at",
            ),
            Mutation(
                "intraday interval check accepts anything",
                M10,
                'sa.CheckConstraint("interval IN (\'15m\')", name="interval"),',
                'sa.CheckConstraint("true", name="interval"),',
                "test_intraday_interval",
            ),
            Mutation(
                "split ratio may be zero",
                M10,
                '"ratio > 0"',
                '"ratio >= 0"',
                "test_split_ratio",
            ),
            Mutation(
                "gap interval accepts anything",
                M11,
                "\"interval IN ('1d', '15m')\"",
                '"true"',
                "test_gap_checks",
            ),
            Mutation(
                "gap range may be empty",
                M11,
                '"missing_to > missing_from"',
                '"missing_to >= missing_from"',
                "test_gap_checks",
            ),
            Mutation(
                "gap may be resolved before it was recorded",
                M11,
                '"resolved_at IS NULL OR resolved_at >= recorded_at"',
                '"true"',
                "test_gap_checks",
            ),
            Mutation(
                "close_reason check is one-way (closed needs a reason)",
                M12,
                '"(valid_to IS NULL) = (close_reason IS NULL)"',
                '"valid_to IS NULL OR close_reason IS NOT NULL"',
                "test_close_reason",
            ),
            Mutation(
                "close_reason check is one-way (a reason needs closed)",
                M12,
                '"(valid_to IS NULL) = (close_reason IS NULL)"',
                '"close_reason IS NULL OR valid_to IS NOT NULL"',
                "test_close_reason",
            ),
            Mutation(
                "close_reason accepts any value",
                M12,
                "\"close_reason IN ('delisted', 'merged', 'removed_by_operator')\"",
                '"true"',
                "test_close_reason",
            ),
            # --- insert-only triggers ---
            Mutation(
                "no UPDATE trigger on the observation tables",
                M10,
                TRIGGER_LOOP,
                'for event, scope in (("DELETE", "ROW"), ("TRUNCATE", "STATEMENT")):',
                "test_no_stored_column_can_be_updated",
            ),
            Mutation(
                "no DELETE trigger on the observation tables",
                M10,
                TRIGGER_LOOP,
                'for event, scope in (("UPDATE", "ROW"), ("TRUNCATE", "STATEMENT")):',
                "test_rows_cannot_be_deleted",
            ),
            Mutation(
                "no TRUNCATE trigger on the observation tables",
                M10,
                TRIGGER_LOOP,
                'for event, scope in (("UPDATE", "ROW"), ("DELETE", "ROW")):',
                "test_rows_cannot_be_deleted",
            ),
            Mutation(
                "the trigger ignores c_raw",
                M10,
                "AND (to_jsonb(NEW) - 'ingestion_run_id') = (to_jsonb(OLD) - 'ingestion_run_id')",
                "AND (to_jsonb(NEW) - 'ingestion_run_id' - 'c_raw')"
                " = (to_jsonb(OLD) - 'ingestion_run_id' - 'c_raw')",
                "test_no_stored_column_can_be_updated and c_raw",
            ),
            Mutation(
                "the run pointer may be repointed",
                M10,
                "OR NEW.ingestion_run_id IS NOT DISTINCT FROM OLD.ingestion_run_id)\n"
                "            THEN\n"
                "                RETURN NEW;\n"
                "            END IF;\n"
                "            RAISE EXCEPTION\n"
                "                '% is insert-only",
                "OR true)\n"
                "            THEN\n"
                "                RETURN NEW;\n"
                "            END IF;\n"
                "            RAISE EXCEPTION\n"
                "                '% is insert-only",
                "test_the_run_pointer and not bar_gaps",
            ),
            Mutation(
                "no carve-out: a purged run cannot be deleted",
                M10,
                "AND (to_jsonb(NEW) - 'ingestion_run_id') = (to_jsonb(OLD) - 'ingestion_run_id')",
                "AND to_jsonb(NEW) = to_jsonb(OLD)",
                "test_the_run_pointer and not bar_gaps",
            ),
            Mutation(
                "purged runs are blocked (no ON DELETE SET NULL)",
                M10,
                '["ingestion_run_id"], ["ingestion_runs.id"], ondelete="SET NULL")',
                '["ingestion_run_id"], ["ingestion_runs.id"])',
                "test_the_run_pointer and not bar_gaps",
            ),
            Mutation(
                "bar_gaps may be re-resolved or reopened",
                M11,
                "OR (OLD.resolved_at IS NULL AND NEW.resolved_at IS NOT NULL))",
                "OR true)",
                "test_a_gap_is_resolved_once",
            ),
            Mutation(
                "bar_gaps trigger ignores missing_to",
                M11,
                "AND (to_jsonb(NEW) - 'resolved_at' - 'ingestion_run_id')\n"
                "                   = (to_jsonb(OLD) - 'resolved_at' - 'ingestion_run_id')",
                "AND (to_jsonb(NEW) - 'resolved_at' - 'ingestion_run_id' - 'missing_to')\n"
                "                   = (to_jsonb(OLD) - 'resolved_at' - 'ingestion_run_id'"
                " - 'missing_to')",
                "test_nothing_else_about_a_gap and missing_to",
            ),
            Mutation(
                "bar_gaps has no DELETE trigger",
                M11,
                TRIGGER_LOOP,
                'for event, scope in (("UPDATE", "ROW"), ("TRUNCATE", "STATEMENT")):',
                "test_a_gap_cannot_be_deleted",
            ),
            Mutation(
                "bar_gaps run pointer may be repointed",
                M11,
                "OR NEW.ingestion_run_id IS NOT DISTINCT FROM OLD.ingestion_run_id)",
                "OR true)",
                "test_the_run_pointer and bar_gaps",
            ),
            # --- hypertables, indexes, comments ---
            Mutation(
                "daily chunk interval is 7 days",
                M10,
                'HYPERTABLES = {"bars_daily": "365 days", "bars_intraday": "28 days"}',
                'HYPERTABLES = {"bars_daily": "7 days", "bars_intraday": "28 days"}',
                "test_hypertables",
            ),
            Mutation(
                "intraday chunk interval is 7 days",
                M10,
                'HYPERTABLES = {"bars_daily": "365 days", "bars_intraday": "28 days"}',
                'HYPERTABLES = {"bars_daily": "365 days", "bars_intraday": "7 days"}',
                "test_hypertables",
            ),
            Mutation(
                "TimescaleDB default indexes come back",
                M10,
                "INTERVAL '{chunk}', create_default_indexes => false)",
                "INTERVAL '{chunk}', create_default_indexes => true)",
                "test_the_only_indexes",
            ),
            Mutation(
                "a time column's comment no longer says it is one",
                M10,
                '"TIME COLUMN. The instant the bar opened, in UTC',
                '"The instant the bar opened, in UTC',
                "test_every_column_and_the_table_carry_a_comment and bars_intraday",
            ),
            Mutation(
                "the daily settle point is not stated",
                M10,
                "the second session after this one (D+2; S measured 2026-11-18)",
                "the second session after this one (S measured 2026-11-18)",
                "test_the_settle_points_are_stated",
            ),
        ],
    ),
}


def pytest(cwd: Path, test_file: str, selector: str | None) -> tuple[int, str]:
    """pytest's exit code and its summary line."""
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", test_file]
    if selector:
        cmd += ["-k", selector]
    done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    lines = [ln for ln in done.stdout.splitlines() if ln.strip()]
    return done.returncode, lines[-1] if lines else ""


def caught(code: int, summary: str) -> bool:
    # Exit 1 with a FAILED test and no ERROR. An error (a fixture, i.e. the migration itself, broke)
    # would also exit 1, and would "catch" the mutation for the wrong reason.
    return code == 1 and "failed" in summary and "error" not in summary


def main(suite: str) -> int:
    test_file, mutations = SUITES[suite]
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "clean"
        shutil.copytree(BACKEND, base, ignore=shutil.ignore_patterns("__pycache__", ".*"))
        files = sorted({test_file, *(m.test_file for m in mutations if m.test_file)})
        for f in files:
            code, summary = pytest(base, f, None)
            if code != 0:
                print(f"FAIL: the unmutated {f} does not pass (pytest exit {code}: {summary})")
                return 1
        print(f"baseline: {', '.join(files)} pass unmutated")
        survived = 0
        for i, m in enumerate(mutations, 1):
            work = Path(tmp) / f"m{i}"
            shutil.copytree(base, work)
            target = work / m.file
            src = target.read_text(encoding="utf-8")
            if src.count(m.old) != 1:
                print(f"FAIL: '{m.name}': snippet occurs {src.count(m.old)} times in {m.file}")
                return 1
            target.write_text(src.replace(m.old, m.new), encoding="utf-8")
            code, summary = pytest(work, m.test_file or test_file, m.tests)
            ok = caught(code, summary)
            verdict = "caught" if ok else f"SURVIVED (pytest exit {code})"
            survived += not ok
            print(f"  {i:2}. {verdict:24} {m.name}  [-k {m.tests}]  {summary.strip('= ')}")
            shutil.rmtree(work)
        print(f"{len(mutations) - survived}/{len(mutations)} mutations caught")
        return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "m2-slice6-step2"))
