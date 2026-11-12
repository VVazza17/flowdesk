# Runbook: repairing `macro_metrics` (the immutability triggers)

`macro_metrics` is append-only. Three triggers refuse any `UPDATE`, `DELETE` or `TRUNCATE` that would change
a stored vintage (migration 0005; `macro_metrics_no_update`, `macro_metrics_no_delete`,
`macro_metrics_no_truncate`). The reason (Kyle, 2026-11-11): an overwritten vintage is the one failure in this
table that is both catastrophic and undetectable. A replay would read the revised number as if it had been known
all along, nothing would be red, and every backtest after it would be quietly wrong. The primary key stops a
duplicate vintage; only the triggers stop an overwrite.

The one change an `UPDATE` may make is clearing `ingestion_run_id` to NULL. That is the foreign key's
`ON DELETE SET NULL` at work (a purged `ingestion_runs` row), and it cannot touch a value or a timestamp.

**This runbook is for a genuine repair, and only that.** Ask first whether it is a repair at all.

| What happened | What to do |
|---|---|
| FRED revised a value | Nothing here. The ingest appends a new row with `revision + 1`. That is the design. |
| A value is wrong because *we* wrote the wrong thing (a parser bug, the wrong series ingested under a metric) | A repair. Use this runbook. |
| You want to "clean up" or re-ingest history | Not a repair. Re-ingesting is idempotent and appends; it never needs this. |

## Before you start

1. **Write the BUILD-LOG entry first**, headed with today's date and "macro_metrics repair": why, which
   `(metric, effective_at, revision)` rows, what the change will be, and the expected row count. A repair that is
   not in the build log did not happen as far as the next reader is concerned. This is the trace; the steps
   below add a second one in the database server's log.
2. **Take a backup:** `bash infra/backup.sh` (restore procedure: `docs/BACKUP.md`).
3. **Capture the before state** of exactly the rows you will touch, and keep the file next to the backup:

   ```
   docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
     -c "\copy (SELECT * FROM macro_metrics WHERE metric = '"'"'XYZ'"'"') TO STDOUT WITH CSV HEADER"' > before.csv
   ```

## The procedure: one transaction, start to finish

Open `psql` (`docker compose exec postgres psql -U flowdesk -d flowdesk`) and run it as **one transaction**:

```sql
BEGIN;
SET LOCAL log_statement = 'all';                      -- the server log records every statement below
ALTER TABLE macro_metrics DISABLE TRIGGER USER;       -- USER = our three triggers; the foreign key's own
                                                      -- internal triggers are not touched
-- the repair, with a WHERE clause, and check the row count it reports against the BUILD-LOG entry:
UPDATE macro_metrics SET value = 123.4 WHERE metric = 'XYZ' AND effective_at = '2025-01-01' AND revision = 0;
ALTER TABLE macro_metrics ENABLE TRIGGER USER;
SELECT tgname, tgenabled FROM pg_trigger
 WHERE tgrelid = 'macro_metrics'::regclass AND NOT tgisinternal;   -- all three must read 'O'
COMMIT;                                               -- or ROLLBACK if anything above looks wrong
```

**Why one transaction.** DDL is transactional in Postgres. If the session dies, the connection drops, or you type
`ROLLBACK`, the triggers are enabled again by definition: there is no state in which they stay off by accident.
(`DISABLE TRIGGER` takes a lock that blocks writers to the table until the transaction ends, so a running ingest
waits; it does not fail.) `SET LOCAL log_statement` needs the superuser, which the compose `POSTGRES_USER` is. The
statements then sit in the Postgres container's log (`docker compose logs postgres`), which lasts only as long as
that container's logs do, so the BUILD-LOG entry is the durable record, and the log is the cross-check.

**What not to use.**
- `SET session_replication_role = replica`. It silences every trigger for the whole session, including the
  foreign keys', leaves no mark, and is exactly the silent bypass this runbook exists to avoid.
- `ALTER FUNCTION` / `DROP FUNCTION macro_metrics_immutable`, or `DROP TRIGGER`. A change to the guard is a
  migration, reviewed like any other.
- A bare `ALTER TABLE ... DISABLE TRIGGER` outside a transaction, or without the `ENABLE` inside the same one.

## After

1. **Confirm the guard is back.** In a fresh session, this must fail with "macro_metrics vintages are immutable",
   and the `ROLLBACK` leaves nothing behind:

   ```sql
   BEGIN; UPDATE macro_metrics SET source = source || 'x'; ROLLBACK;
   ```

   This check needs at least one row: on an empty table the `UPDATE` touches nothing, fires no row trigger, and
   "succeeds". There, rely on the `tgenabled` query instead (three rows, all `O`).

2. Finish the BUILD-LOG entry with the rows actually changed and the after state (`\copy` the same rows to
   `after.csv`).
3. If the repair changes what a past replay would have seen, say so in the entry. A published outcome that was
   computed on a bad vintage is a finding, not just a data fix.

## What is not built

Nothing watches that the triggers are enabled; the single-transaction rule is what makes a forgotten re-enable
hard to do. If this ever becomes a real worry, the trigger states belong on `/health/data` (`tgenabled` per
trigger). The test `test_the_documented_repair_procedure_works_and_leaves_the_triggers_enabled` covers the
procedure above, including that an abandoned repair leaves them enabled.
