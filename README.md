# koucha

Personal site: https://kouchaweb.github.io

## Claude Code plugins

This repository doubles as a Claude Code plugin marketplace (`.claude-plugin/marketplace.json`).

### bash-sense

Good taste in Bash for AI agents: an environment card at session start, a skill that teaches the
command a seasoned engineer would type, and a PreToolUse advisor that blocks commands that would
hang or misuse tools, asks before destructive ones, and nudges the rest.

```
/plugin install bash-sense --marketplace kouchaweb/kouchaweb.github.io
```

Details, examples and configuration: [plugins/bash-sense/README.md](plugins/bash-sense/README.md).
