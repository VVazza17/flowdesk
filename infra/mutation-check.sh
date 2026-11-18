#!/usr/bin/env bash
# Mutation check (CLAUDE.md rule 9): break the code in a copy and the named tests must fail.
#   bash infra/mutation-check.sh m2-slice6-step2          the suite's mutations (backend/tools/mutate.py)
#   bash infra/mutation-check.sh --pytest <pytest args>   just run pytest the same way (debugging)
# Same container, locks and database as `ci-checks.sh test`. The repo is mounted read-only and
# backend/ is copied inside the container, so the working tree is never touched.
set -euo pipefail
cd "$(dirname "$0")/.."
. infra/base-image.sh
[ -f .env ] || { echo "FAIL: no .env (cp .env.example .env)" >&2; exit 1; }
set -a; . ./.env; set +a
. infra/lib.sh
docker compose up -d postgres >/dev/null
wait_ready postgres
id=$(docker compose ps -q postgres)
network=$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}' "$id")
REPO_ROOT="$(pwd -W 2>/dev/null || pwd)"
if [ "${1:-}" = "--pytest" ]; then shift; CMD="python -m pytest -p no:cacheprovider $*"; else CMD="python -m tools.mutate ${1:-m2-slice6-step2}"; fi
MSYS_NO_PATHCONV=1 docker run --rm --network "$network" \
  -v "$REPO_ROOT:/repo:ro" -w /tmp \
  -v flowdesk-pip-cache:/pipcache -e PIP_CACHE_DIR=/pipcache \
  -e PIP_ROOT_USER_ACTION=ignore -e PIP_DISABLE_PIP_VERSION_CHECK=1 -e PYTHONDONTWRITEBYTECODE=1 \
  -e "DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}" \
  "$BASE_IMAGE" sh -ec "
    pip install --quiet --require-hashes -r /repo/backend/requirements-dev.lock
    cp -r /repo/backend /tmp/backend && cd /tmp/backend
    $CMD
  "
