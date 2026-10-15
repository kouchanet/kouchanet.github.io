#!/usr/bin/env bash
# bash-sense SessionStart hook: prints the environment card into the session context.
# Everything printed on stdout is added to Claude's context (plain text).
set -u
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
engine="$root/scripts/bash_sense.py"
if [ "${BASH_SENSE:-}" = "off" ] || [ "${BASH_SENSE:-}" = "0" ]; then
  exit 0
fi
cwd="$(pwd)"
if command -v python3 >/dev/null 2>&1; then
  python3 -I "$engine" --envcard --refresh 2>/dev/null && exit 0
fi
# Minimal fallback without Python: which modern tools exist, and the habits.
echo "## bash-sense · environment card (minimal: python3 not found, the command advisor is inactive)"
have=""; missing=""
for t in rg fd fdfind jq yq gh bat delta fzf tree sd eza uv pnpm bun just docker; do
  if command -v "$t" >/dev/null 2>&1; then have="$have $t"; else missing="$missing $t"; fi
done
echo "- platform: $(uname -s) $(uname -m) · user: $(id -un 2>/dev/null || echo '?') · cwd: $cwd"
echo "- present:${have:- none} · missing:${missing:- none}"
if sed --version >/dev/null 2>&1; then echo "- GNU sed/coreutils (sed -i 's/a/b/' FILE)"; else echo "- BSD sed/coreutils (sed -i '' 's/a/b/' FILE; no grep -P, date -d, xargs -d)"; fi
cat <<'TXT'
- habits that read as good taste: rg over grep -r; jq for JSON; bound every output (head, -n 20, --stat); use the project's scripts and the lockfile's package manager; nothing interactive (no editors, pagers, REPLs, tail -f, watch; servers go to the background); preview before destroying and ask before reset --hard / force push / rm -rf outside tmp; -C or subshells instead of cd; quote "$vars"; keep secrets out of the transcript.
- `/bash-sense:review CMD` reviews a command; `/bash-sense:env` reprints this card.
TXT
exit 0
