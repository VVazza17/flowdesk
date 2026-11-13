"""M2 slice 5, step 3, commit B: the arguments of `python -m app.backfill`. No database."""

import pytest

from app import backfill, settings
from app.backfill import parse_args, select_series


def test_no_arguments_means_every_series_not_yet_captured() -> None:
    args = parse_args([])
    assert args.series is None and args.force is False
    assert select_series(settings.FRED_SERIES, args.series) == list(settings.FRED_SERIES)


def test_series_are_selected_in_configured_order_whatever_order_they_are_named() -> None:
    args = parse_args(["--series", "SOFR,DGS10"])
    chosen = [s.series_id for s in select_series(settings.FRED_SERIES, args.series)]
    assert chosen == ["DGS10", "SOFR"]


def test_force_must_name_its_series(capsys: pytest.CaptureFixture[str]) -> None:
    # Re-capturing every series is about 90 minutes of FRED time: it has to be asked for by name.
    with pytest.raises(SystemExit) as info:
        parse_args(["--force"])
    assert info.value.code == 2
    assert "--force needs --series" in capsys.readouterr().err


def test_force_with_a_named_series_is_accepted() -> None:
    args = parse_args(["--series", "DGS10", "--force"])
    assert args.force is True and args.series == ["DGS10"]


def test_an_unknown_series_is_refused_before_anything_runs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as info:
        parse_args(["--series", "DGS10,NOTASERIES"])
    assert info.value.code == 2
    assert "NOTASERIES" in capsys.readouterr().err


def test_the_start_of_a_run_tells_the_operator_to_keep_the_machine_awake(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A laptop slept mid-request on 2026-11-13 and the run sat for nine hours.
    async def no_run(ids: object, force: bool) -> None:
        return None

    monkeypatch.setattr(backfill, "run", no_run)
    assert backfill.main([]) == 0
    err = capsys.readouterr().err
    assert "KEEP THIS MACHINE AWAKE" in err and "nine hours" in err
