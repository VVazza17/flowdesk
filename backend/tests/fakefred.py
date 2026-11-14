"""A fake FRED for the ingest tests: what the real API was MEASURED to serve (vintage dates,
`output_type=4` first prints with the 2000-vintage cap, `output_type=3` wide rows including bare
rows and a gateway timeout past a window size, the latest values). Shared by the backfill and
poll tests.
"""

from datetime import date, timedelta

import httpx2

START = date(2020, 1, 6)  # a Monday

Chain = list[tuple[date, str | None]]


class Limiter:
    def __init__(self) -> None:
        self.acquired = 0

    async def acquire(self, provider: str) -> None:
        self.acquired += 1


def simple(
    days: int, *, lag: int = 1, rev: dict[int, list[tuple[int, str | None]]] | None = None
) -> dict[date, Chain]:
    """Period i is first printed 1.5 on START+i+lag. `rev[i]` lists (days after that first
    publication, value) for its revisions."""
    out: dict[date, Chain] = {}
    for i in range(days):
        first = START + timedelta(days=i + lag)
        chain: Chain = [(first, "1.5")]
        for after, value in (rev or {}).get(i, []):
            chain.append((first + timedelta(days=after), value))
        out[START + timedelta(days=i)] = chain
    return out


class FakeFred:
    """One series. `periods` are in `output_type=4`; `pre_revised` periods (before the record,
    revised after it) are in `output_type=3` and `latest` only; `pre_plain` and `gaps` are in
    `latest` only. `hide` drops a first print from `output_type=4` alone (a silent shortfall).
    `o3_limit`: a window holding more vintage dates than this gets the gateway's 504."""

    def __init__(
        self,
        periods: dict[date, Chain],
        *,
        pre_revised: dict[date, Chain] | None = None,
        pre_plain: tuple[date, ...] = (),
        gaps: tuple[date, ...] = (),
        hide: tuple[date, ...] = (),
        o3_limit: int = 100_000,
    ) -> None:
        self.periods = periods
        self.pre_revised = pre_revised or {}
        self.pre_plain, self.gaps, self.hide, self.o3_limit = pre_plain, gaps, set(hide), o3_limit
        self.calls: list[str] = []
        self.windows_asked: list[tuple[str, date, date]] = []

    def chains(self) -> dict[date, Chain]:
        return {**self.pre_revised, **self.periods}

    def vintages(self) -> list[date]:
        return sorted({v for chain in self.chains().values() for v, _ in chain})

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        q = request.url.params
        sid = q["series_id"]

        def text_of(value: str | None) -> str:
            return "." if value is None else value

        if request.url.path.endswith("/series/vintagedates"):
            self.calls.append("vintagedates")
            v = self.vintages()
            return httpx2.Response(
                200, json={"count": len(v), "vintage_dates": [d.isoformat() for d in v]}
            )
        output_type = q["output_type"]
        self.calls.append(f"o{output_type}")
        if output_type == "1":
            rows = {p: ch[-1][1] for p, ch in self.chains().items()}
            rows.update({p: "2.5" for p in self.pre_plain})
            rows.update({p: None for p in self.gaps})
            body = [
                {
                    "realtime_start": "2026-11-12",
                    "realtime_end": "2026-11-12",
                    "date": p.isoformat(),
                    "value": text_of(v),
                }
                for p, v in sorted(rows.items())
            ]
            return httpx2.Response(200, json={"count": len(body), "observations": body})
        start, end = date.fromisoformat(q["realtime_start"]), date.fromisoformat(q["realtime_end"])
        held = [v for v in self.vintages() if start <= v <= end]
        self.windows_asked.append((output_type, start, end))
        if not held:
            # FRED's real answer to a vintage output type over a window holding no vintage date. A
            # code path that stops reading `series/vintagedates` first gets this back.
            return httpx2.Response(
                400,
                json={
                    "error_code": 400,
                    "error_message": "Bad Request.  No vintage dates exist for the specified "
                    "real-time period.",
                },
            )
        if len(held) > 2000:
            return httpx2.Response(
                400,
                json={
                    "error_code": 400,
                    "error_message": "This exceeds the maximum number of vintage dates (2000).",
                },
            )
        if output_type == "4":
            rows4 = [
                {
                    "realtime_start": ch[0][0].isoformat(),
                    "realtime_end": "9999-12-31",
                    "date": p.isoformat(),
                    "value": text_of(ch[0][1]),
                }
                for p, ch in sorted(self.periods.items())
                if p not in self.hide and start <= ch[0][0] <= end
            ]
            return httpx2.Response(200, json={"count": len(rows4), "observations": rows4})
        assert output_type == "3"
        if len(held) > self.o3_limit:
            return httpx2.Response(504, text="<html>504 Gateway Time-out</html>")
        rows3: list[dict[str, str]] = []
        for p, ch in sorted(self.chains().items()):
            cols = {f"{sid}_{v:%Y%m%d}": text_of(val) for v, val in ch if start <= v <= end}
            if cols:
                rows3.append({"date": p.isoformat(), **cols})
            elif ch[0][0] < start:
                rows3.append({"date": p.isoformat()})  # unchanged in this window: a bare row
        return httpx2.Response(200, json={"count": len(rows3), "observations": rows3})


class Routed:
    """Routes by series_id to a FakeFred and counts the calls that reach each."""

    def __init__(self, fakes: dict[str, FakeFred]) -> None:
        self.fakes = fakes
        self.touched: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        sid = request.url.params["series_id"]
        self.touched.append(sid)
        if sid not in self.fakes:
            body = {"error_code": 400, "error_message": "Bad Request.  The series does not exist."}
            return httpx2.Response(400, json=body)
        return self.fakes[sid](request)
