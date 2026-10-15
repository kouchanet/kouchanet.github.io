# One-liners and scripts

## When to write a script instead of a one-liner

Write a file when any of these is true: more than one loop or condition; cleanup is needed on
failure (`trap`); the same sequence will run several times; the command line passes ~200
characters or needs nested quoting; the user may want to rerun it later. Otherwise a one-liner
with `&&` is clearer and easier to review. Put scripts in the scratchpad/temp dir unless the user
asked for a file in the repo.

## Skeleton

```bash
#!/usr/bin/env bash
# what this does, in one line
set -euo pipefail            # stop on error, unset variable, failed pipe element
IFS=$'\n\t'                  # safer word splitting

usage() { echo "usage: $0 <input> [out-dir]" >&2; exit 2; }
[ $# -ge 1 ] || usage
input=$1
out=${2:-./out}

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT    # cleanup on any exit

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }

main() {
  mkdir -p "$out"
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    process "$line" > "$tmp/$(basename "$line").out"
  done < "$input"
  cp -a "$tmp"/. "$out"/
  log "wrote $(ls "$out" | wc -l | tr -d ' ') files to $out"
}

process() { local f=$1; sed -n '1,10p' "$f"; }

main "$@"
```

Run `shellcheck script.sh` when it is installed; it catches most quoting and portability mistakes
(`bash -n script.sh` only checks syntax).

## Strict mode, with its sharp edges

- `set -e` does not fire inside `if`/`&&`/`||` conditions or command substitutions in some shells; test the important exit codes explicitly.
- `set -u` makes `$1` with no args an error: use `${1:-}` and `$#` checks.
- `set -o pipefail` makes `cmd | head` fail with 141 (SIGPIPE) when `cmd` writes more than `head` reads; add `|| true` to that specific pipeline, or use `head` only on bounded output.
- `grep` returns 1 on no match: `if grep -q pat f; then …` or `count=$(grep -c pat f || true)`.
- Temporarily relax: `set +e; cmd; rc=$?; set -e`.

## Quoting and expansion

- `"$var"`, `"$@"`, `"${arr[@]}"`, `"$(cmd)"` always. Unquoted is a bug until proven otherwise.
- `[[ … ]]` in bash (no word splitting, `=~`, `&&` inside); `[ … ]` in `sh` with every variable quoted.
- `$(( … ))` for arithmetic; `((i++))` can return 1 under `set -e` when `i` was 0: use `i=$((i+1))`.
- Arrays for lists: `files=(a b "c d"); CMD "${files[@]}"`; never build a command in a string and `eval` it.
- `printf '%s\n' "$x"` instead of `echo "$x"` (no `-n`/`-e` ambiguity, safe for `-`-prefixed strings).
- `${var:?}` guards any variable used in a path that gets deleted or overwritten.
- `--` before user-supplied paths: `rm -- "$f"`, `grep -- "$pat" "$f"`.
- Globs: `shopt -s nullglob` so `*.md` with no match expands to nothing; `shopt -s globstar` for `**`.

## Loops over files and lines

```bash
for f in src/*.ts; do …; done                            # glob (with nullglob)
find . -name '*.log' -print0 | while IFS= read -r -d '' f; do …; done   # NUL-safe; runs in a subshell
while IFS= read -r -d '' f; do …; done < <(find . -name '*.log' -print0)   # same, variables survive
while IFS= read -r line || [ -n "$line" ]; do …; done < file   # keeps the last line without newline
mapfile -t lines < file                                   # bash 4: read lines into an array
```

## Errors, logging, exit codes

- Errors to stderr: `echo "error: …" >&2`; data to stdout. Exit codes: 0 ok, 1 generic, 2 usage, 124 timeout, 126 not executable, 127 not found, 128+N signal.
- `trap 'echo "failed at line $LINENO" >&2' ERR` points at the failing line.
- Retries with backoff:

  ```bash
  for i in 1 2 3 4; do CMD && break; sleep $((2 ** i)); done
  ```

- Parallel work: `xargs -P 4`, `parallel` (GNU, when installed), or background jobs with `wait`:

  ```bash
  for f in *.png; do optipng -q "$f" & done; wait
  ```

## Portability

- `#!/usr/bin/env bash` for bash features (arrays, `[[`, `${var//}`, process substitution); `#!/bin/sh` only when every construct is POSIX (`[ ]`, no arrays, `.` not `source`).
- Avoid GNU-only flags in scripts that run on macOS/alpine (see platforms.md): `sed -i.bak`, `-E`, no `grep -P`, no `date -d`, `mktemp -d /tmp/x.XXXXXX`.
- `command -v tool >/dev/null || { echo "need tool" >&2; exit 1; }` at the top for every non-standard dependency.
- `cd "$(dirname "$0")"` ties the script to its own directory; prefer explicit paths relative to a `root=$(git rev-parse --show-toplevel)`.

## Heredocs for files and inline programs

```bash
cat > "$out/config.json" <<'EOF'        # quoted: no expansion, safe for $ and backticks
{ "retries": 3 }
EOF
python3 - "$arg" <<'PY'                 # inline program with arguments
import sys; print(sys.argv[1])
PY
sed -n '1,5p' <<<"$text"                # here-string
```

Prefer the harness's Write tool for files that matter (it avoids quoting puzzles); heredocs are for
scripts and throwaway inputs. Pick a delimiter that cannot appear in the body (a heredoc whose body
contains a line `EOF` needs another delimiter).

## Make it readable

- Long flags in scripts (`--max-count=5`), short ones on the command line.
- One action per line, `\` continuations aligned, a comment for the non-obvious flag.
- Functions named for intent (`build_image`, `wait_for_port`), `local` for every variable inside them.
- Idempotent: running it twice should be safe (`mkdir -p`, `rm -f`, `ln -sfn`, checks before creates).
