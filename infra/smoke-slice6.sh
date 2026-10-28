#!/usr/bin/env bash
# M1 slice 6 smoke test: killing Redis, then Postgres, produces the documented degraded
# state (docs/HEALTH.md) and never a 500; /health/live stays 200 throughout; recovery works.
# Run from the repo root (needs a .env; see .env.example).
set -euo pipefail

set -a; . ./.env; set +a
. "$(dirname "$0")/lib.sh"
API_PORT="${API_PORT:-8000}"
BASE="http://127.0.0.1:${API_PORT}"

# Whatever this script stops, the trap starts again, failures included.
restore() {
  local rc=$?
  docker compose start postgres redis >/dev/null 2>&1 || echo "WARN: could not restart postgres/redis" >&2
  exit "$rc"
}
trap restore EXIT

docker compose up -d --build
wait_ready

body=""; code=""
# get PATH -> sets $code and $body. --max-time: a hung probe is a failure, not a wait.
get() {
  local out
  out=$(curl -s --max-time 10 -w '\n%{http_code}' "${BASE}$1") || fail "GET $1 did not answer within 10s"
  code=$(printf '%s' "$out" | tail -n1)
  body=$(printf '%s' "$out" | sed '$d')
  [ "$code" != "500" ] || fail "GET $1 returned 500: $body"
}

# expect PATH CODE JSON_FRAGMENT
expect() {
  get "$1"
  [ "$code" = "$2" ] || fail "GET $1: expected HTTP $2, got $code ($body)"
  printf '%s' "$body" | grep -qF "$3" || fail "GET $1: expected $3 in: $body"
}

# wait_for_ok: after restarting a dependency, /health/ready must go back to ok on its own.
wait_for_ok() {
  local deadline=$((SECONDS + 60))
  while :; do
    get /health/ready
    if [ "$code" = "200" ] && printf '%s' "$body" | grep -qF '"status":"ok"'; then return 0; fi
    [ $SECONDS -lt $deadline ] || fail "/health/ready did not recover to ok: $code $body"
    sleep 1
  done
}

echo "== all up"
wait_for_ok
expect /health/live 200 '"status":"ok"'
expect /health/providers 200 '"status":"no_feeds"'
expect /health/data 200 '"status":"no_runs"'

echo "== redis killed -> degraded, not 500"
docker compose stop redis
expect /health/ready 200 '"status":"degraded"'
expect /health/ready 200 '"redis":{"status":"down"'
expect /health/live 200 '"status":"ok"'
expect /health/providers 200 '"status":"no_feeds"'   # Postgres-only: unaffected
expect /health/data 200 '"status":"no_runs"'

echo "== redis back -> recovers"
docker compose start redis
wait_for_ok

echo "== postgres killed -> not_ready 503, not 500"
docker compose stop postgres
expect /health/ready 503 '"status":"not_ready"'
expect /health/ready 503 '"postgres":{"status":"down"'
expect /health/live 200 '"status":"ok"'
expect /health/providers 503 '"status":"unavailable"'
expect /health/data 503 '"status":"unavailable"'

echo "== postgres back -> recovers"
docker compose start postgres
wait_for_ok
expect /health/providers 200 '"status":"no_feeds"'

echo "OK: slice 6 smoke passed (redis down = degraded 200, postgres down = not_ready 503, no 500s, live always 200, both recover)"
