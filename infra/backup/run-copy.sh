#!/usr/bin/env bash
# The off-box copy: put the encrypted backups the destination does not have onto it, over SFTP. Runs inside
# the backup container. Records itself in task_runs as ops.backup_copy, separately from the backup (a sleeping
# destination is a failed copy, not a failed backup). Runbook: docs/BACKUP.md
#
# The destination is a Windows machine running OpenSSH Server. Its restriction is NTFS permissions on a
# dedicated account, NOT anything this script enforces: the account may create files in one folder and may
# not delete or rename. So this script is written to need neither:
#   - no upload to a temporary name followed by a rename (rename is denied);
#   - no reliance on overwriting (denied under the write-once variant);
#   - completeness is checked by SIZE after the upload, because nothing else can be checked.
# A file is skipped only when the destination already has that name at the same size.
#
# An interrupted upload leaves a short file under the real name. Where the account may overwrite, the next run
# repairs it. Where it may not (write-once), the copy fails loudly with PartialOnDestination, naming the file:
# an administrator on the destination deletes it, and the next run uploads it whole. That is the price of an
# account that cannot destroy a backup.
set -uo pipefail
here=$(dirname "$0")
# shellcheck source=lib.sh
. "$here/lib.sh"

BACKUP_DIR=${BACKUP_DIR:-/backups}
KEY=${BACKUP_SSH_KEY:-/run/backup/id_key}
KNOWN_HOSTS=${BACKUP_SSH_KNOWN_HOSTS:-/run/backup/known_hosts}

if [ -z "${BACKUP_SSH_HOST:-}" ]; then
  # Visible, not silent: /health/data shows this step as not_configured. Never a success.
  task_skipped ops.backup_copy NotConfigured "BACKUP_SSH_HOST is empty: no off-box destination is configured" || true
  jlog warning copy_not_configured "detail=BACKUP_SSH_HOST is empty; backups are local only"
  exit 0
fi

if ! id=$(task_start ops.backup_copy) || [ -z "$id" ]; then
  jlog error copy_bookkeeping_failed "detail=could not write task_runs; is Postgres up?"
  exit 1
fi

errlog=$(mktemp /tmp/copy.XXXXXX)
trap 'rm -f "$errlog"' EXIT

fail() { # CLASS MESSAGE
  jlog error copy_failed "error_class=$1" "detail=$2"
  task_finish "$id" failed "$1" "$2" || true
  exit 1
}

[ -n "${BACKUP_SSH_USER:-}" ] || fail ConfigInvalid "BACKUP_SSH_USER is empty"
[ -s "$KEY" ] || fail ConfigInvalid "no SSH key: set BACKUP_SSH_KEY_FILE in .env to a private key file"
[ -s "$KNOWN_HOSTS" ] || fail ConfigInvalid "no known_hosts: set BACKUP_SSH_KNOWN_HOSTS_FILE in .env (see docs/BACKUP.md)"
# DECISION: no default path. A default of "." would put the files in whatever directory the account starts in,
# which is not the folder the permissions were set on, and the backups would be copied somewhere unprotected.
[ -n "${BACKUP_SSH_PATH:-}" ] || fail ConfigInvalid "BACKUP_SSH_PATH is empty: set it to the backup folder (a Windows folder looks like /C:/FlowdeskBackups)"
ls "$BACKUP_DIR"/*.tar.age > /dev/null 2>&1 || fail NothingToCopy "there is no backup to copy yet"

rdir=${BACKUP_SSH_PATH%/}
[ -n "$rdir" ] || rdir=/
target="${BACKUP_SSH_USER}@${BACKUP_SSH_HOST}"
# StrictHostKeyChecking=yes against a pinned known_hosts: a changed or unknown host is a failure, never a prompt
# and never a silent accept. BatchMode: nothing may ever wait for input. -F /dev/null: ignore any ssh config.
sftp_opts=(-q -F /dev/null -i "$KEY" -P "${BACKUP_SSH_PORT:-22}"
  -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$KNOWN_HOSTS"
  -o ConnectTimeout=20 -o ServerAliveInterval=15 -o ServerAliveCountMax=4)

# Run sftp commands from stdin; stdout is the answer, stderr goes to $errlog. `timeout` bounds a hung
# destination. sftp exits 255 when ssh itself failed: the host is asleep or unreachable, the key was refused, or
# the host key changed.
sftp_run() { timeout 900 sftp "${sftp_opts[@]}" -b - "$target" 2> "$errlog"; }

classify_and_fail() { # RC WHAT
  local rc=$1 what=$2 cls
  if [ "$rc" -eq 255 ] || [ "$rc" -eq 124 ]; then cls=Unreachable; else cls=SftpFailed; fi
  fail "$cls" "$what failed (exit $rc): $(tail -c 300 "$errlog")"
}

# name -> size for every file the destination lists. A long listing ends "<size> <Mon> <day> <time> <name>",
# so size and name are read from the RIGHT: the owner and group columns can hold spaces on Windows
# ("NT AUTHORITY\SYSTEM"), which would shift every column read from the left. The last column is the FULL PATH
# as the server prints it (/srv/backups/file here, probably /C:/FlowdeskBackups/file on Windows), so the name is
# only what follows the last slash or backslash.
declare -A remote
list_remote() {
  local out rc
  remote=()
  out=$(printf 'ls -l "%s"\n' "$rdir" | sftp_run)
  rc=$?
  [ "$rc" -eq 0 ] || classify_and_fail "$rc" "listing $rdir"
  while read -r size name; do
    [ -n "$name" ] && remote["$name"]=$size
  done < <(printf '%s\n' "$out" | awk 'NF >= 9 && $1 ~ /^[-dl]/ { n = $NF; sub(/^.*[\/\\]/, "", n); print $(NF-4), n }')
}

list_remote

uploads=()
repairs=()
skipped=0
for f in "$BACKUP_DIR"/*.tar.age; do
  name=${f##*/}
  size=$(wc -c < "$f" | tr -d ' ')
  if [ -n "${remote[$name]+set}" ]; then
    if [ "${remote[$name]}" = "$size" ]; then
      skipped=$((skipped + 1))
      continue
    fi
    repairs+=("$name")   # there, but a different size: an interrupted upload
  fi
  uploads+=("$name")
done

if [ "${#uploads[@]}" -gt 0 ]; then
  batch=""
  for name in "${uploads[@]}"; do
    batch+=$(printf 'put "%s" "%s/%s"\n' "$BACKUP_DIR/$name" "${rdir%/}" "$name")$'\n'
  done
  printf '%s' "$batch" | sftp_run > /dev/null
  rc=$?
  if [ "$rc" -ne 0 ]; then
    if [ "${#repairs[@]}" -gt 0 ] && [ "$rc" -ne 255 ] && [ "$rc" -ne 124 ]; then
      fail PartialOnDestination "the destination has an incomplete copy of ${repairs[*]} and does not let this account overwrite it. Delete that file on the destination as an administrator; the next run uploads it whole. sftp said: $(tail -c 200 "$errlog")"
    fi
    classify_and_fail "$rc" "uploading ${uploads[*]}"
  fi

  # Nothing else can confirm the transfer, so compare sizes with a fresh listing.
  list_remote
  for name in "${uploads[@]}"; do
    want=$(wc -c < "$BACKUP_DIR/$name" | tr -d ' ')
    [ "${remote[$name]:-missing}" = "$want" ] \
      || fail SizeMismatch "$name is ${remote[$name]:-missing} bytes on the destination, expected $want"
  done
fi

task_finish "$id" succeeded || jlog warning copy_bookkeeping_failed "detail=copy done but task_runs was not updated"
jlog info copy_succeeded "host=$BACKUP_SSH_HOST" "uploaded=${#uploads[@]}" "repaired=${#repairs[@]}" "skipped=$skipped"

# The external monitor hears about a successful off-box copy. If these pings stop, it alerts: that is the
# missed-run signal, and it lives outside this machine. The URL is a credential and is never logged.
if [ -n "${BACKUP_PING_URL:-}" ]; then
  if curl -fsS -m 10 --retry 2 -o /dev/null "$BACKUP_PING_URL" 2> /dev/null; then
    jlog info copy_pinged
  else
    jlog warning copy_ping_failed "detail=the copy succeeded but the monitor did not accept the ping"
  fi
fi
