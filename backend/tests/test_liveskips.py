"""The banner that keeps a skipped live-data check from being an absent test nobody notices."""

from types import SimpleNamespace

from tests.liveskips import LIVE_MARKER, banner_lines, live_reason


def skipped(nodeid: str, reason: str) -> SimpleNamespace:
    # The shape pytest gives a skipped test report: longrepr is (path, line, "Skipped: <reason>").
    return SimpleNamespace(nodeid=nodeid, longrepr=("tests/x.py", 1, f"Skipped: {reason}"))


def test_a_live_data_skip_is_announced_with_its_reason() -> None:
    lines = banner_lines(
        [skipped("tests/test_macro_oracle_live.py::t", live_reason("no CPIAUCSL"))]
    )
    text = "\n".join(lines)
    assert "1 LIVE-DATA CHECK(S) DID NOT RUN" in text
    assert "tests/test_macro_oracle_live.py::t" in text
    assert "NOT RUN: no CPIAUCSL" in text
    assert "python -m pytest tests/test_macro_oracle_live.py -rs" in text  # how to run it


def test_an_ordinary_skip_is_not_announced() -> None:
    assert banner_lines([skipped("tests/test_x.py::t", "DATABASE_URL not set")]) == []
    assert banner_lines([]) == []


def test_only_the_live_data_skips_among_many_are_listed() -> None:
    lines = banner_lines(
        [
            skipped("tests/a.py::1", "DATABASE_URL not set"),
            skipped("tests/b.py::2", live_reason("fresh database")),
            skipped("tests/c.py::3", live_reason("unreachable")),
        ]
    )
    text = "\n".join(lines)
    assert "2 LIVE-DATA CHECK(S)" in text and "tests/a.py::1" not in text
    assert "tests/b.py::2" in text and "tests/c.py::3" in text


def test_the_marker_is_in_every_reason_the_live_helper_builds() -> None:
    assert live_reason("anything").startswith(LIVE_MARKER)
