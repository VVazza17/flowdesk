"""Which paths the lint gate covers, as a test (alembic/ once escaped a venv check for two commits).

The gate is `bash infra/ci-checks.sh lint`, which runs INSIDE backend/: `ruff check .`, `ruff format
--check .` and `mypy` (config: backend/pyproject.toml). A check run on a list of paths (`ruff check
app tests`) let alembic/ through. This pins:

* the gating invocation reaches every directory of Python we own, and mypy's `files` match it;
* the repo-root `ruff.toml` (a guard for runs from the root, see its comment) does not change which
  backend files are linted, and keeps the FROZEN spec and spikes out of a root run.

It uses ruff's own discovery and its own `format --check`, so it is the code that gates.
"""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
SKIP_DIRS = {"__pycache__", "node_modules", "venv", "env"}


def ruff_files(cwd: Path) -> list[str]:
    out = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--no-cache", "--show-files", "."],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted(
        Path(line.strip()).resolve().relative_to(ROOT).as_posix()
        for line in out.splitlines()
        if line.strip().endswith(".py")
    )


def python_dirs() -> set[str]:
    """Top-level directories of backend/ that hold Python."""
    found = set()
    for path in BACKEND.rglob("*.py"):
        parts = path.relative_to(BACKEND).parts
        if len(parts) > 1 and not set(parts) & SKIP_DIRS and not parts[0].startswith("."):
            found.add(parts[0])
    return found


def test_the_gating_invocation_covers_every_directory_of_python_we_own() -> None:
    covered = {f.split("/")[1] for f in ruff_files(BACKEND) if f.count("/") >= 2}
    assert {"app", "tests", "alembic", "tools"} <= covered
    assert python_dirs() <= covered, f"not linted: {sorted(python_dirs() - covered)}"
    # And no Python sits loose in backend/ where neither list names it.
    assert [p.name for p in BACKEND.glob("*.py")] == []


def test_mypy_covers_exactly_the_directories_ruff_does() -> None:
    config = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    mypy_files = set(config["tool"]["mypy"]["files"])
    assert mypy_files == python_dirs(), (
        f"mypy files {sorted(mypy_files)} != python directories {sorted(python_dirs())}: "
        "a directory of Python we own is not type-checked, or a listed one is gone"
    )


@pytest.mark.skipif(not (ROOT / "ruff.toml").exists(), reason="no repo-root ruff.toml")
def test_the_root_guard_changes_nothing_for_backend() -> None:
    gating = [f for f in ruff_files(BACKEND) if f.startswith("backend/")]
    from_root = ruff_files(ROOT)
    assert [f for f in from_root if f.startswith("backend/")] == gating  # the same files
    # Files under backend/ are governed by backend/pyproject.toml (the NEAREST config), so the root
    # file's excludes, anchored at the root, never reach them: backend/tests is still linted.
    assert any(f.startswith("backend/tests/") for f in from_root)


@pytest.mark.skipif(not (ROOT / "ruff.toml").exists(), reason="no repo-root ruff.toml")
def test_a_format_run_from_the_repo_root_cannot_rewrite_the_frozen_spec() -> None:
    """The incident, as a test. `ruff format .` from the repo root once rewrote the Python code
    blocks inside docs/architecture.md (ruff formats Markdown) and five spike files. `ruff check
    --show-files` lists only .py files, so it cannot see that; this runs the command that did it, in
    check mode (it writes nothing), and fails if docs/ or spikes/ would be reformatted."""
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--check", "--no-cache", "."],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    touched = [
        line
        for line in (result.stdout + result.stderr).splitlines()
        if line.startswith("Would reformat") and ("docs" in line or "spikes" in line)
    ]
    assert touched == [], f"a root `ruff format .` would rewrite frozen paths: {touched[:3]}"
    assert result.returncode == 0, result.stdout + result.stderr  # and everything else is formatted
