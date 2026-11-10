#!/usr/bin/env bash
# The scheduler: runs inside the backup container as the postgres user. Every 30 seconds it asks two
# questions. Is a backup due? Is an off-box copy pending? Runbook: docs/BACKUP.md
#
# DECISION: a loop here, not Celery Beat. The backup needs pg_dump 16 (the server's own version, so its
# archives restore on this image), and the worker image is the Python base. It is also the same reasoning
# as the heartbeat sender: a separate container whose failures the health machinery can see, because it
# writes the same task_runs table.
#
# Local first, off-box second, as two independent steps. A backup succeeds when it is encrypted and kept in
# /backups. The copy is retried on its own, so a desktop that is asleep is a failed copy, never a failed
# backup, and the copy catches up by itself when the desktop is back.
set -uo pipefail
here=$(dirname "$0")
# shellcheck source=lib.sh
. "$here/lib.sh"

BACKUP_DIR=${BACKUP_DIR:-/backups}
TICK=${BACKUP_TICK_SECONDS:-30}
# The *_SECONDS variants exist for the smoke test, which cannot wait minutes. Production uses the minutes.
RETRY_BASE=${BACKUP_RETRY_SECONDS:-$(( ${BACKUP_RETRY_MINUTES:-15} * 60 ))}
# Two caps, by whether a retry can help (lib.sh, exit_code_for_class): a destination that cannot be reached is
# retried soon (RETRY_CAP, 30 minutes) because the laptop coming home changes the outcome; the rest back off to
# the long cap (6 hours). Failures nothing but an edit can fix (exit 78) are not retried before the next slot.
RETRY_CAP=${BACKUP_RETRY_CAP_SECONDS:-$(( ${BACKUP_RETRY_CAP_MINUTES:-30} * 60 ))}
RETRY_LONG_CAP=${BACKUP_RETRY_LONG_CAP_SECONDS:-$(( ${BACKUP_RETRY_LONG_CAP_MINUTES:-360} * 60 ))}

if ! slot=$(slot_seconds "${BACKUP_AT:-03:00}"); then
  jlog error backup_config_invalid "detail=BACKUP_AT must be HH:MM (24-hour), got ${BACKUP_AT:-}"
  exit 1
fi
jlog info backup_scheduler_started "at=${BACKUP_AT:-03:00}" "tz=${TZ:-UTC}" \
  "offbox=$([ -n "${BACKUP_SSH_HOST:-}" ] && echo configured || echo not_configured)"

stop=0
trap 'stop=1' TERM INT

backup_delay=$RETRY_BASE
copy_delay=$RETRY_BASE
next_backup_try=0
next_copy_try=0
copy_pending=1   # on start: a restart is also a catch-up, and rsync only sends what is missing

while [ "$stop" -eq 0 ]; do
  now=$(date +%s)

  last=$(latest_backup_epoch "$BACKUP_DIR")
  if backup_due "$now" "$(local_seconds_of_day)" "$slot" "$last" && [ "$now" -ge "$next_backup_try" ]; then
    "$here/run-backup.sh"
    rc=$?
    if [ "$rc" -eq 0 ]; then
      backup_delay=$RETRY_BASE
      next_backup_try=0
      copy_pending=1
      next_copy_try=0
    else
      next_slot=$(( $(last_slot_epoch "$now" "$(local_seconds_of_day)" "$slot") + 86400 ))
      read -r next_backup_try backup_delay < <(retry_plan "$rc" "$now" "$backup_delay" "$RETRY_BASE" "$RETRY_CAP" "$RETRY_LONG_CAP" "$next_slot")
      if [ "$rc" -eq "$EX_CONFIG" ]; then
        jlog warning backup_not_retried "detail=a configuration problem; no retry before the next slot. Fix it, then docker compose restart backup to retry now"
      else
        jlog warning backup_will_retry "in_seconds=$((next_backup_try - now))"
      fi
    fi
  fi

  if [ "$copy_pending" -eq 1 ] && [ "$now" -ge "$next_copy_try" ] && ls "$BACKUP_DIR"/*.tar.age > /dev/null 2>&1; then
    "$here/run-copy.sh"
    rc=$?
    if [ "$rc" -eq 0 ]; then
      copy_pending=0
      copy_delay=$RETRY_BASE
    else
      next_slot=$(( $(last_slot_epoch "$now" "$(local_seconds_of_day)" "$slot") + 86400 ))
      read -r next_copy_try copy_delay < <(retry_plan "$rc" "$now" "$copy_delay" "$RETRY_BASE" "$RETRY_CAP" "$RETRY_LONG_CAP" "$next_slot")
      if [ "$rc" -eq "$EX_CONFIG" ]; then
        jlog warning copy_not_retried "detail=a configuration problem; no retry before the next slot. Fix it, then docker compose restart backup to retry now"
      else
        jlog warning copy_will_retry "in_seconds=$((next_copy_try - now))"
      fi
    fi
  fi

  # `sleep & wait` so a stop signal ends the sleep at once instead of after up to TICK seconds.
  sleep "$TICK" &
  wait $! || true
done
jlog info backup_scheduler_stopped
