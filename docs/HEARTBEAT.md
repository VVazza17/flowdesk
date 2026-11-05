# FlowDesk — External heartbeat runbook

The decisions and the reasoning behind them are in `docs/adr/0008-external-heartbeat.md`. This is how to
operate it.

## How it works

The `heartbeat` compose service asks `/health/ready` every 5 minutes and pings the external monitor only if
the answer is exactly `ok`. After 45 minutes without a ping, the monitor alerts. Nothing else is involved.

## Where it is armed

**`HEARTBEAT_URL` stays blank on development machines and is set only on a deployed instance.**

A laptop that sleeps nightly would page constantly and train you to ignore the alerts, which is worse than no
monitoring. The drill below proves the mechanism; it isn't a reason to leave it armed on a dev machine.

Blank is safe and needs no other change: the sender keeps running, sends nothing, and logs
`heartbeat_disabled` every cycle.

Each deployed instance gets **its own check**. If two instances share a URL, the live one masks the dead one.

The URL is a credential: anyone holding it can fake this box being alive. It belongs in `.env` only, which git
ignores.

## Setting up a check

1. Create a free account at healthchecks.io and a check using a **simple schedule**:
   - **Period: 5 minutes. Grace: 40 minutes.** These live only in the dashboard, so nothing in the repo can
     correct them. The reasoning is in ADR-0008 section 3. The provider's docs don't state limits for these
     values, so confirm the dashboard accepts them.
   - Alert to something off the box, such as email.
2. Put the ping URL in `.env` as `HEARTBEAT_URL=https://hc-ping.com/<uuid>`.
3. Run `docker compose up -d heartbeat`. Compose recreates the sender with the new URL.
4. Confirm **the dashboard** shows pings arriving, not just the logs.

## The drill

The smoke test uses a fake monitor. This is the only proof that an alert really reaches you.

1. Arm it, following the steps above. On a dev machine this is temporary.
2. Confirm the dashboard shows pings. This also catches a mistyped UUID.
3. Stop the whole stack (`docker compose down`) or, better, shut the machine down.
4. **An alert should arrive about 40 to 45 minutes later.** Note the time it actually took.
5. Start it again and confirm the recovery notice.
6. **On a dev machine, blank `HEARTBEAT_URL` and run `docker compose up -d heartbeat`.** Pause or delete the check
   too, or it stays down and may keep nagging.
7. Record the result in `docs/BUILD-LOG.md`.

Re-run it on a deployed instance quarterly and after any notice from the provider. A monitor that has died
sends no alert, so this is the only thing that would catch that. Nothing schedules or reminds you of it.

## Reading the logs

`docker compose logs heartbeat`. One JSON line per cycle:

| `event` | Meaning | What to do |
|---|---|---|
| `pinged` | Ready was `ok`; the monitor accepted the ping | Nothing |
| `skipped` | Not pinging. `reason`: `ready_status`, `ready_http_status` or `ready_unreachable` | Check `/health/ready`; an alert follows if this persists |
| `ping_failed` | Ready was `ok`, but the monitor couldn't be reached or returned non-2xx | Check the network, then the provider |
| `ping_rejected` | The monitor returned 200 `OK (not found)`: it doesn't know this URL | Fix `HEARTBEAT_URL` |
| `ping_rate_limited` | The provider dropped the ping | Check that `HEARTBEAT_INTERVAL_SECONDS` isn't set low |
| `heartbeat_disabled` | `HEARTBEAT_URL` is blank | Correct on a dev machine. **On a deployed instance nothing is watching: set it** |

`HEARTBEAT_URL` without `http://` or `https://`, or a non-positive `HEARTBEAT_INTERVAL_SECONDS`, makes the
sender refuse to start. It then restarts in a loop, visible in `docker compose ps` and the logs. The API and the
rest of the stack are unaffected. The error doesn't print the URL.

## Planned downtime on a deployed instance

Under 40 minutes needs nothing. For longer, pause the check in the dashboard first. Pings resume it.

## An alert arrived

1. Is the machine up, and does it have network access? If not, that's the outage.
2. `docker compose ps`: is `heartbeat` running?
3. `curl http://127.0.0.1:8000/health/ready`. Anything but `ok` explains the silence, including `degraded`, and
   the states are in `docs/HEALTH.md`.
4. `docker compose logs --tail 20 heartbeat`.

## Replacing the provider

For a terms change, a new paid requirement, or the service going away. Don't pay for one (`ZERO_COST_MODE`).

1. Choose a free service that accepts a plain GET on a private URL and can alert somewhere off the box.
2. Create a check with the same timing: 5-minute period, 40-minute grace, or 45 minutes of silence if it takes
   a single value.
3. Change `HEARTBEAT_URL` in `.env` and run `docker compose up -d heartbeat`.
4. Run the drill.
5. Update ADR-0008 section 6.

No code changes. If the new service signals errors in a 200 body, as Healthchecks does, check whether the
`not found` handling in `app/heartbeat.py` needs an equivalent. Its substrings just won't match otherwise.

Do not self-host the monitor on the monitored box. That defeats its only purpose.

## Testing

`bash infra/smoke-slice10.sh` runs against the real stack with a throwaway monitor, so it needs no account. The
trap restores the stack and recreates the sender from `.env`. While it runs, an armed instance sends no real
pings, so it should stay well under the 40-minute grace.
