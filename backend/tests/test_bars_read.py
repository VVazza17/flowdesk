"""M2 slice 6 step 5: the point-in-time read of bars (app.services.bars_read).

A seeded scratch database (conftest `scratch_url`) with XNYS sessions; rows are written directly so
each scenario is exact. Rule 9: a SECOND symbol, XLE, carries rows that would WIN every ordering the
read applies: a later session, a higher total-return revision on the same instant, a split of its
own, larger values. Every test reads XLK and asserts nothing of XLE's reached it. Mutation-checked:
`bash infra/mutation-check.sh m2-slice6-step5`.
"""

import asyncio
import inspect
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.ingest.bars import DAILY, INTRADAY_15M, NEW_YORK
from app.services import bars_read
from app.services.bars_read import Bar, Missing, Reason
from app.services.reference_seed import seed_reference
from app.services.sessions import sync_sessions
from tests.yfixtures import nyse

D1, D2, D3, D4, D5 = (date(2025, 12, d) for d in (1, 2, 3, 4, 5))
GAP = date(2025, 12, 8)  # a session with no stored bar, inside the span (D9 is stored)
D9 = date(2025, 12, 9)


def settle(d: date, k: int = 2) -> datetime:
    point = nyse().settle_point(d, DAILY if k == 2 else INTRADAY_15M)
    assert point is not None
    return point


def opens(d: date) -> datetime:
    s = nyse().get(d)
    assert s is not None
    return s.opens_at


def run(url: str, fn: Any) -> Any:
    async def go() -> Any:
        engine = create_async_engine(url)
        try:
            return await fn(engine)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def sql(url: str, statement: str, **params: Any) -> None:
    async def fn(engine: AsyncEngine) -> None:
        async with engine.begin() as conn:
            await conn.execute(text(statement), params)

    run(url, fn)


def put_bar(url: str, ticker: str, d: date, close: str, *, feed: str = "poll", vol: int = 1000,
            available_at: datetime | None = None) -> None:  # fmt: skip
    sql(
        url,
        "INSERT INTO bars_daily (symbol_id, effective_at, o_raw, h_raw, l_raw, c_raw, v_raw, "
        "raw_basis, source, feed, venue_scope, available_at) SELECT id, :e, :c, :c, :c, :c, :v, "
        "'live', 'yfinance', :f, 'provider_default', :a FROM symbols WHERE ticker = :t",
        t=ticker, e=opens(d), c=Decimal(close), v=vol, f=feed, a=available_at or settle(d),
    )  # fmt: skip


def put_dist(
    url: str,
    ticker: str,
    d: date,
    amount: str,
    *,
    kind: str = "dividend",
    feed: str = "poll",
    available_at: datetime | None = None,
) -> None:
    sql(
        url,
        "INSERT INTO distributions (symbol_id, effective_at, kind, amount, raw_basis, feed, "
        "available_at, source) SELECT id, :d, :k, :a, 'live', :f, :t_at, 'yfinance' "
        "FROM symbols WHERE ticker = :t",
        t=ticker, d=d, k=kind, a=Decimal(amount), f=feed, t_at=available_at or opens(d),
    )  # fmt: skip


def put_split(url: str, ticker: str, d: date, ratio: str) -> None:
    sql(
        url,
        "INSERT INTO splits (symbol_id, effective_at, ratio, available_at, source) "
        "SELECT id, :d, :r, :a, 'yfinance' FROM symbols WHERE ticker = :t",
        t=ticker, d=d, r=Decimal(ratio), a=opens(d),
    )  # fmt: skip


@pytest.fixture
def db(scratch_url: str) -> str:
    async def prepare(engine: AsyncEngine) -> None:
        await seed_reference(engine)
        await sync_sessions(engine, ["XNYS"], date(2025, 11, 1), date(2026, 1, 31))

    run(scratch_url, prepare)
    return scratch_url


@pytest.fixture
def world(db: str) -> str:
    """XLK: backfill D1..D3, poll D4, D5, D9 (D8 a gap); a 2:1 split effective D5 (so D1..D4
    adjust);
    dividends ex D3 (2.04 as paid, backfill) and ex D5 (1.03, poll, on the split day). XLE (the
    second key): later sessions, its own 10:1 split effective D2, dividends of 999 ex D2 and D3,
    larger values everywhere."""
    for d, c in ((D1, "200"), (D2, "202"), (D3, "204")):
        put_bar(db, "XLK", d, c, feed="backfill")
    for d, c in ((D4, "206"), (D5, "104"), (D9, "105")):
        put_bar(db, "XLK", d, c)
    put_split(db, "XLK", D5, "2")
    put_dist(db, "XLK", D3, "2.04", feed="backfill")
    put_dist(db, "XLK", D5, "1.03")
    for d in (D1, D2, D3, D4, D5, GAP, D9, date(2025, 12, 10), date(2025, 12, 11)):
        put_bar(db, "XLE", d, "9999", vol=99_999_999)
    put_split(db, "XLE", D2, "10")
    put_dist(db, "XLE", D2, "999")
    put_dist(db, "XLE", D3, "999")
    return db


def read(url: str, fn: str, *args: Any, **kw: Any) -> Any:
    async def go(engine: AsyncEngine) -> Any:
        return await getattr(bars_read, fn)(engine, *args, **kw)

    return run(url, go)


LATE = datetime(2026, 1, 31, 12, tzinfo=NEW_YORK)  # everything above has settled


# --- the signature: both keywords required at every call site -------------------------------------


@pytest.mark.parametrize(
    "fn", ["bars_as_of", "latest_bar_as_of", "bar_as_of", "total_return_between"]
)
def test_as_of_and_include_backfill_are_required_keywords(fn: str) -> None:
    params = inspect.signature(getattr(bars_read, fn)).parameters
    for name in ("as_of", "include_backfill"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, (fn, name)
        assert params[name].default is inspect.Parameter.empty, (fn, name)


def test_a_naive_as_of_is_refused(world: str) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        read(world, "bars_as_of", "XLK", as_of=datetime(2026, 1, 1), include_backfill=True)


# --- visibility is by available_at, never effective_at --------------------------------------------


def test_a_bar_is_invisible_until_its_settle_point_even_after_it_traded(world: str) -> None:
    # D9 traded on Tue 12-09 and settles at 09:00 on Thu 12-11 (D+2). In between, it is not known.
    traded_and_closed = datetime(2025, 12, 10, 12, tzinfo=NEW_YORK)
    missing = read(world, "bar_as_of", "XLK", D9, as_of=traded_and_closed, include_backfill=True)
    assert isinstance(missing, Missing) and missing.reason is Reason.NOT_SETTLED_AS_OF
    at = read(world, "bar_as_of", "XLK", D9, as_of=settle(D9), include_backfill=True)
    assert isinstance(at, Bar) and at.c_raw == Decimal("105")
    snap = read(world, "bars_as_of", "XLK", as_of=traded_and_closed, include_backfill=True)
    assert D9 not in [b.session_date for b in snap.bars]


def test_latest_bar_is_this_symbols_newest_known_bar(world: str) -> None:
    # XLE has later sessions (12-10, 12-11) that would win "newest" if the symbol filter were lost.
    latest = read(world, "latest_bar_as_of", "XLK", as_of=LATE, include_backfill=True)
    assert isinstance(latest, Bar) and latest.session_date == D9 and latest.ticker == "XLK"
    assert latest.c_raw == Decimal("105")


# --- strict mode: include_backfill=False ----------------------------------------------------------


def test_strict_mode_sees_only_poll_rows_and_says_why(world: str) -> None:
    strict = read(world, "bars_as_of", "XLK", as_of=LATE, include_backfill=False)
    full = read(world, "bars_as_of", "XLK", as_of=LATE, include_backfill=True)
    assert [b.session_date for b in strict.bars] == [D4, D5, D9]
    assert [b.session_date for b in full.bars] == [D1, D2, D3, D4, D5, D9]
    assert {b.feed for b in strict.bars} == {"poll"}
    excluded = read(world, "bar_as_of", "XLK", D2, as_of=LATE, include_backfill=False)
    assert isinstance(excluded, Missing) and excluded.reason is Reason.BACKFILL_EXCLUDED


def test_a_symbol_with_only_backfill_rows_is_excluded_not_before_its_record(db: str) -> None:
    put_bar(db, "XLK", D1, "200", feed="backfill")
    latest = read(db, "latest_bar_as_of", "XLK", as_of=LATE, include_backfill=False)
    assert isinstance(latest, Missing) and latest.reason is Reason.BACKFILL_EXCLUDED
    one = read(db, "bar_as_of", "XLK", D1, as_of=LATE, include_backfill=False)
    assert isinstance(one, Missing) and one.reason is Reason.BACKFILL_EXCLUDED


# --- adjusted on read, by splits knowable at T ----------------------------------------------------


def test_adjustment_applies_only_splits_knowable_at_t_and_only_this_symbols(world: str) -> None:
    # D2 settled Thu 12-04 09:00; the split is knowable at D5's open (Fri 12-05 09:30). One minute
    # before that, D2 is known and its adjustment is not.
    before_split = opens(D5) - timedelta(minutes=1)
    early = read(world, "bar_as_of", "XLK", D2, as_of=before_split, include_backfill=True)
    late = read(world, "bar_as_of", "XLK", D2, as_of=LATE, include_backfill=True)
    assert isinstance(early, Bar) and isinstance(late, Bar)
    assert early.split_factor == 1 and early.c_adj == Decimal("202")  # not yet knowable
    assert late.split_factor == 2 and late.c_adj == Decimal("101") and late.v_adj == 2000
    assert late.c_raw == Decimal("202")  # as traded, never rewritten
    # XLE's 10:1 split (effective D2, knowable long before) must not reach XLK's D1.
    d1 = read(world, "bar_as_of", "XLK", D1, as_of=LATE, include_backfill=True)
    assert isinstance(d1, Bar) and d1.split_factor == 2


def test_a_split_does_not_adjust_its_own_session_or_later(world: str) -> None:
    d5 = read(world, "bar_as_of", "XLK", D5, as_of=LATE, include_backfill=True)
    assert isinstance(d5, Bar) and d5.split_factor == 1 and d5.c_adj == Decimal("104")


def test_a_bar_carries_no_stored_total_return() -> None:
    # Step 5b: a stored level leaked hindsight; total return is total_return_between, a ratio.
    assert "total_return" not in Bar.__dataclass_fields__
    assert "total_return_revision" not in Bar.__dataclass_fields__


# --- the reasons, in decision order ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("ticker", "day", "as_of", "include_backfill", "reason"),
    [
        ("NOPE", D1, LATE, True, Reason.UNKNOWN_SYMBOL),
        ("XLK", date(2025, 12, 6), LATE, True, Reason.NOT_A_SESSION),  # a Saturday
        ("XLK", date(2025, 12, 25), LATE, True, Reason.NOT_A_SESSION),  # Christmas
        ("XLK", D1, datetime(2025, 11, 20, tzinfo=NEW_YORK), True, Reason.BEFORE_RECORD),
        ("XLK", date(2025, 11, 28), LATE, True, Reason.PREDATES_RECORD),
        ("XLK", D3, LATE, False, Reason.BACKFILL_EXCLUDED),
        ("XLK", D9, datetime(2025, 12, 10, 12, tzinfo=NEW_YORK), True, Reason.NOT_SETTLED_AS_OF),
        ("XLK", GAP, LATE, True, Reason.NOT_STORED),
        # Strict mode: the record starts at the first POLL row (D4, settling 12-08 09:00), so before
        # that nothing is known, though backfill rows settled earlier.
        ("XLK", D9, datetime(2025, 12, 8, 8, 59, tzinfo=NEW_YORK), False, Reason.BEFORE_RECORD),
        ("XLK", date(2025, 12, 12), LATE, True, Reason.NOT_STORED),  # after the newest stored
    ],
    # Ids that name the reason, so a mutation check can select the cases by it.
    ids=lambda v: v.name if isinstance(v, Reason) else None,
)
def test_each_way_to_have_no_answer(
    world: str, ticker: str, day: date, as_of: datetime, include_backfill: bool, reason: Reason
) -> None:
    got = read(world, "bar_as_of", ticker, day, as_of=as_of, include_backfill=include_backfill)
    assert isinstance(got, Missing) and got.reason is reason, got


def test_strict_mode_record_starts_at_the_first_poll_row(world: str) -> None:
    # Strict mode: XLK's first poll row is D4, knowable at its settle point; before that, nothing.
    t = settle(D4) - timedelta(minutes=1)
    got = read(world, "latest_bar_as_of", "XLK", as_of=t, include_backfill=False)
    assert isinstance(got, Missing) and got.reason is Reason.BEFORE_RECORD
    full = read(world, "latest_bar_as_of", "XLK", as_of=t, include_backfill=True)
    assert isinstance(full, Bar) and full.session_date == D3


# --- bounds, intervals, columns -------------------------------------------------------------------


def test_start_and_end_bound_the_sessions(world: str) -> None:
    snap = read(world, "bars_as_of", "XLK", as_of=LATE, include_backfill=True, start=D2, end=D4)
    assert [b.session_date for b in snap.bars] == [D2, D3, D4]


def test_the_intraday_read_reads_intraday_bars_only(world: str) -> None:
    # A 15m bar at D4 09:30 shares its instant with XLK's daily D4 bar. Each read sees its own
    # table.
    sql(
        world,
        "INSERT INTO bars_intraday (symbol_id, interval, effective_at, o_raw, h_raw, l_raw, c_raw, "
        "v_raw, raw_basis, source, feed, venue_scope, available_at) SELECT id, '15m', :e, 1, 1, 1, "
        "1.5, 7, 'live', 'yfinance', 'poll', 'provider_default', :a "
        "FROM symbols WHERE ticker = 'XLK'",
        e=opens(D4), a=settle(D4, k=1),
    )  # fmt: skip
    m15 = read(world, "bars_as_of", "XLK", as_of=LATE, include_backfill=True, interval=INTRADAY_15M)
    assert [(b.session_date, b.c_raw) for b in m15.bars] == [(D4, Decimal("1.5"))]
    assert m15.bars[0].split_factor == 2  # the D5 split applies to the D4 15m bar too
    daily = read(world, "bars_as_of", "XLK", as_of=LATE, include_backfill=True)
    assert Decimal("1.5") not in [b.c_raw for b in daily.bars]


def test_the_read_selects_an_explicit_column_list() -> None:
    from app.models.prices import BarDaily

    query = bars_read._bars_query(BarDaily, 1, LATE, include_backfill=True)
    names = [c.name for c in query.selected_columns]
    assert names == [
        "session_date", "effective_at", "o_raw", "h_raw", "l_raw", "c_raw", "v_raw",
        "raw_basis", "feed", "available_at",
    ]  # fmt: skip
    assert "ingestion_run_id" not in names and "*" not in str(query)


# --- total return between two sessions, computed on read (step 5b) --------------------------------


def tr(url: str, ticker: str, start: date, end: date, **kw: Any) -> Any:
    kw.setdefault("as_of", LATE)
    kw.setdefault("include_backfill", True)
    return read(url, "total_return_between", ticker, start, end, **kw)


def yahoo(price_ratio: Decimal, *factors: Decimal) -> Decimal:
    out = price_ratio
    for f in factors:
        out /= f
    return out


def test_the_total_return_is_a_ratio_by_yahoos_convention(world: str) -> None:
    # XLK D1 -> D4 as of LATE: the D5 2:1 split is knowable, so every close and amount is on the
    # post-split basis. The D3 dividend, paid 2.04 per pre-split share, is 1.02 on that basis, and
    # the close before its ex-date (D2) is 202 / 2. Yahoo's factor: 1 - 1.02 / 101.
    got = tr(world, "XLK", D1, D4)
    price = (Decimal("206") / 2) / (Decimal("200") / 2)
    expected = yahoo(price, 1 - (Decimal("2.04") / 2) / (Decimal("202") / 2))
    assert got.ratio == expected and got.price_ratio == price
    assert [(d.effective_at, d.amount, d.amount_adj) for d in got.distributions] == [
        (D3, Decimal("2.04"), Decimal("1.02"))
    ]


def test_a_second_symbols_distributions_never_reach_the_first(world: str) -> None:
    # XLE has distributions on D2 and D3 of 999 per share: lost symbol filter, wildly different
    # ratio (or a negative factor).
    got = tr(world, "XLK", D1, D4)
    assert [d.effective_at for d in got.distributions] == [D3]


def test_an_ex_date_on_the_split_day_uses_one_share_basis(world: str) -> None:
    # The D5 dividend is paid on post-split shares; the D4 close is put on that basis by the split.
    got = tr(world, "XLK", D4, D5)
    expected = yahoo(
        Decimal("104") / (Decimal("206") / 2), 1 - Decimal("1.03") / (Decimal("206") / 2)
    )
    assert got.ratio == expected


def test_the_window_is_start_exclusive_end_inclusive(world: str) -> None:
    assert [d.effective_at for d in tr(world, "XLK", D2, D3).distributions] == [D3]
    assert tr(world, "XLK", D3, D4).distributions == ()  # an ex-date AT start belongs before it


def test_a_split_inside_the_window_is_on_one_basis(world: str) -> None:
    got = tr(world, "XLK", D1, D9)
    assert got.price_ratio == Decimal("105") / (Decimal("200") / 2)  # 1.05, not 0.525
    assert [d.effective_at for d in got.distributions] == [D3, D5]


def test_a_split_not_knowable_at_t_is_not_applied(world: str) -> None:
    # As of just before D5's open the split is not knowable: D1 -> D2 is as traded.
    before_split = opens(D5) - timedelta(minutes=1)
    got = tr(world, "XLK", D1, D2, as_of=before_split)
    assert got.price_ratio == Decimal("202") / Decimal("200")


def test_a_distribution_is_invisible_until_its_available_at(world: str) -> None:
    # By the store's rule an in-window distribution is knowable at its ex-date's open, which is
    # always before the end bar's settle point; so the filter is observable only with a LATER
    # available_at. This pins the filter itself: a capital gain ex D4, recorded as knowable only on
    # 2026-01-10, is not applied before then and is after.
    put_dist(
        world,
        "XLK",
        D4,
        "0.5",
        kind="capital_gain",
        available_at=datetime(2026, 1, 10, tzinfo=NEW_YORK),
    )
    early = tr(world, "XLK", D3, D4, as_of=datetime(2026, 1, 9, tzinfo=NEW_YORK))
    late = tr(world, "XLK", D3, D4, as_of=LATE)
    assert early.distributions == ()
    assert [(d.kind, d.amount) for d in late.distributions] == [("capital_gain", Decimal("0.5"))]


def test_strict_mode_refuses_a_window_with_a_backfill_only_distribution(world: str) -> None:
    got = tr(world, "XLK", D4, D5, include_backfill=False)
    assert isinstance(got, Bar) is False and got.ratio == yahoo(
        Decimal("104") / (Decimal("206") / 2), 1 - Decimal("1.03") / (Decimal("206") / 2)
    )
    # Both bars of D4 -> D5 are poll rows; a backfill-only distribution inside the window must still
    # refuse the strict read, never be silently left out of the ratio.
    put_dist(world, "XLK", D5, "0.25", kind="capital_gain", feed="backfill")
    refused = tr(world, "XLK", D4, D5, include_backfill=False)
    assert isinstance(refused, Missing) and refused.reason is Reason.BACKFILL_EXCLUDED
    assert "distribution" in refused.detail


def test_a_distribution_whose_previous_session_has_no_bar_is_not_stored(world: str) -> None:
    put_dist(world, "XLK", D9, "0.4")  # the session before D9 is the gap D8
    got = tr(world, "XLK", D5, D9)
    assert isinstance(got, Missing) and got.reason is Reason.NOT_STORED


def test_a_missing_end_bar_says_why(world: str) -> None:
    got = tr(world, "XLK", D1, date(2025, 12, 6))
    assert isinstance(got, Missing) and got.reason is Reason.NOT_A_SESSION


def test_the_same_session_is_a_ratio_of_one(world: str) -> None:
    assert tr(world, "XLK", D4, D4).ratio == 1
