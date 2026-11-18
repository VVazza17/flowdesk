#!/usr/bin/env bash
# M2 slice 6, step 1(a): throwaway `bars` hypertable benchmark. Touches nothing of the dev stack:
# its own container (benchpg2) and its own NAMED volume (benchpg2-data), both removed by name at the end.
# No blanket prune is used anywhere.
#
# DISK GUARD: TWO filesystems are watched, because they are different limits:
#   host C:   what the Docker VM's disk image (docker_data.vhdx) grows into. `df -k /c` is the real C:.
#   VM disk   /dev/sdc inside Docker's VM, where the volume lives: read with `df` inside the container.
#             (Measured 2026-11-15: ~1,007 GB total, ~938 GB free; it is NOT C:.)
# Each has its own floor = its own free space at start minus BUDGET_GB (the most this benchmark may
# consume on it). Expected use is ~1-3 GB; the default budget is several times that. If EITHER falls
# below its floor the guard kills the container and the script aborts. The lowest value seen on each is
# reported at the end, so the report shows the guard actually saw the benchmark's writes.
set -u
export MSYS_NO_PATHCONV=1
BUDGET_GB="${BUDGET_GB:-15}"
NAME=benchpg2
VOL=benchpg2-data
IMG=timescale/timescaledb:2.31.0-pg16
OUT="${OUT:-bench-bars.out}"
FLAG="$(mktemp -u)"

free_kb() { df -k /c | awk 'NR==2{print $4}'; }
vm_kb() { docker exec "$NAME" df -k /var/lib/postgresql/data 2>/dev/null | awk 'NR==2{print $4}'; }
START_KB=$(free_kb)
FLOOR_KB=$(( START_KB - BUDGET_GB*1024*1024 ))
MIN_FILE="$(mktemp -u)"
echo "host C: free at start: $((START_KB/1024/1024)) GB; abort floor: $((FLOOR_KB/1024/1024)) GB (budget ${BUDGET_GB} GB)" | tee "$OUT"

GUARD_PID=""
guard() { # started AFTER the container is up, so the VM disk can be read
  local mc=$START_KB mv=$VM_START_KB c v
  while :; do
    c=$(free_kb); v=$(vm_kb); [ -z "$v" ] && v=$VM_START_KB
    [ "$c" -lt "$mc" ] && mc=$c; [ "$v" -lt "$mv" ] && mv=$v
    echo "$mc $mv" > "$MIN_FILE"
    if [ "$c" -lt "$FLOOR_KB" ] || [ "$v" -lt "$VM_FLOOR_KB" ]; then
      echo "GUARD TRIPPED: host C: free $((c/1024/1024)) GB (floor $((FLOOR_KB/1024/1024))), VM disk free $((v/1024/1024)) GB (floor $((VM_FLOOR_KB/1024/1024)))" > "$FLAG"
      docker kill "$NAME" >/dev/null 2>&1
      exit 0
    fi
    sleep 1
  done
}

cleanup() {
  [ -n "$GUARD_PID" ] && kill "$GUARD_PID" 2>/dev/null
  [ -s "$MIN_FILE" ] && { read -r mc mv < "$MIN_FILE"; echo "lowest seen: host C: $((mc/1024/1024)) GB free (start $((START_KB/1024/1024))); VM disk $((mv/1024/1024)) GB free (start $((VM_START_KB/1024/1024)), used by this run: $(( (VM_START_KB-mv)/1024 )) MB)" | tee -a "$OUT"; }
  docker rm -f "$NAME" >/dev/null 2>&1
  docker volume rm "$VOL" >/dev/null 2>&1
  echo "end: host C: $(( $(free_kb)/1024/1024 )) GB free (started $((START_KB/1024/1024)) GB)" | tee -a "$OUT"
  [ -s "$FLAG" ] && cat "$FLAG" | tee -a "$OUT"
}
trap cleanup EXIT

check() { [ -s "$FLAG" ] && { echo "ABORTED by disk guard"; exit 3; }; return 0; }
q()  { check; docker exec -i "$NAME" psql -U postgres -d bench -v ON_ERROR_STOP=1 -Atq "$@"; }
say() { echo "$@" | tee -a "$OUT"; }

docker rm -f "$NAME" >/dev/null 2>&1; docker volume rm "$VOL" >/dev/null 2>&1
docker run -d --name "$NAME" -v "$VOL":/var/lib/postgresql/data \
  -e POSTGRES_PASSWORD=bench "$IMG" >/dev/null || exit 1
for i in $(seq 60); do docker exec "$NAME" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
sleep 3
docker exec "$NAME" psql -U postgres -c "create database bench" >/dev/null
q -c "create extension if not exists timescaledb" >/dev/null 2>&1
VM_START_KB=$(vm_kb); VM_FLOOR_KB=$(( VM_START_KB - BUDGET_GB*1024*1024 ))
echo "VM disk free at start: $((VM_START_KB/1024/1024)) GB; abort floor: $((VM_FLOOR_KB/1024/1024)) GB" | tee -a "$OUT"
guard & GUARD_PID=$!

# Draft column set: spec section 8 `bars` plus the D1 provenance columns as RECOMMENDED (not decided).
# Prices are rounded to 4 dp; real yfinance floats stored as NUMERIC would be longer, so bytes/row here is a floor.
mk() {
q >/dev/null <<SQL
create table $1 (
  symbol_id int not null, "interval" text not null, ts timestamptz not null,
  o_raw numeric, h_raw numeric, l_raw numeric, c_raw numeric, v_raw bigint,
  c_split_adj numeric, c_total_return numeric, v_split_adj bigint, adjustment_factor numeric,
  dollar_volume numeric,
  source text not null, feed text not null, venue_scope text not null, raw_basis text not null,
  available_at timestamptz not null, ingested_at timestamptz not null, ingestion_run_id bigint,
  primary key (symbol_id, "interval", ts));
select create_hypertable('$1','ts', chunk_time_interval => interval '${2:-7 days}');
SQL
}
fill() { # table nsym label step days [start offset in days; default 0]
q >/dev/null <<SQL
insert into $1
select s, '$3', t,
  p, p+0.3, p-0.3, p+0.05, (1e5*random())::bigint,
  p+0.05, p+0.06, (1e5*random())::bigint, 1,
  round((p*1e5)::numeric,2),
  'yfinance','yf-$3','provider_default','live', t + interval '1 minute', t + interval '2 minutes', 1
from (
  select s, t, round((100 + 20*sin(s + extract(epoch from t)/1e6))::numeric,4) as p
  from generate_series(1,$2) s,
       generate_series(timestamptz '2025-01-02 14:30+00' + interval '${6:-0} days', timestamptz '2025-01-02 14:30+00' + interval '${6:-0} days' + interval '$5 days', interval '$4') t
  where extract(isodow from t) < 6 and ('$3'='1d' or t::time between '14:30' and '20:45')
) x;
analyze $1;
SQL
}
sizes() { q -c "select 'rows='||count(*)||' size_MB='||round(hypertable_size('$1')/1048576.0,1)||' bytes/row='||round(hypertable_size('$1')::numeric/count(*),1)||' chunks='||(select count(*) from timescaledb_information.chunks where hypertable_name='$1') from $1"; }
med() { sort -n | awk '{a[NR]=$1} END{print a[int((NR+1)/2)]}'; }
timeit() { local i; for i in 1 2 3 4 5; do q -c "explain (analyze, timing off, summary on) $1" | awk '/Execution Time/{print $3}'; done | med; }
plan() { q -c "explain (costs off) $1" | grep -E "Scan" | sed -E 's/^ *(->)? *//; s/ on .*//' | sort -u | head -4 | tr '\n' ';'; }

run_size() { # label nsym ivl step days
  local T="b_$1" ivl="$3"
  say ""; say "=== $1 : $2 symbols, $ivl bars, $5 calendar days ==="
  mk "$T"; fill "$T" "$2" "$ivl" "$4" "$5"
  say "uncompressed: $(sizes "$T")"
  local MID XTS
  MID=$(q -c "select max(ts) from $T")
  XTS=$(q -c "select ts from $T where symbol_id=1 order by ts offset (select count(*)/2 from $T where symbol_id=1) limit 1")
  local ql="select b.* from generate_series(1,$2) s cross join lateral (select * from $T where symbol_id=s and \"interval\"='$ivl' order by ts desc limit 1) b"
  local qw="select * from $T where symbol_id=7 and \"interval\"='$ivl' and ts >= timestamptz '$MID' - interval '30 days'"
  local qx="select * from $T where \"interval\"='$ivl' and ts='$XTS'"
  say "  [PK only]               latest-per-symbol ms=$(timeit "$ql")  one-symbol 30d ms=$(timeit "$qw")  cross-section ms=$(timeit "$qx")"
  say "      cross-section plan: $(plan "$qx")"
  say "      size before extra index: $(q -c "select round(hypertable_size('$T')/1048576.0,1)") MB"
  q -c "create index on $T (\"interval\", ts)" >/dev/null; q -c "analyze $T" >/dev/null
  say "  [+ (interval,ts) index] cross-section ms=$(timeit "$qx")  latest-per-symbol ms=$(timeit "$ql")"
  say "      cross-section plan: $(plan "$qx")"
  say "      size with extra index: $(q -c "select round(hypertable_size('$T')/1048576.0,1)") MB"
  q -c "drop index ${T}_interval_ts_idx" >/dev/null
  q -c "alter table $T set (timescaledb.compress, timescaledb.compress_segmentby='symbol_id,interval', timescaledb.compress_orderby='ts')" >/dev/null
  q -c "select count(compress_chunk(c, if_not_compressed => true)) from show_chunks('$T') c" >/dev/null
  say "compressed:   $(sizes "$T")"
  say "  [compressed]            latest-per-symbol ms=$(timeit "$ql")  one-symbol 30d ms=$(timeit "$qw")  cross-section ms=$(timeit "$qx")"
  say "  free now: host C: $(( $(free_kb)/1024/1024 )) GB, VM disk $(( $(vm_kb)/1024/1024 )) GB"
}

# Step 2 (2026-11-18): one table vs two, and the chunk interval. SCENARIO=step2 runs this instead of step 1(a).
# A daily bar's ts is its session's open (09:30 ET), the SAME instant as that session's first 15m bar, so a
# mixed table's cross-section at a daily ts reads rows of both intervals at that instant and must filter.
reads() { # table nsym ivl label: the three reads of step 1(a) plus a whole-history window for one symbol
  local T="$1" n="$2" ivl="$3" XTS
  XTS=$(q -c "select ts from $T where symbol_id=1 and \"interval\"='$ivl' order by ts offset (select count(*)/2 from $T where symbol_id=1 and \"interval\"='$ivl') limit 1")
  local ql="select b.* from generate_series(1,$n) s cross join lateral (select * from $T where symbol_id=s and \"interval\"='$ivl' order by ts desc limit 1) b"
  local qw="select * from $T where symbol_id=7 and \"interval\"='$ivl' and ts >= (select max(ts) from $T where \"interval\"='$ivl') - interval '30 days'"
  local qa="select * from $T where symbol_id=7 and \"interval\"='$ivl'"
  local qx="select * from $T where \"interval\"='$ivl' and ts='$XTS'"
  say "  $4: latest-per-symbol ms=$(timeit "$ql")  one-symbol 30d ms=$(timeit "$qw")  one-symbol all ms=$(timeit "$qa")  cross-section ms=$(timeit "$qx") (rows $(q -c "select count(*) from ($qx) z"))"
  say "      cross-section plan: $(plan "$qx")"
}
step2() {
  local c
  for c in "7 days" "28 days" "365 days"; do
    local T="d_${c%% *}"
    say ""; say "=== daily only, 24 symbols, 700 calendar days, chunk $c ==="
    mk "$T" "$c"; fill "$T" 24 1d "1 day" 700; say "  $(sizes "$T")"; reads "$T" 24 1d "1d"
  done
  for c in "7 days" "28 days"; do
    local T="m_${c%% *}"
    say ""; say "=== 15m only, 24 symbols, 365 calendar days, chunk $c ==="
    mk "$T" "$c"; fill "$T" 24 15m "15 minutes" 365; say "  $(sizes "$T")"; reads "$T" 24 15m "15m"
  done
  for n in 24 500; do
    local T="x_$n"
    say ""; say "=== MIXED one table, $n symbols: 1d over 700 days + 15m over its LAST 365 days (both end together), chunk 7 days ==="
    mk "$T" "7 days"; fill "$T" "$n" 1d "1 day" 700; fill "$T" "$n" 15m "15 minutes" 365 335; say "  $(sizes "$T")"
    reads "$T" "$n" 1d "1d " ; reads "$T" "$n" 15m "15m"
  done
  say "  free now: host C: $(( $(free_kb)/1024/1024 )) GB, VM disk $(( $(vm_kb)/1024/1024 )) GB"
}

say "timescaledb $(q -c "select extversion from pg_extension where extname='timescaledb'")  postgres $(q -c 'show server_version')  $(date -Is)"
if [ "${SCENARIO:-step1a}" = step2 ]; then
  step2
else
  run_size 12k   24  1d  "1 day"     700
  run_size 157k  24  15m "15 minutes" 365
  run_size 3300k 500 15m "15 minutes" 365
fi
say "DONE"
