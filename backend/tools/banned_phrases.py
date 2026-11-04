"""§17's forbidden vocabulary, checked over UI text, templates and prompts. Exits 1 on a hit.

Usage (from backend/): python -m tools.banned_phrases

The scan names what it covers instead of excluding from a repo-wide grep, because CLAUDE.md and the
spec state these phrases in the rule itself.

Literal on purpose, with no inline suppression: "not guaranteed" fails too (BUILD-LOG, slice 8).

Known limit: user-facing text must live in a path in_scope() matches. A template string in something
like services/premiums.py is not scanned.
"""

import os
import re
import sys
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

# DECISION: §17's list. Decision 14 bans bare "arbitrage"; §7.5, §15 and §17 say "arbitrage
# opportunity". Flagged as IDEA-028 and not resolved here.
PHRASES = ("arbitrage opportunity", "free money", "guaranteed", "riskless")

# \s+ between words, so a phrase wrapped across two lines of JSX is still caught.
PATTERN = re.compile(
    "|".join(r"\s+".join(map(re.escape, phrase.split())) for phrase in PHRASES),
    re.IGNORECASE,
)

SKIPPED_DIRS = {"node_modules", ".next", "out"}
SKIPPED_FILES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock"}
REPO_ROOT = Path(__file__).resolve().parents[2]


def in_scope(path: PurePosixPath) -> bool:
    """`path` is relative to the repo root."""
    parts = path.parts
    if SKIPPED_DIRS.intersection(parts) or path.name in SKIPPED_FILES:
        return False
    if parts[0] == "frontend":
        return True
    if parts[:2] != ("backend", "app"):
        return False
    if len(parts) > 3 and parts[2] == "ai":
        return True
    return any("template" in part.lower() or "prompt" in part.lower() for part in parts[2:])


def scanned_files(root: Path) -> Iterator[Path]:
    for top in ("frontend", "backend/app"):
        for directory, subdirs, files in os.walk(root / top):
            subdirs[:] = [name for name in subdirs if name not in SKIPPED_DIRS]
            for name in files:
                path = Path(directory) / name
                if in_scope(PurePosixPath(path.relative_to(root).as_posix())):
                    yield path


def hits(root: Path) -> tuple[int, list[str]]:
    """Returns (files scanned, one 'path:line: phrase' per hit)."""
    count = 0
    found = []
    for path in scanned_files(root):
        count += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in PATTERN.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            phrase = " ".join(match.group().split())
            found.append(f"{path.relative_to(root).as_posix()}:{line}: {phrase}")
    return count, found


def main() -> int:
    count, found = hits(REPO_ROOT)
    for hit in found:
        print(f"banned phrase: {hit}", file=sys.stderr)
    # The count is printed because 0 files passes trivially, and that should be visible in the log.
    print(f"banned phrases: {count} files scanned, {len(found)} hits")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
