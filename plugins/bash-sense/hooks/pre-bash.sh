#!/usr/bin/env bash
# bash-sense PreToolUse hook (matcher: Bash).
# Reads the hook payload on stdin and prints a decision as JSON on stdout.
# Fails open: when no Python 3 is available the command runs untouched.
set -u
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
engine="$root/scripts/bash_sense.py"
if [ "${BASH_SENSE:-}" = "off" ] || [ "${BASH_SENSE:-}" = "0" ]; then
  exit 0
fi
if command -v python3 >/dev/null 2>&1; then
  exec python3 -I "$engine" --hook
fi
if command -v python >/dev/null 2>&1 && python -c 'import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)' >/dev/null 2>&1; then
  exec python -I "$engine" --hook
fi
exit 0
