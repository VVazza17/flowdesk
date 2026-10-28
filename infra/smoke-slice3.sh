#!/usr/bin/env bash
# M1 slice 3 smoke test: `alembic upgrade head` applies cleanly to an EMPTY database, using
# the same image that ships, and a plain `docker compose up -d` runs it. Run from the repo
# root (needs a .env; see .env.example).
set -euo pipefail

set -a; . ./.env; set +a
. "$(dirname "$0")/lib.sh"

psql_main() { docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA "$@"; }

# 1. The one command: up -d starts everything and the migrate one-shot exits 0.
docker compose up -d --build
wait_ready
ver=$(psql_main -c "select version_num from alembic_version")
[ "$ver" = "0001" ] || fail "alembic_version in the main db is '$ver', expected 0001"

# 2. An empty database, migrated by the shipped image: nothing from step 1 can mask a problem.
scratch="flowdesk_smoke_$$"
cleanup() { psql_main -c "DROP DATABASE IF EXISTS \"$scratch\" WITH (FORCE)" >/dev/null 2>&1 || true; }
trap cleanup EXIT
psql_main -c "CREATE DATABASE \"$scratch\"" >/dev/null

docker compose run --rm -T \
  -e DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${scratch}" \
  migrate >/dev/null 2>&1 || fail "alembic upgrade head failed on an empty database"

ver=$(docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$scratch" -tA -c "select version_num from alembic_version")
[ "$ver" = "0001" ] || fail "scratch db alembic_version is '$ver', expected 0001"
ext=$(docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$scratch" -tA \
  -c "select extname from pg_extension where extname='timescaledb'")
[ "$ext" = "timescaledb" ] || fail "timescaledb extension missing after migration"

echo "OK: slice 3 smoke passed (up -d ran migrate; empty db -> 0001 + timescaledb)"
