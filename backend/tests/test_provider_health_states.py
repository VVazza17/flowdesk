"""M2 slice 4: classify_feed / assemble_providers with first_seen_at (IDEA-026). No database."""

from datetime import UTC, datetime, timedelta

from app.services.health import FeedRow, assemble_providers, classify_feed

T0 = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
THRESHOLD = timedelta(minutes=3)


def _row(
    *,
    last_success_at: datetime | None = None,
    last_error_at: datetime | None = None,
    first_seen_at: datetime | None = T0,
) -> FeedRow:
    return FeedRow(
        "fake",
        "ticks",
        last_success_at,
        last_error_at,
        "transient:Transient" if last_error_at else None,
        first_seen_at,
    )


def test_never_succeeded_inside_its_window_is_not_broken() -> None:
    out = classify_feed(_row(), T0 + timedelta(minutes=2), THRESHOLD)
    assert (out["status"], out["stale"]) == ("never_succeeded", False)


def test_never_succeeded_past_its_own_threshold_is_stale() -> None:
    out = classify_feed(_row(), T0 + timedelta(minutes=4), THRESHOLD)
    assert (out["status"], out["stale"], out["never_succeeded"]) == ("stale", True, True)


def test_a_feed_with_no_threshold_cannot_escalate() -> None:
    out = classify_feed(_row(), T0 + timedelta(days=30), None)
    assert (out["status"], out["stale"]) == ("never_succeeded", False)


def test_without_first_seen_there_is_no_age_and_no_escalation() -> None:
    out = classify_feed(_row(first_seen_at=None), T0 + timedelta(days=30), THRESHOLD)
    assert out["status"] == "never_succeeded"


def test_a_killed_provider_walks_fresh_then_stale_then_recovers() -> None:
    # The documented sequence (docs/HEALTH.md, "Killing a provider").
    last_ok = T0
    failing = {"last_success_at": last_ok, "last_error_at": last_ok + timedelta(minutes=1)}
    soon = classify_feed(_row(**failing), last_ok + timedelta(minutes=2), THRESHOLD)
    assert (soon["status"], soon["last_attempt_failed"]) == ("fresh", True)
    late = classify_feed(_row(**failing), last_ok + timedelta(minutes=4), THRESHOLD)
    assert (late["status"], late["stale"]) == ("stale", True)
    healed_row = _row(
        last_success_at=last_ok + timedelta(minutes=5),
        last_error_at=last_ok + timedelta(minutes=1),
    )
    healed = classify_feed(healed_row, last_ok + timedelta(minutes=6), THRESHOLD)
    assert (healed["status"], healed["last_attempt_failed"]) == ("fresh", False)


def test_a_stale_never_succeeded_feed_makes_the_top_level_stale() -> None:
    body = assemble_providers([_row()], T0 + timedelta(minutes=4), {("fake", "ticks"): THRESHOLD})
    assert body["status"] == "stale"
