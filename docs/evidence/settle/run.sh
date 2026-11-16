#!/usr/bin/env bash
# One settle snapshot. Retries while Docker Desktop comes up after a wake. Log: docs/evidence/settle/run.log
cd "$(dirname "$0")" || exit 1
for i in $(seq 1 20); do docker info >/dev/null 2>&1 && break; sleep 30; done
docker image inspect flowdesk-settle-probe:1.8.0 >/dev/null 2>&1 || { . ../../../infra/base-image.sh && docker build -q --build-arg BASE_IMAGE="$BASE_IMAGE" -t flowdesk-settle-probe:1.8.0 . ; }
{ date -u +%FT%TZ; MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd):/w" flowdesk-settle-probe:1.8.0 python /w/probe.py.txt /w 2>&1 | tail -5; } >> run.log 2>&1
