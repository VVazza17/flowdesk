"""M2 slice 5, step 2: the FRED client. No network, no database: httpx2's MockTransport plays FRED.

What these pin, from ADR-0003 and the slice 5 plan:
  * the request shapes (output_type=4 with the widened window; the default window can never be sent
    with a vintage output type, because FRED answers that 400 "No vintage dates exist")
  * every failure lands in the five-kind taxonomy, and no error text, log line or stored string can
    carry the API key (it rides in the URL)
  * "." is a gap (None), never 0.0; NaN and garbage are rejected, not guessed at
  * paging: every page after the first takes a rate-limit slot, and a misbehaving provider cannot
    loop the client forever
  * an explicit User-Agent, not httpx2's default
"""

import asyncio
import io
import json
import logging
from collections.abc import Callable, Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import httpx2
import pytest
import structlog

from app import settings
from app.ingest.errors import (
    SCHEMA_MISMATCH,
    Permanent,
    ProviderError,
    RateLimited,
    SchemaMismatch,
    Transient,
    is_retryable,
    kind_of,
    run_error,
)
from app.ingest.fred import (
    FIRST_PRINT_WINDOW_END,
    FIRST_PRINT_WINDOW_START,
    FredClient,
    FredObservation,
)
from app.logging import configure_logging

KEY = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
UA = "FlowDesk-test/1.0"

Handler = Callable[[httpx2.Request], httpx2.Response]


def obs(
    day: str, value: str, rt_start: str = "2025-04-30", rt_end: str = "2025-05-29"
) -> dict[str, str]:
    return {"realtime_start": rt_start, "realtime_end": rt_end, "date": day, "value": value}


def body(
    observations: list[dict[str, str]],
    *,
    count: int | None = None,
    offset: int = 0,
    limit: int = 100000,
) -> dict[str, Any]:
    return {
        "realtime_start": "2026-11-11",
        "realtime_end": "2026-11-11",
        "output_type": 4,
        "file_type": "json",
        "count": len(observations) if count is None else count,
        "offset": offset,
        "limit": limit,
        "observations": observations,
    }


def ok(observations: list[dict[str, str]], **kw: Any) -> httpx2.Response:
    return httpx2.Response(200, json=body(observations, **kw))


def make(handler: Handler, **kw: Any) -> FredClient:
    return FredClient(KEY, user_agent=UA, transport=httpx2.MockTransport(handler), **kw)


def run_first_prints(handler: Handler, series: str = "GDPC1", **kw: Any) -> list[FredObservation]:
    async def go() -> list[FredObservation]:
        async with make(handler, **kw) as fred:
            return await fred.first_prints(series)

    return asyncio.run(go())


def fails_with(handler: Handler, error: type[ProviderError]) -> ProviderError:
    with pytest.raises(error) as info:
        run_first_prints(handler)
    return info.value


# --- request shapes -------------------------------------------------------------------------------


def test_first_prints_asks_for_initial_releases_over_the_widened_window() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return ok([obs("2025-01-01", "23526.085")])

    run_first_prints(handler)
    (request,) = seen
    q = dict(request.url.params)
    assert request.url.path.endswith("/series/observations")
    assert q["series_id"] == "GDPC1" and q["api_key"] == KEY and q["file_type"] == "json"
    # ADR-0003 decisions 3 and 4: output_type=4 AND the explicit window, or FRED answers 400.
    assert q["output_type"] == "4"
    assert q["realtime_start"] == FIRST_PRINT_WINDOW_START == "1776-07-04"
    assert q["realtime_end"] == FIRST_PRINT_WINDOW_END == "9999-12-31"
    assert q["limit"] == "100000" and q["offset"] == "0"


def test_latest_sends_no_real_time_window_so_fred_defaults_to_today() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return ok([obs("2025-01-01", "1")])

    async def go() -> None:
        async with make(handler) as fred:
            await fred.latest("DGS10", observation_start=date(2026, 1, 1))

    asyncio.run(go())
    q = dict(seen[0].url.params)
    assert q["output_type"] == "1" and q["observation_start"] == "2026-01-01"
    assert "realtime_start" not in q and "realtime_end" not in q


@pytest.mark.parametrize("output_type", [2, 3, 4])
def test_a_vintage_output_type_without_a_window_is_refused_before_any_request(
    output_type: int,
) -> None:
    # The "No vintage dates exist" 400 reads like a data-availability error but is a wrong-parameter
    # error (ADR-0003 decision 4). The client makes it unreachable rather than classifying it.
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("no request may be sent")

    async def go() -> None:
        async with make(handler) as fred:
            await fred.observations("GDPC1", output_type=output_type)

    with pytest.raises(ValueError, match="real-time window"):
        asyncio.run(go())


@pytest.mark.parametrize("bad", ["", "GDP C1", "GDPC1&api_key=x", "../x", "a" * 65])
def test_a_series_id_that_could_alter_the_query_is_refused(bad: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("no request may be sent")

    with pytest.raises(ValueError, match="series id"):
        run_first_prints(handler, series=bad)


def test_the_user_agent_is_explicit_and_carries_no_personal_contact() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return ok([])

    run_first_prints(handler)
    assert seen[0].headers["user-agent"] == UA
    assert "httpx" not in seen[0].headers["user-agent"].lower()
    # The shipped default identifies the app and nobody else.
    default = settings.fred_user_agent()
    assert default.startswith("FlowDesk") and "@" not in default


def test_a_redirect_is_not_followed_and_is_permanent() -> None:
    # Following a redirect would send the key to whatever host answered.
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(302, headers={"location": "https://elsewhere.example/"})

    err = fails_with(handler, Permanent)
    assert not is_retryable(err)


# --- parsing --------------------------------------------------------------------------------------


def test_observations_are_typed_with_exact_decimals_and_their_publication_date() -> None:
    rows = run_first_prints(
        lambda r: ok([obs("2025-01-01", "23526.085", "2025-04-30", "2025-05-29")])
    )
    assert rows == [
        FredObservation(
            series_id="GDPC1",
            effective_at=date(2025, 1, 1),
            value=Decimal("23526.085"),
            realtime_start=date(2025, 4, 30),
            realtime_end=date(2025, 5, 29),
        )
    ]


def test_a_dot_is_a_gap_kept_as_a_row_never_zero() -> None:
    # ADR-0003 decision 7. CPIAUCSL 2025-10-01 is the recorded example.
    rows = run_first_prints(lambda r: ok([obs("2025-10-01", ".")]), series="CPIAUCSL")
    assert len(rows) == 1
    assert rows[0].value is None
    assert rows[0].effective_at == date(2025, 10, 1)


@pytest.mark.parametrize(
    "junk", ["NaN", "nan", "Infinity", "-Infinity", "1e5", "", "12,5", "abc", " "]
)
def test_a_value_that_is_neither_a_number_nor_a_dot_is_rejected(junk: str) -> None:
    # Decimal("NaN") is valid Python; a NaN stored as a vintage would poison every comparison.
    err = fails_with(lambda r: ok([obs("2025-01-01", junk)]), SchemaMismatch)
    assert kind_of(err) == SCHEMA_MISMATCH and not is_retryable(err)


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, text="not json at all"),
        httpx2.Response(200, json={"count": 0}),
        httpx2.Response(200, json={"observations": []}),
        httpx2.Response(200, json={"count": 1, "observations": [{"date": "2025-01-01"}]}),
        httpx2.Response(200, json={"count": 1, "observations": [obs("not-a-date", "1")]}),
        httpx2.Response(200, json=[1, 2, 3]),
    ],
)
def test_a_payload_that_is_not_the_known_shape_is_a_schema_mismatch(
    response: httpx2.Response,
) -> None:
    assert not is_retryable(fails_with(lambda r: response, SchemaMismatch))


def test_an_empty_but_well_formed_page_is_returned_empty_not_invented_or_hidden() -> None:
    # Whether empty is a failure is the ingest's call (step 3): for a poll it can be legitimate.
    assert run_first_prints(lambda r: ok([])) == []


# --- paging ---------------------------------------------------------------------------------------


def test_paging_follows_offsets_and_takes_a_slot_for_every_page_after_the_first() -> None:
    offsets: list[str] = []
    slots = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        q = dict(request.url.params)
        offsets.append(q["offset"])
        start = int(q["offset"])
        rows = [obs(f"2025-01-{d:02d}", str(d)) for d in range(start + 1, min(start + 2, 5) + 1)]
        return ok(rows, count=5, offset=start, limit=2)

    async def pace() -> None:
        nonlocal slots
        slots += 1

    rows = run_first_prints(handler, page_size=2, before_next_page=pace)
    assert offsets == ["0", "2", "4"]
    assert [r.value for r in rows] == [Decimal(d) for d in (1, 2, 3, 4, 5)]
    # The runner's slot covers the first request of an attempt; the client takes the others.
    assert slots == 2


def test_a_page_that_returns_nothing_before_the_count_is_reached_cannot_loop_forever() -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return ok([], count=10)

    fails_with(handler, SchemaMismatch)
    assert calls == 1


def test_a_provider_that_never_reaches_the_count_hits_the_page_cap() -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return ok([obs("2025-01-01", "1")], count=10**9, limit=1)

    with pytest.raises(SchemaMismatch, match="pages"):
        run_first_prints(handler, page_size=1)
    assert calls <= 1000


# --- failure mapping: every failure lands in the taxonomy -----------------------------------------


def status(code: int, payload: dict[str, Any] | None = None) -> Handler:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(code, json=payload or {"error_code": code, "error_message": "x"})

    return handler


def test_429_is_rate_limited_and_retryable() -> None:
    err = fails_with(status(429), RateLimited)
    assert is_retryable(err)


@pytest.mark.parametrize("code", [500, 502, 503, 504, 423])
def test_server_side_and_locked_responses_are_transient_and_retryable(code: int) -> None:
    # 423 and 500 are listed on FRED's errors page with no explanation (2026-11-11): treated as
    # transient, bounded by the retry policy and Beat's next firing.
    assert is_retryable(fails_with(status(code), Transient))


@pytest.mark.parametrize("code", [401, 403, 404])
def test_a_refusal_is_permanent_and_never_retried(code: int) -> None:
    # Retrying a refused key or a blocked client only hammers the thing that refused it.
    assert not is_retryable(fails_with(status(code), Permanent))


@pytest.mark.parametrize(
    ("message", "needle"),
    [
        (
            "Bad Request.  No vintage dates exist for the specified real-time period.",
            "configuration bug",
        ),
        ("Bad Request.  The series does not exist.", "unknown series"),
        ("Bad Request.  Variable api_key is not registered.", "api key"),
        ("Bad Request.  Something nobody has seen before.", "rejected"),
    ],
)
def test_each_400_is_permanent_and_says_what_it_is(message: str, needle: str) -> None:
    err = fails_with(status(400, {"error_code": 400, "error_message": message}), Permanent)
    assert needle in str(err).lower()
    assert not is_retryable(err)


def test_the_vintage_400_is_never_called_no_data() -> None:
    # ADR-0003 decision 4: a wrong-parameter error, "never 'no data yet'".
    msg = "Bad Request.  No vintage dates exist for the specified real-time period."
    err = fails_with(status(400, {"error_code": 400, "error_message": msg}), Permanent)
    assert "no data" not in str(err).lower()


@pytest.mark.parametrize(
    "exc",
    [
        httpx2.ConnectError("boom"),
        httpx2.ReadTimeout("slow"),
        httpx2.RemoteProtocolError("closed"),
        httpx2.ReadError("connection reset by peer"),
    ],
)
def test_a_network_failure_is_transient(exc: Exception) -> None:
    # A reset connection cannot be told from a network blip, so it stays transient (2026-11-11).
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise exc

    assert is_retryable(fails_with(handler, Transient))


# --- the key never appears anywhere it could be stored --------------------------------------------


def test_no_error_text_can_carry_the_key_even_if_fred_echoes_it() -> None:
    echo = f"Bad Request.  api_key={KEY} is not a 32 character alpha-numeric lower-case string."
    cases: list[Handler] = [
        status(400, {"error_code": 400, "error_message": echo}),
        status(429, {"error_code": 429, "error_message": echo}),
        status(500, {"error_code": 500, "error_message": echo}),
        status(403, {"error_code": 403, "error_message": echo}),
    ]

    def reset(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"cannot reach {request.url}")

    cases.append(reset)
    for handler in cases:
        with pytest.raises(ProviderError) as info:
            run_first_prints(handler)
        err = info.value
        assert KEY not in str(err) and KEY not in repr(err)
        # And the string the runner would store, even without being told the secret.
        assert KEY not in run_error(err)


def test_a_429_logs_its_status_and_headers_but_neither_the_url_nor_the_key() -> None:
    # The first natural 429 is how FRED's real behaviour (Retry-After or not) gets measured.
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            429, json={"error_code": 429}, headers={"retry-after": "30", "set-cookie": "s=1"}
        )

    with structlog.testing.capture_logs() as logs:
        fails_with(handler, RateLimited)
    event = next(e for e in logs if e["event"] == "fred_rate_limited")
    assert event["status"] == 429
    assert event["headers"]["retry-after"] == "30"
    assert "set-cookie" not in event["headers"]
    assert KEY not in json.dumps(logs, default=str)


@pytest.fixture
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    saved = {n: logging.getLogger(n).level for n in ("httpx2", "httpcore2")}
    yield
    root.handlers, root.level = handlers, level
    for name, lvl in saved.items():
        logging.getLogger(name).setLevel(lvl)


@pytest.mark.usefixtures("restore_logging")
def test_the_http_library_cannot_log_the_url_even_at_debug() -> None:
    # httpx2 logs 'HTTP Request: GET <url>' at INFO, and the url carries ?api_key=.
    stream = io.StringIO()
    configure_logging(level="DEBUG", stream=stream)
    run_first_prints(lambda r: ok([obs("2025-01-01", "1")]))
    assert KEY not in stream.getvalue()
    assert logging.getLogger("httpx2").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore2").getEffectiveLevel() >= logging.WARNING


# --- settings -------------------------------------------------------------------------------------


def test_the_api_key_is_validated_at_the_door_and_never_echoed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    assert settings.fred_api_key() is None
    monkeypatch.setenv("FRED_API_KEY", KEY)
    assert settings.fred_api_key() == KEY
    # FRED's own words for a malformed key: "not a 32 character alpha-numeric lower-case string".
    for bad in (
        "change-me",
        "TOOSHORT",
        KEY.upper(),
        KEY + "0",
        "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d!",
    ):
        monkeypatch.setenv("FRED_API_KEY", bad)
        with pytest.raises(ValueError, match="FRED_API_KEY") as info:
            settings.fred_api_key()
        assert bad not in str(info.value)


def test_the_user_agent_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRED_USER_AGENT", raising=False)
    assert settings.fred_user_agent().startswith("FlowDesk")
    monkeypatch.setenv("FRED_USER_AGENT", "   ")
    assert settings.fred_user_agent().startswith("FlowDesk")
    monkeypatch.setenv("FRED_USER_AGENT", "Custom/2")
    assert settings.fred_user_agent() == "Custom/2"


def test_fred_has_no_rate_budget_until_the_documented_limit_is_read_from_the_page() -> None:
    # A deliberate tripwire (docs/BUILD-LOG.md, slice 5 plan): the runner refuses a provider with no
    # budget, so FRED cannot run unthrottled while this stands. Kyle reads FRED's errors page,
    # quotes the rate-limit sentence into the BUILD-LOG, and only then adds the entry and flips
    # this test.
    assert "fred" not in settings.RATE_BUDGETS
