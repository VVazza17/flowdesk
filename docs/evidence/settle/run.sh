#!/usr/bin/env bash
# One settle snapshot. Waits up to 30 minutes (60 x 30 s) for Docker Desktop after a wake. Log: docs/evidence/settle/run.log
# Exits NON-ZERO, and logs a MISSED line, when Docker stays unreachable, the probe fails, or no new snapshot file was written
# (Kyle, 2026-11-20: before this it exited 0 on both failures of 11-18 and 11-19, so Task Scheduler showed success). A missed
# snapshot is recorded as missed (missed.txt) and never filled in afterwards: a later run is its own snapshot, at its own time.
cd "$(dirname "$0")" || exit 1
started=$(date -u +%FT%TZ)
missed() { printf '%s\nMISSED: %s (%s)\n' "$started" "$1" "$(date -u +%FT%TZ)" >> run.log; exit "$2"; }

up=
for i in $(seq 1 60); do docker info >/dev/null 2>&1 && { up=1; break; }; sleep 30; done
[ -n "$up" ] || missed "Docker unreachable after 30 minutes" 2

if ! docker image inspect flowdesk-settle-probe:1.8.0 >/dev/null 2>&1; then
  . ../../../infra/base-image.sh || missed "could not read the base image pin" 3
  docker build -q --build-arg BASE_IMAGE="$BASE_IMAGE" -t flowdesk-settle-probe:1.8.0 . >/dev/null || missed "probe image build failed" 3
fi

before=$(ls snap-*.json 2>/dev/null | wc -l)
out=$(MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd):/w" flowdesk-settle-probe:1.8.0 python /w/probe.py.txt /w 2>&1)
rc=$?
after=$(ls snap-*.json 2>/dev/null | wc -l)
{ printf '%s\n' "$started"; printf '%s\n' "$out" | tail -5; } >> run.log
[ "$rc" -eq 0 ] || missed "probe exited $rc" 4
[ "$after" -gt "$before" ] || missed "probe exited 0 but wrote no snapshot file" 5
exit 0
