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
        "Apache-2.0",
        "Apache 2.0",
        "Apache License 2.0",
        "Apache Software License",
        "PSF-2.0",
        "Python Software Foundation License",
    }
)

# Weak, file-level copyleft: acceptable unmodified and never vendored. Allowed per package, so a new
# MPL dependency still stops for a review. Parentheses are stripped before comparing.
_MPL = frozenset({"MPL-2.0", "Mozilla Public License 2.0 MPL 2.0"})

# certifi is not in any audited lock since the move to httpx2; its entry stands as the owner's
# recorded decision. pathspec comes in through mypy, so it is dev-only. This gate treats shipped and
# dev-only packages alike (IDEA-027).
PACKAGE_EXCEPTIONS: Mapping[str, frozenset[str]] = {
    "certifi": _MPL,
    "pathspec": _MPL,
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
