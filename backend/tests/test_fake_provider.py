"""M2 slice 4: the fake provider's scripting and its opt-in wiring. No database."""

import asyncio
from datetime import timedelta

import pytest

from app import settings
from app.celery_app import create_celery
from app.ingest.errors import EmptyResult, Permanent, RateLimited, SchemaMismatch, Transient
from app.ingest.fake import FakeProvider, InMemoryFakeStore


def test_the_payload_is_identical_on_every_call() -> None:
    fake = FakeProvider(["ok"])
    first, second = asyncio.run(fake.fetch("f")), asyncio.run(fake.fetch("f"))
    assert first == second and first.digest == second.digest


@pytest.mark.parametrize(
    ("mode", "error"),
    [
        ("transient", Transient),
        ("down", Transient),
        ("rate_limited", RateLimited),
        ("permanent", Permanent),
        ("empty", EmptyResult),
        ("schema", SchemaMismatch),
    ],
)
def test_each_failure_mode_raises_its_class(mode: str, error: type[Exception]) -> None:
    with pytest.raises(error):
        asyncio.run(FakeProvider([mode]).fetch("f"))


def test_a_script_is_consumed_in_order_and_the_last_entry_repeats() -> None:
    fake = FakeProvider(["transient", "ok"])

    async def go() -> None:
        with pytest.raises(Transient):
            await fake.fetch("f")
        await fake.fetch("f")
        await fake.fetch("f")

    asyncio.run(go())
    assert fake.calls == 3


def test_the_store_counts_created_or_changed_rows_only() -> None:
    store = InMemoryFakeStore()
    rows = (("a", "1"), ("b", "2"))

    async def go() -> list[int]:
        return [
            await store.apply(rows),
            await store.apply(rows),
            await store.apply((("a", "9"), ("b", "2"))),
        ]

    assert asyncio.run(go()) == [2, 0, 1]


def test_the_fake_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.delenv("FAKE_PROVIDER_ENABLED", raising=False)
    assert settings.fake_provider_enabled() is False
    assert settings.FAKE_FEED not in settings.feed_stale_after()
    assert "fake_ingest" not in create_celery().conf.beat_schedule


def test_enabled_it_schedules_one_task_and_declares_its_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("FAKE_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("FAKE_PROVIDER_INTERVAL_SECONDS", "10")
    app = create_celery()
    assert app.conf.beat_schedule["fake_ingest"]["schedule"] == 10.0
    assert settings.feed_stale_after()[settings.FAKE_FEED] == timedelta(seconds=30)


def test_celery_retries_are_off_for_ingest_tasks() -> None:
    # Beat is the outer retry (IDEA-042): a Celery retry would write a second ingestion_runs row
    # for one logical run.
    from app.tasks.fake_ingest import fake_ingest

    assert fake_ingest.max_retries == 0


def test_the_task_writes_nothing_when_the_fake_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    # No DATABASE_URL or REDIS_URL: if it got past the guard it would raise NotConfigured.
    from app.tasks.fake_ingest import run_fake_ingest

    monkeypatch.delenv("FAKE_PROVIDER_ENABLED", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    asyncio.run(run_fake_ingest())  # returns without raising and without touching anything


def test_a_bad_fake_mode_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_PROVIDER_MODE", "dwon")
    with pytest.raises(ValueError, match="FAKE_PROVIDER_MODE"):
        settings.fake_provider_mode()
