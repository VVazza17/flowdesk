#!/usr/bin/env bash
# Shared helpers for infra/smoke-slice*.sh. Source it; do not execute it.
#
# Why this exists instead of `docker compose up -d --wait`: as of Compose 5.6.1, --wait
# treats ANY exited container as a failure, including a one-shot that exited 0 (the
# `migrate` service). So scripts run plain `up -d` and use wait_ready below, which waits for
# long-running services to be healthy and requires one-shots to have exited 0. The product
# gate is unaffected: `docker compose up -d` is still the one command and still runs migrate.

# Services that run to completion by design. Everything else must stay up and healthy.
ONESHOT_SERVICES="migrate"

WAIT_TIMEOUT="${WAIT_TIMEOUT:-120}"

fail() { echo "FAIL: $1" >&2; exit 1; }

_is_oneshot() {
  case " $ONESHOT_SERVICES " in *" $1 "*) return 0 ;; *) return 1 ;; esac
}

# wait_ready [service...]  (no arguments = every service in the compose file)
wait_ready() {
  local services=("$@")
  if [ ${#services[@]} -eq 0 ]; then
    mapfile -t services < <(docker compose config --services | tr -d '\r')
  fi
  local svc id state health code deadline
  for svc in "${services[@]}"; do
    deadline=$((SECONDS + WAIT_TIMEOUT))
    while :; do
      id=$(docker compose ps -a -q "$svc" | head -n1)
      if [ -n "$id" ]; then
        state=$(docker inspect -f '{{.State.Status}}' "$id")
        if _is_oneshot "$svc"; then
          if [ "$state" = "exited" ]; then
            code=$(docker inspect -f '{{.State.ExitCode}}' "$id")
            [ "$code" = "0" ] || fail "one-shot '$svc' exited $code (docker compose logs $svc)"
            break
          fi
        else
          [ "$state" != "exited" ] && [ "$state" != "dead" ] \
            || fail "'$svc' is $state (docker compose logs $svc)"
          # Services without a healthcheck count as ready once running.
          health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}healthy{{end}}' "$id")
          if [ "$state" = "running" ] && [ "$health" = "healthy" ]; then
            break
          fi
        fi
      fi
      [ $SECONDS -lt $deadline ] || fail "timed out after ${WAIT_TIMEOUT}s waiting for '$svc'"
      sleep 1
    done
  done
}
