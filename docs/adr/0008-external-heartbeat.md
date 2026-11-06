# ADR-0008: External Dead-Man Heartbeat

**Status:** Accepted. The sender is built and smoke-tested, and the end-to-end alert drill passed (recorded 2026-11-05;
see "What has been proven").
**Date:** 2026-11-04
**Source:** M1 slice 10. Interprets §16 M1 ("external dead-man heartbeat, free ping-monitor service, the
monitor must live outside the monitored box"), §2 decision 26, and ADR-0006 decision 6. None of them says
what a ping attests or how long silence lasts before an alert. Those are decisions made here. Spec gaps
are logged as IDEA-031. This is an implementation decision, not an M0 spike finding.

## Context

If the whole stack or machine dies, nothing inside it can report that. ADR-0006 decision 6 defines two
layers: this external, content-blind check, and the internal §11 `heartbeat` Celery task (not built yet),
which alone sees brief lateness and per-feed staleness. This does not replace that task.

## Decisions

### 1. Push, not pull

`app/heartbeat.py` runs on the monitored box as the compose service `heartbeat` and makes outbound GETs to a
third-party monitor. The monitor alerts when the pings stop. A pull monitor would need to reach the API from
the internet, but the API binds to loopback and the public profile is not internet-ready (IDEA-019). Push
opens no inbound path, and the service publishes no port.

The sender living on the box doesn't break "the monitor must live outside the monitored box": the sender
only produces pings, and the part that decides and alerts is off-box. If the box, Docker or the sender dies,
the result is the same silence.

### 2. A ping attests that `/health/ready` is exactly `ok`

Decided by Kyle at planning. `degraded`, a non-200, an unreachable API or an unparseable answer all mean no
ping. There is no `/fail` signal; silence is the only failure signal.

A plain liveness gate (`/health/live`) was rejected: Postgres could be down for days with the monitor green.
A "any 200" gate was rejected too: from M2, Celery Beat and the workers depend on Redis, so a dead Redis also
stops the internal heartbeat. Under that gate, jobs would stop with the external monitor still green and
nothing left to report it.

This is broader than ADR-0006's "total outage" wording. It still doesn't look at content: stale feeds don't
affect `/health/ready`, so they don't page here. They remain the internal heartbeat's job.

### 3. Ping every 5 minutes; alert after 45 minutes of silence (period 5 + grace 40)

- **Failure to alert takes 40 to 45 minutes**, depending on where in the 5-minute cycle the failure lands.
  That is about 8 consecutive missed pings, so one miss never pages.
- **What it must ride out:** a Redis restart or container recycle (seconds to about a minute), an image
  rebuild or `docker compose pull && up -d`, a smoke script stopping services, and a host reboot with OS
  updates. The reboot is the slowest and sets the floor, which is why 30 minutes was too tight. **These
  durations are estimates, not measurements.**
- **Why not 60:** nothing in M1 is time-critical, but from M2 ingestion runs on a schedule, and an hour lost
  on top of the reaction time is a meaningful gap. 45 still clears a slow reboot.

**The window has known blind spots:**

- **An outage shorter than 40 minutes never pages.**
- **A flapping stack reads as healthy.** Each ping resets the monitor's clock, so a stack that is broken most
  of the time but lands one `ok` ping every 40 minutes never alerts and shows green on the dashboard. At one
  check per 5 minutes, that is 1 good check in 8: broken for about seven-eighths of the time and reported as
  healthy. The sender logs a `skipped` line for each missed ping, so the history exists, but nothing reads
  those logs and nobody is told. This is a limit of any dead-man check with a long window, not a bug.

Revisit the number if the live period shows real recoveries near the limit, or if the flapping case actually
occurs.

**These two values live in the provider's dashboard, not this repo**, so nothing can enforce them or detect
them drifting. This ADR and `docs/HEARTBEAT.md` are the record, and the drill checks them.
`HEARTBEAT_INTERVAL_SECONDS` defaults to 300 and is overridable only for the smoke test.

### 4. Warning for anyone changing `/health/ready`

**Adding any new `degraded` condition to `/health/ready` now also stops the heartbeat and pages the owner
after 45 minutes.** `degraded` is a 200 and looks harmless from the API's side. It is not harmless here.

If a new condition shouldn't page, it doesn't belong in `/health/ready`'s `degraded`, or the gate in
`app/heartbeat.py` has to change in the same commit. The same warning sits in the code at the `degraded`
return in `app/services/health.py` and in `docs/HEALTH.md`, because that's where someone adding a condition
will be looking.

### 5. Armed only on a deployed instance

`HEARTBEAT_URL` stays blank on development machines and is set only on a deployed instance (Kyle). A laptop
that sleeps nightly would page constantly and train its owner to ignore alerts, which is worse than no
monitoring. The drill proves the mechanism; it isn't a reason to leave it armed there.

Blank is a valid state: the sender stays running and logs `heartbeat_disabled` every cycle, and `up -d` still
works from a fresh clone (M1 gate). So a deployed instance that loses its URL is **unmonitored without any
alert**, with only that log line to show for it. `docs/HEARTBEAT.md` has the check for this.

**One check per deployed instance.** Sharing a URL means a live instance masks a dead one.

### 6. Provider: Healthchecks.io, free plan, and what happens if it changes or goes away

This is the one piece of FlowDesk outside the owner's control.

As fetched from its pricing and API docs on 2026-11-04: the Hobbyist plan is $0 with 20 checks and 100 log
entries per check, a plain GET counts as a ping, and it asks for at most 5 pings a minute per check. The
pricing page doesn't state a credit card requirement either way, or any commercial-use restriction. Its docs
don't state limits on period or grace.

- **What is coupled to the provider:** one URL in `.env`, the two dashboard values, and one body check. There
  is no SDK or account data in the repo, and no request body is sent. The provider sees only the source IP and
  the ping times. The URL is a credential: anyone holding it can fake this box being alive, so it is never
  logged and errors never echo it.
- **The provider-specific part:** Healthchecks answers a mistyped UUID with **200 `OK (not found)`**, and a
  dropped ping with 200 `OK (rate limited)`. The sender checks the body for those strings so a typo doesn't look
  like success. On another provider they simply never match.
- **Terms change or a paid requirement:** don't pay (`ZERO_COST_MODE`). Pick another free service that accepts a
  plain GET on a private URL and alerts off-box, give it the same 5 + 40, change `HEARTBEAT_URL`, run `docker
  compose up -d heartbeat`, re-run the drill, and update this section. No code change. Steps are in
  `docs/HEARTBEAT.md`.
- **The provider vanishes: this fails silently.** A dead monitor sends no alert. The only trace is `ping_failed`
  lines in a log nobody is watching, and FlowDesk stays up but unmonitored until someone notices. The mitigation
  is procedural and weak: re-run the drill quarterly and after any provider notice. Nothing schedules or enforces
  that.
- **Not fallbacks:** self-hosting the monitor on the same box, which breaks the one constraint. A second
  simultaneous provider isn't built either, because it would add setup for an unlikely failure.

## What has been proven

`infra/smoke-slice10.sh` and `backend/tests/test_heartbeat.py` prove the sender, using a throwaway local
"monitor". **They do not prove an alert reaches anyone.** As of this ADR:

- The real provider has never been called. The `OK (not found)` handling is tested against a fake response
  written from its docs.
- The 45-minute timing has never run.
- The sender has never run on arm64 as a compose service, though it is stdlib-only in the same image.

The drill in `docs/HEARTBEAT.md` closes the first two. Its result belongs in `docs/BUILD-LOG.md`.

**Update, recorded 2026-11-05:** the drill passed against the real provider. The first two items above are now proven:
pings were accepted, and with the stack down at 11:57 the DOWN alert arrived at 12:41:10 (period 5, grace 40), then
an UP notice on recovery. Details are in the 2026-11-05 BUILD-LOG entry. The arm64 item remains open. The list above
is kept as written, as the state at the time of this ADR.

## Consequences

- `/health/ready`'s `ok` now has an operational consumer: changing its meaning changes when the owner is paged.
- Every API instance logs one extra request per 5 minutes.
- A missed-backup signal (IDEA-029) would need its own check, since this one says nothing about backups.
