#!/usr/bin/env bash
# M2 slice 3 smoke test (IDEA-033): reference tables, universes and trading sessions.
#
#   1. `docker compose up -d` migrates to 0003 AND seeds: 24 symbols, 5 universes, 24 memberships
#      (breadth empty, §6.2 gives no list), sessions for XNYS and XKRX with the holiday/half-day fixtures.
#   2. Re-running the migrate service changes nothing (the seed is idempotent and insert-only).
#   3. An EMPTY database, migrated and seeded by the shipped image, reaches the same state; and a
#      database that is only migrated holds no seed rows, so the seed is what fills it.
#   4. The provider_mappings rule holds in the real database: GOOG and GOOGL share a CIK, a symbol cannot
#      hold two active CIKs, a bad id_kind and an unpadded CIK are refused.
#
# Run from the repo root (needs a .env; see .env.example).
set -euo pipefail

set -a; . ./.env; set +a
. "$(dirname "$0")/lib.sh"

psql_db() { local db="$1"; shift; docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$db" -tA "$@" | tr -d '\r'; }
one() { psql_db "$1" -c "$2"; }

expect() {  # expect <db> <what> <sql> <want>
  local got
  got=$(one "$1" "$3")
  [ "$got" = "$4" ] || fail "db '$1': $2 is '$got', expected '$4'"
}

check_seeded() {  # check_seeded <db>
  local db="$1"
  expect "$db" "symbols" "select count(*) from symbols" 24
  expect "$db" "universes" "select count(*) from universes" 5
  expect "$db" "memberships" "select count(*) from universe_membership" 24
  expect "$db" "breadth members (§6.2 gives no list)" \
    "select count(*) from universe_membership m join universes u on u.id = m.universe_id where u.slug = 'breadth'" 0
  expect "$db" "rotation members (§6.2 lists fourteen)" \
    "select count(*) from universe_membership m join universes u on u.id = m.universe_id where u.slug = 'rotation'" 14
  expect "$db" "currencies" "select string_agg(code, ' ' order by code) from currencies" "KRW USD"
  expect "$db" "exchanges" "select string_agg(mic, ' ' order by mic) from exchanges" "XKRX XNYS"
  # The calendar fixtures, through the database: a holiday has no row, a half day closes at 13:00 New York.
  expect "$db" "NYSE 2025-07-04 (holiday)" \
    "select count(*) from trading_sessions where calendar_code = 'XNYS' and session_date = '2025-07-04'" 0
  expect "$db" "NYSE 2025-07-03 close (half day, UTC)" \
    "select to_char(closes_at at time zone 'UTC', 'HH24:MI') || ' ' || is_early_close from trading_sessions where calendar_code = 'XNYS' and session_date = '2025-07-03'" "17:00 true"
  expect "$db" "KRX 2025-10-03 (holiday)" \
    "select count(*) from trading_sessions where calendar_code = 'XKRX' and session_date = '2025-10-03'" 0
  local n
  n=$(one "$db" "select count(*) from trading_sessions where calendar_code = 'XNYS'")
  [ "$n" -gt 2500 ] || fail "db '$db': only $n NYSE sessions seeded"
}

echo "== 1. up -d migrates and seeds"
docker compose up -d --build
wait_ready
ver=$(one "$POSTGRES_DB" "select version_num from alembic_version")
[[ "$ver" > "0002" ]] || fail "alembic_version in the main db is '$ver', expected 0003 or later"
check_seeded "$POSTGRES_DB"

echo "== 2. the seed is idempotent"
before=$(one "$POSTGRES_DB" "select (select count(*) from symbols) || ' ' || (select count(*) from universe_membership) || ' ' || (select count(*) from trading_sessions) || ' ' || (select max(available_at) from universe_membership)")
docker compose run --rm -T migrate >/dev/null 2>&1 || fail "re-running migrate (and the seed) failed"
after=$(one "$POSTGRES_DB" "select (select count(*) from symbols) || ' ' || (select count(*) from universe_membership) || ' ' || (select count(*) from trading_sessions) || ' ' || (select max(available_at) from universe_membership)")
[ "$before" = "$after" ] || fail "re-running the seed changed the data: '$before' -> '$after'"

echo "== 3. an empty database, migrated and seeded by the shipped image"
scratch="flowdesk_smoke3_$$"
bare="flowdesk_smoke3b_$$"
cleanup() {
  psql_db "$POSTGRES_DB" -c "DROP DATABASE IF EXISTS \"$scratch\" WITH (FORCE)" >/dev/null 2>&1 || true
  psql_db "$POSTGRES_DB" -c "DROP DATABASE IF EXISTS \"$bare\" WITH (FORCE)" >/dev/null 2>&1 || true
}
trap cleanup EXIT
psql_db "$POSTGRES_DB" -c "CREATE DATABASE \"$scratch\"" >/dev/null
psql_db "$POSTGRES_DB" -c "CREATE DATABASE \"$bare\"" >/dev/null
docker compose run --rm -T \
  -e DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${scratch}" \
  migrate >/dev/null 2>&1 || fail "migrate + seed failed on an empty database"
check_seeded "$scratch"

# Migrated but NOT seeded: the tables exist and are empty, so the seed is what fills them.
docker compose run --rm -T --entrypoint alembic \
  -e DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${bare}" \
  migrate upgrade head >/dev/null 2>&1 || fail "alembic upgrade head failed on the bare database"
expect "$bare" "symbols without the seed" "select count(*) from symbols" 0
expect "$bare" "sessions without the seed" "select count(*) from trading_sessions" 0

echo "== 4. provider_mappings uniqueness, in the real database"
# Each statement is its own call so one refusal does not hide the next. ON_ERROR_STOP makes psql exit non-zero.
sql() { docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$scratch" -tA -c "$1" 2>&1 | tr -d '\r' || true; }
refused() {  # refused <what> <sql> <constraint-or-index name expected in the error>
  local out
  out=$(sql "$2")
  printf '%s' "$out" | grep -q "$3" || fail "$1: expected a refusal naming '$3', got: $out"
}
sql "insert into symbols (ticker) values ('GOOG'), ('GOOGL'), ('AAPL')" >/dev/null
goog=$(one "$scratch" "select id from symbols where ticker = 'GOOG'")
googl=$(one "$scratch" "select id from symbols where ticker = 'GOOGL'")
aapl=$(one "$scratch" "select id from symbols where ticker = 'AAPL'")
out=$(sql "insert into provider_mappings (symbol_id, provider, id_kind, external_id) values ($goog, 'sec_edgar', 'cik', '0001652044'), ($googl, 'sec_edgar', 'cik', '0001652044')")
printf '%s' "$out" | grep -q 'INSERT 0 2' || fail "GOOG and GOOGL could not share a CIK: $out"
refused "second active CIK on one symbol" \
  "insert into provider_mappings (symbol_id, provider, id_kind, external_id) values ($goog, 'sec_edgar', 'cik', '0000320193')" \
  uq_provider_mappings_symbol_active
sql "insert into provider_mappings (symbol_id, provider, id_kind, external_id) values ($goog, 'yfinance', 'ticker', 'GOOG')" >/dev/null
refused "one live ticker string on two symbols" \
  "insert into provider_mappings (symbol_id, provider, id_kind, external_id) values ($aapl, 'yfinance', 'ticker', 'GOOG')" \
  uq_provider_mappings_external_active
refused "id_kind outside the closed set" \
  "insert into provider_mappings (symbol_id, provider, id_kind, external_id) values ($aapl, 'x', 'isin', 'US02079K3059')" \
  ck_provider_mappings_id_kind
refused "unpadded CIK" \
  "insert into provider_mappings (symbol_id, provider, id_kind, external_id) values ($aapl, 'sec_edgar', 'cik', '320193')" \
  ck_provider_mappings_cik_form
refused "two active symbols with one ticker and no exchange" \
  "insert into symbols (ticker) values ('AAPL')" \
  uq_symbols_active_ticker

echo "OK: slice 3 smoke passed (migrated to 0003 and seeded; idempotent; empty db reaches the same state; mapping rules hold)"
