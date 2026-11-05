#!/usr/bin/env bash
# Restore a backup.sh dump into a new or empty database. Runbook: docs/BACKUP.md
#   bash infra/restore.sh <file.dump> --db NAME [--container NAME]
# --container targets a named container instead of the compose postgres service.
#
# It never drops or overwrites anything: a target with user tables is refused. --db has no default,
# on purpose, so a restore always says where it is going.
#
# The Timescale procedure is required, not optional. A plain pg_dump | pg_restore on 2.31.0 lost a
# whole chunk and the hypertable's primary key, so duplicate keys were accepted in new chunks, while
# still listing the right chunk count, jobs and aggregate. Evidence: docs/BUILD-LOG.md, slice 9.
set -euo pipefail

usage() { echo "usage: $0 <file.dump> --db NAME [--container NAME]" >&2; exit 2; }
dump="" db="" container=""
while [ $# -gt 0 ]; do
  case "$1" in
    --db) db="${2:?--db needs a name}"; shift 2 ;;
    --container) container="${2:?--container needs a name}"; shift 2 ;;
    -*) usage ;;
    *) [ -z "$dump" ] || usage; dump="$1"; shift ;;
  esac
done
[ -n "$dump" ] && [ -n "$db" ] || usage
[ -f "$dump" ] || { echo "FAIL: no such dump: $dump" >&2; exit 1; }
dump="$(cd "$(dirname "$dump")" && pwd)/$(basename "$dump")"

cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
. infra/lib.sh
[[ "$db" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || fail "database name '$db' is not a plain identifier"

pg() {
  if [ -n "$container" ]; then docker exec -i "$container" "$@"
  else docker compose exec -T postgres "$@"; fi
}
sql() { local target="$1"; shift; pg psql -U "$POSTGRES_USER" -d "$target" -v ON_ERROR_STOP=1 -tA "$@"; }

# 1. The dump is what the manifest says it is. The manifest is read, never sourced.
manifest="${dump%.dump}.manifest"
[ -f "$manifest" ] || fail "no manifest at $manifest; versions and checksum cannot be verified"
field() { sed -n "s/^$1=//p" "$manifest" | tr -d '\r'; }
want_sha=$(field sha256)
got_sha=$(sha256sum "$dump" | cut -d' ' -f1)
[ "$want_sha" = "$got_sha" ] || fail "checksum mismatch: the dump is truncated or corrupt (manifest $want_sha, file $got_sha). Nothing was changed."

# 2. The target can take it. See "The version trap" in docs/BACKUP.md.
want_image=$(field image)
want_pg=$(field postgres_version)
want_ts=$(field timescaledb_version)
exists=$(sql postgres -c "select count(*) from pg_database where datname='$db'")
got_pg=$(sql postgres -c "show server_version")
if [ "$exists" = "1" ]; then
  got_ts=$(sql "$db" -c "select extversion from pg_extension where extname='timescaledb'")
fi
[ -n "${got_ts:-}" ] || got_ts=$(sql postgres -c "select default_version from pg_available_extensions where name='timescaledb'")
[ "${want_pg%%.*}" = "${got_pg%%.*}" ] \
  || fail "version mismatch: this dump is PostgreSQL $want_pg, the target is $got_pg. Restore it on image $want_image. Nothing was changed."
[ "$want_ts" = "$got_ts" ] \
  || fail "version mismatch: this dump is TimescaleDB $want_ts, the target is $got_ts. Restore it on image $want_image. Nothing was changed."

# 3. The target is empty. Extension-owned relations don't count; chunks and user tables do.
if [ "$exists" = "1" ]; then
  used=$(sql "$db" -c "
    select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind in ('r','p','v','m','f')
      and n.nspname <> 'information_schema' and n.nspname not like 'pg\_%'
      and not exists (select 1 from pg_depend d where d.objid = c.oid and d.deptype = 'e')")
  [ "$used" = "0" ] || fail "refusing to restore: database '$db' is not empty ($used user relations). Nothing was changed."
else
  # DECISION: template0, so the result can't depend on what the image put in template1.
  sql postgres -c "CREATE DATABASE \"$db\" TEMPLATE template0" > /dev/null
fi

# 4. The documented procedure. Never pg_restore -j: it does not restore the Timescale catalogs correctly.
sql "$db" -q -c "SET client_min_messages = warning" -c "CREATE EXTENSION IF NOT EXISTS timescaledb" \
  -c "SELECT timescaledb_pre_restore()" > /dev/null
# --exit-on-error: without it pg_restore carries on past errors, which is how the plain restore lost data.
if ! pg pg_restore -U "$POSTGRES_USER" --no-owner --exit-on-error -d "$db" < "$dump"; then
  # Left in restoring mode on purpose: background jobs stay stopped on data that is known to be incomplete.
  fail "pg_restore failed: database '$db' is PARTIAL and still in restoring mode. Do not use it. Drop it and restore again into an empty database."
fi
sql "$db" -q -c "SELECT timescaledb_post_restore()" -c "ANALYZE" > /dev/null

# 5. Checked from a new session, because the setting applies per connection.
[ "$(sql "$db" -c "select current_setting('timescaledb.restoring')")" = "off" ] \
  || fail "database '$db' is still in restoring mode after timescaledb_post_restore()"
want_rev=$(field alembic_revision)
if [ "$want_rev" != "none" ]; then
  got_rev=$(sql "$db" -c "select version_num from alembic_version")
  [ "$want_rev" = "$got_rev" ] || fail "alembic revision is '$got_rev', the manifest says '$want_rev'"
fi

echo "OK: restored into '$db' (TimescaleDB $got_ts, alembic $want_rev)"
sql "$db" -c "select 'hypertable ' || hypertable_name || ': ' || num_chunks || ' chunks' from timescaledb_information.hypertables order by 1"
