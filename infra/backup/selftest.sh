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

echo "-- what an ssh failure was (text captured from OpenSSH 10.2, the version in the image)"
cls() { # RC TEXT -> the class
  local f; f=$(mktemp); printf '%s\n' "$2" > "$f"; classify_ssh_failure "$1" "$f"; rm -f "$f"
}
check "a refused key" AuthRefused "$(cls 255 'fd@sink: Permission denied (publickey,keyboard-interactive).
Connection closed')"
check "a refused key on Windows OpenSSH's method list" AuthRefused "$(cls 255 'flowdesk-backup@desktop: Permission denied (publickey,password,keyboard-interactive).')"
changed='@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @
@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
IT IS POSSIBLE THAT SOMEONE IS DOING SOMETHING NASTY!
Offending ED25519 key in /run/backup/known_hosts:1
Host key for sink has changed and you have requested strict checking.
Host key verification failed.
Connection closed'
check "a changed host key" HostKeyChanged "$(cls 255 "$changed")"
check "only the last line of that banner (what a 300-byte tail keeps)" HostKeyChanged "$(cls 255 'Host key verification failed.
Connection closed')"
check "no key pinned for this name is setup state, not an impersonation" ConfigInvalid "$(cls 255 'No ED25519 host key is known for sink and you have requested strict checking.
Host key verification failed.
Connection closed')"
kh=$(mktemp); ssh-keygen -q -t ed25519 -N "" -f "$kh.k"; echo "sink $(cut -d' ' -f1,2 "$kh.k.pub")" > "$kh"; echo "[sink]:2222 $(cut -d' ' -f1,2 "$kh.k.pub")" >> "$kh"
nokey='No RSA host key is known for sink and you have requested strict checking.
Host key verification failed.
Connection closed'
f=$(mktemp); printf '%s
' "$nokey" > "$f"
check "that same line, but a key IS pinned for the host (other key type offered): a mismatch" HostKeyChanged "$(classify_ssh_failure 255 "$f" "$kh" sink 22)"
check "a key is pinned for sink, none for elsewhere: setup state" ConfigInvalid "$(classify_ssh_failure 255 "$f" "$kh" elsewhere 22)"
check "a non-default port is looked up as [host]:port" HostKeyChanged "$(classify_ssh_failure 255 "$f" "$kh" sink 2222)"
check "the right host but a port nothing is pinned for: setup state" ConfigInvalid "$(classify_ssh_failure 255 "$f" "$kh" sink 2200)"
rm -f "$f" "$kh" "$kh.k" "$kh.k.pub"
check "a host that does not resolve" Unreachable "$(cls 255 'ssh: Could not resolve hostname nosuchhost.invalid: Name does not resolve
Connection closed')"
check "connection refused" Unreachable "$(cls 255 'ssh: connect to host sink port 2222: Connection refused
Connection closed')"
check "no route" Unreachable "$(cls 255 'ssh: connect to host 192.168.1.9 port 22: No route to host')"
check "a connect timeout" Unreachable "$(cls 255 'ssh: connect to host 10.255.255.1 port 22: Operation timed out
Connection closed')"
check "our own timeout killing a hung sftp (exit 124)" Unreachable "$(cls 124 '')"
check "a dropped session" Unreachable "$(cls 255 'Connection reset by 192.168.1.9 port 22
Connection closed')"
check "wording nobody has seen: falls back to Unreachable, never a confident class" Unreachable "$(cls 255 'ssh: something an unborn OpenSSH said')"
check "no text at all (what -q leaves)" Unreachable "$(cls 255 'Connection closed')"
check "a phrase quoted mid-line is not a class" Unreachable "$(cls 255 'note: the log said Host key verification failed. and then Permission denied (x)')"
check "an SFTP-level refusal (exit 1) is not an ssh failure" SftpFailed "$(cls 1 'remote readdir: Permission denied')"

echo "-- the destination must be a name, not a bare IP"
for h in 192.168.1.20 10.0.0.5 127.0.0.1 3232235797 ::1 fe80::1 '[fe80::1]' 2001:db8::1; do holds "$h is a bare IP" host_is_bare_ip "$h"; done
for h in desktop desktop.home.arpa DESKTOP-4F2K.local sink nosuchhost.invalid 1password.example host-192-168-1-20; do fails "$h is a name" host_is_bare_ip "$h"; done
fails "empty is not judged here (the empty check is separate)" host_is_bare_ip ''

echo "-- the monitor's failure endpoint"
check "plain" https://hc-ping.com/abc/fail "$(fail_ping_url https://hc-ping.com/abc)"
check "trailing slash" https://hc-ping.com/abc/fail "$(fail_ping_url https://hc-ping.com/abc/)"
check "before a query string" 'https://hc.example/ping/abc/fail?rid=1' "$(fail_ping_url 'https://hc.example/ping/abc?rid=1')"

echo "-- how a failure is retried: exit codes and the two caps"
check "Unreachable is transient" 75 "$(exit_code_for_class Unreachable)"
check "HostKeyChanged has its own code, so the scheduler can signal once" 76 "$(exit_code_for_class HostKeyChanged)"
for c in NoRecipient ConfigInvalid BadDatabaseName; do check "$c is configuration" 78 "$(exit_code_for_class $c)"; done
for c in DumpFailed DumpUnreadable EncryptFailed ReadFailed NoTimescale AuthRefused SftpFailed SizeMismatch NothingToCopy something-new; do
  check "$c takes the long cap" 1 "$(exit_code_for_class $c)"
done
# retry_plan RC NOW DELAY BASE TRANSIENT_CAP LONG_CAP NEXT_SLOT -> "NEXT_TRY NEW_DELAY"
d=900; seq=""; for _ in 1 2 3 4 5; do p=$(retry_plan 75 1000 "$d" 900 1800 21600 99999); d=${p#* }; seq="$seq$d "; done
check "transient backs off 15, 30, 30, 30 minutes (cap 30)" "1800 1800 1800 1800 1800 " "$seq"
d=900; seq=""; for _ in 1 2 3 4 5 6 7; do p=$(retry_plan 1 1000 "$d" 900 1800 21600 99999); d=${p#* }; seq="$seq$d "; done
check "everything else backs off to 6 hours" "1800 3600 7200 14400 21600 21600 21600 " "$seq"
check "the first retry waits the base delay" "1900 1800" "$(retry_plan 75 1000 900 900 1800 21600 99999)"
check "a configuration failure waits for the next slot, not a backoff" "99999 900" "$(retry_plan 78 1000 14400 900 1800 21600 99999)"
check "a transient failure after long waits is brought down to the short cap" "2800 1800" "$(retry_plan 75 1000 21600 900 1800 21600 99999)"

exit $bad
