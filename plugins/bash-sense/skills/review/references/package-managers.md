# Package managers, task runners, version managers

The fastest way to look careless is `npm install` in a pnpm repository or `pip install` into the
system interpreter of someone's laptop. Detect, then follow.

## Detection, in order of authority

1. The lockfile at the repo root (or the nearest `package.json` directory in a monorepo):
   `pnpm-lock.yaml` → pnpm · `yarn.lock` → yarn · `bun.lock`/`bun.lockb` → bun · `package-lock.json`/`npm-shrinkwrap.json` → npm ·
   `uv.lock` → uv · `poetry.lock` → poetry · `Pipfile(.lock)` → pipenv · `pdm.lock` → pdm · `requirements*.txt` → pip in a venv ·
   `Cargo.lock` → cargo · `go.sum` → go · `Gemfile.lock` → bundler · `composer.lock` → composer · `mix.lock` → mix · `pubspec.lock` → dart/flutter · `Package.resolved` → swift.
2. `"packageManager": "pnpm@9.1.0"` in `package.json` (corepack) overrides a stale lockfile.
3. CI config (`.github/workflows/*.yml`) and the README show the maintainers' commands when the tree is ambiguous.
4. Version pins: `.nvmrc`/`.node-version` (nvm/fnm/volta), `.tool-versions` (asdf/mise), `mise.toml`, `.python-version` (pyenv/uv), `rust-toolchain(.toml)`, `.ruby-version`, `.java-version`, `go.mod`'s `go 1.22`.

Quick look: `ls -a | rg -n 'lock|package.json|pyproject|Makefile|justfile|Taskfile|\.tool-versions|\.nvmrc'` and `jq -r '.packageManager, (.scripts|keys[])' package.json`.

## JavaScript / TypeScript

| Task | npm | pnpm | yarn (classic / berry) | bun |
|---|---|---|---|---|
| install from lockfile (CI, reproducible) | `npm ci` | `pnpm install --frozen-lockfile` | `yarn install --frozen-lockfile` / `yarn install --immutable` | `bun install --frozen-lockfile` |
| install (may update lock) | `npm install` | `pnpm install` | `yarn install` | `bun install` |
| add | `npm install PKG` · `-D` | `pnpm add PKG` · `-D` | `yarn add PKG` · `-D` | `bun add PKG` · `-d` |
| remove | `npm uninstall PKG` | `pnpm remove PKG` | `yarn remove PKG` | `bun remove PKG` |
| run script | `npm run NAME` | `pnpm NAME` / `pnpm run NAME` | `yarn NAME` | `bun run NAME` |
| pass args to script | `npm run test -- --run` | `pnpm test --run` | `yarn test --run` | `bun run test --run` |
| run a local bin | `npx NAME` / `npm exec NAME` | `pnpm exec NAME` | `yarn NAME` / `yarn exec NAME` | `bunx NAME` |
| one-off remote package | `npx -y PKG` | `pnpm dlx PKG` | `yarn dlx PKG` (berry) | `bunx PKG` |
| list / why | `npm ls PKG` · `npm ls --depth=0` | `pnpm ls` · `pnpm why PKG` | `yarn list --depth=0` · `yarn why PKG` | `bun pm ls` |
| outdated / update | `npm outdated` · `npm update` | `pnpm outdated` · `pnpm update` | `yarn outdated` · `yarn upgrade` | `bun outdated` · `bun update` |
| workspaces | `npm run build -w pkg-a` · `--workspaces` | `pnpm --filter pkg-a build` · `-r` | `yarn workspace pkg-a build` · `yarn workspaces foreach` | `bun run --filter pkg-a build` |
| quiet | `npm --silent run x` · `npm ci --no-audit --no-fund` | `pnpm --reporter=silent` | `yarn -s` | `bun --silent` |

- `npm test` is `npm run test`; `bun test` is bun's *own* test runner, so in a vitest/jest project use `bun run test`.
- `vitest`/`jest` in non-TTY mode run once, but pass `vitest run` / `jest --watchAll=false` explicitly; never `--watch`.
- Monorepos: `turbo run build --filter=pkg`, `nx run pkg:build`, `lerna run build --scope pkg`; prefer the root script (`pnpm build`) which usually wraps them.
- `node_modules/.bin` is on the PATH inside scripts and under `npx`/`pnpm exec`; from Bash directly use `npx NAME` rather than `./node_modules/.bin/NAME`.
- `corepack enable` and `nvm use` change the user's shell setup: ask. Read `.nvmrc` and say which version the project expects.
- Deno: `deno task NAME`, `deno run -A file.ts`, `deno test`; no node_modules unless `nodeModulesDir`.

## Python

| Task | uv | poetry | pipenv | pdm | plain venv + pip |
|---|---|---|---|---|---|
| create env + install | `uv sync` | `poetry install` | `pipenv install --dev` | `pdm install` | `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` |
| add | `uv add PKG` · `--dev` | `poetry add PKG` · `--group dev` | `pipenv install PKG` · `--dev` | `pdm add PKG` | `.venv/bin/pip install PKG` (and pin it in requirements) |
| run | `uv run CMD` | `poetry run CMD` | `pipenv run CMD` | `pdm run CMD` | `.venv/bin/python CMD` · `.venv/bin/pytest` |
| ad hoc install into the env | `uv pip install PKG` | `poetry run pip install PKG` | `pipenv run pip install PKG` | `pdm run pip install PKG` | `.venv/bin/pip install PKG` |
| one-off tool | `uvx TOOL` | `pipx run TOOL` | `pipx run TOOL` | `pdm run TOOL` | `pipx run TOOL` |
| python version | `uv python install 3.12` · `.python-version` | `poetry env use 3.12` | `pipenv --python 3.12` | `pdm use 3.12` | `pyenv local 3.12` (ask) |
| lock / export | `uv lock` · `uv export > requirements.txt` | `poetry lock` · `poetry export` | `pipenv lock` | `pdm lock` | `pip freeze > requirements.txt` |

- `source .venv/bin/activate` does not survive between Bash calls: call `.venv/bin/python`, `.venv/bin/pip`, `.venv/bin/pytest` by path, or use the runner (`uv run`).
- `python` vs `python3`: check the card; `python3 -m pip` beats a bare `pip` (same interpreter guaranteed); `python3 -m pytest` beats `pytest` when the PATH is unclear.
- `externally-managed-environment`: the distro forbids global pip. Make a venv; `--break-system-packages` only in a disposable container.
- Tests and tools: `pytest -q -x` (stop at first failure), `pytest -q tests/test_x.py::test_name`, `pytest -q -k 'expr'`, `python -m unittest discover -s tests`; `ruff check . && ruff format --check .`, `mypy .`, `pyright`.
- Django/Flask: `python manage.py …` under the runner (`uv run python manage.py migrate`); `runserver`/`flask run` are servers: background them.
- Jupyter notebooks: `jupyter nbconvert --to script nb.ipynb` or `jupyter execute nb.ipynb` rather than `jupyter lab`.

## Rust

- `cargo check` (fast), `cargo build`, `cargo build --release`, `cargo build --locked` (CI), `cargo test -q`, `cargo test -q -- --nocapture test_name`, `cargo clippy -q -- -D warnings`, `cargo fmt --check` / `cargo fmt`, `cargo run -- ARGS`, `cargo add PKG` / `cargo add --dev PKG`, `cargo tree -i PKG`, `cargo update -p PKG`.
- Watchers (`cargo watch`) and `cargo run` of servers: background them.
- Toolchain: `rust-toolchain.toml` wins; `rustup` changes are the user's.
- Output: `cargo` prints progress to stderr; `2>&1 | tail -n 40` for builds; `CARGO_TERM_COLOR=never`.

## Go

- `go build ./...`, `go test ./... 2>&1 | tail -n 40`, `go test -run TestName ./pkg`, `go test -race ./...`, `go vet ./...`, `gofmt -l .` (lists unformatted) / `gofmt -w .`, `staticcheck ./...` when installed, `go mod tidy` (changes go.mod/go.sum: show the diff), `go mod download`, `go run .`, `go run ./cmd/x`, `go get PKG@v1.2.3`, `go install PKG@latest` (installs to `$GOPATH/bin`: a machine change, ask or use `go run PKG@latest`).
- `go -C DIR …` (go ≥ 1.20) instead of `cd`.

## Ruby

- `bundle install`, `bundle exec rspec`, `bundle exec rubocop`, `bundle exec rails …`, `bundle exec rake -T` (list tasks); never bare `gem install` into the system; `.ruby-version` names the expected Ruby.
- Rails servers (`rails s`) and consoles (`rails c`) are interactive/long-running: `rails runner 'puts User.count'` for one-offs, background the server.
- `rails db:reset`, `db:drop`, `db:schema:load` destroy data: ask.

## PHP

- `composer install` (lockfile), `composer require PKG`, `composer run NAME`, `vendor/bin/phpunit`, `vendor/bin/phpstan analyse`, `php artisan …` (Laravel; `migrate:fresh` destroys data: ask), `php -l file.php` (syntax), `php -S` is a server.

## JVM

- Wrappers first: `./gradlew` and `./mvnw` pin the build tool version; use them when they exist.
- Gradle: `./gradlew build -q --console=plain`, `./gradlew test --tests 'com.x.MyTest'`, `./gradlew tasks --all | head`, `--offline` when deps are cached, `-x test` to skip tests when the user asked for build only.
- Maven: `./mvnw -q -B test`, `-Dtest=MyTest`, `-DskipTests package`, `-o` offline, `dependency:tree | head -n 40`.
- Kotlin/Android: `./gradlew assembleDebug -q`, `./gradlew lint`.

## .NET

- `dotnet restore`, `dotnet build --nologo -v q`, `dotnet test --nologo -v q`, `dotnet run --project src/App`, `dotnet format --verify-no-changes`, `dotnet add package PKG`, `dotnet watch` is a watcher.

## Elixir, Dart/Flutter, Swift

- `mix deps.get`, `mix compile`, `mix test`, `mix format --check-formatted`, `mix credo`; `mix phx.server`/`iex -S mix` are long-running.
- `dart pub get`, `dart analyze`, `dart test`, `flutter pub get`, `flutter test`, `flutter analyze`; `flutter run` is interactive.
- `swift build`, `swift test`, `swift package resolve`; `xcodebuild -scheme X test -quiet | tail -n 40`.

## Task runners and project entry points

| File | List | Run | Preview |
|---|---|---|---|
| `Makefile` | `make -qp 2>/dev/null \| awk -F: '/^[a-zA-Z0-9_-]+:([^=]\|$)/{print $1}' \| sort -u \| head -n 30` · `grep -E '^[a-zA-Z0-9_-]+:' Makefile` | `make NAME` · `make -C DIR NAME` · `make -j"$(nproc)"` | `make -n NAME` |
| `justfile` | `just --list` | `just NAME` | `just --dry-run NAME` |
| `Taskfile.yml` | `task --list` | `task NAME` | `task --dry NAME` |
| `package.json` | `jq -r '.scripts' package.json` | `npm run NAME` (etc.) | read the script body |
| `.pre-commit-config.yaml` | `cat .pre-commit-config.yaml \| head -n 40` | `pre-commit run --all-files` · `pre-commit run HOOK --files a b` | |
| `Procfile` | `cat Procfile` | `foreman start` / `overmind start` are long-running | |
| `docker-compose.yml` / `compose.yaml` | `docker compose config --services` | `docker compose run --rm SVC CMD` | `docker compose config` |
| `Rakefile` | `bundle exec rake -T` | `bundle exec rake NAME` | |
| `tox.ini` / `noxfile.py` | `tox -l` · `nox -l` | `tox -e py312` · `nox -s tests` | |

Prefer these over the raw tool whenever they exist: they carry the maintainers' flags and ordering.

## Version managers (change the user's shell: observe, don't reconfigure)

- Read: `cat .nvmrc .node-version .tool-versions .python-version 2>/dev/null`, `node --version`, `python3 --version`.
- `nvm`, `pyenv`, `rbenv`, `asdf`, `mise`, `volta`, `fnm` are shell functions or shims; `nvm use` in one Bash call changes nothing for the next. If the wrong version is active, say so; use `mise exec -- CMD` / `asdf exec CMD` / `volta run --node 20 CMD` / `uv run --python 3.12 CMD` for a single command, or ask the user to switch.
- Installing a toolchain (`nvm install`, `rustup toolchain install`, `pyenv install`) downloads hundreds of MB and changes the machine: ask unless the task is clearly about that.

## System package managers

- `apt-get install -y PKG` with `DEBIAN_FRONTEND=noninteractive` (scripts), `apt-get update` first in a fresh container; `apt` is for humans (unstable CLI, warns in scripts).
- `brew install PKG` (macOS/Linuxbrew), `brew list PKG`, `brew --prefix PKG`.
- `apk add --no-cache PKG` (alpine), `dnf install -y`, `pacman -S --noconfirm`.
- All of these change the machine; inside a disposable container they are routine, on a user's laptop they need a yes.
