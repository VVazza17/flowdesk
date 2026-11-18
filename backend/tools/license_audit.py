"""Decision 24's licence gate. Reads `pip-licenses --format=json` on stdin, exits 1 on a violation.

Usage (from backend/):
    pip-licenses --python=<venv python> --format=json | python -m tools.license_audit

DECISION: an allow-list, not pip-licenses' --allow-only --partial-match. Partial matching passes
"MIT AND AGPL-3.0" because the string contains "MIT". Here every part of an expression must be
allowed, so AGPL, UNKNOWN, an empty field and anything nobody has reviewed all fail.

DECISION: "MIT OR GPL-3.0" also fails, though taking the MIT option would be legal. Failing closed
sends a person to read that package, and no dependency needs otherwise today.

Decision 24 names MIT/BSD/Apache as permissive. PSF-2.0 and the per-package MPL-2.0 entries were
added by the owner: see IDEA-027 in docs/SPEC-DEFECTS.md. Add new entries there first, not just
here.
"""

import json
import re
import sys
from collections.abc import Mapping

ALLOWED = frozenset(
    {
        "MIT",
        "MIT License",
        "BSD",
        "BSD License",
        "BSD-2-Clause",
        "BSD-3-Clause",
        # protobuf's spelling of BSD-3-Clause (IDEA-027 addendum 2026-11-18): an alias, not a
        # new licence.
        "3-Clause BSD License",
        "Apache-2.0",
        "Apache 2.0",
        "Apache License 2.0",
        "Apache Software License",
        "PSF-2.0",
        # MIT No Attribution (cffi, via curl-cffi; IDEA-027 addendum 2026-11-18). Permissive.
        "MIT-0",
        "Python Software Foundation License",
    }
)

# Weak, file-level copyleft: acceptable unmodified and never vendored. Allowed per package, so a new
# MPL dependency still stops for a review. Parentheses are stripped before comparing.
_MPL = frozenset({"MPL-2.0", "Mozilla Public License 2.0 MPL 2.0"})

# pathspec comes in through mypy, so it is dev-only. This gate treats shipped and dev-only packages
# alike (IDEA-027).
PACKAGE_EXCEPTIONS: Mapping[str, frozenset[str]] = {
    "pathspec": _MPL,
    # certifi (CA bundle, via curl-cffi and requests) SHIPS, unlike pathspec. Allowed on condition
    # it is shipped unmodified with its source public on PyPI; a commercial distribution warrants a
    # proper licence review (IDEA-027 addendum 2026-11-18).
    "certifi": _MPL,
}


# numpy's own licence is BSD-3-Clause. The 0BSD, Zlib and CC0-1.0 parts come from vendored
# components (IDEA-027, 2026-11-10), so this is not an approval of those licences in general. It is
# keyed to the exact expression reviewed, not to the package name: a different string from a future
# numpy fails the gate and stops for review instead of inheriting this entry.
EXPRESSION_EXCEPTIONS: Mapping[str, frozenset[str]] = {
    "numpy": frozenset({"BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0"}),
}

# peewee publishes no licence metadata at all (pip-licenses reports UNKNOWN). Its licence FILE is
# MIT text ("Permission is hereby granted, free of charge...", Copyright (c) 2010 Charles Leifer),
# read 2026-11-18 (IDEA-027 addendum). Keyed to the exact (name, version) whose file was read: an
# upgrade fails the gate and must be re-verified, never inherited.
VERSION_EXCEPTIONS: Mapping[tuple[str, str], frozenset[str]] = {
    ("peewee", "4.5.3"): frozenset({"UNKNOWN"}),
}


def license_parts(expression: str) -> list[str]:
    """Split on SPDX AND/OR and on the '; ' pip-licenses puts between classifiers."""
    flat = expression.replace("(", "").replace(")", "")
    return [part.strip() for part in re.split(r"\s+(?:AND|OR)\s+|;", flat) if part.strip()]


def violations(packages: list[dict[str, str]]) -> list[str]:
    problems = []
    for package in packages:
        name, version = package["Name"], package["Version"]
        allowed = ALLOWED | PACKAGE_EXCEPTIONS.get(name.lower(), frozenset())
        expression = package["License"]
        parts = license_parts(expression)
        if expression in EXPRESSION_EXCEPTIONS.get(name.lower(), frozenset()):
            continue
        if expression in VERSION_EXCEPTIONS.get((name.lower(), version), frozenset()):
            continue
        if not parts or any(part not in allowed for part in parts):
            problems.append(f"{name} {version}: {expression!r}")
    return problems


def main() -> int:
    packages = json.load(sys.stdin)
    # An empty report would otherwise pass: a wrong --python path must not look like a clean audit.
    if not packages:
        print("license audit: no packages reported", file=sys.stderr)
        return 1
    problems = violations(packages)
    for problem in problems:
        print(f"license audit: not allowed: {problem}", file=sys.stderr)
    print(f"license audit: {len(packages)} packages checked, {len(problems)} not allowed")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
