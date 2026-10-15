# When a command fails: diagnose, then fix like a maintainer

The reflexes that read as careless: `sudo`, `chmod 777`, `rm -rf node_modules && npm install`,
`--force`, `-k`, `|| true`, retrying the same command unchanged. The reflexes that read as taste:
read the error, confirm the cause with one cheap command, apply the narrow fix, re-run.

## Error → cause → fix

| Error text | Confirm | Fix |
|---|---|---|
| `command not found: X` | `command -v X; echo $PATH` | different name (`fdfind`, `batcat`, `python3`, `docker compose`), project-local bin (`npx X`, `.venv/bin/X`, `uv run X`), or not installed: use the fallback, or ask before installing |
| `No such file or directory` | `pwd; ls -la DIR` | cwd drifted (a `cd` in an earlier call), wrong relative path, unquoted space, file is generated and missing (`npm run build` first) |
| `Permission denied` (reading) | `ls -la PATH; id` | owned by another user: ask; secrets you should not read anyway |
| `Permission denied` (executing) | `ls -la PATH` | `chmod +x PATH` or run via its interpreter (`bash script.sh`, `python3 x.py`) |
| `Permission denied` (writing under `/usr`, `/opt`, another user's `~`) | `ls -ld DIR` | write inside the project or a temp dir instead of escalating |
| `EACCES` from npm/pip global installs | `npm config get prefix` | do not `sudo`; install locally (`npx`, venv) or use a user prefix |
| `externally-managed-environment` | `python3 -m pip --version` | venv or `uv`; `--break-system-packages` only in a throwaway container |
| `Terminal is dumb, but EDITOR unset` / editor hangs | — | `-m`, `--no-edit`, `GIT_EDITOR=true` |
| `the input device is not a TTY` | — | drop `-t`/`-it`; `docker compose exec -T` |
| `Error opening terminal: unknown` | `echo $TERM` | the program needs a TTY (top, watch): use the batch form |
| hangs, then timeout (exit 124 or harness kill) | the command's nature | server/watcher → background; prompt → `-y`, `</dev/null`; network → `--max-time`; genuinely slow → raise the limit knowingly |
| exit 141 / `Broken pipe` | `head` or `grep -m` closed the pipe | harmless; `\|\| true` on that pipeline if `pipefail` is set |
| `Argument list too long` | `… \| wc -l` | `find … -exec CMD {} +`, `xargs`, or batch |
| `Resource temporarily unavailable`, `Too many open files` | `ulimit -n` | close the loop that leaks; `ulimit -n 4096` in the script |
| `No space left on device` | `df -h .; du -sh /tmp/* \| sort -h \| tail` | delete caches/artifacts you created; never system files |
| `illegal option` (BSD), `invalid option` (GNU), `unrecognized option` | `TOOL --version` | wrong flavor: see platforms.md |
| `sed: 1: "…": command a expects \` | BSD sed | `sed -i ''`, `-E`, `$'\n'` for inserts |
| `grep: invalid option -- 'P'` | BSD grep | `rg -P`, `grep -E` |
| `date: illegal option -- d` | BSD date | `date -v`, `date -j -f` |
| `EADDRINUSE` / `address already in use` | `ss -ltnp \| grep :PORT` / `lsof -i :PORT` | stop the one you started (saved pid); otherwise ask; or use another port |
| `ECONNREFUSED` | `curl -sS -o /dev/null -w '%{http_code}' URL` | the service is not up yet: wait with a bounded poll; check its log |
| `ENOTFOUND` / `getaddrinfo` / DNS | `getent hosts HOST`, `dig +short HOST` | no network or wrong host; respect proxies (`HTTPS_PROXY`); do not retry forever |
| TLS: `self signed certificate in certificate chain`, `unable to get local issuer` | `curl -sSv URL 2>&1 \| grep -i -A2 'SSL'` | the environment has a proxy/CA bundle: `--cacert`, `SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS`, `REQUESTS_CA_BUNDLE`, `GIT_SSL_CAINFO`; never `-k`/`NODE_TLS_REJECT_UNAUTHORIZED=0` |
| `403`/`407` from a proxy | `env \| cut -d= -f1 \| grep -i proxy` | the host is not allowed: say so; do not tunnel around it |
| `fatal: detected dubious ownership` | `ls -ld .git; id` | `git config --global --add safe.directory PATH` (ask) or `git -c safe.directory=PATH …` |
| `fatal: not a git repository` | `git rev-parse --show-toplevel` | wrong cwd; `git -C DIR` |
| `error: Your local changes would be overwritten` | `git status --short` | `git stash push -u -m wip`, do the operation, `git stash pop`; never `reset --hard` |
| merge conflict | `git diff --name-only --diff-filter=U` | resolve by hand, regenerate lockfiles with the tool, `git add`, continue |
| `rejected … non-fast-forward` | `git fetch; git log --oneline HEAD..origin/BR` | `git pull --rebase` on your branch, merge on shared ones; never `--force` without a lease and a reason |
| `Could not read from remote` / auth prompt | `GIT_TERMINAL_PROMPT=0 git ls-remote URL` | credentials are the user's: `gh auth status`, deploy keys; do not embed tokens |
| `ERESOLVE unable to resolve dependency tree` | `npm ls PKG` | read which peer conflicts; fix the version; `--legacy-peer-deps` is a workaround to mention, not apply silently |
| `ERR_PNPM_OUTDATED_LOCKFILE` | `git status pnpm-lock.yaml` | `pnpm install` (updates the lock) if dependencies were meant to change; otherwise the lockfile is stale in the repo |
| `Cannot find module` / `ModuleNotFoundError` | `ls node_modules/PKG`, `.venv/bin/pip show PKG` | install with the project's manager; wrong interpreter (`python` vs `.venv/bin/python`); wrong cwd for relative imports |
| `ImportError: … GLIBC` / `symbol not found` | `ldd --version`, `uname -m` | wrong platform wheel/binary (alpine, arm64): use the image's package or a compatible build |
| `SyntaxError` on a shell script (`\r`) | `file script.sh` | CRLF line endings: `sed -i 's/\r$//' script.sh` or `dos2unix` |
| `Killed` (exit 137) | `dmesg \| tail` (if allowed), `free -h` | out of memory: lower parallelism (`-j2`, `NODE_OPTIONS=--max-old-space-size=4096`), split the job |
| tests fail only in the agent run | `CI=1 TZ=UTC LANG=C.UTF-8 …` | environment differences (TTY, locale, timezone, missing services); run the project's documented command |
| flaky (passes on retry) | run twice | report as flaky with evidence; never loop until green |

## The diagnosis toolkit (cheap, safe)

```bash
pwd; ls -la; id; uname -a
command -v TOOL; TOOL --version
echo "$PATH" | tr ':' '\n'
printenv NAME | cut -c1-4            # presence of a variable without leaking it
git status --short --branch; git log --oneline -n 5
ls -la node_modules/.bin | head; ls .venv/bin | head
curl -sS -o /dev/null -w '%{http_code}\n' URL
ss -ltnp | head; lsof -nP -iTCP -sTCP:LISTEN | head
df -h .; du -sh . 2>/dev/null
tail -n 50 /tmp/*.log
CMD 2>&1 | tail -n 40                # the end of a failed build has the real error
```

## Retrying

- Network and registry errors: retry up to 3–4 times with exponential backoff (`2 4 8 16` seconds). Not for logic errors.
- Change one thing per retry, and say what changed. Running the identical command again is only justified for a suspected transient failure.
- When a fix would widen the task (install a toolchain, change the lockfile, rewrite config), stop and report with the exact error and the proposed change.

## Nuclear options are not first options

`rm -rf node_modules && npm install`, `git clean -fdx`, `docker system prune`, `pip install --force-reinstall`,
`cargo clean`: each costs minutes and may delete something that was not the problem. Reach for them
only after the narrow fix failed, and say why.
