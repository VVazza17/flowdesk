#!/usr/bin/env bash
# Back up one database to a dump plus a manifest. Runbook: docs/BACKUP.md
#   bash infra/backup.sh [--db NAME] [--out DIR]     (defaults: POSTGRES_DB, dumps/)
# Prints the dump's path on stdout and nothing else, so a caller can capture it.
#
# Whole database only: a per-table pg_dump loses the hypertable/chunk relationship (Timescale docs).
# pg_dump runs inside the postgres container, so its version always matches the server.
set -euo pipefail

db="" out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --db) db="${2:?--db needs a name}"; shift 2 ;;
    --out) out="${2:?--out needs a directory}"; shift 2 ;;
    *) echo "usage: $0 [--db NAME] [--out DIR]" >&2; exit 2 ;;
  esac
done

# Resolve --out against the caller's directory before moving to the repo root.
if [ -n "$out" ]; then mkdir -p "$out"; out=$(cd "$out" && pwd); fi
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
. infra/lib.sh
db="${db:-$POSTGRES_DB}"
out="${out:-$(pwd)/dumps}"
mkdir -p "$out"
[[ "$db" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || fail "database name '$db' is not a plain identifier"

pg() { docker compose exec -T postgres "$@"; }
sql() { pg psql -U "$POSTGRES_USER" -d "$db" -v ON_ERROR_STOP=1 -tA -c "$1"; }

# Read first: this also fails cleanly, before any file exists, if the database is not there.
pg_version=$(sql "show server_version") || fail "cannot read database '$db'"
ts_version=$(sql "select extversion from pg_extension where extname='timescaledb'")
[ -n "$ts_version" ] || fail "database '$db' has no timescaledb extension"
alembic="none"
if [ "$(sql "select to_regclass('public.alembic_version') is not null")" = "t" ]; then
  alembic=$(sql "select version_num from alembic_version")
fi
image=$(docker inspect -f '{{.Config.Image}}' "$(docker compose ps -q postgres)")

name="${db}_$(date -u +%Y%m%dT%H%M%SZ)"
partial="$out/$name.dump.partial"
partial_manifest="$out/$name.manifest.partial"
errlog=$(mktemp)
cleanup() { rm -f "$partial" "$partial_manifest" "$errlog"; }
trap cleanup EXIT

if ! pg pg_dump -U "$POSTGRES_USER" -d "$db" -Fc > "$partial" 2> "$errlog"; then
  cat "$errlog" >&2
  fail "pg_dump failed; no backup was written"
fi
# Every Timescale dump warns about this catalog cycle (seen on 2.31.0). Anything else is shown. Only
# pg_dump's exit code decides success, because the docs say its messages can usually be ignored.
grep -vE 'circular foreign-key constraints on this table|detail: continuous_agg|using a full dump instead of a --data-only|restore the dump without using --disable-triggers' \
  "$errlog" >&2 || true

[ -s "$partial" ] || fail "pg_dump exited 0 but wrote nothing"
# Reads the table of contents through the same pipe a restore will use.
pg pg_restore --list < "$partial" > /dev/null || fail "pg_restore cannot read the dump just written"

{
  echo "format=1"
  echo "created_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "database=$db"
  echo "sha256=$(sha256sum "$partial" | cut -d' ' -f1)"
  echo "image=$image"
  echo "postgres_version=$pg_version"
  echo "timescaledb_version=$ts_version"
  echo "alembic_revision=$alembic"
} > "$partial_manifest"

# Manifest first: a .dump that exists always has its manifest, so a crash here can't publish half a pair.
mv "$partial_manifest" "$out/$name.manifest"
mv "$partial" "$out/$name.dump"
echo "$out/$name.dump"
