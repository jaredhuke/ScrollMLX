"""
Repo intelligence — local git status across every project AND every remote ("multiple
gits"), with per-remote sync direction (ahead/behind), host, and fetch/pull/push actions.

Used by the full-screen Repo manager. Read-only scanning is safe; the mutate actions
(fetch/pull/push) are only triggered by an explicit click in the UI.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _git(path: str, *args: str, timeout: int = 25):
    return subprocess.run(["git", "-C", str(path), *args],
                          capture_output=True, text=True, timeout=timeout)


def _ok(path: str, *args: str):
    r = _git(path, *args)
    return r.stdout.strip() if r.returncode == 0 else ""


def _host(url: str) -> str:
    u = url or ""
    for h in ("git.epam.com", "github.com", "gitlab.com", "bitbucket.org", "ssh.dev.azure.com", "dev.azure.com"):
        if h in u:
            return h
    # generic: pull the host out of git@host:.. or https://host/..
    if u.startswith("git@"):
        return u.split("@", 1)[1].split(":", 1)[0]
    if "://" in u:
        return u.split("://", 1)[1].split("/", 1)[0]
    return "local"


def repo_info(path: str) -> dict:
    p = Path(path).expanduser()
    out = {"path": str(p), "name": p.name, "is_repo": False}
    if not p.exists():
        out["error"] = "missing"
        return out
    if _git(path, "rev-parse", "--is-inside-work-tree").returncode != 0:
        return out
    out["is_repo"] = True
    branch = _ok(path, "rev-parse", "--abbrev-ref", "HEAD") or "detached"
    out["branch"] = branch
    porcelain = _ok(path, "status", "--porcelain")
    out["dirty"] = len([l for l in porcelain.splitlines() if l.strip()])
    last = _ok(path, "log", "-1", "--pretty=%h%x1f%s%x1f%cr")
    if last:
        h, s, when = (last.split("\x1f") + ["", "", ""])[:3]
        out["last_commit"] = {"hash": h, "subject": s[:80], "when": when}
    # remotes ("multiple gits") + per-remote sync direction
    remotes = []
    seen = set()
    rv = _ok(path, "remote", "-v")
    for line in rv.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] in seen:
            continue
        seen.add(parts[0])
        name, url = parts[0], parts[1]
        ref = f"{name}/{branch}"
        has_ref = _git(path, "rev-parse", "--verify", "--quiet", ref).returncode == 0
        ahead = behind = None
        if has_ref and branch != "detached":
            a = _ok(path, "rev-list", "--count", f"{ref}..HEAD")
            b = _ok(path, "rev-list", "--count", f"HEAD..{ref}")
            ahead = int(a) if a.isdigit() else 0
            behind = int(b) if b.isdigit() else 0
        remotes.append({"name": name, "url": url, "host": _host(url),
                        "tracked": has_ref, "ahead": ahead, "behind": behind})
    out["remotes"] = remotes
    return out


def discover(explicit: list[str]) -> list[str]:
    """Every git repo Scroll touches — not just registered projects. Includes the explicit roots
    (app repo + registered projects) AND anything cloned into the workspace: project folders under
    ~/ScrollProjects, plus repos cloned one level inside them (e.g. ad-hoc clones land in
    ~/ScrollProjects/Ad hoc/<repo>, agent/`/v1/repo/clone` clones land in <cwd>/<repo>)."""
    out, seen = [], set()

    def add(p) -> None:
        if not p:
            return
        s = str(Path(p).expanduser())
        if s not in seen:
            seen.add(s)
            out.append(s)

    for p in explicit:
        add(p)
    base = Path.home() / "ScrollProjects"
    if base.is_dir():
        for child in sorted(base.iterdir()):
            if not child.is_dir():
                continue
            if (child / ".git").exists():
                add(str(child))               # a project folder that is itself a repo
            try:
                for sub in sorted(child.iterdir()):   # repos cloned INTO a project / Ad hoc
                    if sub.is_dir() and (sub / ".git").exists():
                        add(str(sub))
            except Exception:
                pass
    return out


def scan(paths: list[str]) -> list[dict]:
    seen, repos = set(), []
    for p in paths:
        rp = str(Path(p or ".").expanduser())
        if rp in seen:
            continue
        seen.add(rp)
        try:
            info = repo_info(rp)
        except Exception as exc:
            info = {"path": rp, "name": Path(rp).name, "is_repo": False, "error": str(exc)[:120]}
        repos.append(info)
    return repos


def action(path: str, act: str, remote: str = "", branch: str = "") -> dict:
    """fetch / pull / push for one repo+remote — only from an explicit UI click."""
    p = Path(path).expanduser()
    if _git(str(p), "rev-parse", "--is-inside-work-tree").returncode != 0:
        return {"ok": False, "error": "not a git repo"}
    branch = branch or (_ok(str(p), "rev-parse", "--abbrev-ref", "HEAD") or "HEAD")
    remote = remote or "origin"
    if act == "fetch":
        cmd = ["fetch", remote]
    elif act == "pull":
        cmd = ["pull", "--ff-only", remote, branch]
    elif act == "push":
        cmd = ["push", remote, branch]
    else:
        return {"ok": False, "error": f"unknown action {act!r}"}
    try:
        r = _git(str(p), *cmd, timeout=300)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
    msg = (r.stdout + "\n" + r.stderr).strip()
    return {"ok": r.returncode == 0, "output": msg[-600:], "info": repo_info(str(p))}
