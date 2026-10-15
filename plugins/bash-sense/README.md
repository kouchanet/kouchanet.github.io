# bash-sense

AIエージェント（Claude Code）に「コマンド選びのセンス」を与える Claude Code プラグインです。
正しく動くだけのコマンドと、「そうそう、そのコマンドが欲しかった」と思えるコマンドの差を埋めます。

A Claude Code plugin that gives the agent good taste in Bash: the command a seasoned engineer would
type, not merely one that works.

## なぜ作ったか / Why

モデルの性能が上がり、コマンドは「正しく」実行されるようになりました。それでも
`grep -rn foo .` で `node_modules` の山を吐き出す、`cat package-lock.json` で 3,000 行を読む、
`npm run dev` を前景で実行してタイムアウトする、pnpm のリポジトリで `npm install` する、
`git reset --hard` で未コミットの作業を消す……「なんか違う」の正体はだいたいこのどれかです。

このプラグインは三層でそれを直します。

1. **環境カード（SessionStart フック）**: セッション開始時に、インストール済みツール（rg/fd/jq/gh…）、
   sed/grep/find/date が GNU か BSD か、プロジェクトのパッケージマネージャと scripts、git の状態を
   コンテキストに注入します。存在しないツールを提案したり、macOS で `sed -i` を間違えたりしなくなります。
2. **スキル `bash-sense`**: 経験豊富なエンジニアが打つコマンドの「型」をまとめた知識。7つの習慣、
   意図→コマンド表、非対話形式、安全の階段、プラットフォーム差、失敗時の復旧手順。
   `/bash-sense:review <command>` でコマンドのレビューもできます。
3. **PreToolUse アドバイザー**: Bash ツールの実行直前に毎回コマンドを静的に検査します。
   - **deny**（止めて、より良い形を提示）: ハングする／TTY が要る（`tail -f`, `watch`, `vim`, 素の `python`,
     `npm run dev` の前景実行, `docker exec -it`）、明らかに劣るツール（rg があるのに `grep -r`）、
     ロックファイルと違うパッケージマネージャ、OS に無いコマンド（`python` が無い環境、macOS の `grep -P`）。
   - **ask**（ユーザーに確認）: 取り返しのつかない操作（`git reset --hard`, `git clean -f`, force push,
     `rm -rf` の対象が成果物でない、`pkill node`, `sudo`, `docker system prune`, `DROP TABLE`, `npm publish`,
     `cat .env`, 素の `env`）。
   - **advise**（実行はするが一言添える）: 出力が無制限（素の `git log`, 巨大ファイルの `cat`）、
     `cat f | grep`, `which`, バッククォート, `find … | xargs` の `-0` 抜け, `cd dir && …`, `git add .`,
     `curl -k`, `--no-verify` など。

アドバイザーは Python 3 で動き、1 回あたり約 50〜70 ms です。Python が無い環境では何もせずに通します（fail-open）。

## インストール / Install

Claude Code 2.1.275 以降では 1 行です:

```
/plugin install bash-sense --marketplace kouchaweb/kouchaweb.github.io
```

古いバージョンでは 2 段階:

```
/plugin marketplace add kouchaweb/kouchaweb.github.io
/plugin install bash-sense@kouchaweb
```

ローカルで試す（インストールせずにこのセッションだけ）:

```bash
git clone https://github.com/kouchaweb/kouchaweb.github.io
claude --plugin-dir ./kouchaweb.github.io/plugins/bash-sense
```

必要なもの: `python3`（3.8 以降）。無い場合はスキルと環境カード（最小版）だけが動きます。

## 使い方 / Usage

- 何もしなくても動きます。セッション開始時にカードが出て、以後の Bash コマンドが検査されます。
- `/bash-sense:review git log` のように渡すと、そのコマンドを 7 つの習慣に照らしてレビューします。
- `/bash-sense:env` で環境カードを再表示します（ツールを入れた後、別ディレクトリに移った後など）。
- 手元でコマンドを検査する: `python3 plugins/bash-sense/scripts/bash_sense.py --check "grep -rn foo ."`

### 判定の例 / Examples

| 入力 | 判定 | 提示 |
|---|---|---|
| `grep -rn "TODO" .` | deny | `rg -n "TODO"`（.gitignore を尊重、node_modules を掘らない） |
| `npm run dev` | deny | `nohup npm run dev > /tmp/dev.log 2>&1 &` のうえでポーリング |
| `git commit` | deny | `git commit -m "..."`（エディタが開けない） |
| `npm install` （pnpm-lock.yaml がある） | deny | `pnpm install` |
| `sed -i '' 's/a/b/' f` （Linux） | deny | `sed -i 's/a/b/' f` |
| `git reset --hard` | ask | 先に `git stash push -u -m 'before reset'` |
| `rm -rf src` | ask | `ls -la src` で確認、`mv src /tmp/trash-…/` なら戻せる |
| `cat .env` | ask | `cut -d= -f1 .env` |
| `git log` | advise | `git log --oneline -n 20` |
| `cat package-lock.json` | advise | `jq -r '.packages["node_modules/NAME"].version' package-lock.json` |
| `ps aux \| grep node` | advise | `pgrep -af node` |
| `ls -la`, `rg -n x`, `git status --short` | — | 何も言いません |

## 設定 / Configuration

環境変数（Claude Code の `settings.json` の `env`、またはシェル）で調整できます。

| 変数 | 効果 |
|---|---|
| `BASH_SENSE=off` | フックを完全に無効化 |
| `BASH_SENSE_STRICT=1` | advise を deny に昇格（厳格モード） |
| `BASH_SENSE_DEBUG=1` | `$TMPDIR/bash-sense-debug.log` に判定を記録 |

1 回だけ素通しにしたいときは、コマンド末尾に `# bash-sense: off` を付けるか `BASH_SENSE=off cmd` と書きます
（`rm -rf /` のような「never」級の判定だけは外せません）。`ask` はユーザーの確認なので、素通しにはなりません。

## 構成 / Layout

```
plugins/bash-sense/
├── .claude-plugin/plugin.json
├── hooks/hooks.json              # SessionStart + PreToolUse(Bash)
├── hooks/session-start.sh        # 環境カード
├── hooks/pre-bash.sh             # アドバイザー起動（python3 が無ければ素通し）
├── scripts/bash_sense.py         # 解析エンジン（トークナイザ、環境検出、約 70 ルール）
├── skills/review/SKILL.md    # スキル本体 + references/（9 ファイル）
├── skills/env/SKILL.md           # /bash-sense:env
└── tests/test_lint.py            # python3 -m unittest discover -s tests
```

## 設計メモ / Design notes

- **なぜ Plugin か**: 「センス」は知識（Skill）だけでは定着しません。環境を知る（SessionStart）、
  実行の瞬間に止める／添える（PreToolUse）、の三つが揃って初めてエージェントの振る舞いが変わります。
  MCP サーバーは「モデルが呼ぶと決めたときだけ」動くので、センスが無い瞬間には呼ばれません。
- **deny は少なく、確信があるときだけ**: 誤検知で止める道具は信頼されません。deny は
  「ほぼ確実にハングする／壊れる／劣る」場合に限り、スタイルの指摘は advise に留めています。
- **ask は破壊的・外向きの操作だけ**: 判断はユーザーのものです。
- **環境を見る**: `rg` が無ければ `git grep` を勧め、macOS なら BSD の形を、Debian なら `fdfind` を出します。
- **壊れない**: 解析に失敗したら黙って通します。フックが原因で作業が止まることはありません。

## 開発 / Development

```bash
cd plugins/bash-sense
python3 -m unittest discover -s tests -v
python3 scripts/bash_sense.py --check "find / -name x"
python3 scripts/bash_sense.py --parse "cd a && b | c"
python3 scripts/bash_sense.py --envcard
claude plugin validate . --strict
```

## License

MIT
