# The backup container (M2 slice 2): pg_dump, age, rsync and ssh in one image.
#
# DECISION: built FROM the Postgres image the stack runs, so pg_dump is the server's own major version and
# the archives it writes restore on that image (the version trap in docs/BACKUP.md). The tag is defined
# once, in docker-compose.yml, and arrives as a build argument; the default below only serves a plain
# `docker build` by hand and is not a second definition to keep in step.
ARG POSTGRES_IMAGE=timescale/timescaledb:2.31.0-pg16
FROM ${POSTGRES_IMAGE}

# DECISION: the apk packages float at build time (the base is an exact tag; Alpine's repository is not).
# None of them is a service: they are command-line tools that run once a day. This image is not covered by
# the CI vulnerability scan yet, which builds backend/ only (noted in docs/BACKUP.md).
RUN apk add --no-cache age rsync openssh-client curl tzdata bash \
 && mkdir -p /backups /opt/backup \
 && chown postgres:postgres /backups

COPY lib.sh run-backup.sh run-copy.sh backup-loop.sh prune.sh entrypoint.sh /opt/backup/
RUN chmod 755 /opt/backup/*.sh

# ssh looks in $HOME; nothing there should be writable by anyone but the process itself.
ENV HOME=/tmp
ENTRYPOINT ["/opt/backup/entrypoint.sh"]
CMD ["/opt/backup/backup-loop.sh"]
