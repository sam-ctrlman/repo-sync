#!/usr/bin/env python3
"""repo-sync — push / pull git repos to a bare-repo server over ssh, with a safety bundle first.

Usage:
  repo-sync status [repo ...] [--no-fetch]
  repo-sync push   [repo ...|--all] [-n]
  repo-sync pull   [repo ...|--all] [-n]
  repo-sync new    <repo> [--origin] [--as GROUP/NAME] [-n]
  repo-sync prune  [-n]
  repo-sync install | uninstall

<repo> = a path, or a name as `status` shows it (GROUP/NAME). No repo and no --all = the repo you are in.
Config: ~/.config/repo-sync/config.toml (or $REPO_SYNC_CONFIG); see config.example.toml.

Rules the tool keeps:
  - fast-forward only: never --force, never deletes a remote branch (no --prune)
  - a bundle of every local branch + tag is written before each push / pull / origin seed,
    skipped when the refs equal the newest bundle
  - prune deletes a bundle only when it is older than keep_days AND not one of the repo's keep_last newest

Exit codes: 0 ok · 1 a repo failed or was refused · 2 usage / config error.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
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

CONFIG_PATH = Path(os.environ.get("REPO_SYNC_CONFIG", "~/.config/repo-sync/config.toml")).expanduser()
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", ".next", "dist", "build", ".cache", ".turbo"}
MAX_DEPTH = 4
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
os.environ.setdefault("GIT_SSH_COMMAND", "ssh " + " ".join(SSH_OPTS))
os.environ["GIT_TERMINAL_PROMPT"] = "0"


class CmdError(Exception):
    def __init__(self, args: list[str], r: subprocess.CompletedProcess):
        err = (r.stderr or r.stdout or "").strip().splitlines()
        super().__init__(f"`{' '.join(args)}` exit {r.returncode}: {err[-1] if err else ''}")
        self.returncode = r.returncode


class Refused(Exception):
    pass


def xp(s: str) -> Path:
    return Path(os.path.expanduser(s))


@dataclass
class Config:
    host: str
    base: str
    remote: str
    roots: dict[Path, str]
    exclude: list[str]
    bundle_dir: Path
    keep_days: int
    keep_last: int
    branches: dict[Path, list[str]]
    origin_base: str = ""
    origin_user: str = ""
    origin_url: str = ""
    origin_remote: str = "origin"


def load_config() -> Config:
    if not CONFIG_PATH.exists():
        die(f"no config at {CONFIG_PATH} — copy config.example.toml there and edit it", 2)
    try:
        with CONFIG_PATH.open("rb") as f:
            c = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        die(f"config {CONFIG_PATH}: {e}", 2)
    try:
        o = c.get("origin", {})
        cfg = Config(
            host=c.get("host", ""),
            base=c["base"].rstrip("/"),
            remote=c["remote"],
            roots={xp(k): v for k, v in c["roots"].items()},
            exclude=[os.path.expanduser(p) for p in c.get("exclude", [])],
            bundle_dir=xp(c["bundle_dir"]),
            keep_days=int(c.get("keep_days", 45)),
            keep_last=int(c.get("keep_last", 3)),
            branches={xp(k): list(v) for k, v in c.get("branches", {}).items()},
            origin_base=o.get("base", "").rstrip("/"),
            origin_user=o.get("user", ""),
            origin_url=o.get("url", "").rstrip("/"),
            origin_remote=o.get("remote", "origin"),
        )
    except KeyError as e:
        die(f"config {CONFIG_PATH}: missing key {e}", 2)
    if cfg.bundle_dir.resolve() in (Path("/"), Path.home().resolve()) or len(cfg.bundle_dir.resolve().parts) < 3:
        die(f"config: bundle_dir {cfg.bundle_dir} is too broad — use a dedicated folder", 2)
    return cfg


# ----- shell helpers -----

def run(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    if check and r.returncode != 0:
        raise CmdError(args, r)
    return r


def git(p: Path, *args: str, check: bool = True) -> str:
    return run(["git", "-C", str(p), *args], check=check).stdout.strip()


def remote_sh(cfg: Config, script: str) -> subprocess.CompletedProcess:
    if cfg.host:
        return run(["ssh", *SSH_OPTS, cfg.host, script], check=False)
    return run(["sh", "-c", script], check=False)


def q(s: str) -> str:
    return shlex.quote(s)


# ----- repo model -----

@dataclass
class Repo:
    path: Path
    common: Path
    group: str
    url: str = ""
    name: str = ""            # GROUP/NAME on the server ("" = not on the server yet)
    patterns: list[str] = field(default_factory=list)
    alias: str = ""           # set by discover() when two repos would get the same suggested name

    @property
    def suggested(self) -> str:
        return self.alias or f"{self.group}/{self.path.name}"

    @property
    def key(self) -> str:
        return self.name or self.suggested


def _longest(path: Path, table: dict[Path, object]):
    best = None
    for k in table:
        if path == k or k in path.parents:
            if best is None or len(k.parts) > len(best.parts):
                best = k
    return best


def name_from_url(cfg: Config, url: str) -> str:
    path = url
    if cfg.host and url.startswith(cfg.host + ":"):
        path = url[len(cfg.host) + 1:]
    elif url.startswith("ssh://"):
        path = "/" + url.split("/", 3)[3] if url.count("/") >= 3 else url
    if path.startswith(cfg.base + "/"):
        path = path[len(cfg.base) + 1:]
    return path[:-4] if path.endswith(".git") else path


def make_repo(cfg: Config, found: Path, common: Path) -> Repo:
    path = common.parent if common.name == ".git" else found
    root = _longest(path, cfg.roots)
    group = cfg.roots[root] if root is not None else "misc"
    url = git(path, "config", "--get", f"remote.{cfg.remote}.url", check=False)
    pat_root = _longest(path, cfg.branches)
    return Repo(
        path=path, common=common, group=group, url=url,
        name=name_from_url(cfg, url) if url else "",
        patterns=cfg.branches[pat_root] if pat_root is not None else [],
    )


def common_dir(d: Path) -> Path | None:
    out = git(d, "rev-parse", "--path-format=absolute", "--git-common-dir", check=False)
    return Path(out).resolve() if out else None


def excluded(cfg: Config, d: Path) -> bool:
    s = str(d)
    return any(fnmatch.fnmatch(s, pat) for pat in cfg.exclude)


def discover(cfg: Config) -> list[Repo]:
    seen: dict[Path, Repo] = {}
    for root in cfg.roots:
        if not root.is_dir():
            log("notice", f"root not found, skipped: {root}")
            continue
        stack = [(root, 0)]
        while stack:
            d, depth = stack.pop()
            if d not in cfg.roots and excluded(cfg, d):   # a configured root is never excluded
                continue
            if (d / ".git").exists():
                c = common_dir(d)
                if c is not None and c not in seen:
                    seen[c] = make_repo(cfg, d, c)
            if depth >= MAX_DEPTH:
                continue
            try:
                kids = [x for x in d.iterdir() if x.is_dir() and not x.is_symlink() and x.name not in SKIP_DIRS]
            except OSError:
                continue
            stack.extend((x, depth + 1) for x in sorted(kids, reverse=True))
    repos = list(seen.values())
    taken: dict[str, list[Repo]] = {}
    for r in repos:
        taken.setdefault(r.suggested, []).append(r)
    for same in taken.values():
        if len(same) > 1:   # e.g. several <project>/.claude repos: GROUP/<parent>.<name>
            for r in same:
                r.alias = f"{r.group}/{r.path.parent.name}.{r.path.name}"
    return sorted(repos, key=lambda r: (r.name == "", r.key))


def toplevel(p: Path) -> Path | None:
    out = git(p, "rev-parse", "--show-toplevel", check=False)
    return Path(out).resolve() if out else None


def select(cfg: Config, repos: list[Repo], names: list[str], all_: bool, need_remote: bool = True) -> list[Repo]:
    if all_:
        return [r for r in repos if r.url] if need_remote else repos
    if not names:
        c = common_dir(Path.cwd())
        if c is None:
            die("not inside a git repo — name a repo or use --all", 2)
        hit = [r for r in repos if r.common == c]
        if hit:
            return hit
        die(f"{Path.cwd()} is not under a configured root (or is excluded)", 2)
    out = []
    for n in names:
        # 1) a name as `status` prints it; 2) a path, but only a repo's top folder
        #    (a subfolder like "tools/.claude" must not resolve to the repo around it)
        hit = [r for r in repos if n in (r.name, r.suggested)]
        if len(hit) > 1:
            die(f"'{n}' matches {len(hit)} repos: {', '.join(_home(r.path) for r in hit)} — name one by path", 2)
        if not hit:
            p = xp(n).resolve()
            if p.is_dir() and toplevel(p) == p and (c := common_dir(p)) is not None:
                hit = [r for r in repos if r.common == c] or [make_repo(cfg, p, c)]
        if not hit:
            die(f"no repo matches '{n}' — see `repo-sync status`", 2)
        out.extend(hit)
    return out


# ----- git state -----

def refs(p: Path, prefix: str) -> dict[str, str]:
    out = git(p, "for-each-ref", "--format=%(objectname) %(refname)", prefix)
    res = {}
    for line in out.splitlines():
        sha, ref = line.split(" ", 1)
        res[ref[len(prefix):]] = sha
    return res


def local_branches(p: Path) -> dict[str, str]:
    return refs(p, "refs/heads/")


def remote_branches(p: Path, remote: str) -> dict[str, str]:
    return {b: s for b, s in refs(p, f"refs/remotes/{remote}/").items() if b != "HEAD"}


def on_any_remote(p: Path) -> set[str]:
    names = set()
    for r in refs(p, "refs/remotes/"):
        parts = r.split("/", 1)
        if len(parts) == 2 and parts[1] != "HEAD":
            names.add(parts[1])
    return names


def current_branch(p: Path) -> str:
    return git(p, "symbolic-ref", "--short", "-q", "HEAD", check=False)


def upstream_remote(p: Path, b: str) -> str:
    return git(p, "config", "--get", f"branch.{b}.remote", check=False)


def is_ancestor(p: Path, a: str, b: str) -> bool:
    return run(["git", "-C", str(p), "merge-base", "--is-ancestor", a, b], check=False).returncode == 0


def dirty(p: Path) -> bool:
    return bool(git(p, "status", "--porcelain", "--untracked-files=no"))


def fetch(cfg: Config, r: Repo) -> None:
    git(r.path, "fetch", "-q", cfg.remote)


def selected_branches(cfg: Config, r: Repo) -> list[str]:
    cur = current_branch(r.path)
    return [b for b in local_branches(r.path)
            if b == cur or upstream_remote(r.path, b) == cfg.remote
            or any(fnmatch.fnmatchcase(b, pat) for pat in r.patterns)]


def push_plan(cfg: Config, r: Repo) -> tuple[list[str], list[str], list[str]]:
    """-> (to push, behind, diverged)"""
    loc, rem = local_branches(r.path), remote_branches(r.path, cfg.remote)
    todo, behind, div = [], [], []
    for b in selected_branches(cfg, r):
        lsha, rsha = loc[b], rem.get(b)
        if rsha == lsha:
            continue
        if rsha is None or is_ancestor(r.path, rsha, lsha):
            todo.append(b)
        elif is_ancestor(r.path, lsha, rsha):
            behind.append(b)
        else:
            div.append(b)
    return todo, behind, div


# ----- bundles -----

def bundle_files(d: Path) -> list[Path]:
    """newest first"""
    if not d.is_dir():
        return []
    return sorted(d.glob("*.bundle"), key=lambda f: (f.stat().st_mtime, f.name), reverse=True)


def ref_lines(p: Path) -> list[str]:
    out = git(p, "for-each-ref", "--format=%(objectname) %(refname)", "refs/heads/", "refs/tags/")
    return sorted(out.splitlines())


def bundle_ref_lines(p: Path, f: Path) -> list[str]:
    out = git(p, "bundle", "list-heads", str(f), check=False)
    return sorted(l for l in out.splitlines() if " refs/heads/" in l or " refs/tags/" in l)


def make_bundle(cfg: Config, r: Repo, op: str, dry: bool) -> Path | None:
    d = cfg.bundle_dir / r.key
    now = ref_lines(r.path)
    if not now:
        log("skip", f"{r.key}: no commits — nothing to bundle")
        return None
    files = bundle_files(d)
    if files and bundle_ref_lines(r.path, files[0]) == now:
        log("skip", f"{r.key}: refs unchanged since {files[0].name} — no new bundle")
        return files[0]
    stamp = time.strftime("%Y-%m-%d-%H%M%S")
    f = d / f"{stamp}-{op}.bundle"
    n = 2
    while f.exists():
        f = d / f"{stamp}-{op}-{n}.bundle"
        n += 1
    if dry:
        log("dry", f"{r.key}: would write bundle {f}")
        return None
    d.mkdir(parents=True, exist_ok=True)
    git(r.path, "bundle", "create", "-q", str(f), "HEAD", "--branches", "--tags")
    git(r.path, "bundle", "verify", "-q", str(f))
    log("ok", f"{r.key}: bundle {f} ({f.stat().st_size // 1024} KiB)")
    return f


def prune_dir(cfg: Config, d: Path, dry: bool) -> int:
    cutoff = time.time() - cfg.keep_days * 86400
    gone = 0
    for f in bundle_files(d)[cfg.keep_last:]:
        if f.stat().st_mtime < cutoff:
            if dry:
                log("dry", f"would delete {f}")
            else:
                f.unlink()
                log("ok", f"pruned {f}")
            gone += 1
    return gone


# ----- commands -----

def cmd_status(cfg: Config, args) -> int:
    repos = discover(cfg)
    if args.repos:
        repos = select(cfg, repos, args.repos, False, need_remote=False)
    on, off = [r for r in repos if r.url], [r for r in repos if not r.url]
    rc = 0
    rows = [("REPO", "BRANCH", "↑/↓", "DIRTY", "PUSH", "LAST BUNDLE", "NOTES")]
    for r in on:
        notes = []
        if not args.no_fetch:
            try:
                fetch(cfg, r)
            except CmdError as e:
                notes.append(f"fetch failed: {e}")
                rc = 1
        cur = current_branch(r.path) or "(detached)"
        rem = remote_branches(r.path, cfg.remote)
        if cur in rem:
            behind, ahead = git(r.path, "rev-list", "--left-right", "--count", f"{cfg.remote}/{cur}...{cur}").split()
            ab = f"{ahead}/{behind}"
        else:
            ab = "new"
        todo, bh, dv = push_plan(cfg, r)
        if bh:
            notes.append("behind: " + ", ".join(bh))
        if dv:
            notes.append("DIVERGED: " + ", ".join(dv))
        lonely = sorted(set(local_branches(r.path)) - on_any_remote(r.path))
        if lonely:
            notes.append("⚠ local-only: " + ", ".join(lonely))
        files = bundle_files(cfg.bundle_dir / r.key)
        last = files[0].name[:10] if files else "—"
        rows.append((r.key, cur, ab, "yes" if dirty(r.path) else "", str(len(todo)) if todo else "", last, "; ".join(notes)))
    _table(rows)
    if off:
        print(f"\nNot on {cfg.remote} ({len(off)}): create with `repo-sync new <path|name>`")
        for r in off:
            lonely = sorted(set(local_branches(r.path)) - on_any_remote(r.path))
            extra = f" · ⚠ local-only: {', '.join(lonely)}" if lonely else ""
            print(f"  {r.suggested:<34} {_home(r.path)}{extra}")
    return rc


def _home(p: Path) -> str:
    s = str(p)
    h = str(Path.home())
    return "~" + s[len(h):] if s.startswith(h) else s


def _table(rows: list[tuple]) -> None:
    w = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]) - 1)]
    for row in rows:
        print("  ".join(str(c).ljust(w[i]) for i, c in enumerate(row[:-1])) + "  " + str(row[-1]))


def push_one(cfg: Config, r: Repo, dry: bool) -> None:
    fetch(cfg, r)
    todo, behind, div = push_plan(cfg, r)
    if div:
        raise Refused(f"diverged from {cfg.remote}: {', '.join(div)} — not fast-forward; pull/rebase by hand, never forced")
    for b in behind:
        log("notice", f"{r.key}: {b} is behind {cfg.remote} — run `repo-sync pull`")
    if not todo:
        log("skip", f"{r.key}: up to date")
        return
    if dry:
        log("dry", f"{r.key}: would push {', '.join(todo)} (+ reachable tags)")
        make_bundle(cfg, r, "push", dry=True)
        return
    make_bundle(cfg, r, "push", dry=False)
    git(r.path, "push", "-q", "--follow-tags", cfg.remote, *[f"refs/heads/{b}:refs/heads/{b}" for b in todo])
    loc = local_branches(r.path)
    for b in todo:
        out = git(r.path, "ls-remote", cfg.remote, f"refs/heads/{b}")
        rsha = out.split()[0] if out else ""
        if rsha != loc[b]:
            raise Refused(f"verify failed: {cfg.remote} {b} = {rsha[:7] or 'missing'}, local = {loc[b][:7]}")
        if not upstream_remote(r.path, b):
            git(r.path, "branch", "-q", f"--set-upstream-to={cfg.remote}/{b}", b)
    log("ok", f"{r.key}: pushed {', '.join(todo)} — remote = local")
    prune_dir(cfg, cfg.bundle_dir / r.key, dry=False)


def pull_one(cfg: Config, r: Repo, dry: bool) -> None:
    if dirty(r.path):
        raise Refused("uncommitted changes — commit or stash first")
    cur = current_branch(r.path)
    if not cur:
        raise Refused("detached HEAD — check out a branch first")
    fetch(cfg, r)
    rem = remote_branches(r.path, cfg.remote)
    if cur not in rem:
        log("skip", f"{r.key}: {cur} is not on {cfg.remote}")
        return
    lsha, rsha = local_branches(r.path)[cur], rem[cur]
    if lsha == rsha:
        log("skip", f"{r.key}: {cur} up to date")
    elif is_ancestor(r.path, lsha, rsha):
        if dry:
            log("dry", f"{r.key}: would fast-forward {cur} {lsha[:7]}..{rsha[:7]}")
            make_bundle(cfg, r, "pull", dry=True)
            return
        make_bundle(cfg, r, "pull", dry=False)
        git(r.path, "merge", "-q", "--ff-only", f"{cfg.remote}/{cur}")
        log("ok", f"{r.key}: {cur} fast-forwarded {lsha[:7]}..{rsha[:7]}")
        prune_dir(cfg, cfg.bundle_dir / r.key, dry=False)
    elif is_ancestor(r.path, rsha, lsha):
        log("skip", f"{r.key}: {cur} is ahead of {cfg.remote} — run `repo-sync push`")
    else:
        raise Refused(f"{cur} diverged from {cfg.remote}/{cur} — merge or rebase by hand")


def _each(cfg: Config, repos: list[Repo], fn, dry: bool) -> int:
    rc = 0
    for r in repos:
        try:
            fn(cfg, r, dry)
        except (Refused, CmdError) as e:
            log("error", f"{r.key}: {e}")
            rc = 1
    return rc


def cmd_push(cfg: Config, args) -> int:
    return _each(cfg, select(cfg, discover(cfg), args.repos, args.all), push_one, args.dry_run)


def cmd_pull(cfg: Config, args) -> int:
    return _each(cfg, select(cfg, discover(cfg), args.repos, args.all), pull_one, args.dry_run)


def cmd_new(cfg: Config, args) -> int:
    r = select(cfg, discover(cfg), [args.repo], False, need_remote=False)[0]
    name = args.as_name or (r.name if args.origin and r.name else r.suggested)
    try:
        if args.origin:
            new_origin(cfg, r, name, args.dry_run)
        else:
            new_server(cfg, r, name, args.dry_run)
    except (Refused, CmdError) as e:
        log("error", f"{name}: {e}")
        return 1
    return 0


def new_server(cfg: Config, r: Repo, name: str, dry: bool) -> None:
    if r.url:
        raise Refused(f"already has remote {cfg.remote} = {r.url}")
    dest = f"{cfg.base}/{name}.git"
    url = f"{cfg.host}:{dest}" if cfg.host else dest
    if dry:
        log("dry", f"would create bare repo {url} and add remote {cfg.remote} in {_home(r.path)}")
        return
    res = remote_sh(cfg, f'd={q(dest)}; if [ -e "$d" ]; then exit 3; fi; '
                         f'mkdir -p "$(dirname "$d")" && git init -q --bare "$d"')
    if res.returncode == 3:
        raise Refused(f"{url} already exists on the server — not touched")
    if res.returncode != 0:
        raise Refused(f"create failed: {(res.stderr or res.stdout).strip()}")
    git(r.path, "remote", "add", cfg.remote, url)
    log("ok", f"created {url}; remote {cfg.remote} added — next: repo-sync push {name}")


def new_origin(cfg: Config, r: Repo, name: str, dry: bool) -> None:
    if not (cfg.origin_base and cfg.origin_url):
        raise Refused("config has no [origin] base/url")
    rem = cfg.origin_remote
    if git(r.path, "config", "--get", f"remote.{rem}.url", check=False):
        raise Refused(f"already has remote {rem} — not changed")
    dest = f"{cfg.origin_base}/{name}.git"
    url = f"{cfg.origin_url}/{name}.git"
    sudo = f"sudo -n -u {q(cfg.origin_user)} " if cfg.origin_user else ""
    if dry:
        log("dry", f"would seed {dest} ({'as ' + cfg.origin_user if cfg.origin_user else 'no sudo'}) "
                   f"from a bundle and set {rem} = {url}")
        make_bundle(cfg, r, "origin", dry=True)
        return
    make_bundle(cfg, r, "origin", dry=False)
    fd, tmp = tempfile.mkstemp(suffix=".bundle")
    os.close(fd)
    try:
        git(r.path, "bundle", "create", "-q", tmp, "HEAD", "--branches", "--tags")
        os.chmod(tmp, 0o644)
        rtmp = tmp
        if cfg.host:
            rtmp = f"/tmp/{TOOL_NAME}-{os.getpid()}-{int(time.time())}.bundle"
            run(["scp", "-q", *SSH_OPTS, tmp, f"{cfg.host}:{rtmp}"])
        rm_b = 'rm -f "$b"; ' if cfg.host else ""
        script = (f'd={q(dest)}; b={q(rtmp)}; chmod 644 "$b"; '
                  f'if [ -e "$d" ]; then {rm_b}exit 3; fi; '
                  f'{sudo}mkdir -p "$(dirname "$d")" && {sudo}git clone -q --bare "$b" "$d"; rc=$?; '
                  f'[ $rc = 0 ] && {{ {sudo}git -C "$d" remote remove origin 2>/dev/null; }}; '
                  f'{rm_b}exit $rc')
        res = remote_sh(cfg, script)
    finally:
        os.unlink(tmp)
    if res.returncode == 3:
        raise Refused(f"{dest} already exists on the server — not touched")
    if res.returncode != 0:
        raise Refused(f"seed failed: {(res.stderr or res.stdout).strip()}")
    git(r.path, "remote", "add", rem, url)
    log("ok", f"seeded {dest}; remote {rem} = {url}")


def cmd_prune(cfg: Config, args) -> int:
    if not cfg.bundle_dir.is_dir():
        log("skip", f"no bundle dir yet: {cfg.bundle_dir}")
        return 0
    dirs = sorted({f.parent for f in cfg.bundle_dir.rglob("*.bundle")})
    gone = sum(prune_dir(cfg, d, args.dry_run) for d in dirs)
    log("ok", f"{'would prune' if args.dry_run else 'pruned'} {gone} bundle(s) "
              f"(rule: older than {cfg.keep_days} days and not in the newest {cfg.keep_last} per repo)")
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
    if len(sys.argv) > 1 and sys.argv[1] in ("install", "uninstall"):
        return cmd_install() if sys.argv[1] == "install" else cmd_uninstall()

    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Push / pull git repos to a bare-repo server over ssh, with a safety bundle first. "
                    "Also: install | uninstall (symlinks).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="ahead/behind, dirty, local-only branches, repos not on the server")
    s.add_argument("repos", nargs="*")
    s.add_argument("--no-fetch", action="store_true", help="use the last fetched state")

    for name, hlp in (("push", "bundle, then fast-forward push"), ("pull", "bundle, then fast-forward merge")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("repos", nargs="*")
        p.add_argument("--all", action="store_true", help="every discovered repo with the remote")
        p.add_argument("-n", "--dry-run", action="store_true")

    n = sub.add_parser("new", help="create the bare repo on the server and add the remote")
    n.add_argument("repo")
    n.add_argument("--origin", action="store_true", help="seed a repo under [origin] (as its user) and set the origin remote")
    n.add_argument("--as", dest="as_name", metavar="GROUP/NAME", help="name on the server (default GROUP/<folder>)")
    n.add_argument("-n", "--dry-run", action="store_true")

    pr = sub.add_parser("prune", help="delete old bundles (older than keep_days and not in the newest keep_last)")
    pr.add_argument("-n", "--dry-run", action="store_true")

    args = parser.parse_args()
    cfg = load_config()
    return {"status": cmd_status, "push": cmd_push, "pull": cmd_pull,
            "new": cmd_new, "prune": cmd_prune}[args.cmd](cfg, args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("error", "interrupted")
        sys.exit(130)
