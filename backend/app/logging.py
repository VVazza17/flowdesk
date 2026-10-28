import logging
import os
import sys
from typing import IO

import structlog


def configure_logging(level: str | None = None, stream: IO[str] | None = None) -> None:
    """Route structlog and stdlib logging (uvicorn, libraries) to one JSON-per-line stream.

    structlog builds the event dict; ProcessorFormatter lets stdlib records pass through the
    same processors, so uvicorn's own lines come out as JSON too. Idempotent: calling it again
    replaces the handler, which is what lets tests point it at a captured stream.
    """
    # DECISION: JSON only, no console renderer. One format means the dev log and the
    # production log are the same thing, and the smoke test greps what operators will see.
    level_name = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    # Resolved at call time, not import time, so pytest's capsys can capture it.
    stream = stream if stream is not None else sys.stdout

    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level_name)
    # Uvicorn installs its own handlers on these; drop them so records propagate to the root
    # JSON handler instead of printing a second, plain-text copy.
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    # DECISION: uvicorn's access log stays off even if someone drops --no-access-log.
    # CorrelationIdMiddleware owns the one per-request line (it carries status and duration);
    # a second uvicorn line per request would break "one request, one line".
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False
