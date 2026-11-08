#!/usr/bin/env bash
# M1 slice 10 smoke test: the heartbeat sender against the real stack. Pings flow only while
# /health/ready is exactly ok: stopping the API, Postgres or Redis stops them and recovery resumes
# them. A dead monitor doesn't kill the sender, a blank URL doesn't break `up -d`, the public profile
# is covered too, and the URL stays out of the logs. Run from the repo root (needs a .env).
#
# The "monitor" is a throwaway container, so this needs no account and no secret. It proves the
# sender, NOT that an alert ever reaches anyone: only the drill in docs/HEARTBEAT.md proves that.
set -euo pipefail

set -a; . ./.env; set +a
. "$(dirname "$0")/lib.sh"
. "$(dirname "$0")/base-image.sh"

SINK=flowdesk-smoke10-sink
SENTINEL="smoke10-sentinel-$$"
SINK_URL="http://$SINK:9000/?k=$SENTINEL"
INTERVAL=2

# Inline per command, as in slice 7, so each line says which profile and URL it is talking to.
dc_private() { COMPOSE_PROFILES=private HEARTBEAT_URL="$SINK_URL" HEARTBEAT_INTERVAL_SECONDS=$INTERVAL docker compose "$@"; }
dc_public() { COMPOSE_PROFILES=public HEARTBEAT_URL="$SINK_URL" HEARTBEAT_INTERVAL_SECONDS=$INTERVAL docker compose "$@"; }
dc_blank() { COMPOSE_PROFILES=private HEARTBEAT_URL= HEARTBEAT_INTERVAL_SECONDS=$INTERVAL docker compose "$@"; }
dc_both() { COMPOSE_PROFILES=private,public docker compose "$@"; }

# Remember the stack as found. The trap puts it back on every exit, failures included. The sender
# is recreated from .env, so a deployed URL that this script overrode comes back.
private_was_up=0; public_was_up=0
[ -n "$(dc_both ps -q --status running api)" ] && private_was_up=1
[ -n "$(dc_both ps -q --status running api-public)" ] && public_was_up=1
restore() {
  local rc=$?
  docker rm -f "$SINK" >/dev/null 2>&1 || true
  docker compose start postgres redis >/dev/null 2>&1 || echo "WARN: could not restart postgres/redis" >&2
  dc_both rm -sf api api-public >/dev/null 2>&1 || true
  if [ "$private_was_up" = 1 ]; then
    COMPOSE_PROFILES=private docker compose up -d api >/dev/null 2>&1 || echo "WARN: could not restart api" >&2
  elif [ "$public_was_up" = 1 ]; then
    COMPOSE_PROFILES=public docker compose up -d api-public >/dev/null 2>&1 || echo "WARN: could not restart api-public" >&2
  fi
  docker compose up -d heartbeat >/dev/null 2>&1 || echo "WARN: could not restore heartbeat" >&2
  exit "$rc"
}
trap restore EXIT

# Every request the sink has ever logged carrying our sentinel. A stopped sink keeps its log.
pings() { docker logs "$SINK" 2>&1 | grep -c "GET /?k=$SENTINEL" || true; }

expect_flowing() { # what
  local start deadline
  start=$(pings); deadline=$((SECONDS + 45))
  until [ "$(pings)" -ge $((start + 2)) ]; do
    [ $SECONDS -lt $deadline ] || { docker compose logs --tail 20 heartbeat >&2; fail "$1: pings did not flow"; }
    sleep 1
  done
  echo "ok: $1: pings flowing"
}

expect_stopped() { # what
  local before after
  # Let a cycle already past its check finish, then require a window of several intervals.
  sleep $((INTERVAL * 2 + 3))
  before=$(pings)
  sleep $((INTERVAL * 5))
  after=$(pings)
  [ "$before" = "$after" ] || fail "$1: pings kept arriving ($before -> $after)"
  echo "ok: $1: pings stopped"
}

heartbeat_logs() { dc_both logs --no-log-prefix heartbeat 2>&1 | tr -d '\r'; }

expect_log() { # JSON fragment
  heartbeat_logs | grep -qF "$1" || { heartbeat_logs | tail -n 20 >&2; fail "expected $1 in heartbeat logs"; }
}

expect_no_secret() {
  if heartbeat_logs | grep -qF "$SENTINEL"; then fail "the ping URL reached the heartbeat logs"; fi
}

echo "== start: private profile, sender pointed at a throwaway monitor"
dc_both rm -sf api api-public >/dev/null 2>&1 || true
dc_private up -d --build
COMPOSE_PROFILES=private wait_ready

pg_id=$(docker compose ps -q postgres)
network=$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}' "$pg_id")
docker rm -f "$SINK" >/dev/null 2>&1 || true
docker run -d --name "$SINK" --network "$network" "$BASE_IMAGE" python -u -m http.server 9000 >/dev/null
expect_flowing "all up"

echo "== exposure"
hb_id=$(dc_private ps -q heartbeat)
bindings=$(docker inspect -f '{{json .HostConfig.PortBindings}}' "$hb_id")
case "$bindings" in "{}"|"null") ;; *) fail "heartbeat publishes host ports: $bindings" ;; esac
echo "ok: heartbeat publishes no host port"

echo "== API stopped -> silence, then recovery"
dc_private stop api
expect_stopped "api stopped"
expect_log '"reason": "ready_unreachable"'
dc_private start api
expect_flowing "api back"

echo "== Postgres stopped -> silence, then recovery"
docker compose stop postgres
expect_stopped "postgres stopped"
expect_log '"reason": "ready_http_status"'
docker compose start postgres
expect_flowing "postgres back"

echo "== Redis stopped -> /health/ready is a 200 degraded, and still silence (ADR-0008)"
docker compose stop redis
expect_stopped "redis stopped"
expect_log '"ready_status": "degraded"'
docker compose start redis
expect_flowing "redis back"

echo "== monitor down -> the sender survives and says so"
docker stop "$SINK" >/dev/null
deadline=$((SECONDS + 45))
until heartbeat_logs | grep -qF '"event": "ping_failed"'; do
  [ $SECONDS -lt $deadline ] || { heartbeat_logs | tail -n 20 >&2; fail "no ping_failed logged with the monitor down"; }
  sleep 1
done
[ -n "$(dc_private ps -q --status running heartbeat)" ] || fail "sender died when the monitor went away"
docker start "$SINK" >/dev/null
expect_flowing "monitor back"
# After ping_failed on purpose: that is the path most likely to print a URL.
expect_no_secret
echo "ok: the ping URL is in no log line, across every path above"

echo "== blank URL -> up -d still succeeds; running, unarmed, says so"
dc_blank up -d heartbeat
COMPOSE_PROFILES=private wait_ready heartbeat
sleep $((INTERVAL * 2 + 3))
expect_log '"event": "heartbeat_disabled"'
expect_stopped "blank URL"

echo "== public profile"
dc_both rm -sf api api-public >/dev/null 2>&1 || true
dc_public up -d --build api-public heartbeat
COMPOSE_PROFILES=public wait_ready api-public heartbeat
expect_flowing "public profile"

echo "OK: slice 10 smoke passed (pings only on ok; api, postgres and redis each silence them and recover; dead monitor survived; blank URL harmless; public covered; no host port; URL never logged)"
