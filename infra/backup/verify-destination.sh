#!/usr/bin/env bash
# Probe the real destination for the one property the whole design rests on: THIS KEY CANNOT DELETE OR RENAME.
# Runs inside the backup container, with the same key, host-key pinning and path the copy step uses.
#   docker compose exec -u postgres backup /opt/backup/verify-destination.sh
#
# It reports, it does not change the destination's rules. It leaves one small file behind, named
# flowdesk-probe-<UTC stamp>.tar.age (the retention rule ignores that name); delete it on the destination as
# an administrator. That it cannot be deleted from here is the point.
#
# Exit status: 0 when the key cannot delete or rename and can create; 1 otherwise.
#   policy "write-once":  create works; overwrite, append, delete, rename and mkdir are all refused.
#   policy "no-delete":   create works; delete and rename are refused, but an existing file CAN be overwritten,
#                         so a stolen key can still destroy a backup by truncating it.
set -uo pipefail
here=$(dirname "$0")
# shellcheck source=lib.sh
. "$here/lib.sh"

KEY=${BACKUP_SSH_KEY:-/run/backup/id_key}
KNOWN_HOSTS=${BACKUP_SSH_KNOWN_HOSTS:-/run/backup/known_hosts}
for v in BACKUP_SSH_HOST BACKUP_SSH_USER BACKUP_SSH_PATH; do
  [ -n "${!v:-}" ] || { echo "FAIL: $v is empty (set it in .env)"; exit 1; }
done
if host_is_bare_ip "$BACKUP_SSH_HOST"; then echo "FAIL: $(bare_ip_message "$BACKUP_SSH_HOST")"; exit 1; fi
[ -s "$KEY" ] && [ -s "$KNOWN_HOSTS" ] || { echo "FAIL: no SSH key or known_hosts mounted (BACKUP_SSH_KEY_FILE, BACKUP_SSH_KNOWN_HOSTS_FILE)"; exit 1; }

rdir=${BACKUP_SSH_PATH%/}
target="${BACKUP_SSH_USER}@${BACKUP_SSH_HOST}"
sftp_opts=(-q -F /dev/null -i "$KEY" -P "${BACKUP_SSH_PORT:-22}"
  -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$KNOWN_HOSTS"
  -o ConnectTimeout=20 -o ServerAliveInterval=15 -o ServerAliveCountMax=4)

work=$(mktemp -d /tmp/verify.XXXXXX)
trap 'rm -rf "$work"' EXIT
stamp=$(date -u +%Y%m%dT%H%M%SZ)
probe="flowdesk-probe-$stamp.tar.age"
head -c 100 /dev/urandom > "$work/a"
head -c 200 /dev/urandom > "$work/b"

try() { printf '%s\n' "$1" | timeout 120 sftp "${sftp_opts[@]}" -b - "$target" > "$work/out" 2> "$work/err"; }
size_of() { # NAME -> bytes on the destination, or empty
  printf 'ls -l "%s"\n' "$rdir" | timeout 120 sftp "${sftp_opts[@]}" -b - "$target" 2> /dev/null |
    awk -v n="$1" 'NF >= 9 { b = $NF; sub(/^.*[\/\\]/, "", b); if (b == n) print $(NF-4) }'
}
bad=0
row() { printf '%-44s %s\n' "$1" "$2"; }

try "ls -l \"$rdir\"" || { row "connect and list $rdir" "FAIL: $(tail -c 200 "$work/err")"; exit 1; }
row "connect and list $rdir" "ok"

try "put \"$work/a\" \"$rdir/$probe\"" || { row "create a new file" "FAIL: $(tail -c 200 "$work/err")"; exit 1; }
[ "$(size_of "$probe")" = "100" ] || { row "create a new file" "FAIL: uploaded 100 bytes, destination reports '$(size_of "$probe")'"; exit 1; }
row "create a new file (100 bytes, size confirmed)" "ok"

if try "put \"$work/b\" \"$rdir/$probe\""; then
  overwrite=allowed
  row "overwrite it with 200 bytes" "ALLOWED  (a stolen key could truncate existing backups)"
else
  overwrite=refused
  row "overwrite it with 200 bytes" "refused  (good)"
fi

if try "rm \"$rdir/$probe\""; then
  row "delete it" "ALLOWED  <-- the key can delete: the restriction is NOT in force"
  bad=1
else
  row "delete it" "refused  (required)"
fi

if [ "$bad" -eq 0 ]; then
  if try "rename \"$rdir/$probe\" \"$rdir/$probe.moved\""; then
    row "rename it" "ALLOWED  <-- the key can rename: the restriction is NOT in force"
    bad=1
  else
    row "rename it" "refused  (required)"
  fi
fi

if try "mkdir \"$rdir/flowdesk-probe-dir-$stamp\""; then
  mkdir_result=allowed
  row "create a subfolder" "allowed"
  try "rmdir \"$rdir/flowdesk-probe-dir-$stamp\"" || row "  (and it could not be removed)" "delete the folder flowdesk-probe-dir-$stamp on the destination"
else
  mkdir_result=refused
  row "create a subfolder" "refused"
fi

echo
if [ "$bad" -ne 0 ]; then
  echo "RESULT: FAIL. This key can delete or rename on the destination. Fix its permissions (docs/BACKUP.md) before trusting this destination."
  exit 1
fi
if [ "$overwrite" = refused ] && [ "$mkdir_result" = refused ]; then
  echo "RESULT: OK, policy write-once. The key can add backups and nothing else."
else
  echo "RESULT: OK, policy no-delete. It cannot delete or rename, but it CAN overwrite (overwrite $overwrite, subfolders $mkdir_result)."
  echo "        Use the write-once variant in docs/BACKUP.md to close that."
fi
echo "Left on the destination: $probe  (delete it as an administrator)"
