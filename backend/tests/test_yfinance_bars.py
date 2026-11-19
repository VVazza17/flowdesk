"""M2 slice 6 step 3: a yfinance batch to settled, as-traded bars (app.ingest.bars).

Against the batches recorded 2026-11-18 with yfinance 1.8.0 (tests/fixtures/yfinance). Pure: no
database. Rule 9 where a value is keyed by ticker: a SECOND ticker's data that would change the
answer (a split, an older last-stored bar, a broken row) is present, and the test asserts it did
not reach the first. Mutation-checked: `bash infra/mutation-check.sh m2-slice6-step3`.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
import pytest

from app.ingest.bars import (
    DAILY,
    INTRADAY_15M,
    NEW_YORK,
    Calendar,
    DeadSymbols,
    DistributionConflict,
    DistributionEvent,
    DuplicateBar,
    EmptyToday,
    IncompleteBar,
    LikelyGone,
    LikelyGoneSymbols,
    NonExactAmount,
    NonIntegerVolume,
    Session,
    SplitConflict,
    SplitEvent,
    SymbolFailure,
    SymbolsFailed,
    classify_dead,
    parse_batch,
    run_failure,
)
from app.ingest.errors import RateLimited, SchemaMismatch, Transient, health_error, run_error
from tests.yfixtures import load, nyse

# The 24 seeded symbols (settings.UNIVERSES), as recorded in daily_3mo_universe_plus_dead.
UNIVERSE = [
    *("XLK", "SMH", "IGV", "XLF", "XLE", "XLI", "XLV", "XLY", "XLP", "XLU", "XLB", "XLRE", "IWM"),
    *("ARKK", "SPY", "QQQ", "RSP", "USO", "UNG", "GLD", "SLV", "CPER", "URA", "DBA"),
]
DEAD = "ZZZQXFAKE"


def et(y: int, m: int, d: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(y, m, d, hour, minute, tzinfo=NEW_YORK)


def daily(now: datetime | None = None, **kw: Any) -> Any:
    frame, errors, fetched = load("daily_3mo_universe_plus_dead")
    return parse_batch(
        kw.pop("frame", frame),
        kw.pop("errors", errors),
        tickers=kw.pop("tickers", [*UNIVERSE, DEAD]),
        interval=DAILY,
        calendar=nyse(),
        now=now or fetched,
        known_splits=kw.pop("known_splits", {}),
        known_distributions=kw.pop("known_distributions", {}),
    )


def split_batch(**kw: Any) -> Any:
    frame, errors, fetched = load("daily_split_2025_12_05")
    return parse_batch(
        kw.pop("frame", frame),
        errors,
        tickers=["XLK", "XLE", "XLY", "XLU", "XLB"],
        interval=DAILY,
        calendar=nyse(),
        now=fetched,
        known_splits=kw.pop("known_splits", {}),
        known_distributions=kw.pop("known_distributions", {}),
    )


def bar_on(result: Any, ticker: str, day: date) -> Any:
    (found,) = [b for b in result.bars[ticker] if b.session_date == day]
    return found


# --- the shape: columns by name, the index, the calendar ------------------------------------------


def test_the_recorded_daily_batch_parses_every_live_symbol() -> None:
    result = daily()
    assert sorted(result.bars) == sorted(UNIVERSE)
    assert result.failures == []
    assert result.dead == [DEAD]  # D4 / F-e: the all-NaN group


def test_capital_gains_is_accepted_and_a_stock_without_it_is_too() -> None:
    # MEASURED 2026-11-18: the ETFs carry Capital Gains, NVDA does not. Both are known shapes.
    frame, errors, fetched = load("daily_split_nvda_2024_06_10")
    assert ("SPY", "Capital Gains") in frame.columns and (
        "NVDA",
        "Capital Gains",
    ) not in frame.columns
    result = parse_batch(
        frame,
        errors,
        tickers=["NVDA", "SPY"],
        interval=DAILY,
        calendar=nyse(),
        now=fetched,
        known_splits={},
        known_distributions={},
    )
    assert result.failures == [] and sorted(result.bars) == ["NVDA", "SPY"]


def test_an_unknown_column_fails_that_symbol_only() -> None:
    frame, errors, _ = load("daily_3mo_universe_plus_dead")
    frame = frame.copy()
    frame[("XLK", "Repaired?")] = 0.0
    result = daily(frame=frame)
    assert [f.ticker for f in result.failures] == ["XLK"]
    assert isinstance(result.failures[0].error, SchemaMismatch)
    assert "XLF" in result.bars  # a second symbol is untouched


def test_a_missing_required_column_fails_that_symbol() -> None:
    frame, _, _ = load("daily_3mo_universe_plus_dead")
    frame = frame.drop(columns=[("XLE", "Adj Close")])
    result = daily(frame=frame)
    assert [f.ticker for f in result.failures] == ["XLE"]


def test_daily_labels_become_session_dates_and_effective_at_is_the_open() -> None:
    # F-c: the batch's daily index is timezone-NAIVE (as recorded); it is the session date.
    frame, _, _ = load("daily_3mo_universe_plus_dead")
    assert frame.index.tz is None
    b = bar_on(daily(), "SPY", date(2026, 11, 16))
    assert b.effective_at == nyse().get(date(2026, 11, 16)).opens_at  # type: ignore[union-attr]
    assert b.effective_at == datetime(2026, 11, 16, 14, 30, tzinfo=UTC)


def test_an_aware_daily_index_is_read_in_new_york() -> None:
    frame, _, fetched = load("daily_3mo_universe_plus_dead")
    aware = frame.copy()
    aware.index = aware.index.tz_localize(NEW_YORK)
    assert daily(frame=aware).bars["SPY"] == daily().bars["SPY"]


def test_a_daily_date_that_is_not_a_session_fails_the_batch() -> None:
    frame, _, _ = load("daily_3mo_universe_plus_dead")
    frame = frame.copy()
    frame.index = frame.index.where(
        frame.index != pd.Timestamp("2026-11-16"), pd.Timestamp("2026-11-15")
    )
    with pytest.raises(SchemaMismatch, match="not a session"):
        daily(frame=frame)


def test_a_naive_intraday_index_is_rejected() -> None:
    frame, errors, fetched = load("m15_5d_three_plus_dead")
    naive = frame.copy()
    naive.index = naive.index.tz_localize(None)
    with pytest.raises(SchemaMismatch, match="timezone-naive"):
        parse_batch(
            naive,
            errors,
            tickers=["SPY", "QQQ", "XLK", DEAD],
            interval=INTRADAY_15M,
            calendar=nyse(),
            now=fetched,
            known_splits={},
            known_distributions={},
        )


def test_a_ticker_that_was_not_requested_fails_the_batch() -> None:
    with pytest.raises(SchemaMismatch, match="not requested"):
        daily(tickers=[t for t in UNIVERSE if t != "QQQ"] + [DEAD])


# --- the settle point: excluded by the clock, never by the frame ----------------------------------


def test_daily_bars_settle_at_0900_on_the_second_session_after() -> None:
    # Recorded Wed 2026-11-18 16:56 ET. Mon 11-16 settled at Wed 09:00 (D+2); Tue 11-17 settles Thu
    # 09:00; Wed 11-18 (in the frame, and complete) settles Fri. Two rows per symbol are unsettled.
    result = daily()
    assert max(b.session_date for b in result.bars["SPY"]) == date(2026, 11, 16)
    assert result.unsettled == 2 * len(UNIVERSE)
    b = bar_on(result, "SPY", date(2026, 11, 16))
    assert b.available_at == et(2026, 11, 18, 9, 0)


def test_one_microsecond_before_the_settle_point_the_bar_is_not_stored() -> None:
    settle = et(2026, 11, 18, 9, 0)
    before = daily(now=settle - timedelta(microseconds=1))
    at = daily(now=settle)
    assert max(b.session_date for b in before.bars["SPY"]) == date(2026, 11, 13)
    assert max(b.session_date for b in at.bars["SPY"]) == date(2026, 11, 16)


def test_the_settle_point_counts_sessions_not_days() -> None:
    # Thu 11-12's D+2 is Mon 11-16 (the weekend is not two sessions).
    assert nyse().settle_point(date(2026, 11, 12), DAILY) == et(2026, 11, 16, 9, 0)
    assert nyse().settle_point(date(2026, 11, 13), INTRADAY_15M) == et(2026, 11, 16, 9, 0)


def test_intraday_bars_settle_at_0900_on_the_next_session() -> None:
    frame, errors, fetched = load("m15_5d_three_plus_dead")
    result = parse_batch(
        frame,
        errors,
        tickers=["SPY", "QQQ", "XLK", DEAD],
        interval=INTRADAY_15M,
        calendar=nyse(),
        now=fetched,
        known_splits={},
        known_distributions={},
    )
    days = sorted({b.session_date for b in result.bars["SPY"]})
    assert days[-1] == date(2026, 11, 17)  # Tue settled at Wed 09:00; Wed's bars wait for Thu
    assert result.unsettled == 26 * 3  # Wed's 26 bars for each live symbol
    assert result.dead == [DEAD]
    first = min(result.bars["SPY"], key=lambda b: b.effective_at)
    assert first.effective_at.astimezone(NEW_YORK).time().isoformat() == "09:30:00"


def test_intraday_bars_outside_the_session_window_are_dropped_and_counted() -> None:
    # D5 iii: nothing after an early close. A calendar whose Mon 11-16 closed at 13:00 keeps the
    # bars that END by 13:00 (09:30 to 12:45) and drops the rest of that day.
    real = nyse()
    sessions = [
        Session(s.session_date, s.opens_at, et(2026, 11, 16, 13, 0))
        if s.session_date == date(2026, 11, 16)
        else s
        for s in real.sessions
    ]
    frame, errors, fetched = load("m15_5d_three_plus_dead")
    result = parse_batch(
        frame,
        errors,
        tickers=["SPY", "QQQ", "XLK", DEAD],
        interval=INTRADAY_15M,
        calendar=Calendar(sessions),
        now=fetched,
        known_splits={},
        known_distributions={},
    )
    monday = [b for b in result.bars["SPY"] if b.session_date == date(2026, 11, 16)]
    assert len(monday) == 14
    assert result.outside_session == 12 * 3


# --- as traded: splits, reconstruction, volume ----------------------------------------------------


def test_xlk_rebuilds_as_traded_across_its_2025_12_05_split() -> None:
    result = split_batch()
    before = bar_on(result, "XLK", date(2025, 12, 4))
    after = bar_on(result, "XLK", date(2025, 12, 5))
    # D3: the adjusted Close's shortest repr (Q7, never rounded) times 2. The float's exact binary
    # value is 145.535003662109375; its shortest repr is ...938, so the product ends ...876, not
    # ...875: the rebuilt value inherits the provider's rounding (D3), and the repr's too.
    assert before.c_raw == Decimal("145.53500366210938") * 2 == Decimal("291.07000732421876")
    assert before.v_raw == 9_995_200 // 2 and before.raw_basis == "reconstructed"
    assert after.c_raw == Decimal("146.60000610351562") and after.raw_basis == "live"
    assert result.new_splits["XLK"] == [SplitEvent(date(2025, 12, 5), Decimal("2.0"))]


def test_nvda_rebuilds_to_the_d3_reference_value() -> None:
    frame, errors, fetched = load("daily_split_nvda_2024_06_10")
    result = parse_batch(
        frame,
        errors,
        tickers=["NVDA", "SPY"],
        interval=DAILY,
        calendar=nyse(),
        now=fetched,
        known_splits={},
        known_distributions={},
    )
    b = bar_on(result, "NVDA", date(2024, 6, 7))
    assert b.c_raw == Decimal("1208.8800048828125")  # "about 1,208.88" (D3 reference check)
    assert b.v_raw == 412_386_000 // 10
    assert bar_on(result, "SPY", date(2024, 6, 7)).raw_basis == "live"


def test_a_known_split_applies_to_its_own_symbol_only() -> None:
    # Rule 9: XLE carries a stored split after the window (a fictional 1-for-2 reverse split, ratio
    # 0.5, so volumes stay whole), which rebuilds every earlier XLE bar. XLK has none; its bars must
    # be exactly as without XLE's split.
    plain = split_batch()
    with_xle = split_batch(known_splits={"XLE": [SplitEvent(date(2026, 1, 15), Decimal("0.5"))]})
    assert with_xle.failures == []
    assert with_xle.bars["XLK"] == plain.bars["XLK"]
    xle_plain = bar_on(plain, "XLE", date(2025, 12, 8))
    xle_known = bar_on(with_xle, "XLE", date(2025, 12, 8))
    assert xle_known.c_raw == xle_plain.c_raw * Decimal("0.5")
    assert xle_known.v_raw == xle_plain.v_raw * 2 and xle_known.raw_basis == "reconstructed"


def test_a_split_on_an_unsettled_session_still_rebuilds_the_settled_bars() -> None:
    # The split row itself is not settled yet; the bars before it are, and must be rebuilt.
    frame, errors, _ = load("daily_split_2025_12_05")
    result = parse_batch(
        frame,
        errors,
        tickers=["XLK", "XLE", "XLY", "XLU", "XLB"],
        interval=DAILY,
        calendar=nyse(),
        now=et(2025, 12, 8, 9, 0),
        known_splits={},
        known_distributions={},
    )
    assert max(b.session_date for b in result.bars["XLK"]) == date(2025, 12, 4)
    assert bar_on(result, "XLK", date(2025, 12, 4)).c_raw == Decimal("291.07000732421876")
    assert result.new_splits["XLK"] == [SplitEvent(date(2025, 12, 5), Decimal("2.0"))]


def test_a_stored_split_that_disagrees_fails_that_symbol_and_is_not_overwritten() -> None:
    result = split_batch(known_splits={"XLK": [SplitEvent(date(2025, 12, 5), Decimal("3"))]})
    assert [f.ticker for f in result.failures] == ["XLK"]
    assert isinstance(result.failures[0].error, SplitConflict)
    assert "XLK" not in result.bars and "XLK" not in result.new_splits
    assert "XLE" in result.bars  # the second symbol is untouched


def test_a_stored_split_that_agrees_is_not_new() -> None:
    result = split_batch(known_splits={"XLK": [SplitEvent(date(2025, 12, 5), Decimal("2.0"))]})
    assert "XLK" not in result.new_splits and result.bars["XLK"] == split_batch().bars["XLK"]


def test_a_rebuilt_volume_that_is_not_whole_fails_that_symbol_never_rounds() -> None:
    frame, _, _ = load("daily_split_2025_12_05")
    frame = frame.copy()
    frame.loc[pd.Timestamp("2025-12-04"), ("XLK", "Volume")] = 9_995_201.0  # odd: /2 is not whole
    result = split_batch(frame=frame)
    assert [f.ticker for f in result.failures] == ["XLK"]
    assert isinstance(result.failures[0].error, NonIntegerVolume)
    assert bar_on(result, "XLE", date(2025, 12, 4)).v_raw == 23_799_600 // 2


def test_a_fractional_provider_volume_fails_the_symbol() -> None:
    frame, _, _ = load("daily_3mo_universe_plus_dead")
    frame = frame.copy()
    frame.loc[pd.Timestamp("2026-11-16"), ("QQQ", "Volume")] = 100.5
    result = daily(frame=frame)
    assert [f.ticker for f in result.failures] == ["QQQ"]


# --- duplicates and incomplete rows ---------------------------------------------------------------


def _with_duplicate(differs: bool) -> Any:
    frame, _, _ = load("daily_3mo_universe_plus_dead")
    row = frame.loc[[pd.Timestamp("2026-11-16")]].copy()
    if differs:
        row[("SPY", "Close")] = row[("SPY", "Close")] + 1.0
    return pd.concat([frame, row]).sort_index(kind="stable")


def test_an_identical_duplicate_row_is_deduplicated() -> None:
    result = daily(frame=_with_duplicate(differs=False))
    assert result.failures == []
    assert result.bars["SPY"] == daily().bars["SPY"]


def test_a_differing_duplicate_fails_that_symbol_never_last_write_wins() -> None:
    result = daily(frame=_with_duplicate(differs=True))
    assert [f.ticker for f in result.failures] == ["SPY"]
    assert isinstance(result.failures[0].error, DuplicateBar)
    assert "SPY" not in result.bars
    assert result.bars["QQQ"] == daily().bars["QQQ"]  # same duplicated label, identical: fine


def test_an_unsettled_row_is_dropped_by_the_clock_before_its_contents_are_judged() -> None:
    # MEASURED 2026-11-18: at about 20:15 ET the day's own daily bar had Close and Adj Close blank.
    # Wed 11-18 settles Fri 09:00, so at the recording time it is unsettled: dropped and counted,
    # never a failure of the symbol (F-a). The same blank on a settled row still fails it (below).
    frame, _, _ = load("daily_3mo_universe_plus_dead")
    frame = frame.copy()
    for ticker in ("SPY", "QQQ"):
        frame.loc[pd.Timestamp("2026-11-18"), [(ticker, "Close"), (ticker, "Adj Close")]] = float(
            "nan"
        )
    result = daily(frame=frame)
    assert result.failures == []
    assert result.bars["SPY"] == daily().bars["SPY"]
    assert result.unsettled == 2 * len(UNIVERSE)


def test_a_bar_with_a_missing_field_fails_that_symbol_never_filled() -> None:
    frame, _, _ = load("daily_3mo_universe_plus_dead")
    frame = frame.copy()
    frame.loc[pd.Timestamp("2026-11-16"), ("GLD", "Volume")] = float("nan")
    result = daily(frame=frame)
    assert [f.ticker for f in result.failures] == ["GLD"]
    assert isinstance(result.failures[0].error, IncompleteBar)


# --- the errors yfinance swallowed: classified, never stored --------------------------------------


@pytest.mark.parametrize(
    ("text", "raised"),
    [
        ("YFRateLimitError('Too Many Requests. Rate limited. Try after a while.')", RateLimited),
        ("ReadTimeout('curl: (28) Operation timed out after 10001 ms')", Transient),
        (
            "CurlError('Failed to perform, curl: (6) Could not resolve host: query2.finance')",
            Transient,
        ),
        ("SomethingNewError('a class this client has never seen')", Transient),
    ],
)
def test_a_rate_limit_or_network_error_on_any_ticker_fails_the_batch(
    text: str, raised: type
) -> None:
    errors = {DEAD: "No data found, symbol may be delisted", "XLU": text}
    with pytest.raises(raised) as caught:
        daily(errors=errors)
    # Nothing the library said is in our exception.
    assert "curl" not in str(caught.value) and "Too Many" not in str(caught.value)


@pytest.mark.parametrize(
    "text",
    [
        "No data found, symbol may be delisted",
        "YFPricesMissingError('$ZZZQXFAKE: possibly delisted; no price data found')",
        "YFTzMissingError('$ZZZQXFAKE: possibly delisted; no timezone found')",
    ],
)
def test_a_no_data_error_is_judged_by_the_frame(text: str) -> None:
    assert daily(errors={DEAD: text}).dead == [DEAD]


def test_rows_that_came_back_win_over_a_soft_no_data_message() -> None:
    result = daily(errors={DEAD: "No data found", "SPY": "possibly delisted"})
    assert "SPY" in result.bars and result.dead == [DEAD]


# --- D4: empty today, likely gone, and the run's failure ------------------------------------------


def test_likely_gone_at_five_sessions_behind_and_not_at_four() -> None:
    cal = nyse()
    newest = date(2026, 11, 16)
    four_behind, five_behind = date(2026, 11, 10), date(2026, 11, 9)
    assert cal.sessions_between(four_behind, newest) == 4
    out = classify_dead(
        ["XLK", "XLE"],
        last_stored={"XLK": four_behind, "XLE": five_behind},
        newest_stored=newest,
        calendar=cal,
    )
    assert [(f.ticker, type(f.error)) for f in out] == [("XLK", EmptyToday), ("XLE", LikelyGone)]


def test_likely_gone_reads_each_symbols_own_last_bar() -> None:
    # Rule 9: XLF's last stored bar is far older, and it is not dead. XLK's verdict must come from
    # XLK's own last bar.
    out = classify_dead(
        ["XLK"],
        last_stored={"XLF": date(2026, 8, 3), "XLK": date(2026, 11, 13)},
        newest_stored=date(2026, 11, 16),
        calendar=nyse(),
    )
    assert isinstance(out[0].error, EmptyToday)


def test_an_outage_that_empties_every_symbol_is_never_a_retirement() -> None:
    # Nothing new stored by anyone: every symbol is as far behind as the newest, so none is gone.
    last = {t: date(2026, 11, 2) for t in UNIVERSE}
    out = classify_dead(
        UNIVERSE, last_stored=last, newest_stored=date(2026, 11, 2), calendar=nyse()
    )
    assert all(isinstance(f.error, EmptyToday) for f in out)


def test_the_run_names_every_failed_symbol_in_our_own_words() -> None:
    failures = [
        SymbolFailure("XLK", EmptyToday("XLK: no rows in this batch")),
        SymbolFailure("URA", LikelyGone("URA: empty, 6 sessions behind")),
        SymbolFailure("SPY", DuplicateBar("SPY: two different rows for one bar")),
    ]
    err = run_failure(failures)
    assert isinstance(err, SymbolsFailed)  # broken data first
    text = str(err)
    assert "SPY" in text and "URA" in text and "XLK" in text
    assert health_error(err) == "schema_mismatch:SymbolsFailed"
    assert run_error(err, []).startswith(
        "schema_mismatch:SymbolsFailed: data broke a rule for: SPY"
    )
    assert isinstance(run_failure(failures[:2]), LikelyGoneSymbols)
    assert isinstance(run_failure(failures[:1]), DeadSymbols)
    assert run_failure([]) is None
    assert "never automatic" in str(run_failure(failures[1:2]))


# --- distributions as dated events (step 5b) ------------------------------------------------------


def _with_dividends(frame: Any, rows: dict[tuple[str, str], float]) -> Any:
    frame = frame.copy()
    for (ticker, day), amount in rows.items():
        frame.loc[pd.Timestamp(day), (ticker, "Dividends")] = amount
    return frame


def test_a_dividend_before_a_split_is_stored_as_paid_and_one_after_as_given() -> None:
    # MEASURED 2026-11-18: yfinance's dividends are on the split-adjusted basis, so one before XLK's
    # 2025-12-05 2:1 split is multiplied back by 2 (as paid), one after is as given.
    frame, _, _ = load("daily_split_2025_12_05")
    frame = _with_dividends(frame, {("XLK", "2025-12-04"): 0.193, ("XLK", "2025-12-12"): 0.2})
    result = split_batch(frame=frame)
    assert result.new_distributions["XLK"] == [
        DistributionEvent(date(2025, 12, 4), "dividend", Decimal("0.386"), "reconstructed"),
        DistributionEvent(date(2025, 12, 12), "dividend", Decimal("0.2"), "live"),
    ]
    assert "XLE" not in result.new_distributions  # a second symbol's are its own


def test_a_dividend_on_an_unsettled_session_is_still_read() -> None:
    # Read from every row, settled or not, as splits are: at Tue 2025-12-09 09:00 the Mon 12-08 bar
    # has not settled (D+2 is Wed), but its ex-date's open has passed, so the dividend is knowable.
    frame, errors, _ = load("daily_split_2025_12_05")
    frame = _with_dividends(frame, {("XLK", "2025-12-08"): 0.2})
    result = parse_batch(
        frame,
        errors,
        tickers=["XLK", "XLE", "XLY", "XLU", "XLB"],
        interval=DAILY,
        calendar=nyse(),
        now=et(2025, 12, 9, 9),
        known_splits={},
        known_distributions={},
    )
    assert date(2025, 12, 8) not in [b.session_date for b in result.bars["XLK"]]  # unsettled
    assert result.new_distributions["XLK"] == [
        DistributionEvent(date(2025, 12, 8), "dividend", Decimal("0.2"), "live")
    ]


def test_a_capital_gain_is_a_distribution_of_its_own_kind() -> None:
    frame, _, _ = load("daily_split_2025_12_05")
    frame = frame.copy()
    frame.loc[pd.Timestamp("2025-12-08"), ("XLK", "Capital Gains")] = 0.5
    frame.loc[pd.Timestamp("2025-12-08"), ("XLK", "Dividends")] = 0.2
    result = split_batch(frame=frame)
    assert {(d.kind, d.amount) for d in result.new_distributions["XLK"]} == {
        ("capital_gain", Decimal("0.5")),
        ("dividend", Decimal("0.2")),
    }


def test_a_stored_distribution_that_agrees_is_not_new_and_one_that_disagrees_fails_the_symbol() -> (
    None
):
    frame, _, _ = load("daily_split_2025_12_05")
    frame = _with_dividends(frame, {("XLK", "2025-12-04"): 0.193, ("XLE", "2025-12-04"): 0.35})
    same = split_batch(
        frame=frame,
        known_distributions={
            "XLK": [DistributionEvent(date(2025, 12, 4), "dividend", Decimal("0.386"))]
        },
    )
    assert "XLK" not in same.new_distributions and same.failures == []
    differs = split_batch(
        frame=frame,
        known_distributions={
            "XLK": [DistributionEvent(date(2025, 12, 4), "dividend", Decimal("0.4"))]
        },
    )
    assert [f.ticker for f in differs.failures] == ["XLK"]
    assert isinstance(differs.failures[0].error, DistributionConflict)
    # Rule 9: XLE's distribution is unaffected by XLK's stored one.
    assert differs.new_distributions["XLE"][0].amount == Decimal("0.70")


def test_a_rebuilt_amount_that_would_have_to_round_fails_the_symbol_never_rounds() -> None:
    frame, _, _ = load("daily_split_2025_12_05")
    frame = _with_dividends(frame, {("XLK", "2025-12-04"): 0.193})
    # A stored split with a ratio whose product with 0.193 needs more than 28 significant digits.
    odd = SplitEvent(date(2026, 1, 15), Decimal("1.234567890123456789012345678"))
    result = split_batch(frame=frame, known_splits={"XLK": [odd]})
    assert [f.ticker for f in result.failures] == ["XLK"]
    assert isinstance(result.failures[0].error, NonExactAmount)
