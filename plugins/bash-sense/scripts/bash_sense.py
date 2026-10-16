#!/usr/bin/env python3
"""bash-sense: a Bash command advisor for AI coding agents (Claude Code plugin).

Modes
  --hook      Read a PreToolUse hook payload on stdin and answer on stdout.
  --check CMD Lint a command and print the findings as text (for tests and /bash-sense:review).
  --envcard   Print the environment card used by the SessionStart hook.

Answer shapes in --hook mode
  nothing                       -> the command runs as it is
  additionalContext             -> it runs; Claude sees a one-line hint ("advise")
  permissionDecision "deny"     -> blocked; Claude sees the better command ("deny")
  permissionDecision "ask"      -> the user decides ("ask": destructive / outward actions)

The hook fails open: any internal error exits 0 with no output.
"""
import json
import os
import re
import sys

VERSION = "0.1.0"

# --------------------------------------------------------------------------------------
# Tokenizer: split a shell command line into simple commands ("segments")
# --------------------------------------------------------------------------------------

KEYWORDS_SKIP = {"then", "else", "do", "!", "{", "elif", "if", "while", "until", "time"}
SEG_ONLY_KEYWORDS = {"done", "fi", "esac", "}", ";;"}
WRAPPERS = {"sudo", "doas", "env", "time", "nohup", "timeout", "command", "exec", "nice",
            "setsid", "stdbuf", "unbuffer", "caffeinate", "builtin", "chronic", "ionice"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}


class Word(object):
    __slots__ = ("raw", "val", "quoted")

    def __init__(self, raw, val, quoted):
        self.raw = raw
        self.val = val
        self.quoted = quoted

    def __repr__(self):
        return "Word(%r)" % self.raw


class Seg(object):
    def __init__(self):
        self.words = []
        self.prev_op = None
        self.next_op = None
        self.redirs = []        # list of (op, target)
        self.heredocs = []      # list of heredoc bodies
        self.nested = False     # came from $( ), ` `, <( ) or bash -c
        self.subshell = False
        self.background = False
        self.pipeline = [self]
        self.index = 0
        self.via = None         # "xargs", "find-exec", "shell-c"
        # derived by analyze()
        self.env = []
        self.wrappers = []
        self.keyword = None
        self.prog = None        # normalized program basename
        self.prog_raw = None    # as written
        self.args = []          # Word list after the program

    # helpers -------------------------------------------------------------
    @property
    def argv(self):
        return [w.val for w in self.args]

    @property
    def raw(self):
        return " ".join(w.raw for w in self.words)

    def has_flag(self, *flags):
        for w in self.args:
            v = w.val
            if v == "--":
                break
            if w.quoted:
                continue
            if v in flags:
                return True
            # combined short flags: -rn contains -r
            if v.startswith("-") and not v.startswith("--") and len(v) > 2:
                for f in flags:
                    if len(f) == 2 and f[0] == "-" and f[1] in v[1:]:
                        return True
            # --flag=value
            for f in flags:
                if f.startswith("--") and v.startswith(f + "="):
                    return True
        return False

    def positionals(self):
        out = []
        after_dd = False
        for w in self.args:
            if after_dd:
                out.append(w)
                continue
            if w.val == "--":
                after_dd = True
                continue
            if w.val.startswith("-") and len(w.val) > 1:
                continue
            out.append(w)
        return out

    @property
    def stdin_from_pipe(self):
        return self.prev_op in ("|", "|&")

    @property
    def has_stdin(self):
        if self.stdin_from_pipe or self.heredocs:
            return True
        for op, _t in self.redirs:
            if op.startswith("<"):
                return True
        return False

    def stdout_to_file(self):
        for op, target in self.redirs:
            if op.startswith(">") or op.startswith("&>") or op.startswith("1>"):
                if target and not target.startswith("&") and target != "/dev/tty":
                    return True
        return False

    def downstream(self):
        return [s.prog for s in self.pipeline[self.index + 1:]]

    def bounded(self):
        """Is this command's output limited by something further down the pipeline?"""
        if self.nested:
            return True
        if self.stdout_to_file():
            return True
        for p in self.downstream():
            if p in BOUNDERS:
                return True
        for s in self.pipeline[self.index + 1:]:
            if s.stdout_to_file():
                return True
        return False


BOUNDERS = {"head", "tail", "wc", "grep", "rg", "jq", "sed", "awk", "cut", "uniq", "fzf",
            "column", "sort", "tee", "xargs", "less", "more", "yq", "tr", "md5sum", "sha256sum",
            "shasum", "python", "python3", "node", "perl", "ruby", "gzip", "zstd", "xz", "bzip2",
            "cat", "diff", "comm", "paste", "fold", "nl", "tac", "rev", "base64", "gh"}


def _match_paren(text, start, open_ch="(", close_ch=")"):
    """text[start] == open_ch; return the index of the matching close_ch (quote-aware)."""
    depth = 0
    i = start
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            j = text.find("'", i + 1)
            i = n if j < 0 else j + 1
            continue
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\":
                    i += 1
                i += 1
            i += 1
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


def tokenize(text, nested=False, subshell=False, via=None):
    segs = []
    nested_texts = []
    state = {"seg": None}

    def new_seg(prev_op):
        s = Seg()
        s.nested = nested
        s.subshell = subshell
        s.via = via
        s.prev_op = prev_op
        return s

    cur = new_seg(None)
    wval, wraw, wq = [], [], [False]
    pending_heredocs = []
    i, n = 0, len(text)

    def flush_word():
        if wraw:
            cur.words.append(Word("".join(wraw), "".join(wval), wq[0]))
            del wval[:]
            del wraw[:]
            wq[0] = False

    def end_seg(op):
        nonlocal_cur = state
        flush_word()
        seg = nonlocal_cur["seg"]
        if seg.words or seg.redirs:
            seg.next_op = op
            segs.append(seg)
            nxt = new_seg(op)
        else:
            nxt = seg
            nxt.prev_op = op if op else nxt.prev_op
        nonlocal_cur["seg"] = nxt
        return nxt

    state["seg"] = cur

    def consume_heredocs(pos):
        # pos points just after a newline
        bodies = []
        for delim, strip_tabs in pending_heredocs:
            lines = []
            while pos < n:
                nl = text.find("\n", pos)
                line = text[pos:] if nl < 0 else text[pos:nl]
                pos = n if nl < 0 else nl + 1
                cmp_line = line.lstrip("\t") if strip_tabs else line
                if cmp_line == delim:
                    break
                lines.append(line)
            bodies.append("\n".join(lines))
        del pending_heredocs[:]
        return pos, bodies

    while i < n:
        cur = state["seg"]
        c = text[i]
        if c == "\n":
            flush_word()
            i += 1
            if pending_heredocs:
                i, bodies = consume_heredocs(i)
                cur.heredocs.extend(bodies)
            end_seg("\n")
            continue
        if c == "\\":
            if i + 1 < n:
                if text[i + 1] == "\n":
                    i += 2
                    continue
                wval.append(text[i + 1])
                wraw.append(text[i:i + 2])
                i += 2
            else:
                i += 1
            continue
        if c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                j = n
            wval.append(text[i + 1:j])
            wraw.append(text[i:j + 1])
            wq[0] = True
            i = j + 1
            continue
        if c == '"':
            j = i + 1
            buf = []
            while j < n:
                ch = text[j]
                if ch == "\\" and j + 1 < n and text[j + 1] in '"\\$`\n':
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if ch == '"':
                    break
                if ch == "$" and text.startswith("$(", j) and not text.startswith("$((", j):
                    k = _match_paren(text, j + 1)
                    nested_texts.append(text[j + 2:k])
                    buf.append("$(…)")
                    j = k + 1
                    continue
                if ch == "`":
                    k = text.find("`", j + 1)
                    if k < 0:
                        k = n
                    nested_texts.append(text[j + 1:k])
                    buf.append("`…`")
                    j = k + 1
                    continue
                buf.append(ch)
                j += 1
            wval.append("".join(buf))
            wraw.append(text[i:j + 1])
            wq[0] = True
            i = j + 1
            continue
        if c == "#" and not wraw:
            nl = text.find("\n", i)
            i = n if nl < 0 else nl
            continue
        if c == "$" and text.startswith("$((", i):
            k = text.find("))", i)
            k = n if k < 0 else k + 2
            wval.append(text[i:k])
            wraw.append(text[i:k])
            i = k
            continue
        if c == "$" and text.startswith("$(", i):
            k = _match_paren(text, i + 1)
            nested_texts.append(text[i + 2:k])
            wval.append("$(…)")
            wraw.append(text[i:k + 1])
            i = k + 1
            continue
        if c == "`":
            k = text.find("`", i + 1)
            if k < 0:
                k = n
            nested_texts.append(text[i + 1:k])
            wval.append("`…`")
            wraw.append(text[i:k + 1])
            i = k + 1
            continue
        if c in " \t":
            flush_word()
            i += 1
            continue
        if c in "<>" and i + 1 < n and text[i + 1] == "(":
            k = _match_paren(text, i + 1)
            nested_texts.append(text[i + 2:k])
            wval.append(c + "(…)")
            wraw.append(text[i:k + 1])
            i = k + 1
            continue
        if c == "(":
            if not wraw:
                k = _match_paren(text, i)
                inner = text[i + 1:k]
                flush_word()
                prev = cur.prev_op
                subs = tokenize(inner, nested=nested, subshell=True, via=via)
                if subs:
                    subs[0].prev_op = prev
                    segs.extend(subs)
                    # continue after the group: the next operator applies to the last sub-seg
                    state["seg"] = new_seg(None)
                    cur = state["seg"]
                    # mark group end so `&` after `)` marks the last sub-seg
                    cur._group_tail = subs[-1]
                i = k + 1
                continue
            wval.append(c)
            wraw.append(c)
            i += 1
            continue
        if c == ")":
            i += 1
            continue
        if c in "<>":
            fd = ""
            if wraw and "".join(wraw).isdigit():
                fd = "".join(wraw)
                del wval[:]
                del wraw[:]
            else:
                flush_word()
            j = i
            op = ""
            while j < n and text[j] in "<>&|-":
                op += text[j]
                j += 1
                if op in ("<<<", "<<-", "&>>", ">>|"):
                    break
            if op.startswith("<<") and not op.startswith("<<<"):
                strip_tabs = op == "<<-"
                while j < n and text[j] in " \t":
                    j += 1
                # delimiter (maybe quoted)
                dstart = j
                if j < n and text[j] in "'\"":
                    q = text[j]
                    e = text.find(q, j + 1)
                    e = n if e < 0 else e
                    delim = text[j + 1:e]
                    j = e + 1
                else:
                    while j < n and text[j] not in " \t\n;|&<>":
                        j += 1
                    delim = text[dstart:j].replace("\\", "")
                pending_heredocs.append((delim, strip_tabs))
                cur.redirs.append(("<<", delim))
                i = j
                continue
            while j < n and text[j] in " \t":
                j += 1
            tstart = j
            if op.endswith("&") and j < n and text[j].isdigit():
                while j < n and text[j].isdigit():
                    j += 1
                target = "&" + text[tstart:j]
            else:
                if j < n and text[j] in "'\"":
                    q = text[j]
                    e = text.find(q, j + 1)
                    e = n if e < 0 else e
                    target = text[j + 1:e]
                    j = e + 1
                else:
                    while j < n and text[j] not in " \t\n;|&<>":
                        j += 1
                    target = text[tstart:j]
            cur.redirs.append(((fd + op) if fd else op, target))
            i = j
            continue
        if c == "&":
            if text.startswith("&&", i):
                end_seg("&&")
                i += 2
                continue
            if text.startswith("&>", i):
                flush_word()
                op = "&>>" if text.startswith("&>>", i) else "&>"
                j = i + len(op)
                while j < n and text[j] in " \t":
                    j += 1
                tstart = j
                while j < n and text[j] not in " \t\n;|&<>":
                    j += 1
                cur.redirs.append((op, text[tstart:j]))
                i = j
                continue
            flush_word()
            tail = getattr(cur, "_group_tail", None)
            if cur.words or cur.redirs:
                cur.background = True
            elif tail is not None:
                tail.background = True
            end_seg("&")
            i += 1
            continue
        if c == "|":
            if text.startswith("||", i):
                end_seg("||")
                i += 2
            elif text.startswith("|&", i):
                end_seg("|&")
                i += 2
            else:
                end_seg("|")
                i += 1
            continue
        if c == ";":
            i += 2 if text.startswith(";;", i) else 1
            end_seg(";")
            continue
        wval.append(c)
        wraw.append(c)
        i += 1

    flush_word()
    cur = state["seg"]
    if pending_heredocs:
        pass
    end_seg(None)

    for t in nested_texts:
        if t.strip():
            segs.extend(tokenize(t, nested=True, via=via))
    return segs


def _group_pipelines(segs):
    """Attach pipeline membership (only among non-nested siblings in order)."""
    current = []
    for s in segs:
        if s.prev_op in ("|", "|&") and current and not s.nested and not current[-1].nested:
            current.append(s)
        else:
            current = [s]
        s.pipeline = current
        s.index = len(current) - 1


# --------------------------------------------------------------------------------------
# Analysis: program, wrappers, env assignments, synthetic segments
# --------------------------------------------------------------------------------------

ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\+?=|\[.*\]=)")
VALUE_OPTS = {
    "sudo": {"-u", "-g", "-p", "-C", "-D", "-r", "-t", "-T", "-U", "--user", "--group"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "nice": {"-n", "--adjustment"},
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "stdbuf": {"-i", "-o", "-e"},
    "ionice": {"-c", "-n", "-p"},
    "doas": {"-u", "-C"},
}


def _basename(p):
    if "/" in p:
        p = p.rsplit("/", 1)[1]
    return p


def analyze(seg):
    words = list(seg.words)
    # leading keywords
    while words and words[0].val in KEYWORDS_SKIP:
        seg.keyword = words[0].val
        words.pop(0)
    while words and words[-1].val in SEG_ONLY_KEYWORDS:
        words.pop()
    if not words:
        return seg
    i = 0
    while i < len(words):
        w = words[i]
        v = w.val
        if not w.quoted and ASSIGN_RE.match(v):
            seg.env.append(v)
            i += 1
            continue
        base = _basename(v)
        if base == "command" and i + 1 < len(words) and words[i + 1].val in ("-v", "-V", "-vV", "-Vv"):
            seg.prog_raw = v
            seg.prog = "command"
            seg.args = words[i + 1:]
            return seg
        if base in WRAPPERS and not w.quoted:
            start_i = i
            seg.wrappers.append(base)
            i += 1
            vopts = VALUE_OPTS.get(base, set())
            while i < len(words) and words[i].val.startswith("-") and len(words[i].val) > 1:
                if words[i].val in vopts:
                    i += 2
                else:
                    i += 1
            if base == "timeout" and i < len(words):
                i += 1  # the duration
            if base == "env":
                while i < len(words) and ASSIGN_RE.match(words[i].val):
                    seg.env.append(words[i].val)
                    i += 1
            if i >= len(words):
                # nothing follows the wrapper: it is the command (e.g. bare `env`, `sudo -l`)
                seg.wrappers.pop()
                seg.prog_raw = v
                seg.prog = base
                seg.args = words[start_i + 1:]
                return seg
            continue
        break
    if i >= len(words):
        return seg
    seg.prog_raw = words[i].val
    seg.prog = _basename(words[i].val)
    if seg.prog.startswith("python3.") or seg.prog == "python3":
        seg.prog = "python3"
    elif re.match(r"^python2(\.\d+)?$", seg.prog):
        seg.prog = "python2"
    seg.args = words[i + 1:]
    return seg


def _xargs_command(seg):
    """For `xargs [opts] cmd args...` return (cmd_word_index) or None."""
    args = seg.args
    i = 0
    value_opts = {"-n", "-I", "-i", "-L", "-P", "-s", "-d", "-a", "-E", "-e", "--max-args",
                  "--replace", "--max-lines", "--max-procs", "--max-chars", "--delimiter",
                  "--arg-file", "--eof"}
    while i < len(args):
        v = args[i].val
        if v.startswith("-") and len(v) > 1:
            if v in value_opts:
                i += 2
            elif v.startswith("-I") or v.startswith("-n") or v.startswith("-P") or v.startswith("-L") or v.startswith("-d"):
                i += 1
            else:
                i += 1
            continue
        return i
    return None


def _synthetic(parent, words, via):
    s = Seg()
    s.words = words
    s.nested = parent.nested
    s.subshell = parent.subshell
    s.via = via
    s.prev_op = parent.prev_op
    s.background = parent.background
    s.pipeline = parent.pipeline
    s.index = parent.index
    analyze(s)
    return s


def expand(segs):
    """Add synthetic segments for commands run through xargs, find -exec, sh -c, eval."""
    out = []
    extra_text = []
    for s in segs:
        analyze(s)
        out.append(s)
        if s.prog == "xargs":
            idx = _xargs_command(s)
            if idx is not None:
                out.append(_synthetic(s, s.args[idx:], "xargs"))
        elif s.prog in ("find", "fd", "fdfind") and s.args:
            vals = [w.val for w in s.args]
            for key in ("-exec", "-execdir", "-ok", "-okdir", "-x", "--exec", "-X", "--exec-batch"):
                if key in vals:
                    j = vals.index(key) + 1
                    k = j
                    while k < len(vals) and vals[k] not in (";", "+", "\\;"):
                        k += 1
                    if j < len(vals):
                        out.append(_synthetic(s, s.args[j:k], "find-exec"))
        elif s.prog in SHELLS and s.has_flag("-c"):
            pos = s.positionals()
            if pos:
                extra_text.append(pos[0].val)
        elif s.prog == "eval" and s.args:
            extra_text.append(" ".join(w.val for w in s.args))
        elif s.prog in ("watch", "ssh") and False:
            pass
    for t in extra_text:
        sub = tokenize(t, nested=False, via="shell-c")
        _group_pipelines(sub)
        out.extend(expand(sub))
    return out


def parse(cmd):
    segs = tokenize(cmd)
    _group_pipelines(segs)
    return expand(segs)


# git / docker / package manager sub-command helpers ----------------------------------

GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
                  "--super-prefix", "--config-env"}


def git_sub(seg):
    """Return (subcommand, args_after_sub) for a git segment."""
    args = seg.args
    i = 0
    while i < len(args):
        v = args[i].val
        if v.startswith("-"):
            if v in GIT_VALUE_OPTS:
                i += 2
            else:
                i += 1
            continue
        return v, args[i + 1:]
    return None, []


def first_positional(args):
    for w in args:
        if w.val.startswith("-") and len(w.val) > 1:
            continue
        return w.val
    return None


def docker_sub(seg):
    """Return (sub, sub2, rest) e.g. ('compose','exec',[...]) or ('exec',None,[...])."""
    args = seg.args
    i = 0
    value_opts = {"-H", "--host", "-c", "--context", "-l", "--log-level", "--config",
                  "-f", "--file", "-p", "--project-name", "--project-directory", "--env-file",
                  "--profile"}
    while i < len(args) and args[i].val.startswith("-"):
        if args[i].val in value_opts:
            i += 2
        else:
            i += 1
    if i >= len(args):
        return None, None, []
    sub = args[i].val
    rest = args[i + 1:]
    if sub == "compose" or seg.prog == "docker-compose":
        if seg.prog == "docker-compose":
            sub, rest = "compose", args[i:]
        j = 0
        while j < len(rest) and rest[j].val.startswith("-"):
            if rest[j].val in value_opts:
                j += 2
            else:
                j += 1
        if j < len(rest):
            return "compose", rest[j].val, rest[j + 1:]
        return "compose", None, []
    return sub, None, rest


# --------------------------------------------------------------------------------------
# Environment: what is installed, which flavor of the classic tools, which platform
# --------------------------------------------------------------------------------------

TOOLS = [
    "rg", "fd", "fdfind", "jq", "yq", "gh", "git", "bat", "batcat", "delta", "fzf", "tree",
    "sd", "dust", "eza", "exa", "ag", "ack", "shellcheck", "shfmt", "timeout", "gtimeout",
    "gsed", "gdate", "ggrep", "gfind", "gxargs", "gstat", "gtac", "gshuf", "gnproc",
    "python", "python3", "pip", "pip3", "uv", "uvx", "poetry", "pipenv", "pipx", "pytest",
    "node", "npm", "pnpm", "yarn", "bun", "bunx", "npx", "deno", "corepack",
    "cargo", "go", "rustc", "ruby", "bundle", "gem", "php", "composer", "java", "mvn", "gradle",
    "dotnet", "make", "just", "task", "cmake", "ninja",
    "docker", "docker-compose", "podman", "kubectl", "helm", "terraform", "aws", "gcloud", "az",
    "curl", "wget", "http", "https", "xh", "ssh", "rsync", "tar", "unzip", "zip", "7z",
    "tac", "shuf", "nproc", "free", "ss", "lsof", "pgrep", "pkill", "ps", "strace", "hyperfine",
    "watch", "entr", "tmux", "nvim", "vim", "code", "sqlite3", "psql", "mysql", "redis-cli",
    "nc", "dig", "xdg-open", "open", "pbcopy", "xclip", "wl-copy", "realpath", "readlink",
    "column", "sponge", "pv", "parallel", "ast-grep", "sg", "semgrep", "tokei", "cloc",
    "ncdu", "htop", "btop", "pandoc", "ffmpeg", "sha256sum", "shasum", "md5sum", "md5",
    "base64", "openssl", "gpg", "direnv", "mise", "asdf", "nodejs", "perl", "awk", "gawk",
    "sed", "grep", "find", "xargs", "stat", "date", "sort", "head", "tail", "ls", "cat",
    "less", "more", "man", "top", "ping", "mktemp", "env", "printenv", "kill", "killall",
    "sysctl", "sw_vers", "lsb_release", "apt-get", "apt", "brew", "apk", "dnf", "yum", "pacman",
    "pre-commit", "ruff", "black", "mypy", "pyright", "eslint", "prettier", "biome", "tsc",
    "jest", "vitest", "mocha", "playwright", "cypress", "nodemon", "ts-node", "tsx",
    "ast-grep", "ctags", "strings", "file", "which", "locate", "mdfind", "plocate",
]

MODERN_TOOLS = ["rg", "fd", "jq", "yq", "gh", "bat", "delta", "fzf", "tree", "sd", "eza",
                "dust", "ast-grep", "shellcheck", "shfmt", "hyperfine", "entr", "uv", "pnpm",
                "bun", "just", "task", "docker", "kubectl", "tokei"]

ENV_CACHE_TTL = 6 * 3600


def _cache_path():
    base = os.environ.get("BASH_SENSE_CACHE_DIR") or os.environ.get("CLAUDE_PLUGIN_DATA")
    if base:
        try:
            os.makedirs(base, exist_ok=True)
        except Exception:
            base = None
    if not base:
        base = os.environ.get("TMPDIR") or os.environ.get("TMP") or os.environ.get("TEMP") or "/tmp"
    try:
        uid = str(os.getuid())
    except AttributeError:
        uid = os.environ.get("USERNAME", "u")
    return os.path.join(base, "bash-sense-env-%s.json" % uid)


def _run(cmd, timeout=3):
    import subprocess
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "replace")
    except Exception:
        return 127, ""


def _flavor(tool):
    """gnu | bsd | busybox | missing — decided from `tool --version`."""
    import shutil
    path = shutil.which(tool)
    if not path:
        return "missing"
    try:
        real = os.path.realpath(path)
        if os.path.basename(real) == "busybox":
            return "busybox"
    except Exception:
        pass
    rc, out = _run([tool, "--version"])
    low = out.lower()
    if "busybox" in low:
        return "busybox"
    if "bsd" in low:
        return "bsd"
    if "gnu" in low or "free software foundation" in low:
        return "gnu"
    if rc == 0 and tool == "grep" and "bsd" in low:
        return "bsd"
    if rc == 0 and tool == "tar" and "bsdtar" in low:
        return "bsd"
    if rc == 0 and tool == "awk" and ("awk version" in low or "mawk" in low):
        return "bsd" if "awk version" in low else "mawk"
    if rc == 0 and tool == "sed" and "gnu" not in low:
        return "bsd"
    return "bsd" if rc != 0 else "gnu"


def compute_env():
    import platform as _pf
    import shutil
    sysname = _pf.system()
    info = {"computed_at": int(__import__("time").time()), "version": VERSION}
    osn = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}.get(sysname, sysname.lower())
    detail = ""
    if osn == "linux":
        try:
            with open("/proc/version") as fh:
                if "microsoft" in fh.read().lower():
                    osn = "wsl"
        except Exception:
            pass
        try:
            with open("/etc/os-release") as fh:
                for line in fh:
                    if line.startswith("PRETTY_NAME="):
                        detail = line.split("=", 1)[1].strip().strip('"')
        except Exception:
            pass
    elif osn == "macos":
        try:
            detail = "macOS " + _pf.mac_ver()[0]
        except Exception:
            detail = "macOS"
    info["os"] = osn
    info["os_detail"] = detail
    info["arch"] = _pf.machine()
    try:
        info["is_root"] = os.geteuid() == 0
    except AttributeError:
        info["is_root"] = False
    info["shell"] = os.environ.get("SHELL", "")
    rc, out = _run(["bash", "--version"])
    m = re.search(r"version (\d+\.\d+)", out)
    info["bash_version"] = m.group(1) if m else ""
    tools = {}
    for t in sorted(set(TOOLS)):
        p = shutil.which(t)
        tools[t] = p or ""
    info["tools"] = tools
    flavors = {}
    for t in ("sed", "grep", "find", "xargs", "date", "stat", "tar", "awk"):
        flavors[t] = _flavor(t)
    info["flavors"] = flavors
    versions = {}
    for t, args, rx in (
        ("node", ["--version"], r"v?(\d+\.\d+\.\d+)"),
        ("python3", ["--version"], r"(\d+\.\d+\.\d+)"),
        ("git", ["--version"], r"(\d+\.\d+(\.\d+)?)"),
        ("go", ["version"], r"go(\d+\.\d+(\.\d+)?)"),
        ("rustc", ["--version"], r"(\d+\.\d+\.\d+)"),
        ("docker", ["--version"], r"(\d+\.\d+\.\d+)"),
    ):
        if tools.get(t):
            rc, out = _run([t] + args)
            m = re.search(rx, out)
            if m:
                versions[t] = m.group(1)
    info["versions"] = versions
    if osn == "linux" and tools.get("docker"):
        rc, out = _run(["docker", "compose", "version"], timeout=5)
        info["docker_compose_v2"] = rc == 0
    else:
        info["docker_compose_v2"] = bool(tools.get("docker"))
    return info


def load_env(force=False):
    path = _cache_path()
    if os.path.islink(path):
        return compute_env()
    if not force:
        try:
            with open(path) as fh:
                data = json.load(fh)
            import time
            if data.get("version") == VERSION and time.time() - data.get("computed_at", 0) < ENV_CACHE_TTL:
                return data
        except Exception:
            pass
    data = compute_env()
    try:
        import tempfile
        fd, tmp = tempfile.mkstemp(prefix="bash-sense-env-", suffix=".tmp", dir=os.path.dirname(path))
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except Exception:
        pass
    return data


# --------------------------------------------------------------------------------------
# Project: lockfiles, scripts, task runners (cheap stat() calls, per invocation)
# --------------------------------------------------------------------------------------

def find_git_root(cwd):
    d = os.path.abspath(cwd)
    for _ in range(40):
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    return None


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _exists(d, name):
    return os.path.exists(os.path.join(d, name))


def project_info(cwd):
    cwd = os.path.abspath(cwd)
    root = find_git_root(cwd) or cwd
    dirs = [cwd] if cwd == root else [cwd, root]
    p = {"cwd": cwd, "root": root, "git": find_git_root(cwd) is not None}
    # JavaScript -----------------------------------------------------------
    js = None
    js_reason = ""
    yarn_berry = False
    pkg = None
    pkg_dir = None
    for d in dirs:
        if _exists(d, "package.json"):
            pkg = _read_json(os.path.join(d, "package.json")) or {}
            pkg_dir = d
            break
    for d in dirs:
        for lock, name in (("pnpm-lock.yaml", "pnpm"), ("bun.lockb", "bun"), ("bun.lock", "bun"),
                           ("yarn.lock", "yarn"), ("package-lock.json", "npm"),
                           ("npm-shrinkwrap.json", "npm")):
            if _exists(d, lock):
                js, js_reason = name, lock
                break
        if js:
            break
    if pkg and isinstance(pkg.get("packageManager"), str):
        pm = pkg["packageManager"].split("@")[0]
        if pm in ("npm", "pnpm", "yarn", "bun"):
            if js and js != pm:
                js_reason = "%s, but packageManager says %s" % (js_reason, pkg["packageManager"])
            js, js_reason = pm, "packageManager " + pkg["packageManager"]
            if pm == "yarn":
                ver = pkg["packageManager"].split("@")[1] if "@" in pkg["packageManager"] else ""
                yarn_berry = not ver.startswith("1.")
    if js == "yarn" and not yarn_berry:
        for d in dirs:
            if _exists(d, ".yarnrc.yml"):
                yarn_berry = True
    p["js"] = js
    p["js_reason"] = js_reason
    p["yarn_berry"] = yarn_berry
    scripts = pkg.get("scripts") if isinstance(pkg, dict) else None
    p["scripts"] = scripts if isinstance(scripts, dict) else {}
    p["pkg_dir"] = pkg_dir
    p["has_node_modules"] = any(_exists(d, "node_modules") for d in dirs)
    # Python ---------------------------------------------------------------
    py = None
    py_reason = ""
    for d in dirs:
        for f, name in (("uv.lock", "uv"), ("poetry.lock", "poetry"), ("Pipfile.lock", "pipenv"),
                        ("Pipfile", "pipenv"), ("pdm.lock", "pdm")):
            if _exists(d, f):
                py, py_reason = name, f
                break
        if py:
            break
    if not py:
        for d in dirs:
            if _exists(d, "pyproject.toml"):
                try:
                    with open(os.path.join(d, "pyproject.toml"), encoding="utf-8") as fh:
                        txt = fh.read()
                    if "[tool.poetry]" in txt:
                        py, py_reason = "poetry", "pyproject.toml [tool.poetry]"
                    elif "[tool.uv" in txt:
                        py, py_reason = "uv", "pyproject.toml [tool.uv]"
                    else:
                        py, py_reason = "pyproject", "pyproject.toml"
                except Exception:
                    py, py_reason = "pyproject", "pyproject.toml"
                break
            if _exists(d, "requirements.txt"):
                py, py_reason = "pip", "requirements.txt"
                break
    venv = None
    for d in dirs:
        for v in (".venv", "venv", "env"):
            if _exists(d, os.path.join(v, "bin", "python")) or _exists(d, os.path.join(v, "Scripts", "python.exe")):
                venv = os.path.join(d, v)
                break
        if venv:
            break
    p["py"] = py
    p["py_reason"] = py_reason
    p["venv"] = venv
    p["active_venv"] = os.environ.get("VIRTUAL_ENV", "")
    # Task runners -----------------------------------------------------------
    make_targets = []
    just_recipes = []
    for d in dirs:
        for mf in ("Makefile", "makefile", "GNUmakefile"):
            path = os.path.join(d, mf)
            if os.path.exists(path):
                try:
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        for line in fh:
                            m = re.match(r"^([A-Za-z0-9_][A-Za-z0-9_.-]*)\s*:(?!=)", line)
                            if m and not m.group(1).startswith("."):
                                make_targets.append(m.group(1))
                except Exception:
                    pass
                break
        if make_targets:
            break
    for d in dirs:
        for jf in ("justfile", "Justfile", ".justfile"):
            path = os.path.join(d, jf)
            if os.path.exists(path):
                try:
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        for line in fh:
                            m = re.match(r"^([A-Za-z0-9_][A-Za-z0-9_-]*)\b[^=]*:\s*(.*)$", line)
                            if m and not line.startswith(" ") and not line.startswith("\t"):
                                just_recipes.append(m.group(1))
                except Exception:
                    pass
                break
        if just_recipes:
            break
    p["make_targets"] = make_targets[:60]
    p["just_recipes"] = just_recipes[:60]
    p["taskfile"] = any(_exists(d, f) for d in dirs for f in ("Taskfile.yml", "Taskfile.yaml"))
    # Other ecosystems ---------------------------------------------------------
    eco = []
    checks = (
        ("Cargo.toml", "rust: cargo"), ("go.mod", "go"), ("Gemfile", "ruby: bundle exec"),
        ("composer.json", "php: composer"), ("gradlew", "gradle wrapper: ./gradlew"),
        ("mvnw", "maven wrapper: ./mvnw"), ("pom.xml", "maven"), ("build.gradle", "gradle"),
        ("build.gradle.kts", "gradle"), ("mix.exs", "elixir: mix"), ("pubspec.yaml", "dart/flutter"),
        ("Package.swift", "swift"), ("CMakeLists.txt", "cmake"), ("Dockerfile", "docker"),
        ("compose.yaml", "docker compose"), ("compose.yml", "docker compose"),
        ("docker-compose.yml", "docker compose"), ("docker-compose.yaml", "docker compose"),
        (".pre-commit-config.yaml", "pre-commit"), ("deno.json", "deno"), ("deno.jsonc", "deno"),
        ("flake.nix", "nix"), ("shell.nix", "nix"), ("Brewfile", "brew bundle"),
        (".nvmrc", "node version: .nvmrc"), (".node-version", "node version: .node-version"),
        (".tool-versions", "asdf/mise: .tool-versions"), ("mise.toml", "mise"), (".mise.toml", "mise"),
        (".python-version", "python version: .python-version"), ("rust-toolchain.toml", "rust toolchain"),
        ("turbo.json", "turborepo"), ("nx.json", "nx"), ("lerna.json", "lerna"),
        ("pnpm-workspace.yaml", "pnpm workspace"), ("tsconfig.json", "typescript"),
    )
    seen = set()
    for d in dirs:
        for f, label in checks:
            if label not in seen and _exists(d, f):
                eco.append(label)
                seen.add(label)
    p["eco"] = eco
    p["gradlew"] = any(_exists(d, "gradlew") for d in dirs)
    p["mvnw"] = any(_exists(d, "mvnw") for d in dirs)
    return p


def git_state(root):
    """Branch and dirtiness, only for the env card (runs git)."""
    if not root:
        return {}
    rc, out = _run(["git", "-C", root, "branch", "--show-current"])
    branch = out.strip() if rc == 0 else ""
    rc2, out2 = _run(["git", "-C", root, "status", "--porcelain", "--untracked-files=normal"], timeout=5)
    dirty = len([l for l in out2.splitlines() if l.strip()]) if rc2 == 0 else -1
    return {"branch": branch, "dirty": dirty}


# --------------------------------------------------------------------------------------
# Findings and context
# --------------------------------------------------------------------------------------

class Finding(object):
    def __init__(self, tier, rule, title, detail="", hard=False):
        self.tier = tier        # deny | ask | advise
        self.rule = rule
        self.title = title      # one line: what -> better
        self.detail = detail    # why / mapping, one or two sentences
        self.hard = hard        # no escape hatch

    def text(self):
        return self.title + ((" " + self.detail) if self.detail else "")


class Ctx(object):
    def __init__(self, cmd, tool_input=None, cwd=None, env=None):
        self.cmd = cmd
        self.tool_input = tool_input or {}
        self.cwd = cwd or os.getcwd()
        self._env = env
        self._proj = None
        self.run_in_background = bool(self.tool_input.get("run_in_background"))
        self.tool_timeout = self.tool_input.get("timeout")
        self.strict = os.environ.get("BASH_SENSE_STRICT", "") not in ("", "0", "false")

    @property
    def env(self):
        if self._env is None:
            self._env = load_env()
        return self._env

    @property
    def proj(self):
        if self._proj is None:
            self._proj = project_info(self.cwd)
        return self._proj

    def has(self, tool):
        return bool(self.env.get("tools", {}).get(tool))

    @property
    def os(self):
        return self.env.get("os", "")

    @property
    def mac(self):
        return self.os == "macos"

    def flavor(self, tool):
        return self.env.get("flavors", {}).get(tool, "")

    @property
    def is_root(self):
        return bool(self.env.get("is_root"))

    def rg(self):
        return "rg" if self.has("rg") else None

    def fd(self):
        if self.has("fd"):
            return "fd"
        if self.has("fdfind"):
            return "fdfind"
        return None

    def path_exists(self, p):
        p = os.path.expanduser(p)
        if not os.path.isabs(p):
            p = os.path.join(self.cwd, p)
        return os.path.exists(p)

    def file_size(self, p):
        p = os.path.expanduser(p)
        if not os.path.isabs(p):
            p = os.path.join(self.cwd, p)
        try:
            if os.path.isfile(p):
                return os.path.getsize(p)
        except Exception:
            pass
        return -1


RULES = []


def rule(fn):
    RULES.append(fn)
    return fn


def _q(s):
    return "`" + s + "`"


def _hang_exempt(seg, ctx):
    return (seg.background or ctx.run_in_background or "timeout" in seg.wrappers
            or "setsid" in seg.wrappers or "nohup" in seg.wrappers or ctx.tool_timeout)


# --------------------------------------------------------------------------------------
# H: commands that hang, wait for a terminal, or run until killed
# --------------------------------------------------------------------------------------

@rule
def h_tail_follow(seg, ctx):
    if seg.prog != "tail":
        return None
    if not (seg.has_flag("-f", "-F", "--follow") or seg.has_flag("--follow=name")):
        return None
    if _hang_exempt(seg, ctx) or seg.has_flag("--pid"):
        return None
    return Finding("deny", "H01", "`tail -f` never returns here, so the call would just time out.",
                   "Read a window instead: `tail -n 100 FILE`; to sample a live log: `timeout 15 tail -f FILE`; "
                   "to follow a process you started: `tail --pid=$PID -f FILE`.")


@rule
def h_watch(seg, ctx):
    if seg.prog != "watch" or _hang_exempt(seg, ctx):
        return None
    return Finding("deny", "H02", "`watch` runs until killed and needs a terminal.",
                   "Run the command once, or poll with a bound: `for i in $(seq 1 10); do CMD; sleep 2; done`.")


@rule
def h_top(seg, ctx):
    if seg.prog in ("htop", "btop", "glances", "nmon", "atop"):
        return Finding("deny", "H03", "`%s` is a full-screen interactive monitor." % seg.prog,
                       "Snapshot instead: `ps -eo pid,pcpu,pmem,etime,args --sort=-pcpu | head -n 15` (Linux) "
                       "or `top -l 1 -n 15 -o cpu` (macOS).")
    if seg.prog == "top" and not seg.has_flag("-b", "-l", "-n"):
        fix = "`top -l 1 -n 15 -o cpu`" if ctx.mac else "`top -bn1 | head -n 20`"
        return Finding("deny", "H03", "`top` is interactive; without batch mode it fails or hangs.",
                       "Snapshot instead: %s, or `ps -eo pid,pcpu,pmem,etime,args --sort=-pcpu | head -n 15`." % fix)
    return None


@rule
def h_editor(seg, ctx):
    if seg.prog in ("vim", "vi", "nvim", "nano", "pico", "emacs", "micro", "helix", "hx", "joe", "mcedit"):
        if seg.prog == "emacs" and seg.has_flag("--batch", "-batch", "--script"):
            return None
        if seg.prog in ("vim", "nvim", "vi") and seg.has_flag("-es", "-Es"):
            return None
        return Finding("deny", "H04", "`%s` opens a full-screen editor and waits for keystrokes." % seg.prog,
                       "Edit files with the Edit/Write tools, or non-interactively: `sed -i` for a line change, "
                       "a quoted heredoc (`cat > FILE <<'EOF'`) for whole files.")
    return None


@rule
def h_git_interactive(seg, ctx):
    if seg.prog != "git":
        return None
    sub, rest = git_sub(seg)
    if not sub:
        return None
    vals = [w.val for w in rest]
    if sub == "rebase" and ("-i" in vals or "--interactive" in vals):
        return Finding("deny", "H05", "`git rebase -i` opens an editor for the todo list and waits.",
                       "Non-interactive forms: squash with `git reset --soft BASE && git commit`, reorder/drop with "
                       "`GIT_SEQUENCE_EDITOR='sed -i ...' git rebase -i BASE`, or move commits with `git rebase --onto`.")
    if sub in ("add", "commit", "stash", "checkout", "restore", "reset") and any(v in ("-p", "--patch", "-i", "--interactive") for v in vals):
        return Finding("deny", "H05", "`git %s -p/-i` is an interactive hunk picker." % sub,
                       "Stage whole paths (`git add PATH`) or edit the file so only the wanted change remains; "
                       "for a partial revert use `git diff > p.patch`, edit, `git apply -R`.")
    return None


@rule
def h_git_commit_message(seg, ctx):
    if seg.prog != "git":
        return None
    sub, rest = git_sub(seg)
    if sub != "commit":
        return None
    vals = [w.val for w in rest]
    if any(v in ("-e", "--edit") for v in vals):
        return Finding("deny", "H06", "`git commit --edit` opens an editor and waits.",
                       "Give the message inline: `git commit -m \"...\"` (a quoted heredoc via `-F -` for several lines).")
    ok_flags = ("-m", "--message", "-F", "--file", "--no-edit", "-C", "--reuse-message",
                "--fixup", "--squash", "--allow-empty-message")
    for v in vals:
        if v in ok_flags or v.startswith("-m") or v.startswith("--message=") or v.startswith("-F") \
                or v.startswith("--file=") or v.startswith("--fixup=") or v.startswith("--squash=") \
                or v.startswith("-C") or v.startswith("--reuse-message="):
            return None
        if v.startswith("-") and not v.startswith("--") and "m" in v[1:] and not v.startswith("--"):
            return None
    if seg.has_stdin and ("-F" in vals or "--file" in vals):
        return None
    return Finding("deny", "H06", "`git commit` without `-m` opens an editor, which has no terminal here.",
                   "Pass the message: `git commit -m \"subject\" -m \"body\"`, or `git commit -F - <<'EOF' ... EOF` "
                   "for a multi-line message; `--amend` wants `--no-edit` to keep the old one.")


@rule
def h_docker_tty(seg, ctx):
    if seg.prog not in ("docker", "docker-compose", "podman", "nerdctl"):
        return None
    sub, sub2, rest = docker_sub(seg)
    vals = [w.val for w in rest]
    own = []
    for v in vals:
        if not v.startswith("-"):
            break
        own.append(v)
    detached = any(v in ("-d", "--detach") or (v.startswith("-") and not v.startswith("--") and "d" in v[1:]) for v in own)
    if sub in ("run", "exec") and not detached and any(v in ("-it", "-ti", "-t", "--tty") or (v.startswith("-") and not v.startswith("--") and "t" in v[1:] and "i" in v[1:]) for v in own):
        return Finding("deny", "H07", "`%s %s -t` allocates a TTY, which does not exist here (\"the input device is not a TTY\")." % (seg.prog, sub),
                       "Drop `-t` (keep `-i` only when piping stdin): `%s %s CONTAINER CMD`. For a shell session, run commands one by one." % (seg.prog, sub))
    if sub == "compose" and sub2 in ("exec", "run") and not any(v in ("-T", "--no-TTY", "--no-tty", "-d", "--detach") for v in vals):
        return Finding("advise", "H07", "`docker compose %s` allocates a TTY by default; add `-T` to be safe without a terminal." % sub2)
    return None


REPLS = {"python": "python3 -c 'CODE'  (or `python3 script.py`, `python3 - <<'EOF'`)",
         "python3": "python3 -c 'CODE'  (or `python3 script.py`, `python3 - <<'EOF'`)",
         "python2": "python2 -c 'CODE'",
         "node": "node -e 'CODE'  (or `node script.js`, `node - <<'EOF'`)",
         "irb": "ruby -e 'CODE'", "ruby": None, "ipython": "python3 -c 'CODE'", "bpython": "python3 -c 'CODE'",
         "ghci": "runghc FILE", "iex": "elixir -e 'CODE'", "erl": "erl -noshell -eval 'CODE' -s init stop",
         "R": "Rscript -e 'CODE'", "julia": "julia -e 'CODE'", "lua": "lua -e 'CODE'",
         "psql": "psql -c 'SQL' DB   (or `psql DB -f file.sql`)", "mysql": "mysql -e 'SQL' DB",
         "sqlite3": "sqlite3 DB 'SQL'", "redis-cli": "redis-cli CMD", "mongosh": "mongosh --eval 'CODE'",
         "deno": None, "php": "php -r 'CODE'", "perl": "perl -e 'CODE'", "bc": "echo 'EXPR' | bc -l",
         "gdb": "gdb -batch -ex 'CMD' PROG", "lldb": "lldb --batch -o 'CMD' PROG", "ftp": None, "sftp": None,
         "telnet": None, "rails": None, "clj": "clojure -M -e 'CODE'", "scala": None, "jshell": "jshell -s FILE"}


@rule
def h_repl(seg, ctx):
    if seg.prog not in REPLS or seg.has_stdin:
        return None
    pos = seg.positionals()
    vals = seg.argv
    if seg.prog == "rails":
        if vals[:1] in (["c"], ["console"], ["db"], ["dbconsole"]):
            return Finding("deny", "H08", "`rails %s` is an interactive console." % vals[0], "Use `rails runner 'CODE'` for Ruby, or `psql -c 'SQL'` for the database.")
        return None
    if seg.has_flag("--version", "-V", "--help", "-h", "-?") or (seg.prog in ("node", "ruby", "perl", "php", "deno", "julia") and seg.has_flag("-v")):
        return None
    if seg.prog in ("python", "python3", "python2"):
        if pos or any(v in ("-c", "-m", "-") or v.startswith("-c") for v in vals):
            return None
    elif seg.prog == "node":
        if pos or any(v in ("-e", "--eval", "-p", "--print", "-", "--version", "-v", "--test", "--check", "-c", "--run", "--watch") or v.startswith("--test") or v.startswith("--run") for v in vals):
            return None
    elif seg.prog in ("psql", "mysql"):
        if any(v in ("-c", "--command", "-f", "--file", "-e", "--execute", "-l", "--list", "-V", "--version") or v.startswith("--command=") or v.startswith("-c") or v.startswith("--file=") or v.startswith("-f") or v.startswith("-e") for v in vals):
            return None
    elif seg.prog == "sqlite3":
        if len(pos) >= 2 or seg.has_flag("-version", "--version", "-cmd"):
            return None
    elif seg.prog in ("redis-cli", "mongosh"):
        if pos or seg.has_flag("--eval", "-c", "--version"):
            return None
    elif seg.prog in ("ruby", "perl", "php", "lua", "julia", "R", "deno", "rails", "scala", "ghci", "iex", "erl", "clj", "jshell"):
        if pos or any(v in ("-e", "-r", "-v", "--version", "-E", "-M", "-run", "-noshell", "-S", "-s") or v.startswith("-e") or v.startswith("-r") for v in vals):
            return None
        if seg.prog == "deno" and vals:
            return None
        if seg.prog in ("ruby", "perl", "php") and vals and vals[0] in ("-n", "-p", "-l", "-a", "-s"):
            return None
        if seg.prog == "php" and seg.has_flag("-S", "-r", "-i", "-m", "-v", "-l", "-a"):
            return None if not seg.has_flag("-a") else Finding("deny", "H08", "`php -a` is an interactive shell.", "Use `php -r 'CODE'` or `php FILE`.")
    elif seg.prog in ("gdb", "lldb"):
        if seg.has_flag("-batch", "--batch", "-ex", "-x", "-o", "--version"):
            return None
    elif seg.prog in ("ftp", "sftp", "telnet"):
        if seg.has_flag("-b", "--version"):
            return None
    elif seg.prog in ("bc", "irb", "ipython", "bpython"):
        if pos and seg.prog == "bc":
            return None
        if seg.has_flag("--version", "-v"):
            return None
    fix = REPLS.get(seg.prog)
    detail = ("Pass the code or query as an argument: `%s`." % fix) if fix else "Pass the code as an argument or a heredoc on stdin."
    return Finding("deny", "H08", "`%s` with no input starts an interactive session, which has nothing to read from here." % seg.prog, detail)


SERVER_PATTERNS = [
    # (regex over the segment text after wrappers, advice for watchers / None for servers)
    (r"^(npx |pnpm |yarn |bunx |pnpm exec |yarn exec |bun x |npm exec )?(jest|vitest|mocha|ava|playwright test|karma start|cypress open|ng test|nx test)\b.*\s(--watch|-w|--watchAll|--ui|--watch-all)(\s|$|=(?!(false|0)\b))", "drop `--watch`/`--ui` to run once (`vitest run`, `jest --watchAll=false`, `npx playwright test`)"),
    (r"^(npx |pnpm |yarn |bunx |pnpm exec |yarn exec |bun x |npm exec )?(tsc|esbuild|rollup|webpack|sass|tailwindcss|postcss|parcel|cargo|deno run|deno test|node)\b.*\s(-w|--watch)(\s|$|=(?!(false|0)\b))", "drop the watch flag to run it once (`tsc --noEmit`, `esbuild ...` without `--watch`)"),
    (r"^(npx |pnpm |yarn |bunx |pnpm exec |yarn exec |bun x |npm exec )?(nodemon|ts-node-dev|tsx watch|chokidar|onchange|watchexec|cargo watch|air|reflex|modd|entr|parcel watch|webpack serve|webpack-dev-server|deno task dev)\b", "run the underlying command once instead of the file watcher"),
    (r"^(npm|pnpm|yarn|bun) (run )?(dev|start|serve|preview|watch|storybook|start:dev|dev:server)\b", None),
    (r"^(npx |pnpm |yarn |bunx |bun x )?(next|nuxt|vite|astro|remix|gatsby|svelte-kit|expo|ng|parcel|snowpack|storybook|docusaurus|vuepress|vitepress|mkdocs|hugo|zola|eleventy|wrangler|vercel|netlify|firebase|serve|http-server|live-server|browser-sync|json-server) (dev|start|serve|preview|server|emulators:start)\b", None),
    (r"^(npx |pnpm |yarn |bunx )?(serve|http-server|live-server|browser-sync|json-server|wrangler dev|vercel dev|netlify dev|firebase emulators:start|mkdocs serve|docusaurus start|vuepress dev|hugo server|hugo serve|zola serve|caddy run|miniflare)\b", None),
    (r"^(python3?|uv run|poetry run|pipenv run|pdm run) (-m )?(http\.server|flask run|uvicorn|gunicorn|hypercorn|daphne|manage\.py runserver|mkdocs serve|streamlit run|gradio|jupyter (lab|notebook)|bokeh serve|fastapi dev|fastapi run|litestar run|sanic|aiohttp\.web|SimpleHTTPServer)\b", None),
    (r"^(flask run|uvicorn|gunicorn|hypercorn|daphne|streamlit run|jupyter (lab|notebook)|jupyter-lab|mkdocs serve|fastapi (dev|run)|gradio|marimo edit)\b", None),
    (r"^(bundle exec )?(rails (s|server)|jekyll (s|serve)|puma|unicorn|rackup|sidekiq|middleman server|foreman start|overmind start)\b", None),
    (r"^(php (artisan serve|-S)|symfony serve|mix phx\.server|iex -S mix|flutter run|ngrok|cloudflared tunnel|redis-server|mongod|postgres |pg_ctl start$|mysqld|minikube tunnel|kubectl port-forward|kubectl proxy|docker compose up(?![^\n]*\s(-d|--detach)(\s|$))(\s|$)|docker-compose up(?![^\n]*\s(-d|--detach)(\s|$))(\s|$)|dotnet watch|dotnet run --project .*--no-build$|air$|gin$)", None),
    (r"^(nc|ncat|netcat) .*(-l|--listen)\b", None),
    (r"^(yes)$", None),
]


def _seg_text_after_wrappers(seg):
    parts = [seg.prog] + seg.argv
    return " ".join(p for p in parts if p is not None)


@rule
def h_server(seg, ctx):
    if seg.prog is None or _hang_exempt(seg, ctx):
        return None
    text = _seg_text_after_wrappers(seg)
    if seg.prog == "yes" and seg.downstream():
        return None
    if seg.prog in ("ping", "ping6"):
        if seg.has_flag("-c", "-w", "-W") or any(v.startswith("-c") for v in seg.argv):
            return None
        return Finding("deny", "H10", "`ping` without `-c` runs forever.", "Use `ping -c 3 HOST` (add `-W 2` for a per-reply timeout on Linux).")
    for rx, advice in SERVER_PATTERNS:
        if re.match(rx, text):
            if advice:
                shown = text if len(text) <= 70 else text[:67] + "..."
                return Finding("deny", "H09", "`%s` starts a watcher that never exits." % shown, "Run once instead: %s." % advice)
            if text.startswith("docker compose up") or text.startswith("docker-compose up"):
                return Finding("deny", "H09", "`docker compose up` in the foreground blocks this call until it times out.",
                               "Start detached and then look at logs: `docker compose up -d && docker compose logs --tail 50`.")
            shown = text if len(text) <= 70 else text[:67] + "..."
            return Finding("deny", "H09", "`%s` is a long-running server/watcher: it would block this call until the timeout kills it." % shown,
                           "Start it in the background with a log, then probe it: `nohup %s > /tmp/dev.log 2>&1 &` then `sleep 3; tail -n 20 /tmp/dev.log` "
                           "(or `curl -sS -o /dev/null -w '%%{http_code}' localhost:PORT`). If the harness has a run-in-background option, use that." % text)
    return None


@rule
def h_sleep(seg, ctx):
    if seg.prog != "sleep" or not seg.args:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)([smhd]?)$", seg.args[0].val)
    if not m:
        return None
    secs = float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
    if secs >= 60:
        return Finding("advise", "H13", "`sleep %s` blocks this call for a long time with no signal. Poll with a bound instead: "
                       "`for i in $(seq 1 30); do CHECK && break; sleep 2; done`." % seg.args[0].val)
    return None


@rule
def h_pager_like(seg, ctx):
    if seg.prog in ("less", "more", "most"):
        return Finding("advise", "H12", "`%s` just dumps the whole file without a terminal; read a slice: `sed -n '1,120p' FILE` or `head -n 100 FILE`." % seg.prog)
    if seg.prog == "man":
        topic = first_positional(seg.args) or "CMD"
        return Finding("advise", "H11", "`man %s` prints the entire manual; `%s --help | head -n 60` or `man %s | grep -n -A3 'OPTION'` is usually enough." % (topic, topic, topic))
    return None


@rule
def h_prompts(seg, ctx):
    p = seg.prog
    vals = seg.argv
    if seg.has_stdin and p not in ("ssh", "gh"):
        return None
    if p in ("npm", "pnpm", "yarn", "bun") and vals and vals[0] == "init" and not seg.has_flag("-y", "--yes"):
        return Finding("advise", "H14", "`%s init` asks questions interactively; `%s init -y` takes the defaults." % (p, p))
    if p in ("npm", "pnpm", "yarn", "bun", "npx") and vals and (vals[0] == "create" or (p == "npx" and vals[0].startswith("create-"))) and not any(v in ("-y", "--yes", "--defaults", "--template", "-t", "--no-interactive") or v.startswith("--template") for v in vals):
        return Finding("advise", "H14", "project generators prompt interactively; pass their non-interactive flags (`--yes`, `--template NAME`, `--no-git`, etc.) or check `--help` first.")
    if p == "ssh":
        pos = seg.positionals()
        if len(pos) == 1 and not seg.has_flag("-N", "-O", "-W", "-G", "-V", "-T"):
            return Finding("advise", "H15", "`ssh HOST` with no command opens an interactive shell; run `ssh -o BatchMode=yes HOST 'CMD'` and keep each call self-contained.")
    if p == "gh" and vals[:2] == ["auth", "login"] and not seg.has_flag("--with-token"):
        return Finding("deny", "H16", "`gh auth login` is an interactive browser/device flow.",
                       "Use a token: `GH_TOKEN=... gh ...`, or `gh auth login --with-token < token.txt`; the user may need to do this themselves.")
    if p == "gh" and vals[:2] == ["pr", "create"] and not any(v in ("--fill", "-f", "--fill-first", "--fill-verbose", "-t", "--title", "-F", "--body-file", "-w", "--web") or v.startswith("--title=") for v in vals):
        return Finding("advise", "H16", "`gh pr create` prompts without `--title`/`--body` (or `--fill`); pass them explicitly, body via `--body-file -` with a heredoc.")
    if p in ("apt-get", "apt") and vals and vals[0] in ("install", "remove", "purge", "upgrade", "dist-upgrade", "autoremove", "full-upgrade") and not seg.has_flag("-y", "--yes", "--assume-yes", "-qq"):
        return Finding("advise", "H17", "`%s %s` aborts without a terminal unless you pass `-y` (and `DEBIAN_FRONTEND=noninteractive` keeps package scripts from prompting); `apt-get` is the stable CLI for scripts." % (p, vals[0]))
    if p in ("dnf", "yum") and vals and vals[0] in ("install", "remove", "update", "upgrade") and not seg.has_flag("-y", "--assumeyes"):
        return Finding("advise", "H17", "`%s %s` prompts for confirmation; add `-y`." % (p, vals[0]))
    if p == "pacman" and vals and vals[0].startswith("-S") and "--noconfirm" not in vals:
        return Finding("advise", "H17", "`pacman` prompts; add `--noconfirm`.")
    return None


# --------------------------------------------------------------------------------------
# T: the right tool, the right flags, bounded output, project conventions
# --------------------------------------------------------------------------------------

def _grep_to_rg_flags(seg):
    """Translate grep flags to rg flags for the suggestion (best effort)."""
    out = []
    pattern_and_paths = []
    skip_next = False
    for w in seg.args:
        v = w.val
        if skip_next:
            skip_next = False
            continue
        if v == "--":
            continue
        if v.startswith("--include="):
            out.append("-g '%s'" % v.split("=", 1)[1])
            continue
        if v.startswith("--exclude-dir=") or v.startswith("--exclude="):
            continue
        if v in ("--include", "--exclude", "--exclude-dir"):
            skip_next = True
            continue
        if v.startswith("-") and len(v) > 1 and not v.startswith("--"):
            if v in ("-A", "-B", "-C", "-m", "-e", "-f"):
                out.append(v)
                skip_next = True
                nxt_idx = seg.args.index(w) + 1
                if nxt_idx < len(seg.args):
                    out[-1] = v + " " + seg.args[nxt_idx].raw
                continue
            keep = "".join(ch for ch in v[1:] if ch not in "rR")
            if keep:
                out.append("-" + keep)
            continue
        if v.startswith("--"):
            if v in ("--recursive", "--dereference-recursive", "--color", "--color=always", "--color=never", "--binary-files=without-match"):
                continue
            out.append(v)
            continue
        pattern_and_paths.append(w.raw)
    return out, pattern_and_paths


@rule
def t_grep_recursive(seg, ctx):
    if seg.prog not in ("grep", "egrep", "fgrep", "ggrep"):
        return None
    if not seg.has_flag("-r", "-R", "--recursive", "--dereference-recursive"):
        return None
    if seg.has_flag("--exclude-dir", "--exclude-dir=node_modules", "--exclude-dir=.git") or any(v.startswith("--exclude-dir") for v in seg.argv):
        return None
    if ctx.has("rg"):
        flags, rest = _grep_to_rg_flags(seg)
        if seg.prog == "egrep":
            flags.append("-E")
        elif seg.prog == "fgrep":
            flags.append("-F")
        if not any(f.startswith("-") and "n" in f[1:] and not f.startswith("--") for f in flags):
            flags.insert(0, "-n")
        # drop a trailing "." path: rg searches cwd by default
        if rest and rest[-1] in (".", "./"):
            rest = rest[:-1]
        sugg = "rg " + " ".join(flags + rest)
        return Finding("deny", "T01", "`%s -r` searches node_modules, .git, build output and binaries too; `rg` skips what .gitignore skips." % seg.prog,
                       "Use: `%s`  (flags map 1:1: -i -l -w -F -v -c -A/-B; --include='*.py' → -g '*.py' or -t py; add `--no-ignore` to search ignored files, `-uu` for everything)." % sugg)
    if ctx.proj.get("git"):
        return Finding("advise", "T01", "`%s -r` crawls node_modules/.git/build output; inside a repo `git grep -n PATTERN` searches only tracked files, or add `--exclude-dir={.git,node_modules,dist,build,.venv,target} -I`." % seg.prog)
    return Finding("advise", "T01", "`%s -r` also scans vendored, generated and binary files; add `--exclude-dir={.git,node_modules,dist,build,.venv,target} -I`." % seg.prog)


@rule
def t_find(seg, ctx):
    if seg.prog != "find" or seg.via == "find-exec":
        return None
    vals = seg.argv
    starts = []
    for v in vals:
        if v.startswith("-") or v in ("(", ")", "!", "\\(", "\\)"):
            break
        starts.append(v)
    if any(s in ("/", "~", os.path.expanduser("~"), "/*") for s in starts) and "-maxdepth" not in vals:
        alt = ("`fd PATTERN /path`" if ctx.fd() else "`locate NAME`") if not ctx.mac else "`mdfind -name NAME` or `fd PATTERN /path`"
        return Finding("deny", "T02", "`find %s` walks the entire filesystem (slow, noisy, permission errors)." % starts[0],
                       "Scope it: `find /likely/dir -maxdepth 4 -name ...`, or use an index: %s." % alt)
    if seg.bounded():
        return None
    has_exclude = any(v in ("-prune", "-path", "-not", "!", "-maxdepth", "-newer", "-mmin", "-mtime", "-size", "-perm", "-delete", "-exec", "-execdir", "-ok", "-print0", "-printf", "-empty", "-user", "-regex", "-newermt", "-links", "-samefile") for v in vals)
    if has_exclude or "-exec" in vals:
        return None
    if ctx.proj.get("has_node_modules") or (ctx.proj.get("git") and os.path.exists(os.path.join(ctx.proj["root"], ".git"))):
        fd = ctx.fd()
        name = None
        for i, v in enumerate(vals):
            if v in ("-name", "-iname") and i + 1 < len(vals):
                name = vals[i + 1]
        if fd:
            if name:
                pat = name.replace("*", "").replace(".", "\\.")
                if name.startswith("*.") and "*" not in name[2:]:
                    sugg = "%s -e %s" % (fd, name[2:])
                else:
                    sugg = "%s '%s'" % (fd, pat)
            else:
                sugg = "%s -t f" % fd
            return Finding("advise", "T02", "`find` will list node_modules/.git contents too; `%s` respects .gitignore (hidden/ignored need `-H`/`-I`)." % sugg)
        return Finding("advise", "T02", "`find` will list node_modules/.git contents too; add `-not -path '*/node_modules/*' -not -path '*/.git/*'` or use `git ls-files 'PATTERN'`.")
    return None


@rule
def t_ls_recursive(seg, ctx):
    if seg.prog not in ("ls", "eza", "exa") or not seg.has_flag("-R", "--recursive"):
        return None
    pos = [w.val for w in seg.positionals()]
    if any(p in ("/", "~", os.path.expanduser("~")) for p in pos):
        return Finding("deny", "T03", "`ls -R %s` lists the whole filesystem." % pos[0], "Use `ls -la %s` or `find %s -maxdepth 2`." % (pos[0], pos[0]))
    if seg.bounded():
        return None
    fd = ctx.fd()
    alt = "`%s . -d 2`" % fd if fd else "`find . -maxdepth 2 -not -path '*/node_modules/*'`"
    if ctx.has("tree"):
        alt = "`tree -L 2 -I 'node_modules|.git'` or " + alt
    return Finding("advise", "T03", "`ls -R` dumps every file at every depth; show the shape first: %s." % alt)


@rule
def t_ls_grep(seg, ctx):
    if seg.prog not in ("ls",) or seg.nested:
        return None
    down = seg.downstream()
    if down[:1] == ["grep"] or down[:1] == ["rg"]:
        fd = ctx.fd()
        alt = "`%s -d 1 PATTERN`" % fd if fd else "`find . -maxdepth 1 -iname '*PATTERN*'`"
        return Finding("advise", "T04", "`ls | grep` parses human output; `ls -d *PATTERN*` (glob) or %s is exact." % alt)
    return None


@rule
def t_uuoc(seg, ctx):
    if seg.prog != "cat" or seg.nested or seg.has_stdin:
        return None
    pos = seg.positionals()
    if len(pos) != 1 or seg.has_flag("-n", "-A", "-v", "-e", "-t", "-b", "-s", "-E", "-T"):
        return None
    down = seg.downstream()
    if not down:
        return None
    nxt = down[0]
    if nxt in ("grep", "rg", "sed", "awk", "head", "tail", "wc", "sort", "jq", "yq", "cut", "tr", "uniq", "python3", "python", "less", "more"):
        f = pos[0].raw
        if nxt in ("grep", "rg", "sed", "awk", "head", "tail", "wc", "sort", "cut", "uniq", "jq", "yq"):
            hint = "`%s ... %s`" % (nxt, f)
        else:
            hint = "`%s ... < %s`" % (nxt, f)
        return Finding("advise", "T05", "`cat %s | %s` → %s (one process, and %s can use the file directly)." % (f, nxt, hint, nxt))
    return None


SECRET_FILE_RE = re.compile(r"(^|/)(\.env(\.(?!(example|sample|template|dist|defaults|schema|test)\b)[A-Za-z0-9_.-]+)?|\.npmrc|\.pypirc|\.netrc|\.git-credentials|credentials(\.json)?|\.aws/credentials|\.docker/config\.json|id_(rsa|ed25519|ecdsa|dsa)|.*\.pem|.*\.key|.*\.p12|.*\.pfx|secrets?\.(ya?ml|json|toml)|\.secrets?|\.ssh/config|token(s)?\.json|service[-_]account.*\.json|kubeconfig|\.kube/config)$")
LOCKFILE_RE = re.compile(r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|bun\.lock|bun\.lockb|Cargo\.lock|poetry\.lock|uv\.lock|Pipfile\.lock|composer\.lock|Gemfile\.lock|go\.sum|pdm\.lock|flake\.lock|mix\.lock|packages\.lock\.json)$")
BINARY_RE = re.compile(r"\.(png|jpe?g|gif|webp|ico|pdf|zip|gz|tgz|bz2|xz|zst|7z|jar|war|class|o|so|dylib|dll|exe|bin|wasm|woff2?|ttf|otf|eot|mp[34]|mov|avi|sqlite|db|pyc|parquet|npy|pkl|lockb)$", re.I)
MINIFIED_RE = re.compile(r"\.(min\.(js|css)|bundle\.js|map)$", re.I)


@rule
def t_cat_targets(seg, ctx):
    if seg.prog not in ("cat", "bat", "batcat", "head", "tail", "less", "more") or seg.has_stdin:
        return None
    pos = seg.positionals()
    if not pos:
        return None
    for w in pos:
        f = w.val
        if SECRET_FILE_RE.search(f):
            return Finding("ask", "S01", "`%s %s` would print a secrets file into the transcript." % (seg.prog, f),
                           "If only the variable names matter: `cut -d= -f1 %s`; for one value's presence: `grep -c '^NAME=' %s`." % (f, f))
    if seg.prog != "cat" or seg.bounded():
        return None
    for w in pos:
        f = w.val
        if LOCKFILE_RE.search(f):
            base = os.path.basename(f)
            if base == "package-lock.json":
                how = "`jq -r '.packages[\"node_modules/NAME\"].version' package-lock.json` or `npm ls NAME`"
            elif base == "yarn.lock":
                how = "`grep -A3 '^\"\\?NAME@' yarn.lock` or `yarn why NAME`"
            elif base == "pnpm-lock.yaml":
                how = "`pnpm why NAME` or `grep -n -A2 '^  /\\?NAME@' pnpm-lock.yaml`"
            elif base in ("Cargo.lock", "poetry.lock", "uv.lock"):
                how = "`grep -n -A2 'name = \"NAME\"' %s`" % base
            else:
                how = "`grep -n NAME %s`" % base
            return Finding("advise", "T06", "`cat %s` dumps a generated lockfile (usually thousands of lines); ask it a question instead: %s." % (f, how))
        if BINARY_RE.search(f):
            return Finding("advise", "T06", "`cat %s` prints binary garbage; use `file %s`, `ls -la %s`, `strings %s | head`, or a format-specific tool (`unzip -l`, `tar -tzf`, `sqlite3 ... .tables`)." % (f, f, f, f))
        if MINIFIED_RE.search(f):
            return Finding("advise", "T06", "`cat %s` is minified/generated output (one giant line); look at the source file or `head -c 500 %s`." % (f, f))
        size = ctx.file_size(f)
        if size > 48 * 1024:
            kb = size // 1024
            return Finding("advise", "T06", "`cat %s` is %d KB, more than fits in one tool result; read a slice (`sed -n '1,120p' %s`, `head -n 80`), count first (`wc -l`), or search with `rg -n PATTERN %s`." % (f, kb, f, f))
    return None


@rule
def t_ls_huge(seg, ctx):
    if seg.prog != "ls" or seg.nested or seg.bounded() or seg.has_flag("-d"):
        return None
    pos = [w.val for w in seg.positionals()] or ["."]
    for d in pos[:3]:
        path = os.path.expanduser(d)
        if not os.path.isabs(path):
            path = os.path.join(ctx.cwd, path)
        if os.path.isdir(path):
            try:
                n = 0
                with os.scandir(path) as it:
                    for _e in it:
                        n += 1
                        if n > 400:
                            break
                if n > 400:
                    return Finding("advise", "T14", "`ls %s` has more than 400 entries; narrow it: `ls %s | head -n 50`, `ls %s | wc -l`, or `ls -d %s/*PATTERN*`." % (d, d, d, d))
            except Exception:
                pass
    return None


@rule
def t_ps_grep(seg, ctx):
    if seg.prog != "ps" or seg.nested:
        return None
    down = seg.downstream()
    if down and down[0] in ("grep", "rg", "egrep", "fgrep"):
        nxt = seg.pipeline[seg.index + 1]
        pat = first_positional(nxt.args) or "NAME"
        if pat in ("-v", "grep"):
            pat = "NAME"
        if ctx.has("pgrep"):
            return Finding("advise", "T07", "`ps | grep %s` also matches the grep itself and needs awk to get PIDs; `pgrep -af %s` prints pid + full command, `pgrep -f %s | xargs -r kill` acts on it." % (pat, pat, pat))
    return None


@rule
def t_which(seg, ctx):
    if seg.prog == "which":
        pos = [w.val for w in seg.positionals()]
        return Finding("advise", "T08", "`which` is not POSIX and misses shell functions/aliases; `command -v %s` is the portable check (exit status tells presence)." % (pos[0] if pos else "NAME"))
    return None


@rule
def t_backticks(seg, ctx):
    for w in seg.words:
        if "`…`" in w.val and not w.raw.startswith("'"):
            return Finding("advise", "T09", "backticks don't nest and swallow backslashes; use `$(...)` for command substitution.")
    return None


@rule
def t_egrep(seg, ctx):
    if seg.prog in ("egrep", "fgrep") and not seg.has_flag("-r", "-R"):
        return Finding("advise", "T10", "`%s` is deprecated (prints a warning on new systems); use `grep -%s`." % (seg.prog, "E" if seg.prog == "egrep" else "F"))
    return None


@rule
def t_json_by_hand(seg, ctx):
    if not ctx.has("jq"):
        return None
    if seg.prog in ("python", "python3") and seg.has_flag("-c"):
        code = " ".join(w.val for w in seg.args)
        if "import json" in code and ("json.load" in code or "json.loads" in code) and "json.dump" not in code:
            return Finding("advise", "T11", "jq is installed: `jq '.path.to.key' FILE` (or `jq -r` for raw strings, `jq 'keys'` to explore) replaces this Python one-liner.")
    if seg.prog == "node" and seg.has_flag("-e", "-p"):
        code = " ".join(w.val for w in seg.args)
        if "JSON.parse" in code and "require(" in code:
            return Finding("advise", "T11", "jq is installed: `jq '.path.to.key' FILE` replaces this Node one-liner (`jq -r` for raw strings).")
    if seg.prog in ("grep", "rg") and not seg.has_flag("-r", "-R", "-l", "-c", "-L"):
        pos = seg.positionals()
        files = [w.val for w in pos[1:]]
        if len(files) == 1 and files[0].endswith(".json") and not seg.nested:
            pat = pos[0].val.strip('"\'')
            key = re.sub(r'[^A-Za-z0-9_$.-]', "", pat)
            return Finding("advise", "T11", "grepping JSON gives partial lines; `jq '.%s' %s` (or `jq 'paths | join(\".\")' %s | grep %s` to find the key) is exact." % (key or "KEY", files[0], files[0], key or "KEY"))
        if len(files) == 1 and re.search(r"\.ya?ml$", files[0]) and ctx.has("yq") and not seg.nested:
            return Finding("advise", "T11", "yq is installed: `yq '.path.to.key' %s` reads YAML structurally." % files[0])
    return None


@rule
def t_curl(seg, ctx):
    if seg.prog == "curl":
        vals = seg.argv
        if not any(v in ("-s", "-S", "--silent", "--show-error", "-sS", "-Ss", "--no-progress-meter", "-q") or (v.startswith("-") and not v.startswith("--") and ("s" in v[1:] or "S" in v[1:])) for v in vals):
            if seg.has_flag("--version", "-V", "--help", "-h"):
                return None
            has_fail = seg.has_flag("-f", "--fail", "--fail-with-body")
            extra = "" if has_fail else " `-f` makes HTTP errors exit non-zero;"
            return Finding("advise", "T12", "`curl -sS` drops the progress meter but keeps errors;%s add `-L` for redirects and `-o FILE` to save (`-w '%%{http_code}'` to see the status)." % extra)
    if seg.prog == "wget":
        if not seg.has_flag("-q", "--quiet", "-nv", "--no-verbose", "--version", "-V", "-h", "--help"):
            return Finding("advise", "T12", "`wget -q` (or `-nv`) stops the progress dump; `-O FILE` to name the output, `-O-` to print it.")
    return None


@rule
def t_git_log_unbounded(seg, ctx):
    if seg.prog != "git" or seg.nested or seg.bounded():
        return None
    sub, rest = git_sub(seg)
    if sub not in ("log", "reflog", "shortlog"):
        return None
    vals = [w.val for w in rest]
    if any(v in ("-n", "--max-count", "-1", "-2", "-3", "-5", "-10", "--since", "--after", "--until", "--before", "--oneline", "--stat", "--name-only", "--name-status", "-p", "--patch", "-S", "-G", "--grep", "--follow", "--author", "--merges", "--no-merges", "--graph", "--first-parent", "--format", "--pretty") or v.startswith("-n") or v.startswith("--max-count=") or v.startswith("--since=") or v.startswith("--grep=") or v.startswith("--pretty=") or v.startswith("--format=") or v.startswith("--author=") or re.match(r"^-\d+$", v) or ".." in v or v == "--" for v in vals):
        return None
    if sub == "log":
        return Finding("advise", "T13", "`git log` prints the whole history; `git log --oneline -n 20` (add `--stat` or `-- PATH` to focus) answers most questions.")
    return None


@rule
def t_for_ls(seg, ctx):
    if seg.prog != "for":
        return None
    raw = seg.raw
    if re.search(r"\bin\s+\$\(\s*(ls|find)\b", raw) or re.search(r"\bin\s+`\s*(ls|find)\b", raw):
        return Finding("advise", "T15", "`for f in $(ls|find ...)` breaks on spaces and globs; use a glob (`for f in *.log`), `find ... -exec CMD {} +`, `find ... -print0 | xargs -0`, or `while IFS= read -r f; do ...; done < <(find ...)`.")
    return None


@rule
def t_xargs_null(seg, ctx):
    if seg.prog != "xargs" or seg.index == 0:
        return None
    prev = seg.pipeline[seg.index - 1]
    if prev.prog == "find" and "-print0" not in prev.argv and not seg.has_flag("-0", "--null", "-d", "-I", "-i"):
        return Finding("advise", "T16", "paths with spaces split under `xargs`; pair `find ... -print0` with `xargs -0` (or use `find ... -exec CMD {} +`).")
    if prev.prog in ("fd", "fdfind") and "-0" not in prev.argv and "--print0" not in prev.argv and not seg.has_flag("-0", "--null", "-I", "-i"):
        return Finding("advise", "T16", "`fd -0 ... | xargs -0 CMD` is space-safe, or simply `fd ... -x CMD` / `-X CMD` (batch).")
    return None


@rule
def t_cd_chain(seg, ctx):
    if seg.prog != "cd" or seg.subshell or seg.nested or seg.via:
        return None
    if seg.next_op not in ("&&", ";", "\n"):
        return None
    target = first_positional(seg.args) or ""
    nxt = None
    for s in seg.pipeline:
        pass
    # find the following segment's program (same top-level list order is not tracked here; use the raw command)
    m = re.search(r"\bcd\s+\S+\s*(&&|;|\n)\s*([A-Za-z0-9_./-]+)", ctx.cmd)
    follower = _basename(m.group(2)) if m else ""
    hints = {"git": "`git -C %s ...`", "make": "`make -C %s ...`", "npm": "`npm --prefix %s run ...`",
             "pnpm": "`pnpm -C %s ...`", "yarn": "`yarn --cwd %s ...`", "cargo": "`cargo --manifest-path %s/Cargo.toml ...`",
             "go": "`go -C %s ...` (go ≥ 1.20)", "docker": "`docker compose --project-directory %s ...`",
             "tar": "`tar -C %s ...`", "ls": "`ls %s`", "cat": "`cat %s/FILE`"}
    if follower in hints:
        how = hints[follower] % (target or "DIR")
    else:
        how = "`(cd %s && CMD)` so the directory change is scoped to the subshell" % (target or "DIR")
    return Finding("advise", "T17", "the working directory persists across calls, so a bare `cd` silently changes every later command; prefer %s or absolute paths." % how)


@rule
def t_sed_inplace_platform(seg, ctx):
    if seg.prog not in ("sed", "gsed") or not seg.has_flag("-i", "--in-place"):
        return None
    if seg.prog == "gsed":
        return None
    vals = seg.argv
    idx = None
    for i, v in enumerate(vals):
        if v == "-i" or (v.startswith("-") and not v.startswith("--") and v.endswith("i")):
            idx = i
            break
    flavor = ctx.flavor("sed")
    if flavor == "bsd":
        if idx is not None and idx + 1 < len(vals) and (seg.args[idx + 1].raw in ("''", '""') or seg.args[idx + 1].val in (".bak", ".orig")):
            return None
        if any(v.startswith("-i.") or v.startswith("-i'") for v in vals):
            return None
        alt = "`gsed -i ...` (GNU sed via brew coreutils/gnu-sed) or " if ctx.has("gsed") else ""
        return Finding("deny", "T18", "BSD sed (macOS) requires a backup suffix after `-i`; without it the next argument is eaten as the suffix.",
                       "Use %s`sed -i '' 's/OLD/NEW/' FILE` here; the portable form is `sed -i.bak 's/OLD/NEW/' FILE && rm FILE.bak`." % alt)
    if flavor in ("gnu", "busybox") and idx is not None and idx + 1 < len(vals) and seg.args[idx + 1].raw in ("''", '""'):
        return Finding("deny", "T18", "GNU sed treats `-i ''` as an empty script: `sed -i '' ...` fails here (that form is for macOS).",
                       "Use `sed -i 's/OLD/NEW/' FILE` (or `sed -i.bak ...` to be portable).")
    return None


MAC_GNU_ONLY = {
    # tool -> (gnu-prefixed name, mac alternative)
    "tac": ("gtac", "`tail -r FILE`"), "shuf": ("gshuf", "`sort -R` or `jot -r N 1 MAX`"), "nproc": ("gnproc", "`sysctl -n hw.ncpu`"),
    "free": (None, "`vm_stat` or `top -l 1 | head -n 10`"), "ss": (None, "`lsof -nP -iTCP -sTCP:LISTEN` or `netstat -anv | grep LISTEN`"),
    "timeout": ("gtimeout", "`perl -e 'alarm shift; exec @ARGV' SECS CMD`"), "sha256sum": (None, "`shasum -a 256 FILE`"),
    "md5sum": (None, "`md5 -q FILE`"), "readlink": None, "lsb_release": (None, "`sw_vers`"), "xdg-open": (None, "`open`"),
    "xclip": (None, "`pbcopy`/`pbpaste`"), "wl-copy": (None, "`pbcopy`"), "sha1sum": (None, "`shasum FILE`"),
    "mktemp": None, "strace": (None, "`dtruss` (needs sudo) or `fs_usage`"), "ltrace": (None, "`dtrace`"),
    "apt-get": (None, "`brew install`"), "apt": (None, "`brew install`"), "systemctl": (None, "`launchctl`"), "journalctl": (None, "`log show --last 1h`"),
}


@rule
def t_mac_missing_gnu(seg, ctx):
    if ctx.mac and "timeout" in seg.wrappers and not ctx.has("timeout"):
        if ctx.has("gtimeout"):
            return Finding("deny", "T19", "`timeout` is not on PATH on this Mac, but GNU coreutils provides it as `gtimeout`.", "Use `gtimeout SECS CMD` with the same arguments.")
        return Finding("deny", "T19", "`timeout` (GNU coreutils) is not installed on this Mac.", "Use `perl -e 'alarm shift; exec @ARGV' SECS CMD...`, or `brew install coreutils` for `gtimeout`.")
    if not ctx.mac or seg.prog not in MAC_GNU_ONLY or seg.prog in ("readlink", "mktemp"):
        return None
    if ctx.has(seg.prog):
        return None
    spec = MAC_GNU_ONLY[seg.prog]
    gname, alt = spec
    if gname and ctx.has(gname):
        return Finding("deny", "T19", "`%s` is not on PATH on this Mac, but the GNU version is installed as `%s`." % (seg.prog, gname), "Use `%s` with the same arguments." % gname)
    return Finding("deny", "T19", "`%s` is a Linux/GNU tool that is not installed on this Mac." % seg.prog, "Use %s instead." % alt)


@rule
def t_flag_flavor(seg, ctx):
    """GNU-only flags on BSD tools and the reverse (checked against the detected flavor)."""
    p = seg.prog
    vals = seg.argv
    fl = ctx.flavor
    if p == "grep" and fl("grep") == "bsd" and seg.has_flag("-P", "--perl-regexp"):
        alt = "`rg -P`" if ctx.has("rg") else "`grep -E` (or `perl -ne 'print if /RE/'`)"
        return Finding("deny", "T19", "BSD grep (macOS) has no `-P`.", "Use %s; most PCRE patterns work with `-E` after replacing `\\d` by `[0-9]`." % alt)
    if p == "date":
        if fl("date") == "bsd" and (seg.has_flag("-d", "--date") or any(v.startswith("--date=") for v in vals)):
            alt = "`gdate -d ...`" if ctx.has("gdate") else "`date -j -f '%Y-%m-%d' '2024-01-31' +%s` to parse, `date -v-1d` to shift"
            return Finding("deny", "T19", "BSD date (macOS) has no `-d`.", "Use %s." % alt)
        if fl("date") == "gnu" and (seg.has_flag("-v") or seg.has_flag("-j")):
            return Finding("deny", "T20", "GNU date has no `-v`/`-j` (those are macOS forms).", "Use `date -d '1 day ago' +%F` / `date -d @EPOCH`.")
    if p == "stat":
        if fl("stat") == "bsd" and (seg.has_flag("-c", "--format", "--printf") or any(v.startswith("--format=") for v in vals)):
            return Finding("deny", "T19", "BSD stat (macOS) uses `-f`, not `-c`.", "Size: `stat -f %z FILE`; mtime: `stat -f %m FILE`; mode: `stat -f %A FILE`. Portable: `wc -c < FILE`.")
        if fl("stat") == "gnu" and seg.has_flag("-f") and not seg.has_flag("--file-system"):
            if not any(v.startswith("%") for v in vals):
                return None
            return Finding("deny", "T20", "GNU stat's `-f` means file-system; the macOS format flag is `-c` here.", "Size: `stat -c %s FILE`; mtime: `stat -c %Y FILE`; mode: `stat -c %a FILE`.")
    if p == "xargs" and fl("xargs") == "bsd" and (seg.has_flag("-d", "--delimiter") or any(v.startswith("-d") for v in vals if v != "-d")):
        return Finding("deny", "T19", "BSD xargs (macOS) has no `-d`.", "Produce NUL-separated input and use `-0`: `tr '\\n' '\\0' | xargs -0 CMD`.")
    if p == "sed" and fl("sed") == "bsd" and seg.has_flag("-r"):
        return Finding("deny", "T19", "BSD sed (macOS) has no `-r`.", "Use `sed -E` (works on GNU too).")
    if p == "find" and fl("find") == "bsd" and "-printf" in vals:
        return Finding("deny", "T19", "BSD find (macOS) has no `-printf`.", "Use `find ... -exec stat -f '%m %N' {} +` or `fd ... -x stat -f '%m %N'`.")
    if p == "head" and fl("sed") == "bsd" and any(re.match(r"^-n\s*-\d+$", v) or v == "-n" for v in vals):
        for i, v in enumerate(vals):
            if v == "-n" and i + 1 < len(vals) and vals[i + 1].startswith("-"):
                return Finding("deny", "T19", "`head -n -N` (all but the last N lines) is GNU-only.", "Use `sed '$d'` for one line, or `ghead -n -N` from brew coreutils.")
    if p == "base64" and fl("sed") == "bsd" and (seg.has_flag("-w", "--wrap") or any(v.startswith("-w") for v in vals)):
        return Finding("deny", "T19", "macOS base64 has no `-w`.", "Use `base64 | tr -d '\\n'` (macOS base64 does not wrap by default).")
    if p == "cp" and fl("sed") == "bsd" and seg.has_flag("--parents"):
        return Finding("deny", "T19", "BSD cp has no `--parents`.", "Use `rsync -R SRC DEST/` or `mkdir -p` + `cp`.")
    if p == "ls" and fl("sed") == "bsd" and any(v.startswith("--") for v in vals):
        return Finding("deny", "T19", "BSD ls (macOS) has no `--long-options`.", "Use the short flags: `ls -la`, `ls -lt`, `ls -G` for color, `ls -lT` for full timestamps.")
    if p == "sort" and fl("sed") == "bsd" and seg.has_flag("-V", "--version-sort"):
        return Finding("deny", "T19", "BSD sort on macOS lacks `-V` (version sort).", "Use `gsort -V` from brew coreutils or `sort -t. -k1,1n -k2,2n -k3,3n`.")
    if p == "readlink" and fl("sed") == "bsd" and seg.has_flag("-f", "-e", "-m") and not ctx.has("greadlink"):
        return Finding("advise", "T19", "`readlink -f` only exists on macOS 12.3+; `realpath FILE` or `cd DIR && pwd -P` is the safe form.")
    if p == "md5" and fl("sed") == "gnu":
        return Finding("deny", "T20", "`md5` is the macOS command; Linux has `md5sum`.", "Use `md5sum FILE | cut -d' ' -f1`.")
    if p == "shasum" and fl("sed") == "gnu" and not ctx.has("shasum"):
        return Finding("deny", "T20", "`shasum` is not installed here.", "Use `sha256sum FILE` / `sha1sum FILE`.")
    if p == "open" and fl("sed") == "gnu" and not ctx.mac:
        return Finding("advise", "T20", "`open` is a macOS command (on Linux it is openvt); there is no browser/GUI here, so print the path or URL instead.")
    if p == "pbcopy" and not ctx.mac:
        return Finding("deny", "T20", "`pbcopy` is macOS-only.", "Write the text to a file instead, or `xclip -selection clipboard` / `wl-copy` where a display exists.")
    if p == "sysctl" and vals and vals[-1] == "hw.ncpu" and not ctx.mac:
        return Finding("deny", "T20", "`sysctl -n hw.ncpu` is the macOS form.", "Use `nproc` (or `getconf _NPROCESSORS_ONLN`, portable).")
    return None


MISSING_ALT = {
    "python": lambda ctx: ("python3", "`python3`") if ctx.has("python3") else None,
    "pip": lambda ctx: ("python3 -m pip", "`python3 -m pip` (or `uv pip` / the project's tool)") if ctx.has("python3") else None,
    "fd": lambda ctx: ("fdfind", "`fdfind` (Debian/Ubuntu package name)") if ctx.has("fdfind") else (("find", "`find . -name 'PATTERN' -not -path '*/node_modules/*'`") if ctx.has("find") else None),
    "bat": lambda ctx: ("batcat", "`batcat`") if ctx.has("batcat") else ("cat", "`cat -n` / `sed -n`"),
    "rg": lambda ctx: ("git grep", "`git grep -n` inside a repo, or `grep -rn --exclude-dir={.git,node_modules} -I`"),
    "docker-compose": lambda ctx: ("docker compose", "`docker compose` (v2 subcommand; same flags)") if ctx.env.get("docker_compose_v2") else None,
    "nodejs": lambda ctx: ("node", "`node`") if ctx.has("node") else None,
    "ag": lambda ctx: ("rg", "`rg`") if ctx.has("rg") else ("grep", "`grep -rn --exclude-dir=.git`"),
    "ack": lambda ctx: ("rg", "`rg`") if ctx.has("rg") else ("grep", "`grep -rn --exclude-dir=.git`"),
    "exa": lambda ctx: ("eza", "`eza`") if ctx.has("eza") else ("ls", "`ls -la`"),
    "pytest": lambda ctx: _py_tool_alt(ctx, "pytest"),
    "mypy": lambda ctx: _py_tool_alt(ctx, "mypy"),
    "ruff": lambda ctx: _py_tool_alt(ctx, "ruff"),
    "black": lambda ctx: _py_tool_alt(ctx, "black"),
    "flake8": lambda ctx: _py_tool_alt(ctx, "flake8"),
    "pyright": lambda ctx: _py_tool_alt(ctx, "pyright"),
    "jq": lambda ctx: ("python3 -m json.tool", "`python3 -m json.tool FILE` to pretty-print, `python3 -c 'import json,sys; print(json.load(sys.stdin)[\"key\"])'` to pick fields") if ctx.has("python3") else None,
    "tree": lambda ctx: ("fd", "`%s . -d 2`" % ctx.fd()) if ctx.fd() else ("find", "`find . -maxdepth 2 -not -path '*/node_modules/*' | sort`"),
    "timeout": lambda ctx: ("gtimeout", "`gtimeout` (brew coreutils)") if ctx.has("gtimeout") else ("perl", "`perl -e 'alarm shift; exec @ARGV' SECS CMD...`"),
    "http": lambda ctx: ("curl", "`curl -sS URL` (`-X POST -H 'Content-Type: application/json' -d '{}'`)"),
    "https": lambda ctx: ("curl", "`curl -sS URL`"),
    "xh": lambda ctx: ("curl", "`curl -sS URL`"),
    "yq": lambda ctx: ("python3", "`python3 -c 'import yaml,sys; ...'` if PyYAML exists, else `grep -n`") if ctx.has("python3") else None,
    "gh": lambda ctx: ("git", "plain `git` for repo operations; the GitHub API via `curl -sS -H \"Authorization: Bearer $GH_TOKEN\" https://api.github.com/...`"),
    "nvim": lambda ctx: ("vim", "non-interactive edits (Edit tool / `sed -i`)"),
    "wget": lambda ctx: ("curl", "`curl -sSLo FILE URL`") if ctx.has("curl") else None,
    "curl": lambda ctx: ("wget", "`wget -qO- URL`") if ctx.has("wget") else None,
    "pip3": lambda ctx: ("python3 -m pip", "`python3 -m pip`") if ctx.has("python3") else None,
    "sg": lambda ctx: ("ast-grep", "`ast-grep`") if ctx.has("ast-grep") else None,
    "ifconfig": lambda ctx: ("ip", "`ip addr` / `ip route`") if ctx.has("ip") else None,
    "netstat": lambda ctx: ("ss", "`ss -ltnp`") if ctx.has("ss") else (("lsof", "`lsof -nP -iTCP -sTCP:LISTEN`") if ctx.has("lsof") else None),
    "locate": lambda ctx: ("plocate", "`plocate`") if ctx.has("plocate") else (("fd", "`%s PATTERN /`" % ctx.fd()) if ctx.fd() else None),
    "delta": lambda ctx: ("git", "`git diff` (plain)"),
    "code": lambda ctx: ("cat", "there is no editor GUI here; read files with `sed -n` / the Read tool"),
}


def _py_tool_alt(ctx, tool):
    py = ctx.proj.get("py")
    runner = {"uv": "uv run", "poetry": "poetry run", "pipenv": "pipenv run", "pdm": "pdm run"}.get(py)
    if runner and ctx.has(py):
        return (runner, "`%s %s` (the project's %s environment has it)" % (runner, tool, py))
    venv = ctx.proj.get("venv")
    if venv and os.path.exists(os.path.join(venv, "bin", tool)):
        rel = os.path.relpath(venv, ctx.cwd)
        return ("venv", "`%s/bin/%s`" % (rel, tool))
    if ctx.has("python3"):
        return ("python3 -m " + tool, "`python3 -m %s` (if installed), or `uvx %s` / `pipx run %s` for a one-off" % (tool, tool, tool))
    return None


@rule
def t_missing_tool(seg, ctx):
    p = seg.prog
    if p not in MISSING_ALT or seg.via == "shell-c" and False:
        return None
    if "/" in (seg.prog_raw or ""):
        return None
    if ctx.has(p):
        return None
    import shutil
    if shutil.which(p):
        ctx.env.setdefault("tools", {})[p] = shutil.which(p)
        return None
    spec = MISSING_ALT[p](ctx)
    if not spec:
        return None
    return Finding("deny", "T21", "`%s` is not on PATH here (it would fail with \"command not found\")." % p, "Use %s." % spec[1])


JS_PM_TRANSLATE = {
    # canonical -> {pm: template}
    "install_all": {"npm": "npm install", "pnpm": "pnpm install", "yarn": "yarn install", "bun": "bun install"},
    "ci": {"npm": "npm ci", "pnpm": "pnpm install --frozen-lockfile", "yarn": "yarn install --frozen-lockfile", "yarn_berry": "yarn install --immutable", "bun": "bun install --frozen-lockfile"},
    "add": {"npm": "npm install {pkgs}", "pnpm": "pnpm add {pkgs}", "yarn": "yarn add {pkgs}", "bun": "bun add {pkgs}"},
    "add_dev": {"npm": "npm install -D {pkgs}", "pnpm": "pnpm add -D {pkgs}", "yarn": "yarn add -D {pkgs}", "bun": "bun add -d {pkgs}"},
    "remove": {"npm": "npm uninstall {pkgs}", "pnpm": "pnpm remove {pkgs}", "yarn": "yarn remove {pkgs}", "bun": "bun remove {pkgs}"},
    "run": {"npm": "npm run {script}", "pnpm": "pnpm run {script}", "yarn": "yarn {script}", "bun": "bun run {script}"},
    "exec": {"npm": "npx {bin}", "pnpm": "pnpm exec {bin}  (pnpm dlx for packages not installed)", "yarn": "yarn {bin}", "yarn_berry": "yarn dlx {bin}", "bun": "bunx {bin}"},
    "update": {"npm": "npm update", "pnpm": "pnpm update", "yarn": "yarn upgrade", "bun": "bun update"},
    "ls": {"npm": "npm ls --depth=0", "pnpm": "pnpm ls --depth 0", "yarn": "yarn list --depth=0", "bun": "bun pm ls"},
}


def _js_canonical(seg):
    p = seg.prog
    vals = seg.argv
    sub = vals[0] if vals else ""
    rest = vals[1:]
    pkgs = [v for v in rest if not v.startswith("-")]
    dev = any(v in ("-D", "--save-dev", "--dev", "-d") for v in rest)
    if p == "npx" or p == "bunx":
        return "exec", {"bin": " ".join(vals)}
    if p == "yarn" and sub and sub not in ("install", "add", "remove", "run", "dlx", "exec", "upgrade", "list", "why", "init", "create", "test", "build", "dev", "start", "cache", "workspace", "workspaces", "set", "config", "info", "outdated", "version", "publish", "pack", "link", "unlink", "global", "bin", "licenses", "audit", "autoclean", "check", "import", "login", "logout", "owner", "policies", "tag", "team", "node", "up", "patch", "rebuild", "explain", "constraints", "plugin", "npm", "stage", "search", "unplug", "pnpify", "sdks"):
        return "run", {"script": " ".join(vals)}
    if sub in ("install", "i", "add") and not pkgs:
        if any(v in ("--frozen-lockfile", "--immutable") for v in rest):
            return "ci", {}
        return "install_all", {}
    if sub == "ci":
        return "ci", {}
    if sub in ("install", "i", "add") and pkgs:
        return ("add_dev" if dev else "add"), {"pkgs": " ".join(pkgs)}
    if sub in ("uninstall", "remove", "rm", "un"):
        return "remove", {"pkgs": " ".join(pkgs)}
    if sub == "run" or sub == "run-script":
        return "run", {"script": " ".join(rest)}
    if sub in ("test", "start", "build", "dev", "lint"):
        return "run", {"script": " ".join(vals)}
    if sub in ("exec", "x", "dlx"):
        return "exec", {"bin": " ".join(rest)}
    if sub in ("update", "upgrade", "up"):
        return "update", {}
    if sub in ("ls", "list"):
        return "ls", {}
    return None, {}


@rule
def t_js_pm_mismatch(seg, ctx):
    p = seg.prog
    if p not in ("npm", "pnpm", "yarn", "bun", "npx", "bunx"):
        return None
    want = ctx.proj.get("js")
    if not want:
        return None
    mine = {"npx": "npm", "bunx": "bun"}.get(p, p)
    if mine == want:
        return None
    if p == "bun" and seg.argv and seg.argv[0] not in ("install", "add", "remove", "i", "update", "run", "x", "test", "pm", "ci"):
        return None  # `bun file.ts` as a runtime is fine
    canon, params = _js_canonical(seg)
    key = "yarn_berry" if (want == "yarn" and ctx.proj.get("yarn_berry") and canon in ("ci", "exec") and "yarn_berry" in JS_PM_TRANSLATE.get(canon, {})) else want
    sugg = None
    if canon and canon in JS_PM_TRANSLATE:
        tpl = JS_PM_TRANSLATE[canon].get(key) or JS_PM_TRANSLATE[canon].get(want)
        if tpl:
            try:
                sugg = tpl.format(**params)
            except Exception:
                sugg = tpl
    reason = ctx.proj.get("js_reason", "")
    why = "this project uses %s (%s)" % (want, reason)
    if canon in ("install_all", "ci", "add", "add_dev", "remove", "update"):
        if sugg:
            return Finding("deny", "T22", "`%s` would write a second lockfile and a mismatched node_modules: %s." % (seg.raw, why), "Use: `%s`." % sugg)
        return Finding("deny", "T22", "`%s` would write a second lockfile and a mismatched node_modules: %s." % (seg.raw, why), "Use the %s equivalent." % want)
    if canon == "exec" and p == "npx" and want in ("pnpm", "yarn", "bun"):
        return Finding("advise", "T22", "%s; `%s` keeps the project's toolchain (npx also works for one-off packages)." % (why, sugg or (want + " exec")))
    if sugg:
        return Finding("advise", "T22", "%s; use `%s`." % (why, sugg))
    return Finding("advise", "T22", "%s; use the %s equivalent of `%s`." % (why, want, seg.raw))


@rule
def t_python_pm(seg, ctx):
    p = seg.prog
    vals = seg.argv
    is_pip = p in ("pip", "pip3") or (p in ("python", "python3") and vals[:2] == ["-m", "pip"])
    if is_pip:
        sub = (vals[2:] if p.startswith("python") else vals)
        if not sub or sub[0] not in ("install", "uninstall"):
            return None
        if "/" in (seg.prog_raw or "") and (".venv" in seg.prog_raw or "venv" in seg.prog_raw):
            return None
        py = ctx.proj.get("py")
        pkgs = " ".join(v for v in sub[1:] if not v.startswith("-")) or "PKG"
        if py == "uv":
            return Finding("deny", "T23", "this project is managed by uv (%s); a bare `pip install` would not be recorded in pyproject/uv.lock and may target the wrong interpreter." % ctx.proj.get("py_reason"),
                           "Use `uv add %s` (dependency) or `uv pip install %s` (ad hoc, into the project's .venv); run things with `uv run CMD`." % (pkgs, pkgs))
        if py == "poetry":
            return Finding("deny", "T23", "this project is managed by poetry (%s)." % ctx.proj.get("py_reason"), "Use `poetry add %s`; run things with `poetry run CMD`." % pkgs)
        if py == "pipenv":
            return Finding("deny", "T23", "this project is managed by pipenv (%s)." % ctx.proj.get("py_reason"), "Use `pipenv install %s`; run things with `pipenv run CMD`." % pkgs)
        if py == "pdm":
            return Finding("deny", "T23", "this project is managed by pdm (%s)." % ctx.proj.get("py_reason"), "Use `pdm add %s`; run things with `pdm run CMD`." % pkgs)
        if ctx.proj.get("active_venv"):
            return None
        venv = ctx.proj.get("venv")
        if venv:
            rel = os.path.relpath(venv, ctx.cwd)
            return Finding("advise", "T23", "no virtualenv is active, so this installs into the system/user site-packages; the project has one at `%s`: `%s/bin/pip install %s` (or `%s/bin/python -m pip ...`)." % (rel, rel, pkgs, rel))
        if "--user" in sub or "--break-system-packages" in sub or ctx.is_root and ctx.os == "linux" and not ctx.mac and os.path.exists("/.dockerenv"):
            return None
        if ctx.is_root and ctx.os in ("linux", "wsl"):
            return Finding("advise", "T23", "no virtualenv is active, so this installs into the system interpreter; fine in a disposable container (newer distros want `--break-system-packages`), otherwise `python3 -m venv .venv && .venv/bin/pip install %s` keeps the machine clean." % pkgs)
        return Finding("advise", "T23", "no virtualenv is active: `pip install` changes the global interpreter (and newer distros refuse). Create one first: `python3 -m venv .venv && .venv/bin/pip install %s`, then run with `.venv/bin/python` (no activate needed)." % pkgs)
    # running python/pytest in a uv/poetry project without the runner
    if p in ("python", "python3", "pytest", "mypy", "ruff", "black", "flake8", "pyright") and not ctx.proj.get("active_venv") and "/" not in (seg.prog_raw or ""):
        py = ctx.proj.get("py")
        runner = {"uv": "uv run", "poetry": "poetry run", "pipenv": "pipenv run", "pdm": "pdm run"}.get(py)
        if runner and ctx.has(py):
            if p in ("python", "python3") and (not vals or vals[0] in ("-c", "--version", "-V")):
                if vals and vals[0] == "-c":
                    return None
                return None
            return Finding("advise", "T27", "this project uses %s (%s); `%s %s` runs it with the project's environment and dependencies." % (py, ctx.proj.get("py_reason"), runner, seg.raw))
        venv = ctx.proj.get("venv")
        if venv and not runner:
            rel = os.path.relpath(venv, ctx.cwd)
            if os.path.exists(os.path.join(venv, "bin", p)) or p in ("python", "python3"):
                return Finding("advise", "T27", "the project's virtualenv is not active; call its interpreter directly: `%s/bin/%s ...` (no `source activate` needed, and it survives across calls)." % (rel, "python" if p in ("python", "python3") else p))
    return None


TOOL_INTENT = {"eslint": "lint", "prettier": "format", "biome": "lint", "tsc": "typecheck", "jest": "test", "vitest": "test",
               "mocha": "test", "ava": "test", "playwright": "test", "cypress": "test", "ruff": "lint", "black": "format",
               "flake8": "lint", "pylint": "lint", "mypy": "typecheck", "pyright": "typecheck", "pytest": "test",
               "stylelint": "lint", "oxlint": "lint", "next": "build", "vite": "build", "webpack": "build", "rollup": "build",
               "esbuild": "build", "tsup": "build", "cargo": None, "go": None}
INTENT_ALIASES = {"lint": ("lint", "lint:all", "eslint", "check", "lint:js", "lint:ts"), "format": ("format", "fmt", "prettier", "format:check"),
                  "typecheck": ("typecheck", "type-check", "tsc", "types", "check-types", "check:types"), "test": ("test", "test:unit", "tests"),
                  "build": ("build",)}


@rule
def t_project_script(seg, ctx):
    p = seg.prog
    if p in ("npx", "pnpm", "yarn", "bunx", "bun", "uv", "poetry", "pipenv", "pdm"):
        vals = seg.argv
        if p in ("pnpm", "yarn", "bun") and vals[:1] == ["exec"]:
            vals = vals[1:]
        elif p in ("pnpm", "yarn", "bun") and vals[:1] in (["run"], ["x"], ["dlx"]):
            return None
        elif p in ("uv", "poetry", "pipenv", "pdm") and vals[:1] == ["run"]:
            vals = vals[1:]
        elif p in ("uv", "poetry", "pipenv", "pdm", "pnpm", "yarn", "bun"):
            return None
        if not vals:
            return None
        tool = _basename(vals[0])
        tool_args = vals[1:]
    else:
        tool = p
        tool_args = seg.argv
    intent = TOOL_INTENT.get(tool)
    if not intent or "/" in (seg.prog_raw or ""):
        return None
    if tool in ("next", "vite", "webpack", "rollup", "esbuild", "tsup") and (not tool_args or tool_args[0] != "build"):
        return None
    pos = [a for a in tool_args if not a.startswith("-")]
    if tool in ("next", "vite", "webpack", "rollup", "esbuild", "tsup"):
        pos = pos[1:]
    if pos and not all(a in (".", "./", "src", "./src", "lib", "app", "test", "tests", "build") for a in pos):
        return None
    scripts = ctx.proj.get("scripts") or {}
    pm = ctx.proj.get("js") or "npm"
    run = {"npm": "npm run %s", "pnpm": "pnpm run %s", "yarn": "yarn %s", "bun": "bun run %s"}[pm]
    for name in INTENT_ALIASES[intent]:
        body = scripts.get(name)
        if isinstance(body, str) and tool in body:
            return Finding("advise", "T24", "package.json has a `%s` script that runs %s with the project's flags: `%s` (keep the direct call only for a narrower run)." % (name, tool, run % name))
    targets = ctx.proj.get("make_targets") or []
    for name in INTENT_ALIASES[intent]:
        if name in targets:
            return Finding("advise", "T24", "the Makefile has a `%s` target: `make %s` runs the project's canonical %s." % (name, name, intent))
    recipes = ctx.proj.get("just_recipes") or []
    for name in INTENT_ALIASES[intent]:
        if name in recipes:
            return Finding("advise", "T24", "the justfile has a `%s` recipe: `just %s`." % (name, name))
    return None


@rule
def t_global_install(seg, ctx):
    p = seg.prog
    vals = seg.argv
    if p == "npm" and vals[:1] in (["install"], ["i"], ["add"]) and seg.has_flag("-g", "--global"):
        pkgs = " ".join(v for v in vals[1:] if not v.startswith("-")) or "PKG"
        return Finding("advise", "T25", "`npm install -g` changes the machine outside this project; `npx -y %s` runs it once, or add it as a devDependency." % pkgs)
    if p in ("pnpm", "yarn", "bun") and vals[:1] in (["add"], ["install"], ["global"]) and (seg.has_flag("-g", "--global") or vals[:1] == ["global"]):
        return Finding("advise", "T25", "a global install changes the machine; `%s dlx PKG` / `bunx PKG` runs it once, or add it as a devDependency." % ("pnpm" if p == "pnpm" else "yarn"))
    if p in ("pipx", "cargo", "go", "gem", "brew") and vals[:1] == ["install"] and not (p == "brew" and ctx.mac is False):
        one = {"pipx": "`pipx run PKG`", "cargo": "`cargo run --bin` inside the project, or `cargo install --locked PKG` once the user agrees", "go": "`go run pkg@version`", "gem": "`bundle exec` with the gem in the Gemfile", "brew": "`brew install` is fine for tools the user asked for"}[p]
        if p == "brew":
            return None
        return Finding("advise", "T25", "`%s install` adds a tool to the user's machine; prefer a one-off run: %s." % (p, one))
    return None


@rule
def t_build_tools(seg, ctx):
    p = seg.prog
    if p == "gradle" and ctx.proj.get("gradlew"):
        return Finding("advise", "T26", "the project ships `./gradlew` (pinned Gradle version); use `./gradlew %s -q --console=plain`." % " ".join(seg.argv))
    if p == "mvn" and ctx.proj.get("mvnw"):
        return Finding("advise", "T26", "the project ships `./mvnw` (pinned Maven version); use `./mvnw -q %s`." % " ".join(seg.argv))
    if p in ("gradle", "gradlew") and not seg.has_flag("-q", "--quiet", "--console"):
        return Finding("advise", "T26", "Gradle output is huge; add `-q --console=plain` (and `--offline` when deps are cached).")
    if p in ("mvn", "mvnw") and not seg.has_flag("-q", "--quiet", "-B"):
        return Finding("advise", "T26", "Maven output is huge; add `-q -B` (quiet, batch mode, no download progress).")
    return None


@rule
def t_while_true(seg, ctx):
    if seg.keyword in ("while", "until") and seg.prog in ("true", ":") and not seg.args:
        raw = ctx.cmd
        if "break" in raw or "timeout" in raw or re.search(r"\bexit\b", raw):
            return None
        return Finding("advise", "T39", "`while true` with no `break`/`timeout` can spin forever; bound it: `for i in $(seq 1 30); do ...; sleep 2; done` or wrap in `timeout 60 bash -c '...'`.")
    return None


@rule
def t_sed_mass_edit(seg, ctx):
    if seg.prog not in ("sed", "gsed") or not seg.has_flag("-i", "--in-place"):
        return None
    if seg.via in ("xargs", "find-exec") or (seg.index > 0 and seg.pipeline[seg.index - 1].prog in ("find", "fd", "fdfind", "rg", "grep", "git")):
        rg = "rg -l" if ctx.has("rg") else "grep -rl"
        return Finding("advise", "T40", "mass in-place edits are hard to undo: list the files first (`%s PATTERN`), preview with `sed -n 's/OLD/NEW/p' FILE`, and review with `git diff --stat` afterwards." % rg)
    return None


# --------------------------------------------------------------------------------------
# D/S: destructive, irreversible, outward-facing, or secret-leaking actions
# --------------------------------------------------------------------------------------

CATASTROPHIC_RM = {"/", "/*", "~", "~/", "$HOME", "$HOME/", "${HOME}", "${HOME}/", "*", "/usr", "/etc", "/var", "/home",
                   "/Users", "/bin", "/sbin", "/lib", "/lib64", "/opt", "/boot", "/dev", "/proc", "/sys", "/tmp", "/root",
                   ".", "./", "..", "../", "/*/", "/tmp/*", "~/*", "$HOME/*", "/var/*", "/etc/*", "/usr/*", "/home/*", "/Users/*"}
HOME_SENSITIVE = (".ssh", ".gnupg", ".aws", ".config", ".claude", ".kube", ".docker", ".gitconfig", ".bashrc", ".zshrc",
                  ".profile", ".bash_profile", ".npmrc", ".cargo", ".rustup", ".local", ".cache", ".vscode", ".mozilla",
                  "Library", "Documents", "Desktop", "Downloads", "Pictures")
ARTIFACT_DIRS = {"node_modules", "dist", "build", "out", ".next", ".nuxt", ".turbo", ".cache", "coverage", "target",
                 "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".venv", "venv", ".parcel-cache",
                 ".svelte-kit", ".output", ".vercel", ".netlify", "tmp", "temp", ".tmp", ".gradle", "bin", "obj",
                 ".dart_tool", "Pods", "DerivedData", ".angular", "storybook-static", "site", "_site", "public/build",
                 "vendor/bundle", "*.pyc", ".eggs", "*.egg-info", "htmlcov", ".coverage", ".nyc_output", ".idea"}


def _norm_target(v):
    v = v.strip()
    return v


def _is_tmp_path(path):
    tmp = os.environ.get("TMPDIR", "/tmp").rstrip("/")
    return path.startswith("/tmp/") or path.startswith(tmp + "/") or path.startswith("/var/folders/") or path.startswith("/private/tmp/")


@rule
def d_rm(seg, ctx):
    if seg.prog != "rm":
        return None
    recursive = seg.has_flag("-r", "-R", "--recursive", "-rf", "-fr")
    force = seg.has_flag("-f", "--force")
    targets = [w for w in seg.positionals()]
    if seg.has_flag("--no-preserve-root"):
        return Finding("deny", "D01", "`rm --no-preserve-root` exists only to destroy the system.", "", hard=True)
    for w in targets:
        v = w.val
        raw = w.raw
        if v in CATASTROPHIC_RM or raw in CATASTROPHIC_RM:
            if recursive or v in ("/", "/*", "~", "$HOME", "*"):
                return Finding("deny", "D01", "`rm %s %s` would destroy %s." % (" ".join(a.val for a in seg.args if a.val.startswith("-")), raw, "the whole system or home directory" if v not in (".", "./", "..", "../", "*", "/tmp", "/tmp/*") else "the entire current directory (which may be the project, or home)"),
                               "Name the exact paths to delete and check them first with `ls -la PATH`.", hard=True)
        if re.match(r"^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?/?(\*)?$", raw) or re.match(r"^\"?\$\{?[A-Za-z_][A-Za-z0-9_]*\}?/", raw):
            var = re.match(r"^\"?\$\{?([A-Za-z_][A-Za-z0-9_]*)", raw).group(1)
            if recursive and ":?" not in raw:
                return Finding("deny", "D02", "`rm -rf %s`: if `%s` is empty or unset this becomes `rm -rf /` (or `rm -rf /sub`)." % (raw, var),
                               "Guard the expansion: `rm -rf \"${%s:?}/sub\"`, and `ls -la \"$%s\"` first." % (var, var), hard=True)
        expanded = os.path.expanduser(v)
        home = os.path.expanduser("~")
        if recursive and (expanded == home or any(expanded.rstrip("/") == os.path.join(home, h) for h in HOME_SENSITIVE)):
            return Finding("deny", "D01", "`rm -rf %s` deletes the user's own configuration/data." % raw, "This is never part of a coding task; ask the user.", hard=True)
        if recursive and v.rstrip("/").endswith(".git") and len(targets) == 1 and "/" not in v.rstrip("/").rstrip(".git"):
            return Finding("ask", "D03", "`rm -rf .git` throws away the repository's whole history and remotes.", "If the goal is a clean tree, `git stash -u` or a fresh clone is reversible.")
    if recursive and targets:
        plain = []
        root = ctx.proj.get("root") or ctx.cwd
        for w in targets:
            v = w.val.rstrip("/")
            base = os.path.basename(v)
            absolute = os.path.abspath(os.path.join(ctx.cwd, os.path.expanduser(v)))
            if _is_tmp_path(absolute):
                continue
            inside = absolute.startswith(ctx.cwd.rstrip("/") + "/") or absolute.startswith(root.rstrip("/") + "/")
            if inside and (base in ARTIFACT_DIRS or any(v.endswith("/" + a) for a in ARTIFACT_DIRS)):
                continue
            if "$(" in w.raw or "`" in w.raw:
                plain.append(w)
                continue
            plain.append(w)
        if plain:
            names = " ".join(w.raw for w in plain)
            return Finding("ask", "D03", "`rm -rf %s` deletes without any way back." % names,
                           "Preview first (`ls -la %s`), and prefer a reversible move: `mv %s /tmp/trash-$(date +%%s)/`. Build artifacts (node_modules, dist, target...) pass without asking." % (names, names))
    return None


@rule
def d_git_destructive(seg, ctx):
    if seg.prog != "git":
        return None
    sub, rest = git_sub(seg)
    vals = [w.val for w in rest]
    if sub == "reset" and ("--hard" in vals or "--merge" in vals):
        return Finding("ask", "D04", "`git reset --hard` throws away every uncommitted change in the working tree.",
                       "Keep them recoverable first: `git stash push -u -m 'before reset'`; to undo one commit but keep the work, `git reset --soft HEAD~1`.")
    if sub in ("checkout", "restore") and sub == "restore" and not any(v in ("--staged", "-S") for v in vals) and any(v in (".", "./", ":/", "*") or (v == "--" ) for v in vals) is False:
        pass
    if sub == "checkout" and ("--" in vals or any(v in (".", "./", ":/") for v in vals)):
        paths = [v for v in vals if v not in ("--",) and not v.startswith("-")]
        if paths and not any(v in ("-b", "-B", "--orphan", "-t", "--track") for v in vals):
            return Finding("ask", "D04", "`git checkout -- %s` discards uncommitted edits in those paths for good." % " ".join(paths),
                           "`git stash push -m 'wip' -- %s` keeps them; `git diff -- %s` shows what would be lost." % (" ".join(paths), " ".join(paths)))
    if sub == "restore":
        paths = [v for v in vals if not v.startswith("-")]
        staged_only = ("--staged" in vals or "-S" in vals) and "-W" not in vals and "--worktree" not in vals
        if paths and not staged_only:
            return Finding("ask", "D04", "`git restore %s` discards uncommitted edits in those paths for good." % " ".join(paths),
                           "`git stash push -m 'wip' -- %s` keeps them; `git diff -- %s` shows what would be lost." % (" ".join(paths), " ".join(paths)))
    if sub == "stash" and vals[:1] in (["drop"], ["clear"]):
        return Finding("ask", "D04", "`git stash %s` deletes stashed work; it is only recoverable through reflog archaeology." % vals[0], "`git stash list` and `git stash show -p` first.")
    if sub == "clean":
        if "-n" in vals or "--dry-run" in vals:
            return None
        flags = "".join(v[1:] for v in vals if v.startswith("-") and not v.startswith("--"))
        if "f" in flags or "--force" in vals:
            if "x" in flags:
                return Finding("ask", "D06", "`git clean -x` also deletes ignored files: .env, local configs, IDE state, caches.",
                               "Preview with `git clean -ndx`; usually `git clean -fd` (no `-x`) is what is meant.")
            return Finding("ask", "D06", "`git clean -f` deletes untracked files with no way back.", "Preview with `git clean -nd` first.")
    if sub == "push":
        force = any(v in ("--force", "-f") for v in vals) or any(v.startswith("-") and not v.startswith("--") and "f" in v[1:] for v in vals if v not in ("-u",))
        lease = any(v.startswith("--force-with-lease") or v == "--force-if-includes" for v in vals)
        branch = ""
        for v in vals:
            if not v.startswith("-") and v not in ("origin", "upstream"):
                branch = v.split(":")[-1]
        protected = branch in ("main", "master", "develop", "trunk", "release", "production", "prod")
        if force and not lease:
            return Finding("ask", "D05", "`git push --force` overwrites the remote branch and anyone else's commits on it.",
                           "`git push --force-with-lease` refuses if the remote moved since your last fetch%s." % ("; forcing to a shared branch like " + branch + " needs the user's explicit OK" if protected else ""))
        if lease and protected:
            return Finding("ask", "D05", "force-pushing to `%s` rewrites a shared branch's history." % branch, "Confirm this is intended.")
        if any(v in ("--delete", "-d") for v in vals) or any(re.match(r"^:[^:]+$", v) for v in vals):
            return Finding("ask", "D07", "this deletes a remote branch.", "Confirm the branch is merged or no longer needed (`git branch -r --merged`).")
    if sub == "branch" and any(v in ("-D", "--delete --force") for v in vals) or (sub == "branch" and "-d" in vals and "-f" in vals):
        return Finding("ask", "D07", "`git branch -D` deletes a branch even if its commits are not merged anywhere.", "`git branch -d` refuses when work would be lost; `git log BRANCH --not main --oneline` shows what is unique to it.")
    if sub in ("filter-branch", "filter-repo", "replace") or (sub == "rebase" and "--root" in vals):
        return Finding("ask", "D07", "`git %s` rewrites history across the repository." % sub, "Back up first (`git branch backup-$(date +%s)`), and coordinate with everyone who has the branch.")
    if sub == "commit" and any(v in ("--no-verify", "-n") for v in vals):
        return Finding("advise", "D16", "`--no-verify` skips the repo's pre-commit hooks; fix what the hook reports instead (run it directly to see why), unless the user asked to bypass it.")
    if sub == "push" and "--no-verify" in vals:
        return Finding("advise", "D16", "`--no-verify` skips pre-push hooks; fix what they report instead unless the user asked to bypass them.")
    return None


@rule
def d_chmod_chown(seg, ctx):
    if seg.prog not in ("chmod", "chown", "chgrp"):
        return None
    vals = seg.argv
    recursive = seg.has_flag("-R", "--recursive")
    targets = [w.val for w in seg.positionals()][1:]
    if any(t in ("/", "/*", "~", os.path.expanduser("~"), "/usr", "/etc", "/var", "/bin", "/lib") for t in targets) and recursive:
        return Finding("deny", "D08", "`%s -R` on %s breaks the system's permission model (often unbootable or sudo-less)." % (seg.prog, targets[0]), "Scope it to the exact path.", hard=True)
    if seg.prog == "chmod" and any(v in ("777", "0777", "-R777", "a+rwx", "ugo+rwx") for v in vals):
        if recursive:
            return Finding("ask", "D08", "`chmod -R 777` makes everything world-writable: a security hole, and never what a permission error needs.",
                           "Diagnose first (`ls -la PATH`, `id`); the fix is usually `chown -R $(id -u):$(id -g) PATH`, `chmod u+x FILE`, or `chmod 755 DIR`.")
        return Finding("advise", "D08", "`chmod 777` is world-writable; `chmod +x FILE` (executable), `chmod 644 FILE` (file) or `chmod 755 DIR` (dir) is the intended permission almost every time.")
    return None


@rule
def d_curl_pipe_sh(seg, ctx):
    if seg.prog not in ("curl", "wget", "fetch") or seg.nested:
        return None
    for nxt in seg.pipeline[seg.index + 1:seg.index + 3]:
        if nxt.prog in ("sh", "bash", "zsh", "dash", "ksh"):
            script = []
            for w in nxt.args:
                if w.val == "--":
                    break
                if not w.val.startswith("-"):
                    script.append(w)
            if nxt.has_flag("-c") or script:
                continue
        elif nxt.prog in ("python", "python3", "node", "perl", "ruby", "php"):
            if nxt.has_flag("-c", "-e", "-m", "-p", "--eval", "-r", "-n", "-l", "-a") or [w for w in nxt.positionals() if w.val != "-"]:
                continue
        else:
            continue
        return Finding("ask", "D09", "`%s ... | %s` runs remote code without anyone reading it." % (seg.prog, nxt.prog),
                       "Download first, look, then run: `curl -fsSLo /tmp/install.sh URL && head -n 60 /tmp/install.sh`, then `bash /tmp/install.sh`.")
    return None


@rule
def d_disk(seg, ctx):
    p = seg.prog
    if p in ("mkfs", "wipefs", "fdisk", "sfdisk", "parted", "gparted", "diskutil", "shred", "blkdiscard") or (p and p.startswith("mkfs.")):
        if p == "diskutil" and seg.argv[:1] in (["list"], ["info"]):
            return None
        if p == "shred":
            return Finding("ask", "D10", "`shred` irreversibly overwrites file contents (and `-u` removes the file).", "Plain `rm` is enough for a coding task; shredding is a user decision.")
        return Finding("deny", "D10", "`%s` formats or destroys disks/partitions." % p, "Not a coding-task operation; ask the user.", hard=True)
    if p == "dd":
        if any(v.startswith("of=/dev/") for v in seg.argv):
            return Finding("deny", "D10", "`dd of=/dev/...` overwrites a block device.", "", hard=True)
    for op, target in seg.redirs:
        if op.startswith(">") and target.startswith("/dev/") and target not in ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/fd/1", "/dev/fd/2"):
            return Finding("deny", "D10", "writing to `%s` overwrites a device." % target, "", hard=True)
    if re.search(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", ctx.cmd):
        return Finding("deny", "D10", "that is a fork bomb.", "", hard=True)
    return None


@rule
def d_kill(seg, ctx):
    p = seg.prog
    vals = seg.argv
    if p == "kill":
        if "-1" in vals and (seg.has_flag("-9", "-KILL", "-s") or "KILL" in vals or "-TERM" in vals):
            return Finding("deny", "D13", "`kill -1` signals every process you own, including this session.", "", hard=True)
        if "-1" in vals and len([v for v in vals if not v.startswith("-")]) == 0:
            return Finding("deny", "D13", "`kill -1` (pid -1) signals every process you own.", "Name the PID: `pgrep -af NAME` then `kill PID`.", hard=True)
        if seg.has_flag("-9", "-KILL") or vals[:1] == ["-s"] and vals[1:2] in (["KILL"], ["9"]):
            return Finding("advise", "D13", "`kill -9` skips cleanup handlers (lock files, temp files, child processes); send TERM first and escalate only if it survives: `kill PID; sleep 2; kill -0 PID 2>/dev/null && kill -9 PID`.")
        return None
    if p in ("pkill", "killall"):
        pats = [w.val for w in seg.positionals()]
        generic = {"node", "python", "python3", "java", "ruby", "php", "bash", "sh", "zsh", "npm", "go", "dotnet", "code", "chrome", "chromium", "firefox", "docker", "ssh", "sshd", "git", "make", "vim", "nvim", "tmux", "postgres", "mysqld", "redis-server", "nginx", "cargo", "rust-analyzer", "tsserver", "jest", "pytest", "uvicorn", "gunicorn", "next", "vite", "webpack", "claude", "electron"}
        for pat in pats:
            base = pat.strip("'\"")
            if base in generic or (len(base) <= 3 and not base.isdigit()):
                return Finding("ask", "D13", "`%s %s` kills every matching process on the machine, including the user's own editors, servers and this session's tooling." % (p, pat),
                               "Find the exact one first: `pgrep -af %s` (shows pid + full command line), then `kill PID`; or match the full command: `pkill -f 'node server.js'`." % base)
        if seg.has_flag("-9", "-KILL"):
            return Finding("advise", "D13", "`%s -9` skips cleanup; try without `-9` first." % p)
    return None


@rule
def d_docker(seg, ctx):
    if seg.prog not in ("docker", "podman", "docker-compose", "nerdctl"):
        return None
    sub, sub2, rest = docker_sub(seg)
    vals = [w.val for w in rest]
    raw = seg.raw
    if sub in ("system", "image", "container", "volume", "network", "builder") and vals[:1] == ["prune"]:
        if sub == "builder" or (sub == "image" and not seg.has_flag("-a", "--all")):
            return None
        return Finding("ask", "D11", "`docker %s prune` deletes other projects' %s too (everything unused on this machine)." % (sub, "containers, images, networks and build cache" if sub == "system" else sub + "s"),
                       "Remove this project's things by name: `docker compose down`, `docker rm NAME`, `docker volume rm NAME`.")
    vals_text = " ".join(w.val for w in seg.words)
    if sub in ("rm", "rmi", "volume") and ("$(…)" in vals_text or "`…`" in vals_text) and ("ps -aq" in ctx.cmd or "ps -a -q" in ctx.cmd or "images -q" in ctx.cmd or "volume ls -q" in ctx.cmd):
        return Finding("ask", "D11", "this removes every container/image/volume on the machine, not just this project's.", "Name them: `docker rm NAME`, or `docker compose down` for the project.")
    if sub == "compose" and sub2 == "down" and any(v in ("-v", "--volumes") for v in vals):
        return Finding("ask", "D11", "`docker compose down -v` deletes the project's volumes (databases included).", "Plain `docker compose down` keeps the data.")
    if sub == "volume" and vals[:1] == ["rm"]:
        return Finding("ask", "D11", "`docker volume rm` deletes stored data (databases) permanently.", "")
    if sub in ("rm", "rmi") and seg.has_flag("-f", "--force") and sub == "rm":
        return None
    return None


@rule
def d_cloud_infra(seg, ctx):
    p = seg.prog
    vals = seg.argv
    txt = " ".join(vals)
    if p == "kubectl" and vals[:1] == ["delete"]:
        what = " ".join(vals[1:3])
        if any(v in ("--all", "--all-namespaces", "-A") for v in vals) or vals[1:2] in (["ns"], ["namespace"], ["namespaces"], ["pv"], ["pvc"], ["node"], ["nodes"]):
            return Finding("ask", "D12", "`kubectl delete %s` removes cluster resources, possibly with their data." % what, "`kubectl delete ... --dry-run=client` previews; name the resource and namespace explicitly.")
        return Finding("ask", "D12", "`kubectl delete %s` removes live cluster resources." % what, "Preview with `--dry-run=client -o yaml`.")
    if p == "kubectl" and vals[:1] == ["apply"] and not seg.has_flag("--dry-run", "--dry-run=client", "--dry-run=server"):
        return Finding("advise", "D12", "`kubectl diff -f ...` / `kubectl apply --dry-run=server -f ...` shows what would change before touching the cluster.")
    if p in ("terraform", "tofu", "pulumi"):
        if vals[:1] == ["destroy"] or (vals[:1] == ["apply"] and any(v in ("-auto-approve", "--auto-approve", "-y", "--yes") for v in vals)):
            return Finding("ask", "D12", "`%s %s` changes real infrastructure without a review step." % (p, vals[0]), "`%s plan` first and show the user the plan." % p)
        if vals[:1] == ["apply"]:
            return Finding("advise", "D12", "`%s apply` prompts for approval; run `%s plan -out=tf.plan` first, then `%s apply tf.plan` so what runs is exactly what was reviewed." % (p, p, p))
    if p == "aws":
        if re.search(r"\b(s3 (rm|rb)|delete-|terminate-instances|deregister|purge-queue)\b", txt):
            return Finding("ask", "D12", "`aws %s` deletes or destroys cloud resources." % " ".join(vals[:2]), "Use `--dryrun` (s3) or list the targets first.")
        if "s3 rm" in txt and "--recursive" in vals and "--dryrun" not in vals:
            return Finding("ask", "D12", "`aws s3 rm --recursive` deletes every object under the prefix.", "Add `--dryrun` first.")
    if p == "gcloud" and re.search(r"\b(delete|destroy)\b", txt):
        return Finding("ask", "D12", "`gcloud ... delete` removes cloud resources.", "")
    if p == "az" and re.search(r"\b(delete|purge)\b", txt):
        return Finding("ask", "D12", "`az ... delete` removes cloud resources.", "")
    if p in ("vercel", "netlify", "fly", "flyctl", "heroku", "railway", "wrangler", "firebase", "sst", "serverless", "sls", "cdk", "sam", "amplify"):
        if re.search(r"(^|\s)(destroy|delete|remove|rm|teardown)(\s|$)", txt):
            return Finding("ask", "D12", "`%s %s` removes deployed resources." % (p, vals[0] if vals else ""), "")
        if re.search(r"(^|\s)(--prod|--production|production)(\s|$|=)", txt) or (p in ("fly", "flyctl", "wrangler", "railway", "heroku", "sst", "serverless", "sls", "cdk", "sam", "amplify", "firebase") and re.search(r"(^|\s)(deploy|publish|push|up)(\s|$)", txt)):
            return Finding("ask", "D12", "`%s %s` deploys to a live environment." % (p, vals[0] if vals else ""), "Preview/staging deploys are the safe default; a live deploy needs the user's go-ahead.")
    return None


SQL_DANGER = re.compile(r"\b(DROP\s+(TABLE|DATABASE|SCHEMA|INDEX|VIEW)|TRUNCATE\s+(TABLE\s+)?\w|DELETE\s+FROM\s+\w+\s*(;|$|\"|')|ALTER\s+TABLE\s+\w+\s+DROP)", re.I)


@rule
def d_database(seg, ctx):
    p = seg.prog
    vals = seg.argv
    txt = seg.raw + " " + " ".join(seg.heredocs)
    if p in ("psql", "mysql", "sqlite3", "mongosh", "redis-cli", "clickhouse-client", "cqlsh", "duckdb") or p in ("prisma", "rails", "php", "python", "python3", "bin/rails", "sequelize", "knex", "alembic", "flyway", "liquibase", "mix", "dotnet", "bundle"):
        if SQL_DANGER.search(txt):
            m = SQL_DANGER.search(txt)
            return Finding("ask", "D14", "this runs `%s` against a database." % m.group(0).strip(), "Back up or snapshot first; `DELETE` wants a `WHERE` and a `SELECT COUNT(*)` with the same predicate before it.")
        if p == "redis-cli" and any(v.upper() in ("FLUSHALL", "FLUSHDB") for v in vals):
            return Finding("ask", "D14", "`redis-cli %s` wipes the Redis dataset." % vals[0].upper(), "")
        if p == "mongosh" and re.search(r"\.(drop|dropDatabase|deleteMany\(\{\}\))\(", txt):
            return Finding("ask", "D14", "this drops MongoDB data.", "")
        joined = " ".join(vals)
        if re.search(r"\b(db:drop|db:reset|db:schema:load|migrate:fresh|migrate:reset|migrate reset|db push --force-reset|flush --noinput|flush$|reset_db|db:truncate|ecto\.drop|ecto\.reset|database drop)\b", joined):
            return Finding("ask", "D14", "`%s` resets or drops the database (all rows gone)." % seg.raw, "Point it at a disposable database (`DATABASE_URL=...`) or back up first.")
    return None


@rule
def d_system(seg, ctx):
    p = seg.prog
    vals = seg.argv
    if p in ("reboot", "shutdown", "halt", "poweroff", "init") and not (p == "init" and vals[:1] not in (["0"], ["6"])):
        return Finding("ask", "D15", "`%s` restarts or powers off the machine." % p, "")
    if p == "systemctl" and vals and vals[0] in ("stop", "disable", "mask", "restart", "kill", "isolate") and not seg.has_flag("--user"):
        return Finding("ask", "D15", "`systemctl %s` changes a system service." % vals[0], "`systemctl status NAME --no-pager` first; `--user` for your own units.")
    if p == "launchctl" and vals and vals[0] in ("unload", "remove", "bootout", "disable"):
        return Finding("ask", "D15", "`launchctl %s` changes a login/system service." % vals[0], "")
    if p == "crontab" and seg.has_flag("-r"):
        return Finding("ask", "D15", "`crontab -r` deletes the whole crontab without confirmation.", "`crontab -l > /tmp/cron.bak` first; edit with `crontab -l | grep -v PATTERN | crontab -`.")
    if p in ("iptables", "nft", "ufw", "pfctl") and any(v in ("-F", "--flush", "flush", "reset", "disable", "-d") for v in vals):
        return Finding("ask", "D15", "`%s` flushes/disables firewall rules." % p, "")
    if p == "git" and False:
        pass
    return None


@rule
def d_global_config(seg, ctx):
    p = seg.prog
    vals = seg.argv
    if p == "git" and vals[:1] == ["config"] and ("--global" in vals or "--system" in vals) and not any(v in ("--get", "--list", "-l", "--get-all", "--get-regexp") for v in vals):
        key = next((v for v in vals if not v.startswith("-") and v != "config"), "KEY")
        if key == "--add" or key.startswith("safe.directory"):
            return Finding("ask", "D17", "`git config --global %s` edits the user's ~/.gitconfig." % key, "Project-local alternative: `git config %s` (no --global), or set `GIT_CONFIG_GLOBAL=/tmp/gitconfig` for this session." % key)
        return Finding("ask", "D17", "`git config --global %s` changes the user's global git settings." % key, "Drop `--global` to set it for this repository only.")
    home = os.path.expanduser("~")
    for op, target in seg.redirs:
        if not op.startswith(">"):
            continue
        t = os.path.expanduser(target.replace("$HOME", home).replace("${HOME}", home))
        base = os.path.basename(t)
        if base in (".bashrc", ".zshrc", ".profile", ".bash_profile", ".zprofile", ".gitconfig", ".npmrc", ".ssh", "authorized_keys", "config", "hosts", "sudoers", "known_hosts", ".netrc", ".pypirc", "settings.json") and (t.startswith(home + "/.") or t.startswith("/etc/") or ".ssh" in t or ".claude" in t):
            return Finding("ask", "D17", "this writes to `%s`, the user's own shell/tool configuration." % target, "Prefer project-local config (`.envrc`, repo settings) or tell the user what to add.")
        if t.startswith("/etc/"):
            return Finding("ask", "D17", "this writes to `%s` (system configuration)." % target, "")
    if p in ("tee",) and any(w.val.startswith("/etc/") or w.val in (os.path.join(home, ".bashrc"), "~/.bashrc", "~/.zshrc", "~/.gitconfig") for w in seg.positionals()):
        return Finding("ask", "D17", "`tee` into system/user configuration.", "")
    return None


@rule
def d_publish(seg, ctx):
    p = seg.prog
    vals = seg.argv
    if p in ("npm", "pnpm", "yarn", "bun") and vals[:1] == ["publish"] and not seg.has_flag("--dry-run"):
        return Finding("ask", "D18", "`%s publish` pushes a package to the public registry; versions cannot be unpublished after 72h." % p, "`%s publish --dry-run` shows what would ship; `npm pack` inspects the tarball." % p)
    if p in ("cargo",) and vals[:1] == ["publish"] and not seg.has_flag("--dry-run"):
        return Finding("ask", "D18", "`cargo publish` is permanent on crates.io.", "`cargo publish --dry-run` first.")
    if p in ("twine", "flit", "poetry", "uv", "hatch", "pdm") and (vals[:1] in (["upload"], ["publish"])):
        return Finding("ask", "D18", "`%s %s` uploads to PyPI (irreversible for that version)." % (p, vals[0]), "Use `--repository testpypi` or `--dry-run` where supported.")
    if p == "gem" and vals[:1] == ["push"]:
        return Finding("ask", "D18", "`gem push` publishes to rubygems.org.", "")
    if p == "gh":
        if vals[:2] == ["pr", "merge"]:
            return Finding("ask", "D18", "`gh pr merge` merges into the shared branch.", "Confirm with the user that reviews/CI are done, and which strategy (`--squash`, `--merge`, `--rebase`).")
        if vals[:2] == ["release", "create"] or vals[:2] == ["release", "delete"] or vals[:2] == ["repo", "delete"] or vals[:2] == ["repo", "archive"]:
            return Finding("ask", "D18", "`gh %s %s` is a public, hard-to-undo repository action." % (vals[0], vals[1]), "")
        if vals[:2] == ["pr", "close"] or vals[:2] == ["issue", "close"] or vals[:2] == ["issue", "delete"]:
            return Finding("ask", "D18", "`gh %s %s` acts on a shared tracker item." % (vals[0], vals[1]), "")
    if p == "git" and git_sub(seg)[0] == "push" and any(v in ("--tags", "--follow-tags") for v in [w.val for w in git_sub(seg)[1]]):
        return Finding("advise", "D18", "pushing tags often triggers release pipelines; make sure that is intended.")
    return None


SECRET_VAR_RE = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|PASS|API_KEY|APIKEY|PRIVATE_KEY|CREDENTIALS?|AUTH|COOKIE|SESSION_KEY|CLIENT_SECRET|ACCESS_KEY|DSN|DATABASE_URL)", re.I)
SECRET_LITERAL_RE = re.compile(r"(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|gho_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}|glpat-[A-Za-z0-9_-]{20,}|npm_[A-Za-z0-9]{36}|pypi-[A-Za-z0-9_-]{30,}|hf_[A-Za-z0-9]{30,})")


@rule
def s_env_dump(seg, ctx):
    p = seg.prog
    if p in ("env", "printenv") and not seg.positionals() and not seg.has_flag("-0", "--null", "-i", "-u", "-C", "-S") and not seg.nested:
        if p == "env" and seg.args:
            return None
        down = seg.downstream()
        if down[:1] in (["grep"], ["rg"]):
            return Finding("advise", "S02", "`env | grep` still prints matching values; `env | cut -d= -f1 | grep -i NAME` shows names only, `printenv NAME | cut -c1-4` confirms a value without exposing it.")
        if down[:1] in (["cut"], ["awk"], ["sed"], ["wc"], ["md5sum"], ["sha256sum"], ["shasum"]):
            return None
        if seg.stdout_to_file():
            return None
        return Finding("ask", "S02", "`%s` prints every variable, tokens and passwords included, into the transcript%s." % (p, " (piping through `%s` does not hide the values)" % down[0] if down else ""),
                       "Ask for what you need: `printenv HOME PATH`, `env | cut -d= -f1 | sort` (names only), or `env | grep -i '^npm_\\|^node' | cut -d= -f1`.")
    if p == "set" and not seg.args and not seg.nested:
        down = seg.downstream()
        if down[:1] in (["cut"], ["awk"], ["sed"], ["wc"]) or seg.stdout_to_file():
            return None
        return Finding("ask", "S02", "bare `set` dumps every shell variable and function, secrets included.", "`set -o` for options, `declare -p NAME` for one variable.")
    return None


@rule
def s_secret_echo(seg, ctx):
    if seg.prog in ("echo", "printf", "printenv") and not seg.bounded():
        for w in seg.args:
            m = re.search(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)", w.raw)
            name = m.group(1) if m else (w.val if seg.prog == "printenv" else "")
            if name and SECRET_VAR_RE.search(name) and not name.upper().endswith("_FILE") and name.upper() not in ("AUTHOR", "PATH"):
                return Finding("advise", "S03", "printing `$%s` puts a secret into the transcript; check presence instead: `[ -n \"$%s\" ] && echo set || echo unset`, or show a prefix: `printf '%%.4s…\\n' \"$%s\"`." % (name, name, name))
    return None


@rule
def s_secret_literal(seg, ctx):
    m = SECRET_LITERAL_RE.search(ctx.cmd)
    if m and not seg.index and not seg.nested and seg is seg.pipeline[0]:
        kind = m.group(0)[:6]
        return Finding("advise", "S04", "a literal credential (`%s…`) is typed into the command line, where it lands in shell history, process lists and this transcript; read it from the environment or a file instead (`-H \"Authorization: Bearer $TOKEN\"`, `--netrc`, `--token-file`)." % kind)
    return None


@rule
def s_tls_off(seg, ctx):
    p = seg.prog
    if p == "curl" and seg.has_flag("-k", "--insecure"):
        return Finding("advise", "S05", "`curl -k` disables certificate checks; fix the trust chain instead (`--cacert FILE`, `SSL_CERT_FILE`, the proxy's CA bundle).")
    if p == "wget" and seg.has_flag("--no-check-certificate"):
        return Finding("advise", "S05", "`--no-check-certificate` disables TLS verification; point wget at the right CA (`--ca-certificate=FILE`) instead.")
    if any(e.startswith("NODE_TLS_REJECT_UNAUTHORIZED=0") or e.startswith("GIT_SSL_NO_VERIFY=") or e.startswith("PYTHONHTTPSVERIFY=0") for e in seg.env):
        return Finding("advise", "S05", "disabling TLS verification process-wide hides real MITM/proxy problems; set the CA bundle instead (`NODE_EXTRA_CA_CERTS`, `GIT_SSL_CAINFO`, `REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE`).")
    if p in ("pip", "pip3") and seg.has_flag("--trusted-host"):
        return Finding("advise", "S05", "`--trusted-host` skips TLS verification for that index; `PIP_CERT=/path/ca.pem` keeps it on.")
    if p == "git" and "-c" in seg.argv and any(v.startswith("http.sslVerify=false") for v in seg.argv):
        return Finding("advise", "S05", "`http.sslVerify=false` disables TLS checks; `git config http.sslCAInfo /path/ca.pem` keeps them.")
    return None


@rule
def s_sudo(seg, ctx):
    if "sudo" not in seg.wrappers and "doas" not in seg.wrappers:
        return None
    if ctx.is_root:
        return Finding("advise", "S06", "already running as root: drop `sudo` (it may not even be installed here).")
    prog = seg.prog or ""
    detail = "Without a terminal `sudo` cannot ask for a password and fails unless NOPASSWD is configured; "
    if prog in ("rm", "chmod", "chown", "mv", "cp", "tee", "sh", "bash"):
        return Finding("ask", "S06", "`sudo %s` changes the system as root." % prog, detail + "the task can usually be done inside the project without root; otherwise the user should run it.")
    if prog in ("apt-get", "apt", "dnf", "yum", "pacman", "apk", "brew", "snap", "zypper"):
        return Finding("ask", "S06", "`sudo %s` installs or changes system packages." % prog, detail + "ask the user, or use a project-local tool (npx, uvx, cargo run, a container).")
    if prog in ("npm", "pip", "pip3", "gem", "cargo", "go", "pnpm", "yarn"):
        return Finding("ask", "S06", "`sudo %s` installs packages as root, which breaks file ownership later." % prog, "Install without sudo into the project (venv, node_modules) or the user's own prefix.")
    return Finding("ask", "S06", "`sudo %s` runs as root." % prog, detail + "confirm with the user.")


@rule
def t_git_add_all(seg, ctx):
    if seg.prog != "git":
        return None
    sub, rest = git_sub(seg)
    vals = [w.val for w in rest]
    if sub == "add" and any(v in ("-A", "--all", ".", "./", ":/", "*", "-a") for v in vals):
        return Finding("advise", "T33", "`git add %s` stages everything, including stray files (logs, .env, editor files, build output); check `git status --short` and stage the paths you changed by name." % next(v for v in vals if v in ("-A", "--all", ".", "./", ":/", "*", "-a")))
    if sub == "commit" and any(v in ("-a", "--all", "-am", "-am.") or (v.startswith("-a") and not v.startswith("--") and "m" in v) for v in vals):
        if not any(v.startswith("--") for v in vals if v.startswith("--a")):
            return Finding("advise", "T33", "`git commit -a` sweeps every modified tracked file into the commit; `git add PATH...` then `git commit -m` keeps the commit to the change at hand.")
    return None


# --------------------------------------------------------------------------------------
# Running the rules, deciding the answer
# --------------------------------------------------------------------------------------

OFF_INLINE_RE = re.compile(r"#\s*bash-sense\s*:\s*(off|allow|skip)\b", re.I)
OFF_PREFIX_RE = re.compile(r"^\s*BASH_SENSE=(off|0|false)\b", re.I)
TIER_RANK = {"deny": 3, "ask": 2, "advise": 1}


def lint(cmd, ctx=None):
    """Return the list of Findings for a command (deduplicated by rule id)."""
    ctx = ctx or Ctx(cmd)
    findings = []
    seen = set()
    try:
        segs = parse(cmd)
    except Exception:
        return []
    for seg in segs:
        if seg.prog is None and seg.keyword is None and not seg.redirs:
            continue
        for fn in RULES:
            try:
                f = fn(seg, ctx)
            except Exception:
                f = None
            if f is None:
                continue
            key = (f.rule, f.title)
            if key in seen:
                continue
            seen.add(key)
            if ctx.strict and f.tier == "advise":
                f.tier = "deny"
            findings.append(f)
    findings.sort(key=lambda f: -TIER_RANK[f.tier])
    return findings


def escape_hatch(cmd):
    return bool(OFF_INLINE_RE.search(cmd) or OFF_PREFIX_RE.match(cmd))


def decide(cmd, findings):
    """Turn findings into the hook's JSON answer (dict) or None."""
    if not findings:
        return None
    hatch = escape_hatch(cmd)
    denies = [f for f in findings if f.tier == "deny" and (f.hard or not hatch)]
    asks = [f for f in findings if f.tier == "ask"]
    advises = [f for f in findings if f.tier == "advise"]
    if hatch:
        advises = []
    if denies:
        lines = []
        for f in denies[:3]:
            lines.append("- " + f.text())
        soft = [f for f in denies if not f.hard]
        reason = "[bash-sense] This command was not run.\n" + "\n".join(lines)
        if soft and not any(f.hard for f in denies):
            reason += "\n→ Re-run with the better form. If the original really is what is needed, append `# bash-sense: off` to the command."
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}
    if asks:
        lines = ["- " + f.text() for f in asks[:3]]
        reason = "[bash-sense] Destructive or outward-facing action, please confirm:\n" + "\n".join(lines)
        out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                      "permissionDecisionReason": reason}}
        return out
    if advises:
        ctxt = "[bash-sense] " + " | ".join(f.text() for f in advises[:3])
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": ctxt}}
    return None


# --------------------------------------------------------------------------------------
# Environment card (SessionStart)
# --------------------------------------------------------------------------------------

HABITS = [
    "search with `rg` (respects .gitignore), find files with `fd`/`find -not -path '*/node_modules/*'`; never `grep -r` a repo",
    "read structured data with its parser: `jq` for JSON, `yq` for YAML; never grep/sed JSON",
    "bound every output: `head -n 50`, `git log --oneline -n 20`, `--stat` before a full diff, `2>&1 | tail -n 40` for builds; summarize (`wc -l`, `rg -l`) before dumping",
    "use the project's own entry points (`npm run lint`, `make test`, `just`) and the package manager its lockfile names; never mix npm/pnpm/yarn/bun or pip/uv/poetry",
    "nothing interactive: no editors, pagers, REPLs, `tail -f`, `watch`, `docker -it`; add `-y`, `--no-edit`, `-m`; servers go to the background with a log, then poll",
    "preview before destroying (`git clean -n`, `rsync -n`, `ls` before `rm`), prefer reversible (`git stash push -m`, `--force-with-lease`), ask before `reset --hard`, force push, `rm -rf` outside tmp/artifacts, `pkill`, `sudo`",
    "compose cleanly: absolute paths or `git -C DIR`/`make -C DIR` instead of `cd`; `--` before paths; quote `\"$var\"`; `$(...)` not backticks; `&&` to stop on failure",
    "match the platform: BSD vs GNU flags (`sed -i ''` on macOS, `fdfind`/`batcat` on Debian); check `command -v TOOL` before relying on it",
    "keep secrets out of the transcript: no `cat .env`, no `env` dumps, no tokens on the command line",
]


def envcard(cwd=None, force=False):
    env = load_env(force=force)
    cwd = cwd or os.getcwd()
    proj = project_info(cwd)
    tools = env.get("tools", {})
    have = [t for t in MODERN_TOOLS if tools.get(t)]
    if tools.get("fdfind") and not tools.get("fd"):
        have.append("fd(as fdfind)")
    if tools.get("batcat") and not tools.get("bat"):
        have.append("bat(as batcat)")
    missing = [t for t in ("rg", "fd", "jq", "yq", "gh", "tree", "timeout") if not tools.get(t) and not (t == "fd" and tools.get("fdfind"))]
    lines = []
    lines.append("## bash-sense · environment card")
    osd = env.get("os_detail") or env.get("os", "")
    who = "root" if env.get("is_root") else "non-root"
    lines.append("- platform: %s %s (%s) · bash %s · %s%s" % (env.get("os", "?"), env.get("arch", ""), osd, env.get("bash_version") or "?", who, "" if env.get("os") != "wsl" else " · WSL"))
    fl = env.get("flavors", {})
    gnu = [t for t in ("sed", "grep", "find", "xargs", "date", "stat") if fl.get(t) == "gnu"]
    bsd = [t for t in ("sed", "grep", "find", "xargs", "date", "stat") if fl.get(t) == "bsd"]
    bb = [t for t in ("sed", "grep", "find", "xargs", "date", "stat") if fl.get(t) == "busybox"]
    flavor_bits = []
    if gnu:
        flavor_bits.append("GNU: " + " ".join(gnu))
    if bsd:
        flavor_bits.append("BSD: " + " ".join(bsd) + " (`sed -i ''`, no `grep -P`/`date -d`/`xargs -d`)")
    if bb:
        flavor_bits.append("busybox: " + " ".join(bb) + " (fewer flags)")
    if fl.get("tar"):
        flavor_bits.append("tar: " + fl["tar"])
    if flavor_bits:
        lines.append("- classic tools: " + " · ".join(flavor_bits))
    vers = env.get("versions", {})
    vbits = ["%s %s" % (k, v) for k, v in sorted(vers.items())]
    lines.append("- modern tools present: " + (" ".join(have) if have else "none of rg/fd/jq/gh") + ((" · missing: " + " ".join(missing)) if missing else ""))
    if vbits:
        lines.append("- versions: " + ", ".join(vbits))
    if not tools.get("python") and tools.get("python3"):
        lines.append("- note: `python` is not on PATH; use `python3`" + ("" if tools.get("pip") else ", `python3 -m pip`"))
    if not tools.get("docker-compose") and env.get("docker_compose_v2"):
        lines.append("- note: use `docker compose` (v2), not `docker-compose`")
    # project
    pb = []
    if proj.get("git"):
        gs = git_state(proj["root"])
        dirty = gs.get("dirty", -1)
        pb.append("git repo at %s (branch %s, %s)" % (proj["root"], gs.get("branch") or "detached", "clean" if dirty == 0 else ("%d changed/untracked paths" % dirty if dirty > 0 else "state unknown")))
    else:
        pb.append("not a git repo (%s)" % cwd)
    if proj.get("js"):
        s = "JS: %s (%s)" % (proj["js"], proj["js_reason"])
        scripts = list((proj.get("scripts") or {}).keys())
        if scripts:
            s += " · scripts: " + " ".join(scripts[:14]) + (" …" if len(scripts) > 14 else "")
        pb.append(s)
    if proj.get("py"):
        runner = {"uv": "`uv run CMD`, `uv add PKG`", "poetry": "`poetry run CMD`, `poetry add PKG`", "pipenv": "`pipenv run CMD`", "pdm": "`pdm run CMD`", "pip": "venv + `pip install -r requirements.txt`", "pyproject": "pyproject.toml"}.get(proj["py"], "")
        s = "Python: %s (%s)" % (proj["py"], proj["py_reason"])
        if runner:
            s += " → " + runner
        if proj.get("venv"):
            s += " · venv: %s%s" % (os.path.relpath(proj["venv"], cwd), " (active)" if proj.get("active_venv") else " (not active: call `.venv/bin/python` directly)")
        pb.append(s)
    tasks = []
    if proj.get("make_targets"):
        tasks.append("Makefile: " + " ".join(proj["make_targets"][:12]) + (" …" if len(proj["make_targets"]) > 12 else ""))
    if proj.get("just_recipes"):
        tasks.append("justfile: " + " ".join(proj["just_recipes"][:12]))
    if proj.get("taskfile"):
        tasks.append("Taskfile (`task --list`)")
    if tasks:
        pb.append("tasks: " + " · ".join(tasks))
    eco = [e for e in proj.get("eco", []) if not e.startswith("typescript")]
    if eco:
        pb.append("also: " + ", ".join(eco[:10]))
    lines.append("- project: " + " · ".join(pb))
    lines.append("- habits that read as good taste here:")
    for h in HABITS:
        lines.append("  - " + h)
    lines.append("- the PreToolUse advisor will stop or annotate commands that break these; `/bash-sense:review CMD` reviews a command, `/bash-sense:env` reprints this card.")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------------------

def _debug(msg):
    if os.environ.get("BASH_SENSE_DEBUG"):
        try:
            with open(os.path.join(os.environ.get("TMPDIR", "/tmp"), "bash-sense-debug.log"), "a") as fh:
                fh.write(msg + "\n")
        except Exception:
            pass


def run_hook():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        return 0
    if os.environ.get("BASH_SENSE", "").lower() in ("off", "0", "false"):
        return 0
    if data.get("tool_name") not in (None, "Bash"):
        return 0
    ti = data.get("tool_input") or {}
    cmd = ti.get("command") if isinstance(ti, dict) else None
    if not cmd or not isinstance(cmd, str) or not cmd.strip():
        return 0
    ctx = Ctx(cmd, tool_input=ti, cwd=data.get("cwd") or os.getcwd())
    findings = lint(cmd, ctx)
    answer = decide(cmd, findings)
    _debug("cmd=%r findings=%s" % (cmd, [(f.tier, f.rule) for f in findings]))
    if answer:
        sys.stdout.write(json.dumps(answer))
        sys.stdout.write("\n")
    return 0


def run_check(cmd, as_json=False, cwd=None):
    ctx = Ctx(cmd, cwd=cwd)
    findings = lint(cmd, ctx)
    if as_json:
        print(json.dumps({"command": cmd, "findings": [{"tier": f.tier, "rule": f.rule, "title": f.title, "detail": f.detail, "hard": f.hard} for f in findings], "answer": decide(cmd, findings)}, indent=2))
        return 0
    if not findings:
        print("ok: no findings for: %s" % cmd)
        return 0
    hatch = escape_hatch(cmd)
    for f in findings:
        suppressed = hatch and not f.hard
        print("[%s %s]%s %s" % (f.tier.upper(), f.rule, " (suppressed: bash-sense off)" if suppressed else "", f.text()))
    answer = decide(cmd, findings)
    if answer is None:
        return 0
    decision = answer["hookSpecificOutput"].get("permissionDecision", "advise")
    return 1 if decision == "deny" else 0


def main(argv):
    try:
        if "--hook" in argv or len(argv) == 1:
            return run_hook()
        if "--envcard" in argv:
            print(envcard(force="--refresh" in argv))
            return 0
        if "--check" in argv:
            i = argv.index("--check")
            cmd = " ".join(argv[i + 1:]) if i + 1 < len(argv) else sys.stdin.read()
            cmd = cmd.strip()
            if cmd.startswith("--json "):
                cmd = cmd[7:]
            return run_check(cmd, as_json="--json" in argv[:i], cwd=os.getcwd())
        if "--parse" in argv:
            i = argv.index("--parse")
            cmd = " ".join(argv[i + 1:])
            for s in parse(cmd):
                print("prog=%r args=%r env=%r wrappers=%r prev=%r next=%r bg=%r nested=%r sub=%r via=%r redirs=%r heredocs=%d" % (
                    s.prog, s.argv, s.env, s.wrappers, s.prev_op, s.next_op, s.background, s.nested, s.subshell, s.via, s.redirs, len(s.heredocs)))
            return 0
        if "--version" in argv:
            print("bash-sense " + VERSION)
            return 0
        print(__doc__)
        return 0
    except SystemExit:
        raise
    except Exception as e:  # fail open
        _debug("error: %r" % (e,))
        if "--hook" not in argv and len(argv) > 1:
            raise
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
