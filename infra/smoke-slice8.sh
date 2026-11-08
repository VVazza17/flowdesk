#!/usr/bin/env bash
# M1 slice 8 smoke test: every CI gate passes on the repo as it stands, and each one goes red for the
# right reason on a deliberately broken temp copy. Nothing in the working tree is modified.
# Run from the repo root (needs a .env; see .env.example).
#
# Not covered here: the Grype image scan runs only in ci.yml (it is a pinned action).
set -euo pipefail

CHECKS=infra/ci-checks.sh
. infra/base-image.sh
TMP_ROOT=$(mktemp -d)
trap 'rm -rf "$TMP_ROOT"' EXIT

fail() { echo "FAIL: $1" >&2; exit 1; }

# A copy must reuse this checkout's stack, not start a second on the same ports, so the project name is
# pinned for the copies' compose calls. DECISION: pin the name compose itself derives for THIS checkout
# (`docker compose config` prints it), not a literal "flowdesk": a literal made the script act on
# whichever checkout's stack happened to be named flowdesk. An explicit COMPOSE_PROJECT_NAME from the
# caller still wins, like for every other infra/ script.
if [ -z "${COMPOSE_PROJECT_NAME:-}" ]; then
  COMPOSE_PROJECT_NAME=$(docker compose config | sed -n 's/^name: //p' | tr -d '\r') || true
fi
[ -n "${COMPOSE_PROJECT_NAME:-}" ] || fail "could not determine this checkout's compose project name (needs a .env)"
export COMPOSE_PROJECT_NAME
echo "scoped to compose project: $COMPOSE_PROJECT_NAME"

# A fresh copy of what the gates read, with the working tree's uncommitted changes included.
copy_repo() {
  local dir
  dir=$(mktemp -d "$TMP_ROOT/repo.XXXXXX")
  cp -r backend infra docker-compose.yml .env "$dir/"
  echo "$dir"
}

expect_pass() { # name, command...
  local name=$1; shift
  "$@" > "$TMP_ROOT/out.log" 2>&1 || { cat "$TMP_ROOT/out.log" >&2; fail "$name should pass"; }
  echo "ok (green): $name"
}

# The pattern matters: a failed pip install also exits non-zero, and must not count as the gate firing.
expect_red() { # name, pattern the output must contain, command...
  local name=$1 pattern=$2; shift 2
  if "$@" > "$TMP_ROOT/out.log" 2>&1; then
    cat "$TMP_ROOT/out.log" >&2
    fail "$name should have failed"
  fi
  grep -qE "$pattern" "$TMP_ROOT/out.log" || {
    cat "$TMP_ROOT/out.log" >&2
    fail "$name failed, but not with /$pattern/"
  }
  echo "ok (red):   $name"
}

for gate in lint test audit license; do
  expect_pass "$gate on the repo as it stands" bash "$CHECKS" "$gate"
done

repo=$(copy_repo)
printf 'import os\n' >> "$repo/backend/app/settings.py"
printf 'x=1;y=2\nimport   sys\n' > "$repo/backend/app/badstyle.py"
expect_red "lint: ruff violation" 'F401|E702' bash "$repo/$CHECKS" lint

repo=$(copy_repo)
printf 'def f() -> int:\n    return "not an int"\n' > "$repo/backend/app/badtype.py"
expect_red "lint: type error" 'badtype.py.*error' bash "$repo/$CHECKS" lint

repo=$(copy_repo)
mkdir -p "$repo/frontend/app"
printf '<p>Returns are not guaranteed.</p>\n' > "$repo/frontend/app/page.tsx"
expect_red "lint: banned phrase in UI text" 'banned phrase: frontend/app/page.tsx' bash "$repo/$CHECKS" lint

repo=$(copy_repo)
mkdir -p "$repo/docs"
printf 'Never say guaranteed or free money.\n' > "$repo/docs/rules.md"
printf 'Never say guaranteed or free money.\n' > "$repo/CLAUDE.md"
expect_pass "lint: the same phrases in docs do not fire" bash "$repo/$CHECKS" lint

repo=$(copy_repo)
sed -i 's/^fastapi==.*/fastapi==0.1.0/' "$repo/backend/requirements.txt"
expect_red "lint: direct pin missing from its lock" 'pins fastapi==0.1.0' bash "$repo/$CHECKS" lint

repo=$(copy_repo)
sed -i '/^FROM /s/@sha256:[0-9a-f]*//' "$repo/backend/Dockerfile"
expect_red "lint: an unpinned base image" 'not tag@sha256' bash "$repo/$CHECKS" lint

repo=$(copy_repo)
tag=3.12-slim # a variable, so this file doesn't itself contain the literal the guard looks for
echo "docker run --rm python:$tag true" >> "$repo/infra/smoke-fake.sh"
expect_red "lint: a second definition of the base image" 'second definition of the Python base image' \
  bash "$repo/$CHECKS" lint

repo=$(copy_repo)
printf 'def test_broken() -> None:\n    assert False\n' > "$repo/backend/tests/test_broken.py"
expect_red "test: failing test" 'test_broken' bash "$repo/$CHECKS" test

repo=$(copy_repo)
printf 'import pytest\n\n\n@pytest.mark.skip(reason="x")\ndef test_skipped() -> None:\n    pass\n' \
  > "$repo/backend/tests/test_skipped.py"
expect_red "test: a skipped test fails the skip guard" 'tests were skipped' bash "$repo/$CHECKS" test

repo=$(copy_repo)
sed -i 's/^def upgrade() -> None:$/def upgrade() -> None:\n    raise RuntimeError("smoke8 broken migration")/' \
  "$repo"/backend/alembic/versions/0002_ops_tables.py
expect_red "test: a migration that raises" 'smoke8 broken migration' bash "$repo/$CHECKS" test
# A migration that raises must not strand its scratch databases: fixtures clean up on failure too.
leaked=$(docker compose exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -c "select count(*) from pg_database where datname ~ '"'"'^flowdesk_(ops|mig|health)_[0-9a-f]{8}$'"'"'"')
[ "$leaked" = "0" ] || fail "test fixtures left $leaked scratch databases behind"
echo "ok (green): no scratch databases left after a raising migration"

repo=$(copy_repo)
# Hashes aren't checked with --disable-pip: nothing is installed, the pin is only looked up.
printf 'urllib3==1.25.8 --hash=sha256:%064d\n' 0 >> "$repo/backend/requirements.lock"
expect_red "audit: a pin with a known advisory" 'urllib3' bash "$repo/$CHECKS" audit

# At tool level: a real unknown-licence package can't be installed through a hash-pinned lock here
# without publishing one. backend/tests/test_license_audit.py covers the wider cases.
license_tool() {
  MSYS_NO_PATHCONV=1 docker run --rm -i -v "$(pwd -W 2>/dev/null || pwd)/backend:/src:ro" -w /src \
    -e PYTHONDONTWRITEBYTECODE=1 "$BASE_IMAGE" python -m tools.license_audit
}
unknown_licence() { echo '[{"Name":"x","Version":"1","License":"UNKNOWN"}]' | license_tool; }
empty_report() { echo '[]' | license_tool; }
expect_red "license: an unknown licence" 'not allowed: x 1' unknown_licence
expect_red "license: an empty report" 'no packages reported' empty_report

# Emulation isn't always installed on a dev machine, so this reports rather than fails. CI's arm64
# job installs QEMU and is the actual gate.
if docker run --rm --platform linux/arm64 "$BASE_IMAGE" true >/dev/null 2>&1; then
  expect_pass "arm64: builds and answers under emulation" bash "$CHECKS" arm64
else
  echo "NOT RUN: arm64 (no emulation on this machine; ci.yml's arm64 job covers it)"
fi

echo "OK: slice 8 smoke passed"
