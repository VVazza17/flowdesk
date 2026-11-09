#!/bin/sh
# Runs as root for the few seconds it takes to put the secrets where an unprivileged process can use them,
# then drops to the postgres user and runs the command. Nothing else here runs as root.
#
# Why copy at all: the SSH key is bind-mounted from the host. Docker Desktop shows such files as
# world-readable, which ssh refuses ("UNPROTECTED PRIVATE KEY FILE"); on a Linux host the file belongs to
# the host user and is unreadable to the container user. A private copy with mode 600, owned by the
# user that runs ssh, is right on both.
set -eu

install_secret() { # SOURCE DESTINATION: only if the mount holds something (the unset default is empty)
  if [ -s "$1" ]; then
    install -m 600 -o postgres -g postgres "$1" "$2"
  fi
}

mkdir -p /run/backup
chown postgres:postgres /run/backup
chmod 700 /run/backup
install_secret /mnt/ssh_key /run/backup/id_key
install_secret /mnt/known_hosts /run/backup/known_hosts

chown postgres:postgres /backups
exec su-exec postgres "$@"
