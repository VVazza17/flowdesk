#!/usr/bin/env bash
# M2 slice 2 smoke test (IDEA-029): scheduled, encrypted backups with an off-box copy, tested end to end
# without anything from the home network.
#
# What stands in for the desktop: a throwaway sshd container whose authorized_keys line is EXACTLY the
# restricted line documented in docs/BACKUP.md (rrsync, write-only, one directory). So this proves the
# transport, the restriction and the restore path, not just the copy.
#
# What it does NOT prove: that a real unattended dump lands on the real desktop and restores from there.
# The slice closes on that, by hand (docs/BACKUP.md, "Closing the slice").
#
# Safety: everything runs against a scratch database and one-off containers. The running `backup` service,
# its `backups` volume and the real database are never touched. Run from the repo root (needs a .env).
set -euo pipefail

set -a; . ./.env; set +a
. "$(dirname "$0")/lib.sh"
export MSYS_NO_PATHCONV=1   # Git Bash on Windows would otherwise rewrite container paths

SINK=flowdesk-smoke-sink
LOOP=flowdesk-smoke-loop
SCRATCH=flowdesk_smoke_s2
RESTORED=flowdesk_smoke_s2_restored
work=$(mktemp -d)

winpath() { cygpath -m "$1" 2> /dev/null || printf '%s' "$1"; }  # a path Docker Desktop understands
psql_pg() { docker compose exec -T postgres psql -X -q -U "$POSTGRES_USER" -v ON_ERROR_STOP=1 -tA "$@" < /dev/null; }

cleanup() {
  local rc=$?
  docker rm -f "$SINK" "$LOOP" > /dev/null 2>&1 || true
  psql_pg -d postgres -c "DROP DATABASE IF EXISTS $SCRATCH WITH (FORCE)" > /dev/null 2>&1 || true
  psql_pg -d postgres -c "DROP DATABASE IF EXISTS $RESTORED WITH (FORCE)" > /dev/null 2>&1 || true
  if [ "$rc" -ne 0 ] && [ -s "$work/err.log" ]; then echo "--- last container stderr:" >&2; tail -n 20 "$work/err.log" >&2; fi
  rm -rf "$work"
  exit "$rc"
}
trap cleanup EXIT

docker compose up -d --build
wait_ready

echo "== the library's logic (schedule, catch-up, backoff, retention, recipients)"
docker compose run --rm --no-deps -T -v "$(winpath "$PWD/infra/backup/selftest.sh"):/selftest.sh:ro" \
  --entrypoint bash backup /selftest.sh > "$work/selftest.out" 2> "$work/err.log" || { cat "$work/selftest.out"; fail "selftest failed"; }
grep -q '^FAIL' "$work/selftest.out" && { cat "$work/selftest.out"; fail "selftest reported a failure"; }
echo "ok: $(grep -c '^ok:' "$work/selftest.out") assertions"

echo "== throwaway keys: age for encryption, ssh for transport (kept distinct, as in production)"
mkdir -p "$work/k"
docker compose run --rm --no-deps -T -v "$(winpath "$work/k"):/k" --entrypoint sh backup -ec '
  age-keygen -o /k/age.key 2> /dev/null
  age-keygen -y /k/age.key > /k/recipients.txt
  ssh-keygen -q -t ed25519 -N "" -C flowdesk-backup-smoke -f /k/id
  chmod a+r /k/*' > /dev/null 2> "$work/err.log"
[ "$(grep -c '^age1' "$work/k/recipients.txt")" = "1" ] || fail "no age public key was made"
! grep -q 'AGE-SECRET-KEY' "$work/k/recipients.txt" || fail "the recipients file must hold the PUBLIC key only"

echo "== a scratch database, migrated"
psql_pg -d postgres -c "DROP DATABASE IF EXISTS $SCRATCH WITH (FORCE)" > /dev/null
psql_pg -d postgres -c "CREATE DATABASE $SCRATCH" > /dev/null
docker compose run --rm --no-deps -T \
  -e "DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${SCRATCH}" \
  migrate > /dev/null 2> "$work/err.log"

echo "== a stand-in for the desktop: sshd with the restricted key line, write-only, one directory"
network=$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}' "$(docker compose ps -q postgres)")
mkdir -p "$work/sink"
printf 'restrict,command="rrsync -wo /srv/backups" %s\n' "$(cat "$work/k/id.pub")" > "$work/sink/authorized_keys"
cat > "$work/sink/run.sh" << 'SINKEOF'
set -eu
if [ ! -f /tmp/setup-done ]; then
  apk add --no-cache openssh-server rsync rrsync python3 busybox-extras > /dev/null
  adduser -D -s /bin/sh fd
  echo "fd:$(head -c 24 /dev/urandom | base64)" | chpasswd   # unlocks the account; password login is off
  mkdir -p /srv/backups /srv/www /home/fd/.ssh
  echo ok > /srv/www/ping
  chown fd:fd /srv/backups
  cp /setup/authorized_keys /home/fd/.ssh/authorized_keys
  chown -R fd:fd /home/fd/.ssh
  chmod 700 /home/fd/.ssh
  chmod 600 /home/fd/.ssh/authorized_keys
  printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPubkeyAuthentication yes\nAllowUsers fd\n' > /etc/ssh/sshd_config
  ssh-keygen -A > /dev/null
  touch /tmp/setup-done
fi
busybox-extras httpd -f -p 8080 -h /srv/www -vv &
exec /usr/sbin/sshd -D -e
SINKEOF
docker rm -f "$SINK" > /dev/null 2>&1 || true
docker run -d --name "$SINK" --network "$network" --network-alias sink \
  -v "$(winpath "$work/sink"):/setup:ro" alpine:3.23 sh /setup/run.sh > /dev/null
wait_sink() {
  local deadline=$((SECONDS + 90))
  until docker logs "$SINK" 2>&1 | grep -q "Server listening"; do
    [ $SECONDS -lt $deadline ] || { docker logs "$SINK" 2>&1 | tail -n 20 >&2; fail "the sink's sshd did not start"; }
    sleep 1
  done
}
wait_sink
printf 'sink %s\n' "$(docker exec "$SINK" cut -d' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub)" > "$work/k/known_hosts"

# One-off backup containers. The compose service's own bind mounts take their sources from these variables,
# so exporting them here points the one-offs at the throwaway keys. The `backups` volume is never used:
# BACKUP_DIR is a path inside each one-off.
export BACKUP_SSH_KEY_FILE="$(winpath "$work/k/id")"
export BACKUP_SSH_KNOWN_HOSTS_FILE="$(winpath "$work/k/known_hosts")"
export BACKUP_RECIPIENTS_FILE="$(winpath "$work/k/recipients.txt")"
bk() { # [extra docker compose run flags] -- command...
  local flags=()
  while [ "$1" != "--" ]; do flags+=("$1"); shift; done
  shift
  docker compose run --rm --no-deps -T -e "PGDATABASE=$SCRATCH" -e BACKUP_DIR=/tmp/smoke \
    -e BACKUP_SSH_HOST=sink -e BACKUP_SSH_USER=fd "${flags[@]+"${flags[@]}"}" backup "$@" 2> "$work/err.log"
}

echo "== a backup is encrypted and kept locally; the copy reaches the sink; a second copy sends nothing"
out=$(bk -- bash -c '
  mkdir -p /tmp/smoke
  /opt/backup/run-backup.sh && /opt/backup/run-copy.sh && /opt/backup/run-copy.sh
  echo "LOCAL:"; ls -1 /tmp/smoke
  echo "PLAINTEXT-LEFT:"; find /tmp -name "*.dump" -o -name "*.manifest" -o -name "*.partial" | wc -l
  echo "MAGIC:"; head -c 21 /tmp/smoke/*.tar.age; echo
  echo "PGDMP-IN-FILE:"; grep -c PGDMP /tmp/smoke/*.tar.age || true
')
printf '%s\n' "$out" | grep -q '"event":"backup_succeeded"' || { echo "$out" >&2; fail "no backup_succeeded line"; }
printf '%s\n' "$out" | grep -q '"event":"copy_succeeded"' || { echo "$out" >&2; fail "no copy_succeeded line"; }
[ "$(printf '%s\n' "$out" | grep -c '"event":"copy_succeeded"')" = "2" ] || fail "the second copy should also succeed"
printf '%s\n' "$out" | sed -n '/^LOCAL:/,/^PLAINTEXT-LEFT:/p' | grep -q '_[0-9]\{8\}T[0-9]\{6\}Z.tar.age$' || fail "no .tar.age file kept locally"
[ "$(printf '%s\n' "$out" | sed -n '/^PLAINTEXT-LEFT:/{n;p;}')" = "0" ] || fail "plaintext dump, manifest or partial file left behind"
printf '%s\n' "$out" | sed -n '/^MAGIC:/{n;p;}' | grep -q '^age-encryption.org/v1' || fail "the kept file is not an age file"
[ "$(printf '%s\n' "$out" | sed -n '/^PGDMP-IN-FILE:/{n;p;}')" = "0" ] || fail "the encrypted file contains a plaintext pg_dump header"
landed=$(docker exec "$SINK" ls -1 /srv/backups)
[ "$(printf '%s\n' "$landed" | wc -l | tr -d ' ')" = "1" ] || fail "expected exactly one file on the sink, got: $landed"
echo "ok: $landed"

echo "== the copy that landed restores, using only the PRIVATE key (infra/restore-backup.sh)"
docker cp "$SINK:/srv/backups/$landed" "$(winpath "$work")/got.tar.age"   # docker.exe needs a Windows path here
bash infra/restore-backup.sh "$work/got.tar.age" --identity "$work/k/age.key" --db "$RESTORED" > "$work/restore.out" 2>&1 \
  || { cat "$work/restore.out" >&2; fail "restore-backup.sh failed"; }
grep -q "^OK: restored into '$RESTORED'" "$work/restore.out" || { cat "$work/restore.out" >&2; fail "restore did not report OK"; }
# The dump was taken while its own ops.backup row said 'running', so that row is in the restored copy.
[ "$(psql_pg -d "$RESTORED" -c "select count(*) from task_runs where task_name = 'ops.backup' and status = 'running'")" = "1" ] \
  || fail "the restored database does not contain the backup's own task_runs row"
want=$(psql_pg -d "$SCRATCH" -c "select version_num from alembic_version")
[ "$(psql_pg -d "$RESTORED" -c "select version_num from alembic_version")" = "$want" ] || fail "alembic revision differs after restore"
echo "ok: restored $landed into $RESTORED"
! bash infra/restore-backup.sh "$work/got.tar.age" --identity "$work/k/id" --db "${RESTORED}_x" > /dev/null 2>&1 \
  || fail "restore-backup.sh accepted an SSH key as the age identity"
echo "ok: the wrong key is refused"

echo "== the restricted key: no shell, no reading the destination, no writing outside its directory"
probe=$(bk -- bash -c '
  o="-F /dev/null -i /run/backup/id_key -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/run/backup/known_hosts -o ConnectTimeout=10"
  echo "SHELL:"; ssh $o fd@sink "echo shell-works" 2>&1; echo "shell-exit=$?"
  echo "PULL:"; mkdir -p /tmp/pull; rsync -r -e "ssh $o" fd@sink:./ /tmp/pull/ 2>&1; echo "pull-exit=$?"; echo "pulled=$(ls /tmp/pull | wc -l)"
  echo "ESCAPE:"; echo x > /tmp/x.tar.age; rsync -r -e "ssh $o" /tmp/x.tar.age fd@sink:../escape.tar.age 2>&1; echo "escape-exit=$?"
  echo "ABSOLUTE:"; rsync -r -e "ssh $o" /tmp/x.tar.age fd@sink:/etc/escape.tar.age 2>&1; echo "absolute-exit=$?"
' || true)
# Each refusal is checked by the rrsync message that proves WHO refused, so a probe that fails for an
# unrelated reason (a dead sshd, a wrong key) cannot pass as "restricted".
printf '%s\n' "$probe" | grep -q 'shell-works' && { printf '%s\n' "$probe" >&2; fail "the restricted key ran a shell command"; }
printf '%s\n' "$probe" | grep -q 'rrsync error: SSH_ORIGINAL_COMMAND does not run rsync' \
  || { printf '%s\n' "$probe" >&2; fail "a shell command was not refused by rrsync"; }
echo "ok: no shell (refused by rrsync)"
[ "$(printf '%s\n' "$probe" | sed -n 's/^pulled=//p')" = "0" ] || fail "the restricted key could read the destination"
printf '%s\n' "$probe" | grep -q 'rrsync error: reading from write-only server is not allowed' \
  || { printf '%s\n' "$probe" >&2; fail "a pull was not refused by rrsync"; }
echo "ok: nothing can be pulled (refused by rrsync)"
printf '%s\n' "$probe" | grep -q 'rrsync error: do not use .. in arg' \
  || { printf '%s\n' "$probe" >&2; fail "a path with .. was not refused by rrsync"; }
# An absolute path is not refused, it is anchored inside the restricted directory; the point is where it lands.
docker exec "$SINK" sh -c 'test ! -e /srv/escape.tar.age && test ! -e /etc/escape.tar.age && test ! -e /srv/backups/escape.tar.age' \
  || fail "the restricted key wrote outside its directory"
echo "ok: nothing written outside /srv/backups"

echo "== no recipient key: the backup fails closed, writes nothing, and says why"
out=$(bk -v "$(winpath "$PWD/infra/backup/recipients.txt"):/placeholder:ro" -e BACKUP_RECIPIENTS_FILE=/placeholder -- bash -c '
  mkdir -p /tmp/smoke; /opt/backup/run-backup.sh; echo "exit=$?"; echo "files=$(ls /tmp/smoke | wc -l)"' || true)
printf '%s\n' "$out" | grep -q '"error_class":"NoRecipient"' || { echo "$out" >&2; fail "expected a NoRecipient failure"; }
printf '%s\n' "$out" | grep -q '^exit=1$' || fail "a backup with no recipient should exit 1"
printf '%s\n' "$out" | grep -q '^files=0$' || fail "a backup with no recipient wrote a file"
echo "ok: refused, nothing written"

echo "== the destination is unreachable: the backup still succeeds, only the copy fails"
docker stop "$SINK" > /dev/null
out=$(bk -- bash -c '
  mkdir -p /tmp/smoke; /opt/backup/run-backup.sh; echo "backup-exit=$?"; /opt/backup/run-copy.sh; echo "copy-exit=$?"
  echo "kept=$(ls /tmp/smoke | wc -l)"' || true)
printf '%s\n' "$out" | grep -q '^backup-exit=0$' || { echo "$out" >&2; fail "the backup should succeed with the destination down"; }
printf '%s\n' "$out" | grep -q '^copy-exit=1$' || fail "the copy should fail with the destination down"
printf '%s\n' "$out" | grep -q '"error_class":"Unreachable"' || { echo "$out" >&2; fail "expected an Unreachable failure"; }
printf '%s\n' "$out" | grep -q '^kept=1$' || fail "the local backup should be kept"
echo "ok: a failed copy is not a failed backup"

echo "== health: both steps are reported separately, from the real rows the scripts wrote"
health() {
  docker compose run --rm --no-deps -T \
    -e "DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${SCRATCH}" \
    api python - 2> "$work/err.log" << 'PYEOF'
import asyncio, os
from datetime import UTC, datetime
from sqlalchemy.ext.asyncio import create_async_engine
from app import settings
from app.services import health as svc
from app.services.health_probes import SqlRedisProbes
from app.tasks.names import BACKUP, BACKUP_COPY

async def main() -> None:
    eng = create_async_engine(os.environ["DATABASE_URL"])
    probes = SqlRedisProbes(eng, None)
    state = await probes.data_state()
    successes = {n: await probes.last_task_success(n) for n in (BACKUP, BACKUP_COPY)}
    b = svc.assemble_data(state, datetime.now(UTC), successes, settings.backup_stale_after())["backups"]
    print(f"backup={b['backup']['state']} copy={b['copy']['state']} copy_failed={b['copy']['last_attempt_failed']} "
          f"copy_error={b['copy']['last_error_class']}")
    await eng.dispose()

asyncio.run(main())
PYEOF
}
h=$(health)
[ "$h" = "backup=ok copy=ok copy_failed=True copy_error=Unreachable" ] \
  || fail "unexpected backup health: $h (want the backup ok, the copy ok from earlier but its last attempt failed)"
echo "ok: $h"

echo "== the destination is back: the scheduler catches up by itself, pings the monitor, and backs up once"
docker start "$SINK" > /dev/null
wait_sink
docker rm -f "$LOOP" > /dev/null 2>&1 || true
docker compose run -d --name "$LOOP" --no-deps -T -e "PGDATABASE=$SCRATCH" -e BACKUP_DIR=/tmp/smoke \
  -e BACKUP_SSH_HOST=sink -e BACKUP_SSH_USER=fd -e BACKUP_AT=00:00 \
  -e BACKUP_TICK_SECONDS=1 -e BACKUP_RETRY_SECONDS=2 -e BACKUP_RETRY_CAP_SECONDS=2 \
  -e BACKUP_PING_URL=http://sink:8080/ping backup bash -c 'mkdir -p /tmp/smoke && exec /opt/backup/backup-loop.sh' > /dev/null 2> "$work/err.log"
deadline=$((SECONDS + 90))
until docker logs "$LOOP" 2>&1 | grep -q '"event":"copy_pinged"'; do
  docker inspect -f '{{.State.Running}}' "$LOOP" | grep -q true || { docker logs "$LOOP" >&2; fail "the scheduler stopped"; }
  [ $SECONDS -lt $deadline ] || { docker logs "$LOOP" 2>&1 | tail -n 15 >&2; fail "the scheduler never completed a copy"; }
  sleep 2
done
sleep 6   # several more ticks: it must not back up again, because today's slot has been served
logs=$(docker logs "$LOOP" 2>&1)
[ "$(printf '%s\n' "$logs" | grep -c '"event":"backup_succeeded"')" = "1" ] || { printf '%s\n' "$logs" >&2; fail "expected exactly one backup from the scheduler"; }
printf '%s\n' "$logs" | grep -q '"event":"backup_scheduler_started"' || fail "no scheduler start line"
docker logs "$SINK" 2>&1 | grep -q 'url:/ping' || fail "the monitor URL was never requested"
[ "$(docker exec "$SINK" ls -1 /srv/backups | wc -l | tr -d ' ')" = "2" ] || fail "the sink should now hold two backups"
docker rm -f "$LOOP" > /dev/null
echo "ok: backed up once, copied after the destination returned, pinged the monitor"

echo "== no destination configured: the copy is recorded as skipped, never as a success"
bk -e BACKUP_SSH_HOST= -- bash -c 'mkdir -p /tmp/smoke; /opt/backup/run-backup.sh >/dev/null; /opt/backup/run-copy.sh; echo "exit=$?"' > "$work/nc.out" || true
grep -q '"event":"copy_not_configured"' "$work/nc.out" || { cat "$work/nc.out" >&2; fail "expected copy_not_configured"; }
grep -q '^exit=0$' "$work/nc.out" || fail "an unconfigured copy should exit 0"
h=$(health)
[ "$h" = "backup=ok copy=not_configured copy_failed=False copy_error=NotConfigured" ] || fail "unexpected backup health: $h"
echo "ok: $h"

echo "OK: M2 slice 2 smoke passed (encrypted, kept locally, copied through the restricted key, restored with the private key only; fails closed with no recipient; a down destination fails the copy and not the backup; the scheduler catches up and pings; unconfigured is reported as such)"
