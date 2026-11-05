# repo-sync

Push and pull your git repos to a plain bare-repo server over ssh, safely: fast-forward only, and a bundle of the repo is saved before every change, with automatic cleanup.

## Why

Syncing many repos by hand to a self-hosted server (no Gitea/Forgejo yet, just `ssh` + bare repos) is the same 4–5 steps every time: back up → fetch → check ahead/behind → push → verify. `repo-sync` does them in one command, the same way every time:

1. **Never loses work** — it only fast-forwards, never forces, never deletes a remote branch.
2. **Backup without waste** — a bundle before each push/pull, skipped when nothing changed, pruned by a "keep N, then expire" rule.
3. **Nothing missed** — `status` shows branches that exist on no remote and repos not on the server yet.

## Install

```sh
git clone <remote-url> ~/tools/projects/repo-sync
python3 ~/tools/projects/repo-sync/bin/repo-sync.py install
cp ~/tools/projects/repo-sync/config.example.toml ~/.config/repo-sync/config.toml   # then edit
```

Needs `git`, `ssh`/`scp` and Python ≥ 3.11 (standard library only).

## Commands

| Command | What it does |
|---|---|
| `repo-sync status [repo…] [--no-fetch]` | One table: branch, ahead/behind (`↑/↓`), dirty, branches to push, last bundle, notes (`behind`, `DIVERGED`, `⚠ local-only` branches). Then repos found that are **not on the server** yet. |
| `repo-sync push [repo…\|--all] [-n]` | fetch → refuse if any selected branch diverged → bundle → `git push` (no force) → check remote = local → set upstream if a branch had none → prune. |
| `repo-sync pull [repo…\|--all] [-n]` | refuse on uncommitted changes → fetch → bundle → `merge --ff-only` of the current branch. Diverged = refused. |
| `repo-sync new <repo> [--as GROUP/NAME] [-n]` | `git init --bare` at `<base>/GROUP/NAME.git` on the server and add the remote. Refuses if either exists. |
| `repo-sync new <repo> --origin [-n]` | Seed a repo under `[origin] base` as `[origin] user` (bundle → scp → `sudo -n -u <user> git clone --bare`) and set the `origin` remote to `<url>/GROUP/NAME.git`. |
| `repo-sync prune [-n]` | Delete bundles older than `keep_days` that are not among the newest `keep_last` of their repo. Runs by itself after each push/pull. |
| `repo-sync install` / `uninstall` | Symlinks `~/.local/bin/repo-sync` and `~/.claude/skills/repo-sync`. |

`<repo>` is a path or a name as `status` prints it (`GROUP/NAME`). No repo and no `--all` = the repo you are standing in. `-n` = dry run: it still runs `git fetch`, nothing else is written.

### Which branches `push` sends

- the current branch,
- every branch whose upstream is the sync remote,
- every local branch matching the `[branches]` globs of its folder — even if it is new,
- plus annotated tags reachable from them (`--follow-tags`).

Anything else stays local and shows as `⚠ local-only` in `status`.

### Finding repos

Every folder under a `[roots]` entry, up to 4 levels deep, skipping `node_modules`, build folders and `exclude` globs. A configured root is never excluded, even if a glob matches it. Worktrees of one repo count once. Two repos that would get the same name become `GROUP/<parent>.<folder>`; a name that still matches several repos is refused — name it by path (the repo's top folder only). A repo is "on the server" when it has the remote named `remote`; its name is the remote path minus `base`.

## Bundles

`<bundle_dir>/GROUP/NAME/YYYY-MM-DD-HHMMSS-<push|pull|origin>.bundle` — every local branch and tag plus `HEAD`. Restore with `git clone <file>` or `git fetch <file> 'refs/heads/*:refs/heads/restored/*'`. Prune only ever touches `*.bundle` files inside `bundle_dir`.

## Exit codes

0 ok · 1 a repo failed or was refused (the others still ran) · 2 usage or config error.

## Layout

```
~/tools/projects/repo-sync/
├── README.md
├── config.example.toml
├── bin/repo-sync.py
├── docs/PROJECT.md, docs/STATUS.md
└── skills/repo-sync/SKILL.md
```
