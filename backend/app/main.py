from fastapi import FastAPI

from app.api import health


def create_app() -> FastAPI:
    # An app factory keeps tests (and later the private/public profiles, slice 7) able to
    # build an app instance without import-time side effects.
    app = FastAPI(title="FlowDesk")
    app.include_router(health.router)
    return app


app = create_app()
