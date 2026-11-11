#!/usr/bin/env bash
# M2 slice 4 smoke test (IDEA-032, killing a provider): the fake provider is run through the real
# stack (Beat, Redis, the worker, Postgres, the API) and "killed", and the documented states in
# docs/HEALTH.md appear. An external API cannot be killed, so "killed" is defined there: every call to
# it ends in failure after retries. Here that is FAKE_PROVIDER_MODE=down.
#
#   1. healthy: runs succeed; the first writes rows, every later identical run writes 0 (rows_written is
#      the idempotency evidence); one ingestion_runs row per run
#   2. killed: runs fail, providers_health keeps the last success and records a kind+class (never the
#      error text), the feed goes `stale` past its own threshold, /health/providers is `stale` with HTTP
#      200, and /health/ready STAYS `ok` (the readiness rule: machinery, not sources)
#   3. recovered: the next success returns the feed to `fresh`
#   4. a registered feed that never runs (Beat stopped) goes `stale` with never_succeeded=true
#
# Runs with a 5 s fake interval, so the feed's threshold is 15 s. The fake rows are removed afterwards.
# Run from the repo root (needs a .env; see .env.example).
set -euo pipefail

set -a; . ./.env; set +a
. "$(dirname "$0")/lib.sh"
API_PORT="${API_PORT:-8000}"
BASE="http://127.0.0.1:${API_PORT}"
INTERVAL=5
THRESHOLD=$((INTERVAL * 3))

psql_db() { docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA "$@" | tr -d '\r'; }
one() { psql_db -c "$1"; }

restore() {
  local rc=$?
  unset FAKE_PROVIDER_ENABLED FAKE_PROVIDER_MODE FAKE_PROVIDER_INTERVAL_SECONDS INGEST_RETRY_INITIAL_SECONDS
  # Recreate FIRST: a still-running fake worker or Beat would write another row after the delete below.
  docker compose up -d --force-recreate worker beat api >/dev/null 2>&1 \
    || echo "WARN: could not restore the stack" >&2
  one "DELETE FROM ingestion_runs WHERE provider = 'fake'; DELETE FROM providers_health WHERE provider = 'fake'" >/dev/null 2>&1 || true
  docker compose exec -T redis redis-cli del fake:canonical >/dev/null 2>&1 || true
  exit "$rc"
}
trap restore EXIT

export FAKE_PROVIDER_ENABLED=true FAKE_PROVIDER_MODE=ok FAKE_PROVIDER_INTERVAL_SECONDS=$INTERVAL
export INGEST_RETRY_INITIAL_SECONDS=0.2
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

# jget <python expression over j>: evaluated in the api container against the last $body.
jget() { printf '%s' "$body" | docker compose exec -T api python -c "import json,sys; j=json.load(sys.stdin); print($1)" | tr -d '\r'; }
feed_status() { jget "[f['status'] for f in j['feeds'] if f['feed']=='ticks'][0]"; }

wait_feed() { # wait_feed STATE [timeout]
  local want=$1 limit=${2:-60} deadline got
  deadline=$((SECONDS + limit))
  while :; do
    get /health/providers
    got=$(feed_status 2>/dev/null || true)
    [ "$got" = "$want" ] && return 0
    [ $SECONDS -lt $deadline ] || fail "feed never reached '$want' within ${limit}s (last: '$got', $code $body)"
    sleep 1
  done
}

# Precondition: the machinery is healthy BEFORE anything is killed. A canary left stale by whatever ran
# before this script (smoke 6 stops Redis and Postgres) would otherwise read as the stale feed degrading
# /health/ready, which is the one thing step 2 asserts it does not do.
deadline=$((SECONDS + 240))
until get /health/ready; printf '%s' "$body" | grep -q '^{"status":"ok"'; do
  [ $SECONDS -lt $deadline ] || fail "/health/ready never reached ok before the test began: $body"
  sleep 3
done

echo "== 1. healthy: runs succeed, one row per run, identical re-ingests write nothing"
wait_feed fresh 90
deadline=$((SECONDS + 60))
until [ "$(one "SELECT count(*) FROM ingestion_runs WHERE provider='fake' AND status='succeeded'")" -ge 3 ]; do
  [ $SECONDS -lt $deadline ] || fail "fewer than 3 successful fake runs after 60s"
  sleep 1
done
written=$(one "SELECT count(*) FROM ingestion_runs WHERE provider='fake' AND status='succeeded' AND rows_written > 0")
[ "$written" -le 1 ] || fail "$written runs wrote rows: an identical re-ingest must write 0 (rows_written)"
zero=$(one "SELECT count(*) FROM ingestion_runs WHERE provider='fake' AND status='succeeded' AND rows_read = 5 AND rows_written = 0")
[ "$zero" -ge 1 ] || fail "no run read 5 rows and wrote 0: rows_written is not showing the re-ingest"
get /health/ready; [ "$code" = "200" ] || fail "ready is $code while healthy"

echo "== 2. killed: every call fails after retries"
export FAKE_PROVIDER_MODE=down
docker compose up -d worker >/dev/null
deadline=$((SECONDS + 60))
until [ "$(one "SELECT count(*) FROM ingestion_runs WHERE provider='fake' AND status='failed'")" -ge 1 ]; do
  [ $SECONDS -lt $deadline ] || fail "no failed fake run after 60s of FAKE_PROVIDER_MODE=down"
  sleep 1
done
le=$(one "SELECT last_error FROM providers_health WHERE provider='fake'")
[ "$le" = "transient:Transient" ] || fail "providers_health.last_error is '$le', expected the kind and class only"
[ -n "$(one "SELECT last_success_at FROM providers_health WHERE provider='fake'")" ] \
  || fail "a failure erased last_success_at"
err=$(one "SELECT error FROM ingestion_runs WHERE provider='fake' AND status='failed' ORDER BY id DESC LIMIT 1")
case "$err" in transient:Transient:*) ;; *) fail "ingestion_runs.error is '$err'" ;; esac
[ "$(one "SELECT count(*) FROM ingestion_runs WHERE provider='fake' AND status='running' AND started_at < now() - interval '30 seconds'")" = "0" ] \
  || fail "a fake run is stuck in 'running'"

wait_feed stale $((THRESHOLD + 60))
[ "$code" = "200" ] || fail "/health/providers is $code with a stale feed, expected 200"
top=$(jget "j['status']"); [ "$top" = "stale" ] || fail "/health/providers status is '$top', expected stale"
get /health/ready
[ "$code" = "200" ] || fail "/health/ready is $code with a stale feed"
ready=$(printf '%s' "$body" | sed -n 's/^{"status":"\([a-z_]*\)".*/\1/p')
if [ "$ready" != "ok" ]; then
  echo "--- diagnostics: queue length, canary history, worker log tail" >&2
  docker compose exec -T redis redis-cli llen celery >&2 || true
  one "SELECT started_at, completed_at, status FROM task_runs WHERE task_name='ops.canary' ORDER BY id DESC LIMIT 6" >&2 || true
  docker compose logs --tail 25 worker >&2 || true
fi
[ "$ready" = "ok" ] || fail "/health/ready is '$ready': a stale feed must not degrade it (HEALTH.md, the readiness rule): $body"

echo "== 3. recovered: the next success makes the feed fresh again"
export FAKE_PROVIDER_MODE=ok
docker compose up -d worker >/dev/null
wait_feed fresh 90
[ "$(jget "[f['last_attempt_failed'] for f in j['feeds'] if f['feed']=='ticks'][0]")" = "False" ] \
  || fail "last_attempt_failed is still true after a success"

echo "== 4. a registered feed that never runs goes stale, flagged never_succeeded"
docker compose stop beat >/dev/null
one "DELETE FROM ingestion_runs WHERE provider='fake'; DELETE FROM providers_health WHERE provider='fake'" >/dev/null
docker compose run --rm -T migrate >/dev/null 2>&1 || fail "re-running migrate (and the seed) failed"
[ "$(one "SELECT count(*) FROM providers_health WHERE provider='fake' AND last_success_at IS NULL AND first_seen_at IS NOT NULL")" = "1" ] \
  || fail "the seed did not register the declared feed"
get /health/providers
[ "$(feed_status)" = "never_succeeded" ] || fail "a just-registered feed should read never_succeeded: $body"
wait_feed stale $((THRESHOLD + 30))
[ "$(jget "[f['never_succeeded'] for f in j['feeds'] if f['feed']=='ticks'][0]")" = "True" ] \
  || fail "the escalated feed is not flagged never_succeeded"
docker compose start beat >/dev/null

echo "OK: M2 slice 4 smoke passed (killed provider: failed runs, kind+class only, stale after its threshold, ready stays ok, recovers; unrun feed escalates)"
