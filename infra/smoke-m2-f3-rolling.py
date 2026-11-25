"""Probe for infra/smoke-m2-f3-rolling.sh: run INSIDE the worker container (real image, real Redis
limiter, real Postgres), against a SCRATCH database it creates and drops. macro_metrics is
insert-only, so nothing here may touch the dev database: every write goes to the scratch one.

FRED is a stub (httpx2.MockTransport) shaped like what F-6b measured on BAMLC0A0CM / BAMLH0A0HYM2:
  - the oldest periods drop out of the latest vintage AND their vintage dates out of
    series/vintagedates (the rolling window, cause (i));
  - a period appears at a vintage dated before captured_through, after the capture (F3, cause (ii)).
Two series: SMOKE_ROLL (declared rolling) and SMOKE_PLAIN (the same data, not declared): the
control. Each line printed is JSON; the last is {"result": "PASS"} or the probe exits non-zero.
"""

import asyncio
import json
import os
import subprocess
import sys
import uuid
from datetime import date, timedelta
from typing import Any

import httpx2
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app import settings
from app.ingest.budget import RedisSpacingLimiter
from app.ingest.fred_feed import PROVIDER, history_body
from app.ingest.macro import FredSeries, available_at_for
from app.ingest.runner import RetryPolicy, run_feed
from app.tasks.fred_ingest import PollIncomplete, poll_all
from app.tasks.runs import run_recorded

KEY = "zzsmoke0123456789abcdefghijklmn12"  # not a real key; the stub never checks it
START = date(2023, 11, 13)  # a Monday
NO_RETRY = RetryPolicy(attempts=1)


def chains(n: int, skip: tuple[int, ...] = (), drop: tuple[int, ...] = ()) -> dict[date, list]:
    """Period i (calendar days from START) first printed 0.9 + i/100 at vintage START + i + 1."""
    out = {}
    for i in range(n):
        if i in skip or i in drop:
            continue
        out[START + timedelta(days=i)] = [(START + timedelta(days=i + 1), f"{0.9 + i / 100:.2f}")]
    return out


class Stub:
    def __init__(self, periods: dict[date, list]) -> None:
        self.periods = periods
        self.period_requests: list[str] = []

    def vintages(self) -> list[date]:
        return sorted({v for ch in self.periods.values() for v, _ in ch})

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        q, sid = request.url.params, request.url.params["series_id"]
        if request.url.path.endswith("/series/vintagedates"):
            v = self.vintages()
            return httpx2.Response(200, json={"count": len(v), "vintage_dates": [d.isoformat() for d in v]})
        ot = q["output_type"]
        if ot == "1":
            obs = [{"realtime_start": "2026-11-24", "realtime_end": "2026-11-24", "date": p.isoformat(),
                    "value": ch[-1][1]} for p, ch in sorted(self.periods.items())]
            return httpx2.Response(200, json={"count": len(obs), "observations": obs})
        start, end = date.fromisoformat(q["realtime_start"]), date.fromisoformat(q["realtime_end"])
        if not [v for v in self.vintages() if start <= v <= end]:
            return httpx2.Response(400, json={"error_code": 400, "error_message":
                                              "Bad Request.  No vintage dates exist for the specified real-time period."})
        if ot == "4":
            obs = [{"realtime_start": ch[0][0].isoformat(), "realtime_end": "9999-12-31",
                    "date": p.isoformat(), "value": ch[0][1]}
                   for p, ch in sorted(self.periods.items()) if start <= ch[0][0] <= end]
            return httpx2.Response(200, json={"count": len(obs), "observations": obs})
        lo, hi = q.get("observation_start"), q.get("observation_end")
        if lo:
            self.period_requests.append(lo)
        rows = []
        for p, ch in sorted(self.periods.items()):
            if (lo and p < date.fromisoformat(lo)) or (hi and p > date.fromisoformat(hi)):
                continue
            cols = {f"{sid}_{v:%Y%m%d}": val for v, val in ch if start <= v <= end}
            if cols:
                rows.append({"date": p.isoformat(), **cols})
            elif ch[0][0] < start:
                rows.append({"date": p.isoformat()})
        return httpx2.Response(200, json={"count": len(rows), "observations": rows})


class Routed:
    def __init__(self, stubs: dict[str, Stub]) -> None:
        self.stubs = stubs

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        return self.stubs[request.url.params["series_id"]](request)


def out(**kw: Any) -> None:
    print(json.dumps(kw, default=str), flush=True)


def check(ok: bool, what: str) -> None:
    out(check=what, ok=ok)
    if not ok:
        raise SystemExit(f"FAIL: {what}")


async def q(engine: AsyncEngine, sql: str) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        return [dict(r._mapping) for r in await conn.execute(text(sql))]


async def main() -> int:
    admin_url = settings.database_url()
    redis_url = settings.redis_url()
    assert admin_url and redis_url
    name = f"flowdesk_smoke_f3_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    scratch_url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    redis = Redis.from_url(redis_url, decode_responses=True)
    engine = create_async_engine(scratch_url)
    try:
        done = subprocess.run(["alembic", "upgrade", "head"], cwd="/srv", capture_output=True, text=True,
                              env={**os.environ, "DATABASE_URL": scratch_url})
        check(done.returncode == 0, "alembic upgrade head on the scratch database")
        out(scratch_database=name)

        roll = FredSeries("SMOKE_ROLL", START + timedelta(days=1), START, rolling_window=True)
        plain = FredSeries("SMOKE_PLAIN", START + timedelta(days=1), START)
        # Capture: periods 0..9, except period 6, which FRED has not published yet.
        routed = Routed({s.series_id: Stub(chains(10, skip=(6,))) for s in (roll, plain)})
        limiter = RedisSpacingLimiter(redis, settings.RATE_BUDGETS)

        async def pace() -> None:
            await limiter.acquire(PROVIDER)

        async def capture() -> None:
            for s in (roll, plain):
                await run_feed(engine, PROVIDER, s.series_id,
                               history_body(s, api_key=KEY, user_agent="FlowDesk-smoke", pace=pace,
                                            base_url="https://fred.invalid/fred",
                                            transport=httpx2.MockTransport(routed), engine=engine),
                               limiter=limiter, retry=NO_RETRY, secrets=[KEY])

        await run_recorded(engine, "smoke.f3_capture", capture)
        before = await q(engine, "SELECT * FROM macro_metrics WHERE metric = 'SMOKE_ROLL' ORDER BY effective_at")
        check(len(before) == 9, "captured 9 periods of SMOKE_ROLL")

        # FRED moves: periods 0..2 roll off (with their vintages), period 6 arrives at its old
        # vintage (before captured_through), and period 10 is published.
        for stub in routed.stubs.values():
            stub.periods = chains(11, drop=(0, 1, 2))

        async def poll(series: list[FredSeries]) -> None:
            await poll_all(engine, series, api_key=KEY, user_agent="FlowDesk-smoke", limiter=limiter,
                           base_url="https://fred.invalid/fred", transport=httpx2.MockTransport(routed),
                           retry=NO_RETRY)

        try:
            await run_recorded(engine, "smoke.f3_poll", lambda: poll([roll, plain]))
            check(False, "the poll names SMOKE_PLAIN as failed")
        except PollIncomplete as exc:
            check("SMOKE_PLAIN" in str(exc) and "SMOKE_ROLL" not in str(exc),
                  "SMOKE_ROLL accepted, SMOKE_PLAIN (not declared rolling) refused")
        runs = await q(engine, "SELECT id, feed, status, started_at, rows_written, error FROM ingestion_runs "
                               "WHERE task_run_id = (SELECT max(id) FROM task_runs WHERE task_name = 'smoke.f3_poll') "
                               "ORDER BY id")
        out(poll_runs=[{k: r[k] for k in ("feed", "status", "rows_written", "error")} for r in runs])
        roll_run = next(r for r in runs if r["feed"] == "SMOKE_ROLL")
        check(roll_run["status"] == "succeeded" and roll_run["rows_written"] == 2,
              "SMOKE_ROLL wrote 2 rows (the late period 6 and the new period 10)")
        plain_run = next(r for r in runs if r["feed"] == "SMOKE_PLAIN")
        check(plain_run["status"] == "failed" and (plain_run["error"] or "").startswith("schema_mismatch"),
              "SMOKE_PLAIN failed schema_mismatch")

        late_p, new_p = START + timedelta(days=6), START + timedelta(days=10)
        rows = {r["effective_at"]: r for r in await q(
            engine, "SELECT effective_at, available_at, provider_claimed_available_at, ingestion_run_id "
                    f"FROM macro_metrics WHERE metric = 'SMOKE_ROLL' AND effective_at IN ('{late_p}', '{new_p}')")}
        late_row, new_row = rows[late_p], rows[new_p]
        out(late_row=late_row, new_row=new_row, run_started_at=roll_run["started_at"])
        check(late_row["available_at"] == roll_run["started_at"], "late row: available_at = the accepting run's start")
        check(late_row["provider_claimed_available_at"] == available_at_for(late_p + timedelta(days=1)),
              "late row: FRED's claimed date kept in the audit column")
        check(new_row["available_at"] == available_at_for(new_p + timedelta(days=1))
              and new_row["provider_claimed_available_at"] is None,
              "new period after captured_through: dated by FRED, audit column NULL")
        check(routed.stubs["SMOKE_ROLL"].period_requests == [late_p.isoformat()],
              "exactly one own fetch, for the late period")

        kept = await q(engine, "SELECT * FROM macro_metrics WHERE metric = 'SMOKE_ROLL' AND effective_at "
                               f"NOT IN ('{late_p}', '{new_p}') ORDER BY effective_at")
        check(kept == before, "every stored SMOKE_ROLL row, rolled-off ones included, is exactly as it was")

        await run_recorded(engine, "smoke.f3_poll_again", lambda: poll([roll]))
        again = await q(engine, "SELECT status, rows_written FROM ingestion_runs ORDER BY id DESC LIMIT 1")
        check(again == [{"status": "succeeded", "rows_written": 0}],
              "the next poll is quiet: the late row reads back as FRED's vintage")
        out(result="PASS")
        return 0
    finally:
        await engine.dispose()
        await redis.aclose()
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()
        out(dropped=name)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
