#!/usr/bin/env bash
# Apply the backup retention rule to a directory of encrypted backups. The container uses the same rule on
# /backups; run this by hand on the receiving machine, because the restricted SSH key can write there but
# cannot delete, so nothing else will ever prune it. Runbook: docs/BACKUP.md
#
#   bash prune.sh DIR [--keep-daily N] [--keep-weekly N] [--dry-run]     (defaults: 14 and 8)
#
# Only files named <db>_<UTC stamp>.tar.age are considered. Anything else in DIR is left alone.
set -euo pipefail

dir="" kd=14 kw=8 dry=""
while [ $# -gt 0 ]; do
  case "$1" in
    --keep-daily) kd="${2:?--keep-daily needs a number}"; shift 2 ;;
    --keep-weekly) kw="${2:?--keep-weekly needs a number}"; shift 2 ;;
    --dry-run) dry="--dry-run"; shift ;;
    -*) echo "usage: $0 DIR [--keep-daily N] [--keep-weekly N] [--dry-run]" >&2; exit 2 ;;
    *) [ -z "$dir" ] || { echo "usage: $0 DIR [...]" >&2; exit 2; }; dir="$1"; shift ;;
  esac
done
[ -n "$dir" ] && [ -d "$dir" ] || { echo "usage: $0 DIR [--keep-daily N] [--keep-weekly N] [--dry-run]" >&2; exit 2; }
[[ $kd =~ ^[0-9]+$ && $kw =~ ^[0-9]+$ ]] || { echo "FAIL: --keep-daily and --keep-weekly must be whole numbers" >&2; exit 2; }

# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"
prune_dir "$dir" "$kd" "$kw" $dry
