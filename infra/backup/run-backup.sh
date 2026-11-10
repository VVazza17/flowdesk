#!/usr/bin/env bash
# One backup: dump, check, encrypt, keep locally, prune. Runs inside the backup container as the postgres
# user. Records itself in task_runs as ops.backup. Runbook: docs/BACKUP.md
#
# The result is BACKUP_DIR/<db>_<UTC stamp>.tar.age: a tar of the .dump and its .manifest (the same pair
# infra/backup.sh writes, so infra/restore.sh takes them unchanged), encrypted to the public keys in the
# recipients file. The plaintext exists only in a private temp directory on tmpfs and is removed on exit.
set -uo pipefail
here=$(dirname "$0")
# shellcheck source=lib.sh
. "$here/lib.sh"

BACKUP_DIR=${BACKUP_DIR:-/backups}
RECIPIENTS=${BACKUP_RECIPIENTS_FILE:-/etc/backup/recipients.txt}
KEEP_DAILY=${BACKUP_KEEP_DAILY:-14}
KEEP_WEEKLY=${BACKUP_KEEP_WEEKLY:-8}
db=${PGDATABASE:?PGDATABASE is not set}

if ! id=$(task_start ops.backup) || [ -z "$id" ]; then
  jlog error backup_bookkeeping_failed "detail=could not write task_runs; is Postgres up?"
  exit "$EX_TRANSIENT"   # nothing was recorded, so nothing is noise; Postgres coming back is what fixes it
fi

work=$(mktemp -d /tmp/backup.XXXXXX)
trap 'rm -rf "$work"' EXIT

fail() { # CLASS MESSAGE
  jlog error backup_failed "error_class=$1" "detail=$2"
  task_finish "$id" failed "$1" "$2" || true
  rm -f "$BACKUP_DIR"/*.partial
  exit "$(exit_code_for_class "$1")"   # 78 for NoRecipient / BadDatabaseName, else 1 (lib.sh)
}

recipients_ok "$RECIPIENTS" \
  || fail NoRecipient "no age public key in $RECIPIENTS. Nothing was written. See docs/BACKUP.md"
[[ $db =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || fail BadDatabaseName "database name is not a plain identifier"

sql() { psqlc -d "$db" -c "$1"; }
pg_version=$(sql "show server_version") || fail ReadFailed "cannot read database $db"
ts_version=$(sql "select extversion from pg_extension where extname='timescaledb'")
[ -n "$ts_version" ] || fail NoTimescale "database $db has no timescaledb extension"
alembic=none
if [ "$(sql "select to_regclass('public.alembic_version') is not null")" = "t" ]; then
  alembic=$(sql "select version_num from alembic_version")
fi

name="${db}_$(date -u +%Y%m%dT%H%M%SZ)"
dump="$work/$name.dump"
errlog="$work/pg_dump.err"

if ! pg_dump -d "$db" -Fc > "$dump" 2> "$errlog"; then
  fail DumpFailed "pg_dump failed: $(tail -c 300 "$errlog")"
fi
[ -s "$dump" ] || fail DumpFailed "pg_dump exited 0 but wrote nothing"
pg_restore --list "$dump" > /dev/null 2>&1 || fail DumpUnreadable "pg_restore cannot read the dump just written"

{
  echo "format=1"
  echo "created_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "database=$db"
  echo "sha256=$(sha256sum "$dump" | cut -d' ' -f1)"
  echo "image=${POSTGRES_IMAGE:-unknown}"
  echo "postgres_version=$pg_version"
  echo "timescaledb_version=$ts_version"
  echo "alembic_revision=$alembic"
} > "$work/$name.manifest"

partial="$BACKUP_DIR/$name.tar.age.partial"
final="$BACKUP_DIR/$name.tar.age"
if ! tar -cf - -C "$work" "$name.dump" "$name.manifest" | age -R "$RECIPIENTS" -o "$partial"; then
  fail EncryptFailed "tar or age failed; no backup was written"
fi
# age writes this header first. A file without it is not an age file, whatever its size.
[ -s "$partial" ] && [ "$(head -c 21 "$partial")" = "age-encryption.org/v1" ] \
  || fail EncryptFailed "output is not an age file; no backup was written"
mv "$partial" "$final"

prune_dir "$BACKUP_DIR" "$KEEP_DAILY" "$KEEP_WEEKLY"

size=$(wc -c < "$final" | tr -d ' ')
task_finish "$id" succeeded || jlog warning backup_bookkeeping_failed "detail=backup is written but task_runs was not updated"
jlog info backup_succeeded "file=$name.tar.age" "bytes=$size"
