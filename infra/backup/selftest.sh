#!/usr/bin/env bash
# Assertions over lib.sh's pure logic. Not part of the image: infra/smoke-m2-slice2.sh mounts it and runs it
# inside the image, where the busybox `date` and awk that production uses are the ones being tested.
set -uo pipefail
. /opt/backup/lib.sh
export TZ=America/New_York
bad=0
check() { # DESCRIPTION EXPECTED ACTUAL
  if [ "$2" = "$3" ]; then echo "ok:   $1"; else echo "FAIL: $1 (expected '$2', got '$3')"; bad=1; fi
}
holds() { if "${@:2}"; then echo "ok:   $1"; else echo "FAIL: $1"; bad=1; fi; }
fails() { if "${@:2}"; then echo "FAIL: $1"; bad=1; else echo "ok:   $1"; fi; }

echo "-- reading a backup's time from its name"
check "name_epoch" "$(date -u -d '2026-11-08 03:30:00' +%s)" "$(name_epoch flowdesk_20261108T033000Z.tar.age)"
fails "a name without a stamp is rejected" name_epoch notes.tar.age

echo "-- the schedule slot"
check "03:00 is 10800 s" 10800 "$(slot_seconds 03:00)"
check "23:59 is 86340 s" 86340 "$(slot_seconds 23:59)"
fails "24:00 is rejected" slot_seconds 24:00
fails "3:00 is rejected" slot_seconds 3:00
fails "03:60 is rejected" slot_seconds 03:60

echo "-- due, and catching up (slot 03:00)"
now=1000000
noon=43200
slot=10800
check "at 12:00 the latest slot was 9 h ago" -32400 "$(($(last_slot_epoch $now $noon $slot) - now))"
check "at 01:00 the latest slot was yesterday's, 22 h ago" -79200 "$(($(last_slot_epoch $now 3600 $slot) - now))"
holds "12:00, newest backup older than today's slot: due" backup_due $now $noon $slot $((now - 40000))
fails "12:00, backup taken after today's slot: not due" backup_due $now $noon $slot $((now - 30000))
fails "01:00, yesterday's slot already served: not due" backup_due $now 3600 $slot $((now - 70000))
holds "01:00, yesterday's slot missed (slept through it): due, once, on waking" backup_due $now 3600 $slot $((now - 90000))
holds "no backup ever: due" backup_due $now $noon $slot 0

echo "-- backoff after failures (base 900 s, cap 21600 s)"
d=900
seq=""
for _ in 1 2 3 4 5 6; do d=$(next_delay "$d" 900 21600); seq="$seq$d "; done
check "doubles, then holds at the cap" "1800 3600 7200 14400 21600 21600 " "$seq"

echo "-- retention (14 daily, 8 weekly)"
tmp=$(mktemp -d)
base=$(date -u -d '2026-11-08 12:00:00' +%s)
for i in $(seq 0 59); do
  touch "$tmp/flowdesk_$(date -u -d "@$((base - i * 86400))" +%Y%m%dT%H%M%SZ).tar.age"
done
touch "$tmp/flowdesk_20261108T020000Z.tar.age"
touch "$tmp/notes.txt" "$tmp/old.dump" "$tmp/flowdesk_20261108T030000Z.tar.age.partial"
doomed=$(ls "$tmp" | retention_delete 14 8)
check "a second backup on the newest day is not kept" 1 "$(printf '%s\n' "$doomed" | grep -c 20261108T020000Z)"
check "the newest backup is kept" 0 "$(printf '%s\n' "$doomed" | grep -c 20261108T120000Z)"
check "notes, dumps and partials are never named" 0 "$(printf '%s\n' "$doomed" | grep -cE 'notes.txt|old.dump|partial')"
prune_dir "$tmp" 14 8 > /dev/null
check "backups left after pruning" 19 "$(ls "$tmp" | grep -c 'tar.age$')"
check "files that are not backups are untouched" 3 "$(ls "$tmp" | grep -cE 'notes.txt|old.dump|partial')"
check "the newest 14 days are all there" 14 "$(ls "$tmp" | grep 'tar.age$' | grep -cE 'flowdesk_(20261108|20261107|20261106|20261105|20261104|20261103|20261102|20261101|20261031|20261030|20261029|20261028|20261027|20261026)T')"
prune_dir "$tmp" 14 8 > /dev/null
check "pruning twice changes nothing" 19 "$(ls "$tmp" | grep -c 'tar.age$')"
rm -rf "$tmp"

echo "-- retry objects (an interrupted upload is stepped over, then pruned)"
check "a retry name is read as the same backup" "$(name_epoch flowdesk_20261108T033000Z.tar.age)" "$(name_epoch flowdesk_20261108T033000Z.r2.tar.age)"
tmp=$(mktemp -d)
for n in flowdesk_20261108T030000Z.tar.age flowdesk_20261108T030000Z.r1.tar.age flowdesk_20261108T030000Z.r2.tar.age \
  flowdesk_20261107T030000Z.tar.age \
  flowdesk_20261106T030000Z.r9.tar.age flowdesk_20261106T030000Z.r10.tar.age \
  flowdesk-probe-20261108T030000Z.tar.age flowdesk_20261108T030000Z.r.tar.age flowdesk_20261108T030000Z.rx.tar.age; do
  touch "$tmp/$n"
done
doomed=$(ls "$tmp" | retention_delete 14 8)
check "of a backup's objects only the highest-numbered survives" "flowdesk_20261108T030000Z.r1.tar.age
flowdesk_20261108T030000Z.tar.age" "$(printf '%s\n' "$doomed" | grep '20261108T030000Z' | sort)"
check "r10 outranks r9 (numeric, not text, order)" "flowdesk_20261106T030000Z.r9.tar.age" "$(printf '%s\n' "$doomed" | grep '20261106')"
check "a backup with only its plain name is untouched" 0 "$(printf '%s\n' "$doomed" | grep -c '20261107')"
check "probe files and malformed names are never named" 0 "$(printf '%s\n' "$doomed" | grep -cE 'probe|\.r\.tar|\.rx\.')"
rm -rf "$tmp"

echo "-- the recipients file"
r=$(mktemp)
printf '# only a comment\n# age1abc\n' > "$r"
fails "comments only (the committed placeholder) is not a recipient" recipients_ok "$r"
printf 'age1%s\n' "qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq" > "$r"
holds "an age1 key line is a recipient" recipients_ok "$r"
printf 'not a key\n' > "$r"
fails "garbage is not a recipient" recipients_ok "$r"
rm -f "$r"

exit $bad
