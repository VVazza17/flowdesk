from fastapi import FastAPI

from app.api import health
from app.logging import configure_logging
from app.middleware import CorrelationIdMiddleware


def create_app() -> FastAPI:
    # An app factory keeps tests (and later the private/public profiles, slice 7) able to
    # build an app instance without import-time side effects.
    configure_logging()
    app = FastAPI(title="FlowDesk")
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(health.router)
    return app


app = create_app()
