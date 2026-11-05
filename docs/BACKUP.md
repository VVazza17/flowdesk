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

**Nothing runs this automatically yet.** That and off-box copies are IDEA-029, which must be resolved before
M2 writes its first persistent observation. A dump on the same disk protects against a bad `DROP`, not
against losing the machine.

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
