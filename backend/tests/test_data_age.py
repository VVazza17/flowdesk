"""M2 slice 5, step 5: data age on /health/data (assemble_data_age and the route).

DATA freshness, which /health/providers is not (providers_health moves on every successful poll,
changed data or not). No database: the probes are faked and the clock is passed in.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.services.health import assemble_data_age
from tests.fakeprobes import NOW, FakeProbes, client

DAY = timedelta(days=1)


def row(metric: str, available: datetime, effective: date) -> dict[str, Any]:
    return {"metric": metric, "newest_available_at": available, "newest_effective_at": effective}


def by(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["metric"]: s for s in body["series"]}


def test_a_series_is_fresh_up_to_and_including_its_threshold_and_stale_one_second_past() -> None:
    limit = timedelta(days=9)
    edge = row("DGS10", NOW - limit, date(2026, 11, 1))
    past = row("DGS2", NOW - limit - timedelta(seconds=1), date(2026, 11, 1))
    got = by(assemble_data_age([edge, past], NOW, {"DGS10": limit, "DGS2": limit}))
    assert got["DGS10"]["status"] == "fresh" and got["DGS10"]["stale"] is False
    assert got["DGS2"]["status"] == "stale" and got["DGS2"]["stale"] is True
    assert got["DGS10"]["age_seconds"] == int(limit.total_seconds())
    assert got["DGS10"]["stale_after_seconds"] == int(limit.total_seconds())


def test_a_value_published_today_has_age_zero_not_a_negative_age() -> None:
    """available_at is the END of the publication day in New York, so a value published today is
    'available' at tonight's midnight, which is in the future until then."""
    # Published today (2026-11-14, New York): available from 00:00 New York on the 15th = 05:00 UTC.
    tonight = datetime(2026, 11, 15, 5, 0, tzinfo=UTC)
    got = by(assemble_data_age([row("IORB", tonight, date(2026, 11, 16))], NOW, {"IORB": 9 * DAY}))
    assert got["IORB"]["age_seconds"] == 0 and got["IORB"]["status"] == "fresh"
    # IORB publishes values for dates that have not happened yet: the lag is negative, and honest.
    assert got["IORB"]["period_lag_days"] == -2  # published the 14th, for the 16th


def test_period_lag_is_the_publication_day_minus_the_period_and_never_judged() -> None:
    # CPI-shaped: January's figure published on 2026-02-12, i.e. available at the end of that day.
    published = datetime(2026, 2, 13, 5, 0, tzinfo=UTC)  # 00:00 New York on the 13th
    got = by(
        assemble_data_age(
            [row("CPIAUCSL", published, date(2026, 1, 1))], NOW, {"CPIAUCSL": 105 * DAY}
        )
    )
    assert got["CPIAUCSL"]["period_lag_days"] == 42
    assert got["CPIAUCSL"]["newest_effective_at"] == "2026-01-01"


def test_configured_but_nothing_stored_is_no_data_not_stale() -> None:
    body = assemble_data_age([], NOW, {"DGS10": 9 * DAY})
    entry = by(body)["DGS10"]
    assert entry["status"] == "no_data" and entry["stale"] is None
    assert "age_seconds" not in entry  # there is no age to report
    assert body["status"] == "no_data"


def test_stored_but_unconfigured_is_unjudged_not_fresh() -> None:
    # A series with rows and no threshold must not read as fresh: nothing judged it.
    body = assemble_data_age([row("GDPC1", NOW - 400 * DAY, date(2025, 1, 1))], NOW, {})
    entry = by(body)["GDPC1"]
    assert entry["status"] == "unjudged" and entry["stale"] is None
    assert entry["age_seconds"] == 400 * 86400 and entry["stale_after_seconds"] is None


def test_the_top_level_status_is_the_worst_series_in_a_fixed_precedence() -> None:
    fresh = row("A", NOW - DAY, date(2026, 11, 12))
    stale = row("B", NOW - 20 * DAY, date(2026, 10, 1))
    limits = {"A": 9 * DAY, "B": 9 * DAY, "C": 9 * DAY}
    assert assemble_data_age([fresh], NOW, {"A": 9 * DAY})["status"] == "fresh"
    assert (
        assemble_data_age([fresh, stale], NOW, limits)["status"] == "stale"
    )  # stale beats no_data
    assert assemble_data_age([fresh], NOW, limits)["status"] == "no_data"  # C has nothing stored
    assert (
        assemble_data_age([fresh, row("D", NOW, date(2026, 11, 13))], NOW, {"A": 9 * DAY})["status"]
        == "unjudged"
    )
    assert assemble_data_age([], NOW, {})["status"] == "no_series"


def test_series_are_listed_in_a_stable_alphabetical_order() -> None:
    rows = [row(m, NOW, date(2026, 11, 14)) for m in ("VIXCLS", "CPIAUCSL", "DGS10")]
    names = [s["metric"] for s in assemble_data_age(rows, NOW, {})["series"]]
    assert names == sorted(names)


# ---- through the route ----------------------------------------------------------------------


def test_health_data_carries_data_age_and_a_stale_series_never_degrades_ready() -> None:
    """The readiness rule at work: a source gone quiet is visible here and does not touch
    /health/ready, because the machinery (Postgres, Redis, the workers) is fine."""
    stale = row("DGS10", NOW - 30 * DAY, date(2026, 10, 14))
    probes = FakeProbes(macro_age=[stale])
    http = client(probes)
    data = http.get("/health/data").json()
    assert data["data_age"]["status"] in {"stale", "no_data"}  # the other 16 configured: no rows
    assert (
        next(s for s in data["data_age"]["series"] if s["metric"] == "DGS10")["status"] == "stale"
    )
    ready = http.get("/health/ready")
    assert ready.status_code == 200 and ready.json()["status"] == "ok"


def test_the_public_profile_serves_data_age_unchanged() -> None:
    # Series ids and ages are not secrets (the public redaction removes only error text), so the
    # public view must not silently drop the section.
    probes = FakeProbes(macro_age=[row("DGS10", NOW - DAY, date(2026, 11, 13))])
    private = client(probes, "private").get("/health/data").json()["data_age"]
    public = client(probes, "public").get("/health/data").json()["data_age"]
    assert public == private
