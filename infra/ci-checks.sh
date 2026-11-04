#!/usr/bin/env bash
# The CI gates, callable from anywhere: ci.yml and a local shell run this same code.
#   bash infra/ci-checks.sh lint|test|audit|license|arm64
#
# Every Python step runs in python:3.12-slim with the repo mounted read-only, so a Windows host and
# a GitHub runner execute the same interpreter against the same hash-pinned locks.
#
# DECISION: tests/ and tools/ never go in the image; dev tooling never goes in the app. That is why
# these run in a plain Python container and install from the locks each time.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd -W 2>/dev/null || pwd)"

fail() { echo "FAIL: $1" >&2; exit 1; }

# in_python [docker run options...] -- <shell script>
in_python() {
  local opts=()
  while [ "$1" != "--" ]; do opts+=("$1"); shift; done
  shift
  # MSYS_NO_PATHCONV: Git Bash on Windows would otherwise rewrite the container paths.
  MSYS_NO_PATHCONV=1 docker run --rm \
    -v "$REPO_ROOT:/repo:ro" -w /repo/backend \
    -v flowdesk-pip-cache:/pipcache -e PIP_CACHE_DIR=/pipcache \
    -e PIP_ROOT_USER_ACTION=ignore -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
    -e PYTHONDONTWRITEBYTECODE=1 \
    "${opts[@]+"${opts[@]}"}" python:3.12-slim sh -ec "$1"
}

# A direct pin edited in requirements*.txt without re-running lock-deps.sh would otherwise ship the
# old version silently. Offline and fast, unlike recompiling on every push.
check_locks_match() {
  local input lock pin
  for input in requirements requirements-dev requirements-audit; do
    lock="backend/$input.lock"
    while read -r pin; do
      grep -qiE "^${pin//./\\.} " "$lock" || fail "$input.txt pins $pin but $lock does not (bash infra/lock-deps.sh)"
    done < <(tr -d '\r' < "backend/$input.txt" | grep -E '^[A-Za-z0-9]' | sed -E 's/\[[^]]*\]//')
  done
  echo "locks: every direct pin is in its lock"
}

check_lint() {
  check_locks_match
  in_python -- '
    pip install --quiet --require-hashes -r requirements-dev.lock
    ruff check --no-cache .
    ruff format --no-cache --check .
    mypy --cache-dir=/tmp/mypy
    python -m tools.banned_phrases
  '
}

check_test() {
  [ -f .env ] || fail "no .env (cp .env.example .env)"
  set -a; . ./.env; set +a
  . infra/lib.sh
  docker compose up -d postgres
  wait_ready postgres
  local id network
  id=$(docker compose ps -q postgres)
  network=$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}' "$id")
  in_python --network "$network" \
    -e "DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}" -- '
    pip install --quiet --require-hashes -r requirements-dev.lock
    python -m pytest -p no:cacheprovider -W default --junitxml=/tmp/junit.xml
    # DB tests skip without DATABASE_URL, so a broken setup would otherwise pass with no DB coverage.
    grep -q "skipped=\"0\"" /tmp/junit.xml || { echo "FAIL: tests were skipped" >&2; exit 1; }
  '
}

check_audit() {
  in_python -- '
    pip install --quiet --require-hashes -r requirements-audit.lock
    for lock in requirements.lock requirements-dev.lock requirements-audit.lock; do
      echo "pip-audit: $lock"
      pip-audit --require-hashes --disable-pip -r "$lock"
    done
  '
}

check_license() {
  in_python -- '
    python -m venv /tmp/project
    /tmp/project/bin/pip install --quiet --require-hashes -r requirements-dev.lock
    python -m venv /tmp/audit
    /tmp/audit/bin/pip install --quiet --require-hashes -r requirements-audit.lock
    # To a file, not a pipe: sh has no pipefail, and a pip-licenses crash must not pass.
    /tmp/audit/bin/pip-licenses --python=/tmp/project/bin/python --format=json > /tmp/licenses.json
    python -m tools.license_audit < /tmp/licenses.json
  '
}

# Weaker than arm64 hardware: this runs under QEMU, and only the API, with no database. It does not
# prove migrations or TimescaleDB on arm64.
check_arm64() {
  local image=flowdesk-api:arm64-check name=flowdesk-arm64-check port=18000 code=""
  docker buildx build --platform linux/arm64 --load -t "$image" ./backend
  [ "$(docker run --rm --platform linux/arm64 "$image" uname -m)" = "aarch64" ] \
    || fail "image did not run as aarch64"
  # The compiled wheels are what can differ per architecture, so load them explicitly.
  docker run --rm --platform linux/arm64 "$image" \
    python -c "import app.main, asyncpg, greenlet, pydantic_core, sqlalchemy, redis"

  trap 'docker rm -f "$name" >/dev/null 2>&1 || true' RETURN
  docker run -d --name "$name" --platform linux/arm64 -p "127.0.0.1:$port:8000" "$image" >/dev/null
  local deadline=$((SECONDS + 120))
  while [ $SECONDS -lt $deadline ]; do
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/health/live" || true)
    [ "$code" = "200" ] && break
    sleep 2
  done
  [ "$code" = "200" ] || { docker logs "$name" >&2; fail "/health/live returned '$code' on arm64"; }
  echo "arm64: built, imports load, /health/live 200 under emulation"
}

case "${1:-}" in
  lint) check_lint ;;
  test) check_test ;;
  audit) check_audit ;;
  license) check_license ;;
  arm64) check_arm64 ;;
  *) echo "usage: $0 lint|test|audit|license|arm64" >&2; exit 2 ;;
esac
