---
name: env
description: Reprint the bash-sense environment card (installed modern tools, GNU/BSD flavors of sed/grep/find/date, package manager and scripts of the current project, git state). Use when the user asks which tools are available, after installing tools or changing directory, or when a command failed with "command not found".
argument-hint: ""
disable-model-invocation: false
allowed-tools: Bash
---

# bash-sense · environment card (refreshed now)

!`python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/bash_sense.py" --envcard --refresh 2>/dev/null || bash "${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh"`

Use the card above when choosing commands: prefer the tools it lists as present, use the flavor-specific
flags it names, and run project tasks through the package manager and scripts it detected.
If a tool you need is missing, say so and pick the fallback instead of guessing.
