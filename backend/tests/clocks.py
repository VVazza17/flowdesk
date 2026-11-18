"""Clocks for tests that compare two wall-clock instants.

This machine's wall clock is NOT monotonic: Docker Desktop's WSL2 VM runs it about 8% fast and steps
it back about 2.3 s every 30 s (MEASURED 2026-11-18, docs/evidence/clock-step-probe.py.txt and
docs/BUILD-LOG.md). A test that asserts `b >= a` for two `datetime.now()` readings fails whenever a
step lands between them (the heartbeat test did, 5 times in 150 runs). Inject this clock instead
wherever the code under test takes one.
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta


def stepless_clock() -> Callable[[], datetime]:
    """Wall-clock-shaped and timezone-aware, but it cannot step: anchored once to the real clock,
    then advanced by the monotonic clock. Safe to call from several threads."""
    anchor, start = datetime.now(UTC), time.monotonic()
    return lambda: anchor + timedelta(seconds=time.monotonic() - start)
