#!/usr/bin/env bash
# M1 slice 2 smoke test: the API image builds for amd64 AND arm64 (arm64 is skipped with a notice
# if this machine has no emulation), the host-platform image runs, /health/live answers 200, and the
# API is bound to loopback only.
# Run from the repo root (needs a .env; see .env.example).
set -euo pipefail

set -a; . ./.env; set +a
. "$(dirname "$0")/lib.sh"
API_PORT="${API_PORT:-8000}"

# A multi-platform manifest list needs either the containerd image store or a
# docker-container builder; the classic `docker` driver can do neither.
builder=()
if ! docker info --format '{{.DriverStatus}}' | grep -q 'io.containerd.snapshotter'; then
  docker buildx inspect flowdesk-multiarch >/dev/null 2>&1 \
    || docker buildx create --name flowdesk-multiarch --driver docker-container >/dev/null
  builder=(--builder flowdesk-multiarch)
fi

# A red that always means "this laptop has no emulation" is a red people learn to ignore, so that one
# case skips the arm64 half with a notice. Only that case: any other probe failure is a real failure.
platforms=linux/amd64,linux/arm64
built="amd64+arm64 build"
if ! probe=$(docker run --rm --platform linux/arm64 python:3.12-slim true 2>&1); then
  case "$probe" in
    *"exec format error"*)
      platforms=linux/amd64
      built="amd64 build, arm64 SKIPPED"
      echo "NOTICE: arm64 SKIPPED, no emulation on this machine. CI's arm64 job is the gate (.github/workflows/ci.yml)."
      ;;
    *) fail "could not probe arm64 emulation: $probe" ;;
  esac
fi

# 1. Every available platform must compile. No --load: the docker exporter cannot export a manifest
#    list. The result stays in the build cache, which is all this step needs to prove.
docker buildx build "${builder[@]}" --platform "$platforms" ./backend \
  || fail "build for $platforms failed"

# 2. Run the host-platform image through compose.
docker compose up -d --build api
wait_ready api

code=$(curl -s -o /tmp/live.json -w '%{http_code}' "http://127.0.0.1:${API_PORT}/health/live")
[ "$code" = "200" ] || fail "/health/live returned HTTP $code"
grep -q '"status":"ok"' /tmp/live.json || fail "/health/live body was $(cat /tmp/live.json)"

api_bind=$(docker compose port api 8000)
case "$api_bind" in
  127.0.0.1:*) ;;
  *) fail "api bound to '$api_bind', expected 127.0.0.1" ;;
esac

echo "OK: slice 2 smoke passed (api $api_bind, $built, /health/live 200)"
