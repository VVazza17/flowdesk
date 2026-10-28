# FlowDesk — Build Log

A narrative, session-by-session record of what got built and decided. Not a changelog (git
history covers that) and not a spec (docs/architecture.md covers that) — this is the "what
happened and why" thread between them, for a future session to read cold.

**Format:** reverse-chronological, newest entry at top. Append new entries; do not rewrite
old ones except to fix a factual error, and note the correction inline rather than silently
editing history.

---

## 2026-10-28 — M1 slice 1: compose with Postgres/TimescaleDB and Redis

**What shipped**

- `docker-compose.yml` at the repo root: `postgres` and `redis`, nothing else. `.env.example`,
  `.gitattributes` (LF for `*.sh`), `infra/smoke-slice1.sh`, and a filled-in `Commands`
  block in CLAUDE.md.
- Pinned `timescale/timescaledb:2.31.0-pg16` and `redis:7.4-alpine`. Both verified with
  `docker buildx imagetools inspect` to publish linux/amd64 and linux/arm64. The first
  candidate, 2.17.2, was rejected as stale (2024 line); 2.31.0 was the newest 2.x pg16 tag
  on 2026-10-28. `timescale/timescaledb` is still the maintained repo.
- Postgres published on `127.0.0.1` only; Redis publishes no host port.

**Verified:** `infra/smoke-slice1.sh` passes on a cold start (`down -v`, then up) and on a
warm one: `select 1`, timescaledb extension present, Redis `PONG`, Redis unpublished,
Postgres bound to loopback.

**Decisions**

- Healthcheck uses `pg_isready -h 127.0.0.1` plus `start_period: 30s`. The `-h` forces TCP
  because the entrypoint's temporary init server is believed to listen on the unix socket
  only. Not tested in isolation: the cold-start pass shows the check does not break init,
  not that a socket-only check would have failed.
- Redis has no persistence: it is never the source of truth.

**What broke**

- A native Windows `postgresql-x64-17` service held port 5432, so compose could not bind.
  Stopped by the user; `POSTGRES_PORT` in `.env` is the escape hatch if it recurs.
- The smoke test's first Redis check was wrong: `docker compose port` prints `:0` with exit 0
  for an unpublished port, so any non-empty output tripped it. Fixed in the script.

**Not done:** no image of ours is built yet, so `docker buildx build` for amd64+arm64 starts
mattering in slice 2 (first Dockerfile). Nothing committed.

---

## 2026-08-16 — M0 closed: seven ADRs, oracle fixtures, IDEAS registry consolidated to twenty

**What shipped**

- All seven ADRs in `docs/adr/` (0001–0007), closing M0's outstanding-deliverables list:
  0001 FX base/quote convention, 0002 raw-vs-adjusted price policy, 0003 point-in-time
  observation model, 0004 snapshot/watermark policy, 0005 event natural keys & episodes,
  0006 delivery semantics, 0007 private-vs-public deployment mode.
- `tests/fixtures/premium_oracle.json` — the three oracle cases required by §15/ADR-0001:
  the §7.5 hand-computed worked example, a real spike-evidence case (verbatim from
  `spikes/spike1_evidence.json`), and a synthetic non-0.1-ratio case.
- `docs/IDEAS.md` consolidated to twenty entries (IDEA-001 through IDEA-020). IDEA-001
  through 012 are the pre-existing parked-feature backlog; IDEA-013 through 020 are new —
  spec defects surfaced while writing the ADRs, not features.

**Decision:** M0 is CLOSED as of this date.

**The eight spec defects found while writing ADR-0005/0006/0007 (IDEA-013–020)**

Each is a real contradiction or gap in the frozen spec, not a proposal — logged so a future
session doesn't have to rediscover them by re-reading §8 line by line.

1. **IDEA-013** — `events.natural_key` (§8, no date component) contradicts §7.16's own
   five-day-episode worked example (which needs five distinct rows to exist). Blocks M3;
   needs a decision on which side is wrong before the dedup framework is built.
2. **IDEA-014** — `events.episode_id`'s inline comment cites `§7.15` (Risk Lens); should
   cite `§7.16` (Episodes). Cosmetic, predates a v4.7 renumbering. Doesn't block M3 alone.
3. **IDEA-015** — CLAUDE.md's §17.1 hard rule ("never integrate a broker API... in any
   phase") cites decision 15 as its authority while decision 15 (as revised in v4.1) actually
   defines a gated Execution Plane that may execute. Pre-v4.1 text that never got updated.
   Resolved same day — see below.
4. **IDEA-016** — `alerts` (§8) has no FK back to the `events` row that fired it — no
   `event_id`, `calculation_run_id`, or `snapshot_id`. `alert_rules`/`alerts` predate the
   events-v2 lineage redesign. Likely blocks M4's alert-history join and M8's per-alert
   retry view.
5. **IDEA-017** — M3's exit gate promises "exactly once" dispatch across all channels
   including Discord, which is structurally impossible (no idempotency key on Discord's
   webhook API — a timeout is indistinguishable from a lost send). Overclaim, not just an
   unspecified mechanism; needs the gate split into in-app (exactly-once, achievable) vs.
   webhook (at-least-once, the real ceiling).
6. **IDEA-018** — IDEA-003's own unfreeze triggers cite "confidence tier: low, per
   ADR-0007," but confidence tiers are §7.16 content and ADR-0007 is private-vs-public
   deployment mode — dangling reference, predates the current ADR numbering.
7. **IDEA-019** — M1's exit gate ("public profile exposes read-only routes only") conflicts
   with §14's own two-tier design, which allows an authenticated admin surface that mutates.
   Needs a `# DECISION:` before M1's acceptance tests are written, since the two readings
   produce different implementations.
8. **IDEA-020** — the LLM spend cap's "hard-stop" is enforced by a Redis daily counter, but
   Redis is explicitly never the source of truth (decision 26) and a restart/eviction resets
   it mid-day — a real, if small, gap between what §14 claims and what's actually guaranteed.

**IDEA-015 was also resolved today**, not just logged: CLAUDE.md's broker rule was rewritten
to match decision 15 as revised (Execution-Plane code is out of scope until M0–M8 ship and
decision 15's gates are met, rather than banned outright), with the drift explained inline.
The other seven (013, 014, 016–020) remain open — each needs either a `# DECISION:` comment
or a spec correction release before the milestone it blocks starts.

**Pattern noticed:** IDEA-015's writeup ended up cataloguing five separate instances of the
same drift class across the ADRs — a revision or renumbering happened somewhere in the spec
and a dependent reference elsewhere was never updated to match (three stale `§` citations,
one stale hard-rule authority, one schema table that predates a redesign). That's what
motivated the full cross-reference audit now queued as a pre-M1 cleanup item (see CLAUDE.md
"Current state").

**Lesson learned — whole-file rewrites drop content near the end**

While drafting IDEAS.md and the ADRs, a whole-file rewrite of a long document silently
dropped content near the end of the file three separate times during this session — not a
tool failure, a self-inflicted editing habit (regenerating the entire file from a truncated
mental model of it instead of extending what was there). Each time it required restoring the
missing entries from git history.

**Mitigation going forward:** default to append-only edits on long documents (add new
sections at the end or via targeted inserts, don't regenerate the whole file), and run
`git diff` before committing any change to a long document to confirm nothing upstream of
the edit point silently disappeared.
