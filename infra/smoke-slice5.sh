#!/usr/bin/env bash
# M1 slice 5 smoke test: one request produces exactly one JSON log line carrying the
# correlation id, the response echoes the id, and the api emits no non-JSON log lines.
# Run from the repo root (needs a .env; see .env.example).
set -euo pipefail

set -a; . ./.env; set +a
. "$(dirname "$0")/lib.sh"
API_PORT="${API_PORT:-8000}"

# Remember the stack as found: api's LOG_LEVEL (from .env or the caller's shell) and whether
# api was running at all. The trap below puts both back on every exit, failures included.
had_level=0; orig_level=""
if [ -n "${LOG_LEVEL+x}" ]; then had_level=1; orig_level="$LOG_LEVEL"; fi
api_was_up=0
[ -n "$(docker compose ps -q --status running api)" ] && api_was_up=1

hdrs=$(mktemp)
restore() {
  local rc=$?
  rm -f "$hdrs"
  if [ "$had_level" = 1 ]; then export LOG_LEVEL="$orig_level"; else unset LOG_LEVEL; fi
  if [ "$api_was_up" = 1 ]; then
    docker compose up -d --force-recreate api >/dev/null 2>&1 \
      || echo "WARN: could not restore api to its original LOG_LEVEL" >&2
  else
    docker compose stop api >/dev/null 2>&1 || true
  fi
  exit "$rc"
}
trap restore EXIT

# Successful /health/* probes log at DEBUG (the Docker healthcheck would otherwise flood the
# log), so run the api at DEBUG for this check. compose passes LOG_LEVEL through.
export LOG_LEVEL=DEBUG
docker compose up -d --build --force-recreate api
wait_ready api

rid="smoke5-$(date +%s)-$$"
code=$(curl -s -o /dev/null -D "$hdrs" -w '%{http_code}' -H "X-Request-ID: $rid" \
  "http://127.0.0.1:${API_PORT}/health/live")
[ "$code" = "200" ] || fail "/health/live returned HTTP $code"
tr -d '\r' < "$hdrs" | grep -qi "^x-request-id: ${rid}$" \
  || fail "response did not echo X-Request-ID $rid"

sleep 1  # let the container's stdout reach the log driver
logs=$(docker compose logs --no-log-prefix api | tr -d '\r')

matches=$(printf '%s\n' "$logs" | grep -cF "\"request_id\": \"$rid\"" || true)
[ "$matches" = "1" ] || fail "expected exactly 1 log line carrying $rid, found $matches"

# Every line the api has written must be JSON; a stray plain-text line means some logger
# (uvicorn, a library) bypassed the structlog handler.
printf '%s\n' "$logs" | python3 -c '
import json, sys
for n, line in enumerate(sys.stdin, 1):
    line = line.strip()
    if not line:
        continue
    try:
        json.loads(line)
    except ValueError:
        sys.exit(f"line {n} is not JSON: {line[:120]}")
' || fail "non-JSON line in api logs"

line=$(printf '%s\n' "$logs" | grep -F "\"request_id\": \"$rid\"")
for key in method path status duration_ms timestamp level; do
  printf '%s' "$line" | grep -q "\"$key\"" || fail "log line missing '$key': $line"
done

echo "OK: slice 5 smoke passed (1 JSON line for request_id=$rid, header echoed)"
