"""External dead-man heartbeat sender (§16 M1, ADR-0008). Run as `python -m app.heartbeat`.

Every interval: ask the API whether it is ready, and ping the external monitor only if the answer
is exactly `ok`. The monitor alerts when the pings stop. So this process has one job, which is to go
quiet when something is wrong. Everything that can fail here ends in "no ping", never in a crash.

The ping URL is a credential and never reaches a log line. Exceptions are logged by class name only,
the same convention as docs/HEALTH.md.

# DECISION: stdlib urllib, not an HTTP client dependency. Two GETs don't justify a new package in
# the runtime lock, and IDEA-027 notes that an HTTP client is likely to bring certifi back.
"""

import json
import signal
import time
import urllib.error
import urllib.request
from types import FrameType
from typing import Any

import structlog

from app import settings
from app.logging import configure_logging

# A compose network alias carried by both `api` and `api-public`; exactly one of them runs.
READY_URL = "http://api-upstream:8000/health/ready"
TIMEOUT_SECONDS = 10.0
MAX_BODY_BYTES = 64 * 1024

log = structlog.get_logger("heartbeat")


def _get(url: str, timeout: float) -> tuple[int, bytes]:
    """GET a URL. A non-2xx answer comes back as a status, not an exception."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return int(response.status), bytes(response.read(MAX_BODY_BYTES))
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, b""


def _skip_reason(ready_url: str, timeout: float) -> dict[str, Any] | None:
    """Why this cycle must not ping, or None if /health/ready is exactly ok."""
    try:
        code, body = _get(ready_url, timeout)
        if code != 200:
            return {"reason": "ready_http_status", "http_status": code}
        status = json.loads(body).get("status")
    # DECISION: broad on purpose. Whatever goes wrong while checking, the answer is "don't ping".
    except Exception as exc:
        return {"reason": "ready_unreachable", "error": type(exc).__name__}
    # DECISION: `ok` only, so `degraded` stops the pings too (Kyle, ADR-0008). From M2 a dead Redis
    # also stops Beat and the internal heartbeat, so nothing else would report it.
    if status != "ok":
        return {"reason": "ready_status", "ready_status": str(status)}
    return None


def _ping(ping_url: str, timeout: float) -> str:
    try:
        code, body = _get(ping_url, timeout)
    except Exception as exc:
        log.error("ping_failed", error=type(exc).__name__)
        return "ping_failed"
    if not 200 <= code < 300:
        log.error("ping_failed", http_status=code)
        return "ping_failed"
    # Healthchecks answers a mistyped UUID with 200 "OK (not found)", and drops pings over its rate
    # limit with 200 "OK (rate limited)". Status alone would call both a success. These substrings
    # are provider-specific and simply never match on another provider.
    text = body.decode("utf-8", "replace").lower()
    if "not found" in text:
        log.error("ping_rejected", detail="monitor does not know this URL; check HEARTBEAT_URL")
        return "ping_rejected"
    if "rate limited" in text:
        log.warning("ping_rate_limited")
        return "ping_rate_limited"
    log.info("pinged")
    return "pinged"


def run_cycle(ready_url: str, ping_url: str | None, timeout: float = TIMEOUT_SECONDS) -> str:
    """One check and at most one ping. Returns the outcome. Never raises on a network failure."""
    if ping_url is None:
        # Not an error: development machines stay unarmed on purpose (docs/HEARTBEAT.md). Still
        # logged every cycle, so a deployed instance that lost its URL says so.
        log.warning("heartbeat_disabled", detail="HEARTBEAT_URL is blank; nothing is watching")
        return "disabled"
    reason = _skip_reason(ready_url, timeout)
    if reason is not None:
        log.warning("skipped", **reason)
        return "skipped"
    return _ping(ping_url, timeout)


def _stop(_signum: int, _frame: FrameType | None) -> None:
    raise SystemExit(0)


def main() -> None:
    configure_logging()
    # PID 1 ignores SIGTERM unless a handler exists, so `docker compose stop` would otherwise wait
    # out its full 10 seconds on this container every time.
    signal.signal(signal.SIGTERM, _stop)
    ping_url = settings.heartbeat_url()
    interval = settings.heartbeat_interval_seconds()
    log.info("heartbeat_started", interval_seconds=interval, armed=ping_url is not None)
    while True:
        run_cycle(READY_URL, ping_url)
        time.sleep(interval)


if __name__ == "__main__":
    main()
