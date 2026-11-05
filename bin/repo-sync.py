#!/usr/bin/env python3
"""repo-sync — TODO: one-line description of what this tool automates.

Usage:
  repo-sync               # run the workflow (default)
  repo-sync install       # symlink into ~/.local/bin + ~/.claude/skills
  repo-sync uninstall     # remove those symlinks
  repo-sync --help

Scaffolded by `forge`. The tool name is derived from the repo directory, so
nothing here is hardcoded — rename the repo and it follows.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess  # noqa: F401  (kept for convenience when adding workflow steps)
import sys
from pathlib import Path


def log(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}", flush=True)


def die(msg: str, code: int = 1):
    log("error", msg)
    sys.exit(code)


TOOL_NAME = Path(__file__).resolve().parent.parent.name
SYMLINK_TARGET = Path.home() / ".local" / "bin" / TOOL_NAME
CLAUDE_SKILL_TARGET = Path.home() / ".claude" / "skills" / TOOL_NAME
LEGACY_COMMAND_TARGET = Path.home() / ".claude" / "commands" / f"{TOOL_NAME}.md"


def _skill_source() -> Path:
    return Path(__file__).resolve().parent.parent / "skills" / TOOL_NAME


# ---------- the workflow ----------

def cmd_run() -> int:
    # TODO: replace this body with the deterministic steps this tool automates.
    # Keep every step idempotent: begin with a [skip] fast-path when the work
    # is already done. Use log("ok"/"skip"/"install"/"notice"/"error", ...).
    log("notice", f"{TOOL_NAME}: workflow not implemented yet — edit {Path(__file__).resolve()}")
    return 0


# ---------- install / uninstall ----------

def cmd_install() -> int:
    script_path = Path(__file__).resolve()
    mode = script_path.stat().st_mode
    if not (mode & 0o111):
        script_path.chmod(mode | 0o755)
        log("ok", f"made script executable: {script_path}")
    else:
        log("skip", f"script already executable: {script_path}")
    _install_bin_symlink(script_path)
    _install_claude_skill()
    return _install_check_path()


def _install_bin_symlink(script_path: Path) -> None:
    SYMLINK_TARGET.parent.mkdir(parents=True, exist_ok=True)
    if SYMLINK_TARGET.is_symlink():
        current = SYMLINK_TARGET.resolve()
        if current == script_path:
            log("skip", f"bin symlink already correct: {SYMLINK_TARGET} -> {script_path}")
            return
        log("info", f"replacing existing bin symlink ({SYMLINK_TARGET} -> {current})")
        SYMLINK_TARGET.unlink()
    elif SYMLINK_TARGET.exists():
        die(f"{SYMLINK_TARGET} exists and is not a symlink — refusing to overwrite")
    SYMLINK_TARGET.symlink_to(script_path)
    log("ok", f"bin symlink created: {SYMLINK_TARGET} -> {script_path}")


def _install_claude_skill() -> None:
    source = _skill_source()
    if not (source / "SKILL.md").exists():
        log("notice", f"skill source not found at {source / 'SKILL.md'} — skipping Claude integration")
        return
    _remove_legacy_command()
    target = CLAUDE_SKILL_TARGET
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() and target.resolve() == source:
        log("skip", f"skill symlink already correct: {target} -> {source}")
    else:
        if target.is_symlink():
            log("info", f"replacing existing skill symlink ({target} -> {target.resolve()})")
            target.unlink()
        elif target.exists():
            die(f"{target} exists and is not a symlink — refusing to overwrite")
        target.symlink_to(source)
        log("ok", f"skill symlink created: {target} -> {source}")
    _check_skill_ignored()


def _remove_legacy_command() -> None:
    # Before TOOL-SKILLDIR this tool linked ~/.claude/skills/<tool>.md; a leftover link would clash with the skill.
    legacy = LEGACY_COMMAND_TARGET
    if not legacy.is_symlink():
        if legacy.exists():
            log("notice", f"{legacy} is a real file, not this tool's link — left alone; remove it by hand if it duplicates the skill")
        return
    was = legacy.resolve()
    if _skill_source().parent.parent in was.parents:
        legacy.unlink()
        log("ok", f"removed old slash command symlink: {legacy} (was -> {was})")
    else:
        log("notice", f"{legacy} points outside this tool ({was}) — left alone")


def _check_skill_ignored() -> None:
    # Warn only, never edit ~/.claude/.gitignore (TOOL-SKILLDIR D3=a).
    claude_dir = CLAUDE_SKILL_TARGET.parent.parent
    rel = f"skills/{CLAUDE_SKILL_TARGET.name}"
    try:
        r = subprocess.run(["git", "-C", str(claude_dir), "check-ignore", "-q", rel], capture_output=True)
    except FileNotFoundError:
        return
    # 0 = ignored · 1 = not ignored · 128 = ~/.claude is not a git repo (fresh machine): stay quiet
    if r.returncode == 1:
        log("notice", f"{CLAUDE_SKILL_TARGET} is not git-ignored — add this line to {claude_dir}/.gitignore:  /{rel}")

def _install_check_path() -> int:
    on_path = shutil.which(TOOL_NAME)
    if on_path:
        log("ok", f"{TOOL_NAME} on PATH: {on_path}")
        return 0
    log("notice", f"{SYMLINK_TARGET.parent} is not on PATH — add this to your shell rc:")
    log("notice", f'  export PATH="{SYMLINK_TARGET.parent}:$PATH"')
    return 0


def cmd_uninstall() -> int:
    script_path = Path(__file__).resolve()
    _uninstall_bin_symlink()
    _uninstall_claude_skill()
    log("info", f"to reinstall: python3 {script_path} install")
    return 0


def _uninstall_bin_symlink() -> None:
    if not SYMLINK_TARGET.is_symlink() and not SYMLINK_TARGET.exists():
        log("info", f"no bin symlink at {SYMLINK_TARGET} to remove")
        return
    if not SYMLINK_TARGET.is_symlink():
        die(f"{SYMLINK_TARGET} exists but is not a symlink — refusing to remove")
    target = SYMLINK_TARGET.resolve()
    SYMLINK_TARGET.unlink()
    log("ok", f"removed bin symlink: {SYMLINK_TARGET} (was -> {target})")


def _uninstall_claude_skill() -> None:
    target = CLAUDE_SKILL_TARGET
    if not target.is_symlink() and not target.exists():
        log("info", f"no skill symlink at {target} to remove")
    elif not target.is_symlink():
        die(f"{target} exists but is not a symlink — refusing to remove")
    else:
        was = target.resolve()
        target.unlink()
        log("ok", f"removed skill symlink: {target} (was -> {was})")
    _remove_legacy_command()

# ---------- entry point ----------

def main() -> int:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="TODO: repo-sync — describe what this tool does.",
    )
    parser.add_argument(
        "action",
        nargs="?",
        default="run",
        choices=["run", "install", "uninstall"],
        help="run (default) = do the work; install/uninstall = manage symlinks",
    )
    args = parser.parse_args()
    if args.action == "install":
        return cmd_install()
    if args.action == "uninstall":
        return cmd_uninstall()
    return cmd_run()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("error", "interrupted")
        sys.exit(130)
