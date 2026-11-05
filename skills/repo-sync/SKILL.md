---
name: repo-sync
description: "TODO: repo-sync — describe what this slash command does"
argument-hint: "[run|install|uninstall]"
disable-model-invocation: true
---

Run `repo-sync $ARGUMENTS` from the user's current directory using the Bash tool.

`repo-sync` is a Python tool at `~/tools/projects/repo-sync/bin/repo-sync.py` (symlinked to `~/.local/bin/repo-sync`). This skill is at `~/tools/projects/repo-sync/skills/repo-sync/SKILL.md` (its folder is symlinked to `~/.claude/skills/repo-sync`).

Actions:

- **`repo-sync`** or **`repo-sync run`** — TODO: the workflow this tool automates (not implemented yet — edit `cmd_run()` in the script).
- **`repo-sync install`** — create both symlinks (bin + Claude skill). Idempotent.
- **`repo-sync uninstall`** — remove both symlinks. Idempotent.

After it returns, surface the `[ok]` / `[error]` / `[notice]` log lines to the user. Do NOT improvise steps the tool does not perform — the tool owns its workflow.

Pass through `$ARGUMENTS` verbatim. Valid args: `run`, `install`, `uninstall`, or empty (defaults to `run`).
