#!/usr/bin/env bash
# Shared by the backup container's scripts and by prune.sh. Source it; it has no side effects on load.
# Runbook: docs/BACKUP.md

# One JSON object per line on stdout, like the rest of the stack. Values have quotes, backslashes and
# newlines stripped and are cut to 300 characters, so a stray error text can never break the line.
#   jlog LEVEL EVENT [key=value ...]
jlog() {
  local level=$1 event=$2 out="" kv k v
  shift 2
  for kv in "$@"; do
    k=${kv%%=*}
    v=${kv#*=}
    v=$(printf '%s' "$v" | tr -d '"\\\r' | tr '\n' ' ' | cut -c1-300)
    out+=",\"$k\":\"$v\""
  done
  printf '{"event":"%s","level":"%s","timestamp":"%s"%s}\n' "$event" "$level" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$out"
}

# --- time and scheduling (pure arithmetic apart from the two `date` readers) -----------------------

# Seconds since the epoch for the UTC stamp inside a backup file name (db_20261108T033000Z.tar.age).
name_epoch() {
  local base=${1##*/} stamp
  stamp=${base%.tar.age}
  stamp=${stamp##*_}
  [[ $stamp =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || return 1
  date -u -d "${stamp:0:4}-${stamp:4:2}-${stamp:6:2} ${stamp:9:2}:${stamp:11:2}:${stamp:13:2}" +%s
}

# The newest backup's epoch in a directory, or 0 if there is none. The files are the record of what has
# been backed up: a wiped volume means "due now", which is the right answer.
latest_backup_epoch() {
  local dir=$1 f e best=0
  for f in "$dir"/*.tar.age; do
    [ -e "$f" ] || continue
    e=$(name_epoch "$f") || continue
    [ "$e" -gt "$best" ] && best=$e
  done
  echo "$best"
}

local_seconds_of_day() {
  echo $((10#$(date +%H) * 3600 + 10#$(date +%M) * 60 + 10#$(date +%S)))
}

# HH:MM -> seconds into the day. Refuses anything else, so a typo in .env fails at start, not at 3 a.m.
slot_seconds() {
  [[ $1 =~ ^([01][0-9]|2[0-3]):([0-5][0-9])$ ]] || return 1
  echo $((10#${BASH_REMATCH[1]} * 3600 + 10#${BASH_REMATCH[2]} * 60))
}

# The most recent scheduled instant at or before NOW.
#   last_slot_epoch NOW SECONDS_INTO_LOCAL_DAY SLOT_SECONDS
# DECISION: plain arithmetic on the local day, so on the two days a year the clocks change the slot can be
# an hour off. A nightly backup does not care; correctness here is "once per day, after the slot".
last_slot_epoch() {
  local now=$1 sod=$2 slot=$3 t
  t=$((now - sod + slot))
  [ "$t" -le "$now" ] || t=$((t - 86400))
  echo "$t"
}

# Is a backup due? True when the newest one is older than the most recent slot. That one rule is also the
# catch-up: a laptop that slept through 03:00 is due the moment it wakes, once, and not again until the
# next slot.   backup_due NOW SECONDS_INTO_LOCAL_DAY SLOT_SECONDS LAST_BACKUP_EPOCH
backup_due() {
  local slot_epoch
  slot_epoch=$(last_slot_epoch "$1" "$2" "$3")
  [ "$4" -lt "$slot_epoch" ]
}

# After a failure wait longer each time, up to a cap. Without this a missing recipient key would write a
# failed task_runs row every retry interval, forever.   next_delay CURRENT BASE CAP
next_delay() {
  local d=$(($1 * 2))
  [ "$d" -ge "$2" ] || d=$2
  [ "$d" -le "$3" ] || d=$3
  echo "$d"
}

# --- retention --------------------------------------------------------------------------------------

# Reads backup file names on stdin and prints the ones to delete. Keeps the newest backup of each of the
# newest KEEP_DAILY distinct UTC days, plus the newest of each of the newest KEEP_WEEKLY seven-day
# buckets; a file survives if either rule wants it. Names that do not look like backups are ignored, so
# nothing else in the directory can ever be deleted.   retention_delete KEEP_DAILY KEEP_WEEKLY
# DECISION: "weeks" are 7-day buckets counted from the epoch, not ISO calendar weeks. Same count of weekly
# copies, no calendar library.
retention_delete() {
  local kd=$1 kw=$2
  awk '{ if (match($0, /_[0-9]{8}T[0-9]{6}Z\.tar\.age$/)) print substr($0, RSTART + 1, 15), $0 }' |
    sort -r |
    awk -v kd="$kd" -v kw="$kw" '
      # Days since 1970-01-01 for a civil date (Howard Hinnant, days_from_civil).
      function dfc(y, m, d,   yy, mm, era, yoe, doy, doe) {
        yy = (m <= 2) ? y - 1 : y
        era = int(yy / 400)
        yoe = yy - era * 400
        mm = (m > 2) ? m - 3 : m + 9
        doy = int((153 * mm + 2) / 5) + d - 1
        doe = yoe * 365 + int(yoe / 4) - int(yoe / 100) + doy
        return era * 146097 + doe - 719468
      }
      {
        day = dfc(substr($1, 1, 4) + 0, substr($1, 5, 2) + 0, substr($1, 7, 2) + 0)
        wk = int(day / 7)
        keep = 0
        if (!(day in dayseen)) { dayseen[day] = 1; if (++ndays <= kd) keep = 1 }
        if (!(wk in wkseen)) { wkseen[wk] = 1; if (++nwk <= kw) keep = 1 }
        if (!keep) print $2
      }'
}

# Delete what retention_delete names, inside DIR only.   prune_dir DIR KEEP_DAILY KEEP_WEEKLY [--dry-run]
prune_dir() {
  local dir=$1 kd=$2 kw=$3 dry=${4:-} name n=0
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    n=$((n + 1))
    if [ "$dry" = "--dry-run" ]; then
      echo "would delete $name"
    else
      rm -f -- "${dir:?}/$name"
      jlog info backup_pruned "file=$name"
    fi
  done < <(ls -1 "$dir" | retention_delete "$kd" "$kw")
  return 0
}

# --- the task_runs ledger, written with psql ----------------------------------------------------------
# The same table and vocabulary the API reads (app/tasks/names.py). PG* variables come from the compose
# environment.

psqlc() { psql -X -q -t -A -v ON_ERROR_STOP=1 "$@"; }

# Prints the new row's id.   task_start TASK_NAME
task_start() {
  psqlc -c "INSERT INTO task_runs (task_name, started_at, heartbeat_at, status, attempt)
            VALUES ('$1', now(), now(), 'running', 1) RETURNING id" | head -n 1
}

#   task_finish ID STATUS [ERROR_CLASS [ERROR_MESSAGE]]
task_finish() {
  local id=$1 status=$2 cls=${3:-} msg=${4:-}
  [[ $id =~ ^[0-9]+$ ]] || return 1
  psqlc -v id="$id" -v st="$status" -v cls="$cls" -v msg="${msg:0:500}" <<'SQL'
UPDATE task_runs
   SET status = :'st', completed_at = now(), heartbeat_at = now(),
       error_class = NULLIF(:'cls', ''), error_message = NULLIF(:'msg', '')
 WHERE id = :id;
SQL
}

# A step that is switched off. Neither a success nor a failure; the API reports it as not_configured.
#   task_skipped TASK_NAME ERROR_CLASS MESSAGE
task_skipped() {
  psqlc -v name="$1" -v cls="$2" -v msg="${3:0:500}" <<'SQL'
INSERT INTO task_runs (task_name, started_at, completed_at, heartbeat_at, status, attempt, error_class, error_message)
VALUES (:'name', now(), now(), now(), 'skipped', 1, :'cls', :'msg');
SQL
}

# --- configuration checks --------------------------------------------------------------------------

# The recipients file must hold at least one age public key. A file with only comments is the committed
# placeholder, and the backup then fails closed: it never writes an unencrypted copy.
recipients_ok() {
  [ -s "$1" ] && grep -Eq '^age1[0-9a-z]{50,}$' "$1"
}
