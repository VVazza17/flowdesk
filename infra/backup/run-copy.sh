#!/usr/bin/env bash
# The off-box copy: rsync the encrypted backups to the configured machine over SSH. Runs inside the backup
# container. Records itself in task_runs as ops.backup_copy, separately from the backup (a sleeping
# destination is a failed copy, not a failed backup). Runbook: docs/BACKUP.md
#
# Only *.tar.age files are sent, and only ones the destination does not already have. The destination
# directory is chosen by the key on the receiving side (authorized_keys), not by this script: see the
# restricted-key line in docs/BACKUP.md.
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

fail() { # CLASS MESSAGE
  jlog error copy_failed "error_class=$1" "detail=$2"
  task_finish "$id" failed "$1" "$2" || true
  exit 1
}

[ -n "${BACKUP_SSH_USER:-}" ] || fail ConfigInvalid "BACKUP_SSH_USER is empty"
[ -s "$KEY" ] || fail ConfigInvalid "no SSH key: set BACKUP_SSH_KEY_FILE in .env to a private key file"
[ -s "$KNOWN_HOSTS" ] || fail ConfigInvalid "no known_hosts: set BACKUP_SSH_KNOWN_HOSTS_FILE in .env (see docs/BACKUP.md)"
ls "$BACKUP_DIR"/*.tar.age > /dev/null 2>&1 || fail NothingToCopy "there is no backup to copy yet"

# StrictHostKeyChecking=yes against a pinned known_hosts: a changed or unknown host is a failure, never a
# prompt and never a silent accept. BatchMode: nothing may ever wait for input. -F /dev/null: ignore any
# ssh config; everything that matters is on this line.
ssh_cmd="ssh -F /dev/null -i $KEY -p ${BACKUP_SSH_PORT:-22} -o BatchMode=yes -o IdentitiesOnly=yes \
-o StrictHostKeyChecking=yes -o UserKnownHostsFile=$KNOWN_HOSTS -o ConnectTimeout=20 \
-o ServerAliveInterval=15 -o ServerAliveCountMax=4"

errlog=$(mktemp /tmp/copy.XXXXXX)
trap 'rm -f "$errlog"' EXIT

# --ignore-existing: a file that is already there is never touched. No --partial and no --delete: an
# interrupted transfer leaves only rsync's own temporary file, which is removed, so a truncated copy can
# never sit under the real name where --ignore-existing would protect it. Pruning the destination is the
# receiving side's job (docs/BACKUP.md): the restricted key cannot delete.
rsync -rt --no-perms --no-owner --no-group --ignore-existing --timeout=60 \
  --include='*.tar.age' --exclude='*' \
  -e "$ssh_cmd" "$BACKUP_DIR/" "${BACKUP_SSH_USER}@${BACKUP_SSH_HOST}:${BACKUP_SSH_PATH:-.}/" 2> "$errlog"
rc=$?
if [ "$rc" -ne 0 ]; then
  # 255 is ssh itself failing: the host is asleep or unreachable, the key was refused, or the host key
  # changed. Anything else is rsync complaining after a connection was made.
  if [ "$rc" -eq 255 ] || [ "$rc" -eq 12 ] || [ "$rc" -eq 30 ] || [ "$rc" -eq 35 ]; then cls=Unreachable; else cls=RsyncFailed; fi
  fail "$cls" "rsync exit $rc: $(tail -c 300 "$errlog")"
fi

task_finish "$id" succeeded || jlog warning copy_bookkeeping_failed "detail=copy done but task_runs was not updated"
jlog info copy_succeeded "host=$BACKUP_SSH_HOST"

# The external monitor hears about a successful off-box copy. If these pings stop, it alerts: that is the
# missed-run signal, and it lives outside this machine. The URL is a credential and is never logged.
if [ -n "${BACKUP_PING_URL:-}" ]; then
  if curl -fsS -m 10 --retry 2 -o /dev/null "$BACKUP_PING_URL" 2> /dev/null; then
    jlog info copy_pinged
  else
    jlog warning copy_ping_failed "detail=the copy succeeded but the monitor did not accept the ping"
  fi
fi
