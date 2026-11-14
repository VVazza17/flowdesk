"""A complete in-memory `HealthProbes`: everything up and healthy unless a test says otherwise.

The older test files each carry a partial copy of this. New tests use this one, so adding a probe
to the protocol means changing one fake, not five.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from app.deps import get_now, get_probes, get_started_at
from app.main import create_app
from app.services.health import FeedRow

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
LONG_AGO = NOW - timedelta(days=1)


class FakeProbes:
    def __init__(
        self,
        *,
        macro_age: list[dict[str, Any]] | None = None,
        state: dict[str, list[dict[str, Any]]] | None = None,
        provider_rows: list[FeedRow] | None = None,
    ) -> None:
        self._macro_age = macro_age or []
        self._state = state or {"ingestion": [], "backfills": [], "tasks": []}
        self._provider_rows = provider_rows or []

    async def ping_postgres(self) -> None:
        return None

    async def ping_redis(self) -> None:
        return None

    async def migration_revisions(self) -> tuple[str | None, str]:
        return ("0009", "0009")

    async def provider_rows(self) -> list[FeedRow]:
        return self._provider_rows

    async def last_task_success(self, task_name: str) -> datetime | None:
        # Both canaries completed ten seconds ago: "ok" keeps meaning "everything is fine".
        if task_name in ("ops.canary", "ops.canary_ingest"):
            return NOW - timedelta(seconds=10)
        return None

    async def running_task_fresh(self, task_names: Sequence[str], now: datetime) -> str | None:
        return None

    async def macro_age(self) -> list[dict[str, Any]]:
        return self._macro_age

    async def data_state(self) -> dict[str, list[dict[str, Any]]]:
        return self._state


def client(probes: FakeProbes, mode: str = "private") -> TestClient:
    app = create_app(mode)  # type: ignore[arg-type]
    app.dependency_overrides[get_probes] = lambda: probes
    app.dependency_overrides[get_now] = lambda: NOW
    app.dependency_overrides[get_started_at] = lambda: LONG_AGO
    return TestClient(app)
