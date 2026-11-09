# Runbook: the image scan goes red (base-image CVE)

The `security` job in `.github/workflows/ci.yml` builds `backend/Dockerfile` and scans it with Grype:
fixable findings at HIGH or above fail the build (`severity-cutoff: high`, `only-fixed: true`). It also runs
on the daily schedule, so it can go red with no commit from us. The base image does not move: it is pinned by
digest (the `FROM` line of `backend/Dockerfile`, since M2 slice 0), so the image is the same bytes until
someone bumps it. What moves is the vulnerability database, which learns of new advisories daily, and Debian,
which publishes security fixes to `trixie-security` before the published `python:3.12-slim` is rebuilt.

**The cost of the pin.** The base no longer picks up fixes by itself. This scan, which only fires on fixable
HIGH or above, is the tripwire, so lower-severity fixes wait until the next bump.

**Rules.** Fix the image. Do not lower `severity-cutoff`, do not drop `only-fixed`, do not add an ignore
entry. `only-fixed` already excludes findings nobody can act on, so a red run always has a fix available.

First occurrence: 2026-11-08, two HIGH findings in `zlib1g` (CVE-2026-76844, CVE-2026-85091), fixed in
`1:1.3.dfsg+really1.3.1-1+deb13u1`. The published base image still carried `+b1`.

## 1. Read the failure

Open the failed `security` job and read the Grype table: package, installed version, **fixed-in version**,
severity. Reproduce it locally, the same way CI sees it:

```
docker build --no-cache -t flowdesk-api:scan ./backend
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock anchore/grype:latest \
  flowdesk-api:scan --only-fixed --fail-on high -o table
```

`--no-cache` matters, so the apt layer in the Dockerfile re-runs against today's archive. The base itself is
fixed by its digest; there is nothing to pull.

## 2. Ask whether the currently published base image has the fix

The pinned image is what failed. This step looks at what `python:3.12-slim` is *now*, which may have moved on.

```
docker pull python:3.12-slim
docker run --rm python:3.12-slim sh -c 'dpkg-query -W -f="\${Package} \${Version}\n" <package>; apt-get update -qq && apt-cache policy <package>'
```

Compare `Installed` with the fixed-in version, then with `Candidate`.

- **The published image's `Installed` is at or above the fixed version:** a newer base carries the fix. Go to
  step 3a.
- **`Installed` is below it, and `Candidate` is the fixed version:** the published base lags the archive too.
  Go to step 3b.
- **Neither has it:** no fix exists yet. `only-fixed` should have kept this from failing the build, so
  re-read the table. If the finding really has no fix, do not act; if it is in the Python runtime or in
  `pip`, see "Not Debian packages" below.

## 3a. A newer base digest carries the fix: bump the pin

Take the digest of the published tag you checked in step 2:

```
docker buildx imagetools inspect python:3.12-slim      # the "Digest:" line is the multi-arch index digest
```

Then, in `backend/Dockerfile` only:

- Replace the digest in the `FROM python:3.12-slim@sha256:...` line, and update the date in the comment above
  it. The Dockerfile is the one definition: the CI tooling container, `lock-deps.sh` and the smokes read it
  through `infra/base-image.sh`. Do not name the image anywhere else; lint fails if you do.
- Use the **index** digest, not a per-platform one. It is what makes the same line work for amd64 and arm64.
- The interpreter's patch version may change with the bump, and the locks are compiled inside this image.
  Run `bash infra/lock-deps.sh` and check `git diff` shows the `*.lock` files unchanged. If they changed,
  review that as its own change.

Then verify (step 4), and see step 5 for whether the zlib-style workaround block can come out.

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

The patch block is temporary. When the pinned base ships the fixed version (that is, after a bump in step
3a), the `dpkg --compare-versions` assertion passes without the `apt-get install` doing anything. To check,
comment out the block's install and build with `--no-cache`: if the assertion still passes, delete the whole
block and its comment. Because the base is pinned, this can only happen when someone bumps the pin; it will not
happen by itself. Do it in the same change as the bump. Leaving the block in costs one apt layer.

## 6. Record it

Add a `docs/BUILD-LOG.md` line: date, package, CVEs, which of 3a or 3b applied. If 3b, also say when you
expect to remove it.

## Not Debian packages

Grype also reports `pip` and the Python interpreter (`python 3.12.x`, type `binary`). Those are in the
base image, not in our locks, and today they appear only at MEDIUM or lower. If one reaches HIGH:

- `pip` in the image: upgrade it in the Dockerfile, with the same comment and verification as 3b.
- Python itself: a newer published `python:3.12-slim` is the fix (bump the pin, step 3a). Do not hand-patch
  the interpreter.
- A package from `requirements.lock`: that is the `audit` step's job, not this one; fix it with
  `infra/lock-deps.sh`.

## What this scan does not cover

**Only the `backend/` image is scanned.** The `security` job builds `backend/Dockerfile` and nothing else. The
slice 0 pin closed the floating tag on that image, the CI tooling container, `lock-deps.sh` and the smokes. It
did not touch the **backup image** (`infra/backup/`, M2 slice 2), which is built from the Timescale image with a
handful of Alpine packages (`age`, `openssh-client`, `curl`, `tzdata`, `bash`) installed **without versions**, so
they float at every build, and nothing scans that image or the OS packages of its base. This is open, and it is
a hole next to the one slice 0 closed.

What closing it takes: build the second image in the same job and scan it with the same settings (fixable HIGH
and above only, which also keeps unfixable findings in the Timescale base from keeping the build red); then pin
whatever that turns up, by apk version or by base digest, with a bump procedure like step 3a. Nothing here
covers the backup image until that is done, and a red scan on it will not appear on its own.

The backup image has not been built for arm64 either (`infra/ci-checks.sh arm64` covers `backend/` only).
