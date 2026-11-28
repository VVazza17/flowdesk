#!/usr/bin/env bash
# M2 slice 2 smoke test (IDEA-029): scheduled, encrypted backups with an off-box copy to Backblaze B2, tested end to
# end against a STAND-IN, in CI and locally, with nothing from Backblaze (2026-11-25).
#
# What stands in for B2: versitygw (Apache-2.0, an S3 gateway) with versioning, Object Lock and per-user bucket
# policies, so the buckets behave like the real one in the ways the copy depends on:
#   flowdesk-smoke    Object Lock on, DEFAULT retention GOVERNANCE 30 days; the writer may upload, list and read
#                     retention, and nothing else (no delete, no bypass, no read); the reader may list and download.
#   flowdesk-smoke-t  the same, for the tampering and budget cases, so they cannot spoil the main bucket.
#   flowdesk-open     NO lock, and the writer may delete: the probe must FAIL on it, and the copy must say NotLocked.
# B2's own key check (b2_authorize_account) is stood in for by static JSON files on a tiny HTTP server, one per case
# (the v2 shape, ASSUMED until the real-bucket probe shows the real answer). The same server is the dead-man monitor.
#
# What it does NOT prove, and cannot: Backblaze's semantics (capabilities enforced on S3 calls, lifecycle, caps, path
# style). infra/backup/verify-destination.sh proves those against the real bucket; the slice closes on the drill in
# docs/BACKUP.md.
#
# Safety: everything runs against a scratch database and one-off containers. The running `backup` service, its
# `backups` volume and the real database are never touched. Run from the repo root (needs a .env).
set -euo pipefail

set -a; . ./.env; set +a
. "$(dirname "$0")/lib.sh"
export MSYS_NO_PATHCONV=1   # Git Bash on Windows would otherwise rewrite container paths

STANDIN=flowdesk-smoke-standin   # alias: standin
AUTHSTUB=flowdesk-smoke-authstub # alias: authstub
LOOP=flowdesk-smoke-loop
SCRATCH=flowdesk_smoke_s2
RESTORED=flowdesk_smoke_s2_restored
EP=http://standin:7070
ROOT_KEY=fdsmokeroot0001 ROOT_SECRET=fdsmokerootsecret0001
# Distinctive values, so "is the secret anywhere it must not be" can be checked by grep.
W_ID=fdsmokewriterid0001 W_SECRET=fdsmokewritersecretZZQ7
R_ID=fdsmokereaderid0001 R_SECRET=fdsmokereadersecretZZQ8
work=$(mktemp -d)

winpath() { cygpath -m "$1" 2> /dev/null || printf '%s' "$1"; }  # a path Docker Desktop understands
psql_pg() { docker compose exec -T postgres psql -X -q -U "$POSTGRES_USER" -v ON_ERROR_STOP=1 -tA "$@" < /dev/null; }

cleanup() {
  local rc=$?
  docker rm -f "$STANDIN" "$AUTHSTUB" "$LOOP" "$LOOP-cfg" "$LOOP-tr" "$LOOP-lg" "$LOOP-al" > /dev/null 2>&1 || true
  for d in "$SCRATCH" "$RESTORED"; do psql_pg -d postgres -c "DROP DATABASE IF EXISTS $d WITH (FORCE)" > /dev/null 2>&1 || true; done
  if [ "$rc" -ne 0 ] && [ -s "$work/err.log" ]; then echo "--- last container stderr:" >&2; tail -n 20 "$work/err.log" >&2; fi
  rm -rf "$work"
  exit "$rc"
}
trap cleanup EXIT

docker compose up -d --build
wait_ready

echo "== the library's logic (schedule, catch-up, backoff, retention, recipients, S3 answers, capabilities, budget)"
docker compose run --rm --no-deps -T -v "$(winpath "$PWD/infra/backup/selftest.sh"):/selftest.sh:ro" \
  --entrypoint bash backup /selftest.sh > "$work/selftest.out" 2> "$work/err.log" || { cat "$work/selftest.out"; fail "selftest failed"; }
grep -q '^FAIL' "$work/selftest.out" && { cat "$work/selftest.out"; fail "selftest reported a failure"; }
echo "ok: $(grep -c '^ok:' "$work/selftest.out") assertions"

echo "== a throwaway age key"
mkdir -p "$work/k"
docker compose run --rm --no-deps -T -v "$(winpath "$work/k"):/k" --entrypoint sh backup -ec '
  age-keygen -o /k/age.key 2> /dev/null
  age-keygen -y /k/age.key > /k/recipients.txt
  chmod a+r /k/*' > /dev/null 2> "$work/err.log"
[ "$(grep -c '^age1' "$work/k/recipients.txt")" = "1" ] || fail "no age public key was made"
! grep -q 'AGE-SECRET-KEY' "$work/k/recipients.txt" || fail "the recipients file must hold the PUBLIC key only"
export BACKUP_RECIPIENTS_FILE="$(winpath "$work/k/recipients.txt")"
# A comments-only recipients file: the shape of the placeholder before setup. Not the committed file, which
# holds the real public key since drill step A13 (2026-11-28).
printf '%s
' '# no key yet' '# age1....' > "$work/k/placeholder.txt"

echo "== a scratch database, migrated"
psql_pg -d postgres -c "DROP DATABASE IF EXISTS $SCRATCH WITH (FORCE)" > /dev/null
psql_pg -d postgres -c "CREATE DATABASE $SCRATCH" > /dev/null
docker compose run --rm --no-deps -T \
  -e "DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${SCRATCH}" \
  migrate > /dev/null 2> "$work/err.log"

echo "== the stand-in for B2 (versitygw) and for b2_authorize_account and the monitor (busybox httpd)"
network=$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}' "$(docker compose ps -q postgres)")
docker rm -f "$STANDIN" "$AUTHSTUB" > /dev/null 2>&1 || true
# DECISION: pinned by digest is not done for these test-only images; versitygw is pinned to a release tag.
docker run -d --name "$STANDIN" --network "$network" --network-alias standin \
  -e "ROOT_ACCESS_KEY=$ROOT_KEY" -e "ROOT_SECRET_KEY=$ROOT_SECRET" --entrypoint sh versity/versitygw:v1.11.0 \
  -c 'mkdir -p /tmp/iam /tmp/vers /tmp/data; exec versitygw --iam-dir /tmp/iam --port :7070 --admin-port :7071 posix --versioning-dir /tmp/vers /tmp/data' > /dev/null
mkdir -p "$work/stub/www/cgi-bin"
caps() { # BUCKET-OR-null CAPABILITIES -> a b2_authorize_account (v2) answer
  printf '{"accountId":"smoke","allowed":{"bucketId":"x","bucketName":%s,"capabilities":[%s],"namePrefix":null},"apiUrl":"http://x","authorizationToken":"not-a-real-token"}\n' "$1" "$2"
}
W='"listBuckets","listAllBucketNames","listFiles","writeFiles","readFileRetentions"'
R='"listBuckets","listAllBucketNames","listFiles","readFiles","readFileRetentions"'
caps '"flowdesk-smoke"' "$W" > "$work/stub/www/write.json"
caps '"flowdesk-smoke-t"' "$W" > "$work/stub/www/write-t.json"
caps '"flowdesk-open"' "$W" > "$work/stub/www/write-open.json"
caps '"flowdesk-smoke"' "$R" > "$work/stub/www/read.json"
caps '"flowdesk-smoke"' "$W,\"deleteFiles\"" > "$work/stub/www/toobroad.json"
caps null "$W" > "$work/stub/www/unrestricted.json"
printf '{"apiInfo":{"storageApi":{"allowed":[]}}}\n' > "$work/stub/www/v3.json"
echo ok > "$work/stub/www/ping"
printf '#!/bin/sh\necho "Status: 401 Unauthorized"\necho "Content-type: application/json"\necho\necho "{\\"status\\":401}"\n' > "$work/stub/www/cgi-bin/refused"
printf '#!/bin/sh\necho "Content-type: text/plain"\necho\necho ok\n' > "$work/stub/www/cgi-bin/hb"
chmod 755 "$work/stub/www/cgi-bin/"*
docker run -d --name "$AUTHSTUB" --network "$network" --network-alias authstub -v "$(winpath "$work/stub/www"):/www:ro" \
  alpine:3.23 sh -c 'apk add --no-cache busybox-extras > /dev/null && exec busybox-extras httpd -f -p 8080 -h /www -vv' > /dev/null
deadline=$((SECONDS + 60))
until docker logs "$STANDIN" 2>&1 | grep -q ':7070'; do
  [ $SECONDS -lt $deadline ] || { docker logs "$STANDIN" 2>&1 | tail -n 20 >&2; fail "the stand-in did not start"; }
  sleep 1
done
for u in "$W_ID:$W_SECRET" "$R_ID:$R_SECRET"; do
  docker run --rm --network "$network" versity/versitygw:v1.11.0 admin -a "$ROOT_KEY" -s "$ROOT_SECRET" \
    --er http://standin:7071 create-user -a "${u%%:*}" -s "${u#*:}" -r user > /dev/null 2> "$work/err.log" || fail "creating a stand-in user"
done

# Root setup through curl, from the backup image itself (no other client image).
cat > "$work/setup.sh" << SETUPEOF
set -eu
md5_b64() { local hex b="" i; hex=\$(printf '%s' "\$1" | md5sum | cut -c1-32); for ((i = 0; i < 32; i += 2)); do b+="\\\\x\${hex:i:2}"; done; printf "\$b" | base64; }
sig=(--aws-sigv4 "aws:amz:us-east-1:s3" -u "$ROOT_KEY:$ROOT_SECRET")
put() { local code; code=\$(curl -sS -o /tmp/r -w '%{http_code}' "\${sig[@]}" -X PUT "\$@"); case \$code in 2??) ;; *) echo "setup failed: \$code \$*" >&2; cat /tmp/r >&2; exit 1 ;; esac; }
lock='<ObjectLockConfiguration><ObjectLockEnabled>Enabled</ObjectLockEnabled><Rule><DefaultRetention><Mode>GOVERNANCE</Mode><Days>30</Days></DefaultRetention></Rule></ObjectLockConfiguration>'
for b in flowdesk-smoke flowdesk-smoke-t; do
  put -H "x-amz-bucket-object-lock-enabled: true" "$EP/\$b"
  put -H "Content-MD5: \$(md5_b64 "\$lock")" --data-binary "\$lock" "$EP/\$b?object-lock"
  pol='{"Version":"2012-10-17","Statement":[
    {"Effect":"Allow","Principal":{"AWS":["$W_ID"]},"Action":["s3:PutObject","s3:ListBucket","s3:ListBucketVersions","s3:GetObjectRetention"],"Resource":["arn:aws:s3:::'\$b'","arn:aws:s3:::'\$b'/*"]},
    {"Effect":"Allow","Principal":{"AWS":["$R_ID"]},"Action":["s3:GetObject","s3:GetObjectVersion","s3:ListBucket","s3:ListBucketVersions","s3:GetObjectRetention"],"Resource":["arn:aws:s3:::'\$b'","arn:aws:s3:::'\$b'/*"]}]}'
  put --data-binary "\$pol" "$EP/\$b?policy"
done
put "$EP/flowdesk-open"
pol='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"AWS":["$W_ID"]},"Action":["s3:*"],"Resource":["arn:aws:s3:::flowdesk-open","arn:aws:s3:::flowdesk-open/*"]}]}'
put --data-binary "\$pol" "$EP/flowdesk-open?policy"
echo setup-ok
SETUPEOF
docker compose run --rm --no-deps -T -v "$(winpath "$work/setup.sh"):/setup.sh:ro" --entrypoint bash backup /setup.sh \
  > "$work/setup.out" 2> "$work/err.log" || { cat "$work/setup.out" "$work/err.log" >&2; fail "stand-in setup failed"; }
grep -qx setup-ok "$work/setup.out" || fail "stand-in setup did not finish"
echo "ok: three buckets (two locked GOVERNANCE 30 days, one open), a writer and a reader"

# Root-side reads of the stand-in, for the checks (the smoke does not trust the code it is checking).
root_curl() { # PATH_AND_QUERY [extra curl args]
  local p=$1; shift
  docker compose run --rm --no-deps -T --entrypoint curl backup -sS --aws-sigv4 "aws:amz:us-east-1:s3" \
    -u "$ROOT_KEY:$ROOT_SECRET" "$@" "$EP/$p" 2> "$work/err.log"
}
# One-off backup containers. The `backups` volume is never used: BACKUP_DIR is a path inside each one-off.
bk() { # [extra docker compose run flags] -- command...
  local flags=()
  while [ "$1" != "--" ]; do flags+=("$1"); shift; done
  shift
  docker compose run --rm --no-deps -T -e "PGDATABASE=$SCRATCH" -e BACKUP_DIR=/tmp/smoke \
    -e B2_BUCKET=flowdesk-smoke -e "B2_ENDPOINT=$EP" -e B2_REGION=us-east-1 \
    -e "B2_KEY_ID=$W_ID" -e "B2_APPLICATION_KEY=$W_SECRET" -e B2_AUTHORIZE_URL=http://authstub:8080/write.json \
    "${flags[@]+"${flags[@]}"}" backup "$@" 2> "$work/err.log"
}

echo "== a backup is encrypted and kept locally; the copy uploads it, locked; a second copy sends nothing"
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
printf '%s\n' "$out" | grep '"event":"copy_succeeded"' | sed -n 2p | grep -q '"uploaded":"0"' || fail "the second copy should upload nothing"
printf '%s\n' "$out" | grep '"event":"copy_succeeded"' | sed -n 2p | grep -q '"budget":"ok"' || fail "the budget should be ok"
[ "$(printf '%s\n' "$out" | sed -n '/^PLAINTEXT-LEFT:/{n;p;}')" = "0" ] || fail "plaintext dump, manifest or partial file left behind"
printf '%s\n' "$out" | sed -n '/^MAGIC:/{n;p;}' | grep -q '^age-encryption.org/v1' || fail "the kept file is not an age file"
[ "$(printf '%s\n' "$out" | sed -n '/^PGDMP-IN-FILE:/{n;p;}')" = "0" ] || fail "the encrypted file contains a plaintext pg_dump header"
name=$(printf '%s\n' "$out" | sed -n '/^LOCAL:/,/^PLAINTEXT-LEFT:/p' | grep '\.tar\.age$')
listing=$(root_curl "flowdesk-smoke?versions&prefix=flowdesk/")
[ "$(printf '%s' "$listing" | grep -o '<Key>[^<]*</Key>' | sort -u)" = "<Key>flowdesk/$name</Key>" ] || { echo "$listing" >&2; fail "expected exactly flowdesk/$name in the bucket"; }
[ "$(printf '%s' "$listing" | grep -o '<Version>' | wc -l | tr -d ' ')" = "1" ] || fail "expected exactly one version (uploaded once)"
ret=$(root_curl "flowdesk-smoke/flowdesk/$name?retention")
printf '%s' "$ret" | grep -q '<Mode>GOVERNANCE</Mode>' || { echo "$ret" >&2; fail "the uploaded backup is not locked GOVERNANCE"; }
echo "ok: flowdesk/$name, one version, GOVERNANCE (uploaded once, nothing the second time)"

echo "== the copy restores on 'the laptop', with the READ key and the private age key only (restore-backup.sh --from-b2)"
B2_BUCKET=flowdesk-smoke B2_ENDPOINT=$EP B2_REGION=us-east-1 B2_READ_KEY_ID=$R_ID B2_READ_APPLICATION_KEY=$R_SECRET \
  bash infra/restore-backup.sh --from-b2 --identity "$work/k/age.key" --db "$RESTORED" > "$work/restore.out" 2>&1 \
  || { cat "$work/restore.out" >&2; fail "restore-backup.sh --from-b2 failed"; }
grep -q "^OK: restored into '$RESTORED'" "$work/restore.out" || { cat "$work/restore.out" >&2; fail "restore did not report OK"; }
grep -q "fetched $name" "$work/restore.out" || { cat "$work/restore.out" >&2; fail "the newest backup was not the one fetched"; }
want=$(psql_pg -d "$SCRATCH" -c "select version_num from alembic_version")
[ "$(psql_pg -d "$RESTORED" -c "select version_num from alembic_version")" = "$want" ] || fail "alembic revision differs after restore"
echo "ok: fetched $name and restored it into $RESTORED"
! B2_BUCKET=flowdesk-smoke B2_ENDPOINT=$EP B2_REGION=us-east-1 B2_READ_KEY_ID=$W_ID B2_READ_APPLICATION_KEY=$W_SECRET \
  bash infra/restore-backup.sh --from-b2 --identity "$work/k/age.key" --db "${RESTORED}_x" > /dev/null 2>&1 \
  || fail "the WRITE key could download a backup (it must have no read)"
echo "ok: the write key cannot download"

echo "== the probe: OK on the locked bucket with each key, FAIL on an open one"
v=$(bk -- /opt/backup/verify-destination.sh; echo "exit=$?") || true
printf '%s\n' "$v" | grep -q '^RESULT: OK (write key)' || { echo "$v" >&2; fail "the probe should pass with the write key on the locked bucket"; }
for want in "refused (403)" "GOVERNANCE for at least 29 more days" "the original version is still there" "the locked version is still listed after the hide"; do
  printf '%s\n' "$v" | grep -q "$want" || { echo "$v" >&2; fail "the probe did not report: $want"; }
done
v=$(bk -e "B2_KEY_ID=$R_ID" -e "B2_APPLICATION_KEY=$R_SECRET" -e B2_AUTHORIZE_URL=http://authstub:8080/read.json \
  -- /opt/backup/verify-destination.sh --read; echo "exit=$?") || true
printf '%s\n' "$v" | grep -q '^RESULT: OK (read key)' || { echo "$v" >&2; fail "the probe should pass with the read key"; }
printf '%s\n' "$v" | grep -q 'an upload is refused' || fail "the read key's upload should be refused"
v=$(bk -e B2_BUCKET=flowdesk-open -e B2_AUTHORIZE_URL=http://authstub:8080/write-open.json -- /opt/backup/verify-destination.sh; echo "exit=$?") || true
printf '%s\n' "$v" | grep -q '^RESULT: FAIL' || { echo "$v" >&2; fail "the probe passed a bucket with no lock where the key can delete"; }
printf '%s\n' "$v" | grep -q 'the version was DELETED' || { echo "$v" >&2; fail "the probe should name the delete it was allowed"; }
printf '%s\n' "$v" | grep -q '^exit=1$' || fail "the probe should exit 1 on the open bucket"
echo "ok: write key OK, read key OK, the open bucket FAILS (deleted, not locked)"

echo "== the key check refuses a key that could undo the protection, and pages (exit 76)"
expect_copy() { # NAME AUTHFILE-OR-URL EXIT CLASS [extra -e flags]
  local name=$1 url=$2 want_rc=$3 want_cls=$4; shift 4
  local o rc cls
  o=$(bk -e "B2_AUTHORIZE_URL=$url" "$@" -- bash -c 'mkdir -p /tmp/smoke; /opt/backup/run-backup.sh > /dev/null; /opt/backup/run-copy.sh; echo "exit=$?"' || true)
  rc=$(printf '%s\n' "$o" | sed -n 's/^exit=//p')
  cls=$(printf '%s\n' "$o" | sed -n 's/.*"event":"copy_failed".*"error_class":"\([A-Za-z]*\)".*/\1/p' | head -1)
  [ "$rc" = "$want_rc" ] && [ "$cls" = "$want_cls" ] || { printf '%s\n' "$o" >&2; fail "case $name: wanted exit=$want_rc class=$want_cls, got exit=$rc class=$cls"; }
  echo "ok: $name -> $want_cls (exit $want_rc)"
}
expect_copy "a key with deleteFiles" http://authstub:8080/toobroad.json 76 CapabilityTooBroad
expect_copy "a key not restricted to the bucket" http://authstub:8080/unrestricted.json 76 CapabilityTooBroad
expect_copy "a refused key (401)" http://authstub:8080/cgi-bin/refused 76 AuthRefused
expect_copy "an answer in another shape (v3)" http://authstub:8080/v3.json 78 ConfigInvalid
expect_copy "a key for another bucket" http://authstub:8080/write-t.json 78 ConfigInvalid
expect_copy "a bucket with no lock" http://authstub:8080/write-open.json 76 NotLocked -e B2_BUCKET=flowdesk-open
expect_copy "a wrong secret on the S3 calls" http://authstub:8080/write.json 76 AuthRefused -e B2_APPLICATION_KEY=wrongsecretwrongsecret
expect_copy "no such endpoint" http://authstub:8080/write.json 75 Unreachable -e B2_ENDPOINT=http://nosuchhost.invalid:7070
expect_copy "the budget would be passed" http://authstub:8080/write.json 76 OverBudget -e BACKUP_BUDGET_BYTES=1000

echo "== a delete marker under the backup prefix is tampering: uploads still go up, then the run fails and pages"
o=$(bk -e B2_BUCKET=flowdesk-smoke-t -e B2_AUTHORIZE_URL=http://authstub:8080/write-t.json -- bash -c '
  mkdir -p /tmp/smoke; /opt/backup/run-backup.sh > /dev/null; /opt/backup/run-copy.sh; echo "first=$?"; ls -1 /tmp/smoke' || true)
printf '%s\n' "$o" | grep -q '^first=0$' || { echo "$o" >&2; fail "the first copy to the second bucket should succeed"; }
tname=$(printf '%s\n' "$o" | grep '\.tar\.age$' | head -1)
root_curl "flowdesk-smoke-t/flowdesk/$tname" -X DELETE -o /dev/null -w '%{http_code}' > "$work/hide.code" || true
grep -q '^20[04]$' "$work/hide.code" || fail "root could not hide the object (stand-in setup)"
o=$(bk -e B2_BUCKET=flowdesk-smoke-t -e B2_AUTHORIZE_URL=http://authstub:8080/write-t.json -- bash -c '
  mkdir -p /tmp/smoke; /opt/backup/run-backup.sh > /dev/null; /opt/backup/run-copy.sh; echo "exit=$?"' || true)
printf '%s\n' "$o" | grep -q '"error_class":"Tampered"' || { echo "$o" >&2; fail "a delete marker under flowdesk/ was not reported as Tampered"; }
printf '%s\n' "$o" | grep -q '^exit=76$' || fail "Tampered should exit 76 (pages)"
[ "$(root_curl "flowdesk-smoke-t?versions&prefix=flowdesk/" | grep -o '<Version>' | wc -l | tr -d ' ')" = "2" ] \
  || fail "the new backup should still have been uploaded beside the hidden one"
echo "ok: Tampered (exit 76); the hidden version is still there and the new backup went up"

echo "== no recipient key: the backup fails closed, writes nothing, and says why"
out=$(bk -v "$(winpath "$work/k/placeholder.txt"):/placeholder:ro" -e BACKUP_RECIPIENTS_FILE=/placeholder -- bash -c '
  mkdir -p /tmp/smoke; /opt/backup/run-backup.sh; echo "exit=$?"; echo "files=$(ls /tmp/smoke | wc -l)"' || true)
printf '%s\n' "$out" | grep -q '"error_class":"NoRecipient"' || { echo "$out" >&2; fail "expected a NoRecipient failure"; }
printf '%s\n' "$out" | grep -q '^exit=78$' || fail "a backup with no recipient is a configuration failure and should exit 78"
printf '%s\n' "$out" | grep -q '^files=0$' || fail "a backup with no recipient wrote a file"
echo "ok: refused, nothing written"

echo "== B2 is unreachable: the backup still succeeds, only the copy fails (transient)"
docker stop "$STANDIN" > /dev/null
out=$(bk -- bash -c '
  mkdir -p /tmp/smoke; /opt/backup/run-backup.sh; echo "backup-exit=$?"; /opt/backup/run-copy.sh; echo "copy-exit=$?"
  echo "kept=$(ls /tmp/smoke | wc -l)"' || true)
printf '%s\n' "$out" | grep -q '^backup-exit=0$' || { echo "$out" >&2; fail "the backup should succeed with B2 down"; }
printf '%s\n' "$out" | grep -q '^copy-exit=75$' || { echo "$out" >&2; fail "a copy that cannot reach B2 is transient and should exit 75"; }
printf '%s\n' "$out" | grep -q '"error_class":"Unreachable"' || { echo "$out" >&2; fail "expected an Unreachable failure"; }
printf '%s\n' "$out" | grep -q '^kept=1$' || fail "the local backup should be kept"
docker start "$STANDIN" > /dev/null
deadline=$((SECONDS + 60))
until root_curl "flowdesk-smoke?versions" > /dev/null 2>&1 && root_curl "flowdesk-smoke?versions" | grep -q ListVersionsResult; do
  [ $SECONDS -lt $deadline ] || fail "the stand-in did not come back"
  sleep 1
done
echo "ok: a failed copy is not a failed backup"

echo "== health: both steps, from the rows the scripts wrote (sizes, bucket bytes, budget, copied-through)"
health() {
  docker compose run --rm --no-deps -T \
    -e "DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${SCRATCH}" \
    api python - 2> "$work/err.log" << 'PYEOF'
import asyncio, json, os
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
    body = svc.assemble_data(state, datetime.now(UTC), successes, settings.backup_stale_after())
    print(json.dumps(body["backups"]))
    await eng.dispose()

asyncio.run(main())
PYEOF
}
h=$(health)
pyq() { printf '%s' "$h" | python -c "import json,sys; b=json.load(sys.stdin); print($1)"; }
[ "$(pyq "b['backup']['state'], b['copy']['state'], b['copy']['last_attempt_failed'], b['copy']['last_error_class']")" = "ok ok True Unreachable" ] \
  || fail "unexpected backup health: $h"
[ "$(pyq "b['copy']['destination'], b['copy']['budget_state'] is None or b['copy']['budget_state'], b['copy']['tampered']")" = "b2 True False" ] \
  || fail "unexpected copy fields: $h"
[ "$(pyq "len(b['backup']['sizes_weekly']) >= 1 and b['backup']['sizes_weekly'][0]['bytes'] > 0")" = "True" ] || fail "no weekly backup size: $h"
echo "ok: backup ok, copy ok (last attempt Unreachable), destination b2, a weekly size of $(pyq "b['backup']['sizes_weekly'][0]['bytes']") bytes"

echo "== the scheduler catches up by itself, uploads, and pings the monitor"
docker rm -f "$LOOP" > /dev/null 2>&1 || true
docker compose run -d --name "$LOOP" --no-deps -T -e "PGDATABASE=$SCRATCH" -e BACKUP_DIR=/tmp/smoke \
  -e B2_BUCKET=flowdesk-smoke -e "B2_ENDPOINT=$EP" -e B2_REGION=us-east-1 -e "B2_KEY_ID=$W_ID" -e "B2_APPLICATION_KEY=$W_SECRET" \
  -e B2_AUTHORIZE_URL=http://authstub:8080/write.json -e BACKUP_AT=00:00 \
  -e BACKUP_TICK_SECONDS=1 -e BACKUP_RETRY_SECONDS=2 -e BACKUP_RETRY_CAP_SECONDS=2 \
  -e BACKUP_PING_URL=http://authstub:8080/ping backup bash -c 'mkdir -p /tmp/smoke && exec /opt/backup/backup-loop.sh' > /dev/null 2> "$work/err.log"
deadline=$((SECONDS + 90))
until docker logs "$LOOP" 2>&1 | grep -q '"event":"copy_pinged"'; do
  docker inspect -f '{{.State.Running}}' "$LOOP" | grep -q true || { docker logs "$LOOP" >&2; fail "the scheduler stopped"; }
  [ $SECONDS -lt $deadline ] || { docker logs "$LOOP" 2>&1 | tail -n 15 >&2; fail "the scheduler never completed a copy"; }
  sleep 2
done
sleep 6   # several more ticks: it must not back up again, because today's slot has been served
logs=$(docker logs "$LOOP" 2>&1)
[ "$(printf '%s\n' "$logs" | grep -c '"event":"backup_succeeded"')" = "1" ] || { printf '%s\n' "$logs" >&2; fail "expected exactly one backup from the scheduler"; }
printf '%s\n' "$logs" | grep -q '"offbox":"b2"' || fail "the scheduler should say the off-box copy is b2"
docker logs "$AUTHSTUB" 2>&1 | grep -q 'url:/ping' || fail "the monitor URL was never requested"
docker rm -f "$LOOP" > /dev/null
echo "ok: backed up once, uploaded, pinged the monitor"

echo "== an alert class tells the monitor ONCE (POST <ping url>/fail, with the class); a transient failure never does"
docker rm -f "$LOOP-al" "$LOOP-tr" > /dev/null 2>&1 || true
al_env=(--no-deps -T -e "PGDATABASE=$SCRATCH" -e BACKUP_DIR=/tmp/smoke -e B2_BUCKET=flowdesk-smoke -e "B2_ENDPOINT=$EP" -e B2_REGION=us-east-1
  -e "B2_KEY_ID=$W_ID" -e "B2_APPLICATION_KEY=$W_SECRET" -e BACKUP_AT=00:00 -e BACKUP_TICK_SECONDS=1
  -e BACKUP_RETRY_SECONDS=2 -e BACKUP_RETRY_CAP_SECONDS=2 -e BACKUP_RETRY_LONG_CAP_SECONDS=2)
docker compose run -d --name "$LOOP-al" "${al_env[@]}" -e B2_AUTHORIZE_URL=http://authstub:8080/toobroad.json \
  -e BACKUP_PING_URL=http://authstub:8080/cgi-bin/hb/al backup bash -c 'mkdir -p /tmp/smoke && exec /opt/backup/backup-loop.sh' > /dev/null 2> "$work/err.log"
docker compose run -d --name "$LOOP-tr" "${al_env[@]}" -e B2_AUTHORIZE_URL=http://authstub:8080/write.json -e B2_ENDPOINT=http://nosuchhost.invalid:7070 \
  -e BACKUP_PING_URL=http://authstub:8080/cgi-bin/hb/tr backup bash -c 'mkdir -p /tmp/smoke && exec /opt/backup/backup-loop.sh' > /dev/null 2> "$work/err.log"
sleep 20
stub=$(docker logs "$AUTHSTUB" 2>&1)
n_al=$(docker logs "$LOOP-al" 2>&1 | grep -c '"event":"copy_failed".*"error_class":"CapabilityTooBroad"' || true)
n_tr=$(docker logs "$LOOP-tr" 2>&1 | grep -c '"error_class":"Unreachable"' || true)
[ "$n_al" -ge 4 ] || { docker logs "$LOOP-al" 2>&1 | tail -n 12 >&2; fail "the alert loop should have failed at least 4 times in 20 s, got $n_al"; }
[ "$n_tr" -ge 3 ] || fail "the transient loop should have failed at least 3 times, got $n_tr"
n_sig=$(printf '%s\n' "$stub" | grep -c 'url:/cgi-bin/hb/al/fail' || true)
[ "$n_sig" = "1" ] || { printf '%s\n' "$stub" | tail -n 20 >&2; fail "$n_al alert failures should have signalled the monitor exactly once, got $n_sig"; }
! printf '%s\n' "$stub" | grep -q 'url:/cgi-bin/hb/tr' || fail "a transient failure reached the monitor"
docker logs "$LOOP-al" 2>&1 | grep -q '"event":"copy_fail_signalled".*"error_class":"CapabilityTooBroad"' || fail "the signal should name the class"
docker rm -f "$LOOP-al" "$LOOP-tr" > /dev/null
echo "ok: $n_al CapabilityTooBroad failures, exactly 1 signal to the monitor; $n_tr Unreachable failures, none"

echo "== each failure is retried as it deserves: transient soon, alerts slowly, configuration not at all"
for c in "$LOOP-cfg" "$LOOP-tr" "$LOOP-lg"; do docker rm -f "$c" > /dev/null 2>&1 || true; done
loop_env=(--no-deps -T -e "PGDATABASE=$SCRATCH" -e BACKUP_DIR=/tmp/smoke -e B2_BUCKET=flowdesk-smoke -e "B2_ENDPOINT=$EP" -e B2_REGION=us-east-1
  -e "B2_KEY_ID=$W_ID" -e "B2_APPLICATION_KEY=$W_SECRET" -e B2_AUTHORIZE_URL=http://authstub:8080/write.json
  -e BACKUP_AT=00:00 -e BACKUP_TICK_SECONDS=1 -e BACKUP_RETRY_SECONDS=2 -e BACKUP_RETRY_CAP_SECONDS=2 -e BACKUP_RETRY_LONG_CAP_SECONDS=600)
docker compose run -d --name "$LOOP-cfg" "${loop_env[@]}" \
  -v "$(winpath "$work/k/placeholder.txt"):/placeholder:ro" -e BACKUP_RECIPIENTS_FILE=/placeholder \
  backup bash -c 'mkdir -p /tmp/smoke && exec /opt/backup/backup-loop.sh' > /dev/null 2> "$work/err.log"
docker compose run -d --name "$LOOP-tr" "${loop_env[@]}" -e B2_ENDPOINT=http://nosuchhost.invalid:7070 \
  backup bash -c 'mkdir -p /tmp/smoke && exec /opt/backup/backup-loop.sh' > /dev/null 2> "$work/err.log"
docker compose run -d --name "$LOOP-lg" "${loop_env[@]}" -e B2_AUTHORIZE_URL=http://authstub:8080/cgi-bin/refused \
  backup bash -c 'mkdir -p /tmp/smoke && exec /opt/backup/backup-loop.sh' > /dev/null 2> "$work/err.log"
sleep 16
n_cfg=$(docker logs "$LOOP-cfg" 2>&1 | grep -c '"event":"backup_failed"' || true)
n_tr=$(docker logs "$LOOP-tr" 2>&1 | grep -c '"event":"copy_failed"' || true)
n_lg=$(docker logs "$LOOP-lg" 2>&1 | grep -c '"event":"copy_failed"' || true)
docker logs "$LOOP-cfg" 2>&1 | grep -q '"event":"backup_not_retried"' || { docker logs "$LOOP-cfg" >&2; fail "a configuration failure should say it is not being retried"; }
docker logs "$LOOP-lg" 2>&1 | grep -q '"error_class":"AuthRefused"' || fail "the long loop should be failing as AuthRefused"
for c in "$LOOP-cfg" "$LOOP-tr" "$LOOP-lg"; do docker rm -f "$c" > /dev/null; done
[ "$n_cfg" = "1" ] || fail "configuration failure: wanted exactly 1 attempt in 16 s, got $n_cfg"
[ "$n_tr" -ge 5 ] || fail "transient failure: wanted at least 5 attempts in 16 s at a 2 s cap, got $n_tr"
[ "$n_lg" -ge 2 ] && [ "$n_lg" -le 4 ] || fail "alert (long-cap) failure: wanted 2 to 4 attempts in 16 s (2, 4, 8 s backoff), got $n_lg"
echo "ok: in 16 s a missing key was tried $n_cfg time, an unreachable B2 $n_tr times, a refused key $n_lg times"

echo "== no bucket configured: the copy is recorded as skipped, never as a success"
bk -e B2_BUCKET= -- bash -c 'mkdir -p /tmp/smoke; /opt/backup/run-backup.sh >/dev/null; /opt/backup/run-copy.sh; echo "exit=$?"' > "$work/nc.out" || true
grep -q '"event":"copy_not_configured"' "$work/nc.out" || { cat "$work/nc.out" >&2; fail "expected copy_not_configured"; }
grep -q '^exit=0$' "$work/nc.out" || fail "an unconfigured copy should exit 0"
h=$(health)
[ "$(pyq "b['copy']['state'], b['copy']['last_error_class']")" = "not_configured NotConfigured" ] || fail "unexpected backup health: $h"
echo "ok: not_configured"

echo "== the secrets are nowhere they must not be: task_runs and /health/data"
leaks=$(psql_pg -d "$SCRATCH" -c "select count(*) from task_runs where coalesce(error_message,'') || coalesce(error_class,'') ~ '($W_SECRET|$R_SECRET|$W_ID|$R_ID|$ROOT_SECRET)'")
[ "$leaks" = "0" ] || fail "a key or key ID is stored in task_runs"
body=$(curl -s --max-time 10 "http://127.0.0.1:${API_PORT:-8000}/health/data")
case "$body" in *"$W_SECRET"* | *"$W_ID"* | *"$R_SECRET"*) fail "/health/data serves a key or key ID" ;; esac
printf '%s\n' "$h" | grep -q "$W_SECRET\|$W_ID" && fail "the health body carries a key"
echo "ok: no key or key ID in task_runs or /health/data"

echo "OK: M2 slice 2 smoke passed (encrypted, kept locally, uploaded to a locked stand-in bucket once, restored on 'the laptop' with the read key and the private key only; the write key cannot read, delete or bypass; the probe passes both keys and fails an open bucket; a key that could undo the protection, a refused key, an unlocked bucket, a hide and an over-budget bucket each page once; transient failures retry soon and never page; fails closed with no recipient; a down B2 fails the copy and not the backup; health reports sizes, bucket bytes and budget; no secret stored or served)"
