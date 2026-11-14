"""Run INSIDE the worker container (stdin) by smoke-m2-slice5-fred-failures.sh.

One FRED call per failure status, through the real mapping path (FredClient -> run_feed ->
ingestion_runs / providers_health), in the real worker image against the real Redis and Postgres.
The "FRED" is a local stub that answers a fixed status and ECHOES THE REQUEST URL, key included, in
its body (a server may do that). The API key is a canary, never the real one.

Prints one JSON line per status: what the exception said, which kind it mapped to, how many requests
the stub saw. The shell wrapper asserts on that and on what the database and the API serve.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx2
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import create_async_engine

from app import settings
from app.ingest.budget import RedisSpacingLimiter
from app.ingest.errors import kind_of
from app.ingest.fred import FredClient
from app.ingest.runner import FeedContext, FeedResult, RetryPolicy, run_feed
from app.tasks.runs import run_recorded

KEY = "zzcanary0123456789abcdefghijkl12"
STATUSES = (500, 429, 423)
state = {"status": 500, "hits": 0}


class Stub(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        state["hits"] += 1
        body = json.dumps(
            {"error_code": state["status"], "error_message": f"bad request {self.path}"}
        ).encode()
        self.send_response(state["status"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


async def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/fred"
    url, redis_url = settings.database_url(), settings.redis_url()
    assert url and redis_url
    engine = create_async_engine(url)
    redis = Redis.from_url(redis_url, decode_responses=True)
    limiter = RedisSpacingLimiter(redis, settings.RATE_BUDGETS)
    try:
        # POSITIVE CONTROL: the stub really does echo the key. Without it, "the key appears nowhere"
        # could be true only because nothing ever carried it.
        state["status"] = 500
        async with httpx2.AsyncClient(base_url=base) as probe:
            echoed = (await probe.get("/x", params={"api_key": KEY})).text
        print(json.dumps({"control_echoes_key": KEY in echoed}), flush=True)
        for status in STATUSES:
            state.update(status=status, hits=0)
            seen: dict[str, str] = {}

            async def fred_body(ctx: FeedContext) -> FeedResult:
                async with FredClient(KEY, user_agent="smoke", base_url=base) as fred:
                    try:
                        await fred.latest("SMOKE")
                    except Exception as exc:
                        seen["kind"], seen["cls"], seen["text"] = (
                            kind_of(exc),
                            type(exc).__name__,
                            str(exc),
                        )
                        raise
                return FeedResult(rows_read=0, rows_written=0)

            async def task() -> None:
                await run_feed(
                    engine,
                    "fred",
                    f"SMOKE_{status}",
                    fred_body,
                    limiter=limiter,
                    retry=RetryPolicy(attempts=2, initial_seconds=0.2, max_seconds=0.5),
                    secrets=[KEY],
                )

            try:
                await run_recorded(engine, "smoke.fred_failures", task)
            except Exception:
                pass
            print(json.dumps({"status": status, "hits": state["hits"], **seen}), flush=True)
    finally:
        server.shutdown()
        await redis.aclose()
        await engine.dispose()


asyncio.run(main())
