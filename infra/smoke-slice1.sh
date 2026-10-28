#!/usr/bin/env bash
# M1 slice 1 smoke test: Postgres/TimescaleDB and Redis come up healthy, and neither
# is exposed beyond loopback. Run from the repo root (needs a .env; see .env.example).
set -euo pipefail

set -a; . ./.env; set +a
. "$(dirname "$0")/lib.sh"

docker compose up -d
wait_ready

out=$(docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -c "select 1")
[ "$out" = "1" ] || fail "psql select 1 returned '$out'"

ext=$(docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA \
  -c "select extname from pg_extension where extname='timescaledb'")
[ "$ext" = "timescaledb" ] || fail "timescaledb extension not installed"

pong=$(docker compose exec -T redis redis-cli ping | tr -d '\r')
[ "$pong" = "PONG" ] || fail "redis ping returned '$pong'"

# `compose port` prints ":0" (exit 0) when the port is not published.
redis_bind=$(docker compose port redis 6379 2>/dev/null || true)
case "$redis_bind" in
  ""|":0") ;;
  *) fail "redis publishes host port '$redis_bind'" ;;
esac

pg_bind=$(docker compose port postgres 5432)
case "$pg_bind" in
  127.0.0.1:*) ;;
  *) fail "postgres bound to '$pg_bind', expected 127.0.0.1" ;;
esac

echo "OK: slice 1 smoke passed (postgres $pg_bind, redis internal only)"
