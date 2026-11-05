---
name: repo-sync
description: "Push / pull / status of git repos against the bare-repo sync server over ssh: fast-forward only, safety bundle first, old bundles pruned. Also creates new server repos (`new`, `new --origin`)."
argument-hint: "[status|push|pull|new|prune] [repo…|--all] [-n]"
disable-model-invocation: true
---

Run `repo-sync $ARGUMENTS` from the user's current directory using the Bash tool.

`repo-sync` is a Python tool at `~/tools/projects/repo-sync/bin/repo-sync.py` (symlinked to `~/.local/bin/repo-sync`). This skill is at `~/tools/projects/repo-sync/skills/repo-sync/SKILL.md` (its folder is symlinked to `~/.claude/skills/repo-sync`).

## Requires

- `git`, `ssh`, `scp` on PATH
- `python3` ≥ 3.11 (`tomllib`)
- `~/.config/repo-sync/config.toml` (copy `config.example.toml`)

## Step 0 — preflight

One Bash call; all OK → say nothing; anything missing → one line (what + hint), then stop. Never install anything.

```bash
command -v git >/dev/null && command -v ssh >/dev/null && python3 -c 'import tomllib' 2>/dev/null && test -f ~/.config/repo-sync/config.toml && echo OK
```

## Actions

- **`status [repo…] [--no-fetch]`** — read-only apart from `git fetch`. Run freely.
- **`push` / `pull` / `new` / `new --origin`** — write to the server or the repo. These are gated steps: show them in a runbook first (push / new = ⛔ remote), run only after the gate keyword. `-n` (dry run) is read-only.
- **`prune [-n]`** — deletes old bundles inside the configured bundle folder only.
- **`install` / `uninstall`** — symlinks. Idempotent.

After it returns, surface the `[ok]` / `[skip]` / `[dry]` / `[notice]` / `[error]` lines and the status table. Exit 1 = at least one repo failed or was refused (the rest still ran): report which and why; never retry a push blindly — run `repo-sync status <repo>` first. Do NOT improvise steps the tool does not perform, and never "fix" a refusal with `git push --force`.
