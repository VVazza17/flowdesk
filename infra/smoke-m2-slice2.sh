#!/usr/bin/env bash
# M2 slice 2 smoke test (IDEA-029): scheduled, encrypted backups with an off-box SFTP copy, tested end to end
# without anything from the home network.
#
# What stands in for the Windows desktop: throwaway Linux sshd containers that speak SFTP only
# (ForceCommand internal-sftp), configured to mimic the three permission policies the real account could have:
#   no-delete   cannot delete or rename; CAN overwrite          (what "create and write, deny Delete" gives)
#   write-once  also cannot overwrite, append or make folders   (new files are created read-only: the creating
#                                                                handle can fill them, nothing can reopen them)
#   open        nothing is refused                              (to prove the probe catches a broken restriction)
#
# What it does NOT prove, and cannot: Windows OpenSSH Server itself, NTFS ACLs, or that a real unattended dump
# lands on the real desktop and restores from there. The ACL semantics were measured separately on NTFS
# (docs/BACKUP.md); the real destination is checked by infra/backup/verify-destination.sh, and the slice closes
# on the drill in docs/BACKUP.md ("Closing the slice").
#
# Safety: everything runs against a scratch database and one-off containers. The running `backup` service, its
# `backups` volume and the real database are never touched. Run from the repo root (needs a .env).
set -euo pipefail

set -a; . ./.env; set +a
. "$(dirname "$0")/lib.sh"
export MSYS_NO_PATHCONV=1   # Git Bash on Windows would otherwise rewrite container paths

SINKS="flowdesk-smoke-sink flowdesk-smoke-opensink flowdesk-smoke-wosink"
SINK=flowdesk-smoke-sink          # alias: sink       policy: no-delete
OPENSINK=flowdesk-smoke-opensink  # alias: opensink   policy: open
WOSINK=flowdesk-smoke-wosink      # alias: wosink     policy: write-once
LOOP=flowdesk-smoke-loop
SCRATCH=flowdesk_smoke_s2
RESTORED=flowdesk_smoke_s2_restored
RDIR=/srv/backups
work=$(mktemp -d)

winpath() { cygpath -m "$1" 2> /dev/null || printf '%s' "$1"; }  # a path Docker Desktop understands
psql_pg() { docker compose exec -T postgres psql -X -q -U "$POSTGRES_USER" -v ON_ERROR_STOP=1 -tA "$@" < /dev/null; }

cleanup() {
  local rc=$?
  # shellcheck disable=SC2086
  docker rm -f $SINKS "$LOOP" "$LOOP-cfg" "$LOOP-tr" "$LOOP-lg" > /dev/null 2>&1 || true
  psql_pg -d postgres -c "DROP DATABASE IF EXISTS $SCRATCH WITH (FORCE)" > /dev/null 2>&1 || true
  psql_pg -d postgres -c "DROP DATABASE IF EXISTS $RESTORED WITH (FORCE)" > /dev/null 2>&1 || true
  psql_pg -d postgres -c "DROP DATABASE IF EXISTS ${RESTORED}_r WITH (FORCE)" > /dev/null 2>&1 || true
  psql_pg -d postgres -c "DROP DATABASE IF EXISTS ${RESTORED}_s WITH (FORCE)" > /dev/null 2>&1 || true
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

echo "== stand-ins for the desktop: three SFTP-only sshd policies"
network=$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}' "$(docker compose ps -q postgres)")
mkdir -p "$work/sinkctx"
cat "$work/k/id.pub" > "$work/sinkctx/authorized_keys"
cat > "$work/sinkctx/Dockerfile" << 'DOCKEREOF'
FROM alpine:3.23
RUN apk add --no-cache openssh-server openssh-sftp-server busybox-extras \
 && adduser -D -s /bin/false fd \
 && echo "fd:$(head -c 24 /dev/urandom | base64)" | chpasswd \
 && mkdir -p /srv/backups /srv/www /home/fd/.ssh \
 && echo ok > /srv/www/ping  && mkdir -p /srv/www/cgi-bin  && printf '#!/bin/sh\necho "Content-type: text/plain"\necho\necho ok\n' > /srv/www/cgi-bin/hb  && chmod +x /srv/www/cgi-bin/hb \
 && chown fd:fd /srv/backups \
 && ssh-keygen -A > /dev/null
COPY authorized_keys /home/fd/.ssh/authorized_keys
COPY run.sh /run.sh
RUN chown -R fd:fd /home/fd/.ssh && chmod 700 /home/fd/.ssh && chmod 600 /home/fd/.ssh/authorized_keys
ENTRYPOINT ["sh", "/run.sh"]
DOCKEREOF
cat > "$work/sinkctx/run.sh" << 'SINKEOF'
set -eu
# The restriction lives in sshd_config, as it does on the Windows destination: SFTP only, no shell, no tunnels.
# SFTP_ARGS shapes what the SFTP server will do, standing in for the NTFS permissions of the real account.
cat > /etc/ssh/sshd_config << EOF
PasswordAuthentication no
PubkeyAuthentication yes
AllowUsers fd
Subsystem sftp internal-sftp
Match User fd
    ForceCommand internal-sftp ${SFTP_ARGS:-}
    AllowTcpForwarding no
    PermitTTY no
EOF
busybox-extras httpd -f -p 8080 -h /srv/www -vv &
exec /usr/sbin/sshd -D -e
SINKEOF
docker build -q -t flowdesk-smoke-sink "$(winpath "$work/sinkctx")" > /dev/null
docker rm -f $SINKS > /dev/null 2>&1 || true
# no-delete: remove/rename refused. write-once: also mkdir and setstat refused, and files are created 0444.
docker run -d --name "$SINK" --network "$network" --network-alias sink \
  -e "SFTP_ARGS=-P remove,rmdir,rename,posix-rename" flowdesk-smoke-sink > /dev/null
docker run -d --name "$OPENSINK" --network "$network" --network-alias opensink flowdesk-smoke-sink > /dev/null
docker run -d --name "$WOSINK" --network "$network" --network-alias wosink \
  -e "SFTP_ARGS=-u 0222 -P remove,rmdir,rename,posix-rename,mkdir,setstat,fsetstat" flowdesk-smoke-sink > /dev/null
wait_sink() { # CONTAINER
  local deadline=$((SECONDS + 60))
  until docker logs "$1" 2>&1 | grep -q "Server listening"; do
    [ $SECONDS -lt $deadline ] || { docker logs "$1" 2>&1 | tail -n 20 >&2; fail "$1's sshd did not start"; }
    sleep 1
  done
}
for s in $SINKS; do wait_sink "$s"; done
: > "$work/k/known_hosts"
for pair in "$SINK:sink" "$OPENSINK:opensink" "$WOSINK:wosink"; do
  printf '%s %s\n' "${pair#*:}" "$(docker exec "${pair%%:*}" cut -d' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub)" >> "$work/k/known_hosts"
done
echo "ok: $(wc -l < "$work/k/known_hosts" | tr -d ' ') hosts pinned"

# One-off backup containers. The compose service's bind mounts take their sources from these variables, so
# exporting them points the one-offs at the throwaway keys. The `backups` volume is never used: BACKUP_DIR is a
# path inside each one-off.
export BACKUP_SSH_KEY_FILE="$(winpath "$work/k/id")"
export BACKUP_SSH_KNOWN_HOSTS_FILE="$(winpath "$work/k/known_hosts")"
export BACKUP_RECIPIENTS_FILE="$(winpath "$work/k/recipients.txt")"
# Mounted into every one-off: reads a file's size off a destination, taking the NAME from the end of the long
# listing's path (the same rule run-copy.sh uses), so the smoke does not trust the code it is checking.
cat > "$work/helpers.sh" << 'HELPEREOF'
remote_size() { # HOST NAME
  echo "ls -l \"$BACKUP_SSH_PATH\"" |
    sftp -q -F /dev/null -i /run/backup/id_key -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
      -o UserKnownHostsFile=/run/backup/known_hosts -o ConnectTimeout=10 -b - "fd@$1" 2> /dev/null |
    awk -v n="$2" 'NF >= 9 { b = $NF; sub(/^.*[\/\\]/, "", b); if (b == n) print $(NF-4) }'
}
HELPEREOF
bk() { # [extra docker compose run flags] -- command...
  local flags=()
  while [ "$1" != "--" ]; do flags+=("$1"); shift; done
  shift
  docker compose run --rm --no-deps -T -e "PGDATABASE=$SCRATCH" -e BACKUP_DIR=/tmp/smoke \
    -v "$(winpath "$work/helpers.sh"):/helpers.sh:ro" \
    -e BACKUP_SSH_HOST=sink -e BACKUP_SSH_USER=fd -e "BACKUP_SSH_PATH=$RDIR" \
    "${flags[@]+"${flags[@]}"}" backup "$@" 2> "$work/err.log"
}
# Common sftp options for the probes that talk to a sink by hand from inside a one-off.
SFTP='sftp -q -F /dev/null -i /run/backup/id_key -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/run/backup/known_hosts -o ConnectTimeout=10'

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
[ "$(printf '%s\n' "$out" | grep -c '"event":"copy_succeeded"')" = "2" ] || { echo "$out" >&2; fail "both copies should succeed"; }
printf '%s\n' "$out" | grep '"event":"copy_succeeded"' | sed -n 1p | grep -q '"uploaded":"1"' || fail "the first copy should upload one file"
printf '%s\n' "$out" | grep '"event":"copy_succeeded"' | sed -n 2p | grep -q '"uploaded":"0","stepped_over":"0","skipped":"1"' || fail "the second copy should upload nothing and skip one"
printf '%s\n' "$out" | sed -n '/^LOCAL:/,/^PLAINTEXT-LEFT:/p' | grep -q '_[0-9]\{8\}T[0-9]\{6\}Z.tar.age$' || fail "no .tar.age file kept locally"
[ "$(printf '%s\n' "$out" | sed -n '/^PLAINTEXT-LEFT:/{n;p;}')" = "0" ] || fail "plaintext dump, manifest or partial file left behind"
printf '%s\n' "$out" | sed -n '/^MAGIC:/{n;p;}' | grep -q '^age-encryption.org/v1' || fail "the kept file is not an age file"
[ "$(printf '%s\n' "$out" | sed -n '/^PGDMP-IN-FILE:/{n;p;}')" = "0" ] || fail "the encrypted file contains a plaintext pg_dump header"
landed=$(docker exec "$SINK" ls -1 "$RDIR")
[ "$(printf '%s\n' "$landed" | wc -l | tr -d ' ')" = "1" ] || fail "expected exactly one file on the sink, got: $landed"
echo "ok: $landed (uploaded once, skipped the second time)"

echo "== the copy that landed restores, using only the PRIVATE key (infra/restore-backup.sh)"
docker cp "$SINK:$RDIR/$landed" "$(winpath "$work")/got.tar.age"   # docker.exe needs a Windows path here
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

echo "== the key gets SFTP and nothing else: no shell, no delete, no rename (stand-in for the account's rights)"
probe=$(bk -- bash -c '
  mkdir -p /tmp/p; echo data > /tmp/p/one
  echo "SHELL:"; ssh -F /dev/null -i /run/backup/id_key -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/run/backup/known_hosts -o ConnectTimeout=10 fd@sink "echo shell-works; id" 2>&1; echo "shell-exit=$?"
  echo "PUT:"; echo "put /tmp/p/one '"$RDIR"'/probe-one" | '"$SFTP"' -b - fd@sink 2>&1; echo "put-exit=$?"
  echo "RM:"; echo "rm '"$RDIR"'/probe-one" | '"$SFTP"' -b - fd@sink 2>&1; echo "rm-exit=$?"
  echo "RENAME:"; echo "rename '"$RDIR"'/probe-one '"$RDIR"'/probe-two" | '"$SFTP"' -b - fd@sink 2>&1; echo "rename-exit=$?"
' || true)
printf '%s\n' "$probe" | grep -q 'shell-works' && { printf '%s\n' "$probe" >&2; fail "the key ran a shell command"; }
printf '%s\n' "$probe" | grep -q 'uid=' && { printf '%s\n' "$probe" >&2; fail "the key ran a shell command (id)"; }
# The refusals are checked by their own words, so a probe that fails for an unrelated reason (a dead sshd, a
# wrong key) cannot pass as "restricted". The put in the same probe already proves the session itself works.
printf '%s\n' "$probe" | grep -q 'This service allows sftp connections only' \
  || { printf '%s\n' "$probe" >&2; fail "a shell request was not refused by the SFTP-only rule"; }
echo "ok: no shell (sshd said: this service allows sftp connections only)"
printf '%s\n' "$probe" | grep -q '^put-exit=0$' || { printf '%s\n' "$probe" >&2; fail "the key could not create a file"; }
printf '%s\n' "$probe" | grep -q '^rm-exit=[1-9]' || { printf '%s\n' "$probe" >&2; fail "the key could delete"; }
printf '%s\n' "$probe" | grep -q 'remote delete .*Permission denied' || { printf '%s\n' "$probe" >&2; fail "delete was not refused by the server"; }
printf '%s\n' "$probe" | grep -q '^rename-exit=[1-9]' || { printf '%s\n' "$probe" >&2; fail "the key could rename"; }
printf '%s\n' "$probe" | grep -q 'remote rename .*Permission denied' || { printf '%s\n' "$probe" >&2; fail "rename was not refused by the server"; }
docker exec "$SINK" test -e "$RDIR/probe-one" || fail "the probe file vanished"
docker exec "$SINK" test ! -e "$RDIR/probe-two" || fail "the probe file was renamed"
echo "ok: create works; delete and rename are refused and the file is still there"

echo "== an interrupted upload leaves a short object; the retry is a NEW object and the short one is stepped over"
out=$(bk -- bash -c '
  . /helpers.sh
  mkdir -p /tmp/smoke; /opt/backup/run-backup.sh >/dev/null && /opt/backup/run-copy.sh >/dev/null
  f=$(ls /tmp/smoke/*.tar.age); n=${f##*/}; b=${n%.tar.age}
  # Simulate the interruption: this destination lets the account overwrite, so cut the complete copy short.
  head -c 50 "$f" > /tmp/short
  echo "put /tmp/short '"$RDIR"'/$n" | '"$SFTP"' -b - fd@sink
  echo "short: $(remote_size sink "$n")"
  /opt/backup/run-copy.sh; echo "copy-exit=$?"
  echo "sizes: original=$(remote_size sink "$n") retry=$(remote_size sink "$b.r1.tar.age") local=$(wc -c < "$f" | tr -d " ")"
  /opt/backup/run-copy.sh; echo "second-copy-exit=$?"
  echo "second-retry: [$(remote_size sink "$b.r2.tar.age")]"
' || true)
printf '%s\n' "$out" | grep -q '^short: 50$' || { echo "$out" >&2; fail "the setup did not leave a 50-byte object on the destination"; }
printf '%s\n' "$out" | grep -q '"uploaded":"1","stepped_over":"1"' || { echo "$out" >&2; fail "the short object was not stepped over by a new upload"; }
printf '%s\n' "$out" | grep -q '"event":"copy_stepped_over"' || fail "stepping over a short object should be logged"
printf '%s\n' "$out" | grep -q '^copy-exit=0$' || fail "the retry run should succeed"
a=$(printf '%s\n' "$out" | sed -n 's/^sizes: original=\([0-9]*\) retry=\([0-9]*\) local=\([0-9]*\)$/\1 \2 \3/p')
[ "${a%% *}" = "50" ] || { echo "$out" >&2; fail "the short object should be left exactly as it was (got: $a)"; }
set -- $a
[ "$2" = "$3" ] && [ -n "$2" ] || { echo "$out" >&2; fail "the retry object is not the full size (got: $a)"; }
printf '%s\n' "$out" | grep -q '^second-retry: \[\]$' || fail "a second run made another retry although a complete object exists"
echo "ok: 50-byte object left in place, complete copy written beside it ($2 bytes), nothing more on the next run"

echo "== write-once: nothing can be reopened, a double interruption still does not stall the chain"
out=$(bk -e BACKUP_SSH_HOST=wosink -- bash -c '
  . /helpers.sh
  mkdir -p /tmp/smoke; /opt/backup/run-backup.sh >/dev/null
  f=$(ls /tmp/smoke/*.tar.age); n=${f##*/}; b=${n%.tar.age}
  head -c 50 "$f" > /tmp/short; head -c 80 "$f" > /tmp/short2
  echo "put /tmp/short '"$RDIR"'/$n" | '"$SFTP"' -b - fd@wosink
  echo "put /tmp/short2 '"$RDIR"'/$b.r1.tar.age" | '"$SFTP"' -b - fd@wosink
  echo "put /tmp/short2 '"$RDIR"'/$n" | '"$SFTP"' -b - fd@wosink 2>&1; echo "overwrite-exit=$?"
  /opt/backup/run-copy.sh; echo "copy-exit=$?"
  echo "sizes: plain=$(remote_size wosink "$n") r1=$(remote_size wosink "$b.r1.tar.age") r2=$(remote_size wosink "$b.r2.tar.age") local=$(wc -c < "$f" | tr -d " ")"
  echo "backup: $b"
' || true)
printf '%s\n' "$out" | grep -q '^overwrite-exit=[1-9]' || { echo "$out" >&2; fail "the write-once destination let a file be reopened; the premise of this test is gone"; }
printf '%s\n' "$out" | grep -q '^copy-exit=0$' || { echo "$out" >&2; fail "the copy should step over two short objects and succeed"; }
printf '%s\n' "$out" | grep -q '"stepped_over":"2"' || { echo "$out" >&2; fail "expected two short objects stepped over"; }
a=$(printf '%s\n' "$out" | sed -n 's/^sizes: plain=\([0-9]*\) r1=\([0-9]*\) r2=\([0-9]*\) local=\([0-9]*\)$/\1 \2 \3 \4/p')
set -- $a
[ "$1" = "50" ] && [ "$2" = "80" ] && [ "$3" = "$4" ] && [ -n "$3" ] || { echo "$out" >&2; fail "unexpected sizes on the write-once destination: $a"; }
backup_stem=$(printf '%s\n' "$out" | sed -n 's/^backup: //p')
echo "ok: plain 50, r1 80 (both untouched), r2 $3 (complete)"

echo "== a retry object restores; a short object is rejected before it can reach a database"
docker cp "$WOSINK:$RDIR/$backup_stem.r2.tar.age" "$(winpath "$work")/retry.tar.age"
docker cp "$WOSINK:$RDIR/$backup_stem.tar.age" "$(winpath "$work")/short.tar.age"
bash infra/restore-backup.sh "$work/retry.tar.age" --identity "$work/k/age.key" --db "${RESTORED}_r" > "$work/restore2.out" 2>&1 \
  || { cat "$work/restore2.out" >&2; fail "restoring from the retry object failed"; }
grep -q "^OK: restored into '${RESTORED}_r'" "$work/restore2.out" || { cat "$work/restore2.out" >&2; fail "the retry object did not restore"; }
if bash infra/restore-backup.sh "$work/short.tar.age" --identity "$work/k/age.key" --db "${RESTORED}_s" > "$work/restore3.out" 2>&1; then
  cat "$work/restore3.out" >&2; fail "a short object was restored"
fi
grep -q 'could not decrypt\|wrong key, or the file is damaged\|not a tar' "$work/restore3.out" || { cat "$work/restore3.out" >&2; fail "the short object was rejected, but not for the expected reason"; }
[ "$(psql_pg -d postgres -c "select count(*) from pg_database where datname = '${RESTORED}_s'")" = "0" ] \
  || fail "rejecting a short object still created a database"
echo "ok: the retry object restored; the 50-byte object was refused and created nothing"

echo "== the probe tells the policies apart, and fails when the restriction is not in force"
policy_of() { # HOST -> output and exit code of verify-destination.sh against that host
  bk -e "BACKUP_SSH_HOST=$1" -- /opt/backup/verify-destination.sh || true
}
v=$(bk -e BACKUP_SSH_HOST=sink -- /opt/backup/verify-destination.sh; echo "exit=$?") || true
printf '%s\n' "$v" | grep -q 'RESULT: OK, policy no-delete' || { echo "$v" >&2; fail "expected policy no-delete on the no-delete sink"; }
printf '%s\n' "$v" | grep -q '^exit=0$' || fail "the probe should pass on the no-delete sink"
v=$(bk -e BACKUP_SSH_HOST=wosink -- /opt/backup/verify-destination.sh; echo "exit=$?") || true
printf '%s\n' "$v" | grep -q 'RESULT: OK, policy write-once' || { echo "$v" >&2; fail "expected policy write-once on the write-once sink"; }
v=$(bk -e BACKUP_SSH_HOST=opensink -- /opt/backup/verify-destination.sh; echo "exit=$?") || true
printf '%s\n' "$v" | grep -q 'RESULT: FAIL' || { echo "$v" >&2; fail "the probe passed a destination whose key can delete"; }
printf '%s\n' "$v" | grep -q '^exit=1$' || fail "the probe should exit 1 on an open destination"
echo "ok: no-delete, write-once recognised; an open destination fails"

echo "== no recipient key: the backup fails closed, writes nothing, and says why"
out=$(bk -v "$(winpath "$PWD/infra/backup/recipients.txt"):/placeholder:ro" -e BACKUP_RECIPIENTS_FILE=/placeholder -- bash -c '
  mkdir -p /tmp/smoke; /opt/backup/run-backup.sh; echo "exit=$?"; echo "files=$(ls /tmp/smoke | wc -l)"' || true)
printf '%s\n' "$out" | grep -q '"error_class":"NoRecipient"' || { echo "$out" >&2; fail "expected a NoRecipient failure"; }
printf '%s\n' "$out" | grep -q '^exit=78$' || fail "a backup with no recipient is a configuration failure and should exit 78"
printf '%s\n' "$out" | grep -q '^files=0$' || fail "a backup with no recipient wrote a file"
echo "ok: refused, nothing written"

echo "== no destination path: refused, because a default would copy somewhere unprotected"
out=$(bk -e BACKUP_SSH_PATH= -- bash -c '
  mkdir -p /tmp/smoke; /opt/backup/run-backup.sh >/dev/null; /opt/backup/run-copy.sh; echo "exit=$?"' || true)
printf '%s\n' "$out" | grep -q '"error_class":"ConfigInvalid"' || { echo "$out" >&2; fail "expected ConfigInvalid for an empty BACKUP_SSH_PATH"; }
printf '%s\n' "$out" | grep -q 'BACKUP_SSH_PATH is empty' || fail "the message should name BACKUP_SSH_PATH"
printf '%s\n' "$out" | grep -q '^exit=78$' || fail "an empty BACKUP_SSH_PATH is a configuration failure and should exit 78"
echo "ok: refused"

echo "== the destination is unreachable: the backup still succeeds, only the copy fails"
docker stop "$SINK" > /dev/null
out=$(bk -- bash -c '
  mkdir -p /tmp/smoke; /opt/backup/run-backup.sh; echo "backup-exit=$?"; /opt/backup/run-copy.sh; echo "copy-exit=$?"
  echo "kept=$(ls /tmp/smoke | wc -l)"' || true)
printf '%s\n' "$out" | grep -q '^backup-exit=0$' || { echo "$out" >&2; fail "the backup should succeed with the destination down"; }
printf '%s\n' "$out" | grep -q '^copy-exit=75$' || fail "a copy that cannot reach the destination is transient and should exit 75"
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
wait_sink "$SINK"
docker rm -f "$LOOP" > /dev/null 2>&1 || true
docker compose run -d --name "$LOOP" --no-deps -T -e "PGDATABASE=$SCRATCH" -e BACKUP_DIR=/tmp/smoke \
  -e BACKUP_SSH_HOST=sink -e BACKUP_SSH_USER=fd -e "BACKUP_SSH_PATH=$RDIR" -e BACKUP_AT=00:00 \
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
n_on_sink=$(docker exec "$SINK" sh -c "ls -1 $RDIR | grep -c 'tar.age\$'")
[ "$n_on_sink" -ge 2 ] || fail "the sink should now hold the scheduler's backup as well (has $n_on_sink)"
docker rm -f "$LOOP" > /dev/null
echo "ok: backed up once, copied after the destination returned, pinged the monitor"

echo "== ssh failures are told apart: a refused key and a changed host key are not a sleeping desktop"
out=$(bk -- bash -c '
  mkdir -p /tmp/smoke; /opt/backup/run-backup.sh > /dev/null
  ssh-keygen -q -t ed25519 -N "" -f /tmp/badkey
  ssh-keygen -q -t ed25519 -N "" -f /tmp/otherhost
  echo "sink $(cut -d" " -f1,2 /tmp/otherhost.pub)" > /tmp/kh_changed
  echo "elsewhere $(cut -d" " -f1,2 /tmp/otherhost.pub)" > /tmp/kh_unknown
  run() { name=$1; shift; o=$(env "$@" /opt/backup/run-copy.sh 2>&1); rc=$?
    cls=$(printf "%s" "$o" | sed -n "s/.*\"error_class\":\"\([A-Za-z]*\)\".*/\1/p" | head -1)
    echo "CASE $name exit=$rc class=$cls"; }
  run refused    BACKUP_SSH_KEY=/tmp/badkey
  run changed    BACKUP_SSH_KNOWN_HOSTS=/tmp/kh_changed
  run unknown    BACKUP_SSH_KNOWN_HOSTS=/tmp/kh_unknown
  run noresolve  BACKUP_SSH_HOST=nosuchhost.invalid
  run closedport BACKUP_SSH_PORT=2222
  run bareip     BACKUP_SSH_HOST=192.168.1.20
  run reachable  X=1' || true)
expect_case() { # NAME EXIT CLASS
  printf '%s\n' "$out" | grep -qx "CASE $1 exit=$2 class=$3" || { printf '%s\n' "$out" >&2; fail "case $1: wanted exit=$2 class=$3"; }
}
expect_case refused 1 AuthRefused
expect_case changed 76 HostKeyChanged
expect_case unknown 78 ConfigInvalid
expect_case noresolve 75 Unreachable
expect_case closedport 75 Unreachable
expect_case bareip 78 ConfigInvalid
expect_case reachable 0 ""
echo "ok: refused key -> AuthRefused, a changed host key -> HostKeyChanged (exit 76), an unpinned one -> ConfigInvalid (exit 78), no such host and a closed port -> Unreachable (exit 75), a bare IP -> ConfigInvalid (exit 78), a healthy copy -> exit 0"

echo "== the probe refuses a bare IP"
out=$(bk -- bash -c 'o=$(env BACKUP_SSH_HOST=192.168.1.20 /opt/backup/verify-destination.sh); echo "exit=$?"; echo "$o"' || true)
printf '%s
' "$out" | grep -q '^exit=1$' || { echo "$out" >&2; fail "the probe should refuse a bare IP"; }
printf '%s
' "$out" | grep -q 'bare IP' || fail "the probe should say why it refused"
echo "ok: refused, with the reason"

echo "== a changed host key tells the monitor ONCE (POST <ping url>/fail); a refused key and an unpinned host never do"
for c in "$LOOP-hk" "$LOOP-rf" "$LOOP-up"; do docker rm -f "$c" > /dev/null 2>&1 || true; done
# Every attempt fails at once and retries every ~2 s, so a signal on every failure would show as many requests.
# The sink's /cgi-bin/hb answers 200 to a POST, as a real monitor does (its static files answer 501).
hk_env=(--no-deps -T -e "PGDATABASE=$SCRATCH" -e BACKUP_DIR=/tmp/smoke -e BACKUP_SSH_HOST=sink -e BACKUP_SSH_USER=fd -e "BACKUP_SSH_PATH=$RDIR"
  -e BACKUP_AT=00:00 -e BACKUP_TICK_SECONDS=1 -e BACKUP_RETRY_SECONDS=2 -e BACKUP_RETRY_CAP_SECONDS=2 -e BACKUP_RETRY_LONG_CAP_SECONDS=2)
docker compose run -d --name "$LOOP-hk" "${hk_env[@]}" -e BACKUP_PING_URL=http://sink:8080/cgi-bin/hb/hk -e BACKUP_SSH_KNOWN_HOSTS=/tmp/kh   backup bash -c 'mkdir -p /tmp/smoke && ssh-keygen -q -t ed25519 -N "" -f /tmp/o && echo "sink $(cut -d" " -f1,2 /tmp/o.pub)" > /tmp/kh && exec /opt/backup/backup-loop.sh' > /dev/null 2> "$work/err.log"
docker compose run -d --name "$LOOP-rf" "${hk_env[@]}" -e BACKUP_PING_URL=http://sink:8080/cgi-bin/hb/rf -e BACKUP_SSH_KEY=/tmp/badkey   backup bash -c 'mkdir -p /tmp/smoke && ssh-keygen -q -t ed25519 -N "" -f /tmp/badkey && exec /opt/backup/backup-loop.sh' > /dev/null 2> "$work/err.log"
docker compose run -d --name "$LOOP-up" "${hk_env[@]}" -e BACKUP_PING_URL=http://sink:8080/cgi-bin/hb/up -e BACKUP_SSH_KNOWN_HOSTS=/tmp/kh   backup bash -c 'mkdir -p /tmp/smoke && ssh-keygen -q -t ed25519 -N "" -f /tmp/o && echo "elsewhere $(cut -d" " -f1,2 /tmp/o.pub)" > /tmp/kh && exec /opt/backup/backup-loop.sh' > /dev/null 2> "$work/err.log"
sleep 20
sinklog=$(docker logs "$SINK" 2>&1)
n_hk=$(docker logs "$LOOP-hk" 2>&1 | grep -c '"event":"copy_failed".*"error_class":"HostKeyChanged"' || true)
n_up_cls=$(docker logs "$LOOP-up" 2>&1 | grep -c '"error_class":"ConfigInvalid"' || true)
n_rf_cls=$(docker logs "$LOOP-rf" 2>&1 | grep -c '"error_class":"AuthRefused"' || true)
[ "$n_hk" -ge 4 ] || { docker logs "$LOOP-hk" 2>&1 | tail -n 12 >&2; fail "the changed-key loop should have failed at least 4 times in 20 s, got $n_hk"; }
[ "$n_rf_cls" -ge 3 ] || fail "the refused-key loop should have failed as AuthRefused at least 3 times, got $n_rf_cls"
[ "$n_up_cls" -ge 1 ] || fail "the unpinned-host loop should have failed as ConfigInvalid"
n_sig=$(printf '%s
' "$sinklog" | grep -c 'url:/cgi-bin/hb/hk/fail' || true)
[ "$n_sig" = "1" ] || { printf '%s
' "$sinklog" | tail -n 20 >&2; fail "$n_hk changed-key failures should have signalled the monitor exactly once, got $n_sig"; }
! printf '%s
' "$sinklog" | grep -q 'url:/cgi-bin/hb/rf' || fail "a refused key reached the monitor, and only a changed host key may"
! printf '%s
' "$sinklog" | grep -q 'url:/cgi-bin/hb/up' || fail "an unpinned host reached the monitor, and only a changed host key may"
docker logs "$LOOP-hk" 2>&1 | grep -qE '"event":"copy_fail_signal(led|_failed)"' || fail "the signal was sent but the scheduler logged nothing about it"
for c in "$LOOP-hk" "$LOOP-rf" "$LOOP-up"; do docker rm -f "$c" > /dev/null; done
echo "ok: $n_hk changed-key failures, exactly $n_sig signal to the monitor; $n_rf_cls refused-key and $n_up_cls unpinned-host failures, none"

echo "== each failure is retried as it deserves: transient soon, the rest slowly, configuration not at all"
for c in "$LOOP-cfg" "$LOOP-tr" "$LOOP-lg"; do docker rm -f "$c" > /dev/null 2>&1 || true; done
# Base 2 s, transient cap 2 s, long cap 600 s. Without the split, all three would retry every ~2 s.
loop_env=(--no-deps -T -e "PGDATABASE=$SCRATCH" -e BACKUP_DIR=/tmp/smoke -e BACKUP_SSH_USER=fd -e "BACKUP_SSH_PATH=$RDIR"
  -e BACKUP_AT=00:00 -e BACKUP_TICK_SECONDS=1 -e BACKUP_RETRY_SECONDS=2 -e BACKUP_RETRY_CAP_SECONDS=2 -e BACKUP_RETRY_LONG_CAP_SECONDS=600)
# configuration: the committed placeholder has no key -> NoRecipient -> exit 78 -> one attempt, then wait for the next slot
docker compose run -d --name "$LOOP-cfg" "${loop_env[@]}" -e BACKUP_SSH_HOST=sink \
  -v "$(winpath "$PWD/infra/backup/recipients.txt"):/placeholder:ro" -e BACKUP_RECIPIENTS_FILE=/placeholder \
  backup bash -c 'mkdir -p /tmp/smoke && exec /opt/backup/backup-loop.sh' > /dev/null 2> "$work/err.log"
# transient: the host does not resolve -> Unreachable -> exit 75 -> retried at the short cap
docker compose run -d --name "$LOOP-tr" "${loop_env[@]}" -e BACKUP_SSH_HOST=nosuchhost.invalid \
  backup bash -c 'mkdir -p /tmp/smoke && exec /opt/backup/backup-loop.sh' > /dev/null 2> "$work/err.log"
# long: the key is refused -> AuthRefused -> exit 1 -> backs off 2, 4, 8, 16 s toward the long cap
docker compose run -d --name "$LOOP-lg" "${loop_env[@]}" -e BACKUP_SSH_HOST=sink -e BACKUP_SSH_KEY=/tmp/badkey \
  backup bash -c 'mkdir -p /tmp/smoke && ssh-keygen -q -t ed25519 -N "" -f /tmp/badkey && exec /opt/backup/backup-loop.sh' > /dev/null 2> "$work/err.log"
sleep 16
n_cfg=$(docker logs "$LOOP-cfg" 2>&1 | grep -c '"event":"backup_failed"' || true)
n_tr=$(docker logs "$LOOP-tr" 2>&1 | grep -c '"event":"copy_failed"' || true)
n_lg=$(docker logs "$LOOP-lg" 2>&1 | grep -c '"event":"copy_failed"' || true)
docker logs "$LOOP-cfg" 2>&1 | grep -q '"event":"backup_not_retried"' || { docker logs "$LOOP-cfg" >&2; fail "a configuration failure should say it is not being retried"; }
docker logs "$LOOP-tr" 2>&1 | grep -q '"error_class":"Unreachable"' || fail "the transient loop should be failing as Unreachable"
docker logs "$LOOP-lg" 2>&1 | grep -q '"error_class":"AuthRefused"' || fail "the long loop should be failing as AuthRefused"
for c in "$LOOP-cfg" "$LOOP-tr" "$LOOP-lg"; do docker rm -f "$c" > /dev/null; done
[ "$n_cfg" = "1" ] || fail "configuration failure: wanted exactly 1 attempt in 16 s, got $n_cfg"
[ "$n_tr" -ge 5 ] || fail "transient failure: wanted at least 5 attempts in 16 s at a 2 s cap, got $n_tr"
[ "$n_lg" -ge 2 ] && [ "$n_lg" -le 4 ] || fail "long-cap failure: wanted 2 to 4 attempts in 16 s (2, 4, 8 s backoff), got $n_lg"
echo "ok: in 16 s a missing key was tried $n_cfg time, an unreachable host $n_tr times, a refused key $n_lg times"

echo "== no destination configured: the copy is recorded as skipped, never as a success"
bk -e BACKUP_SSH_HOST= -- bash -c 'mkdir -p /tmp/smoke; /opt/backup/run-backup.sh >/dev/null; /opt/backup/run-copy.sh; echo "exit=$?"' > "$work/nc.out" || true
grep -q '"event":"copy_not_configured"' "$work/nc.out" || { cat "$work/nc.out" >&2; fail "expected copy_not_configured"; }
grep -q '^exit=0$' "$work/nc.out" || fail "an unconfigured copy should exit 0"
h=$(health)
[ "$h" = "backup=ok copy=not_configured copy_failed=False copy_error=NotConfigured" ] || fail "unexpected backup health: $h"
echo "ok: $h"

echo "OK: M2 slice 2 smoke passed (encrypted, kept locally, copied over SFTP, restored with the private key only; no shell/delete/rename; an interrupted upload is stepped over by a new retry object on every policy, and a short object never restores; the probe tells no-delete from write-once and fails an open destination; fails closed with no recipient or path; a down destination fails the copy and not the backup; the scheduler catches up and pings; a refused key, a changed host key and an unreachable host are told apart and retried as each deserves; unconfigured is reported as such)"
