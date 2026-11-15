#!/usr/bin/env bash
# IDEA-043: does a RECREATED Beat run a firing it missed while it was down? Asserts both cases.
# Throwaway network + redis + beat; the dev stack is untouched. Entry: crontab(minute="*/2") (infra/beat-smoke/tapp.py).
#   Case A (control): schedule file in the container's /tmp, as docker-compose.yml did before the fix.
#                     EXPECTED: the missed firing is NOT run. If this ever catches up, the premise of the fix changed.
#   Case B (the fix): schedule file on a FRESH named volume mounted at /var/lib/beat, with NO chown here:
#                     the image itself must own that path (backend/Dockerfile). EXPECTED: the missed firing IS run.
# Timing: the first container fires at an even minute :00; it is removed at about :05; the recreate is at the
# NEXT even-minute :10, so an even :00 boundary falls inside the downtime. One trial per case per run.
# Env: IMG (default flowdesk-beat), TAPP_DIR (default infra/beat-smoke), ONLY_B=1 skips case A.
# Exit: 0 if both cases behave as expected, 1 if not, 2 if a container failed to start (result invalid).
set -u
export MSYS_NO_PATHCONV=1
IMG="${IMG:-flowdesk-beat}"
HERE="$(cd "$(dirname "$0")" && pwd)"
TAPP_DIR="${TAPP_DIR:-$HERE/beat-smoke}"
case "$(uname -s)" in MINGW*|MSYS*) TAPP_DIR="$(cd "$TAPP_DIR" && pwd -W)";; esac
RC=0

gone() { docker rm -f "$1" >/dev/null 2>&1; while docker inspect "$1" >/dev/null 2>&1; do sleep 0.5; done; }
cleanup() { gone tb; gone tredis; docker volume rm tsched >/dev/null 2>&1; docker network rm tnet >/dev/null 2>&1; }
trap cleanup EXIT; cleanup
docker network create tnet >/dev/null
docker run -d --name tredis --network tnet redis:7.4-alpine >/dev/null; sleep 3

sendcount() { docker logs tb 2>&1 | grep -c "Sending due task"; }
start() { docker run -d --name tb --network tnet -v "$TAPP_DIR":/t:ro -e PYTHONPATH=/t "$@" \
    --entrypoint celery "$IMG" -A tapp beat --loglevel=INFO --schedule="$SCHED" >/dev/null; }
wait_until() { # second-of-minute, minute parity (0 even, 1 odd)
  while :; do s=$((10#$(date +%S))); m=$((10#$(date +%M))); [ "$s" -eq "$1" ] && [ $((m%2)) -eq "$2" ] && break; sleep 0.5; done; }

case_run() { # label expect(0|1) sched mountargs...
  local label=$1 expect=$2; SCHED=$3; shift 3
  echo "=== $label ($(date +%T))"
  wait_until 20 1
  start "$@" || { echo "  first container failed to start"; RC=2; return; }
  local waited=0
  until [ "$(sendcount)" -ge 1 ]; do
    sleep 1; waited=$((waited+1))
    if [ "$(docker inspect -f '{{.State.Running}}' tb 2>/dev/null)" != "true" ] || [ "$waited" -gt 150 ]; then
      echo "  first container died or never fired (result invalid):"; docker logs tb 2>&1 | tail -3 | sed 's/^/    /'; RC=2; return; fi
  done
  echo "  first container fired at $(date +%T); removing it"
  docker stop tb >/dev/null; gone tb
  wait_until 10 1; wait_until 10 0
  echo "  recreating at $(date +%T); the next boundary is ~110 s away; watching 45 s"
  start "$@" || { echo "  recreate failed to start (result invalid)"; RC=2; return; }
  sleep 45
  echo "  new container StartedAt: $(docker inspect -f '{{.State.StartedAt}}' tb)"
  local n; n=$(sendcount)
  local got=0; [ "$n" -ge 1 ] && got=1
  if [ "$got" -eq "$expect" ]; then echo "  PASS $label: firings in 45 s = $n (expected $([ "$expect" -eq 1 ] && echo 'caught up' || echo 'no catch-up'))"
  else echo "  FAIL $label: firings in 45 s = $n (expected $([ "$expect" -eq 1 ] && echo 'caught up' || echo 'no catch-up'))"; [ "$RC" -eq 0 ] && RC=1; fi
  docker logs tb 2>&1 | grep -E "Sending due|beat: Starting" | head -3 | sed 's/^/    /'
  gone tb
}

[ -n "${ONLY_B:-}" ] || case_run "A (control): /tmp file, recreated" 0 /tmp/celerybeat-schedule
docker volume create tsched >/dev/null
case_run "B (the fix): fresh named volume at /var/lib/beat, no chown" 1 /var/lib/beat/celerybeat-schedule -v tsched:/var/lib/beat
exit "$RC"
