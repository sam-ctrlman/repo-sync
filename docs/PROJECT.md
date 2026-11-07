# repo-sync

<!-- created 2026-11-05 · board prefix TOOL-REPOSYNC · workspace tools -->

## Goal
Safe multi-repo Git sync CLI over SSH: automatic git bundle backups, retention pruning, worktree discovery, and fast-forward-only push/pull.

## Users
A developer syncing many repos between machines through a self-hosted server before a full git forge exists.

## Scope
- In: `status`, `push`, `pull`, `new` (plain bare repo or `--origin` seeded as the web user), `prune`, `install` / `uninstall`.
- Out (non-goals): force pushes, deleting remote branches, merging diverged history, setting up the server or the network.

## Depends on / used by
`git`, `ssh`, `scp`, Python ≥ 3.11. Config lives outside the repo (`~/.config/repo-sync/config.toml`). Replaces the bare-repo creation job of `git-upload` (board TOOL-GUP-RETIRE).
