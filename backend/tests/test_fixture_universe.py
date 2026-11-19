"""The recorded yfinance fixtures use the seeded universe, so a list typed from memory cannot drift.

It happened once (2026-11-18): the recorder's universe was typed from memory (XLC and IBB instead of
IWM and ARKK), the fixture inherited it, and the bar tests agreed with the fixture, so nothing
failed until the backfill met the real seed. Two checks close that: the fixture's tickers, and the
recorder script's own list, must both equal `settings.UNIVERSES`.
"""

import ast
import json
import re

from app import settings
from tests.yfixtures import DIR

DEAD = "ZZZQXFAKE"


def seeded() -> set[str]:
    return {t for u in settings.UNIVERSES for t in u.tickers}


def test_the_universe_fixture_holds_exactly_the_seeded_symbols() -> None:
    d = json.loads((DIR / "daily_3mo_universe_plus_dead.json").read_text(encoding="utf-8"))
    tickers = {c[0] for c in d["columns"]}
    assert tickers - {DEAD} == seeded()
    assert DEAD in tickers  # and the known-dead one, for D4


def test_the_recorders_universe_is_the_seeded_universe() -> None:
    source = (DIR / "record.py.txt").read_text(encoding="utf-8")
    found = re.search(r"^UNIVERSE = (\[.*?\])\n", source, flags=re.S | re.M)
    assert found, "record.py.txt has no UNIVERSE list"
    assert set(ast.literal_eval(found.group(1))) == seeded()
