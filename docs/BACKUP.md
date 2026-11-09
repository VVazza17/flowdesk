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

**Status: built and smoke-tested, not closed.** The slice closes when a real unattended dump lands on the
real desktop and restores from there ([Closing the slice](#closing-the-slice)). Until then the mechanics are
proven only against a stand-in sshd (`infra/smoke-m2-slice2.sh`).

### What runs, and where

A compose service, `backup`, built from `infra/backup/` on top of the same Postgres image the stack runs (so
its `pg_dump` is the server's own version). It is a container, not a host job, so that what it does is written
to `task_runs` and visible on `/health/data`. A host scheduler would be a second system nothing else sees.

Every 30 seconds it asks two questions:

1. **Is a backup due?** One is due when the newest one is older than the most recent `BACKUP_AT` slot
   (default 03:00 `America/New_York`). That one rule is also the catch-up: a laptop that slept through 03:00
   is due the moment it wakes, once, and not again until the next slot.
2. **Is an off-box copy pending?** Yes after every new backup and after every start, and again on a retry
   timer after a failure.

They are **two independent steps**, recorded as two tasks (`ops.backup`, `ops.backup_copy`). A backup
succeeds when it is dumped, checked, encrypted and kept in the `backups` volume. The copy is attempted
afterwards, so **a sleeping desktop is a failed copy and never a failed backup**, and the copy catches up by
itself when the desktop is back. Failures back off (15 min, doubling, capped at 6 h) so a missing key does not
write a failed row every few minutes.

| Step | What | Local result |
|---|---|---|
| `ops.backup` | `pg_dump -Fc`, `pg_restore --list` check, manifest, `tar` of both, `age` to the recipients | `/backups/<db>_<UTC>.tar.age` |
| `ops.backup_copy` | `rsync` of the `*.tar.age` files the destination does not have, over SSH | none |

The archive is the `.dump` and `.manifest` pair that `backup.sh` writes, so `restore.sh` takes them
unchanged. The plaintext exists only in a private directory on tmpfs for the length of one backup.

**Fails closed.** With no real age key in `infra/backup/recipients.txt`, the backup records `NoRecipient` and
writes nothing. It never falls back to an unencrypted copy.

**Retention:** the newest backup of each of the last 14 days, plus the newest of each of the last 8 seven-day
buckets (`BACKUP_KEEP_DAILY`, `BACKUP_KEEP_WEEKLY`). Revisit when the data volume is real. This prunes the
local `backups` volume only: see [The destination](#the-destination).

**`docker compose down -v` wipes the local backups along with the database.** The off-box copies are what
survive that.

### Two keys, kept apart

| | age key | SSH key |
|---|---|---|
| For | Encrypting the dump | The transport to the desktop |
| Public half | `infra/backup/recipients.txt`, **committed** | On the desktop, in `authorized_keys` |
| Private half | **Never on the laptop.** Made on the desktop, kept in the password manager | On the laptop (the container needs it), path in `.env`, never in git |
| What it protects | Anyone who gets the files cannot read them | Only rsync into one directory is possible with it |

The rule: the key and the backups never share a single point of failure. The age private key must exist
somewhere other than the machine that holds the backups.

Make the age key **on the desktop**:

```bash
age-keygen -o flowdesk-age.key        # prints "Public key: age1..."
```

Put the whole file in the password manager. Paste the `age1...` line into `infra/backup/recipients.txt`
(replacing the commented example) and commit it. Rotating later means adding the new public key as a second
line: old backups stay readable with the old key.

Make the SSH key **on the laptop**, with no passphrase (the container cannot type one):

```bash
ssh-keygen -t ed25519 -N "" -C flowdesk-backup -f ~/.ssh/flowdesk_backup
```

### Configuration (`.env`, all gitignored)

```
BACKUP_SSH_HOST=<desktop hostname or IP>
BACKUP_SSH_USER=<account on the desktop>
BACKUP_SSH_PORT=22
BACKUP_SSH_PATH=.
BACKUP_SSH_KEY_FILE=C:/Users/Kyle/.ssh/flowdesk_backup
BACKUP_SSH_KNOWN_HOSTS_FILE=C:/Users/Kyle/.ssh/flowdesk_backup_known_hosts
BACKUP_PING_URL=<the external monitor's ping URL for the backup check>
```

With `BACKUP_SSH_HOST` blank the copy step records `skipped` / `NotConfigured`, which `/health/data` shows as
`not_configured`. It is visible and never counts as a success.

Host-key checking is strict (`StrictHostKeyChecking=yes` against your `known_hosts` file only), so the file
must exist. Make it from the laptop, then **compare the fingerprint with the desktop's own** before trusting
it, so you are not pinning whoever answered first:

```bash
ssh-keyscan -p 22 -t ed25519 <HOST> > ~/.ssh/flowdesk_backup_known_hosts     # use the same HOST string as in .env
ssh-keygen -lf ~/.ssh/flowdesk_backup_known_hosts                             # on the laptop
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub                              # on the desktop: must match
```

### The destination

On the desktop, create a directory for the backups and put this **one line** in the receiving account's
`~/.ssh/authorized_keys` (the public key is the contents of `flowdesk_backup.pub`):

```
restrict,command="/usr/bin/rrsync -wo /home/<user>/flowdesk-backups" ssh-ed25519 AAAA... flowdesk-backup
```

Check where `rrsync` is with `command -v rrsync` and use that path. It is a Python script, so the desktop needs
`python3`. What the line does, and what `infra/smoke-m2-slice2.sh` proves with a line of exactly this form:

- `restrict` turns off a terminal, port and agent forwarding, and X11.
- `rrsync -wo DIR` makes the key **write-only into `DIR`**. A shell command is refused (`SSH_ORIGINAL_COMMAND
  does not run rsync`), reading is refused (`reading from write-only server is not allowed`), `..` is refused,
  and an absolute path is not honoured: it is anchored inside `DIR`.
- Because the directory is fixed by this line, `BACKUP_SSH_PATH` is relative to it. Leave it at `.`.

**The destination is never pruned by the laptop**, because this key cannot delete. Run the same retention
rule on the desktop yourself, from time to time or from its own scheduler:

```bash
bash infra/backup/prune.sh /home/<user>/flowdesk-backups --dry-run      # then without --dry-run
```

It only ever touches files named like `<db>_<UTC stamp>.tar.age`.

### Restoring from a scheduled backup

On a machine with Docker, this repo, a `.env`, and the age **private** key (in practice the desktop):

```bash
bash infra/restore-backup.sh /path/to/flowdesk_20261109T080000Z.tar.age --identity flowdesk-age.key --db flowdesk
```

It decrypts (with `age` if installed, otherwise the copy inside the backup image, so only Docker is needed),
checks the archive holds exactly one dump and its manifest, then runs `restore.sh` unchanged. Everything in
[If you are here because something failed](#if-you-are-here-because-something-failed) applies after that, including
the version trap: the machine restoring needs the same Postgres image tag as the manifest's `image=`.

### Telling you when it stops (the missed-run signal)

A stale backup does **not** degrade `/health/ready`. It can wait until morning, and anything that degrades
`ready` pages you (ADR-0008 section 4). It shows on `/health/data` instead (`backups.backup` and
`backups.copy`, each `ok` / `stale` / `never_succeeded` / `not_configured` / `no_runs`, stale after 36 hours,
`BACKUP_STALE_AFTER_HOURS`), and the page comes from outside the machine:

After each **successful off-box copy**, the container pings `BACKUP_PING_URL`. Make a **second** check at the
same provider as the heartbeat (`docs/HEARTBEAT.md`), separate from the heartbeat's, with **period 1 day and
grace 12 hours**. If the pings stop for any reason (laptop asleep, desktop down, key broken, container dead)
the provider alerts you. Like the heartbeat URL, it is a credential: `.env` only.

On a laptop that sleeps, expect the occasional alert on the morning after a night it was asleep; that is the
signal working, not a fault. A grace longer than 12 hours trades it for slower news of a real failure.

### Closing the slice

IDEA-029 closes when all of this is true, on the real hardware, and is recorded in `docs/BUILD-LOG.md`:

1. `docker compose up -d --build backup`, then `docker compose logs backup` shows `backup_scheduler_started`
   with `"offbox":"configured"`.
2. **Unattended:** you do not run anything by hand. A backup appears after a scheduled slot has passed (or
   after a wake from sleep that crossed one), and its `.tar.age` arrives on the desktop.
3. `/health/data` shows `backups.backup` and `backups.copy` both `ok`.
4. On the desktop, `restore-backup.sh` restores that file into a database named something other than the live
   one, with the private key from the password manager, and prints `OK: restored into ...`.
5. The monitor shows a ping from step 2.

To skip waiting for the slot while setting up, run the two steps by hand as the `postgres` user (the files
must belong to the user the scheduler runs as):

```bash
docker compose exec -u postgres backup /opt/backup/run-backup.sh
docker compose exec -u postgres backup /opt/backup/run-copy.sh
```

That tests the configuration. It does not satisfy step 2.

### What to verify on the desktop first

1. **Which OS?** `rsync` and `rrsync` must exist on the *receiving* side. Linux and macOS: fine (`command -v
   rsync rrsync`). The restricted line was tested against rsync 3.5; older versions ship a different `rrsync`
   (a Perl script) that was not tried. **Windows has neither by default**, and the
   restricted line above is for a system where `rrsync` runs. See "If the desktop is Windows" below.
2. **Is the SSH server running?** Linux: `systemctl status ssh` (or `sshd`). Windows: `Get-Service sshd` shows
   `Running` with start type `Automatic`; if the service is missing, the optional feature isn't installed
   (`Get-WindowsCapability -Online -Name OpenSSH.Server*`).
3. **Is the port open to the laptop?** The Windows firewall rule for TCP 22 must cover your network profile
   (Private, not only Domain). From the laptop: `ssh -p 22 <user>@<host>` should reach a prompt or a
   "permission denied", not time out.
4. **Will the desktop keep the same address?** Use a DHCP reservation on the router or a stable hostname. An
   address that changes breaks the pinned `known_hosts` and looks like a dead desktop.
5. **Is it awake when the copy runs?** It need not be: a failed copy is retried. But a desktop that is mostly
   asleep means the monitor will alert on most mornings.
6. **Does the account have a writable directory** for the backups, and enough disk? Dumps are small now.
7. **The host key fingerprints match** (see above).

#### If the desktop is Windows

The tested, restricted setup needs a Unix-style receiver. Two ways to get one, neither built or tested here:
run the receiving `sshd` and `rsync` inside WSL2 (the `authorized_keys` line then works as written, and the
Windows-side firewall and port forwarding need setting up), or use `sftp` as the transport instead of rsync
(Windows OpenSSH has `sftp-server` built in, and an `internal-sftp` jail restricts the account to one
directory). The second is a change to `run-copy.sh` only; say if you want it. Do not widen the `authorized_keys`
line to an unrestricted shell as a shortcut.

### Limits

- **The backup image is not covered by the CI vulnerability scan**, which builds `backend/` only. Its Alpine
  packages (`age`, `rsync`, `openssh-client`, `curl`) float at build time on top of an exact Postgres tag.
- **The SSH private key lives on the laptop**, because the container needs it. That is why the key is
  restricted to rsync-write into one directory: stolen, it can add files there and nothing else.
- **A write-only key cannot verify the copy afterwards.** `rsync` checks each file as it transfers; nothing
  lists the destination. Restore drills are the check.
- **`BACKUP_AT` and daylight saving:** plain arithmetic on the local day, so on the two days a year the clocks
  change the slot can be an hour off. Once a day, after the slot, is what is guaranteed.
- **Not on arm64 yet:** the image is built for the host's architecture and has only been run on amd64.
