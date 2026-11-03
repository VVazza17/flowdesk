#!/usr/bin/env bash
# M1 slice 7 smoke test: the private and public compose profiles against the real stack. Public:
# only api-public runs, loopback only, writes get 405, docs are gone, raw error text is redacted.
# Private: the same rows are visible and docs stay on. Both at once fails rather than serving both.
# The route invariant itself is tested in backend/tests/test_public_profile.py and enforced at
# startup by create_app. Run from the repo root (needs a .env; see .env.example).
set -euo pipefail

set -a; . ./.env; set +a
. "$(dirname "$0")/lib.sh"
API_PORT="${API_PORT:-8000}"
BASE="http://127.0.0.1:${API_PORT}"
SENTINEL="SMOKE7-raw-provider-text"
HEALTH_PATHS="/health/live /health/ready /health/providers /health/data"

# Inline per command rather than exported, so each line says which profile it is talking to.
dc_private() { COMPOSE_PROFILES=private docker compose "$@"; }
dc_public() { COMPOSE_PROFILES=public docker compose "$@"; }
dc_both() { COMPOSE_PROFILES=private,public docker compose "$@"; }
psql_db() { docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -v ON_ERROR_STOP=1 "$@"; }
remove_rows() {
  psql_db -c "DELETE FROM providers_health WHERE provider='smoke7'" \
          -c "DELETE FROM ingestion_runs WHERE provider='smoke7'" >/dev/null
}

# Remember the stack as found. The trap puts it back on every exit, failures included.
private_was_up=0; public_was_up=0
[ -n "$(dc_both ps -q --status running api)" ] && private_was_up=1
[ -n "$(dc_both ps -q --status running api-public)" ] && public_was_up=1
restore() {
  local rc=$?
  remove_rows 2>/dev/null || echo "WARN: could not delete the smoke7 rows" >&2
  dc_both rm -sf api api-public >/dev/null 2>&1 || true
  if [ "$private_was_up" = 1 ]; then
    dc_private up -d api >/dev/null 2>&1 || echo "WARN: could not restart api" >&2
  elif [ "$public_was_up" = 1 ]; then
    dc_public up -d api-public >/dev/null 2>&1 || echo "WARN: could not restart api-public" >&2
  fi
  exit "$rc"
}
trap restore EXIT

http_code() { curl -s -o /dev/null --max-time 10 -w '%{http_code}' "$@" || true; }

echo "== private profile"
dc_both rm -sf api api-public >/dev/null 2>&1 || true
dc_private up -d --build
COMPOSE_PROFILES=private wait_ready

# Rows only M2 would otherwise write, carrying text that must not cross the public boundary.
psql_db \
  -c "INSERT INTO providers_health (provider, feed, last_error_at, last_error) VALUES ('smoke7', 'f', now(), '$SENTINEL')" \
  -c "INSERT INTO ingestion_runs (provider, feed, started_at, status, error) VALUES ('smoke7', 'f', now(), 'failed', '$SENTINEL')" \
  >/dev/null

for path in /health/providers /health/data; do
  curl -s --max-time 10 "$BASE$path" | grep -qF "$SENTINEL" \
    || fail "private $path should show the raw error text (redaction is public-only)"
done
for path in /docs /openapi.json; do
  [ "$(http_code "$BASE$path")" = "200" ] || fail "private $path should be 200"
done

echo "== public profile"
dc_private rm -sf api
dc_public up -d --build
COMPOSE_PROFILES=public wait_ready

[ -z "$(dc_both ps -q --status running api)" ] || fail "private api is running under the public profile"

api_bind=$(dc_public port api-public 8000)
case "$api_bind" in 127.0.0.1:*) ;; *) fail "api-public bound to '$api_bind', expected loopback" ;; esac
pg_bind=$(dc_public port postgres 5432)
case "$pg_bind" in 127.0.0.1:*) ;; *) fail "postgres bound to '$pg_bind', expected loopback" ;; esac
redis_bind=$(dc_public port redis 6379 2>/dev/null || true)
case "$redis_bind" in ""|":0") ;; *) fail "redis is published at '$redis_bind'" ;; esac

for path in $HEALTH_PATHS; do
  [ "$(http_code "$BASE$path")" = "200" ] || fail "public GET $path should be 200"
  for method in POST PUT PATCH DELETE; do
    code=$(http_code -X "$method" "$BASE$path")
    [ "$code" = "405" ] || fail "public $method $path returned $code, expected 405"
  done
done

for path in /docs /redoc /openapi.json /docs/oauth2-redirect; do
  code=$(http_code "$BASE$path")
  [ "$code" = "404" ] || fail "public $path returned $code, expected 404"
done

providers=$(curl -s --max-time 10 "$BASE/health/providers")
data=$(curl -s --max-time 10 "$BASE/health/data")
for body in "$providers" "$data"; do
  case "$body" in *"$SENTINEL"*) fail "raw error text leaked on the public profile: $body" ;; esac
  # Guards the checks above: the rows must really be in the response for their absence to mean much.
  case "$body" in *smoke7*) ;; *) fail "smoke7 row missing from public body, redaction not exercised: $body" ;; esac
done
case "$providers" in *'"last_error":'*) fail "public feed still has a last_error key: $providers" ;; esac
case "$providers" in *'"last_attempt_failed":true'*) ;; *) fail "public body hides that the feed failed: $providers" ;; esac
case "$data" in *'"error":'*) fail "public ingestion row still has an error key: $data" ;; esac

echo "== both profiles at once must not serve both"
if dc_both up -d >/dev/null 2>&1; then
  fail "enabling both profiles succeeded; expected the shared host port to refuse it"
fi
[ -z "$(dc_both ps -q --status running api)" ] || fail "private api came up alongside api-public"

echo "OK: slice 7 smoke passed (public: api-public only, loopback, writes 405, docs 404, errors redacted; private: unredacted, docs on; both refused)"
