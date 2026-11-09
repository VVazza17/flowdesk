#!/usr/bin/env bash
# Decrypt a scheduled backup (<db>_<stamp>.tar.age) and restore it with infra/restore.sh.
# Runbook: docs/BACKUP.md
#   bash infra/restore-backup.sh <file.tar.age> --identity <age private key file> --db NAME [--container NAME]
#
# Run it where Docker, this repo, a .env and the age PRIVATE key are: in practice the desktop the copies
# land on. The private key is never on the machine that makes the backups. Decryption uses the `age` on
# PATH if there is one, and otherwise the one inside the backup image, so nothing needs installing beyond
# Docker. Everything after decryption is restore.sh unchanged: it verifies the checksum and the version
# trap, refuses a non-empty target, and uses the documented Timescale procedure.
set -euo pipefail

usage() { echo "usage: $0 <file.tar.age> --identity <age key file> --db NAME [--container NAME]" >&2; exit 2; }
file="" identity="" db="" container=""
while [ $# -gt 0 ]; do
  case "$1" in
    --identity) identity="${2:?--identity needs a file}"; shift 2 ;;
    --db) db="${2:?--db needs a name}"; shift 2 ;;
    --container) container="${2:?--container needs a name}"; shift 2 ;;
    -*) usage ;;
    *) [ -z "$file" ] || usage; file="$1"; shift ;;
  esac
done
[ -n "$file" ] && [ -n "$identity" ] && [ -n "$db" ] || usage
fail() { echo "FAIL: $1" >&2; exit 1; }
[ -f "$file" ] || fail "no such backup: $file"
[ -f "$identity" ] || fail "no such key file: $identity"
file="$(cd "$(dirname "$file")" && pwd)/$(basename "$file")"
identity="$(cd "$(dirname "$identity")" && pwd)/$(basename "$identity")"

cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

if command -v age > /dev/null 2>&1; then
  age -d -i "$identity" "$file" > "$work/backup.tar" \
    || fail "age could not decrypt $file with that key (wrong key, or the file is damaged)"
else
  # MSYS_NO_PATHCONV: Git Bash on Windows would otherwise rewrite the container paths. cygpath turns the
  # key's path into one Docker Desktop understands.
  key_for_docker="$(cygpath -m "$identity" 2> /dev/null || echo "$identity")"
  MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -T -v "$key_for_docker:/identity:ro" \
    --entrypoint age backup -d -i /identity < "$file" > "$work/backup.tar" \
    || fail "could not decrypt $file with that key (wrong key, or the file is damaged)"
fi

# Exactly one dump and its manifest. Anything else is not something this tooling wrote.
members=$(tar -tf "$work/backup.tar") || fail "the decrypted data is not a tar archive"
[ "$(printf '%s\n' "$members" | wc -l | tr -d ' ')" = "2" ] \
  && printf '%s\n' "$members" | grep -q '\.dump$' && printf '%s\n' "$members" | grep -q '\.manifest$' \
  || fail "unexpected contents in the backup: $members"
case "$members" in */* | *..*) fail "unsafe path inside the backup archive" ;; esac
tar -xf "$work/backup.tar" -C "$work"
dump=$(ls "$work"/*.dump)

args=("$dump" --db "$db")
[ -z "$container" ] || args+=(--container "$container")
bash infra/restore.sh "${args[@]}"
