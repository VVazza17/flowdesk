#!/usr/bin/env bash
# Decrypt a scheduled backup (<db>_<stamp>.tar.age) and restore it with infra/restore.sh.
# Runbook: docs/BACKUP.md
#   bash infra/restore-backup.sh <file.tar.age> --identity <age private key file> --db NAME [--container NAME]
#   bash infra/restore-backup.sh --from-b2 [<name>.tar.age] --identity <age key file> --db NAME [--container NAME]
#
# Run it where Docker, this repo, a .env and the age PRIVATE key are: the laptop, for the restore drill (Kyle,
# 2026-11-25). The private key is never on the machine that makes the backups, and is on the laptop's disk only
# for the restore. --from-b2 downloads through the backup image with the READ key: B2_READ_KEY_ID and
# B2_READ_APPLICATION_KEY from the laptop's .env, passed into the container by name (never on a command line),
# plus B2_BUCKET and B2_ENDPOINT. Without a name it takes the newest backup, hidden ones included. Decryption uses the `age` on
# PATH if there is one, and otherwise the one inside the backup image, so nothing needs installing beyond
# Docker. Everything after decryption is restore.sh unchanged: it verifies the checksum and the version
# trap, refuses a non-empty target, and uses the documented Timescale procedure.
set -euo pipefail

usage() { echo "usage: $0 <file.tar.age> | --from-b2 [<name>] --identity <age key file> --db NAME [--container NAME]" >&2; exit 2; }
file="" identity="" db="" container="" from_b2=0 b2_name=""
while [ $# -gt 0 ]; do
  case "$1" in
    --from-b2) from_b2=1; shift; case "${1:-}" in "" | -*) ;; *) b2_name=$1; shift ;; esac ;;
    --identity) identity="${2:?--identity needs a file}"; shift 2 ;;
    --db) db="${2:?--db needs a name}"; shift 2 ;;
    --container) container="${2:?--container needs a name}"; shift 2 ;;
    -*) usage ;;
    *) [ -z "$file" ] || usage; file="$1"; shift ;;
  esac
done
[ -n "$identity" ] && [ -n "$db" ] || usage
[ "$from_b2" -eq 1 ] || [ -n "$file" ] || usage
[ "$from_b2" -eq 0 ] || [ -z "$file" ] || usage
fail() { echo "FAIL: $1" >&2; exit 1; }
[ "$from_b2" -eq 1 ] || [ -f "$file" ] || fail "no such backup: $file"
[ -f "$identity" ] || fail "no such key file: $identity"
[ "$from_b2" -eq 1 ] || file="$(cd "$(dirname "$file")" && pwd)/$(basename "$file")"
identity="$(cd "$(dirname "$identity")" && pwd)/$(basename "$identity")"

cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

if [ "$from_b2" -eq 1 ]; then
  [ -n "${B2_READ_KEY_ID:-}" ] && [ -n "${B2_READ_APPLICATION_KEY:-}" ] \
    || fail "B2_READ_KEY_ID and B2_READ_APPLICATION_KEY must be in .env (the READ key; docs/BACKUP.md)"
  export B2_KEY_ID=$B2_READ_KEY_ID B2_APPLICATION_KEY=$B2_READ_APPLICATION_KEY
  # `-e NAME` with no value hands the variable over by name: the key never appears on a command line.
  # B2_REGION is empty in production (derived from the endpoint) and set only for the CI stand-in.
  MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -T -e B2_KEY_ID -e B2_APPLICATION_KEY -e B2_BUCKET \
    -e B2_ENDPOINT -e B2_REGION --entrypoint /opt/backup/fetch-b2.sh backup ${b2_name:+"$b2_name"} \
    > "$work/fetched.tar.age" || fail "could not download from B2 (the message above says why)"
  file=$work/fetched.tar.age
fi

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
