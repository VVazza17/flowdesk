#!/usr/bin/env bash
# M1 slice 9 smoke test: a backup restores into a fresh container and the app serves from it (§15
# item 7b), including a hypertable, a continuous aggregate and policies. The ops tables alone would
# prove nothing about those: every later time series is a hypertable, and §8 requires the other two.
#
# The fixture is a throwaway, inline here with no Alembic revision, in a scratch source database. The
# real database is only read, and the smoke checks it is unchanged at the end.
# Run from the repo root (needs a .env; see .env.example).
set -euo pipefail

set -a; . ./.env; set +a
. "$(dirname "$0")/lib.sh"

TMP_ROOT=$(mktemp -d)
SRC="flowdesk_smoke9_src_$$"
TARGET="flowdesk-smoke9-$$"      # disposable postgres: no published port, so nothing is exposed
API="flowdesk-smoke9-api-$$"
OUT="$TMP_ROOT/dumps"
mkdir -p "$OUT"

cleanup() {
  local rc=$?
  docker rm -f "$API" "$TARGET" >/dev/null 2>&1 || true
  docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -c "DROP DATABASE IF EXISTS \"$SRC\" WITH (FORCE)" >/dev/null 2>&1 || true
  # Put the writers back (see where they are stopped, below).
  docker compose start worker beat backup >/dev/null 2>&1 || true
  rm -rf "$TMP_ROOT"
  exit "$rc"
}
trap cleanup EXIT

# A database is "main <db>" (the compose postgres) or "target <db>" (the disposable container).
pg() {
  local where="$1"; shift
  if [ "$where" = main ]; then docker compose exec -T postgres "$@"
  else docker exec -i "$TARGET" "$@"; fi
}
sql() { local where="$1" db="$2"; shift 2; pg "$where" psql -U "$POSTGRES_USER" -d "$db" -v ON_ERROR_STOP=1 -tA "$@"; }
one() { sql "$1" "$2" -c "$3"; }

expect_red() { # name, pattern the output must contain, command...
  local name="$1" pattern="$2"; shift 2
  if "$@" > "$TMP_ROOT/out.log" 2>&1; then
    cat "$TMP_ROOT/out.log" >&2
    fail "$name should have failed"
  fi
  grep -qE "$pattern" "$TMP_ROOT/out.log" || {
    cat "$TMP_ROOT/out.log" >&2
    fail "$name failed, but not with /$pattern/"
  }
  echo "ok (red):   $name"
}

# Every row of every table and aggregate, enumerated from the catalog so later tables join in unasked.
fingerprint() { # where db
  sql "$1" "$2" <<'SQL'
SET client_min_messages = warning;
SET TIME ZONE 'UTC';
SELECT format(
  $f$SELECT %L || ' ' || count(*) || ' ' || coalesce(md5(string_agg(t::text, E'\n' ORDER BY t::text)), '-') FROM %I.%I t$f$,
  name, 'public', name)
FROM (
  SELECT table_name AS name FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
  UNION SELECT view_name FROM timescaledb_information.continuous_aggregates WHERE view_schema = 'public'
) r ORDER BY name
\gexec
SQL
}

# What makes it a working hypertable rather than a table with the right rows. The index and constraint
# lines are here because the spike's plain restore lost the parent's primary key while every other
# line stayed correct, and old chunks kept their own copy so a duplicate there was still rejected.
structure() { # where db
  sql "$1" "$2" <<'SQL'
SET client_min_messages = warning;
SET TIME ZONE 'UTC';
SELECT 'restoring ' || current_setting('timescaledb.restoring');
SELECT 'hypertable ' || hypertable_schema || '.' || hypertable_name || ' chunks=' || num_chunks
  FROM timescaledb_information.hypertables ORDER BY 1;
SELECT 'dimension ' || hypertable_name || ' ' || column_name || ' ' || time_interval
  FROM timescaledb_information.dimensions ORDER BY 1;
SELECT 'chunk ' || hypertable_name || ' ' || range_start || ' ' || range_end
  FROM timescaledb_information.chunks ORDER BY 1;
SELECT 'aggregate ' || view_name || ' ' || materialized_only || ' ' || md5(view_definition)
  FROM timescaledb_information.continuous_aggregates ORDER BY 1;
SELECT 'job ' || proc_name || ' ' || scheduled || ' ' || schedule_interval || ' ' || coalesce(config::text, '')
  FROM timescaledb_information.jobs WHERE job_id >= 1000 ORDER BY 1;
SELECT 'index ' || indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1;
SELECT 'constraint ' || c.conrelid::regclass || ' ' || c.conname || ' ' || pg_get_constraintdef(c.oid)
  FROM pg_constraint c JOIN pg_class r ON r.oid = c.conrelid JOIN pg_namespace n ON n.oid = r.relnamespace
  WHERE n.nspname = 'public' ORDER BY 1;
SELECT format('SELECT %L || count(*) FROM ONLY %I.%I', 'rows in parent ' || hypertable_name || ' ', hypertable_schema, hypertable_name)
  FROM timescaledb_information.hypertables ORDER BY 1
\gexec
SQL
}

# Compares and reports both layers, so a test can assert which one caught a break.
verify() { # src_where src_db dst_where dst_db
  local a="$TMP_ROOT/a" b="$TMP_ROOT/b" data=identical struct=identical
  fingerprint "$1" "$2" > "$a.data" || return 2
  fingerprint "$3" "$4" > "$b.data" || return 2
  structure "$1" "$2" > "$a.struct" || return 2
  structure "$3" "$4" > "$b.struct" || return 2
  diff "$a.data" "$b.data" || data=DIFFERS
  diff "$a.struct" "$b.struct" || struct=DIFFERS
  echo "verify: data=$data structure=$struct"
  [ "$data" = identical ] && [ "$struct" = identical ]
}

echo "== start the stack and a fresh target container of the same image"
docker compose up -d --build
wait_ready
pg_id=$(docker compose ps -q postgres)
image=$(docker inspect -f '{{.Config.Image}}' "$pg_id")
network=$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}' "$pg_id")
docker run -d --name "$TARGET" --network "$network" -e POSTGRES_USER="$POSTGRES_USER" \
  -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" -e POSTGRES_DB="$POSTGRES_DB" "$image" >/dev/null
# -h 127.0.0.1: the entrypoint's temporary init server only listens on the socket (see docker-compose.yml).
deadline=$((SECONDS + WAIT_TIMEOUT))
until docker exec "$TARGET" pg_isready -q -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"; do
  [ $SECONDS -lt $deadline ] || fail "target container not ready after ${WAIT_TIMEOUT}s"
  sleep 1
done

# DECISION: the background writers are paused for the rest of this smoke. It asserts the REAL database is
# unchanged, and since M2 the stack writes to it on its own: the worker's canary every minute (slice 1) and
# the backup service's task_runs rows (slice 2). Left running, a tick between the dump and a comparison
# would fail the round trip at random. The exit trap starts them again.
docker compose stop worker beat backup >/dev/null

main_before=$(fingerprint main "$POSTGRES_DB")

echo "== seed a scratch source: migrated ops tables, plus the throwaway hypertable"
one main "$POSTGRES_DB" "CREATE DATABASE \"$SRC\"" >/dev/null
docker compose run --rm -T \
  -e DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${SRC}" \
  migrate >/dev/null 2>&1 || fail "alembic upgrade head failed on the source database"

# Fixed timestamps, so runs are comparable. The policies are set so that neither changes any data if
# its background job fires mid-run: the rows are far outside the refresh window and inside retention.
sql main "$SRC" > /dev/null <<'SQL'
SET client_min_messages = warning;
INSERT INTO providers_health(provider, feed, last_success_at) VALUES ('smoke9', 'smoke9_feed', '2026-01-01 00:00+00');
INSERT INTO ingestion_runs(provider, feed, started_at, status) VALUES ('smoke9', 'smoke9_feed', '2026-01-01 00:00+00', 'ok');
INSERT INTO task_runs(task_name, started_at, status) VALUES ('smoke9_task', '2026-01-01 00:00+00', 'ok');
INSERT INTO backfill_runs(feed, status, started_at) VALUES ('smoke9_feed', 'ok', '2026-01-01 00:00+00');

CREATE TABLE smoke_ticks (
  id bigint GENERATED ALWAYS AS IDENTITY,
  ts timestamptz NOT NULL,
  symbol text NOT NULL,
  price numeric(12,4) NOT NULL,
  volume bigint NOT NULL,
  PRIMARY KEY (ts, symbol)
);
CREATE INDEX smoke_ticks_symbol ON smoke_ticks (symbol, ts DESC);
SELECT create_hypertable('smoke_ticks', by_range('ts', interval '1 day'));
INSERT INTO smoke_ticks (ts, symbol, price, volume)
SELECT g, s, 100 + extract(epoch FROM g)::bigint % 97 + length(s), extract(epoch FROM g)::bigint % 1000
FROM generate_series('2026-01-01 00:00+00'::timestamptz, '2026-01-10 23:00+00', '1 hour') g,
     unnest(ARRAY['AAA', 'BBBB']) s;

CREATE MATERIALIZED VIEW smoke_ticks_daily WITH (timescaledb.continuous) AS
  SELECT time_bucket('1 day', ts) AS bucket, symbol, avg(price) AS avg_price, sum(volume) AS vol, count(*) AS n
  FROM smoke_ticks GROUP BY 1, 2 WITH DATA;
SELECT add_continuous_aggregate_policy('smoke_ticks_daily', start_offset => interval '30 days',
  end_offset => interval '1 day', schedule_interval => interval '1 hour');
SELECT add_retention_policy('smoke_ticks', drop_after => interval '100 years');
SQL
[ "$(one main "$SRC" "select num_chunks from timescaledb_information.hypertables where hypertable_name='smoke_ticks'")" = "10" ] \
  || fail "the fixture should span 10 chunks"
[ "$(one main "$SRC" "select count(*) from smoke_ticks")" = "480" ] || fail "the fixture should hold 480 rows"

echo "== back up, and restore into both kinds of fresh database"
dump=$(bash infra/backup.sh --db "$SRC" --out "$OUT") || fail "backup.sh failed"
[ -s "$dump" ] && [ -s "${dump%.dump}.manifest" ] || fail "backup.sh did not produce a dump and a manifest"
restore() { bash infra/restore.sh "$dump" --container "$TARGET" --db "$1"; }

# The init-created POSTGRES_DB is what a disaster restore gets: the image pre-installs the extension there.
restore "$POSTGRES_DB" >/dev/null || fail "restore into the init-created database failed"
verify main "$SRC" target "$POSTGRES_DB" || fail "restore into the init-created database is not faithful"
echo "ok (green): init-created database reproduces data and structure"

# template0 has no extension. template1 does on this image, so CREATE DATABASE alone wouldn't test this.
restore "smoke9_template0" >/dev/null || fail "restore into a database created from template0 failed"
verify main "$SRC" target smoke9_template0 || fail "restore into a template0 database is not faithful"
echo "ok (green): template0 database reproduces data and structure"

echo "== the restored hypertable works, not just reads back"
DB="$POSTGRES_DB"
parent_rows() { one target "$DB" "select count(*) from only smoke_ticks"; }
next_id=$(one target "$DB" "select max(id) + 1 from smoke_ticks")
new_id=$(one target "$DB" "INSERT INTO smoke_ticks (ts, symbol, price, volume) VALUES ('2026-02-01 00:00+00', 'AAA', 1, 1) RETURNING id" | head -n1)
[ "$new_id" = "$next_id" ] || fail "identity did not continue: expected id $next_id, got $new_id"
[ "$(one target "$DB" "select num_chunks from timescaledb_information.hypertables where hypertable_name='smoke_ticks'")" = "11" ] \
  || fail "an insert in a new time range did not create a chunk"
[ "$(parent_rows)" = "0" ] || fail "a row landed in the parent table instead of a chunk"
duplicate() { one target "$DB" "INSERT INTO smoke_ticks (ts, symbol, price, volume) VALUES ('$1', 'AAA', 1, 1)"; }
expect_red "primary key holds in a restored chunk" 'duplicate key' duplicate '2026-01-05 00:00+00'
# A plain restore passes the line above and fails this one: with the parent's primary key gone, new
# chunks have none, and the spike stored two rows for one key.
expect_red "primary key holds in a chunk created after the restore" 'duplicate key' duplicate '2026-02-01 00:00+00'
scans=$(one target "$DB" "EXPLAIN SELECT * FROM smoke_ticks WHERE ts >= '2026-01-05 00:00+00' AND ts < '2026-01-05 12:00+00'" | grep -cE 'Scan.*_hyper_' || true)
[ "$scans" = "1" ] || fail "chunk exclusion: a query on one day scanned $scans chunks"
one target "$DB" "CALL refresh_continuous_aggregate('smoke_ticks_daily', '2026-02-01', '2026-02-02')" >/dev/null
[ "$(one target "$DB" "select n from smoke_ticks_daily where bucket='2026-02-01 00:00+00' and symbol='AAA'")" = "1" ] \
  || fail "the continuous aggregate did not pick up a new row"
retention_job=$(one target "$DB" "select job_id from timescaledb_information.jobs where proc_name='policy_retention'")
[ -n "$retention_job" ] || fail "the retention job is gone"
one target "$DB" "CALL run_job($retention_job)" >/dev/null || fail "the retention job does not run"
[ "$(one target "$DB" "select count(*) from smoke_ticks")" = "481" ] || fail "the retention job changed the row count"
ops_next=$(one target "$DB" "select max(id) + 1 from ingestion_runs")
ops_id=$(one target "$DB" "INSERT INTO ingestion_runs(provider, feed, started_at, status) VALUES ('x', 'y', '2026-01-02 00:00+00', 'ok') RETURNING id" | head -n1)
[ "$ops_id" = "$ops_next" ] || fail "ops table identity did not continue: expected $ops_next, got $ops_id"
one target "$DB" "DELETE FROM ingestion_runs WHERE id = $ops_id" >/dev/null
echo "ok (green): inserts, new chunks, keys, chunk exclusion, aggregate refresh, retention job, identities"

echo "== the app serves from it (§15 item 7b)"
target_url="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${TARGET}:5432/${POSTGRES_DB}"
migrate_out=$(docker compose run --rm -T -e DATABASE_URL="$target_url" migrate 2>&1) \
  || { echo "$migrate_out" >&2; fail "alembic upgrade head failed against the restored database"; }
if printf '%s' "$migrate_out" | grep -q 'Running upgrade'; then fail "alembic upgrade head was not a no-op: $migrate_out"; fi

# compose run publishes no ports unless asked, so this can sit alongside the real API.
docker compose --profile private run -d --no-deps --name "$API" -e DATABASE_URL="$target_url" api >/dev/null
# MSYS_NO_PATHCONV: Git Bash on Windows would otherwise rewrite the URL path into a Windows path.
api_get() {
  MSYS_NO_PATHCONV=1 docker exec "$API" python -c "import urllib.request,sys; print(urllib.request.urlopen('http://127.0.0.1:8000'+sys.argv[1], timeout=5).read().decode())" "$1" 2> "$TMP_ROOT/api.err"
}
deadline=$((SECONDS + 60)); body=""
until body=$(api_get /health/ready); do
  [ $SECONDS -lt $deadline ] || { tail -n 3 "$TMP_ROOT/api.err" >&2; fail "/health/ready never answered 200 against the restored database"; }
  sleep 1
done
printf '%s' "$body" | grep -qF '"status":"ok"' || fail "/health/ready is not ok against the restored database: $body"
body=$(api_get /health/data) || fail "/health/data failed against the restored database"
# The seeded name exists only in the source, so this can't be answered from the real database.
printf '%s' "$body" | grep -qF 'smoke9_feed' || fail "/health/data does not show the restored rows: $body"
echo "ok (green): alembic is a no-op; /health/ready ok; /health/data shows the restored feed"

echo "== the verifier goes red, and says which layer caught it"
fresh() { restore "$1" >/dev/null || fail "restore into $1 failed"; }
verify_against() { ( verify main "$SRC" target "$1" ); }

# The false pass this smoke exists to stop: the same rows, in plain tables.
fresh red_plain
sql target red_plain > /dev/null <<'SQL'
SET client_min_messages = warning;
CREATE TABLE plain_daily AS SELECT * FROM smoke_ticks_daily;
CREATE TABLE plain_ticks (LIKE smoke_ticks INCLUDING ALL);
INSERT INTO plain_ticks OVERRIDING SYSTEM VALUE SELECT * FROM smoke_ticks;
DROP MATERIALIZED VIEW smoke_ticks_daily;
DROP TABLE smoke_ticks;
ALTER TABLE plain_ticks RENAME TO smoke_ticks;
ALTER TABLE plain_daily RENAME TO smoke_ticks_daily;
SQL
expect_red "hypertable replaced by a plain table with identical rows" 'verify: data=identical structure=DIFFERS' verify_against red_plain

fresh red_value
one target red_value "UPDATE smoke_ticks SET volume = volume + 1 WHERE ts = '2026-01-05 00:00+00' AND symbol = 'AAA'" >/dev/null
expect_red "one value changed, same row count" 'verify: data=DIFFERS structure=identical' verify_against red_value

fresh red_chunk
one target red_chunk "SELECT drop_chunks('smoke_ticks', older_than => '2026-01-02 00:00+00'::timestamptz)" >/dev/null
expect_red "one chunk dropped" 'verify: data=DIFFERS structure=DIFFERS' verify_against red_chunk

fresh red_job
one target red_job "SELECT remove_retention_policy('smoke_ticks')" >/dev/null
expect_red "retention job deleted" 'verify: data=identical structure=DIFFERS' verify_against red_job

# What the plain restore actually did to the schema.
fresh red_pk
one target red_pk "ALTER TABLE smoke_ticks DROP CONSTRAINT smoke_ticks_pkey" >/dev/null
expect_red "primary key lost" 'verify: data=identical structure=DIFFERS' verify_against red_pk

echo "== bad inputs are refused, and leave the target alone"
db_exists() { one target postgres "select count(*) from pg_database where datname='$1'"; }
copy_dump() { # dir: a copy of the dump and manifest; prints the dump's new path
  mkdir -p "$1"; cp "$dump" "$1/"; cp "${dump%.dump}.manifest" "$1/"; echo "$1/$(basename "$dump")"
}
restore_file() { bash infra/restore.sh "$1" --container "$TARGET" --db "$2"; }

trunc=$(copy_dump "$TMP_ROOT/trunc")
size=$(wc -c < "$trunc")
head -c $((size / 2)) "$dump" > "$trunc"
expect_red "truncated dump" 'checksum mismatch' restore_file "$trunc" red_trunc
[ "$(db_exists red_trunc)" = "0" ] || fail "a refused restore still created the database"

# The checksum can't help if the file was already short when the manifest was written.
partial=$(copy_dump "$TMP_ROOT/partial")
head -c $((size / 2)) "$dump" > "$partial"
sed -i "s/^sha256=.*/sha256=$(sha256sum "$partial" | cut -d' ' -f1)/" "${partial%.dump}.manifest"
expect_red "truncated dump with a matching checksum" 'PARTIAL' restore_file "$partial" red_partial
[ "$(one target red_partial "select current_setting('timescaledb.restoring')")" = "on" ] \
  || fail "a failed restore should leave the target in restoring mode"

before=$(fingerprint target smoke9_template0)
expect_red "restore into a non-empty database" 'not empty' restore_file "$dump" smoke9_template0
[ "$(fingerprint target smoke9_template0)" = "$before" ] || fail "a refused restore changed the target"
real_restore() { bash infra/restore.sh "$dump" --db "$POSTGRES_DB"; }
expect_red "restore over the real database" 'not empty' real_restore

other=$(copy_dump "$TMP_ROOT/other")
sed -i 's/^timescaledb_version=.*/timescaledb_version=0.0.0/' "${other%.dump}.manifest"
expect_red "dump from another TimescaleDB version names the image to use" "version mismatch.*Restore it on image $image" restore_file "$other" red_version
[ "$(db_exists red_version)" = "0" ] || fail "a refused restore still created the database"

nomanifest=$(copy_dump "$TMP_ROOT/nomanifest")
rm "${nomanifest%.dump}.manifest"
expect_red "dump with no manifest" 'no manifest' restore_file "$nomanifest" red_nomanifest

failed_out="$TMP_ROOT/failed"
missing_db() { bash infra/backup.sh --db flowdesk_smoke9_no_such_db --out "$failed_out"; }
expect_red "backup of a database that does not exist" 'cannot read database' missing_db
# pg_dump dying midway can't be provoked from outside, so a shim fails only that call and writes junk first.
mkdir -p "$TMP_ROOT/shim"
cat > "$TMP_ROOT/shim/docker" <<SHIM
#!/usr/bin/env bash
for arg in "\$@"; do
  if [ "\$arg" = pg_dump ]; then printf 'half a dump'; echo 'pg_dump: error: simulated failure' >&2; exit 1; fi
done
exec "$(command -v docker)" "\$@"
SHIM
chmod +x "$TMP_ROOT/shim/docker"
failing_dump() { PATH="$TMP_ROOT/shim:$PATH" bash infra/backup.sh --db "$SRC" --out "$failed_out"; }
expect_red "pg_dump failing midway" 'pg_dump failed' failing_dump
[ -z "$(ls -A "$failed_out" 2>/dev/null)" ] || fail "a failed backup left files behind: $(ls -A "$failed_out")"
echo "ok (green): a failed backup leaves nothing that looks like a backup"

echo "== the default path, on the real database"
main_dump=$(bash infra/backup.sh --out "$OUT/main") || fail "backup.sh with default arguments failed"
bash infra/restore.sh "$main_dump" --container "$TARGET" --db smoke9_real >/dev/null || fail "restoring the real database failed"
verify main "$POSTGRES_DB" target smoke9_real || fail "the real database did not round-trip"
[ "$(fingerprint main "$POSTGRES_DB")" = "$main_before" ] || fail "the smoke changed the real database"
echo "ok (green): the real database round-trips, and the smoke left it unchanged"

echo "OK: slice 9 smoke passed (fresh container, hypertable + aggregate + policies reproduced and working, app serves from it, 15 breaks caught)"
