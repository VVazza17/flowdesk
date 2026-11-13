#!/usr/bin/env bash
# M2 slice 5, step 3, part C smoke: a REAL death leaves a row the sweep can find.
#
# The unit tests plant rows and so test the SQL. Only a hard kill tests that a process which dies
# mid-task really leaves what the sweep looks for. It must be SIGKILL: a graceful SIGTERM does not
# do, because Celery's warm shutdown lets the task finish and run_recorded's `finally` then writes a
# status, so there is nothing to sweep. This script takes about five minutes by construction (a
# heartbeat is fresh for 120 s, and Beat sweeps every 120 s), and the time cannot be shortened
# without making the freshness window an environment variable, which would make it tunable in
# production for the sake of a test.
#
#   1. a slow fake ingest task runs in the ingest worker; its heartbeat_at is seen to ADVANCE (the
#      thread is live in the real process, not just in a unit test) and its ingestion run is linked
#      to its task_runs row (task_run_id)
#   2. `docker kill -s KILL worker` mid-task. Both rows are still `running`: nothing ran a `finally`
#   3. readiness: while the dead worker's last beat is still fresh the check reads `busy` (the cost
#      of the design, stated in docs/HEALTH.md), and once it goes stale the check reads `stale` and
#      /health/ready degrades, minutes after the death and not half an hour
#   4. Beat's sweep (worker-ops, the ops queue) fails BOTH rows: task_runs error_class 'Orphaned',
#      ingestion_runs error 'Orphaned...', and not before the beat was 120 s old
#   5. the swept rows are not resurrected when the worker comes back; the sweep has its own ledger row
#
# CANARY_INTERVAL_SECONDS=3 so the readiness states move quickly. INGEST_TASK_MAX_SECONDS is left at
# its default: the slow task must be killed by this script, not by Celery's hard time limit.
# Run from the repo root (needs a .env; see .env.example).
set -euo pipefail

set -a; . ./.env; set +a
. "$(dirname "$0")/lib.sh"
API_PORT="${API_PORT:-8000}"
BASE="http://127.0.0.1:${API_PORT}"
FRESH=120   # app/tasks/liveness.py: HEARTBEAT_FRESH_SECONDS
BEAT=30     # app/tasks/liveness.py: HEARTBEAT_INTERVAL_SECONDS

psql_db() { docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA "$@" | tr -d '\r'; }
one() { psql_db -c "$1"; }

restore() {
  local rc=$?
  unset CANARY_INTERVAL_SECONDS FAKE_PROVIDER_ENABLED FAKE_PROVIDER_MODE FAKE_PROVIDER_SLOW_SECONDS \
    FAKE_PROVIDER_INTERVAL_SECONDS
  # Recreate FIRST so that nothing is still writing fake rows when they are deleted below.
  docker compose up -d --force-recreate worker worker-ops beat api >/dev/null 2>&1 \
    || echo "WARN: could not restore the stack" >&2
  one "DELETE FROM ingestion_runs WHERE provider = 'fake'; DELETE FROM providers_health WHERE provider = 'fake'; DELETE FROM task_runs WHERE task_name = 'ingest.fake'" >/dev/null 2>&1 || true
  docker compose exec -T redis redis-cli del fake:canonical >/dev/null 2>&1 || true
  exit "$rc"
}
trap restore EXIT

export CANARY_INTERVAL_SECONDS=3
export FAKE_PROVIDER_ENABLED=true FAKE_PROVIDER_MODE=slow FAKE_PROVIDER_SLOW_SECONDS=900
export FAKE_PROVIDER_INTERVAL_SECONDS=3600
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
check_field() { # check_field NAME FIELD
  printf '%s' "$body" | sed -n "s/.*\"$1\":{[^}]*\"$2\":\"\{0,1\}\([A-Za-z0-9_]*\).*/\1/p"
}
ingest_status() { check_field ingest_worker status; }
ops_status() { check_field worker status; }
top_status() { printf '%s' "$body" | sed -n 's/^{"status":"\([a-z_]*\)".*/\1/p'; }

echo "== precondition: both canaries completing, /health/ready ok"
deadline=$((SECONDS + 180))
while :; do
  get /health/ready
  [ "$(top_status)" = "ok" ] && [ "$(ops_status)" = "ok" ] && [ "$(ingest_status)" = "ok" ] && break
  [ $SECONDS -lt $deadline ] || fail "never reached ok within 180s: $body"
  sleep 2
done

echo "== 1. a slow ingest task runs; its heartbeat advances; its ingestion run is linked to it"
one "DELETE FROM ingestion_runs WHERE provider = 'fake'; DELETE FROM task_runs WHERE task_name = 'ingest.fake'" >/dev/null
docker compose exec -T worker celery -A app.worker call ingest.fake >/dev/null
deadline=$((SECONDS + 40))
until [ "$(one "SELECT count(*) FROM ingestion_runs WHERE provider='fake' AND status='running' AND task_run_id IS NOT NULL")" -ge 1 ]; do
  [ $SECONDS -lt $deadline ] || fail "the slow fake run never reached 'running' with a task_run_id"
  sleep 1
done
TASK_ID=$(one "SELECT task_run_id FROM ingestion_runs WHERE provider='fake' AND status='running' LIMIT 1")
[ "$(one "SELECT task_name || ' ' || status FROM task_runs WHERE id = $TASK_ID")" = "ingest.fake running" ] \
  || fail "the ingestion run's task_run_id ($TASK_ID) is not a running ingest.fake row"
deadline=$((SECONDS + BEAT * 2 + 15))
until [ "$(one "SELECT floor(extract(epoch FROM heartbeat_at - started_at))::int >= $((BEAT - 5)) FROM task_runs WHERE id = $TASK_ID")" = "t" ]; do
  [ $SECONDS -lt $deadline ] || fail "the heartbeat of task $TASK_ID never advanced past its start: the thread is not live in the real process"
  sleep 3
done
echo "   task $TASK_ID is beating (heartbeat_at advanced $(one "SELECT floor(extract(epoch FROM heartbeat_at - started_at))::int FROM task_runs WHERE id = $TASK_ID") s past its start)"
get /health/ready
[ "$(top_status)" = "ok" ] || fail "/health/ready is '$(top_status)' while a task is healthily running: $body"

echo "== 2. SIGKILL the ingest worker mid-task: nothing runs a finally, both rows stay 'running'"
docker compose kill -s SIGKILL worker >/dev/null
KILLED=$SECONDS
sleep 3
[ "$(one "SELECT status FROM task_runs WHERE id = $TASK_ID")" = "running" ] \
  || fail "the task row is not 'running' after a hard kill: something wrote a status, so this is not a death"
[ "$(one "SELECT status FROM ingestion_runs WHERE task_run_id = $TASK_ID")" = "running" ] \
  || fail "the ingestion run is not 'running' after a hard kill"
LAST_BEAT=$(one "SELECT heartbeat_at FROM task_runs WHERE id = $TASK_ID")

echo "== 3. readiness: busy while the dead worker's last beat is still fresh, then stale and degraded"
saw_busy=0; degraded_at=""
deadline=$((SECONDS + FRESH + 90))
while [ $SECONDS -lt $deadline ]; do
  get /health/ready
  [ "$(ops_status)" = "ok" ] || fail "the ops worker read '$(ops_status)': it was not killed: $body"
  case "$(ingest_status)" in
    busy) saw_busy=1 ;;
    stale)
      [ "$(top_status)" = "degraded" ] || fail "ingest worker stale but /health/ready is '$(top_status)': $body"
      degraded_at=$((SECONDS - KILLED)); break ;;
  esac
  sleep 3
done
[ -n "$degraded_at" ] || fail "a worker killed mid-task never read stale within $((FRESH + 90))s of the kill: $body"
[ "$saw_busy" = 1 ] || fail "the dead worker never read 'busy' while its last beat was fresh; the documented cost did not show"
[ "$degraded_at" -lt 240 ] || fail "detection took ${degraded_at}s; part C promised minutes, not half an hour"
echo "   read busy while the last beat was fresh; stale and degraded ${degraded_at}s after the kill"

echo "== 4. Beat's sweep fails both rows (not before the beat was ${FRESH}s old)"
deadline=$((SECONDS + 330))
until [ "$(one "SELECT status FROM task_runs WHERE id = $TASK_ID")" = "failed" ]; do
  [ $SECONDS -lt $deadline ] || fail "the sweep never failed task $TASK_ID within 330s of the kill"
  sleep 5
done
SWEPT_AFTER=$((SECONDS - KILLED))
[ "$(one "SELECT error_class FROM task_runs WHERE id = $TASK_ID")" = "Orphaned" ] || fail "task row error_class is not 'Orphaned'"
[ "$(one "SELECT status FROM ingestion_runs WHERE task_run_id = $TASK_ID")" = "failed" ] \
  || fail "the linked ingestion run was not failed in the same sweep"
case "$(one "SELECT error FROM ingestion_runs WHERE task_run_id = $TASK_ID")" in Orphaned*) ;; *) fail "ingestion run error does not start with 'Orphaned'" ;; esac
[ "$(one "SELECT finished_at IS NOT NULL FROM ingestion_runs WHERE task_run_id = $TASK_ID")" = "t" ] || fail "ingestion run has no finished_at"
age=$(one "SELECT floor(extract(epoch FROM completed_at - heartbeat_at))::int FROM task_runs WHERE id = $TASK_ID")
[ "$age" -ge "$FRESH" ] || fail "the sweep failed the task when its last beat was only ${age}s old (< ${FRESH}s): a live task could be swept"
echo "   both rows failed 'Orphaned' ${SWEPT_AFTER}s after the kill, when the last beat was ${age}s old"

echo "== 5. the swept rows stay swept when the worker returns; the sweep has its own ledger row"
[ "$(one "SELECT count(*) FROM task_runs WHERE task_name='ops.sweep' AND status='succeeded'")" -ge 1 ] \
  || fail "no succeeded ops.sweep row in task_runs"
docker compose up -d worker >/dev/null
deadline=$((SECONDS + 120))
while :; do
  get /health/ready
  [ "$(top_status)" = "ok" ] && [ "$(ingest_status)" = "ok" ] && break
  [ $SECONDS -lt $deadline ] || fail "never recovered to ok after the worker came back: $body"
  sleep 2
done
[ "$(one "SELECT status FROM task_runs WHERE id = $TASK_ID")" = "failed" ] || fail "the swept task row changed status after the worker returned"

echo "OK: M2 slice 5 step 3 sweep smoke passed (a SIGKILLed worker left running rows; readiness read busy then stale; the sweep failed both rows as Orphaned ${SWEPT_AFTER}s after the kill, after a ${age}s-old beat; recovered)"
