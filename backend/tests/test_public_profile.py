"""Slice 7: the public-profile invariant and riders (IDEA-019, docs/SPEC-DEFECTS.md).

No infrastructure needed: probes are faked via dependency_overrides.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI, WebSocket
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import BaseRoute, Host, Match, Route, WebSocketRoute
from starlette.types import Receive, Scope, Send

from app import main, settings
from app.deps import get_now, get_probes
from app.main import create_app
from app.route_policy import public_route_violations
from app.services.health import FeedRow

HEALTH_PATHS = {"/health/live", "/health/ready", "/health/providers", "/health/data"}
SECRET = "SENTINEL-raw-provider-text"
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


async def _handler() -> dict[str, str]:
    return {}


async def _starlette_handler(_: Request) -> Response:
    return PlainTextResponse("served")


def bare_app() -> FastAPI:
    return FastAPI(openapi_url=None, docs_url=None, redoc_url=None)


# --- the walk really sees the app ----------------------------------------------------------


def test_walk_sees_exactly_the_four_health_paths_on_the_public_app() -> None:
    # Guards against a vacuous pass: a walker that finds nothing also finds no violations.
    app = create_app("public")
    assert {ctx.path for ctx in iter_route_contexts(app.routes)} == HEALTH_PATHS
    assert public_route_violations(app.routes) == []


def test_naive_walk_of_app_routes_finds_none_of_them() -> None:
    # Pins why route_policy uses iter_route_contexts (FastAPI 0.162.0). If this starts failing
    # after an upgrade, included routers became flat again and the note there needs revisiting.
    app = create_app("public")
    assert not HEALTH_PATHS & {getattr(r, "path", None) for r in app.routes}


# --- passes --------------------------------------------------------------------------------


@pytest.mark.parametrize("methods", [["GET"], ["HEAD"], ["OPTIONS"], ["GET", "HEAD", "OPTIONS"]])
def test_read_only_methods_pass(methods: list[str]) -> None:
    app = bare_app()
    app.add_api_route("/x", _handler, methods=methods)
    assert public_route_violations(app.routes) == []


# --- fails closed --------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_write_method_fails(method: str) -> None:
    app = bare_app()
    app.add_api_route("/x", _handler, methods=[method])
    assert public_route_violations(app.routes) == [f"/x: not read-only ({method})"]


def test_mixed_get_and_post_fails() -> None:
    app = bare_app()
    app.add_api_route("/x", _handler, methods=["GET", "POST"])
    assert public_route_violations(app.routes) == ["/x: not read-only (POST)"]


def test_post_inside_nested_included_router_fails() -> None:
    inner = APIRouter(prefix="/inner")
    inner.add_api_route("/write", _handler, methods=["POST"])
    middle = APIRouter(prefix="/middle")
    middle.include_router(inner)
    app = bare_app()
    app.include_router(middle, prefix="/top")
    assert public_route_violations(app.routes) == ["/top/middle/inner/write: not read-only (POST)"]


def test_plain_starlette_post_inside_included_router_fails() -> None:
    router = APIRouter(prefix="/r")
    router.routes.append(Route("/s", _starlette_handler, methods=["POST"]))
    app = bare_app()
    app.include_router(router)
    assert len(public_route_violations(app.routes)) == 1


def test_websocket_fails() -> None:
    app = bare_app()

    @app.websocket("/ws")
    async def ws(_: WebSocket) -> None: ...

    assert public_route_violations(app.routes) == ["/ws: cannot classify APIWebSocketRoute"]


def test_websocket_inside_included_router_fails() -> None:
    router = APIRouter()

    @router.websocket("/ws")
    async def ws(_: WebSocket) -> None: ...

    app = bare_app()
    app.include_router(router)
    assert public_route_violations(app.routes) == ["/ws: cannot classify APIWebSocketRoute"]


def test_starlette_websocket_route_fails() -> None:
    assert public_route_violations([WebSocketRoute("/ws", _handler)]) == [
        "/ws: cannot classify WebSocketRoute"
    ]


def test_mount_fails_even_with_only_get_routes_inside() -> None:
    # A sub-app's routes are invisible from here, so "probably fine" is not an answer.
    app = bare_app()
    app.mount("/sub", FastAPI())
    assert public_route_violations(app.routes) == ["/sub: cannot classify Mount"]


def test_host_route_fails() -> None:
    violations = public_route_violations([Host("x.example", app=FastAPI())])
    assert len(violations) == 1 and "cannot classify Host" in violations[0]


class UnknownRoute(BaseRoute):
    """A route type nothing in the policy knows about, which really does answer requests."""

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        hit = scope["type"] == "http" and scope["path"] == "/unknown"
        return (Match.FULL, {}) if hit else (Match.NONE, {})

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        await PlainTextResponse("served")(scope, receive, send)


def test_unknown_route_type_fails() -> None:
    violations = public_route_violations([UnknownRoute()])
    assert len(violations) == 1 and "cannot classify UnknownRoute" in violations[0]


def test_unknown_route_type_inside_included_router_is_not_served() -> None:
    # FastAPI 0.162.0 drops unknown types while flattening an included router, so the walk cannot
    # see this one. That is only safe because dispatch drops it too; hence the live request. If
    # either half changes after an upgrade, re-check that the walk still matches what is served.
    router = APIRouter()
    router.routes.append(UnknownRoute())
    router.add_api_route("/kept", _handler, methods=["GET"])
    app = bare_app()
    app.include_router(router)
    assert [ctx.path for ctx in iter_route_contexts(app.routes)] == ["/kept"]
    assert TestClient(app).get("/unknown").status_code == 404


def test_route_with_no_declared_methods_fails() -> None:
    # Starlette defaults a function endpoint to GET/HEAD, but a class endpoint or ASGI app gets
    # methods=None and answers everything.
    route = Route("/x", _starlette_handler)
    route.methods = None
    assert public_route_violations([route]) == ["/x: no declared methods, cannot classify"]


def test_empty_walk_fails() -> None:
    assert public_route_violations(bare_app().routes) == [
        "walk found no routes, so nothing was checked"
    ]


# --- the app enforces it -------------------------------------------------------------------


@pytest.fixture
def router_with_write(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = APIRouter()
    bad.add_api_route("/write", _handler, methods=["POST"])
    monkeypatch.setattr(main.health, "router", bad)


@pytest.mark.usefixtures("router_with_write")
def test_public_app_refuses_to_start_with_a_write_route() -> None:
    with pytest.raises(RuntimeError, match="/write"):
        create_app("public")


@pytest.mark.usefixtures("router_with_write")
def test_private_app_may_have_a_write_route() -> None:
    assert create_app("private").state.deployment_mode == "private"  # §14: mutations allowed


def test_mode_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEPLOYMENT_MODE", "public")
    assert create_app().state.deployment_mode == "public"


def test_mode_defaults_to_private(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEPLOYMENT_MODE", raising=False)
    assert settings.deployment_mode() == "private"


@pytest.mark.parametrize("bad", ["", "Public", "PUBLIC", "public ", "readonly"])
def test_unrecognised_mode_refuses_to_start(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("DEPLOYMENT_MODE", bad)
    with pytest.raises(ValueError, match="DEPLOYMENT_MODE"):
        create_app()


# --- riders --------------------------------------------------------------------------------


class SecretProbes:
    async def provider_rows(self) -> list[FeedRow]:
        return [FeedRow("p", "f", None, NOW, SECRET)]

    async def data_state(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "ingestion": [{"provider": "p", "feed": "f", "status": "failed", "error": SECRET}],
            "backfills": [],
            "tasks": [{"task_name": "t", "status": "failed", "error_class": "TimeoutError"}],
        }


def client(mode: settings.DeploymentMode) -> TestClient:
    app = create_app(mode)
    app.dependency_overrides[get_probes] = lambda: SecretProbes()
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app)


def test_public_providers_removes_last_error_but_keeps_the_failure_visible() -> None:
    r = client("public").get("/health/providers")
    assert r.status_code == 200
    assert SECRET not in r.text
    feed = r.json()["feeds"][0]
    assert "last_error" not in feed  # removed, not null: null would read as "no error"
    assert feed["last_attempt_failed"] is True
    assert feed["last_error_at"] == NOW.isoformat()


def test_private_providers_keeps_last_error() -> None:
    assert client("private").get("/health/providers").json()["feeds"][0]["last_error"] == SECRET


def test_public_data_removes_ingestion_error_and_keeps_the_rest() -> None:
    r = client("public").get("/health/data")
    assert r.status_code == 200
    assert SECRET not in r.text
    body = r.json()
    assert "error" not in body["ingestion"][0]
    assert body["ingestion"][0]["status"] == "failed"
    assert body["tasks"][0]["error_class"] == "TimeoutError"


def test_private_data_keeps_ingestion_error() -> None:
    assert client("private").get("/health/data").json()["ingestion"][0]["error"] == SECRET


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_public_docs_are_gone(path: str) -> None:
    assert client("public").get(path).status_code == 404


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_private_docs_stay(path: str) -> None:
    assert client("private").get(path).status_code == 200


@pytest.mark.parametrize("path", sorted(HEALTH_PATHS))
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_public_health_routes_reject_writes(path: str, method: str) -> None:
    assert getattr(client("public"), method)(path).status_code == 405
