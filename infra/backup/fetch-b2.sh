#!/usr/bin/env bash
# Download one backup from B2 to stdout, for infra/restore-backup.sh --from-b2 (the restore drill, on the laptop,
# with the READ key). Version-aware: a backup someone hid (a delete marker) is still found, because the listing is
# of versions, not of current objects. Messages go to stderr; the bytes, and nothing else, to stdout.
#   fetch-b2.sh            the newest backup under B2_PREFIX
#   fetch-b2.sh NAME       that one (<db>_<stamp>.tar.age)
set -uo pipefail
here=$(dirname "$0")
# shellcheck source=lib.sh
. "$here/lib.sh"
PREFIX=${B2_PREFIX:-flowdesk/}
say() { echo "$*" >&2; }
for v in B2_BUCKET B2_ENDPOINT B2_KEY_ID B2_APPLICATION_KEY; do
  [ -n "${!v:-}" ] || { say "FAIL: $v is empty"; exit 1; }
done
export S3_KEY_ID=$B2_KEY_ID S3_SECRET=$B2_APPLICATION_KEY
work=$(mktemp -d /tmp/fetch.XXXXXX)
trap 'rm -rf "$work"' EXIT

c=$(list_all_versions "$B2_BUCKET" "$PREFIX" "$work/l")
[ "$c" = ok ] || { say "FAIL: listing the bucket: $c"; exit 1; }
if [ -n "${1:-}" ]; then
  [[ $1 =~ ^[A-Za-z0-9_]+_[0-9]{8}T[0-9]{6}Z\.tar\.age$ ]] || { say "FAIL: not a backup name: $1"; exit 1; }
  pick=$(awk -v k="$PREFIX$1" '$1 == "V" && $2 == k { print $2, $3, $4 }' "$work/l" | head -n 1)
else
  pick=$(awk -v p="$PREFIX" '$1 == "V" && index($2, p) == 1 { print $2, $3, $4 }' "$work/l" | sort -r | head -n 1)
fi
[ -n "$pick" ] || { say "FAIL: no such backup in $B2_BUCKET/$PREFIX"; exit 1; }
read -r key size ver <<< "$pick"
code=$(s3_request GET "$B2_BUCKET/$key?versionId=$ver" "$work/obj")
[ "$(classify_http "$code")" = ok ] || { say "FAIL: download refused or failed ($(classify_http "$code"))"; exit 1; }
[ "$(wc -c < "$work/obj" | tr -d ' ')" = "$size" ] || { say "FAIL: downloaded size differs from the listed $size bytes"; exit 1; }
delete_markers "$PREFIX" < "$work/l" | grep -qx "$key" && say "NOTE: $key carries a delete marker (someone hid it); restoring the locked version anyway"
say "fetched ${key#"$PREFIX"} ($size bytes, version $ver)"
cat "$work/obj"
