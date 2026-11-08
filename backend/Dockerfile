# DECISION: slim, not alpine. Alpine's musl libc breaks many prebuilt wheels (asyncpg,
# later numpy/pandas) and gains little. python:3.12-slim publishes amd64 and arm64.
#
# DECISION: pinned by digest (2026-11-08, M2 slice 0). A floating tag changes under a build that did not
# touch it, and M2 adds numpy/pandas wheels, where a base change in the same commit as a dependency change
# is hard to tell apart. This is the ONE definition: the CI tooling container, lock-deps.sh and the
# smokes read this line (infra/base-image.sh). The digest is the multi-arch index, so it covers amd64 and
# arm64. Cost: the base no longer picks up fixes by itself. The daily image scan is the tripwire, and
# docs/RUNBOOK-IMAGE-CVE.md says how to bump. Resolved from tag 3.12-slim on 2026-11-08 (Debian 13).
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

# Base-image CVE patch, applied on top because the published python:3.12-slim lagged Debian's security
# archive: two HIGH findings in zlib1g, fixed in 1:1.3.dfsg+really1.3.1-1+deb13u1 (trixie-security).
# This upgrades that one package, not the whole image. The dpkg check fails the build if the fixed
# version did not land, and passes once the base image ships it or anything newer. The base is pinned, so
# that only happens when someone bumps the digest above; delete this block in the same change
# (docs/RUNBOOK-IMAGE-CVE.md has the procedure and the removal check).
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade zlib1g \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' zlib1g)" ge "1:1.3.dfsg+really1.3.1-1+deb13u1" \
    && rm -rf /var/lib/apt/lists/*

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
USER flowdesk

EXPOSE 8000

# slim has no curl; the stdlib is enough for a liveness probe.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2).status == 200 else 1)"]

# --no-access-log: CorrelationIdMiddleware emits the one per-request line (slice 5).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
