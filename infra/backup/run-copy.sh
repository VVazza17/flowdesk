#!/usr/bin/env bash
# The off-box copy: put the encrypted backups Backblaze B2 does not have yet into the bucket. Runs inside the backup
# container. Records itself in task_runs as ops.backup_copy, separately from the backup (a failed copy is not a
# failed backup). Runbook: docs/BACKUP.md
#
# What protects the copies is on B2's side, not here (Kyle, 2026-11-25):
#   - the bucket has Object Lock with a DEFAULT retention of GOVERNANCE, 30 days, so every upload is locked
#     without this script asking for it;
#   - this machine's key is restricted to the bucket and holds only listBuckets, listAllBucketNames, listFiles,
#     writeFiles and readFileRetentions: no deleteFiles, no bypassGovernance, no readFiles;
#   - lifecycle rules hide each object after 31 days and delete it a day later, and never delete a locked version.
# So this script never deletes, never overwrites (B2 would keep the old version anyway), and checks every run that
# the protection is really there: the key's capabilities, each new object's lock, delete markers nobody made, and
# the bucket against the free tier. Each of those failing pages the owner at once (exit 76, lib.sh).
#
# Secrets: B2_KEY_ID and B2_APPLICATION_KEY come from .env through compose. They reach rclone through its own
# environment and curl on stdin, never a command line, and nothing a provider returns is logged or stored (B2's
# error bodies echo the key ID). Only class names leave this script.
set -uo pipefail
here=$(dirname "$0")
# shellcheck source=lib.sh
. "$here/lib.sh"

BACKUP_DIR=${BACKUP_DIR:-/backups}
PREFIX=${B2_PREFIX:-flowdesk/}
LOCK_DAYS=${B2_LOCK_DAYS:-30}
BUDGET=${BACKUP_BUDGET_BYTES:-10000000000}

if [ -z "${B2_BUCKET:-}" ]; then
  # Visible, not silent: /health/data shows this step as not_configured. Never a success.
  task_skipped ops.backup_copy NotConfigured "B2_BUCKET is empty: no off-box destination is configured" || true
  jlog warning copy_not_configured "detail=B2_BUCKET is empty; backups are local only"
  exit 0
fi

if ! id=$(task_start ops.backup_copy) || [ -z "$id" ]; then
  jlog error copy_bookkeeping_failed "detail=could not write task_runs; is Postgres up?"
  exit "$EX_TRANSIENT"   # nothing was recorded, so nothing is noise; Postgres coming back is what fixes it
fi

work=$(mktemp -d /tmp/copy.XXXXXX)
trap 'rm -rf "$work"' EXIT
export TASK_OUTPUT_BYTES="" TASK_OUTPUT_WATERMARK=""

fail() { # CLASS MESSAGE   (the message is ours, never provider text)
  jlog error copy_failed "error_class=$1" "detail=$2"
  task_finish "$id" failed "$1" "$2" || true
  exit "$(exit_code_for_class "$1")"   # 75 / 76 / 78 / 1: how the scheduler retries, and whether it pages (lib.sh)
}

[ -n "${B2_ENDPOINT:-}" ] || fail ConfigInvalid "B2_ENDPOINT is empty: set it to https://s3.<region>.backblazeb2.com"
[ -n "${B2_KEY_ID:-}" ] && [ -n "${B2_APPLICATION_KEY:-}" ] \
  || fail ConfigInvalid "B2_KEY_ID or B2_APPLICATION_KEY is empty: the write key goes in .env (docs/BACKUP.md)"
s3_region "$B2_ENDPOINT" > /dev/null \
  || fail ConfigInvalid "B2_ENDPOINT is not https://s3.<region>.backblazeb2.com and B2_REGION is not set"
[[ $PREFIX =~ ^[A-Za-z0-9_-]+/$ ]] || fail ConfigInvalid "B2_PREFIX must be a plain folder name ending in /"
[[ $LOCK_DAYS =~ ^[1-9][0-9]*$ ]] || fail ConfigInvalid "B2_LOCK_DAYS must be a whole number of days"
[[ $BUDGET =~ ^[1-9][0-9]*$ ]] || fail ConfigInvalid "BACKUP_BUDGET_BYTES must be a whole number"
ls "$BACKUP_DIR"/*.tar.age > /dev/null 2>&1 || fail NothingToCopy "there is no backup to copy yet"
export S3_KEY_ID=$B2_KEY_ID S3_SECRET=$B2_APPLICATION_KEY

# 1. The key may do what it must and nothing more. A key that gained deleteFiles, bypassGovernance or any other
#    capability, or lost its bucket restriction, is refused and pages: the protection would be gone.
cls=$(check_key "$B2_BUCKET" write)
case $cls in
  ok) ;;
  CapabilityTooBroad) fail CapabilityTooBroad "the B2 key holds a capability outside its list, or is not restricted to $B2_BUCKET" ;;
  CapabilityUnknown) fail ConfigInvalid "b2_authorize_account did not answer in the expected (v2) shape" ;;
  ConfigInvalid) fail ConfigInvalid "the B2 key is restricted to another bucket, or lacks writeFiles, listFiles or readFileRetentions" ;;
  *) fail "$cls" "b2_authorize_account refused or failed ($cls)" ;;
esac

# 2. What the bucket holds: every version and delete marker, all prefixes (the budget counts everything).
listing() { # OUT -> sets nothing; fails the run on a listing error
  local c
  c=$(list_all_versions "$B2_BUCKET" "" "$1")
  [ "$c" = ok ] || fail "$c" "listing the bucket failed ($c)"
}
listing "$work/before"
bytes=$(bucket_bytes < "$work/before")
TASK_OUTPUT_BYTES=$bytes
tampered=$(delete_markers "$PREFIX" < "$work/before" | sort -u | wc -l | tr -d ' ')

# 3. Which local backups go up: young enough to be locked for the full period, and not in the bucket under that
#    name in any version. A name already there at another size is a mismatch to look at, never re-uploaded.
now=$(date +%s)
uploads=()
newest=0
for f in "$BACKUP_DIR"/*.tar.age; do
  name=${f##*/}
  upload_wanted "$name" "$now" "$LOCK_DAYS" || continue
  size=$(wc -c < "$f" | tr -d ' ')
  have=$(awk -v k="$PREFIX$name" '$1 == "V" && $2 == k { print $3 }' "$work/before" | sort -u)
  if [ -n "$have" ]; then
    [ "$have" = "$size" ] || fail SizeMismatch "$PREFIX$name is in the bucket at another size ($have, local $size)"
    continue
  fi
  uploads+=("$name")
done
for k in $(awk -v p="$PREFIX" '$1 == "V" && index($2, p) == 1 { sub(".*/", "", $2); print $2 }' "$work/before"); do
  if e=$(name_epoch "$k") && [ "$e" -gt "$newest" ]; then newest=$e; fi
done

# 4. The budget before uploading: never push the bucket past the free tier's 95% line.
add=0
for name in "${uploads[@]+"${uploads[@]}"}"; do add=$((add + $(wc -c < "$BACKUP_DIR/$name" | tr -d ' '))); done
if [ "$(budget_state $((bytes + add)) "$BUDGET")" = over ]; then
  fail OverBudget "the bucket would hold $((bytes + add)) bytes, past 95% of $BUDGET: nothing uploaded (docs/BACKUP.md, the budget)"
fi

# 5. Upload, then verify each object from the listing (its size) and its retention (GOVERNANCE, the full period).
#    rclone is told not to HEAD the object before or after (the key has no readFiles, by design) and not to create
#    the bucket. Its config is environment only, in this function's own scope.
upload() { # LOCAL REMOTE_KEY ERRFILE
  RCLONE_CONFIG_B2_TYPE=s3 RCLONE_CONFIG_B2_PROVIDER=Other RCLONE_CONFIG_B2_ENDPOINT="$B2_ENDPOINT" \
    RCLONE_CONFIG_B2_REGION="$(s3_region "$B2_ENDPOINT")" RCLONE_CONFIG_B2_ACCESS_KEY_ID="$S3_KEY_ID" \
    RCLONE_CONFIG_B2_SECRET_ACCESS_KEY="$S3_SECRET" RCLONE_CONFIG=/dev/null \
    timeout 1800 rclone copyto --no-check-dest --s3-no-head --s3-no-check-bucket --retries 1 --low-level-retries 3 \
      -q "$1" "B2:$B2_BUCKET/$2" 2> "$3"
}
for name in "${uploads[@]+"${uploads[@]}"}"; do
  upload "$BACKUP_DIR/$name" "$PREFIX$name" "$work/rclone.err"
  cls=$(classify_rclone $? "$work/rclone.err")
  [ "$cls" = ok ] || fail "$cls" "uploading $name failed ($cls)"
  code=$(s3_request GET "$B2_BUCKET/$PREFIX$name?retention" "$work/ret.xml")
  cls=$(classify_http "$code")
  # An object with no retention answers 4xx (no lock configuration); only an unreachable B2 or a refused key is
  # something other than "not locked".
  case $cls in
    ok | Unreachable | AuthRefused) ;;
    *) cls=NotLocked ;;
  esac
  [ "$cls" = ok ] || fail "$cls" "reading the retention of $name failed ($cls)"
  retention_ok "$work/ret.xml" "$(date +%s)" "$LOCK_DAYS" \
    || fail NotLocked "$name was uploaded but is not locked GOVERNANCE for $LOCK_DAYS days: check the bucket's default retention"
done
listing "$work/after"
TASK_OUTPUT_BYTES=$(bucket_bytes < "$work/after")
for name in "${uploads[@]+"${uploads[@]}"}"; do
  want=$(wc -c < "$BACKUP_DIR/$name" | tr -d ' ')
  got=$(awk -v k="$PREFIX$name" '$1 == "V" && $2 == k { print $3 }' "$work/after" | sort -u)
  [ "$got" = "$want" ] || fail SizeMismatch "$name is ${got:-missing} bytes in the bucket, expected $want"
  if e=$(name_epoch "$name") && [ "$e" -gt "$newest" ]; then newest=$e; fi
done
[ "$newest" -eq 0 ] || TASK_OUTPUT_WATERMARK=$newest

# 6. A delete marker under the backup prefix: FlowDesk never makes one, so someone with a key (or the console)
#    hid a backup. The locked version is still there for the rest of its 30 days; the owner hears now.
[ "$tampered" -eq 0 ] || fail Tampered "$tampered object(s) under $PREFIX have a delete marker FlowDesk did not make"
state=$(budget_state "$TASK_OUTPUT_BYTES" "$BUDGET")

task_finish "$id" succeeded || jlog warning copy_bookkeeping_failed "detail=copy done but task_runs was not updated"
jlog info copy_succeeded "uploaded=${#uploads[@]}" "bucket_bytes=$TASK_OUTPUT_BYTES" "budget=$state"
[ "$state" = ok ] || jlog warning copy_budget_warn "bucket_bytes=$TASK_OUTPUT_BYTES" "budget_bytes=$BUDGET"

# The external monitor hears about a verified copy. If these pings stop, it alerts: the missed-run signal, outside
# this machine. The URL is a credential and is never logged.
if [ -n "${BACKUP_PING_URL:-}" ]; then
  if curl -fsS -m 10 --retry 2 -o /dev/null "$BACKUP_PING_URL" 2> /dev/null; then
    jlog info copy_pinged
  else
    jlog warning copy_ping_failed "detail=the copy succeeded but the monitor did not accept the ping"
  fi
fi
