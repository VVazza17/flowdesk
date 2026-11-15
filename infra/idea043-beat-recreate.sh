#!/usr/bin/env bash
# IDEA-043 test: does a RECREATED Beat run a firing it missed while it was down?
# Throwaway network + redis + beat (the dev stack is untouched). Entry: crontab(minute="*/2").
# Case A: schedule file in the container's /tmp (what docker-compose.yml does today).
# Case B: schedule file on a named volume (the IDEA-043 proposal), volume chowned to the app user.
# Needs TAPP_DIR (a dir holding tapp.py) and the flowdesk-beat image. Cleans up by name.
set -u
IMG="${IMG:-flowdesk-beat}"; TAPP_DIR="${TAPP_DIR:?}"
export MSYS_NO_PATHCONV=1
cleanup() { docker rm -f tb tredis >/dev/null 2>&1; docker volume rm tsched >/dev/null 2>&1; docker network rm tnet >/dev/null 2>&1; }
trap cleanup EXIT; cleanup
docker network create tnet >/dev/null
docker run -d --name tredis --network tnet redis:7.4-alpine >/dev/null; sleep 3
sendcount() { docker logs tb 2>&1 | grep -c "Sending due task"; }
start() { # mount-args...
  docker run -d --name tb --network tnet -v "$TAPP_DIR":/t:ro -e PYTHONPATH=/t "$@" \
    --entrypoint celery "$IMG" -A tapp beat --loglevel=INFO --schedule="$SCHED" >/dev/null; }
wait_until_sec() { # wait until wall clock second-of-minute==$1 and minute parity==$2 (0 even)
  while :; do s=$((10#$(date +%S))); m=$((10#$(date +%M))); [ "$s" -eq "$1" ] && [ $((m%2)) -eq "$2" ] && break; sleep 0.5; done; }
case_run() { # label sched mountarg...
  local label=$1; SCHED=$2; shift 2
  echo "=== $label ($(date +%T))"
  wait_until_sec 20 1                      # odd minute :20 -> first firing at the next even minute
  start "$@"
  until [ "$(sendcount)" -ge 1 ]; do sleep 1; done
  echo "  first container fired at $(date +%T); stopping and removing it"
  docker stop tb >/dev/null; docker rm -f tb >/dev/null 2>&1; while docker inspect tb >/dev/null 2>&1; do sleep 0.5; done
  wait_until_sec 10 1                      # odd :10 first, so the NEXT even :10 is two minutes on:
  wait_until_sec 10 0                      # a boundary (the even :00 between) passes while it is down
  echo "  recreating at $(date +%T); next boundary is ~110 s away; watching 45 s"
  start "$@" || { echo "  recreate FAILED to start: result invalid"; return 1; }; sleep 45
  echo "  new container StartedAt: $(docker inspect -f '{{.State.StartedAt}}' tb)"
  n=$(sendcount); echo "  RESULT $label: firings in the first 45 s after recreate = $n ($( [ "$n" -ge 1 ] && echo 'CAUGHT UP the missed firing' || echo 'DID NOT run the missed firing'))"
  docker logs tb 2>&1 | grep -E "Sending due|beat: Starting|Scheduler" | head -4 | sed 's/^/    /'
  docker rm -f tb >/dev/null 2>&1; while docker inspect tb >/dev/null 2>&1; do sleep 0.5; done
}
[ -n "${ONLY_B:-}" ] || case_run "A: /tmp file, container recreated" /tmp/celerybeat-schedule
docker volume create tsched >/dev/null
# A fresh named volume is root-owned and Beat runs as uid 10001 (Errno 13 on the first try of this test, 2026-11-15).
# The real fix must make the mount path owned by that user in the image; here we emulate that.
docker run --rm -v tsched:/sched --user root --entrypoint chown "$IMG" 10001:999 /sched >/dev/null
case_run "B: named volume" /sched/celerybeat-schedule -v tsched:/sched
