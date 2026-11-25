#!/usr/bin/env bash
# The probe for the real B2 bucket (2026-11-25). Run it once after setup, then quarterly and after any rclone bump.
# It proves what the copy relies on but never does itself. Runbook: docs/BACKUP.md
#
#   write mode (default), on the stack machine, with the write key from .env:
#     docker compose -p flowdesk exec -u postgres backup /opt/backup/verify-destination.sh
#   read mode, on the laptop, with the read key (B2_KEY_ID / B2_APPLICATION_KEY set to the READ key for this run):
#     ... /opt/backup/verify-destination.sh --read
#
# Write mode, in order:
#   1. the key holds exactly the write capabilities and is restricted to the bucket (no deleteFiles, no
#      bypassGovernance, no readFiles, ...);
#   2. a probe upload to probe/ succeeds;
#   3. it is locked GOVERNANCE for B2_LOCK_DAYS days (the bucket's default retention);
#   4. deleting that VERSION is refused;
#   5. deleting it with x-amz-bypass-governance-retention is refused;
#   6. a same-name upload leaves the original version listed, at its size (B2 cannot refuse the upload; it keeps
#      the old version, which is what "cannot overwrite" means here);
#   7. a hide (delete without a version id) is RECORDED: Backblaze documents that writeFiles allows it. Either way the
#      locked version must still be listed afterwards;
#   8. the bucket against the budget.
# Read mode: the read capabilities; the newest backup under the prefix downloads at its listed size; an upload is
# refused.
#
# Probe objects are a few bytes, under probe/, and stay locked for the lock period: expected, and counted in the
# budget. Nothing provider-written is printed: classes and our own words only (B2's error bodies echo the key ID).
set -uo pipefail
here=$(dirname "$0")
# shellcheck source=lib.sh
. "$here/lib.sh"

mode=write
[ "${1:-}" != "--read" ] || mode=read
LOCK_DAYS=${B2_LOCK_DAYS:-30}
PREFIX=${B2_PREFIX:-flowdesk/}
BUDGET=${BACKUP_BUDGET_BYTES:-10000000000}
work=$(mktemp -d /tmp/probe.XXXXXX)
trap 'rm -rf "$work"' EXIT
bad=0
ok() { echo "  OK    $1"; }
no() { echo "  FAIL  $1"; bad=1; }
note() { echo "  NOTE  $1"; }
finish() {
  if [ "$bad" -eq 0 ]; then echo "RESULT: OK ($mode key)"; exit 0; fi
  echo "RESULT: FAIL ($mode key)"; exit 1
}

for v in B2_BUCKET B2_ENDPOINT B2_KEY_ID B2_APPLICATION_KEY; do
  [ -n "${!v:-}" ] || { echo "  FAIL  $v is empty (.env; docs/BACKUP.md)"; echo "RESULT: FAIL ($mode key)"; exit 1; }
done
s3_region "$B2_ENDPOINT" > /dev/null || { echo "  FAIL  B2_ENDPOINT is not https://s3.<region>.backblazeb2.com"; echo "RESULT: FAIL ($mode key)"; exit 1; }
export S3_KEY_ID=$B2_KEY_ID S3_SECRET=$B2_APPLICATION_KEY
echo "== $mode key, bucket $B2_BUCKET"

echo "-- 1. what the key may do"
export CAP_INFO_FILE=$work/caps
cls=$(check_key "$B2_BUCKET" "$mode")
[ ! -s "$CAP_INFO_FILE" ] || echo "        $(cat "$CAP_INFO_FILE")"
if [ "$cls" = ok ]; then ok "exactly the $mode capabilities, restricted to $B2_BUCKET"; else no "the key: $cls"; finish; fi

rclone_put() { # LOCAL KEY
  RCLONE_CONFIG_B2_TYPE=s3 RCLONE_CONFIG_B2_PROVIDER=Other RCLONE_CONFIG_B2_ENDPOINT="$B2_ENDPOINT" \
    RCLONE_CONFIG_B2_REGION="$(s3_region "$B2_ENDPOINT")" RCLONE_CONFIG_B2_ACCESS_KEY_ID="$S3_KEY_ID" \
    RCLONE_CONFIG_B2_SECRET_ACCESS_KEY="$S3_SECRET" RCLONE_CONFIG=/dev/null \
    timeout 300 rclone copyto --no-check-dest --s3-no-head --s3-no-check-bucket --retries 1 -q "$1" "B2:$B2_BUCKET/$2" 2> "$work/rclone.err"
  classify_rclone $? "$work/rclone.err"
}
versions_of() { # KEY -> "V key size version latest" lines for that key
  local c
  c=$(list_all_versions "$B2_BUCKET" "$1" "$work/l") || true
  [ "$c" = ok ] || { echo "LISTFAIL $c"; return 0; }
  awk -v k="$1" '$2 == k' "$work/l"
}

if [ "$mode" = read ]; then
  echo "-- 2. the newest backup downloads"
  c=$(list_all_versions "$B2_BUCKET" "$PREFIX" "$work/l")
  [ "$c" = ok ] || { no "listing the bucket: $c"; finish; }
  newest=$(awk '$1 == "V" { print $2, $3, $4 }' "$work/l" | sort -r | head -n 1)
  if [ -z "$newest" ]; then note "no backup under $PREFIX yet: nothing to download"; else
    read -r key size ver <<< "$newest"
    code=$(s3_request GET "$B2_BUCKET/$key?versionId=$ver" "$work/got")
    if [ "$(classify_http "$code")" = ok ] && [ "$(wc -c < "$work/got" | tr -d ' ')" = "$size" ]; then
      ok "downloaded ${key##*/} ($size bytes, the listed size)"
    else no "downloading ${key##*/}: $(classify_http "$code")"; fi
  fi
  echo "-- 3. the read key cannot write"
  echo probe > "$work/p"
  code=$(s3_request PUT "$B2_BUCKET/probe/read-key-$(date -u +%Y%m%dT%H%M%SZ).probe" "$work/out" -T "$work/p")
  if [ "$(classify_http "$code")" = AuthRefused ]; then ok "an upload is refused ($code)"; else no "an upload was NOT refused (HTTP $code)"; fi
  finish
fi

stamp=$(date -u +%Y%m%dT%H%M%SZ)
key="probe/flowdesk-probe-$stamp.probe"
head -c 48 /dev/urandom > "$work/p1"
head -c 96 /dev/urandom > "$work/p2"

echo "-- 2. upload"
cls=$(rclone_put "$work/p1" "$key")
if [ "$cls" = ok ]; then ok "uploaded $key (48 bytes)"; else no "upload: $cls"; finish; fi
first=$(versions_of "$key" | awk '$1 == "V" { print $4 }' | head -n 1)
[ -n "$first" ] || { no "the upload is not in the listing"; finish; }

echo "-- 3. locked"
code=$(s3_request GET "$B2_BUCKET/$key?retention" "$work/ret.xml")
if [ "$(classify_http "$code")" = ok ] && retention_ok "$work/ret.xml" "$(date +%s)" "$LOCK_DAYS"; then
  ok "GOVERNANCE for at least $((LOCK_DAYS - 1)) more days"
else no "not locked GOVERNANCE for $LOCK_DAYS days (HTTP $code): the bucket's default retention"; fi

echo "-- 4. deleting the version is refused"
code=$(s3_request DELETE "$B2_BUCKET/$key?versionId=$first" "$work/out")
case $(classify_http "$code") in
  AuthRefused) ok "refused ($code)" ;;
  ok) no "the version was DELETED (HTTP $code): the key can delete" ;;
  *) no "unexpected answer (HTTP $code)" ;;
esac

echo "-- 5. deleting it with bypass-governance is refused"
code=$(s3_request DELETE "$B2_BUCKET/$key?versionId=$first" "$work/out" -H 'x-amz-bypass-governance-retention: true')
case $(classify_http "$code") in
  AuthRefused) ok "refused ($code)" ;;
  ok) no "the version was DELETED with bypass (HTTP $code): the key holds bypassGovernance" ;;
  *) no "unexpected answer (HTTP $code)" ;;
esac

echo "-- 6. a same-name upload leaves the original"
cls=$(rclone_put "$work/p2" "$key")
note "the same-name upload: $cls (B2 keeps the old version either way)"
if versions_of "$key" | awk -v v="$first" '$1 == "V" && $4 == v && $3 == 48' | grep -q .; then
  ok "the original version is still there, 48 bytes"
else no "the original version is gone or changed"; fi

echo "-- 7. a hide (delete without a version id)"
code=$(s3_request DELETE "$B2_BUCKET/$key" "$work/out")
case $(classify_http "$code") in
  ok) note "allowed ($code), as documented for writeFiles: a delete marker under probe/ (the copy ignores probe/)" ;;
  AuthRefused) note "refused ($code)" ;;
  *) note "unexpected answer (HTTP $code)" ;;
esac
if versions_of "$key" | awk -v v="$first" '$1 == "V" && $4 == v' | grep -q .; then
  ok "the locked version is still listed after the hide"
else no "the locked version is gone after the hide"; fi

echo "-- 8. the budget"
c=$(list_all_versions "$B2_BUCKET" "" "$work/all")
if [ "$c" = ok ]; then
  b=$(bucket_bytes < "$work/all")
  st=$(budget_state "$b" "$BUDGET")
  if [ "$st" = over ]; then no "$b bytes: over 95% of $BUDGET"; else ok "$b bytes of $BUDGET ($st)"; fi
else no "listing the bucket: $c"; fi

finish
