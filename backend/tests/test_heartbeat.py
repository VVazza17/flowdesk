"""The sender's decision table, over real local sockets rather than a mocked urlopen: timeouts,
refused connections and non-2xx handling are the behaviour under test here."""

import io
import json
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from app import heartbeat, settings
from app.logging import configure_logging

SECRET = "ping-secret-uuid"
TIMEOUT = 0.5


class FakeServer:
    """Answers every GET with a preset response, and records that it was asked."""

    def __init__(self) -> None:
        self.code = 200
        self.body = b"OK"
        self.delay = 0.0
        self.hits = 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                fake.hits += 1
                time.sleep(fake.delay)
                self.send_response(fake.code)
                self.send_header("Content-Length", str(len(fake.body)))
                self.end_headers()
                self.wfile.write(fake.body)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/{SECRET}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def ready() -> Iterator[FakeServer]:
    server = FakeServer()
    server.body = json.dumps({"status": "ok"}).encode()
    yield server
    server.close()


@pytest.fixture
def monitor() -> Iterator[FakeServer]:
    server = FakeServer()
    yield server
    server.close()


@pytest.fixture
def logs() -> io.StringIO:
    buf = io.StringIO()
    configure_logging(level="INFO", stream=buf)
    return buf


def _closed_port_url() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}/{SECRET}"


def _events(buf: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


def test_ok_pings(ready: FakeServer, monitor: FakeServer, logs: io.StringIO) -> None:
    assert heartbeat.run_cycle(ready.url, monitor.url, TIMEOUT) == "pinged"
    assert monitor.hits == 1
    assert _events(logs)[-1]["event"] == "pinged"


@pytest.mark.parametrize(
    ("code", "body", "reason"),
    [
        # The gate Kyle chose: `degraded` is a 200 and still must not ping (ADR-0008).
        (200, b'{"status":"degraded"}', "ready_status"),
        (503, b'{"status":"not_ready"}', "ready_http_status"),
        (500, b"boom", "ready_http_status"),
        (200, b"not json", "ready_unreachable"),
        (200, b'["ok"]', "ready_unreachable"),
        (200, b"{}", "ready_status"),
    ],
)
def test_anything_but_ok_does_not_ping(
    ready: FakeServer,
    monitor: FakeServer,
    logs: io.StringIO,
    code: int,
    body: bytes,
    reason: str,
) -> None:
    ready.code, ready.body = code, body
    assert heartbeat.run_cycle(ready.url, monitor.url, TIMEOUT) == "skipped"
    assert monitor.hits == 0
    assert _events(logs)[-1]["reason"] == reason


def test_api_down_does_not_ping(monitor: FakeServer, logs: io.StringIO) -> None:
    assert heartbeat.run_cycle(_closed_port_url(), monitor.url, TIMEOUT) == "skipped"
    assert monitor.hits == 0


def test_hung_api_times_out_and_does_not_ping(
    ready: FakeServer, monitor: FakeServer, logs: io.StringIO
) -> None:
    ready.delay = TIMEOUT * 4
    assert heartbeat.run_cycle(ready.url, monitor.url, TIMEOUT) == "skipped"
    assert monitor.hits == 0
    assert _events(logs)[-1]["reason"] == "ready_unreachable"


def test_monitor_unreachable_is_logged_and_does_not_raise(
    ready: FakeServer, logs: io.StringIO
) -> None:
    assert heartbeat.run_cycle(ready.url, _closed_port_url(), TIMEOUT) == "ping_failed"
    assert _events(logs)[-1]["event"] == "ping_failed"


def test_monitor_error_status_is_a_failed_ping(
    ready: FakeServer, monitor: FakeServer, logs: io.StringIO
) -> None:
    monitor.code = 500
    assert heartbeat.run_cycle(ready.url, monitor.url, TIMEOUT) == "ping_failed"
    assert _events(logs)[-1]["http_status"] == 500


def test_hung_monitor_does_not_raise(
    ready: FakeServer, monitor: FakeServer, logs: io.StringIO
) -> None:
    monitor.delay = TIMEOUT * 4
    assert heartbeat.run_cycle(ready.url, monitor.url, TIMEOUT) == "ping_failed"


def test_mistyped_uuid_is_not_mistaken_for_success(
    ready: FakeServer, monitor: FakeServer, logs: io.StringIO
) -> None:
    # What Healthchecks really answers for a UUID it doesn't know: 200, not 404.
    monitor.body = b"OK (not found)"
    assert heartbeat.run_cycle(ready.url, monitor.url, TIMEOUT) == "ping_rejected"
    event = _events(logs)[-1]
    assert event["event"] == "ping_rejected"
    assert event["level"] == "error"


def test_rate_limited_ping_is_flagged(
    ready: FakeServer, monitor: FakeServer, logs: io.StringIO
) -> None:
    monitor.body = b"OK (rate limited)"
    assert heartbeat.run_cycle(ready.url, monitor.url, TIMEOUT) == "ping_rate_limited"


def test_unset_url_never_checks_or_pings(ready: FakeServer, logs: io.StringIO) -> None:
    assert heartbeat.run_cycle(ready.url, None, TIMEOUT) == "disabled"
    assert ready.hits == 0
    assert _events(logs)[-1]["event"] == "heartbeat_disabled"


def test_ping_url_never_reaches_the_log(
    ready: FakeServer, monitor: FakeServer, logs: io.StringIO
) -> None:
    # Every path that handles the URL: success, error status, rejected, and unreachable.
    heartbeat.run_cycle(ready.url, monitor.url, TIMEOUT)
    monitor.code = 500
    heartbeat.run_cycle(ready.url, monitor.url, TIMEOUT)
    monitor.code, monitor.body = 200, b"OK (not found)"
    heartbeat.run_cycle(ready.url, monitor.url, TIMEOUT)
    heartbeat.run_cycle(ready.url, _closed_port_url(), TIMEOUT)
    assert len(_events(logs)) == 4
    assert SECRET not in logs.getvalue()


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_url_means_unarmed(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("HEARTBEAT_URL", value)
    assert settings.heartbeat_url() is None


def test_url_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEARTBEAT_URL", raising=False)
    assert settings.heartbeat_url() is None


def test_malformed_url_refuses_without_echoing_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEARTBEAT_URL", f"hc-ping.com/{SECRET}")
    with pytest.raises(ValueError) as exc:
        settings.heartbeat_url()
    assert SECRET not in str(exc.value)


def test_interval_defaults_to_the_monitor_period(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEARTBEAT_INTERVAL_SECONDS", raising=False)
    assert settings.heartbeat_interval_seconds() == 300


@pytest.mark.parametrize("value", ["0", "-5", "soon"])
def test_bad_interval_refuses_to_start(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("HEARTBEAT_INTERVAL_SECONDS", value)
    with pytest.raises(ValueError):
        settings.heartbeat_interval_seconds()
