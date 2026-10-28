from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any, TypeVar

import structlog
from fastapi import APIRouter, Depends, Response

from app import settings
from app.deps import get_now, get_probes
from app.services import health as svc

log = structlog.get_logger(__name__)
T = TypeVar("T")

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict[str, str]:
    # DECISION: liveness only means "the process answers". It deliberately touches no DB,
    # Redis or clock, so killing a dependency can never turn this into a 500. Dependency
    # state belongs to /health/ready, /health/data and /health/providers (slice 6).
    return {"status": "ok"}


Probes = Annotated[svc.HealthProbes, Depends(get_probes)]


@router.get("/ready")
async def ready(response: Response, probes: Probes) -> dict[str, Any]:
    # DECISION: bare JSON, not the §9 {data, as_of, stale} envelope. That envelope is specified
    # for /api/v1; these routes sit outside it, like /health/live. Logged as IDEA-025.
    # ok / degraded -> 200 (Redis is never the source of truth); not_ready -> 503. Never a 500:
    # every probe failure is converted to data inside svc.run_check.
    code, body = await svc.assemble_ready(probes)
    response.status_code = code
    return body


async def _from_postgres(
    response: Response, name: str, load: Callable[[], Awaitable[T]]
) -> tuple[T | None, dict[str, Any] | None]:
    """Run a Postgres-only read. On any failure (down, timeout, table missing because
    migrations have not run) set 503 and return an 'unavailable' body instead of raising."""
    try:
        return await load(), None
    except Exception as exc:  # noqa: BLE001 - must never surface as a 500
        err = type(exc).__name__
        log.warning("health_read_failed", endpoint=name, error=err)
        response.status_code = 503
        return None, {"status": "unavailable", "error": err}


@router.get("/providers")
async def providers(
    response: Response, probes: Probes, now: Annotated[datetime, Depends(get_now)]
) -> dict[str, Any]:
    # Reads Postgres only, so a Redis outage does not touch this endpoint. A stale feed is
    # reported in the body with HTTP 200: it is data about the feed, not an outage of the API.
    rows, failure = await _from_postgres(response, "providers", probes.provider_rows)
    if failure is not None or rows is None:
        return failure or {"status": "unavailable", "error": "NoResult"}
    return svc.assemble_providers(rows, now, settings.FEED_STALE_AFTER)


@router.get("/data")
async def data(response: Response, probes: Probes) -> dict[str, Any]:
    state, failure = await _from_postgres(response, "data", probes.data_state)
    if failure is not None or state is None:
        return failure or {"status": "unavailable", "error": "NoResult"}
    return svc.assemble_data(state)
