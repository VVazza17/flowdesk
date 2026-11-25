#!/bin/sh
# Runs as root only to fix the ownership of the backups volume, then drops to the postgres user and runs the
# command. Nothing else here runs as root. The B2 key arrives in the environment (compose, from .env); there are no
# key files to install since the SFTP copy was removed (2026-11-25).
set -eu
chown postgres:postgres /backups
exec su-exec postgres "$@"
