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

echo "-- retry objects (made by the SFTP copy until 2026-11-25; an old local volume must still prune right)"
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

echo "-- the S3 region of a B2 endpoint"
check "s3.us-west-004.backblazeb2.com" us-west-004 "$(B2_REGION= s3_region https://s3.us-west-004.backblazeb2.com)"
check "a trailing slash" eu-central-003 "$(B2_REGION= s3_region https://s3.eu-central-003.backblazeb2.com/)"
fails "another host is refused without B2_REGION" env B2_REGION= bash -c '. /opt/backup/lib.sh; s3_region https://example.com'
check "B2_REGION overrides (the stand-in)" us-east-1 "$(B2_REGION=us-east-1 s3_region http://standin:7070)"

echo "-- what an HTTP answer was"
check "200" ok "$(classify_http 200)"; check "204" ok "$(classify_http 204)"
check "nothing answered" Unreachable "$(classify_http 000)"
for c in 429 500 502 503; do check "$c is transient" Unreachable "$(classify_http $c)"; done
for c in 401 403; do check "$c is the key" AuthRefused "$(classify_http $c)"; done
check "404 is configuration" ConfigInvalid "$(classify_http 404)"
check "anything else" S3Failed "$(classify_http 409)"

echo "-- what an rclone failure was (text from rclone v1.76.0 against the stand-in)"
rcl() { local f; f=$(mktemp); printf '%s\n' "$2" > "$f"; classify_rclone "$1" "$f"; rm -f "$f"; }
check "exit 0" ok "$(rcl 0 '')"
check "a wrong secret" AuthRefused "$(rcl 1 'ERROR : Attempt 1/1 failed with 1 errors and: operation error S3: PutObject, https response error StatusCode: 403, RequestID: X, HostID: Y, api error SignatureDoesNotMatch: The request signature we calculated does not match')"
check "a HEAD refused (no readFiles)" AuthRefused "$(rcl 1 'operation error S3: HeadObject, https response error StatusCode: 403, RequestID: X, api error Forbidden: Forbidden')"
check "no such host" Unreachable "$(rcl 1 'Failed to copyto: dial tcp: lookup s3.us-west-004.backblazeb2.com: no such host')"
check "a 503" Unreachable "$(rcl 1 'https response error StatusCode: 503, RequestID: X')"
check "our timeout" Unreachable "$(rcl 124 '')"
check "wording nobody has seen: the long cap, never a confident class" UploadFailed "$(rcl 1 'something an unborn rclone said')"

echo "-- reading a version listing (ListObjectVersions, the stand-in's shape)"
x=$(mktemp)
cat > "$x" << 'XML'
<?xml version="1.0" encoding="UTF-8"?>
<ListVersionsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>true</IsTruncated><Name>fdbk</Name><NextKeyMarker>flowdesk/b.tar.age</NextKeyMarker><NextVersionIdMarker>v9</NextVersionIdMarker><Version><ETag>&#34;x&#34;</ETag><IsLatest>true</IsLatest><Key>flowdesk/a.tar.age</Key><Size>600</Size><VersionId>v2</VersionId></Version><Version><IsLatest>false</IsLatest><Key>flowdesk/a.tar.age</Key><Size>500</Size><VersionId>v1</VersionId></Version><DeleteMarker><IsLatest>true</IsLatest><Key>flowdesk/b.tar.age</Key><VersionId>v9</VersionId></DeleteMarker><Version><IsLatest>false</IsLatest><Key>flowdesk/b.tar.age</Key><Size>70</Size><VersionId>v8</VersionId></Version><DeleteMarker><Key>probe/p.probe</Key><VersionId>v7</VersionId><IsLatest>true</IsLatest></DeleteMarker></ListVersionsResult>
XML
p=$(parse_versions "$x")
check "every version, in order" "V flowdesk/a.tar.age 600 v2 true
V flowdesk/a.tar.age 500 v1 false
V flowdesk/b.tar.age 70 v8 false" "$(printf '%s\n' "$p" | grep '^V ')"
check "delete markers" "D flowdesk/b.tar.age v9 true
D probe/p.probe v7 true" "$(printf '%s\n' "$p" | grep '^D ')"
check "the paging line" "T true flowdesk/b.tar.age v9" "$(printf '%s\n' "$p" | grep '^T ')"
check "bytes count EVERY version, hidden ones included" 1170 "$(printf '%s\n' "$p" | bucket_bytes)"
check "a delete marker under the backup prefix is found" flowdesk/b.tar.age "$(printf '%s\n' "$p" | delete_markers flowdesk/)"
check "one under probe/ is not tampering with backups" "" "$(printf '%s\n' "$p" | delete_markers flowdesk/ | grep probe || true)"
printf '<ListVersionsResult><IsTruncated>false</IsTruncated></ListVersionsResult>' > "$x"
check "an empty bucket: no entries, not truncated" "T false - -" "$(parse_versions "$x")"
check "an empty bucket holds 0 bytes" 0 "$(parse_versions "$x" | bucket_bytes)"
rm -f "$x"

echo "-- the budget (10 GB; warn 80%, over 95%)"
B=10000000000
check "0 bytes" ok "$(budget_state 0 $B)"
check "just under 80%" ok "$(budget_state 7999999999 $B)"
check "80%" warn "$(budget_state 8000000000 $B)"
check "just under 95%" warn "$(budget_state 9499999999 $B)"
check "95%" over "$(budget_state 9500000000 $B)"
check "past 100%" over "$(budget_state 12000000000 $B)"

echo "-- an object's retention"
r=$(mktemp)
now=$(date -u -d '2026-11-25 07:00:00' +%s)
printf '<Retention><Mode>GOVERNANCE</Mode><RetainUntilDate>2026-12-25T07:18:05.186Z</RetainUntilDate></Retention>' > "$r"
holds "GOVERNANCE for 30 days" retention_ok "$r" "$now" 30
fails "GOVERNANCE but asked for 40 days" retention_ok "$r" "$now" 40
printf '<Retention><Mode>COMPLIANCE</Mode><RetainUntilDate>2026-12-25T07:18:05Z</RetainUntilDate></Retention>' > "$r"
fails "COMPLIANCE is not what was decided (Kyle: governance)" retention_ok "$r" "$now" 30
printf '<Retention><Mode>GOVERNANCE</Mode><RetainUntilDate>2026-11-26T07:00:00Z</RetainUntilDate></Retention>' > "$r"
fails "GOVERNANCE for one day only" retention_ok "$r" "$now" 30
printf '<Error><Code>NoSuchObjectLockConfiguration</Code></Error>' > "$r"
fails "no retention at all" retention_ok "$r" "$now" 30
rm -f "$r"

echo "-- the key's capabilities (b2_authorize_account v2 shape; ASSUMED until the real-bucket probe shows it)"
j=$(mktemp)
cap() { printf '{"accountId":"a","allowed":{"bucketId":"b","bucketName":%s,"capabilities":[%s],"namePrefix":null},"authorizationToken":"t"}' "$1" "$2" > "$j"; judge_capabilities "$j" fdbk "$3"; }
W='"listBuckets","listAllBucketNames","listFiles","writeFiles","readFileRetentions"'
R='"listBuckets","listAllBucketNames","listFiles","readFiles","readFileRetentions"'
check "the write key as specified" ok "$(cap '"fdbk"' "$W" write)"
check "the read key as specified" ok "$(cap '"fdbk"' "$R" read)"
check "without the two S3-compatibility capabilities, still allowed" ok "$(cap '"fdbk"' '"listFiles","writeFiles","readFileRetentions"' write)"
for extra in deleteFiles bypassGovernance writeFileRetentions writeBucketRetentions writeBuckets writeKeys shareFiles readFiles writeFileLegalHolds; do
  check "write key plus $extra: too broad" CapabilityTooBroad "$(cap '"fdbk"' "$W,\"$extra\"" write)"
done
check "read key plus writeFiles: too broad" CapabilityTooBroad "$(cap '"fdbk"' "$R,\"writeFiles\"" read)"
check "not restricted to a bucket: too broad" CapabilityTooBroad "$(cap null "$W" write)"
check "restricted to another bucket" ConfigInvalid "$(cap '"elsewhere"' "$W" write)"
check "missing writeFiles" ConfigInvalid "$(cap '"fdbk"' '"listFiles","readFileRetentions"' write)"
check "missing readFileRetentions (the lock could not be checked)" ConfigInvalid "$(cap '"fdbk"' '"listFiles","writeFiles"' write)"
printf '{"apiInfo":{"storageApi":{"allowed":[]}}}' > "$j"
check "another shape (v3): unknown, fails closed" CapabilityUnknown "$(judge_capabilities "$j" fdbk write)"
printf 'not json' > "$j"
check "not JSON: unknown" CapabilityUnknown "$(judge_capabilities "$j" fdbk write)"
rm -f "$j"

echo "-- which backups go up (only those younger than the lock period)"
now=$(date -u -d '2026-11-25 12:00:00' +%s)
holds "today's" upload_wanted flowdesk_20261125T080000Z.tar.age "$now" 30
holds "29 days old" upload_wanted flowdesk_20261027T120001Z.tar.age "$now" 30
fails "30 days old: it would be locked for less than the promise" upload_wanted flowdesk_20261026T120000Z.tar.age "$now" 30
fails "a weekly from two months ago (else re-uploaded daily after lifecycle deleted it)" upload_wanted flowdesk_20260920T030000Z.tar.age "$now" 30
fails "not a backup name" upload_wanted notes.tar.age "$now" 30

echo "-- the monitor's failure endpoint"
check "plain" https://hc-ping.com/abc/fail "$(fail_ping_url https://hc-ping.com/abc)"
check "trailing slash" https://hc-ping.com/abc/fail "$(fail_ping_url https://hc-ping.com/abc/)"
check "before a query string" 'https://hc.example/ping/abc/fail?rid=1' "$(fail_ping_url 'https://hc.example/ping/abc?rid=1')"

echo "-- how a failure is retried: exit codes and the two caps"
check "Unreachable is transient" 75 "$(exit_code_for_class Unreachable)"
check "Unavailable is transient" 75 "$(exit_code_for_class Unavailable)"
for c in AuthRefused CapabilityTooBroad NotLocked Tampered OverBudget; do check "$c pages at once (76)" 76 "$(exit_code_for_class $c)"; done
for c in NoRecipient ConfigInvalid BadDatabaseName; do check "$c is configuration" 78 "$(exit_code_for_class $c)"; done
for c in DumpFailed DumpUnreadable EncryptFailed ReadFailed NoTimescale UploadFailed S3Failed SizeMismatch NothingToCopy CapabilityUnknown something-new; do
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
