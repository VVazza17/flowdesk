#!/usr/bin/env bash
# M2 slice 6 / IDEA-043 smoke: Beat's schedule file survives a container recreate.
#   1. the built image owns /var/lib/beat as uid 10001 (the Errno 13 trap);
#   2. docker-compose.yml puts Beat's --schedule on that path, on a named volume;
#   3. the real dev `beat` service, recreated, still has its schedule file (persisted on the volume);
#   4. infra/idea043-beat-recreate.sh: a recreated Beat catches up a missed firing with the volume, and does not without it.
# Takes about 8 minutes (case A and B each wait out a real cron boundary). Run it again after any change to Beat, the
# Dockerfile, or the compose volumes: one trial once is not a guarantee.
set -u
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1
FAIL=0
ok()  { echo "  ok   $*"; }
bad() { echo "  FAIL $*"; FAIL=1; }

echo "== build"
docker compose build beat >/dev/null 2>&1 || { echo "build failed"; exit 1; }
# The tag compose gives the beat build: <project>-beat. `compose images -q` returned an id Docker could not find.
IMG="${COMPOSE_PROJECT_NAME:-flowdesk}-beat"
docker image inspect "$IMG" >/dev/null 2>&1 || { echo "image $IMG not found after build"; exit 1; }

echo "== 1. the image owns /var/lib/beat as uid 10001"
own="$(docker run --rm --entrypoint stat "$IMG" -c '%u' /var/lib/beat 2>&1)"
[ "$own" = "10001" ] && ok "owner uid $own" || bad "owner is '$own', expected 10001"

echo "== 2. compose: schedule path and volume"
cfg="$(docker compose config 2>/dev/null)"
echo "$cfg" | grep -q -- "--schedule=/var/lib/beat/celerybeat-schedule" && ok "beat --schedule is on /var/lib/beat" || bad "beat --schedule is not on /var/lib/beat"
echo "$cfg" | grep -q "beat_schedule" && ok "named volume beat_schedule declared" || bad "no beat_schedule volume"
echo "$cfg" | grep -q -- "--schedule=/tmp" && bad "a --schedule in /tmp is still configured" || ok "no schedule in /tmp"

echo "== 3. the dev beat service, recreated, keeps its schedule file"
docker compose up -d --force-recreate beat >/dev/null 2>&1
sleep 15
f="$(docker compose exec -T beat sh -c 'ls -l /var/lib/beat/celerybeat-schedule* 2>&1 | head -2')"
echo "$f" | grep -q "flowdesk" && ok "schedule file exists, owned by flowdesk" || bad "no schedule file as flowdesk: $f"
docker compose up -d --force-recreate beat >/dev/null 2>&1
sleep 10
f2="$(docker compose exec -T beat sh -c 'ls /var/lib/beat/ 2>&1 | head -3')"
[ -n "$f2" ] && echo "$f2" | grep -q celerybeat && ok "file still there after a second recreate" || bad "file gone after recreate: $f2"

echo "== 4. throwaway Beat: catch-up with and without the volume (~8 min)"
IMG="$IMG" bash infra/idea043-beat-recreate.sh
rc=$?
[ "$rc" -eq 0 ] && ok "case A did not catch up, case B did" || bad "recreate test exit code $rc (1 = wrong behaviour, 2 = invalid trial)"

[ "$FAIL" -eq 0 ] && echo "SMOKE PASS" || echo "SMOKE FAIL"
exit "$FAIL"
