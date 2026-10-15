# Git with taste

Git is where an agent's command choice is judged most harshly: a careless `reset --hard` destroys
an afternoon of someone's work, a `git add .` ships a `.env`, a bare `git log` floods the context.
This file covers inspection, non-interactive editing of history, and the ask-first list.

## Inspection

- Start every git task with `git status --short --branch` and `git log --oneline -n 10`. They are cheap and they set the frame.
- Diffs: `--stat` first, then `-- path`. `git diff` (unstaged), `git diff --cached` (staged), `git diff HEAD` (both), `git diff main...HEAD` (since branching; three dots), `git diff main..HEAD` (two dots: between tips).
- History bounded and focused: `git log --oneline -n 20`, `-- path`, `--author`, `--since='3 days ago'`, `--grep='fix'`, `-S'needle'` (commits that add/remove the text), `-G'regex'`, `--follow -- path` (through renames), `--no-merges`, `--first-parent`.
- One commit: `git show --stat HEAD`, `git show HEAD -- path`, `git show HEAD:path/file | head -n 40` (a file at a revision, no checkout).
- Blame a range, not a file: `git blame -L 120,140 -- file`; `git blame -w -M -C` ignores whitespace and follows moves.
- Who touched this recently: `git log -n 5 --format='%h %ad %an %s' --date=short -- path`.
- Branches: `git branch --show-current`, `git branch -a --sort=-committerdate | head -n 15`, `git branch --merged main`, `git for-each-ref --sort=-committerdate --format='%(refname:short) %(committerdate:short)' refs/heads | head`.
- Remote state without touching the tree: `git fetch origin`, `git log --oneline HEAD..origin/main` (what they have), `git log --oneline origin/main..HEAD` (what you have).
- Machine-readable: `git status --porcelain`, `git log --format='%H%x09%s'`, `git diff --name-only`, `git diff --name-status`, `git rev-parse --abbrev-ref HEAD`, `git rev-parse --show-toplevel`.
- Ignored or not: `git check-ignore -v path`. Untracked: `git ls-files --others --exclude-standard`.
- No pager games are needed when stdout is a pipe; if a tool forces one, `GIT_PAGER=cat` or `git --no-pager`.

## Committing without an editor

- `git add path1 path2` by name. `git add -A`/`git add .` is for a fresh tree you fully own; check `git status --short` first either way. `git add -p` is interactive: edit the file instead so only the wanted change remains, or split with `git stash push -- path`.
- `git commit -m "subject" -m "body paragraph"`; multi-line body with a quoted heredoc:

  ```bash
  git commit -F - <<'EOF'
  feat(parser): handle heredocs

  Bodies are skipped so their contents are not linted as commands.
  EOF
  ```

- `git commit --amend --no-edit` keeps the message; `--amend -m "new"` replaces it. Amend only commits that have not been pushed (or on your own branch with `--force-with-lease`).
- Hooks failing: run the hook's command yourself (`npm run lint`, `pre-commit run --all-files`) and fix what it says. `--no-verify` is a user's decision, not a default.
- Author identity missing: `git -c user.name='Name' -c user.email='a@b' commit …` for one commit, or `git config user.name …` (repo-local). `--global` touches the user's machine: ask.
- Sign-offs/trailers: `git commit --trailer 'Co-authored-by: …'` (git ≥ 2.32) or put them at the end of the body.

## Branching and syncing

- `git switch -c name` (new), `git switch name`, `git switch -` (back). `git checkout` still works but `switch`/`restore` say what they mean.
- Update a feature branch: `git fetch origin main && git rebase origin/main` on a branch only you push; `git merge --no-edit origin/main` on a shared branch (a merge keeps everyone's checkout valid).
- Push: `git push -u origin BRANCH` first time. Retry a few times with backoff on network errors; do not loop forever.
- Stash with a message, always: `git stash push -u -m 'wip: why'`; `git stash list`; `git stash pop` (or `apply` to keep the stash).
- Worktrees for parallel work: `git worktree add ../repo-feature feature`; `git worktree list`; `git worktree remove ../repo-feature`.

## History surgery without an interactive rebase

| Want | Non-interactive form |
|---|---|
| squash the last N commits | `git reset --soft HEAD~N && git commit -m "…"` |
| reorder / drop / reword | `GIT_SEQUENCE_EDITOR='sed -i "s/^pick \(abc123\)/drop \1/"' git rebase -i BASE` (scripted todo), or cherry-pick onto a fresh branch in the order you want |
| edit one old commit's message | `git commit --fixup=SHA` then `GIT_SEQUENCE_EDITOR=true git rebase -i --autosquash BASE` |
| move commits onto another base | `git rebase --onto NEW_BASE OLD_BASE BRANCH` |
| undo a commit, keep the work | `git reset --soft HEAD~1` (staged) · `git reset HEAD~1` (unstaged) |
| undo a pushed commit | `git revert SHA` (new commit; safe on shared branches) |
| split a commit | `git reset HEAD~1`, then `git add` + `git commit` in pieces |
| abort anything half done | `git rebase --abort`, `git merge --abort`, `git cherry-pick --abort` |
| find lost work | `git reflog | head -n 30`, then `git branch rescue SHA` |

Before rewriting: `git branch backup-$(date +%s)` costs nothing and makes every mistake recoverable.

## Resolving a merge conflict

```bash
git merge --no-edit origin/main 2>&1 | tail -n 20   # or rebase
git diff --name-only --diff-filter=U                 # conflicted files
rg -n '^(<{7}|={7}|>{7})' -- FILE                    # markers
# edit FILE (keep both sides' intent), then:
git add FILE && git merge --continue                 # or: GIT_EDITOR=true git rebase --continue
```

Generated files (lockfiles, snapshots) are regenerated with the project's tool (`pnpm install`,
`cargo update -p x`), never hand-merged.

## Ask-first list (destructive or shared)

| Command | What it loses | Safer form |
|---|---|---|
| `git reset --hard` | every uncommitted change | `git stash push -u -m 'before reset'` first; `--soft`/`--mixed` to keep work |
| `git checkout -- .` / `git restore .` | uncommitted edits in those paths | `git stash push -m wip -- path` · `git diff -- path` to see what goes |
| `git clean -f` (`-x`: also ignored files such as `.env`) | untracked files | `git clean -nd` / `-ndx` preview; `-X` for ignored only |
| `git push --force` | other people's commits on the branch | `--force-with-lease`; never on main/master without the user |
| `git branch -D` | unmerged commits | `git branch -d` refuses when unsafe; `git log BRANCH --not main --oneline` |
| `git stash drop` / `clear` | stashed work | `git stash show -p` first |
| `git push origin --delete BRANCH` | the remote branch | check `git branch -r --merged` |
| `git filter-branch` / `filter-repo` / `rebase --root` | all history | backup branch + coordination |
| `git commit --amend` on pushed commits | linear history for collaborators | new commit, or `--force-with-lease` on your own branch |
| `git config --global …` | the user's own settings | repo-local `git config …`, or `-c key=val` per command |
| `git gc --prune=now`, `reflog expire --expire=now` | the safety net | let git decide |

## Odds and ends

- `git -C DIR …` instead of `cd DIR && git …`.
- `git log` inside `$(…)` or pipes is fine; the pager only appears on a TTY.
- "dubious ownership": the repo is owned by another uid (containers). `git config --global --add safe.directory PATH` changes global config (ask); per-session: `GIT_CONFIG_GLOBAL=/tmp/gitconfig git config --global --add safe.directory PATH` with that variable set for later commands, or `git -c safe.directory=PATH …`.
- Credentials: never embed tokens in remote URLs; `GIT_TERMINAL_PROMPT=0` makes a missing credential fail fast instead of hanging; `gh auth setup-git` or a credential helper is the user's job.
- Large output: `git diff --stat | tail -n 1` gives totals; `git diff --shortstat`.
- LFS/submodules: `git submodule update --init --recursive` (ask before `--remote`); `git lfs ls-files | head`.
