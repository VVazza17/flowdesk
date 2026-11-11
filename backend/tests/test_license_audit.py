"""Decision 24's licence gate: AGPL and unknown fail, and "contains MIT" is not enough."""

import pytest

from tools.license_audit import license_parts, violations


def _package(license_text: str, name: str = "somepkg") -> dict[str, str]:
    return {"Name": name, "Version": "1.0", "License": license_text}


@pytest.mark.parametrize(
    "license_text",
    [
        "MIT",
        "MIT License",
        "BSD-3-Clause",
        "BSD License",
        "Apache-2.0",
        "Apache Software License",
        "PSF-2.0",
        "MIT OR Apache-2.0",
        "MIT AND PSF-2.0",
        "Apache-2.0 OR BSD-2-Clause",
        "(MIT OR Apache-2.0) AND BSD-3-Clause",
        "Apache Software License; MIT License",
    ],
)
def test_permissive_licences_pass(license_text: str) -> None:
    assert violations([_package(license_text)]) == []


@pytest.mark.parametrize(
    "license_text",
    [
        "AGPL-3.0",
        "GNU Affero General Public License v3",
        "UNKNOWN",
        "",
        "LGPL-3.0",
        "GPL-3.0",
        "MIT AND AGPL-3.0",
        "MIT OR AGPL-3.0",
        "MIT; AGPL-3.0",
        "(MIT OR Apache-2.0) AND AGPL-3.0",
        "MIT-ish custom terms",
        "SSPL-1.0",
    ],
)
def test_everything_else_fails(license_text: str) -> None:
    assert len(violations([_package(license_text)])) == 1


@pytest.mark.parametrize("license_text", ["MPL-2.0", "Mozilla Public License 2.0 (MPL 2.0)"])
def test_mpl_is_allowed_only_for_named_packages(license_text: str) -> None:
    assert violations([_package(license_text, name="pathspec")]) == []
    assert len(violations([_package(license_text, name="otherpkg")])) == 1
    # Removed on 2026-11-04 (IDEA-027). If it returns with an HTTP client, it stops for a review.
    assert len(violations([_package(license_text, name="certifi")])) == 1


def test_package_exception_does_not_cover_other_licences() -> None:
    assert len(violations([_package("AGPL-3.0", name="pathspec")])) == 1


NUMPY_EXPRESSION = "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0"


def test_numpy_exception_is_scoped_to_the_reviewed_expression() -> None:
    assert violations([_package(NUMPY_EXPRESSION, name="numpy")]) == []
    # Same package, different string (a future numpy): the gate goes red, no inherited exception.
    changed = NUMPY_EXPRESSION + " AND GPL-3.0"
    assert len(violations([_package(changed, name="numpy")])) == 1
    assert len(violations([_package("BSD-3-Clause AND 0BSD", name="numpy")])) == 1
    # Same string, different package: no exception, and 0BSD/Zlib/CC0-1.0 stay unapproved generally.
    assert len(violations([_package(NUMPY_EXPRESSION, name="otherpkg")])) == 1


def test_a_violation_names_the_package_and_its_licence() -> None:
    assert violations([_package("AGPL-3.0")]) == ["somepkg 1.0: 'AGPL-3.0'"]


def test_license_parts_splits_operators_and_classifier_separators() -> None:
    assert license_parts("(MIT OR Apache-2.0) AND X; Y") == ["MIT", "Apache-2.0", "X", "Y"]
