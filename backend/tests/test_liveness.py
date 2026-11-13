"""M2 slice 5, step 3, part C: what "alive" means, stated once (app.tasks.liveness). No database."""

from datetime import UTC, datetime, timedelta

from app.tasks.liveness import (
    HEARTBEAT_FRESH_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    SWEEP_INTERVAL_SECONDS,
    is_fresh,
    stale_cutoff,
)

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def test_a_beat_at_the_boundary_is_fresh_and_one_microsecond_past_it_is_not() -> None:
    edge = NOW - timedelta(seconds=HEARTBEAT_FRESH_SECONDS)
    assert is_fresh(edge, NOW) is True
    assert is_fresh(edge - timedelta(microseconds=1), NOW) is False


def test_a_null_heartbeat_is_never_fresh() -> None:
    assert is_fresh(None, NOW) is False


def test_a_heartbeat_from_now_or_the_future_is_fresh() -> None:
    assert is_fresh(NOW, NOW) and is_fresh(NOW + timedelta(seconds=5), NOW)


def test_the_cutoff_is_the_one_definition_the_sql_uses() -> None:
    # The sweep and the health probe compare `heartbeat_at >= stale_cutoff(now)`; is_fresh must
    # agree with that for every value, or a task could be alive to one and dead to the other.
    cutoff = stale_cutoff(NOW)
    for delta in (-1, 0, 1):
        beat = cutoff + timedelta(seconds=delta)
        assert is_fresh(beat, NOW) is (beat >= cutoff)


def test_three_beats_can_be_lost_before_a_live_task_looks_dead() -> None:
    # Pinned so the two numbers cannot drift into a state where a healthy task is swept.
    assert HEARTBEAT_FRESH_SECONDS >= 3 * HEARTBEAT_INTERVAL_SECONDS


def test_the_sweep_looks_at_least_as_often_as_a_row_goes_stale() -> None:
    assert SWEEP_INTERVAL_SECONDS <= HEARTBEAT_FRESH_SECONDS
