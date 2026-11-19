-- IDEA-029 restore drill: a fingerprint of one database as of a cutoff instant (docs/BACKUP.md,
-- "The restore drill, step by step"). Run the SAME file on the laptop's live database and on the
-- desktop's restored one, with the backup's own UTC stamp as the cutoff; every line must match.
--
--   docker compose exec -T postgres psql -U flowdesk -d <db> -Atq -v cutoff=2026-11-20T08:00:03Z -f - < infra/drill-fingerprint.sql
--
-- Why a cutoff: the laptop keeps writing after the dump. The observation tables are insert-only and
-- carry ingested_at, so "rows ingested at or before the stamp" is exactly what the dump holds (the stamp
-- is taken just before pg_dump starts; nothing is scheduled at the backup slot). The reference tables
-- change only through the seed (symbols and mappings are fixed at 24). The md5 lines fingerprint values.
\set ON_ERROR_STOP on
-- Timestamps are hashed as text, and their text depends on the session's zone: pin it on both sides.
SET TimeZone = 'UTC';
SELECT 'alembic ' || version_num FROM alembic_version;
SELECT 'hypertables ' || string_agg(hypertable_name, ',' ORDER BY hypertable_name)
  FROM timescaledb_information.hypertables;
SELECT 'insert_only_triggers ' || count(*)
  FROM pg_trigger WHERE NOT tgisinternal AND tgname ~ '_no_(update|delete|truncate)$';
SELECT 'symbols ' || count(*) FROM symbols;
SELECT 'provider_mappings ' || count(*) FROM provider_mappings;
-- Past sessions only: the seed extends the calendar forward on every `up` (insert-only, never rewrites).
SELECT 'trading_sessions_to_cutoff ' || count(*) FROM trading_sessions
  WHERE session_date <= (:'cutoff'::timestamptz AT TIME ZONE 'UTC')::date;
SELECT 'macro_metrics ' || count(*) || ' ' || md5(coalesce(string_agg(
    metric || '|' || effective_at || '|' || revision || '|' || coalesce(value::text, 'NULL') || '|' || available_at,
    ';' ORDER BY metric, effective_at, revision), ''))
  FROM macro_metrics WHERE ingested_at <= :'cutoff'::timestamptz;
SELECT 'bars_daily ' || count(*) || ' ' || md5(coalesce(string_agg(
    symbol_id || '|' || effective_at || '|' || o_raw || '|' || h_raw || '|' || l_raw || '|' || c_raw || '|' || v_raw
      || '|' || feed || '|' || available_at,
    ';' ORDER BY symbol_id, effective_at), ''))
  FROM bars_daily WHERE ingested_at <= :'cutoff'::timestamptz;
SELECT 'bars_intraday ' || count(*) || ' ' || md5(coalesce(string_agg(
    symbol_id || '|' || effective_at || '|' || c_raw || '|' || v_raw || '|' || feed || '|' || available_at,
    ';' ORDER BY symbol_id, effective_at), ''))
  FROM bars_intraday WHERE ingested_at <= :'cutoff'::timestamptz;
SELECT 'splits ' || count(*) || ' ' || md5(coalesce(string_agg(
    symbol_id || '|' || effective_at || '|' || ratio || '|' || available_at,
    ';' ORDER BY symbol_id, effective_at), ''))
  FROM splits WHERE ingested_at <= :'cutoff'::timestamptz;
SELECT 'distributions ' || count(*) || ' ' || md5(coalesce(string_agg(
    symbol_id || '|' || effective_at || '|' || kind || '|' || amount || '|' || feed || '|' || available_at,
    ';' ORDER BY symbol_id, effective_at, kind), ''))
  FROM distributions WHERE ingested_at <= :'cutoff'::timestamptz;
