#!/usr/bin/env bash
# M2 slice 6 step 6 smoke: the two settled bars polls through the REAL worker (real image, Redis,
# Postgres, and real Yahoo), as one manual run each (allowed on the dev stack before IDEA-029 closes;
# Kyle, 2026-11-18). It asserts:
#   - Beat, as configured (BARS_POLL_ENABLED unset or false), schedules neither poll; the worker has
#     both tasks registered (a manual `celery call` works with the flag off)
#   - each poll is one task_runs row (succeeded, the poll's task name) and its ingestion_runs rows
#     (feed bars_daily / bars_15m) are linked to it, and the 15m poll took two rate-budget slots
#     (it ran at least 60 s: its own daily split check, then the 15m batch)
#   - /health/ready stays ok; /health/providers shows both bars feeds fresh afterwards
# and prints each run's `bars_poll_done` line (rows, volume revisions per symbol).
# PREREQUISITE: both backfills on this stack (`python -m app.bars_backfill` and `--interval 15m`);
# otherwise every symbol fails as NotBackfilled, which this smoke reports as a failure.
# It WRITES poll rows into the dev database (insert-only: only dropping the database removes them).
# Run from the repo root, after `docker compose build` if the code changed. About 3 minutes.
set -euo pipefail

set -a; . ./.env; set +a
. "$(dirname "$0")/lib.sh"
API_PORT="${API_PORT:-8000}"
BASE="http://127.0.0.1:${API_PORT}"

psql_db() { docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA "$@" | tr -d '\r'; }
one() { psql_db -c "$1"; }

docker compose up -d >/dev/null
wait_ready

echo "== Beat with the flag as configured (${BARS_POLL_ENABLED:-unset}) schedules no bars poll"
case "${BARS_POLL_ENABLED:-false}" in true|TRUE|True) fail "BARS_POLL_ENABLED is true: IDEA-029 has not closed";; esac
keys=$(docker compose exec -T beat python -c "from app.celery_app import create_celery; print(' '.join(sorted(create_celery().conf.beat_schedule)))" | tr -d '\r')
echo "beat schedule: $keys"
case " $keys " in *" bars_daily_poll "*|*" bars_15m_poll "*) fail "Beat schedules a bars poll with the flag off";; esac
registered=$(docker compose exec -T worker python -c "
from app.celery_app import create_celery
app = create_celery(); app.loader.import_default_modules()
print(' '.join(sorted(t for t in app.tasks if t.startswith('ingest.bars'))))" | tr -d '\r')
[ "$registered" = "ingest.bars_15m_poll ingest.bars_daily_poll" ] || fail "worker registers '$registered'"
echo "ok: neither poll scheduled; both registered"

run_poll() { # task feed min_seconds
  local task=$1 feed=$2 min=$3 before id status start end secs
  before=$(one "SELECT coalesce(max(id), 0) FROM task_runs")
  start=$(date +%s)
  docker compose exec -T worker celery -A app.worker call "$task" >/dev/null
  for _ in $(seq 1 120); do
    id=$(one "SELECT id FROM task_runs WHERE task_name = '$task' AND id > $before ORDER BY id LIMIT 1")
    if [ -n "$id" ]; then
      status=$(one "SELECT status FROM task_runs WHERE id = $id")
      [ "$status" != "running" ] && break
    fi
    sleep 3
  done
  end=$(date +%s); secs=$((end - start))
  [ -n "${id:-}" ] || fail "$task: no task_runs row appeared"
  [ "$status" = "succeeded" ] || {
    one "SELECT status, error FROM ingestion_runs WHERE task_run_id = $id"
    fail "$task: task_runs $id is $status"
  }
  local linked
  linked=$(one "SELECT count(*) FROM ingestion_runs WHERE task_run_id = $id AND feed = '$feed' AND status = 'succeeded'")
  [ "$linked" -ge 1 ] || fail "$task: no succeeded $feed ingestion run linked to task_runs $id"
  [ "$secs" -ge "$min" ] || fail "$task took ${secs}s, expected at least ${min}s"
  echo "ok $task: task_runs $id succeeded in ${secs}s; ingestion runs (id, rows_written):"
  one "SELECT id || ' ' || rows_written FROM ingestion_runs WHERE task_run_id = $id ORDER BY id" | sed 's/^/    /'
  docker compose logs --no-log-prefix --since "${secs}s" worker 2>/dev/null | tr -d '\r' \
    | grep '"bars_poll_done"' | grep "\"interval\": \"$( [ "$feed" = bars_daily ] && echo 1d || echo 15m )\"" | tail -1 | sed 's/^/    /'
}

echo "== the daily poll"
run_poll ingest.bars_daily_poll bars_daily 0
echo "== the 15m poll (its own daily split check first: two rate-budget slots, 60 s apart)"
run_poll ingest.bars_15m_poll bars_15m 55

echo "== health"
ready=$(curl -s --max-time 10 "$BASE/health/ready")
printf '%s' "$ready" | grep -q '"status":"ok"' || fail "/health/ready is not ok: $ready"
providers=$(curl -s --max-time 10 "$BASE/health/providers")
for feed in bars_daily bars_15m; do
  printf '%s' "$providers" | python -c "
import json, sys
body = json.load(sys.stdin)
feeds = body.get('feeds', [])
row = [f for f in feeds if f.get('provider') == 'yfinance' and f.get('feed') == '$feed']
sys.exit(0 if row and row[0].get('status') == 'fresh' else 1)" || fail "/health/providers: yfinance/$feed is not fresh"
done
echo "ok: /health/ready ok; yfinance bars_daily and bars_15m fresh"
echo "PASS smoke-m2-slice6-poll"
