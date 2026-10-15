---
name: review
description: Choose and shape Bash commands the way a seasoned engineer would - the right tool (rg/fd/jq instead of grep -r/find/cat|grep), bounded output, non-interactive forms, the project's own scripts and package manager, GNU vs BSD differences, a safety ladder for destructive git/rm/docker operations, and a repair playbook when a command fails. Use this whenever you are about to run shell commands for a multi-step task, before installing packages, starting servers, running tests or builds, or touching git history; when a command failed, hung, or printed too much; when writing a shell one-liner or script; and when a user asks why you chose a command or wants one reviewed (`/bash-sense:review <command>`).
argument-hint: "[command to review]"
---

# bash-sense — the command a seasoned engineer would type

A command can be *correct* and still feel wrong to the person watching: it crawls `node_modules`,
dumps 3,000 lines to find one value, opens a pager, installs with the wrong package manager, or
deletes something that was not backed up. This skill is about the difference between "it worked"
and "yes, that is exactly the command I would have typed".

The plugin also installs two hooks. At session start you get an **environment card** (installed
tools, GNU/BSD flavors, the project's package manager and scripts). Before every Bash call a
**PreToolUse advisor** stops commands that would hang or misuse a tool, asks before destructive ones,
and annotates the rest. When it blocks something, re-issue the better form it suggests.

## How to use this skill

- **Invoked with an argument** (`/bash-sense:review git log`): review that command. Follow *Reviewing a command* at the end.
- **Invoked without one, or loaded automatically**: apply the habits below to the commands you are about to run; open a reference file when its topic comes up.
- Prefer the harness's dedicated tools (Read, Edit, Write, Grep, Glob) for reading, editing and searching files when its instructions say so. Bash is for running things, composing, and what those tools cannot do. The habits below apply to the Bash you do run.

## The one question

Before typing a command, ask: *what would the engineer who maintains this repository type here, with
this toolchain, on this machine?* They would not reach for the generic Unix answer; they would use
the tool that is installed, the script the project defines, the flag that keeps the output small,
and the form that cannot surprise anyone. Every habit below is a concrete form of that question.

## Seven habits

### 1. Reach for the tool that fits

| Reflex | What they type | Why it reads as taste |
|---|---|---|
| `grep -rn "x" .` | `rg -n "x"` (`-t py`, `-g '*.ts'`, `-l`, `-F`) | skips `.gitignore`d paths, binaries, `.git`; 10× faster; no `node_modules` noise. Fallback in a repo: `git grep -n "x"` |
| `find . -name "*.py"` | `fd -e py` (`-H` hidden, `-I` ignored, `-t f`, `-x CMD`) | same reasons; `find` needs `-not -path '*/node_modules/*'` to be quiet |
| `cat f.json \| grep key` | `jq '.key' f.json` (`-r` raw, `keys`, `.[]`, `-c`) | structural, exact, no partial lines; `yq` for YAML |
| `ps aux \| grep node` | `pgrep -af node` | no self-match, prints pid + full command |
| `kill $(ps … \| awk …)` | `pkill -f 'node server.js'` after `pgrep -af` | match the full command line, never a bare name |
| `which x` | `command -v x` | POSIX, sees functions/aliases, exit status is the answer |
| `` `cmd` `` | `$(cmd)` | nests, no backslash surprises |
| `ls \| grep foo` | `ls -d *foo*` or `fd -d 1 foo` | globs are exact; `ls` output is for humans |
| `cat big.log` | `wc -l big.log`, then `head -n 50`, `sed -n '120,160p'`, `rg -n ERROR` | summarize, then zoom |
| `python -c "import json…"` | `jq` (when installed) | one tool, no quoting puzzle |
| `du -sh *` | `du -sh * \| sort -h \| tail -n 20` | sorted, bounded |
| `curl URL` | `curl -fsSL URL` (`-o FILE`, `-w '%{http_code}'`) | no progress bar, fails on HTTP errors, follows redirects |

The quick table in `references/intent-to-command.md` has ~120 more intents (archives, network,
disk, processes, dates, text, git, docker, package managers) with fallbacks when a tool is missing.

### 2. Bound every output

The reader is you, with a limited context window, and the person watching the transcript. Make
each command answer one question with as few lines as possible:

- Limits by default: `head -n 50`, `git log --oneline -n 20`, `--max-count`, `docker logs --tail 100`, `tail -n 40`.
- Summaries before detail: `git diff --stat` → `git diff -- path`; `rg -l` → `rg -n`; `jq 'keys'` → `jq .key`; `ls | wc -l` → `ls`.
- Builds and tests: `2>&1 | tail -n 40` keeps the failure (it is at the end); quiet flags (`pytest -q`, `cargo test -q`, `mvn -q -B`, `gradle -q --console=plain`, `pip -q`, `npm --silent`).
- Machine-readable when parsing: `git status --porcelain`, `--json`, `-o json`, `--format`, `NO_COLOR=1`, `--color=never`.
- Never `cat` lockfiles, minified bundles, binaries, or `.env`. Ask the file a question instead (`jq`, `grep -n`, `file`, `tar -tzf`, `unzip -l`).
- Keep `tee` for things worth keeping: `make 2>&1 | tee /tmp/build.log | tail -n 30`.

### 3. Nothing that waits for a human

There is no terminal behind these commands. Anything interactive hangs until the timeout or fails
with "not a TTY", and both read as carelessness.

- Editors and pagers: never `vim`/`nano`/`less`/`man`; edit with the Edit tool, `sed -i`, or a quoted heredoc. `git` needs no `--no-pager` when stdout is a pipe, but `git commit` needs `-m` (or `-F -`), `--amend` needs `--no-edit`, and `rebase -i`/`add -p` are off the table.
- Monitors and followers: `tail -n 100` not `tail -f` (or `timeout 15 tail -f`, `tail --pid=$PID -f`); `top -bn1 | head -n 20` not `top`; a bounded loop not `watch`; `ping -c 3`.
- REPLs: `python3 -c`, `node -e`, `psql -c`, `sqlite3 DB 'SQL'`, or a heredoc on stdin.
- Prompts: `-y`/`--yes` (`apt-get -y`, `npm init -y`), `DEBIAN_FRONTEND=noninteractive`, `GIT_TERMINAL_PROMPT=0`, `CI=1`, `ssh -o BatchMode=yes HOST 'CMD'`, `gh pr create --fill`/`--title`.
- Containers: `docker exec CONTAINER CMD` (no `-it`), `docker compose exec -T`, `docker compose up -d` then `logs --tail 50`.
- Servers, watchers, dev modes (`npm run dev`, `vite`, `next dev`, `flask run`, `tsc -w`, `jest --watch`, `nodemon`): run the one-shot form (`vitest run`, `tsc --noEmit`), or start it in the background with a log and poll it:

  ```bash
  nohup npm run dev > /tmp/dev.log 2>&1 &
  for i in $(seq 1 20); do curl -sf -o /dev/null localhost:3000 && break; sleep 1; done
  tail -n 20 /tmp/dev.log
  ```

  If the harness offers a run-in-background option for Bash, use that instead of `nohup`.

Details, including every environment variable that silences a prompt: `references/non-interactive.md`.

### 4. Follow the project, not the generic manual

The repository already encodes its maintainers' taste. Read it before running anything:

- **Package manager** from the lockfile: `pnpm-lock.yaml` → `pnpm`, `yarn.lock` → `yarn`, `bun.lock(b)` → `bun`, `package-lock.json` → `npm`; `packageManager` in `package.json` wins. `uv.lock` → `uv add`/`uv run`, `poetry.lock` → `poetry add`/`poetry run`, `Pipfile` → `pipenv`, else a venv (`python3 -m venv .venv && .venv/bin/pip install …`, then call `.venv/bin/python` directly; activation does not survive between calls). Mixing managers writes a second lockfile and a broken `node_modules`.
- **Entry points** before raw tools: `npm run lint` not `eslint .`; `make test`, `just check`, `task build`, `./gradlew test`, `./mvnw -q test`, `bundle exec rspec`, `pre-commit run --all-files`. The script carries the flags and config the maintainers chose. Call the tool directly only for a narrower run (`eslint src/one.ts`).
- **Versions** from `.nvmrc`, `.node-version`, `.tool-versions`, `.python-version`, `rust-toolchain.toml`, `packageManager` (corepack).
- **Install exactly**: `npm ci` / `pnpm install --frozen-lockfile` / `yarn install --immutable` / `uv sync` / `cargo build --locked` when the lockfile must be respected; `install`/`add` only when changing dependencies.
- **Global installs change the user's machine.** One-off: `npx -y PKG`, `pnpm dlx`, `uvx`/`pipx run`, `cargo run`, `go run pkg@ver`. Otherwise add a dev dependency, or ask.

Per-ecosystem commands and detection rules: `references/package-managers.md`.

### 5. Preview, prefer reversible, ask before the point of no return

A seasoned engineer's destructive commands are boring because they already know what will happen.

| Ladder | Examples |
|---|---|
| **Preview** | `git clean -nd`, `rsync -n`, `ls -la PATH` before `rm`, `find … -print` before `-delete`, `sed -n 's/a/b/p'` before `sed -i`, `terraform plan`, `kubectl --dry-run=client`, `npm publish --dry-run`, `git push --dry-run` |
| **Reversible** | `git stash push -u -m 'why'` instead of `checkout -- .`/`reset --hard`; `git revert` instead of rewriting; `--force-with-lease` instead of `--force`; `mv PATH /tmp/trash-$(date +%s)/` instead of `rm -rf`; `git branch backup-$(date +%s)` before a rebase; `cp -a f f.bak` before a risky edit |
| **Ask first** | `git reset --hard`, `git checkout -- .`, `git clean -f` (especially `-x`: it deletes `.env`), force pushes, `git branch -D`, `rm -rf` outside tmp/build artifacts, `rm -rf .git`, `pkill node`/`killall`, `kill -9` on processes you did not start, `sudo`, global installs, `git config --global`, writes to `~/.bashrc`/`/etc`, `docker system prune`/`down -v`, `kubectl delete`, `terraform apply`, `DROP`/`TRUNCATE`/`DELETE` without `WHERE`, `db:reset`, `npm publish`, `gh pr merge`, `crontab -r`, `systemctl stop` |
| **Never** | `rm -rf /`, `~`, `*`, `.`, `$VAR/` unguarded (`"${VAR:?}"`), `chmod -R 777`, `chown -R … /`, `curl … \| sh` unseen, `dd of=/dev/…`, `mkfs`, `kill -9 -1`, `--no-preserve-root` |

Guard every variable that feeds a destructive path: `rm -rf "${BUILD_DIR:?}/out"`. Scope recursive
operations to the directory you mean. Delete build artifacts freely (`node_modules`, `dist`,
`target`, `__pycache__`, `.venv`); treat everything else as somebody's work.

Full catalogue with the safer alternatives: `references/safety.md`.

### 6. Compose so nothing leaks between calls

- **The working directory persists between calls** in most harnesses. A bare `cd dir && cmd` silently moves every later command. Use `git -C dir`, `make -C dir`, `npm --prefix dir run`, `pnpm -C dir`, `cargo --manifest-path dir/Cargo.toml`, `tar -C dir`, absolute paths, or a subshell `(cd dir && cmd)`.
- **Environment does not persist** (`export`, `source venv/bin/activate`, `nvm use` die with the call). Prefix instead: `FOO=1 cmd`, `.venv/bin/python`, `npx`, `uv run`.
- `--` before paths that might start with `-`; quote `"$var"`, `"$@"`, paths with spaces; `$(…)` not backticks; `[[ … ]]` in bash; `printf` not `echo -e`.
- `&&` to stop at the first failure; `;` only for independent reads (`git status --short; git branch --show-current`); `||` for a fallback, never `|| true` to hide a failure you need to see.
- `2>&1` when diagnosing (errors go to stderr); `2>/dev/null` only when the noise is known and irrelevant.
- Files with spaces: `find … -print0 | xargs -0`, `find … -exec CMD {} +`, `fd -x`, `while IFS= read -r f`; never `for f in $(ls)`.
- One clear command beats a clever one-liner, and one command beats five trivial calls: batch independent reads, keep writes separate.
- Temp files: `mktemp -d` (`t=$(mktemp -d); trap 'rm -rf "$t"' EXIT`), not hard-coded names in `/tmp`.
- Secrets stay out of the transcript: no `cat .env` (use `cut -d= -f1 .env`), no bare `env`, no `echo $TOKEN`, no literal tokens on the command line (`-H "Authorization: Bearer $TOKEN"`).

Writing scripts (strict mode, traps, arrays, portability, ShellCheck): `references/scripting.md`.

### 7. Match the platform

The same command means different things on GNU/Linux, macOS (BSD userland), busybox containers and
Windows shells. Check the environment card, then write for the flavor you have:

| | GNU/Linux | macOS (BSD) |
|---|---|---|
| in-place sed | `sed -i 's/a/b/' f` | `sed -i '' 's/a/b/' f` (portable: `sed -i.bak … && rm f.bak`) |
| PCRE grep | `grep -P` | no `-P`: `rg -P` or `grep -E` |
| dates | `date -d '1 day ago' +%F`, `date -d @EPOCH` | `date -v-1d +%F`, `date -r EPOCH`, `date -j -f FMT STR` |
| file size/mtime | `stat -c %s f`, `stat -c %Y f` | `stat -f %z f`, `stat -f %m f` (portable: `wc -c < f`) |
| xargs delimiter | `xargs -d '\n'` | `tr '\n' '\0' \| xargs -0` |
| missing on macOS | — | `timeout`→`gtimeout`, `tac`→`tail -r`, `nproc`→`sysctl -n hw.ncpu`, `sha256sum`→`shasum -a 256`, `ss`→`lsof -nP -iTCP -sTCP:LISTEN`, `readlink -f`→`realpath`/`pwd -P` |
| Debian/Ubuntu names | `fdfind`, `batcat` | — |
| busybox (alpine) | fewer flags: no `find -printf`, no `grep -P`, `sed -i` without suffix works, `wget` not `curl` | — |

`command -v TOOL` before relying on a tool you have not seen in the card. Full tables: `references/platforms.md`.

## Intent → command, the thirty most common

| Intent | Command |
|---|---|
| what is in this repo | `git ls-files \| head -n 50`, `fd -t d -d 2`, `tree -L 2 -I 'node_modules\|.git'` |
| find text | `rg -n "pat"` · `rg -n -t py "pat"` · `rg -l "pat"` · `rg -n -F 'a.b(c)'` · `rg -n -w word` · `rg -n -A3 -B1 pat` |
| find files | `fd pattern` · `fd -e ts` · `fd -H '^\.env'` · `fd --changed-within 1d` · `fd -t f -S +10m` |
| read part of a file | `sed -n '40,80p' f` · `head -n 60 f` · `tail -n 40 f` · `rg -n "def " f` |
| JSON / YAML | `jq '.a.b' f` · `jq -r '.[] \| .name' f` · `jq 'keys' f` · `jq -c '.' f` · `yq '.spec.ports' f` |
| count things | `rg -c pat` · `wc -l f` · `sort \| uniq -c \| sort -rn \| head` · `fd -e py \| wc -l` |
| recent git history | `git log --oneline -n 20` · `git log --oneline --stat -n 5 -- path` · `git log -S'needle' --oneline` · `git blame -L 10,20 f` |
| what changed | `git status --short` · `git diff --stat` · `git diff -- path` · `git diff --cached` · `git show --stat HEAD` |
| branches / remotes | `git branch --show-current` · `git branch -a --sort=-committerdate \| head` · `git remote -v` · `git fetch --prune` |
| commit | `git add path…` · `git commit -m "subject" -m "body"` · `git commit -F - <<'EOF' … EOF` |
| who listens on a port | `ss -ltnp \| grep :3000` (Linux) · `lsof -nP -iTCP:3000 -sTCP:LISTEN` (macOS) |
| process by name | `pgrep -af name` · `ps -eo pid,etime,pcpu,pmem,args --sort=-pcpu \| head -n 15` |
| disk / size | `du -sh dir` · `du -sh * \| sort -h \| tail -n 20` · `df -h .` · `wc -c < f` |
| HTTP | `curl -fsSL URL` · `curl -sS -o /dev/null -w '%{http_code}\n' URL` · `curl -sS -X POST -H 'Content-Type: application/json' -d @body.json URL` · `curl -sS --max-time 10 --retry 3 URL` |
| archives | `tar -tzf x.tgz \| head` · `tar -xzf x.tgz -C dir` · `tar -czf out.tgz dir` · `unzip -l x.zip` · `unzip -q x.zip -d dir` |
| checksums | `sha256sum f` (`shasum -a 256 f`) · `md5sum f` |
| env / system | `uname -a` · `cat /etc/os-release` · `sw_vers` · `nproc` · `free -h` · `id` · `command -v tool` · `printenv NAME` |
| dates | `date -u +%Y-%m-%dT%H:%M:%SZ` · `date +%s` |
| temp files | `t=$(mktemp -d)` · `f=$(mktemp)` |
| wait for a port | `for i in $(seq 1 30); do nc -z localhost 5432 && break; sleep 1; done` |
| run with a time limit | `timeout 60 CMD` (`gtimeout` on macOS) |
| compare | `diff -u a b` · `diff <(cmd1) <(cmd2)` · `comm -23 <(sort a) <(sort b)` |
| columns / text | `awk '{print $2}'` · `cut -d: -f1` · `tr -s ' '` · `sort -t, -k2,2n` · `column -t` · `paste -sd,` |
| replace in files | `rg -l old \| xargs sed -i 's/old/new/g'` (preview with `rg -n old` first; `sed -i ''` on macOS) |
| docker | `docker ps --format '{{.Names}}\t{{.Status}}'` · `docker logs --tail 100 NAME` · `docker exec NAME CMD` · `docker compose ps` |
| gh | `gh pr view N --json state,title` · `gh pr checks N` · `gh run list -L 5` · `gh api repos/{owner}/{repo}/pulls --jq '.[].title'` |
| tests | `npm test -- --run` / `pnpm test` · `pytest -q -x tests/` · `cargo test -q` · `go test ./... 2>&1 \| tail -n 40` |
| lint / format | `npm run lint` · `ruff check .` · `cargo clippy -q` · `gofmt -l .` · `shellcheck script.sh` |
| install deps exactly | `npm ci` · `pnpm install --frozen-lockfile` · `uv sync` · `cargo build --locked` · `bundle install` |
| start a server for a check | `nohup CMD > /tmp/srv.log 2>&1 &` then poll, then `tail -n 20 /tmp/srv.log` |

## When a command fails

Recovery is where taste shows most. Read the error, then pick the fix that a maintainer would:

| Symptom | Likely cause → what to do |
|---|---|
| `command not found` | not installed or named differently: `command -v`, `fdfind`/`batcat`, `python3`, `docker compose`; use the fallback from the card; do not install globally without asking |
| `Permission denied` | wrong owner/mode, or writing outside the project: `ls -la`, `id`; fix the specific file; never `chmod 777`/`sudo` reflexively |
| hangs until timeout | waits for input or a TTY, or is a server: see habit 3; add `</dev/null`, `-y`, `timeout`, background it |
| `No such file or directory` | cwd drift or quoting: `pwd`, `ls -la`, absolute paths; check the `cd` from an earlier call |
| exit 124 | `timeout` fired: the command is slow or a server; raise the limit knowingly or run it in the background |
| exit 141 / `Broken pipe` | `head` closed the pipe; harmless |
| `the input device is not a TTY` | drop `-t`/`-it` |
| `Terminal is dumb, but EDITOR unset` | `git commit -m`, `--no-edit`, `GIT_SEQUENCE_EDITOR=true` |
| `EADDRINUSE` / port busy | `ss -ltnp \| grep :PORT` or `lsof -i :PORT`, then decide with the user which process to stop |
| `dubious ownership` (git) | container uid mismatch: `git config --global --add safe.directory PATH` (ask) or `GIT_CONFIG_GLOBAL` for the session |
| `externally-managed-environment` (pip) | create a venv, or `uv`; `--break-system-packages` only in a disposable container |
| `ERESOLVE`/peer deps (npm) | read the conflict; `--legacy-peer-deps` is a workaround to mention, not a default |
| `Argument list too long` | `find … -exec CMD {} +` or `xargs` |
| BSD/GNU flag errors (`illegal option`, `invalid option`) | wrong flavor: habit 7 |
| network/TLS errors | `curl -sS -v URL 2>&1 \| tail -n 20`; respect the proxy and CA bundle; never `-k` as the fix |
| flaky | retry once with backoff, not a loop forever; report it as flaky if it alternates |

More cases and the diagnosis commands for each: `references/recovery.md`.

## Reviewing a command (`/bash-sense:review <command>`)

1. Run the same checks the hook runs: `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/bash_sense.py" --check "<command>"` (prints `[DENY|ASK|ADVISE rule] …` or `ok`).
2. Read the command against the seven habits and the environment card. Name what the command *means* (its intent), then what the maintainer would type for that intent.
3. Answer in this shape:

```
Intent: <one line>
Better: `<command>`
Why: <2–3 bullets: tool, output size, safety/non-interactive, project convention>
Keep as is when: <the case where the original is right, if any>
```

Keep it short; a review is a nudge, not a lecture. If the original is already the right call, say so in one line.

## References

- `references/intent-to-command.md` — the long table, by domain, with fallbacks when a tool is missing
- `references/git.md` — inspection, non-interactive commits, history surgery, what to ask before
- `references/package-managers.md` — detection rules and commands for JS, Python, Rust, Go, Ruby, PHP, JVM, .NET, task runners, version managers
- `references/non-interactive.md` — everything that prompts or pages, and the flag or variable that stops it; backgrounding servers
- `references/safety.md` — destructive commands, their reversible alternatives, the ask-first list, secrets hygiene
- `references/platforms.md` — GNU vs BSD vs busybox vs Windows, with detection snippets
- `references/text-processing.md` — grep/rg/sed/awk/sort/uniq/cut/tr/xargs/jq/yq idioms and when each is the right tool
- `references/scripting.md` — when to write a script, strict mode, quoting, loops over files, traps, portability, ShellCheck
- `references/recovery.md` — error message → diagnosis → fix
