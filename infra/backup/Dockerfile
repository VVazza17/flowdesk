# The backup container (M2 slice 2; off-box copy to Backblaze B2 since 2026-11-25): pg_dump, age, rclone, curl.
#
# DECISION: built FROM the Postgres image the stack runs, so pg_dump is the server's own major version and
# the archives it writes restore on that image (the version trap in docs/BACKUP.md). The tag is defined
# once, in docker-compose.yml, and arrives as a build argument; the default below only serves a plain
# `docker build` by hand and is not a second definition to keep in step.
ARG POSTGRES_IMAGE=timescale/timescaledb:2.31.0-pg16
FROM ${POSTGRES_IMAGE}

# DECISION: the apk packages float at build time (the base is an exact tag; Alpine's repository is not). None of
# them is a service: they are command-line tools that run once a day. CI's `security` job scans this image since
# 2026-11-25 (docs/RUNBOOK-IMAGE-CVE.md), which is the tripwire for them. jq reads b2_authorize_account's JSON.
RUN apk add --no-cache age curl jq tzdata bash \
 && mkdir -p /backups /opt/backup \
 && chown postgres:postgres /backups

# rclone, the uploader (MIT). PINNED, the one definition (backend/tests/test_backup_image.py checks it is the only
# one): the release zip at an exact version, checked against SHA256 values copied from that release's SHA256SUMS
# after its PGP signature was verified against rclone's published key, fingerprint
# FBF737ECE9F8AB18604BD2AC93935E02FF3B54FA (MEASURED 2026-11-25: "Good signature"). Not Alpine's package: rclone
# is only in Alpine's edge branch. Binary 85.8 MB (amd64, MEASURED 2026-11-25). Bump procedure:
# docs/RUNBOOK-IMAGE-CVE.md, "rclone".
# The build arguments are PIN_*, not RCLONE_*: rclone reads every RCLONE_* environment variable as a flag (MEASURED
# 2026-11-25: RCLONE_VERSION=1.76.0 became --version and failed the build).
ARG TARGETARCH=amd64
ARG PIN_RCLONE_VERSION=1.76.0
ARG PIN_RCLONE_SHA256_AMD64=b00f00b3266c3764413afe599d3ec316f6f43b3e0ea8a0bc1d186d2133e46df7
ARG PIN_RCLONE_SHA256_ARM64=ee169016675e23a69d77240565133ff7dbc9b33d16fa8f7053d4af39f1eaeb78
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) sum=$PIN_RCLONE_SHA256_AMD64 ;; \
      arm64) sum=$PIN_RCLONE_SHA256_ARM64 ;; \
      *) echo "no rclone pin for $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    zip="rclone-v${PIN_RCLONE_VERSION}-linux-${TARGETARCH}.zip"; \
    curl -fsSL -o "/tmp/$zip" "https://downloads.rclone.org/v${PIN_RCLONE_VERSION}/$zip"; \
    echo "$sum  /tmp/$zip" | sha256sum -c -; \
    unzip -q -j "/tmp/$zip" "rclone-v${PIN_RCLONE_VERSION}-linux-${TARGETARCH}/rclone" -d /usr/local/bin; \
    rm -f "/tmp/$zip"; \
    chmod 755 /usr/local/bin/rclone; \
    rclone version | head -n 1 | grep -qx "rclone v${PIN_RCLONE_VERSION}"

COPY lib.sh run-backup.sh run-copy.sh verify-destination.sh fetch-b2.sh backup-loop.sh entrypoint.sh /opt/backup/
RUN chmod 755 /opt/backup/*.sh

ENV HOME=/tmp
ENTRYPOINT ["/opt/backup/entrypoint.sh"]
CMD ["/opt/backup/backup-loop.sh"]
