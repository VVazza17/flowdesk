#!/usr/bin/env bash
# Regenerates the hash-pinned locks from backend/requirements*.txt. Run from the repo root after
# editing any of those files. CI checks that every direct pin is in its lock (ci-checks.sh lint).
#
# Why a container: pip-compile resolves for the interpreter and OS it runs on, and the locks must
# describe the image (Linux, Python 3.12), not a Windows or 3.13 host. Hashes cover every published
# wheel, so one lock serves amd64 and arm64; the arm64 CI job proves that by installing it there.
#
# DECISION: pip-tools is pinned here, not in requirements-dev.txt. Nothing but this script uses it,
# and in the dev lock it would add its own dependency tree to the licence audit.
set -euo pipefail
. "$(dirname "$0")/base-image.sh"

# Git Bash on Windows rewrites paths in docker arguments unless told not to.
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd)/backend:/src" -w /src \
  -e CUSTOM_COMPILE_COMMAND="bash infra/lock-deps.sh" \
  "$BASE_IMAGE" sh -ec '
    pip install --quiet --no-cache-dir --root-user-action=ignore pip-tools==7.6.2
    compile() { pip-compile --quiet --generate-hashes --strip-extras --allow-unsafe "$@"; }
    compile --output-file=requirements.lock requirements.txt
    # Constrained by the runtime lock, so tests run against the versions the image ships.
    compile --constraint=requirements.lock --output-file=requirements-dev.lock requirements-dev.txt
    compile --output-file=requirements-audit.lock requirements-audit.txt
  '
