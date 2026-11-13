#!/usr/bin/env bash
# M2 slice 5, step 0 smoke test: the canary cannot queue behind ingest work, and the ingest worker is
# still watched. Two workers on two queues (docker-compose.yml): `worker-ops` runs ops.canary alone,
# `worker` runs everything else, including ops.canary_ingest.
#
#   1. routing: the ingest worker never receives ops.canary; the ops worker never receives anything
#      from the ingest queue; both canaries are completing
#   2. saturation (the bug this step exists to fix): two slow ingest tasks hold BOTH ingest slots for
#      longer than the stale window. /health/ready stays `ok` throughout, checks.worker stays ok, the
#      ingest canary's age is seen to exceed the threshold, and checks.ingest_worker is seen `busy` (never
#      `stale`), so the test is not vacuous.
#      Before this step, with one worker and one queue, this exact sequence degraded /health/ready.
#   3. a wedged ingest worker (paused) -> checks.ingest_worker stale -> degraded, ops worker still ok
#      (machinery stopping must page even though every feed would only read stale; the readiness rule)
#   4. a wedged ops worker (paused) -> checks.worker stale -> degraded, ingest worker still ok
#
# `--baseline` runs ONLY the saturation scenario (2) against the pre-fix compose (one worker, one queue,
# no worker-ops), asserting the same thing: /health/ready stays ok and the canary keeps completing. It
# is how the fix is shown to cure a bug someone demonstrated: that run is EXPECTED TO FAIL on the old
# stack, and to pass on the new one. Only the cause of the failure matters: it must be the canary going
# stale under saturation, not a missing service.
#
# Since part C (step 3) the ingest canary's threshold is the same three intervals as the ops worker's, and
# a starved canary behind a live task reads `busy` (a running task of the ingest worker with a fresh
# heartbeat), not `ok`: scenario 2 asserts exactly that.
#
# CANARY_INTERVAL_SECONDS=3 (both canaries stale after 9 s), INGEST_TASK_MAX_SECONDS=40 (only the hard task
# limit now), slow fake calls last 20 s. The fake's Beat schedule is pushed out to an hour: the two slow runs are
# sent by hand, so exactly two slots fill and there is no race over which task gets a freed slot.
# Run from the repo root (needs a .env; see .env.example).
set -euo pipefail

set -a; . ./.env; set +a
. "$(dirname "$0")/lib.sh"
API_PORT="${API_PORT:-8000}"
BASELINE=0
[ "${1:-}" = "--baseline" ] && BASELINE=1
BASE="http://127.0.0.1:${API_PORT}"
OPS_STALE=9

psql_db() { docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA "$@" | tr -d '\r'; }
one() { psql_db -c "$1"; }

restore() {
  local rc=$?
  docker compose unpause worker >/dev/null 2>&1 || true
  docker compose unpause worker-ops >/dev/null 2>&1 || true
  unset CANARY_INTERVAL_SECONDS INGEST_TASK_MAX_SECONDS FAKE_PROVIDER_ENABLED FAKE_PROVIDER_MODE \
    FAKE_PROVIDER_SLOW_SECONDS FAKE_PROVIDER_INTERVAL_SECONDS
  # Recreate FIRST: a still-running slow fake task would write another row after the delete below.
  # worker-ops does not exist in the pre-fix compose, and an unknown service fails the whole command.
  local svcs="worker beat api"
  docker compose config --services | tr -d '\r' | grep -qx worker-ops && svcs="worker worker-ops beat api"
  # shellcheck disable=SC2086
  docker compose up -d --force-recreate $svcs >/dev/null 2>&1 \
    || echo "WARN: could not restore the stack" >&2
  one "DELETE FROM ingestion_runs WHERE provider = 'fake'; DELETE FROM providers_health WHERE provider = 'fake'" >/dev/null 2>&1 || true
  docker compose exec -T redis redis-cli del fake:canonical >/dev/null 2>&1 || true
  exit "$rc"
}
trap restore EXIT

export CANARY_INTERVAL_SECONDS=3 INGEST_TASK_MAX_SECONDS=40
export FAKE_PROVIDER_ENABLED=true FAKE_PROVIDER_MODE=slow FAKE_PROVIDER_SLOW_SECONDS=20
export FAKE_PROVIDER_INTERVAL_SECONDS=3600
docker compose up -d --build
wait_ready

if [ "$BASELINE" = 0 ]; then
  docker compose config --services | tr -d '\r' | grep -qx 'worker-ops' || fail "compose has no worker-ops service"
fi

body=""; code=""
get() { # sets $code and $body; any 500 is a failure
  local out
  out=$(curl -s --max-time 10 -w '\n%{http_code}' "${BASE}$1") || fail "GET $1 did not answer within 10s"
  code=$(printf '%s' "$out" | tail -n1)
  body=$(printf '%s' "$out" | sed '$d')
  [ "$code" != "500" ] || fail "GET $1 returned 500: $body"
}

# Each check is one flat object. `"worker":{` cannot match inside `"ingest_worker":{` (the quote
# before the name), so the two sed patterns are independent.
check_field() { # check_field NAME FIELD  (string or number value)
  printf '%s' "$body" | sed -n "s/.*\"$1\":{[^}]*\"$2\":\"\{0,1\}\([A-Za-z0-9_]*\).*/\1/p"
}
ops_status() { check_field worker status; }
ingest_status() { check_field ingest_worker status; }
top_status() { printf '%s' "$body" | sed -n 's/^{"status":"\([a-z_]*\)".*/\1/p'; }

wait_ready_ok() { # wait_ready_ok [timeout]: both workers read ok and so does the verdict
  local limit=${1:-120} deadline=$((SECONDS + ${1:-120}))
  while :; do
    get /health/ready
    if [ "$(top_status)" = "ok" ] && [ "$(ops_status)" = "ok" ] \
      && { [ "$BASELINE" = 1 ] || [ "$(ingest_status)" = "ok" ]; }; then return 0; fi
    [ $SECONDS -lt $deadline ] || fail "never reached ok on both workers within ${limit}s: $body"
    sleep 2
  done
}

wait_check() { # wait_check ops|ingest STATE [timeout]
  local which=$1 want=$2 limit=${3:-60} deadline=$((SECONDS + ${3:-60})) got
  while :; do
    get /health/ready
    if [ "$which" = ops ]; then got=$(ops_status); else got=$(ingest_status); fi
    [ "$got" = "$want" ] && return 0
    [ $SECONDS -lt $deadline ] || fail "$which worker never reached '$want' within ${limit}s (last '$got': $body)"
    sleep 1
  done
}

echo "== precondition: both canaries completing, /health/ready ok"
wait_ready_ok 180
canaries="ops.canary ops.canary_ingest"; [ "$BASELINE" = 1 ] && canaries="ops.canary"
for t in $canaries; do
  n=$(one "SELECT count(*) FROM task_runs WHERE task_name='$t' AND status='succeeded'")
  [ "$n" -ge 1 ] || fail "no succeeded $t row in task_runs"
done

if [ "$BASELINE" = 0 ]; then
echo "== 1. routing: each worker receives only its own queue's tasks"
# Celery logs "Task <name>[<id>] received" at INFO on the process that took the message.
sleep 8
ingest_log=$(docker compose logs worker 2>&1 | tr -d '\r')
ops_log=$(docker compose logs worker-ops 2>&1 | tr -d '\r')
printf '%s' "$ingest_log" | grep -q 'ops\.canary_ingest\[' || fail "the ingest worker never received ops.canary_ingest"
printf '%s' "$ops_log" | grep -q 'ops\.canary\[' || fail "the ops worker never received ops.canary"
if printf '%s' "$ingest_log" | grep -q 'ops\.canary\['; then fail "the ingest worker received ops.canary: the queues are not separate"; fi
if printf '%s' "$ops_log" | grep -q 'ops\.canary_ingest\['; then fail "the ops worker received ops.canary_ingest"; fi
fi

echo "== 2. saturation: both ingest slots busy for 20 s; the ops canary must not wait"
# Two slow runs, one per slot (--concurrency=2). They are sent by hand: Beat's schedule is an hour.
docker compose exec -T worker celery -A app.worker call ingest.fake >/dev/null
docker compose exec -T worker celery -A app.worker call ingest.fake >/dev/null
deadline=$((SECONDS + 30))
until [ "$(one "SELECT count(*) FROM ingestion_runs WHERE provider='fake' AND status='running'")" -ge 2 ]; do
  [ $SECONDS -lt $deadline ] || fail "two slow fake runs never reached 'running': the ingest slots are not busy"
  sleep 1
done
max_ingest_age=0
saw_busy=0
end=$((SECONDS + 15))
while [ $SECONDS -lt $end ]; do
  get /health/ready
  [ "$(top_status)" = "ok" ] || fail "/health/ready is '$(top_status)' while the ingest slots are busy: $body"
  [ "$(ops_status)" = "ok" ] || fail "the ops worker read '$(ops_status)' while ingest work ran: the canary queued behind it: $body"
  if [ "$BASELINE" = 0 ]; then
    st=$(ingest_status)
    case "$st" in
      ok) ;;
      busy) saw_busy=1 ;;
      *) fail "the ingest worker read '$st' while its own tasks were running and beating: $body" ;;
    esac
    age=$(check_field ingest_worker age_seconds)
    [ -n "$age" ] && [ "$age" -gt "$max_ingest_age" ] && max_ingest_age=$age
  fi
  sleep 1
done
if [ "$BASELINE" = 0 ]; then
  [ "$max_ingest_age" -gt "$OPS_STALE" ] \
    || fail "the ingest canary's age never exceeded ${OPS_STALE}s (max ${max_ingest_age}s): the slots were not starving it, so this proved nothing"
  [ "$saw_busy" = 1 ] \
    || fail "checks.ingest_worker never read 'busy' while the canary was starved: part C's liveness did not act"
  echo "   ingest canary starved up to ${max_ingest_age}s (past the threshold of ${OPS_STALE}s); the worker read busy, ready stayed ok"
fi
deadline=$((SECONDS + 60))
until [ "$(one "SELECT count(*) FROM ingestion_runs WHERE provider='fake' AND status='succeeded'")" -ge 2 ]; do
  [ $SECONDS -lt $deadline ] || fail "the slow fake runs never finished"
  sleep 1
done
wait_ready_ok 60

if [ "$BASELINE" = 1 ]; then
  wait_ready_ok 60
  echo "BASELINE PASSED: /health/ready stayed ok under saturation (expected to fail on the pre-fix stack)"
  exit 0
fi

echo "== 3. ingest worker wedged (paused) -> its canary goes stale -> degraded; the ops worker is fine"
docker compose pause worker
wait_check ingest stale 120
get /health/ready
[ "$code" = "200" ] || fail "/health/ready is $code with a wedged ingest worker, expected 200"
[ "$(top_status)" = "degraded" ] || fail "a wedged ingest worker must degrade /health/ready (machinery), got '$(top_status)': $body"
[ "$(ops_status)" = "ok" ] || fail "the ops worker should read ok while only the ingest worker is wedged: $body"
docker compose unpause worker
wait_ready_ok 120

echo "== 4. ops worker wedged (paused) -> its canary goes stale -> degraded; the ingest worker is fine"
docker compose pause worker-ops
wait_check ops stale 60
get /health/ready
[ "$(top_status)" = "degraded" ] || fail "a wedged ops worker must degrade /health/ready, got '$(top_status)': $body"
[ "$(ingest_status)" = "ok" ] || fail "the ingest worker should read ok while only the ops worker is wedged: $body"
docker compose unpause worker-ops
wait_ready_ok 60

get /health/live; [ "$code" = "200" ] || fail "/health/live is $code"
echo "OK: M2 slice 5 step 0 smoke passed (queues are separate; ingest saturation never delays the ops canary or degrades ready; a wedged ingest worker and a wedged ops worker each degrade it)"
