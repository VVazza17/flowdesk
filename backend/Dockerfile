# DECISION: slim, not alpine. Alpine's musl libc breaks many prebuilt wheels (asyncpg,
# later numpy/pandas) and gains little. python:3.12-slim publishes amd64 and arm64.
#
# DECISION: pinned by digest (2026-11-08, M2 slice 0). A floating tag changes under a build that did not
# touch it, and M2 adds numpy/pandas wheels, where a base change in the same commit as a dependency change
# is hard to tell apart. This is the ONE definition: the CI tooling container, lock-deps.sh and the
# smokes read this line (infra/base-image.sh). The digest is the multi-arch index, so it covers amd64 and
# arm64. Cost: the base no longer picks up fixes by itself. The daily image scan is the tripwire, and
# docs/RUNBOOK-IMAGE-CVE.md says how to bump. Resolved from tag 3.12-slim on 2026-11-24 (Debian 13;
# libc6/libc-bin 2.41-12+deb13u5 for CVE-2026-19499 and CVE-2026-5435; zlib1g already +deb13u1, so the
# zlib1g patch block of 2026-11-08 came out in this bump; Python 3.12.16, unchanged). First pin 2026-11-08.
FROM python:3.12-slim@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Dependencies first: this layer is cached until the lock changes. --require-hashes means the image
# gets exactly the set CI audited, or the build fails. Edit requirements.txt, then infra/lock-deps.sh.
COPY requirements.lock .
RUN pip install --require-hashes -r requirements.lock

COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic

RUN useradd --system --uid 10001 --no-create-home flowdesk

# Beat's schedule file lives here, on a named volume (IDEA-043). The directory must exist and be owned by the
# app user IN THE IMAGE: Docker copies a path's ownership into a new, empty named volume on first mount,
# whereas a volume over a path the image does not have is root-owned and Beat (uid 10001) dies with
# Errno 13 (seen 2026-11-15). A compose-only fix does not work. infra/smoke-m2-beat-schedule.sh proves it.
RUN mkdir /var/lib/beat && chown flowdesk /var/lib/beat

# yfinance's timezone, cookie and ISIN caches (SQLite files). By default yfinance puts them under
# the user's home, which this user does not have, so it ran WITHOUT them (2026-11-18: `Permission
# denied: '/home/flowdesk'`) and made extra requests per batch: not the conditions step 1(b)
# measured its throttling margin under. app.ingest.yfinance_client points yfinance here
# (settings.yfinance_cache_dir). Owned by the app user in the image, as /var/lib/beat is.
RUN mkdir /var/cache/yfinance && chown flowdesk /var/cache/yfinance

USER flowdesk

EXPOSE 8000

# slim has no curl; the stdlib is enough for a liveness probe.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2).status == 200 else 1)"]

# --no-access-log: CorrelationIdMiddleware emits the one per-request line (slice 5).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
