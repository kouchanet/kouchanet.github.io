# Safety: preview, reversible, ask, never

The point is not timidity. It is that an engineer with taste knows exactly what a destructive
command will do before running it, and has made the result recoverable when it matters.

## The ladder

1. **Preview**: a dry run or a listing that shows what would be touched.
2. **Reversible**: a form that keeps a way back (stash, backup branch, move to trash, `--force-with-lease`).
3. **Ask**: the user decides, with the preview in front of them.
4. **Never**: no task justifies it.

## Catalogue

| Command | Risk | Preview | Reversible / safer form | Tier |
|---|---|---|---|---|
| `rm -rf PATH` | gone for good | `ls -la PATH`, `du -sh PATH` | `d=/tmp/trash-$(date +%s); mkdir -p "$d" && mv PATH "$d"/`; artifacts (`node_modules`, `dist`, `target`, `.venv`, caches) are fine to delete | ask (artifacts: ok) |
| `rm -rf $VAR/sub` | `$VAR` empty → `rm -rf /sub` | `echo "$VAR"` | `rm -rf "${VAR:?}/sub"` | never unguarded |
| `rm -rf /`, `~`, `*`, `.`, `..`, `/usr` … | the machine / all work | — | — | never |
| `rm -rf .git` | all history and remotes | `git log --oneline \| wc -l` | fresh clone elsewhere | ask |
| `find … -delete`, `find … -exec rm` | mass delete | the same `find` with `-print` | `-exec mv {} /tmp/trash/ \;` | preview, then ask |
| `git reset --hard` | uncommitted work | `git status --short`, `git stash list` | `git stash push -u -m why`; `--soft`/`--mixed` | ask |
| `git checkout -- .`, `git restore .` | uncommitted edits | `git diff --stat` | `git stash push -m why -- path` | ask |
| `git clean -f` / `-fdx` | untracked / ignored files (`.env`!) | `git clean -nd` / `-ndx` | `-X` (ignored only), `-e .env` keeps it even with `-x` | ask |
| `git push --force` | collaborators' commits | `git log origin/BR..BR` | `--force-with-lease`; never to main without the user | ask |
| `git branch -D` | unmerged commits | `git log BR --not main --oneline` | `-d` | ask |
| `git stash drop/clear` | stashed work | `git stash show -p` | `git stash branch` | ask |
| `git filter-repo`, `filter-branch`, `rebase --root` | all history | — | `git branch backup-$(date +%s)` | ask |
| `git commit --amend` after push | collaborators' history | `git status -sb` (ahead/behind) | new commit | advise |
| `git config --global`, `~/.gitconfig` edits | the user's identity/aliases | `git config --global --list` | repo-local config, `-c k=v` | ask |
| `chmod -R 777`, `chmod 777` | security hole; never the fix | `ls -la`, `id` | `chmod u+x`, `755`/`644`, `chown -R $(id -u) PATH` | ask / advise |
| `chown -R … /`, `chmod -R … /` | unbootable system | — | — | never |
| `curl … \| sh`, `wget -O- … \| bash` | runs unread remote code | `curl -fsSLo /tmp/i.sh URL; head -n 60 /tmp/i.sh` | run the file after reading | ask |
| `sudo CMD` | root on the user's machine; no password prompt possible | `id`, `sudo -n true` | do it inside the project; or the user runs it | ask (drop `sudo` when already root) |
| `npm i -g`, `pip install` (system), `brew install`, `apt-get install`, `cargo install`, `go install` | machine state | `command -v TOOL` | `npx -y`, `pnpm dlx`, `uvx`, `pipx run`, `go run PKG@ver`, dev dependency | advise (container: routine) |
| `pkill NAME`, `killall NAME` | the user's own editor/server with that name | `pgrep -af '[N]AME'` | `kill PID` of the one you started, `pkill -f '[e]xact cmd'` (bracketed so the tool's own shell does not match) | ask |
| `kill -9` | no cleanup (locks, temp files, children) | `kill -0 PID` | `kill PID; sleep 2; kill -9 PID` only if alive | advise |
| `kill -9 -1`, fork bombs, `dd of=/dev/…`, `mkfs`, `> /dev/sda`, `shred` | hardware/data | — | — | never |
| `docker system/volume prune`, `docker rm -f $(docker ps -aq)`, `rmi $(docker images -q)` | other projects' containers, images, data | `docker ps -a`, `docker volume ls` | remove by name; `docker compose down` | ask |
| `docker compose down -v` | the project's databases | `docker volume ls` | `docker compose down` (keeps volumes) | ask |
| `kubectl delete …`, `--all`, namespaces | live resources | `--dry-run=client -o yaml`, `kubectl get` | scoped names + namespace | ask |
| `terraform apply -auto-approve`, `destroy` | real infrastructure | `terraform plan -out=tf.plan` | `apply tf.plan` after the user saw it | ask |
| `aws s3 rm --recursive`, `delete-*`, `gcloud … delete`, `az … delete` | cloud data | `--dryrun`, `ls` | versioning/backups first | ask |
| `DROP`, `TRUNCATE`, `DELETE FROM t` (no `WHERE`), `db:reset`, `migrate:fresh`, `FLUSHALL` | rows gone | `SELECT COUNT(*) … WHERE …`, `pg_dump`, snapshot | transaction with `ROLLBACK` first; disposable DB via `DATABASE_URL` | ask |
| `npm publish`, `cargo publish`, `twine upload`, `gem push` | public, irreversible | `--dry-run`, `npm pack` | test registries | ask |
| `gh pr merge`, `gh release create`, `gh repo delete` | shared/public state | `gh pr checks`, `gh pr view` | leave to the user unless asked | ask |
| `crontab -r`, `systemctl stop/disable/mask`, `launchctl unload`, `iptables -F`, `reboot` | machine behaviour | `crontab -l`, `systemctl status --no-pager` | `crontab -l > backup`; `--user` units | ask |
| writes to `~/.bashrc`, `~/.zshrc`, `~/.ssh/*`, `/etc/*` | the user's environment | `cat` the relevant part | project-local config, tell the user what to add | ask |
| `> FILE` on a tracked file | clobbers content | `git diff FILE` later | `>>`, or write to a temp file and `mv` | advise |
| `sed -i` across many files | hard to review | `rg -n OLD`, `sed -n 's/OLD/NEW/p'` | `rg -l OLD \| xargs sed -i`, then `git diff --stat` | preview |
| `git add -A` / `.` / `commit -a` | stray files in the commit | `git status --short` | stage by path | advise |
| `--no-verify`, `HUSKY=0` | skipped checks | run the hook's command | fix the finding | advise |
| `curl -k`, `--no-check-certificate`, `NODE_TLS_REJECT_UNAUTHORIZED=0`, `GIT_SSL_NO_VERIFY` | silent MITM, hides proxy problems | `curl -sSv URL 2>&1 \| tail` | `--cacert`, `SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS` | advise |

## Guarding variables and paths

```bash
rm -rf "${BUILD_DIR:?BUILD_DIR must be set}/out"   # aborts if unset/empty
[ -d "$dir" ] && [ "$dir" != "/" ] || exit 1          # belt and braces in scripts
cd "$dir" || exit 1                                   # never let a failed cd run the next line elsewhere
set -euo pipefail                                     # scripts: stop on errors, unset vars, failed pipes
```

Recursive flags (`-r`, `-R`) deserve a second look every time: `rm -rf`, `chmod -R`, `chown -R`,
`cp -r` onto an existing dir, `rsync --delete`, `scp -r`.

## Secrets hygiene

- Reading: never `cat .env`, `cat ~/.aws/credentials`, `cat id_rsa`. Names only: `cut -d= -f1 .env`; presence: `grep -c '^NAME=' .env`; one prefix if truly needed: `grep '^NAME=' .env | cut -c1-12`.
- Environment: never bare `env`/`printenv`/`set`. `env | cut -d= -f1 | sort`, `printenv NAME | cut -c1-4`, `[ -n "$NAME" ] && echo set`.
- Command lines are logged (shell history, `ps`, transcripts): `-H "Authorization: Bearer $TOKEN"`, `--netrc`, `-u "$USER:$PASS"` from variables; never a literal `ghp_…`, `sk-…`, `AKIA…`.
- Output: redirect or filter when a tool echoes secrets (`docker inspect`, `kubectl get secret -o yaml`, `aws sts get-session-token`); `| jq 'del(.Env)'`, `| sed 's/\(token=\)[^&]*/\1…/'`.
- Files you create: do not write secrets into the repo (`.env` is ignored for a reason); `chmod 600` for anything private under `~`.
- Git history: `git log -p` and `git show` can reveal past secrets; do not paste them into messages or PRs.

## When the user asks for the destructive thing anyway

Do it, in the safest form that still does what they asked: preview once, make the reversible
copy when it is cheap, then run the real command and say exactly what it did.
