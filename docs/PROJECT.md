# repo-sync

<!-- created 2026-11-05 · board prefix TOOL-REPOSYNC · workspace tools -->

## Goal
Push and pull git repos to a bare-repo server over ssh with one command per sync: fast-forward only, a bundle first, old bundles pruned (older than `keep_days` AND not in the newest `keep_last`).

## Users
A developer syncing many repos between machines through a self-hosted server before a full git forge exists.

## Scope
- In: `status`, `push`, `pull`, `new` (plain bare repo or `--origin` seeded as the web user), `prune`, `install` / `uninstall`.
- Out (non-goals): force pushes, deleting remote branches, merging diverged history, setting up the server or the network.

## Depends on / used by
`git`, `ssh`, `scp`, Python ≥ 3.11. Config lives outside the repo (`~/.config/repo-sync/config.toml`). Replaces the bare-repo creation job of `git-upload` (board TOOL-GUP-RETIRE).
