"""The B2 budget and the rclone pin (2026-11-25): numbers the shell scripts and the API must share,
and the one
definition of the uploader's version. No infrastructure."""

import re
from pathlib import Path

from app import settings

REPO = Path(__file__).resolve().parents[2]
LIB = (REPO / "infra" / "backup" / "lib.sh").read_text(encoding="utf-8")
RUN_COPY = (REPO / "infra" / "backup" / "run-copy.sh").read_text(encoding="utf-8")
DOCKERFILE = (REPO / "infra" / "backup" / "Dockerfile").read_text(encoding="utf-8")


def test_the_shell_and_the_api_use_the_same_budget_lines() -> None:
    warn = re.search(r"^BUDGET_WARN_PERCENT=(\d+)$", LIB, re.M)
    over = re.search(r"^BUDGET_OVER_PERCENT=(\d+)$", LIB, re.M)
    default = re.search(r"BUDGET=\$\{BACKUP_BUDGET_BYTES:-(\d+)\}", RUN_COPY)
    assert warn and over and default
    assert int(warn.group(1)) == settings.BACKUP_BUDGET_WARN_PERCENT == 80
    assert int(over.group(1)) == settings.BACKUP_BUDGET_OVER_PERCENT == 95
    assert int(default.group(1)) == settings.BACKUP_BUDGET_BYTES == 10_000_000_000


def test_rclone_is_pinned_once_by_version_and_both_hashes() -> None:
    versions = re.findall(r"^ARG PIN_RCLONE_VERSION=(\S+)$", DOCKERFILE, re.M)
    hashes = re.findall(r"^ARG PIN_RCLONE_SHA256_(AMD64|ARM64)=([0-9a-f]{64})$", DOCKERFILE, re.M)
    assert versions == ["1.76.0"]
    assert sorted(h[0] for h in hashes) == ["AMD64", "ARM64"]
    # The image refuses to build unless the binary is the pinned version.
    assert 'grep -qx "rclone v${PIN_RCLONE_VERSION}"' in DOCKERFILE
    assert "sha256sum -c -" in DOCKERFILE


def test_no_build_argument_is_in_rclones_own_namespace() -> None:
    """rclone reads every RCLONE_* environment variable as a flag (MEASURED 2026-11-25:
    RCLONE_VERSION became
    --version and failed the build). Build arguments are in the RUN step's environment."""
    assert not re.findall(r"^ARG RCLONE_", DOCKERFILE, re.M)


def test_the_write_and_read_capability_lists_are_the_ones_documented() -> None:
    write = re.search(r'^WRITE_CAPABILITIES="([^"]+)"$', LIB, re.M)
    read = re.search(r'^READ_CAPABILITIES="([^"]+)"$', LIB, re.M)
    assert write and read
    assert set(write.group(1).split()) == {
        "listBuckets",
        "listAllBucketNames",
        "listFiles",
        "writeFiles",
        "readFileRetentions",
    }
    assert set(read.group(1).split()) == {
        "listBuckets",
        "listAllBucketNames",
        "listFiles",
        "readFiles",
        "readFileRetentions",
    }
    for never in (
        "deleteFiles",
        "bypassGovernance",
        "writeFileRetentions",
        "writeBuckets",
        "writeKeys",
    ):
        assert never not in write.group(1) and never not in read.group(1)
