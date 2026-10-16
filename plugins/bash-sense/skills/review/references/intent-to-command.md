# Intent → command

The command a maintainer would type for each intent, with the fallback when the preferred tool is
not installed (check the environment card). `rg`/`fd`/`jq`/`gh` are assumed when present.

Contents: files · text search · reading files · structured data · processes and ports · disk ·
network · archives · checksums and encoding · dates and math · system facts · git · docker ·
package managers and tasks · temp files and cleanup.

## Files and directories

| Intent | Preferred | Fallback / notes |
|---|---|---|
| shape of a project | `fd -t d -d 2` · `tree -L 2 -I node_modules -I dist` | `find . -maxdepth 2 -type d -not -path '*/node_modules/*' -not -path '*/.git/*' \| sort` |
| tracked files | `git ls-files` · `git ls-files 'src/**/*.ts'` | only inside a repo; respects `.gitignore` by construction |
| files by extension | `fd -e py` · `fd -e ts -e tsx src` | `find . -type f \( -name '*.ts' -o -name '*.tsx' \) -not -path '*/node_modules/*'` |
| files by name pattern | `fd 'config\.' ` · `fd -g '*.config.js'` | `find . -type f -name '*.config.js' -not -path '*/node_modules/*'` |
| including hidden / ignored | `fd -H '^\.env'` · `fd -I -e log` | `find . -name '.env*'` |
| recently changed | `fd --changed-within 1d` · `fd --changed-within 2h -e ts` | `find . -type f -mmin -120 -not -path '*/.git/*'` |
| big files | `fd -t f -S +10m` · `du -ah . \| sort -h \| tail -n 20` | `find . -type f -size +10M` |
| empty dirs | `fd -t d -te` | `find . -type d -empty` |
| count files | `fd -e py \| wc -l` | `find . -name '*.py' \| wc -l` |
| run a command per file | `fd -e png -x optipng {}` · `fd -e py -X ruff check` (batch) | `find . -name '*.png' -exec optipng {} \;` · `-exec CMD {} +` (batch) |
| list newest first | `ls -lt \| head -n 20` | `ls -lt` is portable; `--sort` is GNU only |
| one level, dirs only | `ls -d */` · `fd -t d -d 1` | |
| file type / what is this | `file PATH` · `head -c 200 PATH \| xxd \| head` | |
| real path | `realpath PATH` · `readlink -f PATH` (GNU; macOS 12.3+) | `cd DIR && pwd -P` |
| copy preserving attrs | `cp -a SRC DEST` · `rsync -a --exclude node_modules SRC/ DEST/` | `rsync -n` to preview |
| make dirs | `mkdir -p a/b/c` | |
| safe move | `mv -n SRC DEST` (no clobber) · `mv -i` is interactive, avoid | |
| who owns / modes | `ls -la PATH` · `stat -c '%U %a %n' PATH` (GNU) / `stat -f '%Su %Lp %N' PATH` (BSD) | |
| watch for changes | `entr` is interactive-ish; prefer a bounded loop or `fd … --changed-within` | |

## Text search

| Intent | Preferred | Fallback / notes |
|---|---|---|
| literal string anywhere | `rg -n -F 'exact(text)'` | `git grep -n -F 'exact(text)'` · `grep -rnF --exclude-dir={.git,node_modules} 'exact(text)' .` |
| regex | `rg -n 'fo+\s*bar'` · `rg -n -P '(?<=x)y'` (PCRE) | `grep -rnE` · `grep -P` is GNU only |
| whole word / case-insensitive | `rg -nw word` · `rg -ni word` | `grep -w` · `grep -i` |
| only in some file types | `rg -n -t py 'import x'` · `rg -n -g '*.{ts,tsx}' useEffect` · `rg -n -g '!*.test.ts' …` | `grep --include='*.py'` |
| only file names / counts | `rg -l pat` · `rg -c pat` · `rg --files-with-matches` | `grep -rl` · `grep -rc` |
| context | `rg -n -C2 pat` · `-A3 -B1` | same in grep |
| in ignored/hidden/vendored | `rg -n --no-ignore pat` · `rg -n --hidden pat` · `rg -nuu pat` (everything) | `grep -r` already does |
| multiline | `rg -n -U 'start[\s\S]*?end'` | `grep -Pzo` (GNU) |
| JSON output for parsing | `rg --json pat \| jq …` | |
| list files then act | `rg -l pat \| xargs sed -i 's/a/b/'` · `rg -l0 pat \| xargs -0 …` (space-safe) | preview with `rg -n pat` first |
| search git history | `git log -S'needle' --oneline` (adds/removes) · `git log -G'regex' --oneline` · `git grep -n pat $(git rev-list -n 20 HEAD)` | |
| search inside archives / binaries | `zgrep -n pat file.gz` · `strings bin \| rg pat` | |
| structural code search | `ast-grep -p 'console.log($A)' -l ts` (when installed) | `rg -n 'console\.log\('` |
| TODOs | `rg -n -e TODO -e FIXME -e XXX` | |

## Reading files

| Intent | Preferred | Notes |
|---|---|---|
| first/last N lines | `head -n 60 f` · `tail -n 40 f` | `head -5` works but `-n 5` is the portable spelling |
| a range | `sed -n '120,160p' f` · `awk 'NR>=120 && NR<=160' f` | `sed -n '/^def main/,/^$/p' f` for a block |
| with line numbers | `cat -n f \| sed -n '1,80p'` · `nl -ba f \| head` · `rg -n '' f \| head -n 80` | |
| long lines / minified | `head -c 500 f` · `fold -w 120 f \| head` · `cut -c1-200 f` | never `cat` a `.min.js` |
| how big is it | `wc -l f` · `wc -c < f` · `du -h f` | |
| binary | `file f` · `xxd f \| head` · `strings f \| head` | |
| compressed logs | `gunzip -c f.gz \| tail -n 40` (macOS `zcat` wants `.Z` files) · `zless` is a pager, avoid | |
| follow a log for a bit | `timeout 10 tail -f f` · `tail --pid=$PID -f f` (GNU) | never bare `tail -f` |
| multiple files, labelled | `tail -n +1 a b` · `head -n 20 a b` | prints `==> name <==` headers |
| diff two files/outputs | `diff -u a b` · `diff <(sort a) <(sort b)` · `git diff --no-index a b` | |

## Structured data

| Intent | Preferred | Fallback |
|---|---|---|
| pretty-print JSON | `jq . f` · `jq -C . f \| head` | `python3 -m json.tool f \| head` |
| pick a field | `jq -r '.scripts.test' package.json` · `jq '.a.b[0].c' f` | `python3 -c 'import json,sys; print(json.load(open("f"))["a"])'` |
| explore keys | `jq 'keys' f` · `jq 'paths(scalars) \| map(tostring) \| join(".")' f \| head -n 50` · `jq 'map(keys) \| add \| unique' f` | |
| filter array | `jq '.[] \| select(.enabled) \| .name' f` · `jq -r '.[] \| [.id,.name] \| @tsv' f` | |
| from a command's output | `gh api … \| jq -r '.[].title'` · `curl -sS URL \| jq .` | |
| modify in place | `jq '.version="1.2.3"' f > tmp && mv tmp f` (jq has no `-i`) · `tmp=$(mktemp)` | preserve key order: jq does by default |
| JSON lines | `jq -c '.' f.jsonl \| head` · `jq -s 'length' f.jsonl` | |
| YAML | `yq '.services \| keys' compose.yaml` · `yq -r '.spec.ports[0].port' f` | mikefarah `yq` syntax; the python `yq` wrapper takes jq syntax |
| TOML | `python3 -c 'import tomllib,sys; print(tomllib.load(open("pyproject.toml","rb"))["project"]["name"])'` (3.11+) · `yq -p toml` | `grep -n '^name' pyproject.toml` |
| CSV | `column -s, -t f.csv \| head` · `awk -F, '{print $2}' f.csv` · `python3 -c 'import csv…'` for quoted fields · `mlr` if installed | |
| .env keys | `cut -d= -f1 .env` · `grep -c '^NAME=' .env` | never print values |
| XML/HTML | `xmllint --xpath '//item/@id' f.xml` · `python3 -c 'import xml.etree.ElementTree as E…'` | |

## Processes and ports

| Intent | Preferred | Notes |
|---|---|---|
| is X running | `pgrep -af '[n]ode'` (Linux) · `pgrep -fl '[u]vicorn.*8000'` (macOS: `-l` prints the command, `-a` means ancestors) | the `[n]` bracket keeps the pattern from matching the shell running the command |
| top consumers | `ps -eo pid,pcpu,pmem,etime,args --sort=-pcpu \| head -n 15` (Linux) · `top -l 1 -n 15 -o cpu` (macOS) | never bare `top`/`htop` |
| process tree | `ps -ef --forest \| grep -A5 PID` (Linux) · `pstree -p PID` | |
| who listens on a port | `ss -ltnp \| grep ':3000 '` (Linux) · `lsof -nP -iTCP:3000 -sTCP:LISTEN` (macOS/Linux) | `netstat -anv \| grep LISTEN` on macOS |
| stop a process you started | `kill PID` → wait → `kill -9 PID` only if it survives · `kill %1` for a shell job | `pkill -f '[e]xact command line'` (bracketed, or it also matches the tool's shell); never `pkill node` |
| wait for exit | `wait $PID` (same shell) · `while kill -0 $PID 2>/dev/null; do sleep 1; done` | |
| run with time limit | `timeout 60 CMD` · `timeout -k 5 60 CMD` | `gtimeout` on macOS |
| background with log | `nohup CMD > /tmp/x.log 2>&1 &` · `echo $!` to keep the pid · `setsid CMD &` (Linux) to detach from the session | |
| limits / env of a pid | `cat /proc/PID/environ \| tr '\0' '\n' \| cut -d= -f1` (Linux) · `cat /proc/PID/limits` | |
| open files | `lsof -p PID \| head` · `ls -la /proc/PID/fd` | |

## Disk

| Intent | Preferred |
|---|---|
| size of a dir | `du -sh dir` · `du -sh */ \| sort -h` |
| biggest subdirs | `du -h -d1 . \| sort -h \| tail -n 15` (BSD/GNU) · `dust` when installed |
| free space | `df -h .` · `df -h /` |
| what is eating space in a repo | `git count-objects -vH` · `du -sh .git node_modules target` |
| clean build artifacts | `git clean -ndX` (preview ignored files) → `git clean -fdX` (only ignored) · `rm -rf node_modules dist target` |
| inode exhaustion | `df -i .` |

## Network

| Intent | Preferred | Notes |
|---|---|---|
| GET to screen | `curl -fsSL URL` · `curl -sS URL \| head -c 2000` | `-s` silent, `-S` show errors, `-f` fail on 4xx/5xx, `-L` follow |
| status code only | `curl -sS -o /dev/null -w '%{http_code}\n' URL` | |
| headers | `curl -sSI URL` · `curl -sS -D - -o /dev/null URL` | |
| download | `curl -fsSLo FILE URL` · `curl -fsSLO URL` (keep name) · `wget -q URL` | `--max-time 30 --retry 3 --retry-delay 2` for flaky hosts |
| POST JSON | `curl -sS -X POST URL -H 'Content-Type: application/json' -d @body.json` · `-d '{"a":1}'` | |
| auth | `-H "Authorization: Bearer $TOKEN"` · `--netrc` · `-u "$USER:$PASS"` only from variables | never a literal token |
| debug TLS/proxy | `curl -sSv URL 2>&1 \| tail -n 30` · `curl --cacert FILE` · respect `HTTPS_PROXY` | never `-k` as the fix |
| is a port open | `nc -zv HOST 5432` · `timeout 3 bash -c '</dev/tcp/HOST/5432' && echo open` | |
| DNS | `dig +short HOST` · `getent hosts HOST` (Linux) · `nslookup HOST` · `host HOST` | |
| my IP / interfaces | `ip -br addr` (Linux) · `ifconfig \| grep inet` (macOS) · `curl -sS ifconfig.me` | |
| wait for an HTTP service | `for i in $(seq 1 30); do curl -sf -o /dev/null localhost:3000 && break; sleep 1; done` | |
| rsync | `rsync -avz --delete -n SRC/ DEST/` (preview) then without `-n` | trailing slash semantics matter |
| ssh | `ssh -o BatchMode=yes -o ConnectTimeout=5 HOST 'CMD'` · `scp -q FILE HOST:PATH` | never a bare `ssh HOST` |

## Archives

| Intent | Preferred |
|---|---|
| list | `tar -tzf x.tgz \| head -n 30` · `tar -tjf x.tbz2` · `unzip -l x.zip` · `7z l x.7z` |
| extract | `tar -xzf x.tgz -C DIR` · `tar -xzf x.tgz --strip-components=1 -C DIR` · `unzip -q x.zip -d DIR` |
| create | `tar -czf out.tgz --exclude=node_modules DIR` · `zip -rq out.zip DIR -x '*/node_modules/*'` |
| one file out of an archive | `tar -xzf x.tgz path/inside/file -O \| head` · `unzip -p x.zip path/inside/file \| head` |
| gzip a single file | `gzip -k f` (keep) · `gunzip -c f.gz \| head` · `zstd -q f` |

## Checksums and encoding

| Intent | GNU/Linux | macOS |
|---|---|---|
| sha256 | `sha256sum f` · `sha256sum -c sums.txt` | `shasum -a 256 f` |
| md5 | `md5sum f` | `md5 -q f` |
| base64 | `base64 -w0 f` · `base64 -d` | `base64 -i f` · `base64 -D` |
| hex dump | `xxd f \| head` · `od -c f \| head` | same |
| url-encode | `jq -rn --arg s "$S" '$s\|@uri'` · `python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))' "$S"` | same |
| random | `openssl rand -hex 16` · `uuidgen` · `head -c 16 /dev/urandom \| xxd -p` | same |

## Dates and math

| Intent | GNU/Linux | macOS |
|---|---|---|
| ISO timestamp UTC | `date -u +%Y-%m-%dT%H:%M:%SZ` | same |
| epoch | `date +%s` | same |
| epoch → date | `date -d @1700000000` | `date -r 1700000000` |
| relative | `date -d '2 days ago' +%F` | `date -v-2d +%F` |
| parse a string | `date -d '2024-01-31' +%s` | `date -j -f '%Y-%m-%d %H:%M:%S' '2024-01-31 00:00:00' +%s` (unspecified fields default to *now*) |
| file mtime | `stat -c %y f` · `date -r f` | `stat -f %Sm f` |
| arithmetic | `echo $((7*24))` · `bc -l <<< '22/7'` · `python3 -c 'print(22/7)'` | same |
| time a command | `time CMD` · `hyperfine 'CMD'` (installed) · `/usr/bin/time -v CMD` (GNU) | `/usr/bin/time -l CMD` |

## System facts

| Intent | Command |
|---|---|
| OS | `uname -a` · `cat /etc/os-release` (Linux) · `sw_vers` (macOS) · `grep -i microsoft /proc/version` (WSL) |
| CPU / memory | `nproc` / `sysctl -n hw.ncpu` · `free -h` / `vm_stat` · `getconf _NPROCESSORS_ONLN` (portable) |
| shell and tool versions | `bash --version \| head -n 1` · `node --version` · `python3 --version` · `git --version` |
| is a tool installed | `command -v rg` · `command -v fd \|\| command -v fdfind` |
| user / privileges | `id` · `whoami` · `sudo -n true 2>/dev/null && echo passwordless-sudo` |
| environment, safely | `printenv PATH HOME SHELL` · `env \| cut -d= -f1 \| sort` · `[ -n "$TOKEN" ] && echo set` |
| locale / tz | `locale` · `date +%Z` · `cat /etc/timezone` |
| container? | `[ -f /.dockerenv ] && echo docker` · `cat /proc/1/cgroup \| head -n 3` |

## Git (see `git.md` for the long form)

| Intent | Command |
|---|---|
| state | `git status --short --branch` · `git branch --show-current` · `git rev-parse --short HEAD` |
| history | `git log --oneline -n 20` · `git log --oneline --graph --all -n 30` · `git log --stat -n 3` · `git log --oneline -- path` · `git log --since='2 weeks ago' --author=NAME --oneline` |
| what changed | `git diff --stat` · `git diff -- path` · `git diff --cached --stat` · `git diff main...HEAD --stat` · `git show --stat HEAD` · `git show HEAD:path/file \| head` |
| who / when | `git blame -L 10,30 -- file` · `git log -n 1 --format='%h %an %ad' -- file` |
| find | `git log -S'text' --oneline -- path` · `git grep -n pat` · `git log --all --oneline -- '*filename*'` |
| commit | `git add path…` · `git commit -m "subject" -m "body"` · multi-line: `git commit -F - <<'EOF' … EOF` |
| branch | `git switch -c name` · `git switch main` · `git fetch origin main` · `git rebase origin/main` (on your own branch) · `git merge --no-edit origin/main` |
| undo safely | `git stash push -u -m 'why'` · `git revert HEAD` · `git reset --soft HEAD~1` · `git restore --staged path` |
| remote | `git remote -v` · `git fetch --prune` · `git push -u origin BRANCH` · `git push --force-with-lease` (own branch only) |
| ignored? | `git check-ignore -v path` · `git status --ignored --short \| head` |
| tags | `git tag --sort=-v:refname \| head` · `git describe --tags --always` |
| worktrees | `git worktree add ../wt-feature feature` · `git worktree list` |
| size / health | `git count-objects -vH` · `git fsck --no-dangling` · `git gc --prune=now` (ask) |

## Docker and compose

| Intent | Command |
|---|---|
| what runs | `docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'` · `docker compose ps` |
| logs | `docker logs --tail 100 NAME` · `docker compose logs --tail 50 --no-color SVC` · `--since 10m` |
| run a command | `docker exec NAME CMD` · `docker compose exec -T SVC CMD` · `docker run --rm IMAGE CMD` |
| build | `docker build -t tag --progress=plain . 2>&1 \| tail -n 40` · `docker compose build SVC` |
| start / stop | `docker compose up -d SVC` · `docker compose down` (keeps volumes) · `docker stop NAME` |
| inspect | `docker inspect NAME --format '{{.State.Status}}'` · `docker image ls \| head` · `docker compose config` (rendered) |
| copy | `docker cp NAME:/path ./local` |
| clean (ask) | `docker compose down -v` · `docker system prune` |

## Package managers and tasks (see `package-managers.md`)

| Intent | Command |
|---|---|
| detect | `ls package-lock.json pnpm-lock.yaml yarn.lock bun.lock* uv.lock poetry.lock Cargo.lock go.sum Gemfile.lock 2>/dev/null` · `jq -r .packageManager package.json` |
| scripts / targets | `jq -r '.scripts \| keys[]' package.json` · `make -qp 2>/dev/null \| awk -F: '/^[a-zA-Z0-9_-]+:/{print $1}' \| sort -u \| head` · `just --list` · `task --list` |
| install exactly | `npm ci` · `pnpm install --frozen-lockfile` · `yarn install --immutable` · `bun install --frozen-lockfile` · `uv sync` · `poetry install` · `bundle install` · `cargo fetch` · `go mod download` |
| add a dependency | `npm install PKG` · `pnpm add -D PKG` · `yarn add PKG` · `bun add PKG` · `uv add PKG` · `poetry add PKG` · `cargo add PKG` · `go get PKG@latest` |
| run a script | `npm run NAME` · `pnpm NAME` · `yarn NAME` · `bun run NAME` · `uv run CMD` · `poetry run CMD` |
| one-off tool | `npx -y PKG` · `pnpm dlx PKG` · `bunx PKG` · `uvx PKG` · `pipx run PKG` · `go run PKG@latest` · `cargo run --` |
| outdated / why | `npm outdated` · `pnpm why PKG` · `npm ls PKG` · `pip list --outdated` · `cargo tree -i PKG` |
| version of an installed pkg | `npm ls PKG --depth=0` · `jq -r '.packages["node_modules/PKG"].version' package-lock.json` · `pip show PKG \| grep -i ^version` · `cargo tree -p PKG --depth 0` |
| tests | `npm test -- --run` · `pnpm test` · `pytest -q -x` · `cargo test -q` · `go test ./... 2>&1 \| tail -n 40` · `./gradlew test -q --console=plain` · `bundle exec rspec` · `dotnet test --nologo -v q` |
| lint / typecheck / format | `npm run lint` · `ruff check . && ruff format --check .` · `mypy .` · `cargo clippy -q -- -D warnings` · `cargo fmt --check` · `gofmt -l .` · `go vet ./...` · `shellcheck *.sh` |

## Temp files and cleanup

| Intent | Command |
|---|---|
| temp dir/file | `t=$(mktemp -d)` · `f=$(mktemp)` · `mktemp -d /tmp/work.XXXXXX` (portable template) |
| auto-clean in a script | `trap 'rm -rf "$t"' EXIT` |
| scratch that survives the call | use the harness's scratchpad dir when it names one; else `/tmp/<task>-$(date +%s)` |
| delete safely | `ls -la PATH` → `rm -rf PATH` for artifacts · `d=/tmp/trash-$(date +%s); mkdir -p "$d" && mv PATH "$d"/` for anything else |
