#!/usr/bin/env bash
# Commit and push ONLY if the gates pass (CLAUDE.md rule 11).
#   bash infra/commit-gated.sh <message-file> <path> [<path> ...]
#
# Why a script and not a command line: `set -e` does NOT take effect in the Claude Code Bash tool's own
# shell (MEASURED 2026-11-18: `set -euo pipefail; false; echo x` prints x there), so a gate that failed
# let the next line, a commit, run. Twice in one day. In a script run by `bash`, `set -e` works, and here
# each gate is also checked explicitly. Lint, then the full test gate; any non-zero exit stops before
# `git add`. The test gate includes the live-data checks on a development stack.
set -euo pipefail
cd "$(dirname "$0")/.."

[ $# -ge 2 ] || { echo "usage: bash infra/commit-gated.sh <message-file> <path>..." >&2; exit 2; }
message="$1"; shift
[ -s "$message" ] || { echo "FAIL: empty or missing message file $message" >&2; exit 2; }
log_dir="$(mktemp -d)"

run_gate() {
  local name="$1"
  if bash infra/ci-checks.sh "$name" > "$log_dir/$name.txt" 2>&1; then
    echo "gate $name: passed ($(grep -E 'passed|Success' "$log_dir/$name.txt" | tail -1))"
  else
    echo "gate $name: FAILED (exit $?). Nothing committed. Log: $log_dir/$name.txt" >&2
    grep -E '^FAILED|^ERROR|-->|error:' "$log_dir/$name.txt" | head -20 >&2 || true
    exit 1
  fi
}

run_gate lint
run_gate test
git add -- "$@"
git commit -q -F "$message"
git push -q
echo "committed and pushed: $(git log -1 --format='%h %s')"
