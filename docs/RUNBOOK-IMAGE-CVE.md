# Runbook: the image scan goes red (base-image CVE)

The `security` job in `.github/workflows/ci.yml` builds `backend/Dockerfile` and scans it with Grype:
fixable findings at HIGH or above fail the build (`severity-cutoff: high`, `only-fixed: true`). It also runs
on the daily schedule, so it can go red with no commit from us: the base image is `python:3.12-slim`, which
moves, and Debian publishes security fixes to `trixie-security` before the image is rebuilt.

**Rules.** Fix the image. Do not lower `severity-cutoff`, do not drop `only-fixed`, do not add an ignore
entry. `only-fixed` already excludes findings nobody can act on, so a red run always has a fix available.

First occurrence: 2026-11-08, two HIGH findings in `zlib1g` (CVE-2026-76844, CVE-2026-85091), fixed in
`1:1.3.dfsg+really1.3.1-1+deb13u1`. The published base image still carried `+b1`.

## 1. Read the failure

Open the failed `security` job and read the Grype table: package, installed version, **fixed-in version**,
severity. Reproduce it locally, the same way CI sees it:

```
docker build --pull --no-cache -t flowdesk-api:scan ./backend
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock anchore/grype:latest \
  flowdesk-api:scan --only-fixed --fail-on high -o table
```

`--pull` matters. Without it a stale local copy of the base image hides what CI sees.

## 2. Ask whether the base image already has the fix

```
docker pull python:3.12-slim
docker run --rm python:3.12-slim sh -c 'apt-get update -qq && apt-cache policy <package>'
```

Compare `Installed` with `Candidate`.

- **Installed is at or above the fixed version:** the base is fine and your local copy was stale. Re-run CI.
- **Installed is below it, and `Candidate` is the fixed version:** the base lags the archive. Go to step 3b.
- **Neither has it:** no fix exists yet. `only-fixed` should have kept this from failing the build, so
  re-read the table. If the finding really has no fix, do not act; if it is in the Python runtime or in
  `pip`, see "Not Debian packages" below.

## 3a. A newer base digest carries the fix: pin it

The Dockerfile currently floats on the tag. If a rebuilt `python:3.12-slim` carries the fix, nothing in the
Dockerfile needs to change: the next CI build pulls it. Re-run the failed job. Only pin a digest
(`FROM python:3.12-slim@sha256:...`) if we decide to stop floating, and then it becomes this runbook's job to
move it. That is a separate decision; today there is no pin.

## 3b. The base lags: upgrade that one package in the Dockerfile

Edit the marked block near the top of `backend/Dockerfile`:

- Add the package to the `--only-upgrade` list. One package per finding, never `apt-get upgrade`.
- Set the `dpkg --compare-versions ... ge "<fixed-in version>"` assertion to the fixed version from the
  Grype table. The assertion fails the build if the fix did not land, and also if the Debian mirror is
  missing the package, so a bad upgrade can never ship quietly.
- Update the comment above the block with the package, the CVEs, the fixed version and the date.

Do not pin `pkg=version` in the install: when the base later ships a newer version that would turn into a
downgrade error. `--only-upgrade` plus the `ge` assertion survives that.

## 4. Verify before pushing

```
docker build -q -t flowdesk-api:scan ./backend
docker run --rm flowdesk-api:scan dpkg-query -W -f='${Package} ${Version}\n' <package>
docker buildx build --platform linux/arm64 --load -q -t flowdesk-api:scan-arm ./backend
docker run --rm --platform linux/arm64 flowdesk-api:scan-arm dpkg-query -W -f='${Package} ${Version}\n' <package>
```

Then repeat the Grype command from step 1 against `flowdesk-api:scan`; it must exit 0. Both architectures
matter because §13 targets ARM; Debian security fixes can reach one architecture before the other.
Finally run `bash infra/smoke-slice2.sh` (build, `/health/live`, loopback bind).

## 5. Remove the workaround

The patch block is temporary. When a base image ships the fixed version, the `dpkg --compare-versions`
assertion starts passing without the `apt-get install` doing anything. To check, comment out the block's
install and build with `--pull --no-cache`: if the assertion still passes, delete the whole block and its
comment. Do this when you next touch the Dockerfile; it is not urgent. Leaving it in costs one apt layer.

## 6. Record it

Add a `docs/BUILD-LOG.md` line: date, package, CVEs, which of 3a or 3b applied. If 3b, also say when you
expect to remove it.

## Not Debian packages

Grype also reports `pip` and the Python interpreter (`python 3.12.x`, type `binary`). Those are in the
base image, not in our locks, and today they appear only at MEDIUM or lower. If one reaches HIGH:

- `pip` in the image: upgrade it in the Dockerfile, with the same comment and verification as 3b.
- Python itself: a new `python:3.12-slim` is the fix (step 3a). Do not hand-patch the interpreter.
- A package from `requirements.lock`: that is the `audit` step's job, not this one; fix it with
  `infra/lock-deps.sh`.
