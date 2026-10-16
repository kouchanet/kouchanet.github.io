# Nothing that waits for a human

Every command runs without a terminal: stdin is empty or closed, stdout is a pipe, nobody can press
a key. Three things go wrong: a prompt waits forever (the call times out), a TTY check fails
("not a tty", "Error opening terminal"), or a server keeps running until the harness kills it.

## What happens without a TTY

| Program | Without a terminal | Right form |
|---|---|---|
| `less`, `more`, `man` | acts like `cat` (whole file) | `head -n`, `sed -n 'a,bp'`, `CMD --help \| head -n 60` |
| `git log/diff/show` | no pager (git checks the TTY) | fine; bound with `-n`, `--stat` |
| `git commit` (no `-m`) | launches `$EDITOR`: hangs or "Terminal is dumb" | `-m`, `-F -` with a heredoc, `--no-edit` |
| `git merge` | no editor unless `-e` | `--no-edit` to be explicit |
| `git rebase -i`, `add -p` | editor / hunk prompt | scripted forms (see git.md) |
| `vim`, `nano`, `emacs` | hangs or errors | Edit tool, `sed -i`, heredoc |
| `top`, `htop` | "failed tty get" / hangs | `top -bn1 \| head -n 20`, `ps -eo … --sort` |
| `watch` | "Error opening terminal" / hangs | bounded loop |
| `tail -f` | hangs | `tail -n 100`, `timeout 15 tail -f`, `tail --pid=$PID -f` |
| `python`, `node`, `irb`, `psql` (no args) | reads EOF and exits, or hangs | `-c`, `-e`, heredoc on stdin |
| `sudo` | "a terminal is required to read the password" | NOPASSWD or ask the user |
| `ssh HOST` (no command) | hangs / exits | `ssh -o BatchMode=yes HOST 'CMD'` |
| `ssh` to a new host | host key prompt (fails or hangs) | `-o StrictHostKeyChecking=accept-new` |
| `docker exec -it`, `docker run -it` | "the input device is not a TTY" | drop `-t`; `-i` only when piping stdin |
| `docker compose exec` | Compose v2 drops the TTY by itself when stdout is not a terminal | `-T` to be explicit in scripts |
| `apt-get install` (no `-y`) | "Abort." | `-y`, `DEBIAN_FRONTEND=noninteractive` |
| `npm init`, `yarn init` | questions | `-y` |
| `npx PKG` (first run) | auto-yes with a warning | `-y` to be explicit |
| `gh pr create` | "must provide --title and --body" | `--fill` / `--title … --body-file -` |
| `gh auth login` | device flow, hangs | `GH_TOKEN` / `--with-token` |
| `read`, `select` | returns at EOF | avoid in commands |
| `ping` (no `-c`) | forever | `ping -c 3` |
| `yes`, `cat` with no input | forever / waits | pipe into something, or `cat FILE` |
| `nc -l`, `python -m http.server`, any server | forever | background + poll |

## Variables that silence prompts and progress

```bash
export CI=1                               # many tools switch to non-interactive, no colors, no spinners
export NO_COLOR=1 FORCE_COLOR=0 TERM=dumb # no ANSI escapes in output
export DEBIAN_FRONTEND=noninteractive     # apt/dpkg never ask
export GIT_TERMINAL_PROMPT=0              # git fails fast instead of asking for credentials
export GIT_EDITOR=true GIT_SEQUENCE_EDITOR=true  # any editor invocation becomes a no-op (use deliberately)
export PAGER=cat GIT_PAGER=cat SYSTEMD_PAGER=cat LESS=FRX
export PYTHONUNBUFFERED=1                 # python logs appear in order
export npm_config_yes=true                # npx auto-confirms
export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_CLEANUP=1
export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1
export CARGO_TERM_COLOR=never
```

Prefix a single command instead of exporting (exports do not survive between calls):
`DEBIAN_FRONTEND=noninteractive apt-get install -y curl`.

Pass `</dev/null` to a command that might read stdin when you want it to fail fast rather than hang:
`some-tool --flag </dev/null`.

## Servers, watchers, dev modes

Signs you are about to start one: `dev`, `serve`, `start`, `watch`, `-w`, `preview`, `runserver`,
`http.server`, `uvicorn`, `flask run`, `rails s`, `next dev`, `vite`, `storybook`, `jupyter lab`,
`docker compose up` (no `-d`), `kubectl port-forward`, `ngrok`, `nodemon`, `tsc -w`, `jest --watch`.

Decide what you actually need:

1. **A one-shot result** (tests, type check, build): use the non-watch form. `vitest run`, `jest --watchAll=false`, `tsc --noEmit`, `next build`, `vite build`, `cargo build`, `go build ./...`.
2. **A running service to probe**: start it detached with a log, wait for the port, probe, then read the log tail. Keep the pid to stop it later.

```bash
nohup npm run dev > /tmp/dev.log 2>&1 &
echo $! > /tmp/dev.pid
for i in $(seq 1 30); do curl -sf -o /dev/null http://localhost:3000 && break; sleep 1; done
curl -sS -o /dev/null -w 'status=%{http_code}\n' http://localhost:3000
tail -n 20 /tmp/dev.log
# later:
kill "$(cat /tmp/dev.pid)"
```

- `docker compose up -d SVC` then `docker compose logs --tail 50 SVC`; `docker compose down` when done.
- If the harness's Bash tool has a run-in-background option, use it instead of `nohup` and poll its output.
- `setsid CMD >/tmp/x.log 2>&1 < /dev/null &` (Linux; macOS has no setsid) fully detaches when the process must outlive the call's shell.
- Port already in use: `ss -ltnp | grep :3000` (Linux) / `lsof -nP -iTCP:3000 -sTCP:LISTEN` (macOS); kill only the process you started (by saved pid) or ask.

## Polling with a bound

```bash
# wait up to 60 s for a port
for i in $(seq 1 60); do nc -z localhost 5432 && break; sleep 1; done
# wait for a file to appear
timeout 60 bash -c 'until [ -f /tmp/done ]; do sleep 1; done'
# wait for a job, then show the tail
timeout 300 bash -c 'while kill -0 '"$PID"' 2>/dev/null; do sleep 2; done'; tail -n 30 /tmp/job.log
```

`while true` without `break`/`timeout`, or `sleep 300`, are the two shapes that waste a call.

## Time limits

- `timeout 60 CMD` (GNU coreutils; `gtimeout` on macOS via brew, or `perl -e 'alarm shift; exec @ARGV' 60 CMD`).
- `timeout -k 5 60 CMD` sends KILL 5 s after TERM.
- Exit status 124 (`timeout`) or 142 (the perl `alarm` form, killed by SIGALRM) means the limit fired; decide whether the command is slow (raise it knowingly) or a server (background it).
- `curl --max-time 20 --connect-timeout 5`, `ssh -o ConnectTimeout=5`, `nc -w 3`, `pip --timeout 60`, `npm --fetch-timeout=60000` for network tools.

## Confirmations that tools ask for

| Tool | Flag |
|---|---|
| `rm -i`, `cp -i`, `mv -i` (some aliases) | `command rm`, or `rm -f` when absence is fine |
| `apt-get`, `yum`, `dnf` | `-y` |
| `pacman` | `--noconfirm` |
| `apk` | non-interactive by default |
| `brew` | non-interactive; `HOMEBREW_NO_AUTO_UPDATE=1` to skip the update |
| `pip uninstall` | `-y` |
| `npm init`, `npm create`, `yarn create` | `-y`, `--yes`, `--template NAME`, generator-specific `--no-interactive` |
| `cargo new`, `cargo init` | non-interactive |
| `gh pr create`, `gh repo create` | `--fill`, `--title/--body`, `--source . --public/--private` |
| `terraform apply`, `destroy` | `-auto-approve` (only after the user saw the plan) |
| `kubectl delete` | non-interactive; preview with `--dry-run=client` |
| `docker system prune` | `-f` (ask first) |
| `git clean` | `-f` (preview with `-n` first) |
| `mysql_secure_installation`, `aws configure`, `gcloud init` | interactive by design: the user runs them |
