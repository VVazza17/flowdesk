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

**Status: built, smoke-tested against stand-ins, not closed.** The slice closes when a real unattended dump
lands on the real desktop and restores from there ([Closing the slice](#closing-the-slice)).

What has been run, and what has not:

| | Where | State |
|---|---|---|
| Scheduling, encryption, the copy, restore with the private key, fail-closed, catch-up, health states | `infra/smoke-m2-slice2.sh`, against throwaway Linux sshd containers that speak SFTP only | **Run** |
| What each NTFS permission variant allows (commands below) | The exact commands, on a throwaway folder on a Windows machine, with a stand-in account | **Run** |
| `prune.sh` under Git Bash on Windows | A Windows 10 machine, 45 files | **Run** |
| Windows OpenSSH Server accepting the `sshd_config` below, the key file location, the `/C:/...` path form, how `sftp-server.exe` behaves under these permissions | The real desktop | **Not run.** Written from Microsoft's documentation. Treat the first real attempt as the test, and `verify-destination.sh` as the instrument |

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
| `ops.backup_copy` | Over SFTP: list the destination, `put` every `*.tar.age` it lacks, then list again and compare sizes | none |

The archive is the `.dump` and `.manifest` pair that `backup.sh` writes, so `restore.sh` takes them
unchanged. The plaintext exists only in a private directory on tmpfs for the length of one backup.

**Fails closed.** With no real age key in `infra/backup/recipients.txt`, the backup records `NoRecipient` and
writes nothing. It never falls back to an unencrypted copy. The same goes for the copy: with no
`BACKUP_SSH_PATH` it refuses, because a default would put the files somewhere the permissions were not set on.

**Retention** keeps the newest backup of each of the last 14 days, plus the newest of each of the last 8
seven-day buckets (`BACKUP_KEEP_DAILY`, `BACKUP_KEEP_WEEKLY`). **It applies to the local `backups` volume
only.** See [Retention on the destination](#retention-on-the-destination-not-enforced-yet).

**`docker compose down -v` wipes the local backups along with the database.** The off-box copies are what
survive that.

### Two keys, kept apart

| | age key | SSH key |
|---|---|---|
| For | Encrypting the dump | The transport to the desktop |
| Public half | `infra/backup/recipients.txt`, **committed** | On the desktop, in a key file only administrators can edit |
| Private half | **Never on the laptop.** Made on the desktop, kept in the password manager | On the laptop (the container needs it), path in `.env`, never in git |
| What it protects | Anyone who gets the files cannot read them | SFTP into one folder; what it may do there is set by NTFS on the desktop |

The rule: the key and the backups never share a single point of failure. The age private key must exist
somewhere other than the machine that holds the backups.

Make the age key **on the desktop**:

```powershell
age-keygen -o flowdesk-age.key        # prints "Public key: age1..."
```

(`age` for Windows: `winget install FiloSottile.age`, or use the copy inside the backup image through
`infra/restore-backup.sh`, which needs only Docker.) Put the whole file in the password manager. Paste the
`age1...` line into `infra/backup/recipients.txt` (replacing the commented example) and commit it. Rotating
later means adding the new public key as a second line: old backups stay readable with the old key.

Make the SSH key **on the laptop, in Git Bash**, with no passphrase (the container cannot type one). Git Bash
and not PowerShell, because empty arguments are passed differently by different PowerShell versions:

```bash
ssh-keygen -t ed25519 -N "" -C flowdesk-backup -f ~/.ssh/flowdesk_backup
```

### Configuration (`.env`, all gitignored)

```
BACKUP_SSH_HOST=<desktop hostname or IP>
BACKUP_SSH_USER=flowdesk-backup
BACKUP_SSH_PORT=22
BACKUP_SSH_PATH=/C:/FlowdeskBackups
BACKUP_SSH_KEY_FILE=C:/Users/Kyle/.ssh/flowdesk_backup
BACKUP_SSH_KNOWN_HOSTS_FILE=C:/Users/Kyle/.ssh/flowdesk_backup_known_hosts
BACKUP_PING_URL=<the external monitor's ping URL for the backup check>
```

`BACKUP_SSH_PATH` is the folder on the desktop, in the form the native Windows OpenSSH server uses for a drive:
`/C:/FlowdeskBackups` (the leading slash and the colon are part of it; WinSCP's FAQ documents this form). It has
no default on purpose. `verify-destination.sh` fails with a clear message if the path does not resolve.

With `BACKUP_SSH_HOST` blank the copy step records `skipped` / `NotConfigured`, which `/health/data` shows as
`not_configured`. It is visible and never counts as a success.

Host-key checking is strict (`StrictHostKeyChecking=yes` against your `known_hosts` file only), so the file
must exist. Make it from the laptop, then **compare the fingerprint with the desktop's own** before trusting
it, so you are not pinning whoever answered first:

```bash
# On the laptop, in Git Bash (PowerShell 5.1's > writes UTF-16, which ssh cannot read):
ssh-keyscan -t ed25519 <HOST> > ~/.ssh/flowdesk_backup_known_hosts     # use the same HOST string as in .env
ssh-keygen -lf ~/.ssh/flowdesk_backup_known_hosts
```

```powershell
# On the desktop: the fingerprint must match the one above.
ssh-keygen -lf C:\ProgramData\ssh\ssh_host_ed25519_key.pub
```

### The destination: Windows OpenSSH Server and NTFS

**Why this shape.** The desktop is Windows, which has no rsync, so the copy is SFTP. OpenSSH Server is a native
Windows service with start type Automatic, so it comes back by itself after a reboot. (Running sshd inside WSL2
was rejected: it needs a Task Scheduler entry to start after a reboot, and a destination that silently stops
accepting backups after a reboot is the failure you find out about when you need the backup.)

**Two layers, and which does what.**
- `sshd_config` makes the account **SFTP-only**: no shell, no tunnels.
- **NTFS permissions** decide what the account can do inside the backup folder. The restriction on deleting is
  not enforced by the backup code or by sshd; it is the ACL, which is why `verify-destination.sh` exists.

**Not used: `ChrootDirectory`.** Windows supports it for SFTP sessions only, and there are reports of Windows
`Match User` + `ChrootDirectory` setups behaving unpredictably, including the service refusing to start. A
restriction that can stop sshd is the wrong place to put one. The consequence is in [Limits](#limits).

**Not possible as asked: "home is the backup folder".** A Windows local account's SFTP start directory is its
profile, and I found nothing in Microsoft's documentation for changing that. So the copy names the folder
explicitly (`BACKUP_SSH_PATH`), and the permissions are on that folder.

Run these in an **elevated PowerShell on the desktop**. Do them in order and stop at the first error.

**1. OpenSSH Server running and automatic.**

```powershell
Get-WindowsCapability -Online -Name OpenSSH.Server*                  # State should be Installed
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0        # only if it says NotPresent
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
Get-Service sshd                                                      # Status Running, StartType Automatic
Get-NetFirewallRule -Name *ssh* | Format-Table Name, Enabled, Profile, Direction, Action
```

The firewall rule must be enabled for the network profile your LAN uses (usually Private).

**2. A dedicated, non-administrator account.** Its password is random, never used and not stored: sign-in is by
key only.

```powershell
$pw = ConvertTo-SecureString ([guid]::NewGuid().ToString('N') + 'Aa1!') -AsPlainText -Force
New-LocalUser -Name 'flowdesk-backup' -Password $pw -PasswordNeverExpires -UserMayNotChangePassword -AccountNeverExpires -Description 'FlowDesk backup receiver (SFTP only)'
Add-LocalGroupMember -Group 'Users' -Member 'flowdesk-backup'
```

It must **not** be in Administrators (administrators' keys are read from a different file, and it would defeat the
point).

**3. The folder and its permissions.** Create it, remove inherited permissions, and give the administrators and
SYSTEM control (so an administrator can prune and clean up). Then choose **one** of the two variants.

```powershell
New-Item -ItemType Directory C:\FlowdeskBackups
icacls C:\FlowdeskBackups /inheritance:r
icacls C:\FlowdeskBackups /grant "SYSTEM:(OI)(CI)F" "BUILTIN\Administrators:(OI)(CI)F"
```

**Variant W, write-once. The default.** The account can add a new backup and read what is there. It cannot
overwrite, truncate, append to, rename or delete anything, and cannot make subfolders. This is the default
because of a tested finding about Variant D, below, and not as a matter of taste.

```powershell
icacls C:\FlowdeskBackups /grant "flowdesk-backup:(RX,WD)"
icacls C:\FlowdeskBackups /grant "flowdesk-backup:(OI)(IO)(RX)"
icacls C:\FlowdeskBackups /deny  "flowdesk-backup:(OI)(IO)(WD,AD,WEA,WA,DE)"
icacls C:\FlowdeskBackups /deny  "flowdesk-backup:(DC)"
```

**Variant D, no-delete. The original specification, and not append-only. Do not use it as the default.** The
account can create and write files and read; Delete and Delete-subfolders-and-files are denied.

```powershell
icacls C:\FlowdeskBackups /grant "flowdesk-backup:(OI)(CI)(RX,WD,WEA,WA)"
icacls C:\FlowdeskBackups /deny  "flowdesk-backup:(OI)(CI)(DE,DC)"
```

Write `DE`, not `D`. In a deny entry icacls's `D` shorthand also sets Synchronize, which blocks almost every file
open (listing and reading stopped working when I first tried it). `DE` is exactly Delete.

What each variant allowed, measured by running the commands above on a throwaway folder on Windows with a
stand-in account (not elevated, so permissions applied):

| Operation | Variant W | Variant D |
|---|---|---|
| List; read a backup | allowed | allowed |
| Create a new file and fill it in one open (what an upload does) | allowed | allowed |
| Delete; rename (so no temp-name-then-rename) | denied | denied |
| Create a subfolder | denied | denied |
| Append; truncating overwrite, asking for generic write (what most runtimes ask) | denied | denied |
| **Open an existing file asking only for the data-write right, write in place or truncate** | **denied** | **ALLOWED** |

**The finding: denying Delete does not make a destination append-only.** Under Variant D, an open on an existing
backup that asked only for the data-write right wrote into it in place, and a truncating overwrite with the same
right **cut a 24-byte backup to 1 byte**. Delete and rename were refused, exactly as specified. Writing into an
existing file is a different permission from deleting it, and D grants it. Under Variant W every overwrite,
truncate, append, delete and rename was refused, whatever access the open asked for.

Opens that ask for generic write (which also includes append) are denied under D, so whether a stolen key could
do this through Windows' `sftp-server.exe` depends on the exact access it requests, which I could not observe
from here. That uncertainty is the reason not to rely on D: under D a stolen key *may* be able to destroy every
backup without deleting one, and under W it cannot.

Reproduce it yourself, before trusting either variant, in an ordinary (not elevated) PowerShell on the desktop.
It runs the exact commands above on a throwaway folder, with your own account standing in for the backup account,
and removes the folder afterwards:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File infra\backup\acl-experiment.ps1
```

It prints the table above and ends `RESULT: as documented` or says what differs.

**The price of W:** an upload that is interrupted halfway (the laptop sleeps mid-copy) leaves a short file under
the real name, and the account cannot overwrite it to repair it. The copy notices by size, fails loudly with
`PartialOnDestination`, and names the file. You delete that one file on the desktop (as an administrator), and
the next run uploads it whole. That is a visible, manual step; the alternative is a key that can corrupt your
backups. Under D the copy repairs it by itself, if the server's open allows it.

**4. The account's key.** Keep it in a file only administrators can edit, so the account cannot change its own
key. Put the single line from `flowdesk_backup.pub` into it:

```powershell
$line = '<paste the one line from flowdesk_backup.pub>'
Set-Content -Path C:\ProgramData\ssh\flowdesk-backup.authorized_keys -Value $line -Encoding ascii
icacls C:\ProgramData\ssh\flowdesk-backup.authorized_keys /inheritance:r /grant "SYSTEM:F" /grant "BUILTIN\Administrators:F"
```

If sshd refuses the key from that location, check its log (`Get-WinEvent -LogName 'OpenSSH/Operational' -MaxEvents 20`)
and fall back to the standard place, `C:\Users\flowdesk-backup\.ssh\authorized_keys`, which exists only after the
account's first sign-in. Do not create `C:\Users\flowdesk-backup` by hand: Windows may then give the real profile
a different folder name.

**5. SFTP-only, in `C:\ProgramData\ssh\sshd_config`.** Add this block at the **end** of the file (a `Match` block
applies to everything after it). Leave the existing `Subsystem sftp ...` line alone: the SFTP client needs it, and
without it the connection opens and closes straight away. User names in `Match` must be lower case.

```
Match User flowdesk-backup
    AuthorizedKeysFile C:/ProgramData/ssh/flowdesk-backup.authorized_keys
    ForceCommand internal-sftp
    PasswordAuthentication no
    AllowTcpForwarding no
    PermitTTY no
```

Only use keywords the Windows build supports (`KbdInteractiveAuthentication`, `X11Forwarding`, `StrictModes`
and several others are not available there, per Microsoft's documentation).

**6. Test the configuration before restarting.** A bad `sshd_config` can stop the service, and a stopped service
is exactly the silent failure to avoid.

```powershell
& "$env:WINDIR\System32\OpenSSH\sshd.exe" -t        # prints nothing if the file is valid
Restart-Service sshd
Get-Service sshd                                     # Running
```

**7. Check it from the laptop, with the real tool.** Add the settings to `.env`, then:

```powershell
docker compose up -d --build backup
docker compose exec -u postgres backup /opt/backup/verify-destination.sh
```

It connects with the real key, creates a probe file, then tries to overwrite, delete, rename and make a folder,
and says which policy is in force. **It must end with `RESULT: OK`; delete and rename must say `refused`.**
`policy write-once` is Variant W working; `policy no-delete` is Variant D, with the overwrite warning. It leaves
one file, `flowdesk-probe-<stamp>.tar.age`, which only an administrator can remove (that is the point).

### Restoring from a scheduled backup

On a machine with Docker, this repo, a `.env`, and the age **private** key (in practice the desktop):

```bash
bash infra/restore-backup.sh /path/to/flowdesk_20261109T080000Z.tar.age --identity flowdesk-age.key --db flowdesk
```

It decrypts (with `age` if installed, otherwise the copy inside the backup image, so only Docker is needed),
checks the archive holds exactly one dump and its manifest, then runs `restore.sh` unchanged. Everything in
[If you are here because something failed](#if-you-are-here-because-something-failed) applies after that, including
the version trap: the machine restoring needs the same Postgres image tag as the manifest's `image=`. Use Git
Bash on Windows, as for the rest of these scripts.

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

1. `verify-destination.sh` against the real desktop ends `RESULT: OK` (and `policy write-once`, the default).
2. `docker compose logs backup` shows `backup_scheduler_started` with `"offbox":"configured"`.
3. **Unattended:** you do not run anything by hand. A backup appears after a scheduled slot has passed (or
   after a wake from sleep that crossed one), and its `.tar.age` arrives on the desktop.
4. `/health/data` shows `backups.backup` and `backups.copy` both `ok`.
5. On the desktop, `restore-backup.sh` restores that file into a database named something other than the live
   one, with the private key from the password manager, and prints `OK: restored into ...`.
6. The monitor shows a ping from step 3.

To skip waiting for the slot while setting up, run the two steps by hand as the `postgres` user (the files
must belong to the user the scheduler runs as):

```bash
docker compose exec -u postgres backup /opt/backup/run-backup.sh
docker compose exec -u postgres backup /opt/backup/run-copy.sh
```

That tests the configuration. It does not satisfy step 3.

### What to verify on the desktop first

1. **Is the OpenSSH Server feature installed and the `sshd` service Running and Automatic?** (Step 1.) It is
   available on Windows 10 and 11 including Home.
2. **Is TCP 22 reachable from the laptop** on the right firewall profile? From the laptop, `ssh -p 22
   flowdesk-backup@<host>` should say `This service allows sftp connections only.` or at least ask for nothing
   and close, not time out.
3. **Will the desktop keep the same address?** Use a DHCP reservation on the router or a stable hostname. An
   address that changes breaks the pinned `known_hosts` and looks like a dead desktop.
4. **Is it awake when the copy runs?** It need not be: a failed copy is retried. But a desktop that is mostly
   asleep means the monitor will alert on most mornings.
5. **Disk space** on the backup drive, and see the retention section below.
6. **The host-key fingerprints match** (see above).

### Retention on the destination: not enforced yet

The 14-daily plus 8-weekly rule runs inside the container, on the **local** volume. It cannot run on the
destination: the account is deliberately unable to delete there. So today **nothing prunes the desktop, and the
14+8 rule is aspirational for the copies that matter most** until something runs it.

What would run it: a job on the desktop, as an administrator or SYSTEM (who have Delete), weekly, applying the
same rule with `infra/backup/prune.sh`. That script was run under Git Bash on Windows (GNU awk), 45 files in,
18 left, and it leaves anything not named like `<db>_<UTC stamp>.tar.age` alone, including the probe files. The
desktop needs Git for Windows and a clone of this repo. A Task Scheduler entry would look like the following. It
has **not** been run on the desktop, and the quoting is the part most likely to need a fix:

```powershell
schtasks /Create /TN "FlowDesk backup prune" /SC WEEKLY /D SUN /ST 04:00 /RU SYSTEM /RL HIGHEST `
  /TR "\"C:\Program Files\Git\bin\bash.exe\" -c \"/c/path/to/Flowdesk/infra/backup/prune.sh /c/FlowdeskBackups\""
```

Try it with `--dry-run` first. If Git for Windows is not on the desktop, the alternative is a PowerShell port
of the rule, which should be checked against `retention_delete` on the same file names so the two cannot
drift. Neither exists yet. Until one runs, the destination grows without bound; a dump is 37 KB today, and that
will not stay true.

### Limits

- **The backup image is not covered by CI's vulnerability scan.** The `security` job builds `backend/` only.
  The backup image's Alpine packages (`age`, `openssh-client`, `curl`, `tzdata`, `bash`) float at build time,
  on top of an exact Postgres tag whose own packages nobody scans either. Slice 0 pinned the Python base and
  closed that floating tag; this is a second image with the same problem, and it is open. Closing it means
  scanning it in the same job, and probably pinning the Alpine packages or the base by digest.
- **arm64 is untested for the backup image.** `infra/ci-checks.sh arm64` builds `backend/` only. The image is
  built FROM the multi-arch Timescale image and Alpine publishes arm64 packages for everything it installs, but
  neither has been built or run under emulation.
- **Retention on the destination is not enforced** (above).
- **Nothing here has run against Windows OpenSSH Server**, and the NTFS result was measured with Win32 calls
  and a stand-in account, not through `sftp-server.exe`. `verify-destination.sh` is how that gets checked.
- **The SFTP session is not chrooted.** If the SSH key leaked, the account could list and read whatever the
  `Users` group can read on the desktop (not other users' profiles), though it could not write outside the
  backup folder. The backups are encrypted. Chroot is the usual fix and is not used, for the reason above.
- **The SSH private key lives on the laptop**, because the container needs it. That is why what it can do on the
  desktop is limited by NTFS, and why Variant W is the default.
- **A write-restricted account cannot confirm a copy's integrity beyond its size.** Restore drills are the check.
- **`BACKUP_AT` and daylight saving:** plain arithmetic on the local day, so on the two days a year the clocks
  change the slot can be an hour off. Once a day, after the slot, is what is guaranteed.
- **The destination's long listing is parsed from the right** (size and name are the last five columns), and the
  name is whatever follows the last `/` or `\`. Real Windows output has not been seen.
