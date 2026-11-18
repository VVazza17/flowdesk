"""The failure taxonomy and credential scrubbing (M2 slice 4). No database."""

import pytest

from app.ingest import errors as e

KEY = "0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize(
    ("exc", "kind", "retryable"),
    [
        (e.Transient("x"), "transient", True),
        (e.RateLimited("x"), "rate_limited", True),
        (e.Permanent("x"), "permanent", False),
        (e.EmptyResult("x"), "empty_result", False),
        (e.SchemaMismatch("x"), "schema_mismatch", False),
        (e.BudgetUnavailable("x"), "budget_unavailable", False),
        (ValueError("x"), "unexpected", False),
    ],
)
def test_kinds_and_which_are_retried(exc: Exception, kind: str, retryable: bool) -> None:
    assert e.kind_of(exc) == kind
    assert e.is_retryable(exc) is retryable


def test_health_error_is_kind_and_class_and_never_the_text() -> None:
    exc = e.Transient(f"GET https://x/?api_key={KEY} timed out")
    assert e.health_error(exc) == "transient:Transient"
    assert KEY not in e.health_error(exc)


@pytest.mark.parametrize(
    "text",
    [
        f"GET https://api.stlouisfed.org/fred/series/observations?series_id=GDPC1&api_key={KEY}",
        f"api_key={KEY}&file_type=json",
        f"apikey: {KEY}",
        f"token='{KEY}'",
        f"Authorization: Bearer {KEY}",
        f"headers={{'Authorization': 'Bearer {KEY}'}}",
    ],
)
def test_scrub_removes_credentials_by_pattern(text: str) -> None:
    assert KEY not in e.scrub(text)


def test_scrub_removes_a_known_secret_even_with_no_pattern_around_it() -> None:
    text = "provider said: bad key hunter2-secret"
    assert "hunter2-secret" not in e.scrub(text, ["hunter2-secret"])


def test_scrub_keeps_ordinary_text_and_truncates() -> None:
    assert e.scrub("series GDPC1 not found") == "series GDPC1 not found"
    assert len(e.scrub("x" * 5000)) == e.ERROR_TEXT_MAX_CHARS


def test_run_error_names_kind_and_class() -> None:
    assert e.run_error(e.Permanent("bad series"), []).startswith("permanent:Permanent: bad series")


def test_nothing_a_library_says_is_ever_stored() -> None:
    # Kyle, 2026-11-18: a library exception that escaped a client's mapping may carry anything (a
    # request URL with a key, provider text). It is stored as <kind>:<Class> and nothing else.
    leaked = ValueError("GET https://query1.example/v8/chart/XLK?crumb=abc123 said: try later")
    assert e.run_error(leaked, []) == "unexpected:ValueError"
    assert e.health_error(leaked) == "unexpected:ValueError"


def test_our_own_fixed_text_follows_the_kind_and_class() -> None:
    # D4: the run names the dead symbols. That text is FlowDesk's, written by the client.
    err = e.EmptyResult("no rows today for: XLK, XLE")
    assert e.run_error(err, []) == "empty_result:EmptyResult: no rows today for: XLK, XLE"
    assert e.health_error(err) == "empty_result:EmptyResult"
