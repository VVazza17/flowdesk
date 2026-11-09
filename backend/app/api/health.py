from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Response

from app import settings
from app.deps import get_deployment_mode, get_now, get_probes, get_started_at
from app.services import health as svc
from app.tasks.names import BACKUP, BACKUP_COPY

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict[str, str]:
    # DECISION: liveness only means "the process answers". It deliberately touches no DB,
    # Redis or clock, so killing a dependency can never turn this into a 500. Dependency
    # state belongs to /health/ready, /health/data and /health/providers (slice 6).
    return {"status": "ok"}


Probes = Annotated[svc.HealthProbes, Depends(get_probes)]
Mode = Annotated[settings.DeploymentMode, Depends(get_deployment_mode)]


@router.get("/ready")
async def ready(
    response: Response,
    probes: Probes,
    now: Annotated[datetime, Depends(get_now)],
    started_at: Annotated[datetime, Depends(get_started_at)],
) -> dict[str, Any]:
    # DECISION: bare JSON, not the §9 {data, as_of, stale} envelope. That envelope is specified
    # for /api/v1; these routes sit outside it, like /health/live. Logged as IDEA-025.
    # ok / degraded -> 200 (Redis is never the source of truth); not_ready -> 503. Never a 500:
    # every probe failure is converted to data inside svc.run_check.
    code, body = await svc.assemble_ready(probes, now, started_at, settings.worker_stale_after())
    response.status_code = code
    return body


async def _from_postgres[T](
    response: Response, name: str, load: Callable[[], Awaitable[T]]
) -> tuple[T | None, dict[str, Any] | None]:
    """Run a Postgres-only read. On any failure (down, timeout, table missing because
    migrations have not run) set 503 and return an 'unavailable' body instead of raising."""
    try:
        return await load(), None
    except Exception as exc:  # must never surface as a 500
        err = type(exc).__name__
        log.warning("health_read_failed", endpoint=name, error=err)
        response.status_code = 503
        return None, {"status": "unavailable", "error": err}


@router.get("/providers")
async def providers(
    response: Response,
    probes: Probes,
    now: Annotated[datetime, Depends(get_now)],
    mode: Mode,
) -> dict[str, Any]:
    # Reads Postgres only, so a Redis outage does not touch this endpoint. A stale feed is
    # reported in the body with HTTP 200: it is data about the feed, not an outage of the API.
    rows, failure = await _from_postgres(response, "providers", probes.provider_rows)
    if failure is not None or rows is None:
        return failure or {"status": "unavailable", "error": "NoResult"}
    body = svc.assemble_providers(rows, now, settings.FEED_STALE_AFTER)
    # `!= "private"`, not `== "public"`: anything but the known-private mode gets the redacted body.
    return body if mode == "private" else svc.redact_providers(body)


@router.get("/data")
async def data(
    response: Response,
    probes: Probes,
    now: Annotated[datetime, Depends(get_now)],
    mode: Mode,
) -> dict[str, Any]:
    async def load() -> tuple[dict[str, list[dict[str, Any]]], dict[str, datetime | None]]:
        state = await probes.data_state()
        # The newest *success* of each backup step, which the latest-run rows cannot give.
        successes = {name: await probes.last_task_success(name) for name in (BACKUP, BACKUP_COPY)}
        return state, successes

    loaded, failure = await _from_postgres(response, "data", load)
    if failure is not None or loaded is None:
        return failure or {"status": "unavailable", "error": "NoResult"}
    state, successes = loaded
    body = svc.assemble_data(state, now, successes, settings.backup_stale_after())
    return body if mode == "private" else svc.redact_data(body)
