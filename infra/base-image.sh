#!/usr/bin/env bash
# Source it; do not execute it. Sets BASE_IMAGE to the Python base image reference (tag@sha256:digest)
# that backend/Dockerfile builds from.
#
# DECISION: the Dockerfile's FROM line is the one definition of the base image. Scripts that run a
# container of their own (CI checks, lock-deps, the smokes) read it from there instead of repeating
# the name, so the image CI audits, the image the locks are compiled in and the image that ships can
# never be different images. Bump procedure: docs/RUNBOOK-IMAGE-CVE.md.
#
# The digest is a multi-arch index digest, so the same reference resolves to the right image under
# --platform linux/amd64 and linux/arm64.
base_image() {
  local dockerfile ref
  dockerfile="$(dirname "${BASH_SOURCE[0]}")/../backend/Dockerfile"
  ref=$(tr -d '\r' < "$dockerfile" | sed -n 's/^FROM[[:space:]][[:space:]]*\([^[:space:]][^[:space:]]*\).*/\1/p' | head -n 1)
  case "$ref" in
    *@sha256:????????????????????????????????????????????????????????????????)
      printf '%s' "$ref" ;;
    *)
      echo "FAIL: backend/Dockerfile's first FROM is '${ref:-<none>}', not tag@sha256:<digest>. The base image must be pinned (docs/RUNBOOK-IMAGE-CVE.md)." >&2
      return 1 ;;
  esac
}

BASE_IMAGE="$(base_image)" || return 1
