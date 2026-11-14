"""Make a skipped LIVE-DATA check impossible to miss.

A test that checks the stored data against independent evidence needs data a fresh database
(GitHub's CI, a clean checkout) does not have, so it skips there, permanently. A permanent silent
skip is an absent test that nobody notices. So a live-data skip carries a marker in its reason, and
`tests/conftest.py` prints a banner from `pytest_terminal_summary` for every such skip: it prints
whatever the verbosity, with `-q`, and with plain `pytest` (CI passes no `-rs`). The test gate
(`backend/tools/junit_skips.py`) allows exactly this kind of skip and prints it a second time; any
other skip still fails the run.
"""

from collections.abc import Iterable
from typing import Any

LIVE_MARKER = "[live-data]"


def live_reason(detail: str) -> str:
    """The skip reason for a live-data check: marker first, 'NOT RUN' in words."""
    return f"{LIVE_MARKER} NOT RUN: {detail}"


def _reason(report: Any) -> str:
    longrepr = getattr(report, "longrepr", None)
    # A skip's longrepr is (path, line, "Skipped: <reason>").
    return str(longrepr[-1]) if isinstance(longrepr, tuple) and longrepr else str(longrepr)


def banner_lines(skipped: Iterable[Any]) -> list[str]:
    """The lines to print, or [] when no live-data check was skipped."""
    live = [r for r in skipped if LIVE_MARKER in _reason(r)]
    if not live:
        return []
    lines = [
        f"{len(live)} LIVE-DATA CHECK(S) DID NOT RUN. They check the stored data against "
        "independent evidence, so they cannot run on a fresh database (GitHub's CI, a clean "
        "checkout):"
    ]
    for report in live:
        reason = _reason(report).replace("Skipped: ", "", 1)
        lines.append(f"  {report.nodeid}")
        lines.append(f"      {reason}")
    lines.append(
        "Run them in a development stack with the series captured: "
        "python -m pytest tests/test_macro_oracle_live.py -rs"
    )
    return lines
