#!/usr/bin/env bash
# F-6b smoke (2026-11-24): F3 late arrivals and the rolled-off exemption through the REAL worker.
#
# Pipes infra/smoke-m2-f3-rolling.py into the running worker container: the real image, the real
# Redis rate limiter, run_recorded and the poll task's own poll_all, against a SCRATCH database on
# the real Postgres that the probe creates, migrates to head and drops. macro_metrics is insert-only,
# so this never writes to the dev database. FRED is a stub shaped like what F-6b measured. Asserts:
#   - a series declared rolling polls past its rolled-off periods (rules 3 and 5), and every stored
#     row is exactly as it was; the same data on an undeclared series still refuses (the control)
#   - a period FRED adds at a vintage before captured_through is accepted on its own fetch, with
#     available_at = the accepting run's start and FRED's date in provider_claimed_available_at
#   - a period after captured_through in the same run is dated by FRED (audit column NULL)
#   - the next poll is quiet (0 rows): the late row reads back as FRED's vintage
# Run from the repo root (needs a .env and a running stack built from this tree).
set -euo pipefail

set -a; . ./.env; set +a
. "$(dirname "$0")/lib.sh"

docker compose -p flowdesk up -d >/dev/null
wait_ready

echo "== the probe, in the worker container"
out=$(docker compose -p flowdesk exec -T worker python - < infra/smoke-m2-f3-rolling.py | tr -d '\r') \
  || { printf '%s\n' "$out"; fail "the probe failed"; }
printf '%s\n' "$out"
printf '%s\n' "$out" | tail -2 | grep -q '"result": "PASS"' || fail "the probe did not report PASS"
printf '%s\n' "$out" | grep -q '"dropped": "flowdesk_smoke_f3_' || fail "the scratch database was not dropped"
echo "PASS: F3 late arrival and rolled-off exemption through the real worker; dev database untouched"
