# FlowDesk — Backup and restore

Implemented in M1 slice 9. Verified by `infra/smoke-slice9.sh`. Use Git Bash on Windows, not PowerShell:
PowerShell 5.1's redirection corrupts binary files.

## If you are here because something failed

The dump is a whole-database copy. Your `.env` is **not** in it, so keep the same `POSTGRES_USER`.

**1. Check which image the dump needs.** Open the `.manifest` next to the `.dump` and read `image=`.
If it differs from the `image:` on the postgres service in `docker-compose.yml`, read
[The version trap](#the-version-trap) before going further.

**2. Get an empty database.** Stop anything that writes to the database first.

```bash
docker compose stop api                 # or api-public
```

If the data is gone, or you are replacing it, this wipes the volume. **It is irreversible, so first
confirm the dump you are about to rely on exists and has a manifest.**

```bash
docker compose down -v
docker compose up -d postgres           # postgres ONLY. See the warning below.
```

**Not plain `docker compose up -d`.** That also runs `migrate`, which creates tables, and the restore
then refuses with `database 'flowdesk' is not empty`. If that happened, run the two commands above again.

If you want to keep the current data while you check the backup, skip the wipe and restore under a
new name instead: `--db flowdesk_check` creates that database.

**3. Restore.**

```bash
bash infra/restore.sh dumps/<name>.dump --db flowdesk
```

Expected: `OK: restored into 'flowdesk' ...`, then a line per hypertable with its chunk count.

**4. Start everything and check.**

```bash
docker compose up -d
curl -s http://127.0.0.1:8000/health/ready    # "status":"ok", migrations "ok"
```

`migrate` running here is correct. It does nothing if the dump is at head, and applies newer migrations
if the dump is older than the code.

Sequence last run literally on 2026-11-04, on a separate compose project, against a database with no
hypertables yet (M1). Hypertable restores are covered by the smoke test, in a disposable container.

## The version trap

**`restore.sh` refuses a dump if the target's TimescaleDB version differs from the dump's. So an old dump
needs the old image tag to restore. The manifest's `image=` line records which one.**

This means bumping the image tag in `docker-compose.yml` does not update your existing dumps. Every dump
taken before the bump can only be restored on the old tag. The refusal looks like this, and names the image:

```
FAIL: version mismatch: this dump is TimescaleDB 2.30.0, the target is 2.31.0. Restore it on image
timescale/timescaledb:2.30.0-pg16. Nothing was changed.
```

To restore such a dump and end up on the current version:

```bash
# 1. In docker-compose.yml, set the postgres image to the manifest's image= value, temporarily.
docker compose down -v                  # irreversible; only if you are replacing the data
docker compose up -d postgres
bash infra/restore.sh dumps/<old>.dump --db flowdesk

# 2. Put the current tag back in docker-compose.yml. Same volume, new image:
docker compose up -d postgres
docker compose exec postgres psql -X -U flowdesk -d flowdesk -c "ALTER EXTENSION timescaledb UPDATE"

# 3. Start everything.
docker compose up -d
```

`-X` is required: `ALTER EXTENSION` must be the first thing the session does. Run it once per database
in the volume that you use.

Tested on 2026-11-04 from 2.30.0 to 2.31.0, one minor version, with a hypertable, an aggregate and a
retention policy: rows, aggregate, chunks and job all unchanged, and a new-range insert made a new chunk.
Larger jumps are untested and may have their own upgrade notes.

**The old tag has to still exist.** Docker Hub is outside our control. Before relying on a long-lived dump,
keep a copy of its image: `docker save <image> -o <file>.tar`.

The rule is stricter than Timescale's own docs, which ask for the same major version. `# DECISION:` our image
is pinned to an exact tag, so an exact match costs little, and a restore that went wrong across versions
could look fine, as the plain restore below did.

## Taking a backup

```bash
bash infra/backup.sh                    # POSTGRES_DB into dumps/ (gitignored)
bash infra/backup.sh --db NAME --out DIR
```

It writes `<db>_<UTC timestamp>.dump` and `.manifest`, and prints the dump's path. A failed run leaves neither.

**Dumps are unencrypted full copies of the data.** Treat them accordingly.

One warning from `pg_dump` is expected on every run and is filtered out, because TimescaleDB's own catalog has
this cycle. Anything else `pg_dump` prints is shown. Only its exit code decides success.

```
pg_dump: warning: there are circular foreign-key constraints on this table:
pg_dump: detail: continuous_agg
```

`backup.sh` is the manual tool. The scheduled, encrypted one with an off-box copy is the `backup` service,
described in [Scheduled backups](#scheduled-backups-m2-slice-2-idea-029) below. A dump on the same disk
protects against a bad `DROP`, not against losing the machine.

## What restore refuses

Each of these changes nothing and exits non-zero. All are exercised by the smoke test.

| Message | Meaning | What to do |
|---|---|---|
| `no manifest` | No `.manifest` beside the dump | Find it. The checksum and versions can't be checked without it |
| `checksum mismatch` | The file is truncated or corrupt | Use another dump |
| `version mismatch` | See [The version trap](#the-version-trap) | Use the image the message names |
| `is not empty` | The target has user tables | Pick a new `--db`, or wipe it deliberately |

And one failure that does change something:

| Message | Meaning | What to do |
|---|---|---|
| `database '...' is PARTIAL` | `pg_restore` stopped partway | Do not use that database: it is incomplete, with background jobs stopped on purpose. `DROP DATABASE` it and restore again. If it repeats, the dump is bad |

## Why not a plain pg_dump and pg_restore

`restore.sh` wraps `pg_restore` in `timescaledb_pre_restore()` and `timescaledb_post_restore()`, as
[Timescale documents](https://www.tigerdata.com/docs/self-hosted/latest/backup-and-restore/logical-backup).
Do not restore by hand without them. Measured on 2.31.0 (2026-11-04, details in `docs/BUILD-LOG.md`), a plain
`pg_restore` of 480 rows in 10 chunks:

- lost 48 rows, one whole chunk
- lost the hypertable's primary key, so a duplicate key inserted into a new chunk was **accepted**
- still listed 10 chunks, both jobs and an unchanged continuous aggregate, and the app would have started
- reported this only through exit code 1 and two error lines, after carrying on to the end

Also never use `pg_restore -j` ("does not correctly restore the TimescaleDB catalogs"), or dump single tables
or schemas (the dump loses the hypertable/chunk relationship). Both are from the same Timescale page.

## `macro_metrics` is append-only, and restores are unaffected (M2 slice 5)

`macro_metrics` has triggers that refuse `UPDATE`, `DELETE` and `TRUNCATE` (migration 0005,
`docs/RUNBOOK-VINTAGE-REPAIR.md`). A backup and a restore are not affected: `pg_restore` loads rows with `COPY`
into an empty database, which fires none of them, and `pg_dump` emits the triggers after the data, so the
guard comes back with the table. Nothing in `infra/` deletes from it. **Take a backup before any repair** (the
runbook says so); the dump is how a bad repair is undone.

## What this does not cover

- **Redis:** not backed up, on purpose. Postgres owns all durable state.
- **`.env`:** keep it somewhere safe separately. Passwords are not in the dump, and neither are roles. A fresh
  container creates `POSTGRES_USER` from `.env`.
- **arm64:** never exercised. Dumps have only been taken and restored on amd64.
- **Compressed chunks:** the docs say they back up fine, and the spec doesn't use them, so the fixture doesn't
  cover them. Whoever enables compression needs to extend `smoke-slice9.sh` first.
- **Scale and live writes:** the fixture has 10 chunks and an idle source. Restore time on real volumes is
  unknown, and it cannot be sped up with `-j`.

## Scheduled backups (M2 slice 2, IDEA-029)

**Status: built, tested against a stand-in, not closed.** The off-box copy goes to **Backblaze B2** (Kyle,
2026-11-25; `docs/BUILD-LOG.md`, addenda 2026-11-25 second and third). The slice closes when a backup the scheduler
took and uploaded unattended restores on the laptop with matching fingerprints ([the drill](#the-restore-drill-step-by-step)).
The SFTP copy to a Windows desktop that this section used to describe was removed the same day: no such destination
exists (the stack runs on the desktop). Git history keeps it.

| | Where | State |
|---|---|---|
| Scheduling, encryption, upload, lock check, key check, tamper and budget checks, restore with the read key, fail-closed, catch-up, alerts, health | `infra/smoke-m2-slice2.sh` against versitygw (an S3 stand-in with Object Lock), in CI (`backup-offbox`) | **Run** |
| Backblaze itself: capabilities enforced on S3 calls, default retention, lifecycle, caps, the v2 `b2_authorize_account` shape, path-style requests | `infra/backup/verify-destination.sh` against the real bucket | **Not run yet** (Kyle's setup steps first) |

### What runs, and where

A compose service, `backup`, built from `infra/backup/` on top of the same Postgres image the stack runs (so its
`pg_dump` is the server's own version). It is a container, not a host job, so that what it does is written to
`task_runs` and visible on `/health/data`.

Every 30 seconds it asks two questions:

1. **Is a backup due?** One is due when the newest one is older than the most recent `BACKUP_AT` slot (default 03:00
   `America/New_York`). That one rule is also the catch-up: a machine that slept through 03:00 is due the moment it
   wakes, once, and not again until the next slot.
2. **Is an off-box copy pending?** Yes after every new backup and after every start, and again on a retry timer after
   a failure.

| Step | What | Result |
|---|---|---|
| `ops.backup` | `pg_dump -Fc`, `pg_restore --list` check, manifest, `tar` of both, `age` to the recipients | `/backups/<db>_<UTC>.tar.age`; its size in `task_runs.output_bytes` |
| `ops.backup_copy` | Check the key; list the bucket; upload each local backup younger than 30 days that the bucket lacks; check each upload's size and lock; check for delete markers and the budget | `flowdesk/<db>_<UTC>.tar.age` in the bucket; the bucket's bytes in `output_bytes` |

The plaintext exists only in a private directory on tmpfs for the length of one backup. **B2 only ever holds
ciphertext.**

**Fails closed.** With no real age key in `infra/backup/recipients.txt`, the backup records `NoRecipient` and writes
nothing. It never falls back to an unencrypted copy. With no `B2_BUCKET` the copy records `skipped` / `NotConfigured`,
shown as `not_configured`, never a success.

**Retention, local:** the newest backup of each of the last 14 days, plus the newest of each of the last 8 seven-day
buckets (`BACKUP_KEEP_DAILY`, `BACKUP_KEEP_WEEKLY`). **Off-box:** see [Retention on B2](#retention-on-b2).
`docker compose down -v` wipes the local backups along with the database; the B2 copies survive that.

### What protects the copies: on B2's side, not in the script

The machine's key is the realistic thing to lose. So the protection is server-side, where a stolen key cannot reach
it (Kyle, 2026-11-25):

- **Object Lock, GOVERNANCE mode, a default retention of 30 days** on the bucket. Every upload is locked without the
  uploader asking. A locked version cannot be deleted by any key without `bypassGovernance`, and the machine's key never
  has it. Governance, not compliance, because a wrong setting stays recoverable with the master key, which lives only in
  the password manager.
- **The write key** (on this machine) is restricted to the bucket and holds exactly `listBuckets, listAllBucketNames,
  listFiles, writeFiles, readFileRetentions`:
  - no `deleteFiles`, `bypassGovernance`, `writeFileRetentions`, `writeBucketRetentions` or `writeBuckets`;
  - no `readFiles` either, so a stolen key cannot even download the ciphertext.
- **The read key** (on the laptop, for restores only): `listBuckets, listAllBucketNames, listFiles, readFiles,
  readFileRetentions`.

The keys are made with the b2 command-line tool and an explicit list. The web UI's "Write Only" is not used, because
Backblaze does not document which capabilities it grants.

**What a stolen write key can still do, and how each is caught:**

| Attack | Effect | Caught by |
|---|---|---|
| Delete a version | Refused: no `deleteFiles`, and the lock | `verify-destination.sh` steps 4 and 5 |
| Overwrite | B2 cannot refuse a same-name upload; it keeps the old version | Step 6. The copy never re-uses a name |
| Hide (a delete marker, allowed by `writeFiles`) | The locked version stays for its 30 days, then lifecycle removes it. A stolen key can shorten history to 30 days, never less | Every copy run lists versions: a delete marker under `flowdesk/` is `Tampered`, paged at once |
| Upload junk to run up a bill | Storage grows | The budget check (`OverBudget`) and B2's caps |

**Checked every run, not once.** The copy asks `b2_authorize_account` what the key may do and refuses
(`CapabilityTooBroad`, paged) if it holds anything else or is not restricted to the bucket. After every upload it reads
the object's retention and refuses (`NotLocked`, paged) unless it is GOVERNANCE for the full period.

### The two clients

- **rclone** (MIT) uploads: one static binary, pinned by version and SHA256 in the Dockerfile
  ([RUNBOOK-IMAGE-CVE.md](RUNBOOK-IMAGE-CVE.md#rclone-the-b2-uploader-the-bump)). It uses its S3 backend against B2's
  S3-compatible endpoint, so a stand-in S3 can test it in CI.
  - It is told not to HEAD the object before or after (`--no-check-dest --s3-no-head`), because the write key
    deliberately has no `readFiles`.
  - Its own `--immutable` is not used: that check needs a HEAD. The copy's "never upload a name twice" comes from the
    version listing instead.
- **curl** (`--aws-sigv4`) reads what rclone does not expose: an object's retention, and the version listing with its
  delete markers.
- **Secrets.** Neither client ever sees a secret on its command line: rclone takes it from `RCLONE_CONFIG_*` in its own
  environment, curl from a config on stdin. **Nothing a provider returns is logged or stored**, because B2's (and
  S3's) error bodies echo the key ID, itself a secret here. Only class names reach `task_runs`. The smoke greps
  `task_runs` and `/health/data` for the keys and key IDs.

### Configuration (`.env`, all gitignored)

On the stack machine:

```
B2_BUCKET=<the bucket name>
B2_ENDPOINT=https://s3.<region>.backblazeb2.com
B2_KEY_ID=<the WRITE key's ID>
B2_APPLICATION_KEY=<the WRITE key>
BACKUP_PING_URL=<the backup check's ping URL>
HEARTBEAT_URL=<the heartbeat check's ping URL>          # this PC is the deployed instance (Kyle, 2026-11-25)
```

On the laptop, for the drill only: `B2_BUCKET`, `B2_ENDPOINT`, `B2_READ_KEY_ID`, `B2_READ_APPLICATION_KEY`. **The read
key is never on the stack machine; the write key is never on the laptop.** Both key IDs and both keys are secrets:
`.env` and the password manager only, never chat, logs or stored errors (the `HEARTBEAT_URL` rule).

### The age key, kept apart

Kyle, 2026-11-25: **generated on the laptop**. The private key is stored in the password manager, and is on the laptop's
disk only during a restore. Kyle commits the public key to `infra/backup/recipients.txt`. **The stack machine never holds
the private key**, so the backups and the key that opens them never share a machine.

### Retention on B2

- Each backup is uploaded once, under a unique name, and locked GOVERNANCE for **30 days** (R, Kyle: one month of
  protection, DERIVED).
- **Lifecycle rules:** `daysFromUploadingToHiding: 31` and `daysFromHidingToDeleting: 1`, on `flowdesk/` and on
  `probe/`. So a backup is hidden on day 31 and deleted on day 32, after its lock has expired.
  - Backblaze documents that a lifecycle rule never deletes a locked version, so a wrong rule cannot cut history short.
  - Pruning is server-side only: the machine's key cannot delete.
- **Only backups younger than 30 days are uploaded.** An older local weekly would be locked for less than the promise.
  After lifecycle deleted its first copy it would also be re-uploaded every day.
- Storage is about 32 × the dump size: **55 MB** at today's 1.72 MB dump (MEASURED 2026-11-25).

### The budget (the no-cost rule)

FlowDesk takes no mandatory recurring cost (`ZERO_COST_MODE`).
- **What is free.** B2's first 10 GB of storage are "always free". Sign-up needs no credit card. Transaction
  classes A, B and C are free. Egress is free up to 3× the stored data.
- **What bounds it.** The bound is **copies kept × dump size**, not one year's growth (BUILD-LOG, addendum 2026-11-25
  second). The growth rate is not measured: "1 GB a year" was a 500-symbol projection, ASSUMED.
- **What the copy does.** It measures every version in the bucket, hidden ones included (whether B2 bills hidden
  versions is not documented, so they are ASSUMED billed), on every run:
  - at **80%** of 10 GB it warns on `/health/data`;
  - at **95%** it refuses to upload and pages (`OverBudget`).
- **What fixes it.** Not a bill: fewer copies (weekly uploads) or a shorter lock.
- **B2's own caps** (Caps & Alerts) are set to $0 for storage and for Class A, B and C. Backblaze's docs do not say
  whether $0 is accepted, or what happens when a cap is reached (ASSUMED: requests are refused). Kyle records what the
  UI accepted.

### Restoring from a scheduled backup

On the laptop, with the read key in its `.env` and the age private key from the password manager on its disk for the
duration:

```bash
bash infra/restore-backup.sh --from-b2 --identity <age key file> --db <new-or-empty-db>             # the newest
bash infra/restore-backup.sh --from-b2 <db>_<stamp>.tar.age --identity <age key file> --db <...>   # a named one
```

It downloads through the backup image (`fetch-b2.sh`), version-aware: a hidden backup is still found and says so.
Everything after decryption is `infra/restore.sh` unchanged. Then delete the key file.

### Telling you when it stops (the missed-run signal, and the alerts)

A stale backup does **not** degrade `/health/ready`; it shows on `/health/data` (`backups`; states and fields in
`docs/HEALTH.md`). The page comes from outside the machine:

- After each **verified** copy (key checked, upload sized and locked, no tampering, under budget) the container pings
  `BACKUP_PING_URL`. Make a check at the same provider as the heartbeat, separate from the heartbeat's, with **period
  1 day and grace 24 hours**. If the pings stop for any reason, the provider alerts.
- **The alert classes page at once:** `AuthRefused`, `CapabilityTooBroad`, `NotLocked`, `Tampered` and `OverBudget`.
  The scheduler POSTs the class to `BACKUP_PING_URL/fail`, once per occurrence (until the next successful copy).
  Check that the provider accepts `/fail` (healthchecks.io does).

### How a failure is retried

| Failure | Exit | Retried | Pages at once |
|---|---|---|---|
| `Unreachable` (B2 or the network away, 429, 5xx), or `task_runs` not writable | 75 | up to **30 minutes** | no |
| `AuthRefused`, `CapabilityTooBroad`, `NotLocked`, `Tampered`, `OverBudget` | 76 | up to **6 hours** (the fix is made in Backblaze's console, which this machine cannot see) | **yes**, once |
| `NoRecipient`, `ConfigInvalid`, `BadDatabaseName` | 78 | **not before the next slot** | no |
| Everything else (`DumpFailed`, `UploadFailed`, `S3Failed`, `SizeMismatch`, ...) | 1 | up to **6 hours** | no |

"Not before the next slot" is held in the scheduler's memory only, so `docker compose -p flowdesk restart backup`
retries at once. That is also how a changed `.env` or `recipients.txt` is picked up. A plan change, recorded: the plan
put `AuthRefused`, `CapabilityTooBroad` and `NotLocked` under configuration (78). As built they page and retry slowly
(76), so a key fixed in the console is picked up within hours without a restart.

### Closing the slice

1. `verify-destination.sh` against the real bucket ends `RESULT: OK (write key)`, and with the read key on the laptop
   `RESULT: OK (read key)`.
2. One backup taken and uploaded by the scheduler, unattended (Part B).
3. That backup restores on the laptop and the fingerprints match (Part C).

### The restore drill, step by step

For Kyle, once the build is committed. **Run each step once, in order; one command per step, no `&&`.** Each step
names its shell, what success looks like, and what to send back. **Never send a key, a key ID, or a ping URL.**
Steps marked **IRREVERSIBLE** cannot be undone.

#### Part A: setup

**A1. Browser: the Backblaze account.** Sign up at `https://www.backblaze.com/sign-up/cloud-storage` (Backblaze's page:
"No credit card required"). Choose the region (it cannot be changed for the bucket later). Success: the B2 web
console shows "Buckets".

**A2. Browser, then PowerShell: the b2 command-line tool.**
1. Download `b2-windows.exe` (v5.0.3, MIT) and `b2-windows.exe_hashes.txt` from
   `https://github.com/Backblaze/B2_Command_Line_Tool/releases` into `C:\Tools\b2\`.
2. PowerShell: `Get-FileHash C:\Tools\b2\b2-windows.exe -Algorithm SHA256`.

Success: the hash equals the SHA256 line in the hashes file. Send back: matched, yes or no.

**A3. Browser: the master application key.** Application Keys → generate the master application key. It is shown
once: put the key ID and key in the password manager. It is used only in A4 to A9, never stored on this machine
afterwards.

**A4. Git Bash: sign the tool in.** Run `cd /c/Tools/b2`, then `./b2-windows.exe account authorize`. It prompts for the
key ID and the key: paste the master key's there, never on the command line. Success: account details print, with no
error.

**A5. Git Bash: the bucket. IRREVERSIBLE: Object Lock cannot be turned off, and the name cannot be changed.**
`./b2-windows.exe bucket create --file-lock-enabled flowdesk-backups-<something-random> allPrivate`
Bucket names are global; add something random. Success: a bucket ID prints.

**A6. Git Bash: the default retention, GOVERNANCE, 30 days.** Changeable later with the master key, so not
irreversible.
`./b2-windows.exe bucket update --default-retention-mode governance --default-retention-period "30 days" flowdesk-backups-<...>`
Success: the bucket's JSON prints, showing default retention mode governance and 30 days.

**A7. Git Bash: the lifecycle rules.** Both go in one command, because "all bucket lifecycle rules are set at once":

```
./b2-windows.exe bucket update --lifecycle-rule '{"fileNamePrefix": "flowdesk/", "daysFromUploadingToHiding": 31, "daysFromHidingToDeleting": 1}' --lifecycle-rule '{"fileNamePrefix": "probe/", "daysFromUploadingToHiding": 31, "daysFromHidingToDeleting": 1}' flowdesk-backups-<...>
```

Success: the JSON lists both rules.

**A8. Git Bash: check the bucket.** `./b2-windows.exe bucket get flowdesk-backups-<...>`
Success: file lock enabled, default retention governance 30 days, the two lifecycle rules. Send back: that section of
the output (it holds no secret).

**A9. Git Bash: the two keys.** A key cannot be edited, only deleted and made again.
1. The write key:
   `./b2-windows.exe key create --bucket flowdesk-backups-<...> flowdesk-backup-write listBuckets,listAllBucketNames,listFiles,writeFiles,readFileRetentions`
   It prints the key ID and the key **once**. Put them in the password manager, and in `.env` **on this PC** as
   `B2_KEY_ID=` and `B2_APPLICATION_KEY=`.
2. The read key:
   `./b2-windows.exe key create --bucket flowdesk-backups-<...> flowdesk-backup-read listBuckets,listAllBucketNames,listFiles,readFiles,readFileRetentions`
   Put it in the password manager **only**. It goes into the laptop's `.env` at Part C.
3. Check both: `./b2-windows.exe key list -l`. Success: both keys, each restricted to the bucket, with exactly those
   capabilities. Send back: that output **with every key ID deleted**.

**A10. Git Bash: sign the tool out.** `./b2-windows.exe account clear` (ASSUMED command name, from the tool's
`account` group; not run by Claude). If it says the command does not exist, stop and send that line back.

**A11. Browser: the caps.** Caps & Alerts. For Storage, Class A, Class B and Class C, clear "No Cap" and enter **0**.
If the UI refuses 0, enter the smallest amount it accepts and note it. Add your email for the alerts. Send back: what
each cap accepted.

**A12. Browser: the dead-man monitor.** If the healthchecks.io account from the 2026-11-05 heartbeat drill still exists,
use it; otherwise create a free one. Make **two** checks, each alerting to email:
- `flowdesk-heartbeat`: simple schedule, period 5 minutes, grace 40 minutes (`docs/HEARTBEAT.md`);
- `flowdesk-backup`: period 1 day, grace 24 hours.

Copy each ping URL straight into `.env` on this PC: `HEARTBEAT_URL=` and `BACKUP_PING_URL=`. **Warning (MEASURED
2026-11-21 to 11-24 from this PC's event log):** this PC sleeps or is off most nights, for 10 to 13 hours. With the
heartbeat check armed, every such night pages after about 45 minutes. Keep the PC awake, or decide differently, before
arming it.

**A13. Laptop: the age key.**
1. PowerShell: `winget install FiloSottile.age`, then `age-keygen -o flowdesk-age.key`. It prints `Public key: age1...`.
2. Put the whole file in the password manager, then delete it from the laptop's disk.
3. On this PC, replace the commented example in `infra/backup/recipients.txt` with that `age1...` line, then commit and
   push it.

Success: `git log -1` shows the commit. Send back: the public key line (it is not a secret).

**A14. Git Bash, this PC: start it.** `docker compose -p flowdesk up -d`, then
`docker compose -p flowdesk restart backup` (it reads the new `.env` and `recipients.txt`). Success: `/health/ready` is
`ok`.

**A15. Git Bash, this PC: the probe.** `docker compose -p flowdesk exec -u postgres backup /opt/backup/verify-destination.sh`
Success: the last line is `RESULT: OK (write key)`. Send back: the whole output. It holds capability names and its own
words, no secret.

#### Part B: one unattended backup (run nothing by hand)

Leave the stack running. At 03:00 ET, or on waking if the PC slept through it, the scheduler backs up and uploads.
Then, in Git Bash:

1. `docker compose -p flowdesk exec -T postgres psql -U flowdesk -d flowdesk -Atq -c "select task_name, status, error_class, started_at from task_runs where task_name like 'ops.backup%' order by id desc limit 4"`
   Success: an `ops.backup` and an `ops.backup_copy` row, both `succeeded`, started at or after 03:00 ET by the
   scheduler.
2. `curl -s http://127.0.0.1:8000/health/data`. Success: `backups.copy.state` is `ok`, `budget_state` is `ok`,
   `tampered` is `false`, and `copied_through` is the backup's time.
3. The `flowdesk-backup` check on healthchecks.io shows a ping.

Send back: step 1's rows and step 2's `backups` object.

#### Part C: the restore, on the laptop

1. Clone the repo, `cp .env.example .env`, and set `B2_BUCKET`, `B2_ENDPOINT`, `B2_READ_KEY_ID` and
   `B2_READ_APPLICATION_KEY` (the read key). Keep `POSTGRES_USER=flowdesk`, the source's user: roles are not in
   the dump.
2. Git Bash: `docker compose up -d postgres`.
3. Git Bash, the read-key probe. Hand the key to the container by name, so it never appears on a command line, one
   command per line:
   - `set -a`
   - `. ./.env`
   - `set +a`
   - `export B2_KEY_ID="$B2_READ_KEY_ID" B2_APPLICATION_KEY="$B2_READ_APPLICATION_KEY"`
   - `docker compose run --rm --no-deps -T -e B2_KEY_ID -e B2_APPLICATION_KEY backup /opt/backup/verify-destination.sh --read`

   Success: `RESULT: OK (read key)`.
4. Copy the age key from the password manager to a file, e.g. `~/flowdesk-age.key`.
5. Git Bash: `bash infra/restore-backup.sh --from-b2 --identity ~/flowdesk-age.key --db flowdesk_drill`
   Success: `fetched <name>` and `OK: restored into 'flowdesk_drill'`.
6. The fingerprints, with the same cutoff on both sides: on this PC, run `infra/drill-fingerprint.sql` against
   `flowdesk`; on the laptop, against `flowdesk_drill`. Use the cutoff = the backup's `created_utc` from its manifest:
   `docker compose exec -T postgres psql -U flowdesk -d <db> -Atq -v cutoff=<stamp> -f - < infra/drill-fingerprint.sql`.
   Success: the two outputs are identical.
7. Delete `~/flowdesk-age.key`.

Send back: step 3's last line, step 5's last two lines, and both fingerprint outputs. **IDEA-029 closes when they
match.**

### Limits

- **The backup image's scan is report-only** (RUNBOOK-IMAGE-CVE.md). Its Timescale base and Alpine's `age` carry fixable
  HIGH findings, none of them in rclone. The Alpine packages float.
- **arm64:** the image builds and its tools run under QEMU (MEASURED 2026-11-25). The backup and restore have not run
  on arm64.
- **Backblaze's behaviour is ASSUMED until `verify-destination.sh` runs against the real bucket:**
  - capabilities enforced on S3 calls;
  - path-style requests;
  - the v2 `b2_authorize_account` shape;
  - default retention applied to S3 uploads;
  - what a cap does.

  The stand-in proves FlowDesk's logic, never B2's semantics.
- **The shell scripts' keyed lookups** (an object by name) are covered by the smoke's cases and the self-test, not by a
  mutation harness: `tools/mutate.py` mutates `backend/` only.
