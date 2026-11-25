#!/usr/bin/env bash
# Shared by the backup container's scripts. Source it; it has no side effects on load.
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
# Three exit codes tell the scheduler how to retry and whether to page. Everything else that fails exits 1.
#   75 EX_TEMPFAIL  a retry soon can change the outcome (the network or B2 is back; Postgres finishes restarting)
#   76 EX_ALERT     something the owner must hear about NOW: a refused key, a key holding capabilities it must not
#                   have, an upload that is not locked, a delete marker FlowDesk did not make, the bucket over
#                   budget. Retried like any long-cap failure; the scheduler POSTs to the monitor's /fail once per
#                   occurrence (backup-loop.sh), since each attempt is a new process and cannot remember it did.
#   78 EX_CONFIG    no number of attempts fixes it: a file nobody has edited, a setting nobody has changed
EX_TRANSIENT=75
EX_ALERT=76
EX_CONFIG=78

# Which exit code a failure class gets.   exit_code_for_class CLASS
# DECISION: split by whether a retry can help, and by whether it pages. Only an unreachable or unavailable
# destination is transient. Configuration is NoRecipient, ConfigInvalid, BadDatabaseName. The alert classes
# (AuthRefused, CapabilityTooBroad, NotLocked, Tampered, OverBudget) page at once and back off to the long cap: the
# fix is made in Backblaze's console, which this machine cannot see, so it keeps trying, slowly, and a container
# restart tries at once. Everything else (DumpFailed, UploadFailed, SizeMismatch, ...) takes the long cap.
exit_code_for_class() {
  case $1 in
    Unreachable | Unavailable) echo "$EX_TRANSIENT" ;;
    AuthRefused | CapabilityTooBroad | NotLocked | Tampered | OverBudget) echo "$EX_ALERT" ;;
    NoRecipient | ConfigInvalid | BadDatabaseName) echo "$EX_CONFIG" ;;
    *) echo 1 ;;
  esac
}

# --- Backblaze B2, through its S3-compatible API (2026-11-25; docs/BACKUP.md) -------------------------------
#
# Two clients, each for what the other cannot do. rclone uploads (MIT, pinned by digest in the Dockerfile); curl's
# --aws-sigv4 reads what rclone does not expose: an object's retention, and the version listing with its delete
# markers. Neither ever sees the secret on its command line: rclone takes it from RCLONE_CONFIG_* in its own
# environment, curl from a config on stdin. NOTHING a provider returns is logged or stored: B2's (and S3's) error
# bodies echo the access key ID, which is itself a secret here, so only a class name ever leaves this file.

# The SigV4 region for an endpoint: s3.<region>.backblazeb2.com -> <region>. B2_REGION overrides it (the stand-in).
#   s3_region ENDPOINT
s3_region() {
  if [ -n "${B2_REGION:-}" ]; then echo "$B2_REGION"; return 0; fi
  local host=${1#*://}
  host=${host%%/*}
  [[ $host =~ ^s3\.([a-z0-9-]+)\.backblazeb2\.com$ ]] || return 1
  echo "${BASH_REMATCH[1]}"
}

# One signed S3 request. Body to OUT, HTTP code on stdout ("000" when nothing answered). The key pair comes from
# S3_KEY_ID / S3_SECRET in the environment and reaches curl on stdin, never on its argv.
#   s3_request METHOD PATH_AND_QUERY OUT [extra curl args...]
s3_request() {
  local method=$1 path=$2 out=$3 region
  shift 3
  region=$(s3_region "$B2_ENDPOINT") || { echo 000; return 1; }
  printf 'user = "%s:%s"\n' "$S3_KEY_ID" "$S3_SECRET" |
    curl -sS -K - -o "$out" -w '%{http_code}' -m 60 --aws-sigv4 "aws:amz:$region:s3" -X "$method" "$@" \
      "${B2_ENDPOINT%/}/$path" 2> /dev/null || true
}

# What an HTTP answer was.   classify_http CODE   (prints a class; "ok" for 2xx)
#   000 (nothing answered), 429 and 5xx are transient. 401/403 are the key. 404 is the bucket or the object not
#   being there: configuration. Anything else is S3Failed, the long cap.
classify_http() {
  case $1 in
    2??) echo ok ;;
    000 | 429 | 5??) echo Unreachable ;;
    401 | 403) echo AuthRefused ;;
    404) echo ConfigInvalid ;;
    *) echo S3Failed ;;
  esac
}

# What an rclone failure was, from its exit code and stderr (a file). Unrecognised text is UploadFailed (the long
# cap), never a confident wrong class.   classify_rclone RC ERRFILE
classify_rclone() {
  local rc=$1 file=$2
  [ "$rc" -ne 0 ] || { echo ok; return 0; }
  [ "$rc" -ne 124 ] || { echo Unreachable; return 0; }   # our own timeout
  if grep -Eq 'StatusCode: 40[13]|SignatureDoesNotMatch|InvalidAccessKeyId|AccessDenied|api error Forbidden' "$file" 2> /dev/null; then
    echo AuthRefused
  elif grep -Eq 'dial tcp|no such host|connection refused|i/o timeout|TLS handshake timeout|context deadline exceeded|StatusCode: (429|5[0-9][0-9])' "$file" 2> /dev/null; then
    echo Unreachable
  else
    echo UploadFailed
  fi
}

# The version listing of an S3 ListObjectVersions answer, one line per entry:
#   V <key> <size> <versionId> <isLatest>      a stored version
#   D <key> <versionId> <isLatest>             a delete marker (B2: a "hide")
# and, last, "T <true|false> <nextKeyMarker> <nextVersionIdMarker>" for paging. Keys are FlowDesk's own names
# (letters, digits, _ . / -), so plain text extraction is enough; a key with XML escapes would print escaped.
#   parse_versions FILE
parse_versions() {
  tr '<' '\n' < "$1" | awk -F'>' '
    $1 == "Version"           { kind = "V"; k = ""; s = ""; v = ""; l = ""; next }
    $1 == "DeleteMarker"      { kind = "D"; k = ""; v = ""; l = ""; next }
    $1 == "/Version"          { print "V", k, s, v, l; kind = ""; next }
    $1 == "/DeleteMarker"     { print "D", k, v, l; kind = ""; next }
    kind != "" && $1 == "Key"       { k = $2 }
    kind != "" && $1 == "Size"      { s = $2 }
    kind != "" && $1 == "VersionId" { v = $2 }
    kind != "" && $1 == "IsLatest"  { l = $2 }
    kind == "" && $1 == "IsTruncated"         { t = $2 }
    kind == "" && $1 == "NextKeyMarker"       { nk = $2 }
    kind == "" && $1 == "NextVersionIdMarker" { nv = $2 }
    END { print "T", (t == "" ? "false" : t), (nk == "" ? "-" : nk), (nv == "" ? "-" : nv) }'
}

# Every version and delete marker in the bucket (all pages), parsed, into OUT. Prints the HTTP class: "ok", or
# what went wrong (OUT is then incomplete and must not be used).   list_all_versions BUCKET PREFIX OUT
list_all_versions() {
  local bucket=$1 prefix=$2 out=$3 km="" vm="" page code cls q trunc
  page=$(mktemp)
  : > "$out"
  while :; do
    q="$bucket?versions&max-keys=1000"
    [ -z "$prefix" ] || q+="&prefix=$prefix"
    [ -z "$km" ] || q+="&key-marker=$km"
    [ -z "$vm" ] || q+="&version-id-marker=$vm"
    code=$(s3_request GET "$q" "$page")
    cls=$(classify_http "$code")
    if [ "$cls" != ok ]; then rm -f "$page" "$page.p"; echo "$cls"; return 0; fi
    parse_versions "$page" > "$page.p"
    grep -v '^T ' "$page.p" >> "$out"
    read -r _ trunc km vm < <(grep '^T ' "$page.p")
    rm -f "$page.p"
    [ "$trunc" = true ] || break
    [ "$km" != "-" ] || km=""
    [ "$vm" != "-" ] || vm=""
  done
  rm -f "$page"
  echo ok
}

# Every version's bytes, hidden ones included (whether B2 bills hidden versions is not documented: ASSUMED billed).
#   bucket_bytes < parsed-listing
bucket_bytes() { awk '$1 == "V" { n += $3 } END { printf "%d\n", n }'; }

# Delete markers under PREFIX. FlowDesk never makes one there, so any is tampering (or a person in the console).
#   delete_markers PREFIX < parsed-listing
delete_markers() { awk -v p="$1" '$1 == "D" && index($2, p) == 1 { print $2 }'; }

# The bucket against B2's always-free 10 GB (Kyle, 2026-11-25): ok, warn from 80%, over from 95%. The same numbers
# as app/settings.py (BACKUP_BUDGET_*), pinned equal by backend/tests/test_backup_budget.py.
#   budget_state BYTES BUDGET
BUDGET_WARN_PERCENT=80
BUDGET_OVER_PERCENT=95
budget_state() {
  if [ $(($1 * 100)) -ge $(($2 * BUDGET_OVER_PERCENT)) ]; then echo over
  elif [ $(($1 * 100)) -ge $(($2 * BUDGET_WARN_PERCENT)) ]; then echo warn
  else echo ok
  fi
}

# Is an object locked as the bucket's default should lock it? GOVERNANCE, retained at least DAYS-1 days from NOW
# (one day of slack: B2 rounds and the clocks differ). Reads an S3 GetObjectRetention answer.
#   retention_ok FILE NOW_EPOCH DAYS
retention_ok() {
  local mode until_s until_e
  mode=$(tr '<' '\n' < "$1" | awk -F'>' '$1 == "Mode" { print $2 }')
  until_s=$(tr '<' '\n' < "$1" | awk -F'>' '$1 == "RetainUntilDate" { print $2 }')
  [ "$mode" = GOVERNANCE ] || return 1
  [ -n "$until_s" ] || return 1
  until_e=$(date -u -d "$(printf '%s' "$until_s" | sed 's/T/ /; s/\.[0-9]*//; s/Z$//')" +%s 2> /dev/null) || return 1
  [ "$until_e" -ge $(($2 + ($3 - 1) * 86400)) ]
}

# The two keys' capabilities (docs/BACKUP.md, "The two keys"). Exactly these and no others; listBuckets and
# listAllBucketNames are needed for S3 compatibility (Backblaze's S3 app-keys page).
WRITE_CAPABILITIES="listBuckets listAllBucketNames listFiles writeFiles readFileRetentions"
READ_CAPABILITIES="listBuckets listAllBucketNames listFiles readFiles readFileRetentions"

# Judge a b2_authorize_account (v2) answer: the key must be restricted to BUCKET and hold no capability outside the
# set for MODE. Prints ok, CapabilityTooBroad (an extra capability, or no bucket restriction: pages), ConfigInvalid
# (another bucket, or a required capability missing) or CapabilityUnknown (the answer is not the shape expected:
# ASSUMED v2 shape, so a changed API fails closed instead of passing). With CAP_INFO_FILE set, also writes the
# bucket and capability NAMES there (not secret) for the probe to show.   judge_capabilities FILE BUCKET write|read
judge_capabilities() {
  local file=$1 bucket=$2 mode=$3 allowed caps name c extra="" missing=""
  allowed=$WRITE_CAPABILITIES
  [ "$mode" = write ] || allowed=$READ_CAPABILITIES
  name=$(jq -r '.allowed.bucketName // empty' "$file" 2> /dev/null) || { echo CapabilityUnknown; return 0; }
  caps=$(jq -r '.allowed.capabilities[]?' "$file" 2> /dev/null | tr '\n' ' ') || { echo CapabilityUnknown; return 0; }
  [ -n "$caps" ] || { echo CapabilityUnknown; return 0; }
  [ -z "${CAP_INFO_FILE:-}" ] || echo "bucket=${name:-<all buckets>} capabilities=${caps% }" > "$CAP_INFO_FILE"
  [ -n "$name" ] || { echo CapabilityTooBroad; return 0; }
  [ "$name" = "$bucket" ] || { echo ConfigInvalid; return 0; }
  for c in $caps; do
    case " $allowed " in *" $c "*) ;; *) extra+="$c " ;; esac
  done
  [ -z "$extra" ] || { echo CapabilityTooBroad; return 0; }
  for c in listFiles readFileRetentions; do
    case " $caps " in *" $c "*) ;; *) missing+="$c " ;; esac
  done
  if [ "$mode" = write ]; then case " $caps " in *" writeFiles "*) ;; *) missing+="writeFiles " ;; esac; fi
  if [ "$mode" = read ]; then case " $caps " in *" readFiles "*) ;; *) missing+="readFiles " ;; esac; fi
  [ -z "$missing" ] || { echo ConfigInvalid; return 0; }
  echo ok
}

# Ask B2 what the key in S3_KEY_ID / S3_SECRET may do, and judge it.   check_key BUCKET write|read
# Prints a class. The answer holds an authorization token: it stays in a private temp file and is deleted.
check_key() {
  local out code cls
  out=$(mktemp)
  code=$(printf 'user = "%s:%s"\n' "$S3_KEY_ID" "$S3_SECRET" |
    curl -sS -K - -o "$out" -w '%{http_code}' -m 30 "${B2_AUTHORIZE_URL:-https://api.backblazeb2.com/b2api/v2/b2_authorize_account}" 2> /dev/null || true)
  cls=$(classify_http "$code")
  if [ "$cls" = ok ]; then judge_capabilities "$out" "$1" "$2"; else echo "$cls"; fi
  rm -f "$out"
}

# Is a local backup young enough to upload? Only backups younger than the lock period go to B2: an older one would
# be locked for less time than the rule promises, and after lifecycle deleted its first copy it would be uploaded
# again, every day, forever.   upload_wanted NAME NOW_EPOCH DAYS
upload_wanted() {
  local e
  e=$(name_epoch "$1") || return 1
  [ $(($2 - e)) -lt $(($3 * 86400)) ]
}

# Tell the monitor now. POSTs the class to the ping URL's /fail. Prints nothing (the URL is a credential); returns 0
# only if the monitor accepted it.   signal_monitor_fail URL CLASS
signal_monitor_fail() {
  curl -fsS -m 10 --retry 2 -o /dev/null -d "${2:-Alert}" "$(fail_ping_url "$1")" 2> /dev/null
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
# Retry objects (<db>_<stamp>.r<N>.tar.age) were made by the SFTP copy, removed 2026-11-25; none are made now, and
# the rule below is kept so an old local volume still prunes right. They sort
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
# TASK_OUTPUT_BYTES and TASK_OUTPUT_WATERMARK (epoch seconds) in the environment, when set, go to task_runs'
# output_bytes (migration 0015) and output_watermark: what the run measured, on success or failure.
task_finish() {
  local id=$1 status=$2 cls=${3:-} msg=${4:-} bytes=${TASK_OUTPUT_BYTES:-} wm=${TASK_OUTPUT_WATERMARK:-}
  [[ $id =~ ^[0-9]+$ ]] || return 1
  [[ $bytes =~ ^[0-9]*$ ]] || bytes=""
  [[ $wm =~ ^[0-9]*$ ]] || wm=""
  psqlc -v id="$id" -v st="$status" -v cls="$cls" -v msg="${msg:0:500}" -v bytes="$bytes" -v wm="$wm" <<'SQL'
UPDATE task_runs
   SET status = :'st', completed_at = now(), heartbeat_at = now(),
       error_class = NULLIF(:'cls', ''), error_message = NULLIF(:'msg', ''),
       output_bytes = NULLIF(:'bytes', '')::bigint,
       output_watermark = to_timestamp(NULLIF(:'wm', '')::double precision)
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
