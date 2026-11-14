"""The test gate's rule about skipped tests: none, except a LOUD live-data check.

`ci-checks.sh test` used to fail on `skipped="0"`, because DB tests skip without DATABASE_URL and a
broken setup would otherwise pass with no database coverage. That rule stands. The one exception is
a live-data check (tests/test_macro_oracle_live.py): it compares the stored data with independent
evidence, so on a fresh database such as GitHub's it skips by design. Letting it skip silently would
be an absent test; failing the gate on it would be a permanently red CI, or an invitation to weaken
the rule. So a skip whose reason carries the live-data marker is ALLOWED and PRINTED, and every
other skip fails the run.

    python -m tools.junit_skips /tmp/junit.xml
"""

import sys
import xml.etree.ElementTree as ET

# Must equal tests/liveskips.py::LIVE_MARKER (a test pins it).
LIVE_MARKER = "[live-data]"


def skipped_tests(path: str) -> list[tuple[str, str]]:
    """(test id, skip reason) for every skipped test case in a pytest junit file."""
    found: list[tuple[str, str]] = []
    for case in ET.parse(path).getroot().iter("testcase"):
        skip = case.find("skipped")
        if skip is not None:
            test_id = f"{case.get('classname', '')}::{case.get('name', '')}"
            found.append((test_id, skip.get("message") or (skip.text or "")))
    return found


def judge(skips: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(not allowed, allowed live-data skips)."""
    live = [s for s in skips if LIVE_MARKER in s[1]]
    bad = [s for s in skips if LIVE_MARKER not in s[1]]
    return bad, live


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m tools.junit_skips <junit.xml>", file=sys.stderr)
        return 2
    bad, live = judge(skipped_tests(argv[1]))
    for test_id, reason in live:
        print(f"NOTE: live-data check skipped (allowed): {test_id}\n      {reason}")
    if bad:
        print(f"FAIL: {len(bad)} test(s) were skipped:", file=sys.stderr)
        for test_id, reason in bad:
            print(f"  {test_id}: {reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
