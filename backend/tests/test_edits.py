"""tools/edits.py: a script that writes files writes ALL its changes or NONE (CLAUDE.md rule 11,
2026-11-18)."""

from pathlib import Path

import pytest

from tools.edits import Append, Edit, EditsFailed, apply


def test_every_change_is_written_when_all_apply(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("one two three", encoding="utf-8")
    (tmp_path / "b.md").write_text("log\n", encoding="utf-8")
    apply(
        [Edit("a.md", "two", "2"), Edit("a.md", "2 three", "2 3"), Append("b.md", "entry\n")],
        tmp_path,
    )
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == "one 2 3"
    assert (tmp_path / "b.md").read_text(encoding="utf-8") == "log\nentry\n"


def test_one_failing_edit_writes_nothing_anywhere(tmp_path: Path) -> None:
    # The failure of 2026-11-18: an append and an edit applied, then an edit missed. Now the miss
    # is found before any file is touched, and every miss is named.
    (tmp_path / "a.md").write_text("one two", encoding="utf-8")
    (tmp_path / "b.md").write_text("log\n", encoding="utf-8")
    with pytest.raises(EditsFailed) as caught:
        apply(
            [
                Append("b.md", "entry\n"),
                Edit("a.md", "one", "1"),
                Edit("a.md", "missing", "x"),
                Edit("c.md", "x", "y"),
            ],
            tmp_path,
        )
    assert "3. a.md: expected exactly 1 match, found 0" in str(caught.value)
    assert "4. c.md: no such file" in str(caught.value)
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == "one two"
    assert (tmp_path / "b.md").read_text(encoding="utf-8") == "log\n"


def test_an_ambiguous_edit_is_refused(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("x x", encoding="utf-8")
    with pytest.raises(EditsFailed, match="found 2"):
        apply([Edit("a.md", "x", "y")], tmp_path)
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == "x x"
