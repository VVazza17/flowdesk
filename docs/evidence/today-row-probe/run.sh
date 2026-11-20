#!/usr/bin/env bash
# One today-row probe run (Kyle's item 3, Fri 2026-11-20). Scheduled at 09:05, 09:35 and 10:05 ET as one-time tasks
# FlowDesk-today-row-0905 / -0935 / -1005. Waits up to 30 minutes for Docker; if Docker stays down, or the probe fails, it
# writes a FAILED line to the evidence file and exits non-zero (loud: Task Scheduler shows the code). A failed run is not
# re-run or filled in. Read-only: the probe runs in the worker image with --no-deps and opens no database connection.
cd "$(dirname "$0")/../../.." || exit 1
out=docs/evidence/2026-11-20-today-row-probe.txt
label="${1:-manual}"
started=$(date -u +%FT%TZ)
fail() { printf '=== %s (%s) FAILED at %s: %s\n\n' "$label" "$started" "$(date -u +%FT%TZ)" "$1" >> "$out"; exit "$2"; }

up=
for i in $(seq 1 60); do docker info >/dev/null 2>&1 && { up=1; break; }; sleep 30; done
[ -n "$up" ] || fail "Docker unreachable after 30 minutes" 2

probe_dir=$(cd docs/evidence/today-row-probe && (pwd -W 2>/dev/null || pwd))
report=$(MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -T -e PYTHONPATH=/srv -v "$probe_dir:/probe:ro" worker python /probe/probe.py 2>&1)
rc=$?
{ printf '=== %s (task start %s)\n' "$label" "$started"; printf '%s\n\n' "$report"; } >> "$out"
[ "$rc" -eq 0 ] || fail "probe exited $rc (output above)" 3
exit 0
