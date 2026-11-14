#!/usr/bin/env bash
# M2 slice 5 smoke: FRED failures through the REAL worker, and the credential-disclosure invariant.
#
# providers_health.last_error stores `<kind>:<Class>` and never the error text, because FRED's api_key
# rides in the request URL and that column is served over HTTP by /health/providers. This runs one FRED
# call for each of 500, 429 and 423 against a local stub that answers that status and echoes the whole
# request URL (key included) in its body, through FredClient -> run_feed -> the two ledgers, inside the
# worker container (real image, real Redis, real Postgres). Then it asserts:
#   - the stub really echoes the key (positive control: otherwise "absent" proves nothing)
#   - each status maps to its kind and is retried (2 attempts = 2 requests)
#   - the exception text, providers_health.last_error, ingestion_runs.error, /health/providers and
#     /health/data contain the canary key nowhere; last_error is exactly `<kind>:<Class>`
#   - /health/ready stays ok (a source failing is not the machinery stopping)
# It is NOT the poll: the poll needs a captured baseline per series. It uses feeds named SMOKE_<status>
# under provider `fred`, removed afterwards; no real series is touched. The canary key is not a real key.
# Run from the repo root (needs a .env and a running stack).
set -euo pipefail

set -a; . ./.env; set +a
. "$(dirname "$0")/lib.sh"
API_PORT="${API_PORT:-8000}"
BASE="http://127.0.0.1:${API_PORT}"
KEY="zzcanary0123456789abcdefghijkl12"

psql_db() { docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA "$@" | tr -d '\r'; }
one() { psql_db -c "$1"; }
cleanup() {
  local rc=$?
  one "DELETE FROM ingestion_runs WHERE feed LIKE 'SMOKE_%'; DELETE FROM providers_health WHERE feed LIKE 'SMOKE_%'; DELETE FROM task_runs WHERE task_name = 'smoke.fred_failures'" >/dev/null 2>&1 || true
  exit "$rc"
}
trap cleanup EXIT

docker compose up -d >/dev/null
wait_ready
cleanup_first=$(one "SELECT count(*) FROM ingestion_runs WHERE feed LIKE 'SMOKE_%'")
[ "$cleanup_first" = "0" ] || fail "leftover SMOKE_ rows from an earlier run"

echo "== running the probe in the worker container"
out=$(docker compose exec -T worker python - < infra/smoke-m2-slice5-fred-failures.py | tr -d '\r') \
  || fail "the probe failed"
printf "%s
" "$out" | grep "^{"

py() { printf '%s\n' "$out" | python -c "import json,sys; rows=[json.loads(l) for l in sys.stdin if l.startswith('{')]; $1"; }

[ "$(py "print(rows[0]['control_echoes_key'])")" = "True" ] || fail "positive control: the stub did not echo the key"
[ "$(py "print(len(rows))")" = "4" ] || fail "expected the control and three statuses"

check_status() { # status kind
  local s=$1 kind=$2
  [ "$(py "print([r['kind'] for r in rows if r.get('status')==$s][0])")" = "$kind" ] || fail "$s did not map to $kind"
  [ "$(py "print([r['hits'] for r in rows if r.get('status')==$s][0])")" = "2" ] || fail "$s: expected 2 requests (1 try + 1 retry)"
  py "t=[r['text'] for r in rows if r.get('status')==$s][0]; sys.exit(1 if '$KEY' in t or 'api_key' in t or 'http' in t else 0)" \
    || fail "$s: the exception text carries the key, an api_key parameter or a URL"
  local cls le re
  cls=$(py "print([r['cls'] for r in rows if r.get('status')==$s][0])")
  le=$(one "SELECT last_error FROM providers_health WHERE provider='fred' AND feed='SMOKE_$s'")
  [ "$le" = "$kind:$cls" ] || fail "$s: providers_health.last_error is '$le', expected '$kind:$cls'"
  re=$(one "SELECT status || ' ' || coalesce(error,'') FROM ingestion_runs WHERE provider='fred' AND feed='SMOKE_$s'")
  case "$re" in "failed $kind:$cls"*) ;; *) fail "$s: ingestion_runs row is '$re'";; esac
  case "$re" in *"$KEY"*) fail "$s: ingestion_runs.error carries the key";; esac
  [ "$(one "SELECT count(*) FROM ingestion_runs WHERE feed='SMOKE_$s'")" = "1" ] || fail "$s: not exactly one ingestion_runs row"
  echo "ok $s -> $kind:$cls, retried, key absent from text, last_error and ingestion_runs.error"
}
check_status 500 transient
check_status 423 transient
check_status 429 rate_limited

echo "== what the API serves"
for path in /health/providers /health/data; do
  body=$(curl -s --max-time 10 -w '\n%{http_code}' "${BASE}${path}")
  code=$(printf '%s' "$body" | tail -n1); body=$(printf '%s' "$body" | sed '$d')
  [ "$code" = "200" ] || fail "GET $path returned $code"
  case "$body" in *"$KEY"*) fail "GET $path serves the canary key";; esac
  if [ "$path" = "/health/providers" ]; then
    case "$body" in *"SMOKE_500"*) ;; *) fail "/health/providers does not list the SMOKE feeds: the check would pass on an empty answer";; esac
  fi
done
code=$(curl -s -o /dev/null -w '%{http_code}' "${BASE}/health/ready")
[ "$code" = "200" ] || fail "/health/ready returned $code with a failing source"
echo "PASS: FRED 500, 423 and 429 through the real worker; the api_key appears in none of the stored or served text"
