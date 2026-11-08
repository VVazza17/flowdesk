from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI

from app import settings
from app.api import health
from app.logging import configure_logging
from app.middleware import CorrelationIdMiddleware
from app.route_policy import public_route_violations


def create_app(mode: settings.DeploymentMode | None = None) -> FastAPI:
    # An app factory keeps tests and the private/public profiles able to build an app instance
    # without import-time side effects. `mode` is fixed for the app's lifetime (ADR-0007: a
    # deploy-time choice, not a runtime toggle); None means "read DEPLOYMENT_MODE".
    configure_logging()
    mode = mode or settings.deployment_mode()
    # IDEA-019 rider names /docs and /openapi.json. /redoc serves the same schema, so it goes too.
    docs_off: dict[str, Any] = (
        {"openapi_url": None, "docs_url": None, "redoc_url": None} if mode == "public" else {}
    )
    app = FastAPI(title="FlowDesk", **docs_off)
    app.state.deployment_mode = mode
    app.state.started_at = datetime.now(UTC)
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(health.router)
    if mode == "public" and (violations := public_route_violations(app.routes)):
        # DECISION: refuse to start, not just fail a test, so the invariant also holds on any
        # deployed public app and not only where CI ran.
        raise RuntimeError(f"public profile route invariant failed: {violations}")
    return app


app = create_app()
