"""Apply text edits to files ALL OR NOTHING.

Why it exists (2026-11-18): twice in one day a script stopped partway and the step after it ran
anyway (a lint failure pushed; a doc script that applied half its edits, then a commit). The rule,
CLAUDE.md rule 11: a script that writes files stops on the first failure, and writes nothing until
every change is known to apply. So `apply` builds every file's new text in memory first, checks that
each `Edit.old` occurs exactly once in the text as it stands at that point, and reports EVERY
failure together. Only when there are none does it write, and then it writes all files.

    from tools.edits import Append, Edit, apply
    apply([Edit("CLAUDE.md", "old text", "new text"), Append("docs/BUILD-LOG.md", "...")], root)
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Edit:
    path: str
    old: str
    new: str


@dataclass(frozen=True)
class Append:
    path: str
    text: str


class EditsFailed(Exception):
    """At least one edit did not apply. Nothing was written."""


def plan(changes: Sequence[Edit | Append], root: Path) -> dict[Path, str]:
    """The new text of every file touched, or EditsFailed naming every edit that does not apply."""
    texts: dict[Path, str] = {}
    problems: list[str] = []
    for i, change in enumerate(changes, 1):
        path = (root / change.path).resolve()
        if path not in texts:
            if not path.exists():
                problems.append(f"{i}. {change.path}: no such file")
                continue
            texts[path] = path.read_text(encoding="utf-8")
        if isinstance(change, Append):
            texts[path] += change.text
            continue
        n = texts[path].count(change.old)
        if n != 1:
            head = (
                change.old.strip().splitlines()[0][:70] if change.old.strip() else repr(change.old)
            )
            problems.append(f"{i}. {change.path}: expected exactly 1 match, found {n}: {head!r}")
            continue
        texts[path] = texts[path].replace(change.old, change.new)
    if problems:
        raise EditsFailed("nothing written; these edits do not apply:\n" + "\n".join(problems))
    return texts


def apply(changes: Sequence[Edit | Append], root: Path) -> list[Path]:
    """Write every change, or none of them (raising EditsFailed). Returns the files written."""
    texts = plan(changes, root)
    for path, text in texts.items():
        path.write_text(text, encoding="utf-8", newline="\n")
    return list(texts)
