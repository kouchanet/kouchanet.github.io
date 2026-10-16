# Text processing: the right tool for each job

| Job | Tool | Not |
|---|---|---|
| find lines | `rg` / `grep` | `awk '/pat/'` (fine, but less readable), `sed -n '/pat/p'` |
| pick columns | `awk '{print $2}'`, `cut -d: -f1` | `sed` with capture groups for simple fields |
| edit lines in place | `sed -i` (small, mechanical), the Edit tool (structural) | `perl -pi` unless you need Perl regex |
| transform fields with logic | `awk` | chains of `cut \| sed \| tr` |
| JSON | `jq` | `grep`/`sed`/`awk` |
| YAML | `yq` | `grep` |
| CSV with quotes | `python3 -c 'import csv…'`, `mlr`, `csvkit` | `cut -d,` (breaks on quoted commas) |
| count / group | `sort \| uniq -c \| sort -rn` | `awk` histograms (fine for larger logic) |
| set operations | `comm`, `sort -u`, `join` | nested greps |
| one-char translation | `tr` | `sed` |
| wrap / columns / alignment | `fold`, `column -t`, `paste` | manual spacing |
| anything multi-line or stateful | `awk`, `python3` | heroic `sed` |

## grep / rg

```bash
rg -n 'pat' [path]           # line numbers; recursive by default; .gitignore respected
rg -n -F 'a.b(c)'            # literal
rg -n -i -w 'word'           # case-insensitive, whole word
rg -n -t py 'import x'       # by type (rg --type-list)
rg -n -g '*.{ts,tsx}' -g '!*.test.*' 'useEffect'
rg -l 'pat' ; rg -c 'pat'    # files only; counts
rg -n -A3 -B1 'pat'          # context
rg -n --no-ignore --hidden 'pat'   # include ignored and dotfiles; -uu for both plus binaries
rg -n -U 'start[\s\S]*?end'  # multiline
rg -o -P 'id=\K\d+' f        # only the matching part, PCRE lookbehind
rg -n 'pat' --json | jq      # structured results
grep -n 'pat' f              # single file: grep is fine
grep -rn --exclude-dir={.git,node_modules,dist} -I 'pat' .   # when rg is missing
git grep -n 'pat'            # tracked files only; fast; works without rg
```

- `-F` for literals saves escaping; `-e` for patterns starting with `-`; `--` before paths.
- `rg` ignores binary files and hidden/ignored paths by default; grep does not (`-I`, `--exclude-dir`).
- Exit status 1 = no match; in `&&` chains, decide whether "no match" is a failure.

## sed

```bash
sed -n '12,40p' f                       # print a range
sed -n '/^def /p' f                     # lines matching
sed -n '/^start/,/^end/p' f             # block
sed 's/old/new/' f                      # first occurrence per line (prints to stdout)
sed 's/old/new/g' f                     # all occurrences
sed -E 's/(id)=([0-9]+)/\2=\1/' f       # extended regex with groups
sed -i 's/old/new/g' f                  # in place (GNU); macOS: sed -i '' …; portable: -i.bak
sed -i '/^#/d' f                        # delete lines
sed -i '3i\new line' f                  # insert before line 3 (GNU)
sed -i 's|/usr/local|/opt|g' f          # alternate delimiter when the pattern has slashes
sed '$d' f ; sed '1d' f                 # drop last / first line
sed -n 's/^version = "\(.*\)"/\1/p' Cargo.toml   # extract a value
```

- Preview without `-i` first, or with `-n 's/old/new/p'` to see only changed lines.
- Quote the script in single quotes; use `"…$var…"` only when you need expansion, and escape `&` and `/` in replacements.
- `sed` is line-based; for multi-line edits use `perl -0pe` or python.
- `-E` works everywhere; `-r` is GNU and macOS 11+ only; `\+` and `\?` are GNU basic-regex extensions.

## awk

```bash
awk '{print $1, $3}' f                 # columns
awk -F: '{print $1}' /etc/passwd       # custom separator
awk -F'\t' -v OFS='\t' '{print $2,$1}' f
awk 'NR>=10 && NR<=20' f               # line range
awk '/ERROR/ {n++} END {print n}' f    # count
awk '{s+=$3} END {print s}' f          # sum a column
awk '{c[$1]++} END {for (k in c) print c[k], k}' f | sort -rn | head   # histogram
awk 'length($0) > 120 {print FILENAME": "NR}' f   # long lines
awk '!seen[$0]++' f                    # unique lines, order preserved
awk 'NF' f                             # drop blank lines
awk '$2 == "open" {print $1}' f        # filter by field
```

- `$NF` is the last field, `NR` the line number, `NF` the field count; `-v var=value` passes shell values in.
- Portable awk only (no `gensub`, `strftime`, `--re-interval`) unless the card says gawk.

## sort / uniq / cut / tr / paste / comm / join

```bash
sort -u f                               # unique (sorted)
sort -n ; sort -h ; sort -r ; sort -t, -k2,2n f   # numeric, human (K/M/G), reverse, by column
sort -k3,3 -k1,1 f                      # multiple keys (stable with -s)
sort | uniq -c | sort -rn | head -n 20  # top frequencies
uniq -d f ; uniq -u f                   # duplicates only / singletons (input must be sorted)
cut -d, -f1,3 f ; cut -c1-80 f          # fields / character range
tr -s ' ' ; tr '[:upper:]' '[:lower:]' ; tr -d '\r' ; tr '\n' '\0'
paste -sd, f                            # join lines with commas
paste a b                               # side by side
comm -23 <(sort a) <(sort b)            # in a, not in b (sorted inputs)
comm -12 <(sort a) <(sort b)            # in both
join -t, -1 1 -2 1 <(sort a) <(sort b)  # relational join on first field
rev f | cut -d/ -f1 | rev               # last path component (or: awk -F/ '{print $NF}')
nl -ba f ; cat -n f                     # number lines
wc -l ; wc -w ; wc -c < f               # counts (`< f` avoids the filename in output)
head -c 200 f ; tail -c 200 f           # bytes
fold -w 100 -s f ; column -t -s, f      # wrap; align
shuf -n 5 f (GNU) ; sort -R f | head -n 5
diff -u a b ; diff <(cmd1) <(cmd2) ; diff -rq dirA dirB
```

## xargs and loops over files

```bash
rg -l 'old' | xargs sed -i 's/old/new/g'               # safe when no spaces in paths
rg -l0 'old' | xargs -0 sed -i 's/old/new/g'           # space-safe
find . -name '*.log' -print0 | xargs -0 rm -f           # space-safe
find . -name '*.py' -exec ruff check {} +               # batched, no xargs needed
fd -e png -x optipng {}                                 # per file
fd -e js -X prettier --write                            # batched
xargs -I{} sh -c 'echo "{}: $(wc -l < "{}")"'           # per item with a shell (-I already means one item per command)
xargs -P 4 -n 1 CMD                                     # parallel (GNU and BSD)
xargs -r CMD                                            # do nothing on empty input (GNU default runs once; BSD accepts -r too)
while IFS= read -r line; do …; done < f                 # line loop (keeps spaces/backslashes)
while IFS= read -r -d '' f; do …; done < <(find . -name '*.md' -print0)   # NUL-safe loop
for f in *.md; do [ -e "$f" ] || continue; …; done      # glob loop (guard against no match)
```

Never `for f in $(ls)` or `$(find …)`: whitespace splits, globs expand.

## jq

```bash
jq . f ; jq -c . f ; jq -r '.name' f                    # pretty / compact / raw string
jq '.a.b[0].c' f ; jq '.items[] | .name' f
jq 'keys' f ; jq 'keys_unsorted' f ; jq 'length' f ; jq 'type' f
jq '.[] | select(.active) | {id, name}' f
jq -r '.[] | [.id, .name] | @tsv' f ; @csv ; @json ; @base64
jq 'map(select(.n > 3)) | length' f
jq 'to_entries | map(select(.value != null)) | from_entries' f
jq --arg k "$KEY" '.[$k]' f ; jq --argjson n 5 '.limit = $n' f
jq -n '{a: 1, b: [1,2]}'                                # build JSON
jq -s 'add' a.json b.json ; jq -s '.[0] * .[1]' a b     # merge
jq '.version = "1.2.3"' f > tmp && mv tmp f             # edit (no -i)
jq 'del(.secret) | .list |= sort_by(.name)' f
jq 'paths(scalars) | map(tostring) | join(".")' f | head -n 40   # all leaf paths (map(tostring) keeps jq 1.5 happy)
jq -r '.. | .url? // empty' f                           # recursive search
cat *.jsonl | jq -c 'select(.level == "error")' | head  # JSON lines
```

`yq` (mikefarah) uses the same expressions on YAML: `yq '.services | keys' compose.yaml`,
`yq -i '.a.b = 1' f.yaml`, `yq -o json . f.yaml | jq …`. The python `yq` wrapper is `yq` with jq
syntax but different flags; `yq --version` tells which.

## Quoting cheat sheet

- Single quotes: literal, no expansion. Use for sed/awk/jq programs.
- Double quotes: `$var`, `$(cmd)`, `\"` work; glob characters do not expand.
- `"$var"` always, `"$@"` for arguments, `${var:-default}`, `${var:?msg}`, `${var%suffix}`, `${var#prefix}`, `${var//a/b}` (bash).
- A literal single quote inside single quotes: `'it'"'"'s'` or `$'it\'s'`.
- Heredocs: `<<'EOF'` (no expansion, for file contents and scripts), `<<EOF` (expands `$var`), `<<-EOF` strips leading tabs.
- Globs: `*.py` expands before the command runs; quote it (`'*.py'`) when the tool should do the matching (`find -name`, `rg -g`, `fd -g`).
