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
  stamp=${stamp%%.r[0-9]*}   # a retry object, <db>_<stamp>.r<N>.tar.age, is the same backup
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

# --- what a failure means, and whether another attempt can help ---------------------------------------
#
# Two exit codes, borrowed from sysexits.h, tell the scheduler how to retry. Everything else that fails exits 1.
#   75 EX_TEMPFAIL  a retry soon can change the outcome (the laptop comes home; Postgres finishes restarting)
#   78 EX_CONFIG    no number of attempts fixes it: a file nobody has edited, a setting nobody has changed
#   76 EX_PROTOCOL  a pinned host key did not match. Retried like any long-cap failure; it has its own number only so
#                   the scheduler can tell the monitor once (backup-loop.sh), since each attempt is a new process
EX_TRANSIENT=75
EX_HOSTKEY=76
EX_CONFIG=78

# Which exit code a failure class gets.   exit_code_for_class CLASS
# DECISION: split by whether a retry can help, not by step. Only a destination that cannot be reached is
# transient. NoRecipient, ConfigInvalid and BadDatabaseName are configuration. Everything else gets the long
# retry cap, deliberately including DumpFailed (pg_dump is expensive and a half hour rarely changes the answer)
# and AuthRefused / HostKeyChanged: the fix for those may be made on the desktop, which this machine cannot see,
# so it keeps trying, slowly, and a container restart tries at once.
exit_code_for_class() {
  case $1 in
    Unreachable) echo "$EX_TRANSIENT" ;;
    HostKeyChanged) echo "$EX_HOSTKEY" ;;
    NoRecipient | ConfigInvalid | BadDatabaseName) echo "$EX_CONFIG" ;;
    *) echo 1 ;;
  esac
}

# What an ssh/sftp failure was, from its exit code and the text it wrote to stderr (a file).
#   classify_ssh_failure RC STDERR_FILE [KNOWN_HOSTS_FILE HOST PORT]      prints a class
#
# sftp exits 255 when ssh itself failed, 124 when our `timeout` killed it, and anything else when the SFTP
# session ran and an operation failed. Only 255 is read for text.
#   HostKeyChanged  a key IS pinned for this host and what answered did not match it. Something other than the
#                   desktop may have answered: a security signal, not an outage.
#   ConfigInvalid   "No <type> host key is known for <host>" and NOTHING is pinned for the host: setup state, the
#                   key was never pinned, so nothing can be impersonating it. The pinning step in docs/BACKUP.md.
#                   DECISION: OpenSSH prints the same line when the host HAS pinned keys but the server offers a
#                   type that is not among them, which is a mismatch. So the optional known_hosts arguments
#                   decide: if ssh-keygen finds the host in the file, it is HostKeyChanged. Without them the
#                   line reads as setup state.
#   AuthRefused     the server answered and refused the key.
#   Unreachable     everything else: resolution failures, timeouts, no route, connection refused and closed,
#                   AND any message not recognised. DECISION: OpenSSH's wording is not a stable interface, so
#                   an unrecognised message must never produce a confident wrong class; "host away" is the
#                   mildest wrong answer and the monitor still pages when copies stop.
# The patterns are anchored to the start of a line, as OpenSSH prints them (checked against OpenSSH 10.2), so a
# phrase quoted in the middle of some other message does not count. This needs ssh at LogLevel=ERROR: with -q
# every failure collapses to "Connection closed" and nothing here could tell them apart.
classify_ssh_failure() {
  local rc=$1 file=$2 kh=${3:-} host=${4:-} port=${5:-22}
  case $rc in
    255) ;;
    124) echo Unreachable; return 0 ;;
    *) echo SftpFailed; return 0 ;;
  esac
  # Checked first: this line is followed by "Host key verification failed.", which would otherwise match below.
  if grep -Eq '^No [A-Za-z0-9-]+ host key is known for ' "$file" 2> /dev/null; then
    if [ -n "$kh" ] && [ -n "$host" ]; then
      [ "$port" = 22 ] || host="[$host]:$port"
      if ssh-keygen -F "$host" -f "$kh" > /dev/null 2>&1; then echo HostKeyChanged; return 0; fi
    fi
    echo ConfigInvalid
  elif grep -Eq '^Host key verification failed\.|^@ +WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!|^Host key for .+ has changed' "$file" 2> /dev/null; then
    echo HostKeyChanged
  elif grep -Eq '^[^ ]+: Permission denied \(' "$file" 2> /dev/null; then
    echo AuthRefused
  else
    echo Unreachable
  fi
}

# Is HOST a bare IP address rather than a name?   host_is_bare_ip HOST   (true = bare IP)
# DECISION: BACKUP_SSH_HOST must be a name, because HostKeyChanged pages the owner at once (run-copy.sh). A private
# IP is not an identity: on another network the same address belongs to someone else's machine, the pinned key
# does not match, and the owner is paged for going to work. A name that resolves only at home is Unreachable
# everywhere else, which is the quiet answer. So: anything with a colon (IPv6, bracketed or not) or made only of
# digits and dots (IPv4, in any spelling) is refused.
host_is_bare_ip() {
  case $1 in
    '') return 1 ;;
    *:* | \[*) return 0 ;;
    *[!0-9.]*) return 1 ;;
    *) return 0 ;;
  esac
}

# Said wherever a bare IP is refused.
bare_ip_message() { # HOST
  printf 'BACKUP_SSH_HOST is a bare IP address (%s). Use a hostname that resolves only on your home network. On any other network that address can belong to a stranger'"'"'s machine, which reads as a changed host key and pages you. A name that does not resolve away from home is just Unreachable, the quiet answer. A hosts-file or extra_hosts entry that maps the name to the IP defeats this and is the same thing as a bare IP.' "$1"
}

# Tell the monitor now. POSTs to the ping URL's /fail. Prints nothing (the URL is a credential); returns 0 only if the
# monitor accepted it.   signal_monitor_fail URL
signal_monitor_fail() {
  curl -fsS -m 10 --retry 2 -o /dev/null -d HostKeyChanged "$(fail_ping_url "$1")" 2> /dev/null
}

# The monitor's failure endpoint for a ping URL: /fail appended to the path, ahead of any query string.
#   fail_ping_url https://hc-ping.com/<uuid>  ->  https://hc-ping.com/<uuid>/fail
fail_ping_url() {
  local u=$1 base query=""
  base=${u%%\?*}
  [ "$base" = "$u" ] || query="?${u#*\?}"
  printf '%s/fail%s' "${base%/}" "$query"
}

# After a failed attempt: when to try again, and the delay to carry into the next failure. Prints
# "NEXT_TRY_EPOCH NEW_DELAY".   retry_plan RC NOW DELAY BASE TRANSIENT_CAP LONG_CAP NEXT_SLOT_EPOCH
#   78: not retried before the next scheduled slot (one row a day, not one every few hours). Held in the
#       scheduler's memory only, so restarting the container, which is also how a changed key or setting is
#       picked up, tries again at once. Nothing is persisted.
#   75: backs off 15, 30, ... up to TRANSIENT_CAP.
#   else: the same, up to LONG_CAP.
# A delay already above the cap in force is brought down to it, so a short-cap failure after long waits does
# not inherit them.
retry_plan() {
  local rc=$1 now=$2 delay=$3 base=$4 cap=$5 long=$6 next_slot=$7 limit wait
  if [ "$rc" -eq "$EX_CONFIG" ]; then
    echo "$next_slot $base"
    return 0
  fi
  limit=$long
  [ "$rc" -ne "$EX_TRANSIENT" ] || limit=$cap
  wait=$delay
  [ "$wait" -le "$limit" ] || wait=$limit
  echo "$((now + wait)) $(next_delay "$wait" "$base" "$limit")"
}

# --- retention --------------------------------------------------------------------------------------

# Reads backup file names on stdin and prints the ones to delete. Keeps the newest backup of each of the
# newest KEEP_DAILY distinct UTC days, plus the newest of each of the newest KEEP_WEEKLY seven-day
# buckets; a file survives if either rule wants it. Names that do not look like backups are ignored, so
# nothing else in the directory can ever be deleted.   retention_delete KEEP_DAILY KEEP_WEEKLY
# DECISION: "weeks" are 7-day buckets counted from the epoch, not ISO calendar weeks. Same count of weekly
# copies, no calendar library.
#
# Retry objects (<db>_<stamp>.r<N>.tar.age, made when an upload was interrupted; see run-copy.sh) sort
# AFTER the plain name for the same stamp, so the highest-numbered object of a backup is the one kept and
# the short ones below it are deleted. That is right because a retry is only ever made when every lower
# object is short. Known edge: if the newest backup of a day has ONLY short objects when this runs (its upload
# was interrupted and not yet retried), an older complete backup the same day is the one deleted. One backup
# a day makes that rare, and the retry completes the newer one; it is why the destination is pruned weekly,
# not continuously.
retention_delete() {
  local kd=$1 kw=$2
  awk '{
    if (match($0, /_[0-9]{8}T[0-9]{6}Z(\.r[0-9]+)?\.tar\.age$/)) {
      st = substr($0, RSTART + 1, 15)
      at = 0
      if (match($0, /\.r[0-9]+\.tar\.age$/)) at = substr($0, RSTART + 2, RLENGTH - 10) + 0
      print st, at, $0
    }
  }' |
    sort -k1,1r -k2,2nr |
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
        if (!keep) print $3
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
