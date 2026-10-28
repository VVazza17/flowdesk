import logging
import re
import time
import uuid

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADER = "x-request-id"

# Why validate at all: JSONRenderer already escapes newlines, so a hostile header cannot forge
# a log line. The pattern bounds the length and keeps ids greppable (no spaces or quotes).
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_log = structlog.get_logger("app.request")


class CorrelationIdMiddleware:
    """Bind a request id into structlog's contextvars and emit one access line per request.

    Pure ASGI, not BaseHTTPMiddleware: the latter runs the app in a separate task, which
    breaks contextvar propagation and buffers streaming responses.

    The contextvar binding is the extension point for propagating the id into Celery tasks
    later (see docs/BUILD-LOG.md). Not built yet.

    DECISION: a 500 from an unhandled exception carries no X-Request-ID response header.
    Starlette's outer ServerErrorMiddleware builds that response outside this middleware. The
    log line for it still has the id. Fixing the header needs an exception handler; deferred.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = Headers(scope=scope).get(HEADER)
        request_id = inbound if inbound and _SAFE_ID.match(inbound) else uuid.uuid4().hex

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        status = 500
        start = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[HEADER] = request_id
            await send(message)

        path = scope["path"]
        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            _log.error(
                "request",
                method=scope["method"],
                path=path,
                status=500,
                duration_ms=_ms(start),
                exc_info=True,
            )
            raise
        else:
            # DECISION: successful health probes log at DEBUG. The Docker healthcheck fires
            # every 10s and would drown `docker compose logs api`. A failing probe (>= 400)
            # logs at the normal level, so a 500 on /health/* is never hidden.
            quiet = path.startswith("/health/") and status < 400
            _log.log(
                logging.DEBUG if quiet else logging.INFO,
                "request",
                method=scope["method"],
                path=path,
                status=status,
                duration_ms=_ms(start),
            )
        finally:
            structlog.contextvars.clear_contextvars()


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)
