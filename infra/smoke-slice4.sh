#!/usr/bin/env bash
# M1 slice 4 smoke test: the ops tables (§8) exist after the migrate one-shot runs, and an
# EMPTY database migrated by the shipped image reaches 0002 with all four. Run from the repo
# root (needs a .env; see .env.example).
set -euo pipefail

set -a; . ./.env; set +a
. "$(dirname "$0")/lib.sh"

psql_db() { local db="$1"; shift; docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$db" -tA "$@"; }
TABLES="backfill_runs ingestion_runs providers_health task_runs"

check_tables() {  # check_tables <db>
  local got
  got=$(psql_db "$1" -c "select string_agg(table_name, ' ' order by table_name) from information_schema.tables where table_schema='public' and table_name in ('providers_health','ingestion_runs','task_runs','backfill_runs')")
  [ "$got" = "$TABLES" ] || fail "db '$1' has ops tables '$got', expected '$TABLES'"
}

# 1. The one command: up -d runs migrate, which now stops at 0002.
docker compose up -d --build
wait_ready
ver=$(psql_db "$POSTGRES_DB" -c "select version_num from alembic_version")
[ "$ver" = "0002" ] || fail "alembic_version in the main db is '$ver', expected 0002"
check_tables "$POSTGRES_DB"

# 2. An empty database, migrated by the shipped image.
scratch="flowdesk_smoke_$$"
cleanup() { psql_db "$POSTGRES_DB" -c "DROP DATABASE IF EXISTS \"$scratch\" WITH (FORCE)" >/dev/null 2>&1 || true; }
trap cleanup EXIT
psql_db "$POSTGRES_DB" -c "CREATE DATABASE \"$scratch\"" >/dev/null

docker compose run --rm -T \
  -e DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${scratch}" \
  migrate >/dev/null 2>&1 || fail "alembic upgrade head failed on an empty database"

ver=$(psql_db "$scratch" -c "select version_num from alembic_version")
[ "$ver" = "0002" ] || fail "scratch db alembic_version is '$ver', expected 0002"
check_tables "$scratch"

echo "OK: slice 4 smoke passed (up -d migrated to 0002; empty db -> 4 ops tables)"
