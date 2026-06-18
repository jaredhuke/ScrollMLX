"""
Projects — a workspace = a git repo + a "product" (its runnable dev server).

Scroll keeps a small registry of projects (~/.scroll/projects.json) and can stand
each one up: it runs the project's start command as a child process group, streams
its output into a ring buffer, and reports whether the port is live. One click in
the UI launches/stops a small project's server and you manage them from here.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

_DIR = Path.home() / ".scroll"
_FILE = _DIR / "projects.json"
_BASE = Path.home() / "ScrollProjects"          # every project lives in its own folder here


def _slugname(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def adhoc_dir() -> Path:
    """Dedicated home for non-project ('Ad hoc') work — kept OUT of the Scroll app repo."""
    d = _BASE / "Ad hoc"
    d.mkdir(parents=True, exist_ok=True)
    return d


def app_dir() -> Path:
    """The Scroll application's own directory — work must not land here unless it IS the project."""
    return Path(__file__).resolve().parent.parent

# in-memory runtime: id -> {proc, logs(deque), started}
_RUN: dict[str, dict] = {}


def _load() -> list[dict]:
    try:
        return json.loads(_FILE.read_text())
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    _DIR.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(items, indent=2))


def _id(name: str, items: list[dict]) -> str:
    import re
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "project"
    pid, n = base, 2
    have = {p["id"] for p in items}
    while pid in have:
        pid = f"{base}-{n}"; n += 1
    return pid


def detect_start(path: str) -> tuple[str, int]:
    """Best-effort start command + port for a folder."""
    p = Path(path).expanduser()
    if (p / "package.json").exists():
        try:
            scripts = (json.loads((p / "package.json").read_text()).get("scripts") or {})
        except Exception:
            scripts = {}
        for s in ("dev", "start", "serve"):
            if s in scripts:
                return f"npm run {s}", 3000
        return "npm start", 3000
    if (p / "manage.py").exists():
        return "python manage.py runserver 0.0.0.0:8000", 8000
    if (p / "pyproject.toml").exists() or (p / "requirements.txt").exists():
        return "", 8000
    if (p / "index.html").exists():
        return "python3 -m http.server 4321", 4321
    return "", 0


def _port_up(port: int) -> bool:
    if not port:
        return False
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.25)
    try:
        return s.connect_ex(("127.0.0.1", int(port))) == 0
    except Exception:
        return False
    finally:
        s.close()


def _running(pid: str) -> bool:
    r = _RUN.get(pid)
    return bool(r and r["proc"].poll() is None)


def _public(p: dict) -> dict:
    running = _running(p["id"])
    port = int(p.get("port") or 0)
    return {
        **p,
        "running": running,
        "port_up": _port_up(port) if running else False,
        "url": f"http://127.0.0.1:{port}" if port else "",
        "exists": Path(p.get("path", "")).expanduser().exists(),
    }


def list_projects() -> list[dict]:
    return [_public(p) for p in _load()]


def get(pid: str) -> dict | None:
    for p in _load():
        if p["id"] == pid:
            return p
    return None


def add(name: str, path: str = "", repo: str = "", start: str = "", port: int = 0) -> dict:
    items = _load()
    name = (name or "").strip() or "Untitled"
    repo = (repo or "").strip()
    path = (path or "").strip()

    if repo and not path:
        _BASE.mkdir(parents=True, exist_ok=True)
        dest = _BASE / (name.replace(" ", "-").lower() or "repo")
        if not dest.exists():
            r = subprocess.run(["git", "clone", "--depth", "1", repo, str(dest)],
                               capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                raise RuntimeError((r.stderr or r.stdout).strip()[:300])
        path = str(dest)
    # Rigid separation: a project never defaults to the app's own working directory.
    # No explicit path → give it a clean, dedicated folder under ~/ScrollProjects/<name>.
    if path:
        path = str(Path(path).expanduser())
    else:
        _BASE.mkdir(parents=True, exist_ok=True)
        dest = _BASE / (_slugname(name) or "project")
        dest.mkdir(parents=True, exist_ok=True)
        path = str(dest)

    if not start:
        start, dport = detect_start(path)
        port = port or dport

    proj = {
        "id": _id(name, items), "name": name, "path": path, "repo": repo,
        "start": start, "port": int(port or 0), "created": time.time(),
    }
    items.append(proj)
    _save(items)
    return _public(proj)


def update(pid: str, **fields) -> dict | None:
    items = _load()
    for p in items:
        if p["id"] == pid:
            for k in ("name", "path", "repo", "start", "port"):
                if k in fields and fields[k] is not None:
                    p[k] = int(fields[k]) if k == "port" else fields[k]
            _save(items)
            return _public(p)
    return None


def remove(pid: str) -> bool:
    stop(pid)
    items = [p for p in _load() if p["id"] != pid]
    _save(items)
    return True


def launch(pid: str) -> dict:
    proj = get(pid)
    if not proj:
        return {"ok": False, "error": "unknown project"}
    if not proj.get("start"):
        return {"ok": False, "error": "no start command set for this project"}
    if _running(pid):
        return {"ok": True, "already": True, **_public(proj)}
    path = Path(proj["path"]).expanduser()
    if not path.exists():
        return {"ok": False, "error": f"path not found: {path}"}
    logs: deque = deque(maxlen=300)
    try:
        proc = subprocess.Popen(
            proj["start"], shell=True, cwd=str(path),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            start_new_session=True,  # own process group → we can stop child servers too
            env={**os.environ, "PYTHONUNBUFFERED": "1", "FORCE_COLOR": "0"},
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    def _pump():
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                logs.append(line.rstrip("\n")[:500])
        except Exception:
            pass
    threading.Thread(target=_pump, daemon=True).start()
    _RUN[pid] = {"proc": proc, "logs": logs, "started": time.time()}
    return {"ok": True, **_public(proj)}


def stop(pid: str) -> dict:
    r = _RUN.get(pid)
    if not r or r["proc"].poll() is not None:
        return {"ok": True, "stopped": False}
    proc = r["proc"]
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    for _ in range(20):
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    if proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
    return {"ok": True, "stopped": True}


def logs(pid: str, n: int = 80) -> list[str]:
    r = _RUN.get(pid)
    return list(r["logs"])[-n:] if r else []
