"""The FRED client (M2 slice 5, step 2; ADR-0003; §6.4).

It fetches series observations and maps every failure into the five-kind taxonomy
(app.ingest.errors). It writes nothing: storing vintages is step 3, reading them back point-in-time
is step 4. The framework above it never sees an httpx2 type.

Three things in here exist because of what FRED does, not because of taste:

* The API key rides in the URL's query string. So no exception message, log line or stored string
  may contain the request URL or anything FRED sent back. Errors carry fixed text and the exception
  CLASS only, and the HTTP library's own request log is held at WARNING (app.logging).
* A vintage output type answers 400 "No vintage dates exist" when its real-time window holds no
  vintage date. What that means depends on who asked (ADR-0003 addendum, 2026-11-13): for a
  backfill, whose windows are built from the series' own vintage dates, it is a bug or a moved
  record, so it is raised as NoVintageDates (a Permanent) and fails the run; for a poll over a
  short window it is a quiet day, so a caller passes `no_vintages_is_empty=True` and gets []. The
  default is the fatal reading. `observations` still refuses output types 2, 3 or 4 without an
  explicit window, because the DEFAULT window (today to today) is the wrong-parameter case.
* "." is a genuine gap (ADR-0003 decision 7): kept as a row with value None, never 0.0, never
  forward-filled. NaN, Infinity and anything else that is not a plain decimal is rejected.

The rate budget (60 a minute, half FRED's documented 120) is declared in settings.RATE_BUDGETS.
"""

import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import TracebackType
from typing import Any, Self

import httpx2
import structlog

from app.ingest.errors import (
    GatewayTimeout,
    NoVintageDates,
    Permanent,
    RateLimited,
    SchemaMismatch,
    Transient,
)
from app.ingest.macro import HISTORY_WINDOW, VINTAGE_DATE_CAP, FredVintageRow, chunk_vintage_dates

log = structlog.get_logger(__name__)

BASE_URL = "https://api.stlouisfed.org/fred"

# ADR-0003 decisions 3 and 4: output_type=4 is "initial release only", and it needs a window wide
# enough to contain every vintage. The start is arbitrary (any date before the series' first vintage
# works; 1776-07-04 is not itself significant). The end is the largest date FRED accepts.
OUTPUT_TYPE_LATEST = 1
OUTPUT_TYPE_FIRST_PRINT = 4
FIRST_PRINT_WINDOW_START = "1776-07-04"
FIRST_PRINT_WINDOW_END = "9999-12-31"
_VINTAGE_OUTPUT_TYPES = frozenset({2, 3, 4})

# series/observations: `limit` is an integer from 1 to 100000 (FRED's documentation).
MAX_PAGE_SIZE = 100_000
# series/vintagedates: `limit` up to 10000 (its default), paged by offset like observations.
MAX_VINTAGEDATES_PAGE = 10_000
# A guard against a provider that never reaches its own `count`: 100 pages is 10 million rows.
MAX_PAGES = 100

# DECISION: placeholders, not measurements. No real FRED call has been timed from this project; the
# first natural failures will say whether these are sensible.
TIMEOUT = httpx2.Timeout(30.0, connect=10.0)
# output_type=3 requests are slow by nature: about 0.16 s per vintage date in the window (measured
# 2026-11-13), so a 300-date window takes 48 s and a 100-date one 17 s. At the 30 s above the client
# would give up on answers FRED was about to send, and would report a client timeout where it must
# see FRED's own 504 (at 60 s) to know to halve the window. 70 s is above the gateway's 60.
HISTORY_TIMEOUT = httpx2.Timeout(70.0, connect=10.0)

_SERIES_ID = re.compile(r"[A-Za-z0-9_]{1,64}")
# Plain decimals only. [0-9], not \d: \d matches other scripts' digits, which Decimal also accepts.
_NUMBER = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")
# Never logged, whatever FRED sends.
_HEADERS_NEVER_LOGGED = frozenset({"set-cookie", "cookie", "authorization"})


@dataclass(frozen=True)
class FredObservation:
    """One row of series/observations, typed.

    `effective_at` is the period the value describes (FRED's `date`). `realtime_start` is when this
    value was published: for output_type=4 that is the first print's publication date, which is a
    DATE (the store turns it into `available_at`, ADR-0003 addendum of 2026-11-11). `value` is None
    for FRED's "." gap.
    """

    series_id: str
    effective_at: date
    value: Decimal | None
    realtime_start: date
    realtime_end: date


@dataclass(frozen=True)
class RevisionHistory:
    """Everything `revision_history` fetched: the vintage dates, the wide rows of every window, and
    how the windowing went (`splits` > 0 means FRED's gateway timed a window out; `final_window` is
    the size the last windows used)."""

    vintage_dates: tuple[date, ...]
    rows: tuple[FredVintageRow, ...]
    splits: int
    final_window: int


class FredClient:
    """Use as `async with FredClient(...) as fred:`.

    `before_next_request` is awaited before every request this client makes after its first. The
    runner takes a rate-limit slot per attempt, which covers the first request; a paged or
    chunked call makes more, and each is a request against the budget, so the caller passes its
    limiter's acquire here. Build one client per attempt, so the count starts again with the slot.
    """

    def __init__(
        self,
        api_key: str,
        *,
        user_agent: str,
        transport: httpx2.AsyncBaseTransport | None = None,
        base_url: str = BASE_URL,
        page_size: int = MAX_PAGE_SIZE,
        before_next_request: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("a FRED api key is required")
        if not user_agent.strip():
            raise ValueError("an explicit User-Agent is required (not the HTTP library's default)")
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
        self._api_key = api_key
        self._page_size = page_size
        self._before_next_request = before_next_request
        self._requests_sent = 0
        # follow_redirects=False on purpose: a redirect would send the key to whatever host
        # answered.
        self._client = httpx2.AsyncClient(
            base_url=base_url,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=TIMEOUT,
            follow_redirects=False,
            transport=transport,
        )

    def __repr__(self) -> str:
        return "FredClient(api_key=[redacted])"

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def first_prints(self, series_id: str) -> list[FredObservation]:
        """First prints over the whole widened window, in ONE request (ADR-0003).

        FRED refuses this for a series with more than 2000 vintage dates (measured 2026-11-12:
        six of the seventeen in 6.4), with an explicit 400 that this client raises as Permanent.
        The backfill uses `first_prints_chunked`.
        """
        return await self.first_prints_window(
            series_id,
            date.fromisoformat(FIRST_PRINT_WINDOW_START),
            date.fromisoformat(FIRST_PRINT_WINDOW_END),
        )

    async def first_prints_window(
        self, series_id: str, start: date, end: date
    ) -> list[FredObservation]:
        """First prints whose publication falls in the real-time window [start, end]."""
        return await self.observations(
            series_id,
            output_type=OUTPUT_TYPE_FIRST_PRINT,
            realtime_start=start,
            realtime_end=end,
        )

    async def vintage_dates(self, series_id: str) -> list[date]:
        """Every date on which the series' data changed, oldest first (series/vintagedates)."""
        if not _SERIES_ID.fullmatch(series_id):
            raise ValueError("invalid FRED series id")
        found: list[date] = []
        offset = 0
        for _ in range(MAX_PAGES):
            response = await self._get(
                "/series/vintagedates",
                {
                    "series_id": series_id,
                    "api_key": self._api_key,
                    "file_type": "json",
                    "realtime_start": FIRST_PRINT_WINDOW_START,
                    "realtime_end": FIRST_PRINT_WINDOW_END,
                    "limit": str(MAX_VINTAGEDATES_PAGE),
                    "offset": str(offset),
                },
            )
            page, count = _parse_vintage_dates(response)
            found.extend(page)
            offset += len(page)
            if offset >= count:
                return sorted(found)
            if not page:
                raise SchemaMismatch("FRED returned an empty page before reaching its count")
        raise SchemaMismatch(f"FRED never reached its own count within {MAX_PAGES} pages")

    async def first_prints_chunked(
        self, series_id: str, vintage_dates: Sequence[date] | None = None
    ) -> list[FredObservation]:
        """First prints for any series: the vintage dates are split into windows FRED accepts.

        A window holding more than 2000 vintage dates is refused (an explicit 400, never a
        silent cut), so every series is chunked, including the ones that fit today (SOFR was 98
        short of the cap when measured). Measured on the six series over the cap: the windows
        answer 200, `count` equals the rows returned, and no observation date appears in two
        windows. The caller still has to check the union against the other outputs: see
        app.ingest.macro.check_history. `vintage_dates` may be passed when the caller already
        holds them (one request saved).
        """
        if vintage_dates is None:
            dates = await self.vintage_dates(series_id)
        else:
            dates = list(vintage_dates)
        rows: list[FredObservation] = []
        for start, end in chunk_vintage_dates(dates):
            rows.extend(await self.first_prints_window(series_id, start, end))
        return rows

    async def revision_history(
        self, series_id: str, *, window: int = HISTORY_WINDOW
    ) -> RevisionHistory:
        """A series' whole revision history, with each revision's true vintage date
        (`output_type=3`, ADR-0003 addendum 2026-11-13).

        The vintage dates are split into disjoint windows of at most `window` dates, so the union
        of the answers is the whole answer. FRED's gateway answers 504 at 60 s to a window it cannot
        finish; a window that does is **halved and tried again, from the same start**, and the
        smaller size is kept for the rest of the series (one slow stretch must not cost a 60 s
        failure per window). A single date that still times out is raised: nothing smaller exists.
        Every attempt, including a timed-out one, takes a slot of the rate budget.
        """
        if not 1 <= window <= VINTAGE_DATE_CAP:
            raise ValueError(f"window must be between 1 and {VINTAGE_DATE_CAP}")
        dates = await self.vintage_dates(series_id)
        rows: list[FredVintageRow] = []
        size, splits, i = window, 0, 0
        while i < len(dates):
            chunk = dates[i : i + size]
            try:
                rows.extend(await self._revision_window(series_id, chunk[0], chunk[-1]))
            except GatewayTimeout:
                if len(chunk) == 1:
                    raise
                size = max(1, len(chunk) // 2)
                splits += 1
                log.warning(
                    "fred_window_split", series=series_id, vintages=len(chunk), next_size=size
                )
                continue
            i += len(chunk)
        return RevisionHistory(tuple(dates), tuple(rows), splits, size)

    async def _revision_window(
        self, series_id: str, start: date, end: date
    ) -> list[FredVintageRow]:
        """`output_type=3` for one real-time window, all pages. A window built from the series' own
        vintage dates always holds one, so "No vintage dates exist" here is NOT a quiet day: it is
        raised (NoVintageDates), and it fails the run."""
        if not _SERIES_ID.fullmatch(series_id):
            raise ValueError("invalid FRED series id")
        params = {
            "series_id": series_id,
            "api_key": self._api_key,
            "file_type": "json",
            "output_type": "3",
            "realtime_start": start.isoformat(),
            "realtime_end": end.isoformat(),
            "limit": str(self._page_size),
        }
        rows: list[FredVintageRow] = []
        offset = 0
        for _ in range(MAX_PAGES):
            response = await self._get(
                "/series/observations",
                {**params, "offset": str(offset)},
                timeout=HISTORY_TIMEOUT,
            )
            page, count = _parse_wide_page(response, series_id)
            rows.extend(page)
            offset += len(page)
            if offset >= count:
                return rows
            if not page:
                raise SchemaMismatch("FRED returned an empty page before reaching its count")
        raise SchemaMismatch(f"FRED never reached its own count within {MAX_PAGES} pages")

    async def latest(
        self, series_id: str, *, observation_start: date | None = None
    ) -> list[FredObservation]:
        """Current values as of today (the daily poll). No real-time window, so FRED defaults to
        today."""
        return await self.observations(
            series_id, output_type=OUTPUT_TYPE_LATEST, observation_start=observation_start
        )

    async def observations(
        self,
        series_id: str,
        *,
        output_type: int = OUTPUT_TYPE_LATEST,
        realtime_start: date | None = None,
        realtime_end: date | None = None,
        observation_start: date | None = None,
        no_vintages_is_empty: bool = False,
    ) -> list[FredObservation]:
        # ValueError, not a ProviderError: these are bugs in our own code, caught before any
        # request is made.
        if not _SERIES_ID.fullmatch(series_id):
            raise ValueError("invalid FRED series id")
        if output_type in _VINTAGE_OUTPUT_TYPES and (
            realtime_start is None or realtime_end is None
        ):
            raise ValueError(
                f"output_type {output_type} needs an explicit real-time window: FRED answers 400 "
                "'No vintage dates exist' to the default window (ADR-0003 decision 4)"
            )
        if no_vintages_is_empty and output_type not in _VINTAGE_OUTPUT_TYPES:
            raise ValueError("no_vintages_is_empty only applies to vintage output types")
        params: dict[str, str] = {
            "series_id": series_id,
            "api_key": self._api_key,
            "file_type": "json",
            "output_type": str(output_type),
            "limit": str(self._page_size),
        }
        if realtime_start is not None:
            params["realtime_start"] = realtime_start.isoformat()
        if realtime_end is not None:
            params["realtime_end"] = realtime_end.isoformat()
        if observation_start is not None:
            params["observation_start"] = observation_start.isoformat()

        rows: list[FredObservation] = []
        offset = 0
        pages = 0
        while True:
            pages += 1
            if pages > MAX_PAGES:
                raise SchemaMismatch(f"FRED never reached its own count within {MAX_PAGES} pages")
            try:
                response = await self._get(
                    "/series/observations", {**params, "offset": str(offset)}
                )
            except NoVintageDates:
                if no_vintages_is_empty:
                    # A poll window in which nothing was published: a normal quiet day.
                    return rows
                raise
            page, count = _parse_page(response, series_id)
            rows.extend(page)
            offset += len(page)
            if offset >= count:
                return rows
            if not page:
                # Without this, a provider that keeps saying there is more and sends nothing
                # would loop us forever.
                raise SchemaMismatch("FRED returned an empty page before reaching its count")

    async def _get(
        self, path: str, params: dict[str, str], *, timeout: httpx2.Timeout | None = None
    ) -> httpx2.Response:
        if self._requests_sent and self._before_next_request is not None:
            await self._before_next_request()
        self._requests_sent += 1
        try:
            if timeout is None:
                response = await self._client.get(path, params=params)
            else:
                response = await self._client.get(path, params=params, timeout=timeout)
        except httpx2.HTTPError as exc:
            # The CLASS only. httpx2's message can carry the request URL, and the URL carries
            # the key.
            raise Transient(f"FRED request failed ({type(exc).__name__})") from None
        _raise_for_status(response)
        return response


def _loggable_headers(response: httpx2.Response) -> dict[str, str]:
    return {k: v for k, v in response.headers.items() if k.lower() not in _HEADERS_NEVER_LOGGED}


def _raise_for_status(response: httpx2.Response) -> None:
    """Map FRED's HTTP status into the taxonomy. Every message is fixed text: nothing FRED sends
    back is copied into an exception, because it may echo the request (and the key)."""
    code = response.status_code
    if 200 <= code < 300:
        return
    if code == 429:
        # FRED's errors page documents 429 past 120 requests a minute and says nothing about
        # Retry-After. The first natural 429 is how the real behaviour gets measured, so keep
        # everything but the URL, the key and cookies.
        log.warning("fred_rate_limited", status=code, headers=_loggable_headers(response))
        raise RateLimited("FRED rate limit (429)")
    if code == 400:
        message = _explain_400(response)
        if message.startswith(_NO_VINTAGE_PREFIX):
            raise NoVintageDates(message)
        raise Permanent(message)
    if code in (401, 403):
        # Never transient: retrying a refused key or a blocked client only hammers what refused it.
        raise Permanent(f"FRED refused the request ({code}): key rejected or client blocked")
    if 300 <= code < 400:
        raise Permanent(f"FRED redirected ({code}); redirects are not followed")
    if code == 504:
        # Not logged as unexpected: the windowed fetch expects it and answers by halving the window.
        raise GatewayTimeout("FRED answered 504 (gateway timeout)")
    if code == 423 or code >= 500:
        # FRED's errors page lists the complete set of statuses it may return: 400, 404, 423,
        # 429, 500 (read by Kyle, 2026-11-12). 423 and 500 are listed but unexplained, so
        # treating them as transient is a JUDGEMENT CALL, not something the docs say: a gap in
        # the docs would be a missing entry, and this is a present entry with no meaning
        # given. Bounded by the retry policy and Beat's next firing; logged so the real
        # behaviour can be learned. 404 is listed too and falls to the permanent branch below.
        log.warning("fred_unexpected_status", status=code, headers=_loggable_headers(response))
        raise Transient(f"FRED answered {code}")
    raise Permanent(f"FRED answered {code}")


_NO_VINTAGE_PREFIX = "FRED 400: no vintage dates exist"


def _explain_400(response: httpx2.Response) -> str:
    try:
        message = str(response.json().get("error_message", ""))
    except (ValueError, AttributeError):
        message = ""
    lowered = message.lower()
    if "maximum number of vintage dates" in lowered:
        # Observed 2026-11-12: a window holding more than 2000 vintage dates is refused outright
        # (HTTP 400 with the count and the limit), never silently shortened. The caller must split
        # the real-time window; this client does not.
        return (
            "FRED 400: the real-time window holds more than the maximum number of vintage dates "
            "(2000); split the window"
        )
    if "no vintage dates" in lowered:
        # Raised by _raise_for_status as NoVintageDates; the text claims nothing about the cause.
        return (
            "FRED 400: no vintage dates exist in this real-time window (a bug for a backfill, a "
            "quiet day for a poll: ADR-0003 addendum 2026-11-13)"
        )
    if "series does not exist" in lowered:
        return "FRED 400: unknown series id (ADR-0003 decision 8)"
    if "api_key" in lowered:
        return "FRED 400: the api key is missing, malformed or not registered"
    return "FRED 400: the request was rejected"


def _parse_page(response: httpx2.Response, series_id: str) -> tuple[list[FredObservation], int]:
    try:
        data: Any = response.json()
    except ValueError:
        raise SchemaMismatch("FRED returned a body that is not JSON") from None
    if not isinstance(data, dict):
        raise SchemaMismatch("FRED returned JSON that is not an object")
    count, items = data.get("count"), data.get("observations")
    if not isinstance(count, int) or isinstance(count, bool) or not isinstance(items, list):
        raise SchemaMismatch("FRED response has no usable count and observations")
    rows: list[FredObservation] = []
    for item in items:
        try:
            effective_at = date.fromisoformat(item["date"])
            realtime_start = date.fromisoformat(item["realtime_start"])
            realtime_end = date.fromisoformat(item["realtime_end"])
            raw = item["value"]
        except (KeyError, TypeError, ValueError):
            raise SchemaMismatch("an observation has a missing field or a malformed date") from None
        rows.append(
            FredObservation(
                series_id=series_id,
                effective_at=effective_at,
                value=_parse_value(raw),
                realtime_start=realtime_start,
                realtime_end=realtime_end,
            )
        )
    return rows, count


def _parse_wide_page(response: httpx2.Response, series_id: str) -> tuple[list[FredVintageRow], int]:
    """output_type=3: `{"date": ..., "<SERIES>_<YYYYMMDD>": value, ...}` per period, one column for
    each vintage in which the period changed (shape recorded 2026-11-13, tests/fixtures/fred/)."""
    try:
        data: Any = response.json()
    except ValueError:
        raise SchemaMismatch("FRED returned a body that is not JSON") from None
    if not isinstance(data, dict):
        raise SchemaMismatch("FRED returned JSON that is not an object")
    count, items = data.get("count"), data.get("observations")
    if not isinstance(count, int) or isinstance(count, bool) or not isinstance(items, list):
        raise SchemaMismatch("FRED response has no usable count and observations")
    prefix = f"{series_id}_"
    rows: list[FredVintageRow] = []
    for item in items:
        if not isinstance(item, dict):
            raise SchemaMismatch("an observation is not an object")
        try:
            effective_at = date.fromisoformat(item["date"])
        except (KeyError, TypeError, ValueError):
            raise SchemaMismatch("an observation has a missing or malformed date") from None
        values: dict[date, Decimal | None] = {}
        for key, raw in item.items():
            if key == "date":
                continue
            stamp = key[len(prefix) :]
            if not key.startswith(prefix) or not re.fullmatch(r"[0-9]{8}", stamp):
                raise SchemaMismatch("an observation has a column that is not a vintage")
            try:
                vintage = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
            except ValueError:
                raise SchemaMismatch("a vintage column has an impossible date") from None
            values[vintage] = _parse_value(raw)
        # A row with only a date is NORMAL (recorded 2026-11-13: 32 of 515 rows in one CSUSHPINSA
        # window): the period exists but did not change inside this window. It says nothing about
        # any vintage, so it is kept as an empty row and ignored by the check, never an error.
        rows.append(FredVintageRow(effective_at, values))
    return rows, count


def _parse_vintage_dates(response: httpx2.Response) -> tuple[list[date], int]:
    try:
        data: Any = response.json()
    except ValueError:
        raise SchemaMismatch("FRED returned a body that is not JSON") from None
    if not isinstance(data, dict):
        raise SchemaMismatch("FRED returned JSON that is not an object")
    count, items = data.get("count"), data.get("vintage_dates")
    if not isinstance(count, int) or isinstance(count, bool) or not isinstance(items, list):
        raise SchemaMismatch("FRED response has no usable count and vintage_dates")
    try:
        return [date.fromisoformat(item) for item in items], count
    except (TypeError, ValueError):
        raise SchemaMismatch("a vintage date is malformed") from None


def _parse_value(raw: Any) -> Decimal | None:
    if raw == ".":
        return None  # a genuine gap: kept as a row, never 0.0 (ADR-0003 decision 7)
    if isinstance(raw, str) and _NUMBER.fullmatch(raw):
        return Decimal(raw)
    # The value is deliberately not echoed: it came from the provider and is rejected, not
    # guessed at.
    raise SchemaMismatch("an observation value is neither a number nor '.'")
