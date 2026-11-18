"""Mutation check: break the code in a copy, and the named tests must fail.

Rule 9 (CLAUDE.md) says a keyed test is checked by mutation: delete the key or filter and watch the
test fail. This runs that, for a named suite, without touching the working tree:

    python -m tools.mutate m2-slice6-step2      (from backend/, with DATABASE_URL set)
    python -m tools.mutate heartbeat-clock

For each mutation: copy backend/ to a temporary directory, replace one exact snippet in one file
(the snippet must occur exactly once, so a stale mutation fails loudly instead of testing nothing),
run only the tests named for it, and require pytest's exit code 1 (tests failed). Exit code 0 means
the mutation SURVIVED: the tests did not notice the code was broken. The unmutated copy is run first
and must pass, or every "caught" mutation would mean nothing.

Only the suite's own test file runs, never test_migrations.py: `alembic check` would flag almost any
schema mutation as model drift, and that would say nothing about whether these tests catch it.
"""

import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

M10 = "alembic/versions/0010_price_tables.py"
M11 = "alembic/versions/0011_bar_gaps.py"
M12 = "alembic/versions/0012_universe_membership_close_reason.py"
# The trigger-creating loop in 0010 and 0011 (each file has it once).
TRIGGER_LOOP = (
    'for event, scope in (("UPDATE", "ROW"), ("DELETE", "ROW"), ("TRUNCATE", "STATEMENT")):'
)


@dataclass(frozen=True)
class Mutation:
    name: str
    file: str
    old: str
    new: str
    tests: str  # a pytest -k expression: the tests that must catch it


SUITES: dict[str, tuple[str, list[Mutation]]] = {
    "heartbeat-clock": (
        "tests/test_task_heartbeat.py",
        [
            Mutation(
                "heartbeat held at the maximum (GREATEST)",
                "app/tasks/runs.py",
                ".values(heartbeat_at=now)",
                ".values(heartbeat_at=func.greatest(TaskRun.heartbeat_at, now))",
                "test_a_clock_step_backwards",
            ),
        ],
    ),
    "m2-slice6-step2": (
        "tests/test_price_tables.py",
        [
            # --- keys (rule 9) ---
            Mutation(
                "bars_daily key loses symbol_id",
                M10,
                'sa.PrimaryKeyConstraint("symbol_id", "effective_at"),\n'
                '        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"]),\n'
                "        _run_fk(),\n"
                "        *_bar_checks(),\n"
                '        comment=TABLE_COMMENTS["bars_daily"],',
                'sa.PrimaryKeyConstraint("effective_at"),\n'
                '        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"]),\n'
                "        _run_fk(),\n"
                "        *_bar_checks(),\n"
                '        comment=TABLE_COMMENTS["bars_daily"],',
                "test_bar_key_is_per_symbol and bars_daily",
            ),
            Mutation(
                "bars_intraday key loses symbol_id",
                M10,
                'sa.PrimaryKeyConstraint("symbol_id", "interval", "effective_at"),',
                'sa.PrimaryKeyConstraint("interval", "effective_at"),',
                "test_bar_key_is_per_symbol and bars_intraday",
            ),
            Mutation(
                "bar_total_return key loses revision",
                M10,
                'sa.PrimaryKeyConstraint("symbol_id", "effective_at", "revision"),',
                'sa.PrimaryKeyConstraint("symbol_id", "effective_at"),',
                "test_total_return_key",
            ),
            Mutation(
                "bar_total_return key loses symbol_id",
                M10,
                'sa.PrimaryKeyConstraint("symbol_id", "effective_at", "revision"),',
                'sa.PrimaryKeyConstraint("effective_at", "revision"),',
                "test_total_return_key",
            ),
            Mutation(
                "splits key loses symbol_id",
                M10,
                'sa.PrimaryKeyConstraint("symbol_id", "effective_at"),\n'
                '        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"]),\n'
                "        _run_fk(),\n"
                '        sa.CheckConstraint("ratio > 0"',
                'sa.PrimaryKeyConstraint("effective_at"),\n'
                '        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"]),\n'
                "        _run_fk(),\n"
                '        sa.CheckConstraint("ratio > 0"',
                "test_splits_key",
            ),
            Mutation(
                "bar_gaps uniqueness loses interval",
                M11,
                'sa.UniqueConstraint("symbol_id", "interval", "missing_from"),',
                'sa.UniqueConstraint("symbol_id", "missing_from"),',
                "test_gap_uniqueness",
            ),
            Mutation(
                "bar_gaps uniqueness loses symbol_id",
                M11,
                'sa.UniqueConstraint("symbol_id", "interval", "missing_from"),',
                'sa.UniqueConstraint("interval", "missing_from"),',
                "test_gap_uniqueness",
            ),
            Mutation(
                "bar_gaps has no uniqueness at all",
                M11,
                'sa.UniqueConstraint("symbol_id", "interval", "missing_from"),',
                "",
                "test_gap_uniqueness",
            ),
            # --- CHECKs ---
            Mutation(
                "raw_basis check accepts anything",
                M10,
                "\"raw_basis IN ('live', 'reconstructed', 'unreconstructed')\"",
                '"true"',
                "test_bar_checks_refuse_bad_values and raw_basis",
            ),
            Mutation(
                "feed check accepts anything",
                M10,
                "sa.CheckConstraint(\"feed IN ('poll', 'backfill')\"",
                'sa.CheckConstraint("true"',
                "test_bar_checks_refuse_bad_values and feed",
            ),
            Mutation(
                "feed check is case-insensitive",
                M10,
                "sa.CheckConstraint(\"feed IN ('poll', 'backfill')\"",
                "sa.CheckConstraint(\"lower(feed) IN ('poll', 'backfill')\"",
                "test_bar_checks_refuse_bad_values and feed",
            ),
            Mutation(
                "venue_scope check accepts consolidated",
                M10,
                "\"venue_scope IN ('provider_default')\"",
                "\"venue_scope IN ('provider_default', 'consolidated')\"",
                "test_bar_checks_refuse_bad_values and venue_scope",
            ),
            Mutation(
                "a bar may be available at its own open",
                M10,
                'sa.CheckConstraint("available_at > effective_at", name="settled_after_open"),\n'
                "    ]",
                'sa.CheckConstraint("available_at >= effective_at", name="settled_after_open"),\n'
                "    ]",
                "test_bar_checks_refuse_bad_values and available_at",
            ),
            Mutation(
                "intraday interval check accepts anything",
                M10,
                'sa.CheckConstraint("interval IN (\'15m\')", name="interval"),',
                'sa.CheckConstraint("true", name="interval"),',
                "test_intraday_interval",
            ),
            Mutation(
                "total return has no settle check",
                M10,
                'sa.CheckConstraint("available_at > effective_at", name="settled_after_open"),\n'
                '        comment=TABLE_COMMENTS["bar_total_return"],',
                'comment=TABLE_COMMENTS["bar_total_return"],',
                "test_total_return_is_settled",
            ),
            Mutation(
                "split ratio may be zero",
                M10,
                '"ratio > 0"',
                '"ratio >= 0"',
                "test_split_ratio",
            ),
            Mutation(
                "gap interval accepts anything",
                M11,
                "\"interval IN ('1d', '15m')\"",
                '"true"',
                "test_gap_checks",
            ),
            Mutation(
                "gap range may be empty",
                M11,
                '"missing_to > missing_from"',
                '"missing_to >= missing_from"',
                "test_gap_checks",
            ),
            Mutation(
                "gap may be resolved before it was recorded",
                M11,
                '"resolved_at IS NULL OR resolved_at >= recorded_at"',
                '"true"',
                "test_gap_checks",
            ),
            Mutation(
                "close_reason check is one-way (closed needs a reason)",
                M12,
                '"(valid_to IS NULL) = (close_reason IS NULL)"',
                '"valid_to IS NULL OR close_reason IS NOT NULL"',
                "test_close_reason",
            ),
            Mutation(
                "close_reason check is one-way (a reason needs closed)",
                M12,
                '"(valid_to IS NULL) = (close_reason IS NULL)"',
                '"close_reason IS NULL OR valid_to IS NOT NULL"',
                "test_close_reason",
            ),
            Mutation(
                "close_reason accepts any value",
                M12,
                "\"close_reason IN ('delisted', 'merged', 'removed_by_operator')\"",
                '"true"',
                "test_close_reason",
            ),
            # --- insert-only triggers ---
            Mutation(
                "no UPDATE trigger on the observation tables",
                M10,
                TRIGGER_LOOP,
                'for event, scope in (("DELETE", "ROW"), ("TRUNCATE", "STATEMENT")):',
                "test_no_stored_column_can_be_updated",
            ),
            Mutation(
                "no DELETE trigger on the observation tables",
                M10,
                TRIGGER_LOOP,
                'for event, scope in (("UPDATE", "ROW"), ("TRUNCATE", "STATEMENT")):',
                "test_rows_cannot_be_deleted",
            ),
            Mutation(
                "no TRUNCATE trigger on the observation tables",
                M10,
                TRIGGER_LOOP,
                'for event, scope in (("UPDATE", "ROW"), ("DELETE", "ROW")):',
                "test_rows_cannot_be_deleted",
            ),
            Mutation(
                "the trigger ignores c_raw",
                M10,
                "AND (to_jsonb(NEW) - 'ingestion_run_id') = (to_jsonb(OLD) - 'ingestion_run_id')",
                "AND (to_jsonb(NEW) - 'ingestion_run_id' - 'c_raw')"
                " = (to_jsonb(OLD) - 'ingestion_run_id' - 'c_raw')",
                "test_no_stored_column_can_be_updated and c_raw",
            ),
            Mutation(
                "the run pointer may be repointed",
                M10,
                "OR NEW.ingestion_run_id IS NOT DISTINCT FROM OLD.ingestion_run_id)\n"
                "            THEN\n"
                "                RETURN NEW;\n"
                "            END IF;\n"
                "            RAISE EXCEPTION\n"
                "                '% is insert-only",
                "OR true)\n"
                "            THEN\n"
                "                RETURN NEW;\n"
                "            END IF;\n"
                "            RAISE EXCEPTION\n"
                "                '% is insert-only",
                "test_the_run_pointer and not bar_gaps",
            ),
            Mutation(
                "no carve-out: a purged run cannot be deleted",
                M10,
                "AND (to_jsonb(NEW) - 'ingestion_run_id') = (to_jsonb(OLD) - 'ingestion_run_id')",
                "AND to_jsonb(NEW) = to_jsonb(OLD)",
                "test_the_run_pointer and not bar_gaps",
            ),
            Mutation(
                "purged runs are blocked (no ON DELETE SET NULL)",
                M10,
                '["ingestion_run_id"], ["ingestion_runs.id"], ondelete="SET NULL")',
                '["ingestion_run_id"], ["ingestion_runs.id"])',
                "test_the_run_pointer and not bar_gaps",
            ),
            Mutation(
                "bar_gaps may be re-resolved or reopened",
                M11,
                "OR (OLD.resolved_at IS NULL AND NEW.resolved_at IS NOT NULL))",
                "OR true)",
                "test_a_gap_is_resolved_once",
            ),
            Mutation(
                "bar_gaps trigger ignores missing_to",
                M11,
                "AND (to_jsonb(NEW) - 'resolved_at' - 'ingestion_run_id')\n"
                "                   = (to_jsonb(OLD) - 'resolved_at' - 'ingestion_run_id')",
                "AND (to_jsonb(NEW) - 'resolved_at' - 'ingestion_run_id' - 'missing_to')\n"
                "                   = (to_jsonb(OLD) - 'resolved_at' - 'ingestion_run_id'"
                " - 'missing_to')",
                "test_nothing_else_about_a_gap and missing_to",
            ),
            Mutation(
                "bar_gaps has no DELETE trigger",
                M11,
                TRIGGER_LOOP,
                'for event, scope in (("UPDATE", "ROW"), ("TRUNCATE", "STATEMENT")):',
                "test_a_gap_cannot_be_deleted",
            ),
            Mutation(
                "bar_gaps run pointer may be repointed",
                M11,
                "OR NEW.ingestion_run_id IS NOT DISTINCT FROM OLD.ingestion_run_id)",
                "OR true)",
                "test_the_run_pointer and bar_gaps",
            ),
            # --- hypertables, indexes, comments ---
            Mutation(
                "daily chunk interval is 7 days",
                M10,
                'HYPERTABLES = {"bars_daily": "365 days", "bars_intraday": "28 days"}',
                'HYPERTABLES = {"bars_daily": "7 days", "bars_intraday": "28 days"}',
                "test_hypertables",
            ),
            Mutation(
                "intraday chunk interval is 7 days",
                M10,
                'HYPERTABLES = {"bars_daily": "365 days", "bars_intraday": "28 days"}',
                'HYPERTABLES = {"bars_daily": "365 days", "bars_intraday": "7 days"}',
                "test_hypertables",
            ),
            Mutation(
                "TimescaleDB default indexes come back",
                M10,
                "INTERVAL '{chunk}', create_default_indexes => false)",
                "INTERVAL '{chunk}', create_default_indexes => true)",
                "test_the_only_indexes",
            ),
            Mutation(
                "a time column's comment no longer says it is one",
                M10,
                '"TIME COLUMN. The instant the bar opened, in UTC',
                '"The instant the bar opened, in UTC',
                "test_every_column_and_the_table_carry_a_comment and bars_intraday",
            ),
            Mutation(
                "the daily settle point is not stated",
                M10,
                "the second session after this one (D+2; S measured 2026-11-18)",
                "the second session after this one (S measured 2026-11-18)",
                "test_the_settle_points_are_stated",
            ),
        ],
    ),
}


def pytest(cwd: Path, test_file: str, selector: str | None) -> tuple[int, str]:
    """pytest's exit code and its summary line."""
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", test_file]
    if selector:
        cmd += ["-k", selector]
    done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    lines = [ln for ln in done.stdout.splitlines() if ln.strip()]
    return done.returncode, lines[-1] if lines else ""


def caught(code: int, summary: str) -> bool:
    # Exit 1 with a FAILED test and no ERROR. An error (a fixture, i.e. the migration itself, broke)
    # would also exit 1, and would "catch" the mutation for the wrong reason.
    return code == 1 and "failed" in summary and "error" not in summary


def main(suite: str) -> int:
    test_file, mutations = SUITES[suite]
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "clean"
        shutil.copytree(BACKEND, base, ignore=shutil.ignore_patterns("__pycache__", ".*"))
        code, summary = pytest(base, test_file, None)
        if code != 0:
            print(f"FAIL: the unmutated suite does not pass (pytest exit {code}: {summary})")
            return 1
        print(f"baseline: {test_file} passes unmutated")
        survived = 0
        for i, m in enumerate(mutations, 1):
            work = Path(tmp) / f"m{i}"
            shutil.copytree(base, work)
            target = work / m.file
            src = target.read_text(encoding="utf-8")
            if src.count(m.old) != 1:
                print(f"FAIL: '{m.name}': snippet occurs {src.count(m.old)} times in {m.file}")
                return 1
            target.write_text(src.replace(m.old, m.new), encoding="utf-8")
            code, summary = pytest(work, test_file, m.tests)
            ok = caught(code, summary)
            verdict = "caught" if ok else f"SURVIVED (pytest exit {code})"
            survived += not ok
            print(f"  {i:2}. {verdict:24} {m.name}  [-k {m.tests}]  {summary.strip('= ')}")
            shutil.rmtree(work)
        print(f"{len(mutations) - survived}/{len(mutations)} mutations caught")
        return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "m2-slice6-step2"))
