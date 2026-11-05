# repo-sync

TODO: one-line description of what repo-sync automates.

## Install

```sh
git clone <remote-url> ~/tools/projects/repo-sync
python3 ~/tools/projects/repo-sync/bin/repo-sync.py install
```

After install, `repo-sync` works in any shell and `/repo-sync` works in Claude Code.

## Actions

| Action | What it does |
|--------|-------------|
| `repo-sync` / `repo-sync run` | TODO: the automated workflow |
| `repo-sync install` | Create bin + Claude command symlinks |
| `repo-sync uninstall` | Remove both symlinks |

## Layout

```
~/tools/projects/repo-sync/
├── README.md
├── bin/repo-sync.py
└── skills/repo-sync/SKILL.md
```

## Next steps after scaffolding

1. Edit `bin/repo-sync.py` — replace the `cmd_run()` body with the deterministic
   steps this tool should automate. Keep each step idempotent (start with a
   `[skip]` fast-path when the work is already done).
2. Update this README and the `description:` in `skills/repo-sync/SKILL.md`.
3. `git add -A && git commit` the changes.
4. Before publishing: `forge scan repo-sync` must exit 0.
5. (Optional) add a remote and `git push -u origin main`.
