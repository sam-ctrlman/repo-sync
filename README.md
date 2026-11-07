# repo-sync

**Safe multi-repository Git sync CLI and Claude Code skill over SSH — automates `git bundle` pre-sync backups, retention pruning, worktree discovery, and fast-forward-only push/pull to bare-repo servers.**

Synchronizing dozens of Git repositories across machines through a self-hosted SSH server (plain bare repositories without a heavy web forge) normally requires repeating the same 5 manual steps in every directory: create a backup bundle, `git fetch`, check ahead/behind status, push without forcing, and verify remote refs. `repo-sync` discovers all repositories under your configured workspace roots (including git worktrees and nested config repos) and runs those steps deterministically in a single command.

---

## Why Use `repo-sync`?

- **Strict Fast-Forward Safety:** Never passes `--force`, never deletes remote branches (`--prune` is never used), and refuses immediately if a branch has diverged or if `pull` sees uncommitted local changes.
- **Automated Pre-Sync `git bundle` Backups:** Before every `push`, `pull`, or `new --origin` operation, `repo-sync` writes and verifies (`git bundle verify`) a full local bundle of all branches, tags, and `HEAD` — skipping bundle creation automatically when refs haven't changed since the latest bundle.
- **Smart Retention Pruning (`keep_days` + `keep_last`):** Automatically prunes bundles older than `keep_days` (default `45`) **only** when they are not among the newest `keep_last` (default `3`) bundles for that repository, so inactive projects never lose their last 3 backups.
- **Multi-Root & Worktree Discovery:** Scans configured `[roots]` up to 4 levels deep, deduplicating git worktrees so each repository is processed once and flagging local-only branches (`⚠ local-only`) or unbacked repositories (`Not on <remote>`).
- **One-Command Bare Repo Provisioning (`new` & `new --origin`):** Initializes bare repositories over SSH (`git init --bare`) or seeds a web-served bare repository (`new --origin` via `scp` + `sudo -n -u <user> git clone --bare`).
- **Zero External Python Dependencies:** Uses Python ≥ 3.11 standard library (`tomllib`), `git`, and standard `ssh`/`scp`.

---

## Example Output

### `repo-sync status`
```text
$ repo-sync status
REPO              BRANCH  ↑/↓  DIRTY  PUSH  LAST BUNDLE  NOTES
projects/web-app  main    2/0         1     2026-11-06   ⚠ local-only: spike/ experiment
projects/api      main    0/0  yes          2026-11-05
tools/claude-audit main   0/0               2026-11-07

Not on backup (1): create with `repo-sync new <path|name>`
  tools/new-helper                   ~/tools/projects/new-helper · ⚠ local-only: main
```

### `repo-sync push --all`
```text
$ repo-sync push --all
[ok] projects/web-app: bundle ~/backup/repo-sync/projects/web-app/2026-11-07-151022-push.bundle (148 KiB)
[ok] projects/web-app: pushed main — remote = local
[skip] projects/api: up to date
[skip] tools/claude-audit: up to date
```

---

## Installation & Setup

```sh
git clone <remote-url> ~/tools/projects/repo-sync
python3 ~/tools/projects/repo-sync/bin/repo-sync.py install
mkdir -p ~/.config/repo-sync
cp ~/tools/projects/repo-sync/config.example.toml ~/.config/repo-sync/config.toml
```

**Requirements:**
- `git` and `ssh` / `scp` on `PATH` (with SSH access to your bare-repo server)
- Python `>= 3.11` (uses built-in `tomllib`)
- `~/.config/repo-sync/config.toml` (or set `$REPO_SYNC_CONFIG`)

Once installed:
1. Run `repo-sync` from **any terminal shell**.
2. Run `/repo-sync` directly **inside Claude Code**.

To remove the symlinks:
```sh
repo-sync uninstall
```

---

## Commands & Options

| Command | Description |
| :--- | :--- |
| `repo-sync status [repo…] [--no-fetch]` | Fetch from the sync remote (unless `--no-fetch`) and display a unified table: `REPO`, `BRANCH`, ahead/behind (`↑/↓`), `DIRTY`, branches queued for `PUSH`, `LAST BUNDLE` date, `NOTES` (`behind`, `DIVERGED`, `⚠ local-only`), plus any local repos not yet on the server. |
| `repo-sync push [repo…\|--all] [-n]` | Fetch → refuse if any selected branch diverged → create & verify `.bundle` → fast-forward `git push --follow-tags` → verify remote SHA matches local SHA → set upstream if missing → prune old bundles. |
| `repo-sync pull [repo…\|--all] [-n]` | Refuse on uncommitted changes or detached `HEAD` → fetch → create & verify `.bundle` → `git merge --ff-only` of current branch → prune old bundles. |
| `repo-sync new <repo> [--as GROUP/NAME] [-n]` | Create `<base>/<GROUP>/<NAME>.git` on the SSH server (`git init --bare`, refusing if path exists) and add the configured sync remote locally. |
| `repo-sync new <repo> --origin [--as GROUP/NAME] [-n]` | Bundle `<repo>`, copy via `scp`, clone as a bare repo under `[origin] base` (as `[origin] user`), and add the `origin` remote pointing to `<url>/<GROUP>/<NAME>.git`. |
| `repo-sync prune [-n]` | Delete `.bundle` files older than `keep_days` that are not among the newest `keep_last` bundles of their repository. |
| `repo-sync install` / `uninstall` | Create or remove `~/.local/bin/repo-sync` and `~/.claude/skills/repo-sync` symlinks. |

- **Targeting Repos:** `<repo>` accepts either the logical `GROUP/NAME` shown in `status` or the top-level filesystem path of a repository. Omitting `<repo>` and `--all` targets the repository of your current working directory (`$PWD`).
- **Dry-Run Mode (`-n` / `--dry-run`):** Runs `git fetch` read-only and prints what bundles, pushes, merges, or prunes *would* happen without writing anything to disk or the remote server.

---

## Configuration (`~/.config/repo-sync/config.toml`)

```toml
host = "git-server"                 # SSH host alias from ~/.ssh/config ("" = local filesystem)
base = "/srv/git"                   # Bare repos live at <base>/<GROUP>/<NAME>.git
remote = "backup"                   # Git remote name pointing to <host>:<base>/...
exclude = ["*/node_modules", "*/Downloads/*", "*/.gemini", "*/.codex", "*/.antigravity", "*/.agents"]
bundle_dir = "~/backup/repo-sync"   # Pre-sync bundles stored at <bundle_dir>/<GROUP>/<NAME>/
keep_days = 45                      # Prune only when older than 45 days ...
keep_last = 3                       # ... AND not among the newest 3 bundles for that repo

# Folders searched for git repos (up to 4 levels deep) -> server GROUP name
[roots]
"~/projects" = "projects"
"~/tools" = "tools"

# Per-root branch globs pushed even when they have no upstream yet
[branches]
"~/projects" = ["main", "production", "feat/*", "fix/*"]

# Optional: `repo-sync new --origin` seeds a bare repo served over HTTP/HTTPS
[origin]
base = "/var/www/git"
user = "www-data"
url = "https://git.example.com/repos"
```

### Which Branches `push` Sends
1. The currently checked-out branch (`HEAD`).
2. Every local branch whose configured upstream remote matches `remote`.
3. Every local branch matching the `[branches]` glob list for its root folder — even if newly created locally.
4. Annotated tags reachable from the pushed branches (`--follow-tags`).

All other local branches remain untouched and appear as `⚠ local-only` in `repo-sync status`.

---

## Restoring from a Safety Bundle

Bundles are stored at `<bundle_dir>/<GROUP>/<NAME>/YYYY-MM-DD-HHMMSS-<push|pull|origin>.bundle` and contain all local branches, tags, and `HEAD`.

Clone a full repository from a bundle:
```sh
git clone ~/backup/repo-sync/projects/web-app/2026-11-07-151022-push.bundle restored-web-app
```

Or fetch branches from a bundle into an existing repository without overwriting active branches:
```sh
git fetch ~/backup/repo-sync/projects/web-app/2026-11-07-151022-push.bundle 'refs/heads/*:refs/heads/restored/*'
```

---

## Exit Codes & Project Layout

### Exit Codes
- `0` — All requested operations succeeded.
- `1` — One or more repositories failed or were refused (remaining repositories in a batch still run).
- `2` — CLI usage error or invalid/missing `config.toml`.

### Directory Structure
```text
~/tools/projects/repo-sync/
├── LICENSE
├── README.md
├── config.example.toml         # Template configuration file
├── bin/repo-sync.py            # CLI executable & SSH/bundle engine
├── docs/
│   ├── PROJECT.md
│   └── STATUS.md
└── skills/repo-sync/SKILL.md   # Claude Code /repo-sync skill definition
```

---

## Frequently Asked Questions (FAQ)

**What happens if a branch has diverged between my machine and the SSH server?**  
`repo-sync` refuses to push or pull that repository (`Refused: diverged from <remote>`) and exits with code `1` while continuing to process any other repositories in the batch. It never runs `git push --force` or creates automatic merge commits.

**Does `repo-sync` create duplicate bundles if I run `repo-sync push --all` twice in a row?**  
No. Before creating a bundle, `repo-sync` compares the repository's current `refs/heads/*` and `refs/tags/*` against `git bundle list-heads` of the newest existing bundle. If no refs changed, bundle creation is skipped.

**How does `repo-sync` handle Git worktrees and nested `.claude` memory repositories?**  
Worktrees sharing the same `--git-common-dir` are deduplicated so the repository is synced once. Hidden nested repositories (such as `<project>/.claude`) are automatically named `<GROUP>/<project>.claude` so they never collide across projects.
