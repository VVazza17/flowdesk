"""The test gate allows exactly one kind of skip: a loud live-data check (tools/junit_skips.py)."""

from pathlib import Path

import pytest

from tests.liveskips import LIVE_MARKER, live_reason
from tools import junit_skips


def junit(tmp_path: Path, *skips: str) -> str:
    cases = "".join(
        f'<testcase classname="tests.t" name="t{i}"><skipped type="pytest.skip" '
        f'message="{message}">x</skipped></testcase>'
        for i, message in enumerate(skips)
    )
    path = tmp_path / "junit.xml"
    path.write_text(
        f'<testsuites><testsuite tests="{len(skips) + 1}">'
        f'<testcase classname="tests.t" name="passes"/>{cases}</testsuite></testsuites>',
        encoding="utf-8",
    )
    return str(path)


def test_the_marker_is_the_one_the_tests_use() -> None:
    assert junit_skips.LIVE_MARKER == LIVE_MARKER


def test_a_run_with_no_skips_passes(tmp_path: Path) -> None:
    assert junit_skips.main(["x", junit(tmp_path)]) == 0


def test_a_live_data_skip_is_allowed_and_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = junit_skips.main(["x", junit(tmp_path, live_reason("fresh database"))])
    assert code == 0
    out = capsys.readouterr().out
    assert "live-data check skipped (allowed)" in out and "fresh database" in out  # never silent


def test_any_other_skip_still_fails_the_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # DB tests skip without DATABASE_URL; a broken setup must not pass with no database coverage.
    code = junit_skips.main(["x", junit(tmp_path, "DATABASE_URL not set")])
    assert code == 1
    assert "DATABASE_URL not set" in capsys.readouterr().err


def test_one_bad_skip_among_allowed_ones_fails(tmp_path: Path) -> None:
    path = junit(tmp_path, live_reason("fresh database"), "DATABASE_URL not set")
    assert junit_skips.main(["x", path]) == 1


def test_wrong_usage_is_an_error() -> None:
    assert junit_skips.main(["x"]) == 2
