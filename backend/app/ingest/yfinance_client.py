"""The yfinance client (M2 slice 6, step 3; ADR-0002; §6.12).

It fetches one batch and maps every failure into the five-kind taxonomy (app.ingest.errors). It
writes nothing and judges nothing: `app.ingest.bars.parse_batch` turns the batch into settled,
as-traded bars. The framework above never sees a yfinance, curl_cffi or requests type.

Why it calls `yfinance.multi._download_impl` (a private function) and not `yf.download`: `download`
catches EVERY per-ticker exception, a rate limit and a network failure included, keeps it in a
`_DownloadCtx` it creates and discards, and returns an empty group for that ticker (MEASURED
2026-11-18 by reading yfinance 1.8.0). In the frame alone, a rate-limited or offline symbol is
indistinguishable from a dead one, and a dead one feeds D4's likely_gone prompt. Passing our own
context keeps those errors so they can be classified (they are never stored). This depends on the
pinned 1.8.0; a version bump re-checks it (a test pins the call's shape against the recorded
fixtures). Hard rule: a drifted provider keeps the interface and adapts the client internals.

Every call: `auto_adjust=False` always and explicit (ADR-0002: the default silently returns
total-return prices), `prepost=False`, grouped by ticker, actions (splits, dividends, capital gains)
for daily only. Nothing the library says is ever stored: exceptions raised here carry fixed text.
"""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd  # type: ignore[import-untyped]

from app.ingest.bars import DAILY, INTRADAY_15M
from app.ingest.errors import Permanent, ProviderError, RateLimited, Transient

# Decided 2026-11-16: a 15m `start=` request reaching back more than 60 calendar days is refused by
# Yahoo (MEASURED); the gap limit is 59, and no `period=` fallback is used.
INTRADAY_START_LIMIT_DAYS = 59
INTERVALS = (DAILY, INTRADAY_15M)


@dataclass(frozen=True)
class RawBatch:
    frame: pd.DataFrame
    errors: dict[str, str]  # yfinance's per-ticker errors: for classification only, never stored


Download = Callable[..., tuple[pd.DataFrame | None, dict[str, str]]]


def yf_download(
    tickers: Sequence[str],
    *,
    interval: str,
    start: date | None,
    period: str | None,
    actions: bool,
) -> tuple[pd.DataFrame | None, dict[str, str]]:
    """One `_download_impl` call with our own context (module docstring)."""
    from yfinance.multi import _download_impl, _DownloadCtx  # type: ignore[import-untyped]

    ctx = _DownloadCtx()
    frame = _download_impl(
        ctx,
        list(tickers),
        start=start.isoformat() if start else None,
        period=period,
        interval=interval,
        actions=actions,
        auto_adjust=False,
        prepost=False,
        group_by="ticker",
        threads=True,
        progress=False,
    )
    return frame, dict(ctx.errors)


def map_exception(exc: Exception) -> Exception:
    """A library exception as a FlowDesk ProviderError with fixed text. An exception this client
    does not know is returned as it is: the framework stores it as `<kind>:<Class>` only."""
    import curl_cffi
    import requests
    import yfinance.exceptions as yfe  # type: ignore[import-untyped]

    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, yfe.YFRateLimitError):
        return RateLimited("yfinance reported a rate limit")
    if isinstance(exc, yfe.YFInvalidPeriodError):
        return Permanent("yfinance refused the requested period")
    if isinstance(exc, curl_cffi.CurlError | requests.exceptions.RequestException | TimeoutError):
        return Transient("the request to yfinance failed")
    if isinstance(exc, ConnectionError):
        return Transient("the connection to yfinance failed")
    if isinstance(exc, yfe.YFException):
        return Permanent("yfinance refused the request")
    return exc


class YFinanceClient:
    def __init__(self, *, max_batch: int, download: Download = yf_download) -> None:
        self._max_batch = max_batch
        self._download = download

    async def fetch(
        self,
        tickers: Sequence[str],
        interval: str,
        *,
        today: date,
        start: date | None = None,
        period: str | None = None,
    ) -> RawBatch:
        """One batch. Exactly one of `start` (the gap-driven poll) or `period` (the backfill)."""
        if interval not in INTERVALS:
            raise Permanent("unknown interval")
        if not 1 <= len(tickers) <= self._max_batch:
            raise Permanent("batch size outside the rate budget's max_batch")
        if len(set(tickers)) != len(tickers):
            raise Permanent("a ticker is requested twice")
        if (start is None) == (period is None):
            raise Permanent("give exactly one of start or period")
        if (
            interval == INTRADAY_15M
            and start is not None
            and start < today - timedelta(days=INTRADAY_START_LIMIT_DAYS)
        ):
            # The poll records an unrecoverable gap beyond this; asking would be refused anyway.
            raise Permanent("15m start is beyond the 59-day limit")
        try:
            frame, errors = await asyncio.to_thread(
                self._download,
                tickers,
                interval=interval,
                start=start,
                period=period,
                actions=interval == DAILY,
            )
        except Exception as exc:
            mapped = map_exception(exc)
            if mapped is exc:
                raise
            raise mapped from None
        return RawBatch(frame=frame if frame is not None else pd.DataFrame(), errors=errors)
