# Platforms: GNU/Linux, macOS (BSD), busybox, Windows

The environment card tells you which flavor you have (`classic tools: GNU … / BSD …`). Write for
that flavor, or use the portable form when the command may run elsewhere (CI matrices, scripts
checked into the repo).

## Detect in one call

```bash
uname -s                                   # Linux / Darwin / MINGW64_NT… (Git Bash) / MSYS_NT
sed --version >/dev/null 2>&1 && echo GNU-sed || echo BSD-or-busybox-sed
grep -i microsoft /proc/version 2>/dev/null && echo WSL
readlink /bin/sed 2>/dev/null | grep -q busybox && echo busybox
cat /etc/os-release 2>/dev/null | head -n 2 ; sw_vers 2>/dev/null
command -v gsed gdate gtimeout greadlink 2>/dev/null   # GNU tools from brew coreutils on macOS
```

## GNU vs BSD, the ones that bite

| Task | GNU (Linux, brew `g*`) | BSD (macOS) | Portable |
|---|---|---|---|
| in-place sed | `sed -i 's/a/b/' f` | `sed -i '' 's/a/b/' f` | `sed -i.bak 's/a/b/' f && rm f.bak` |
| extended regex | `sed -E` (also `-r`) | `sed -E` | `sed -E` |
| `\+`, `\|`, `\?` in sed basic regex | GNU extensions | not in BSD | use `-E`; `\d` is not a digit class in any sed: `[0-9]` |
| insert/append text | `sed '3i text'` | `sed '3i\'$'\n''text'` | `awk` or `printf`/`ed` |
| PCRE grep | `grep -P '\d+(?=px)'` | no `-P` | `rg -P`, `perl -ne` |
| grep -o with groups | `grep -oP 'id=\K\d+'` | — | `sed -E 's/.*id=([0-9]+).*/\1/'`, `rg -oP` |
| `date` relative | `date -d '1 day ago' +%F` | `date -v-1d +%F` | `python3 -c 'import datetime as d; print(d.date.today()-d.timedelta(1))'` |
| `date` from epoch | `date -d @1700000000` | `date -r 1700000000` | `python3 -c 'import datetime as d; print(d.datetime.fromtimestamp(1700000000, d.timezone.utc))'` |
| parse a date string | `date -d '2024-01-31' +%s` | `date -j -f '%Y-%m-%d %H:%M:%S' '2024-01-31 00:00:00' +%s` (unspecified fields default to now) | python |
| file size | `stat -c %s f` | `stat -f %z f` | `wc -c < f` |
| mtime epoch | `stat -c %Y f` | `stat -f %m f` | `find f -newermt …` / python |
| mode / owner | `stat -c '%a %U' f` | `stat -f '%Lp %Su' f` | `ls -l f` |
| `xargs` delimiter | `xargs -d '\n'` | — | `tr '\n' '\0' \| xargs -0` |
| `xargs` with empty input | runs the command once unless `-r` | never runs it; `-r` accepted for compatibility | `xargs -r` |
| `find -printf` | yes | no | `find … -exec stat … {} +` |
| `find -regextype` | yes | `-E` flag before path | `-name`/`-path` globs |
| `readlink -f` | yes | macOS 12.3+ only | `realpath` (13+/coreutils), `cd DIR && pwd -P` |
| `realpath` | yes | macOS 13+ | python `os.path.realpath` |
| `cp --parents`, `cp -u` | yes | no | `rsync -R`, `mkdir -p` |
| `ls --time-style`, `ls --sort` | yes | no (`--color=when` exists since macOS 12; use `ls -G`, `ls -lT`) | `ls -l` |
| `sort -V` | yes | yes (10.14+) | `sort -t. -k1,1n -k2,2n -k3,3n` on very old systems |
| `head -n -N` (all but last N) | yes | no | `sed '$d'` for one line; `awk` |
| `tail -r` | — | reverse | `tac` (GNU) / `tail -r` (BSD) |
| `tac`, `shuf`, `nproc`, `timeout`, `sha256sum`, `md5sum`, `free`, `ss` | yes | missing | `tail -r`, `sort -R`/`jot`, `sysctl -n hw.ncpu`, `gtimeout`/perl, `shasum -a 256`, `md5 -q`, `vm_stat`, `lsof -iTCP -sTCP:LISTEN` |
| `base64 -w0` | yes | `base64` (no wrap by default) | `base64 \| tr -d '\n'` |
| `base64 -d` | yes | `base64 -D` (older) / `-d` (newer) | `base64 --decode` |
| `mktemp` | `mktemp -d` · `mktemp -d /tmp/x.XXXXXX` | `mktemp -d` · `mktemp -d -t x` | `mktemp -d /tmp/x.XXXXXX` |
| `split -d` (numeric suffixes) | yes | macOS 14+ only | `split -a 3` |
| `du -b`, `du --max-depth` | yes | `du -d` | `du -d 1` (both) |
| `wc -l` output | `12` | `      12` (padded) | `wc -l < f \| tr -d ' '` |
| `seq` | yes | yes (also `jot`) | `seq 1 10` |
| `echo -e`, `echo -n` | bash builtin ok | bash builtin ok; `/bin/sh` differs | `printf '%s\n'` |
| `awk` | gawk/mawk | BSD awk (no `gensub`, `strftime`) | POSIX awk subset |
| `tar` | GNU tar (`--wildcards`, `--transform`) | bsdtar (`-s` for rename) | `tar -czf`, `-xzf`, `-tzf` |
| `ps` | `ps -eo pid,args --sort=-pcpu` | `ps -eo pid,args -r` | `ps -eo pid,args` |
| open a URL/file | `xdg-open` | `open` | print it |
| clipboard | `xclip -selection clipboard`, `wl-copy` | `pbcopy` | write to a file |
| `sysctl` | `/proc/sys`, `sysctl -n kernel.hostname` | `sysctl -n hw.ncpu` | `getconf _NPROCESSORS_ONLN` |
| `/proc` | yes | no | `ps`, `lsof` |
| `ip`, `ss` | yes | `ifconfig`, `netstat`, `lsof` | `ifconfig` is deprecated on Linux but usually present |
| `strace` | yes | `dtruss` (sudo) | — |

Homebrew's `coreutils`, `gnu-sed`, `findutils`, `grep` packages install GNU versions with a `g`
prefix (`gsed`, `gdate`, `gfind`, `ggrep`, `gtimeout`). When the card lists them, use them rather
than contorting BSD syntax; do not add brew's `gnubin` to PATH on the user's behalf.

## Debian/Ubuntu naming

`fd` → `fdfind`, `bat` → `batcat`, `python` may be absent (`python3`), `pip` absent (`python3 -m pip`
or `pip3`), `node` may be `nodejs` on old images, `docker-compose` (v1, python) vs `docker compose`
(v2 plugin). Alpine: `apk add`, busybox userland, `bash` may be missing (`sh` = ash), `curl` may be
missing (`wget -qO-`), no `glibc` (prebuilt binaries may not run).

## Busybox (alpine, embedded, minimal images)

- Commands are symlinks to one binary with fewer flags: `find` lacks `-printf`/`-newermt`; `grep` lacks `-P`; `sed -i` works without a suffix but `-E` support varies; `xargs` lacks `-d`; `ps` is `ps -o pid,args`; `tar` lacks some long options; `wget` is busybox wget (`-qO-` works, `--no-check-certificate` exists); `diff` may lack `-u` on tiny images; `timeout` exists but with `-t` on old versions.
- No `bash`: write `sh`-compatible commands (`[ … ]`, no arrays, no `[[`, no `${var//a/b}`, no process substitution).
- Install what you need: `apk add --no-cache bash coreutils grep findutils` (ask in a non-disposable context).

## WSL

- Linux tools, Windows disks under `/mnt/c`; files there are slow and have odd permissions (`chmod` is a no-op unless metadata is enabled). Keep repos under the Linux home.
- `cmd.exe /c`, `powershell.exe -c`, `explorer.exe .` bridge to Windows; `wslpath -w PATH` converts paths. Line endings: `git config core.autocrlf` and `dos2unix` when scripts fail with `\r`.

## Windows shells (Git Bash / MSYS2, PowerShell, cmd)

- Git Bash gives GNU-ish tools (`sed -i`, `grep -P` work) but no `apt`, limited `ps`, path mangling of arguments starting with `/` (`MSYS_NO_PATHCONV=1`), and `python` is the Windows one.
- PowerShell is not bash: `Get-ChildItem`, `Select-String`, `Remove-Item -Recurse -Force`, `$env:VAR`, `;` to chain, `|` works on objects. `rg`, `fd`, `jq`, `gh` work unchanged. `where.exe TOOL` instead of `command -v`.
- Paths: quote them; backslashes in bash strings need `\\` or forward slashes (most tools accept `/`).
- Line endings and executable bits do not exist in the same way: `chmod +x` is a no-op; shebangs are honoured by Git Bash, not by cmd.

## Containers and CI

- Often root, so `sudo` is absent and unnecessary; `apt-get update` is required before `install` in a fresh image.
- No `$HOME` persistence, no editors, sometimes no `git`, `curl`, `ps`, `python3`: check the card first.
- `TERM` may be unset: tools that draw (`top`, `watch`, `less`) error out instead of hanging.
- File ownership mismatches with mounted volumes cause git's "dubious ownership" and permission errors; fix per command rather than with `chmod -R 777`.

## Portable one-liners worth remembering

```bash
nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || getconf _NPROCESSORS_ONLN
sha256sum f 2>/dev/null || shasum -a 256 f
(command -v gsed || command -v sed) >/dev/null   # pick GNU sed when installed
SED=$(command -v gsed || echo sed)
DATE=$(command -v gdate || echo date)
python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$p"    # realpath anywhere
```
