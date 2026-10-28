import io
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.logging import configure_logging
from app.main import create_app


def _client(level: str = "DEBUG") -> tuple[TestClient, io.StringIO]:
    app = create_app()

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("kaboom")

    @app.get("/health/broken")
    def broken() -> None:
        raise RuntimeError("probe down")

    # create_app() logs to stdout; point it at a buffer after the fact.
    buf = io.StringIO()
    configure_logging(level=level, stream=buf)
    # The test client (httpx) logs its own "HTTP Request:" line, and asyncio logs "Using
    # proactor" at DEBUG, both to the root handler. Harness noise, not app output.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    return TestClient(app, raise_server_exceptions=False), buf


def _lines(buf: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


def test_one_request_one_json_line_carrying_the_id() -> None:
    client, buf = _client()
    resp = client.get("/health/live")
    lines = _lines(buf)
    assert len(lines) == 1
    line = lines[0]
    assert line["event"] == "request"
    assert line["method"] == "GET"
    assert line["path"] == "/health/live"
    assert line["status"] == 200
    assert isinstance(line["duration_ms"], float)
    assert line["request_id"] == resp.headers["x-request-id"]
    assert line["level"] == "debug"
    assert line["timestamp"].endswith("Z")


def test_valid_inbound_id_is_honoured_and_echoed() -> None:
    client, buf = _client()
    resp = client.get("/health/live", headers={"X-Request-ID": "abc-123_X.y"})
    assert resp.headers["x-request-id"] == "abc-123_X.y"
    assert _lines(buf)[0]["request_id"] == "abc-123_X.y"


@pytest.mark.parametrize("bad", ["has space", 'quo"te', "x" * 65, "semi;colon"])
def test_unsafe_or_overlong_inbound_id_is_replaced(bad: str) -> None:
    client, buf = _client()
    resp = client.get("/health/live", headers={"X-Request-ID": bad})
    rid = resp.headers["x-request-id"]
    assert rid != bad
    assert len(rid) == 32 and rid.isalnum()
    assert _lines(buf)[0]["request_id"] == rid


def test_ids_do_not_leak_between_requests() -> None:
    client, buf = _client()
    client.get("/health/live", headers={"X-Request-ID": "first"})
    client.get("/health/live")
    first, second = _lines(buf)
    assert first["request_id"] == "first"
    assert second["request_id"] != "first"


def test_successful_health_probe_is_silent_at_info() -> None:
    client, buf = _client(level="INFO")
    client.get("/health/live")
    assert _lines(buf) == []


def test_non_health_request_logs_at_info() -> None:
    client, buf = _client(level="INFO")
    resp = client.get("/nope")
    assert resp.status_code == 404
    (line,) = _lines(buf)
    assert line["level"] == "info"
    assert line["status"] == 404
    assert line["request_id"] == resp.headers["x-request-id"]


def test_failing_health_probe_is_not_hidden_at_info() -> None:
    client, buf = _client(level="INFO")
    resp = client.get("/health/broken")
    assert resp.status_code == 500
    (line,) = _lines(buf)
    assert line["level"] == "error"
    assert line["status"] == 500
    assert line["path"] == "/health/broken"


def test_unhandled_exception_logs_once_with_traceback_and_id() -> None:
    client, buf = _client(level="INFO")
    resp = client.get("/boom", headers={"X-Request-ID": "boom-1"})
    assert resp.status_code == 500
    (line,) = _lines(buf)
    assert line["status"] == 500
    assert line["request_id"] == "boom-1"
    assert "RuntimeError: kaboom" in line["exception"]
    # DECISION recorded in the middleware: the outer 500 response has no id header.
    assert "x-request-id" not in resp.headers


def test_stdlib_loggers_come_out_as_json_with_the_request_id() -> None:
    client, buf = _client()
    app = client.app

    @app.get("/stdlib")  # type: ignore[attr-defined]
    def stdlib() -> None:
        logging.getLogger("uvicorn.error").warning("from stdlib")

    client.get("/stdlib", headers={"X-Request-ID": "std-1"})
    lines = _lines(buf)
    msg = next(entry for entry in lines if entry["event"] == "from stdlib")
    assert msg["request_id"] == "std-1"
