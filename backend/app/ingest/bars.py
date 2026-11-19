"""Turning a yfinance batch into settled, as-traded bars (M2 slice 6, step 3).

Pure: no database, no HTTP, no clock. The caller passes the frame, the per-ticker errors yfinance
swallowed, the calendar, `now`, and what is already stored (known splits, each symbol's last stored
session). Writing the result is the backfill's and the poll's (steps 4 and 6). Every rule here is a
recorded decision (docs/BUILD-LOG.md, slice 6); the name of the decision is beside each.

What comes out is only what may be stored: bars at or past their settle point, rebuilt as traded,
with `available_at` = the settle point. Anything else is either dropped by a stated rule (counted),
or fails the symbol, or fails the batch. Nothing is guessed, rounded or filled.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, Inexact, localcontext
from zoneinfo import ZoneInfo

import pandas as pd  # type: ignore[import-untyped]

from app.ingest.errors import EmptyResult, ProviderError, RateLimited, SchemaMismatch, Transient

NEW_YORK = ZoneInfo("America/New_York")

DAILY = "1d"
INTRADAY_15M = "15m"
INTERVAL_LENGTH = {INTRADAY_15M: timedelta(minutes=15)}

# S (MEASURED 2026-11-18, Kyle): 09:00 America/New_York on the k-th session after the bar's session.
SETTLE_SESSIONS_AFTER = {DAILY: 2, INTRADAY_15M: 1}
SETTLE_TIME = time(9, 0)

# D4, approved 2026-11-18: likely_gone when the symbol's last stored bar is this many sessions or
# more behind the newest session any symbol stored. It prompts the operator; it never retires a
# symbol.
LIKELY_GONE_SESSIONS = 5

# Columns by name (step 3 plan; F-d). Required on every live group; the action columns come with
# actions=True, and Capital Gains only for funds (MEASURED 2026-11-18: the 24 ETFs carry it, NVDA
# does not). Anything else is a shape this client does not know: schema_mismatch, never guessed.
PRICE_COLUMNS = ("Open", "High", "Low", "Close", "Adj Close", "Volume")
ACTION_COLUMNS = frozenset({"Dividends", "Stock Splits", "Capital Gains"})

# yfinance's per-ticker errors (its `_DownloadCtx.errors`) are reprs or soft strings. They are read
# here to CLASSIFY, never stored (nothing the library says is stored, Kyle 2026-11-18).
RATE_LIMIT_CLASSES = frozenset({"YFRateLimitError"})
DEAD_CLASSES = frozenset({"YFPricesMissingError", "YFTzMissingError", "YFTickerMissingError"})


# --- failures (fixed text: FlowDesk's own words and our own tickers, nothing from the library) ---


class DuplicateBar(SchemaMismatch):
    """Two different rows for one bar in one batch (D5 iv: never last-write-wins)."""


class IncompleteBar(SchemaMismatch):
    """A bar with some fields missing: never filled or partly stored."""


class NonIntegerVolume(SchemaMismatch):
    """Rebuilt volume is not a whole number of shares (Q6, 2026-11-18): fails, never rounds."""


class SplitConflict(SchemaMismatch):
    """The provider reports a ratio for a split date that differs from the stored one (D3)."""


class DistributionConflict(SchemaMismatch):
    """The provider reports an amount for a stored distribution that differs from it (step 5b):
    a validation failure, never an overwrite."""


class NonExactAmount(SchemaMismatch):
    """A distribution's amount rebuilt as paid (x later split ratios) is not exact: fails the
    symbol, never rounded (Kyle, 2026-11-18)."""


class EmptyToday(EmptyResult):
    """D4: the symbol returned no rows in this batch. Not yet a reason to think it is gone."""


class LikelyGone(EmptyResult):
    """D4: no rows, and its last stored bar is LIKELY_GONE_SESSIONS or more sessions behind the
    newest session any symbol stored. Prompts the operator; never retires the symbol."""


class SymbolsFailed(SchemaMismatch):
    """At least one symbol's data broke a rule. The good symbols' rows are still returned (D4)."""


class DeadSymbols(EmptyResult):
    """Symbols that returned nothing today (D4): the run fails and names them."""


class LikelyGoneSymbols(EmptyResult):
    """Symbols empty for LIKELY_GONE_SESSIONS or more sessions (D4): the run fails, names them, and
    says how they leave a universe. Never automatic."""


# --- data ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Session:
    session_date: date
    opens_at: datetime
    closes_at: datetime


@dataclass(frozen=True)
class SplitEvent:
    effective_at: date  # the session date the split takes effect
    ratio: Decimal


# The action columns that carry cash distributions, by `distributions.kind`.
DISTRIBUTION_COLUMNS = {"Dividends": "dividend", "Capital Gains": "capital_gain"}


@dataclass(frozen=True)
class DistributionEvent:
    """A cash distribution as a dated event (step 5b): its ex-date, kind, and amount AS PAID."""

    effective_at: date  # the ex-date session
    kind: str  # dividend | capital_gain
    amount: Decimal
    raw_basis: str = "live"  # live | reconstructed


@dataclass(frozen=True)
class SettledBar:
    ticker: str
    interval: str
    session_date: date
    effective_at: datetime  # daily: the session's opens_at; intraday: the bar's open instant
    o_raw: Decimal
    h_raw: Decimal
    l_raw: Decimal
    c_raw: Decimal
    v_raw: int
    raw_basis: str  # live | reconstructed
    available_at: datetime  # the settle point
    # No total return: it is computed on read from distributions (step 5b; D2 superseded).


@dataclass(frozen=True)
class SymbolFailure:
    ticker: str
    error: ProviderError


@dataclass
class BatchResult:
    bars: dict[str, list[SettledBar]] = field(default_factory=dict)
    new_splits: dict[str, list[SplitEvent]] = field(default_factory=dict)
    new_distributions: dict[str, list[DistributionEvent]] = field(default_factory=dict)
    failures: list[SymbolFailure] = field(default_factory=list)
    dead: list[str] = field(default_factory=list)  # empty groups, classified later (classify_dead)
    unsettled: int = 0  # rows dropped because their settle point is after `now`
    outside_session: int = 0  # intraday rows not inside a trading_sessions window (D5 iii)


# --- the calendar ---------------------------------------------------------------------------------


class Calendar:
    """`trading_sessions` for one exchange, in date order."""

    def __init__(self, sessions: Iterable[Session]) -> None:
        self.sessions = sorted(sessions, key=lambda s: s.session_date)
        self._index = {s.session_date: i for i, s in enumerate(self.sessions)}

    def get(self, d: date) -> Session | None:
        i = self._index.get(d)
        return self.sessions[i] if i is not None else None

    def settle_point(self, session_date: date, interval: str) -> datetime | None:
        """09:00 New York on the k-th session after `session_date` (S, MEASURED 2026-11-18).
        None if the calendar does not reach that far (then nothing can be called settled)."""
        i = self._index.get(session_date)
        k = SETTLE_SESSIONS_AFTER[interval]
        if i is None or i + k >= len(self.sessions):
            return None
        later = self.sessions[i + k].session_date
        return datetime.combine(later, SETTLE_TIME, tzinfo=NEW_YORK)

    def sessions_between(self, earlier: date, later: date) -> int:
        """How many sessions after `earlier`, up to and including `later` (0 if not later)."""
        i, j = self._index.get(earlier), self._index.get(later)
        if i is None or j is None:
            raise SchemaMismatch("a session date is not in trading_sessions")
        return max(0, j - i)


# --- parsing --------------------------------------------------------------------------------------


def _error_class(text: str) -> str | None:
    """The exception class a yfinance error repr starts with, or None for a soft message."""
    head = text.split("(", 1)[0].strip()
    return head if head.isidentifier() and "(" in text else None


def _check_ctx_errors(errors: Mapping[str, str], tickers: Sequence[str]) -> None:
    """Classify the per-ticker errors yfinance swallowed. A rate limit or a network failure fails
    the whole batch (nothing stored; the next run is gap-driven and fills it). A "no data" error is
    left for the frame to judge (an all-NaN group is a dead symbol)."""
    for ticker in tickers:
        text = errors.get(ticker)
        if text is None:
            continue
        cls = _error_class(text)
        if cls in RATE_LIMIT_CLASSES:
            raise RateLimited("yfinance rate-limited the batch") from None
        if cls is None or cls in DEAD_CLASSES:
            continue
        # DECISION: an exception class not known to mean "no data" (a network error, anything new)
        # fails the batch as transient: it must not be counted as a dead symbol, because dead
        # symbols feed likely_gone and an operator prompt.
        raise Transient("yfinance could not fetch part of the batch") from None


def _session_dates_daily(index: pd.Index, calendar: Calendar) -> list[date]:
    """F-c: a timezone-naive daily index is the exchange-local session date; an aware one is read
    in New York. Either way it must be midnight and match a trading_sessions row."""
    out = []
    for ts in index:
        stamp = pd.Timestamp(ts)
        if stamp.tzinfo is not None:
            stamp = stamp.tz_convert(NEW_YORK).tz_localize(None)
        if stamp != stamp.normalize():
            raise SchemaMismatch("a daily bar's label is not a date")
        d = stamp.date()
        if calendar.get(d) is None:
            raise SchemaMismatch("a daily bar's date is not a session in trading_sessions")
        out.append(d)
    return out


def _instants_intraday(index: pd.Index) -> list[datetime]:
    """D5 rule 2: an intraday index must be timezone-aware; a naive one is rejected."""
    if getattr(index, "tz", None) is None:
        raise SchemaMismatch("an intraday batch has a timezone-naive index")
    return [pd.Timestamp(ts).to_pydatetime() for ts in index]


def _decimal(value: float) -> Decimal:
    # Q7 (2026-11-18): the float's shortest repr, never rounded.
    return Decimal(repr(float(value)))


def _whole(value: float) -> int:
    if not float(value).is_integer():
        raise NonIntegerVolume("a provider volume is not a whole number")
    return int(value)


def _groups(frame: pd.DataFrame, tickers: Sequence[str]) -> dict[str, pd.DataFrame]:
    if frame is None or frame.empty and len(frame.columns) == 0:
        return {}
    if not isinstance(frame.columns, pd.MultiIndex) or frame.columns.nlevels != 2:
        raise SchemaMismatch("the batch is not grouped by ticker")
    present = set(frame.columns.get_level_values(0))
    unknown = present - set(tickers)
    if unknown:
        raise SchemaMismatch("the batch holds tickers that were not requested")
    return {t: frame[t] for t in tickers if t in present}


def _check_columns(group: pd.DataFrame) -> None:
    cols = set(group.columns)
    missing = set(PRICE_COLUMNS) - cols
    extra = cols - set(PRICE_COLUMNS) - ACTION_COLUMNS
    if missing or extra:
        raise SchemaMismatch("a ticker's columns are not the known set")


def _factor(splits: Iterable[SplitEvent], session_date: date) -> Decimal:
    """D3 b: the product of the ratios of every split effective AFTER the bar's session."""
    f = Decimal(1)
    for s in splits:
        if s.effective_at > session_date:
            f *= s.ratio
    return f


def parse_batch(
    frame: pd.DataFrame,
    errors: Mapping[str, str],
    *,
    tickers: Sequence[str],
    interval: str,
    calendar: Calendar,
    now: datetime,
    known_splits: Mapping[str, Sequence[SplitEvent]],
    known_distributions: Mapping[str, Sequence[DistributionEvent]],
) -> BatchResult:
    """The settled, as-traded bars in one batch, and what failed. Raises for the whole batch only
    when the batch itself is unusable (rate limit, network, a shape we do not know)."""
    if interval not in SETTLE_SESSIONS_AFTER:
        raise SchemaMismatch("unknown interval")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    # A rate limit or a network failure fails the whole batch here. A soft "no data" error is not
    # trusted on its own: the frame decides (an all-NaN group is dead; rows that came back win, and
    # the message, library text, is dropped and never stored).
    _check_ctx_errors(errors, tickers)
    groups = _groups(frame, tickers)
    result = BatchResult()

    if interval == DAILY:
        dates = _session_dates_daily(frame.index, calendar) if groups else []
        instants: list[datetime] = []
    else:
        instants = _instants_intraday(frame.index) if groups else []
        dates = []

    for ticker in tickers:
        group = groups.get(ticker)
        if (
            group is None
            or group[[c for c in PRICE_COLUMNS if c in group.columns]].isna().all().all()
        ):
            # D4: an all-NaN group (MEASURED F-e) or no group at all is a dead symbol today.
            result.dead.append(ticker)
            continue
        try:
            _check_columns(group)
            bars, splits, distributions = _parse_symbol(
                ticker,
                group,
                interval,
                dates,
                instants,
                calendar,
                now,
                known_splits.get(ticker, ()),
                known_distributions.get(ticker, ()),
                result,
            )
        except ProviderError as exc:
            result.failures.append(SymbolFailure(ticker, exc))
            continue
        result.bars[ticker] = bars
        if splits:
            result.new_splits[ticker] = splits
        if distributions:
            result.new_distributions[ticker] = distributions
    return result


def _parse_symbol(
    ticker: str,
    group: pd.DataFrame,
    interval: str,
    dates: Sequence[date],
    instants: Sequence[datetime],
    calendar: Calendar,
    now: datetime,
    known: Sequence[SplitEvent],
    known_distributions: Sequence[DistributionEvent],
    result: BatchResult,
) -> tuple[list[SettledBar], list[SplitEvent], list[DistributionEvent]]:
    rows: dict[date | datetime, tuple[float, ...]] = {}
    order: list[date | datetime] = []
    frame_splits: dict[date, Decimal] = {}
    frame_distributions: dict[
        tuple[date, str], Decimal
    ] = {}  # the provider's split-adjusted amount

    for pos, values in enumerate(group.itertuples(index=False, name=None)):
        record = dict(zip(group.columns, values, strict=True))
        prices = [record[c] for c in PRICE_COLUMNS]
        if all(pd.isna(v) for v in prices):
            continue  # this ticker has no bar at this label (another ticker does)
        key: date | datetime = dates[pos] if interval == DAILY else instants[pos]
        day = dates[pos] if interval == DAILY else instants[pos].astimezone(NEW_YORK).date()
        # Splits come from EVERY row, settled or not: a split on a session not yet settled still
        # applies to the settled bars before it.
        ratio = record.get("Stock Splits")
        if ratio is not None and not pd.isna(ratio) and ratio != 0:
            frame_splits[day] = _decimal(ratio)
        # Distributions too, from every row (step 5b): an ex-date is knowable at its open, whether
        # or not that session's bar has settled.
        for column, kind in DISTRIBUTION_COLUMNS.items():
            amount = record.get(column)
            if amount is not None and not pd.isna(amount) and amount != 0:
                frame_distributions[(day, kind)] = _decimal(amount)
        # F-a: an unsettled row is excluded by the CLOCK, BEFORE anything in it is judged. Its
        # contents are provisional by definition: MEASURED 2026-11-18, the day's own daily bar had
        # Close and Adj Close blank at about 20:15 ET (complete at 18:37). Judged first, that blank
        # failed every symbol on an evening run.
        settle = calendar.settle_point(day, interval)
        if settle is None or settle > now:
            result.unsettled += 1
            continue
        if any(pd.isna(v) for v in prices):
            raise IncompleteBar(f"{ticker}: a bar has missing fields")
        stamp = tuple(float(v) for v in prices)
        if key in rows:
            if rows[key] != stamp:
                raise DuplicateBar(f"{ticker}: two different rows for one bar")
            continue  # an identical duplicate is deduplicated (D5 iv)
        rows[key] = stamp
        order.append(key)

    # D3: a split the provider reports must agree with the stored one; it is never overwritten.
    stored = {s.effective_at: s.ratio for s in known}
    for d, ratio in frame_splits.items():
        if d in stored and stored[d] != ratio:
            raise SplitConflict(f"{ticker}: a split's ratio differs from the stored one")
    new_splits = [SplitEvent(d, r) for d, r in sorted(frame_splits.items()) if d not in stored]
    all_splits = [*known, *new_splits]

    # Distributions AS PAID (step 5b): yfinance reports them on the split-adjusted basis (MEASURED
    # 2026-11-18), so the amount is multiplied back by the ratios of splits after the ex-date, as
    # prices are. Exactly: a product that would have to round fails the symbol (Inexact trapped).
    stored_distributions = {(x.effective_at, x.kind): x.amount for x in known_distributions}
    new_distributions = []
    for (ex_date, kind), provider_amount in sorted(frame_distributions.items()):
        factor = _factor(all_splits, ex_date)
        with localcontext() as exact:
            exact.traps[Inexact] = True
            try:
                as_paid = provider_amount * factor
            except Inexact:
                raise NonExactAmount(f"{ticker}: a rebuilt distribution is not exact") from None
        if (ex_date, kind) in stored_distributions:
            if stored_distributions[(ex_date, kind)] != as_paid:
                raise DistributionConflict(f"{ticker}: a distribution differs from the stored one")
            continue
        basis = "reconstructed" if factor != 1 else "live"
        new_distributions.append(DistributionEvent(ex_date, kind, as_paid, basis))

    bars: list[SettledBar] = []
    for key in order:
        o, h, lo, c, _adj, v = rows[key]
        if interval == DAILY and not isinstance(key, datetime):
            session = calendar.get(key)
            assert session is not None
            session_date, effective_at = key, session.opens_at
        else:
            assert isinstance(key, datetime)
            session_date = key.astimezone(NEW_YORK).date()
            session = calendar.get(session_date)
            # D5 iii: only bars inside that day's trading_sessions window; nothing after an early
            # close, nothing before the open.
            if (
                session is None
                or key < session.opens_at
                or key + INTERVAL_LENGTH[interval] > session.closes_at
            ):
                result.outside_session += 1
                continue
            effective_at = key
        # Every row here is settled (the first pass dropped the rest, by the clock).
        settle = calendar.settle_point(session_date, interval)
        assert settle is not None
        # Intraday rows carry no Stock Splits column, so a 15m batch rebuilds from `known` only.
        # STEP 6 REQUIREMENT (Kyle, 2026-11-18): every 15m run first fetches its own daily
        # actions=True batch (same symbols, same window plus the next session) and checks any new
        # split here, so `known` is complete before a 15m bar is accepted. Never a schedule rule.
        factor = _factor(all_splits, session_date)
        volume = Decimal(_whole(v))
        raw_volume = volume / factor
        if raw_volume != raw_volume.to_integral_value():
            raise NonIntegerVolume(f"{ticker}: rebuilt volume is not a whole number")
        bars.append(
            SettledBar(
                ticker=ticker,
                interval=interval,
                session_date=session_date,
                effective_at=effective_at,
                o_raw=_decimal(o) * factor,
                h_raw=_decimal(h) * factor,
                l_raw=_decimal(lo) * factor,
                c_raw=_decimal(c) * factor,
                v_raw=int(raw_volume),
                raw_basis="reconstructed" if factor != 1 else "live",
                available_at=settle,
            )
        )
    return bars, new_splits, new_distributions


# --- D4: what an empty symbol means, and the run's failure ----------------------------------------


def classify_dead(
    dead: Iterable[str],
    *,
    last_stored: Mapping[str, date | None],
    newest_stored: date | None,
    calendar: Calendar,
) -> list[SymbolFailure]:
    """D4: empty today, or likely gone (its last stored bar is LIKELY_GONE_SESSIONS or more sessions
    behind the newest session ANY symbol stored, so an outage that empties every symbol never reads
    as retirements). `newest_stored` must include what this batch just stored."""
    out = []
    for ticker in dead:
        last = last_stored.get(ticker)
        behind = (
            calendar.sessions_between(last, newest_stored)
            if last is not None and newest_stored is not None
            else 0
        )
        if behind >= LIKELY_GONE_SESSIONS:
            out.append(
                SymbolFailure(ticker, LikelyGone(f"{ticker}: empty, {behind} sessions behind"))
            )
        else:
            out.append(SymbolFailure(ticker, EmptyToday(f"{ticker}: no rows in this batch")))
    return out


def run_failure(failures: Sequence[SymbolFailure]) -> ProviderError | None:
    """The one exception that fails the run after the good symbols' rows are written (D4).

    Its text is FlowDesk's own: the tickers and what was wrong, never anything the library said.
    DECISION: precedence is broken data, then likely gone, then empty today. Broken data means an
    assumption of this client failed and comes first; every group is still named in the text.
    """
    if not failures:
        return None
    by_kind: dict[type, list[str]] = defaultdict(list)
    for f in failures:
        by_kind[type(f.error)].append(f.ticker)
    broken = sorted(t for f in failures if isinstance(f.error, SchemaMismatch) for t in [f.ticker])
    gone = sorted(by_kind.get(LikelyGone, []))
    empty = sorted(by_kind.get(EmptyToday, []))
    parts = []
    if broken:
        parts.append("data broke a rule for: " + ", ".join(broken))
    if gone:
        parts.append(
            f"empty for {LIKELY_GONE_SESSIONS} or more sessions, suspected gone (an operator "
            "closes its universe membership; never automatic): " + ", ".join(gone)
        )
    if empty:
        parts.append("no rows in this batch for: " + ", ".join(empty))
    text = "; ".join(parts)
    if broken:
        return SymbolsFailed(text)
    if gone:
        return LikelyGoneSymbols(text)
    return DeadSymbols(text)
