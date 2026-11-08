#!/usr/bin/env bash
# M2 slice 1 smoke test (IDEA-032, killing the worker): the worker's health is judged by whether the
# canary task keeps completing, not by whether a process is up. Each way of stopping it produces the
# documented state in docs/HEALTH.md, never a 500, and each recovers.
#
#   stopped worker    -> stale -> /health/ready degraded
#   paused worker     -> stale -> degraded   (process alive but wedged: the case a liveness check misses)
#   stopped Beat      -> stale -> degraded   (the same single state as the worker)
#   fresh, no canary  -> never_completed, ok inside the grace, degraded after it
#   stopped Redis     -> redis down AND worker stale, both reported
#
# Runs with CANARY_INTERVAL_SECONDS=3 so the stale threshold is 9 s; the stack is put back to the
# default interval afterwards. Run from the repo root (needs a .env; see .env.example).
set -euo pipefail

set -a; . ./.env; set +a
. "$(dirname "$0")/lib.sh"
API_PORT="${API_PORT:-8000}"
BASE="http://127.0.0.1:${API_PORT}"
INTERVAL=3
GRACE=$((INTERVAL * 3))   # the stale window, which is also the startup grace

# Whatever this script stops or pauses, the trap puts back, and it returns the stack to the default
# interval so a 3 s canary does not outlive the test.
restore() {
  local rc=$?
  docker compose unpause worker >/dev/null 2>&1 || true
  unset CANARY_INTERVAL_SECONDS
  docker compose up -d redis postgres worker beat api >/dev/null 2>&1 \
    || echo "WARN: could not restore the stack" >&2
  exit "$rc"
}
trap restore EXIT

export CANARY_INTERVAL_SECONDS=$INTERVAL
docker compose up -d --build
wait_ready

body=""; code=""
get() { # sets $code and $body; any 500 is a failure
  local out
  out=$(curl -s --max-time 10 -w '\n%{http_code}' "${BASE}$1") || fail "GET $1 did not answer within 10s"
  code=$(printf '%s' "$out" | tail -n1)
  body=$(printf '%s' "$out" | sed '$d')
  [ "$code" != "500" ] || fail "GET $1 returned 500: $body"
}

# The worker check is one flat object, so a sed over it is enough and needs no JSON tool on the host.
worker_status() { printf '%s' "$body" | sed -n 's/.*"worker":{[^}]*"status":"\([a-z_]*\)".*/\1/p'; }
worker_field() { printf '%s' "$body" | sed -n "s/.*\"worker\":{[^}]*\"$1\":\([a-z0-9]*\).*/\1/p"; }

# wait_worker STATE [timeout]: poll /health/ready until the worker check reads STATE.
wait_worker() {
  local want=$1 limit=${2:-60} deadline got
  deadline=$((SECONDS + limit))
  while :; do
    get /health/ready
    got=$(worker_status)
    [ "$got" = "$want" ] && return 0
    [ $SECONDS -lt $deadline ] || fail "worker never reached '$want' within ${limit}s (last: '$got', $code $body)"
    sleep 1
  done
}

# expect_verdict CODE STATUS: /health/ready answers with this HTTP code and top-level status, and
# /health/live stays 200 whatever state the worker is in.
expect_verdict() {
  get /health/ready
  [ "$code" = "$1" ] || fail "/health/ready: expected HTTP $1, got $code ($body)"
  printf '%s' "$body" | grep -qF "\"status\":\"$2\",\"checks\"" \
    || fail "/health/ready: expected top-level status $2 in: $body"
  get /health/live
  [ "$code" = "200" ] || fail "/health/live: expected 200, got $code"
}

echo "== all up: the canary completes and the worker reads ok"
wait_worker ok
expect_verdict 200 ok
n=$(docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -c "select count(*) from task_runs where task_name = '"'"'ops.canary'"'"' and status = '"'"'succeeded'"'"'"')
[ "${n//[$'\r\n ']/}" -ge 1 ] || fail "task_runs has no succeeded canary row"
get /health/data
printf '%s' "$body" | grep -qF '"task_name":"ops.canary"' || fail "/health/data does not report the canary: $body"

echo "== worker stopped -> stale, degraded"
docker compose stop worker
wait_worker stale 45
expect_verdict 200 degraded
echo "== worker back -> recovers"
docker compose start worker
wait_worker ok 60
expect_verdict 200 ok

echo "== worker PAUSED (process alive, not working) -> stale, degraded"
docker compose pause worker
# A paused container still has its process (Running=true), frozen (Paused=true): alive, not working.
[ "$(docker inspect -f '{{.State.Running}} {{.State.Paused}}' "$(docker compose ps -q worker)")" = "true true" ] \
  || fail "worker should have a live process but be paused"
wait_worker stale 45
expect_verdict 200 degraded
echo "== worker unpaused -> recovers"
docker compose unpause worker
wait_worker ok 60
expect_verdict 200 ok

echo "== beat stopped -> the same single state as a dead worker"
docker compose stop beat
wait_worker stale 45
expect_verdict 200 degraded
docker compose start beat
wait_worker ok 60
expect_verdict 200 ok

echo "== fresh stack, no canary yet -> never_completed: ok inside the grace, degraded after it"
docker compose stop worker beat
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "truncate task_runs"' >/dev/null
docker compose restart api >/dev/null
wait_ready api
get /health/ready
[ "$(worker_status)" = "never_completed" ] || fail "expected never_completed right after the API started: $body"
[ "$(worker_field in_grace)" = "true" ] || fail "expected in_grace=true right after the API started: $body"
expect_verdict 200 ok
sleep $((GRACE + 2))
get /health/ready
[ "$(worker_status)" = "never_completed" ] || fail "expected never_completed after the grace: $body"
[ "$(worker_field in_grace)" = "false" ] || fail "expected in_grace=false after the grace: $body"
expect_verdict 200 degraded
docker compose start worker beat
wait_worker ok 60
expect_verdict 200 ok

echo "== redis stopped -> redis down AND worker stale, both reported"
docker compose stop redis
wait_worker stale 45
expect_verdict 200 degraded
get /health/ready   # expect_verdict leaves the /health/live answer in $body
printf '%s' "$body" | grep -qF '"redis":{"status":"down"' || fail "redis should read down: $body"
docker compose start redis
wait_worker ok 90
expect_verdict 200 ok

echo "OK: M2 slice 1 smoke passed (worker stopped, paused, beat stopped, fresh start and redis down each give the documented state; no 500s; live always 200; all recover)"
