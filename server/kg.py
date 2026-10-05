"""
Neo4j lifecycle supervisor for the agent-memory KG.

Neo4j is a SHARED instance (orbit.py already pushes ORBIT 3 telemetry into it,
alongside other unrelated domains on the same machine) — this module must never
assume it owns the process. Ownership is tracked on disk (~/.scroll/orbit/neo4j.pid)
so it survives a Python-server restart (uvicorn itself gets killed/relaunched by the
native app on every boot).

States: not_installed | starting | running_ours | running_external | unreachable | error

Restart semantics differ by ownership — this is the load-bearing rule: if we didn't
spawn the process, we never SIGKILL it (it may be serving another domain). "Restart"
on a running_external instance silently degrades to a re-probe instead.

Uses `neo4j console` (foreground) rather than `neo4j start` (which daemonizes and
returns a launcher pid, not the actual server pid) — that keeps supervision the same
subprocess.Popen + terminate()/kill() shape as server/projects.py already uses.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import psutil

from server import orbit

_DIR = Path.home() / ".scroll" / "orbit"
_PIDFILE = _DIR / "neo4j.pid"
_CONFIG = _DIR / "config.json"
_HTTP_PORT = 7474

_lock = threading.Lock()
_transitional = False
_since = time.time()


def _load_pidfile() -> dict | None:
    try:
        return json.loads(_PIDFILE.read_text())
    except Exception:
        return None


def _save_pidfile(pid: int) -> None:
    _DIR.mkdir(parents=True, exist_ok=True)
    try:
        create_time = psutil.Process(pid).create_time()
    except Exception:
        create_time = 0
    _PIDFILE.write_text(json.dumps({"pid": pid, "create_time": create_time}))


def _clear_pidfile() -> None:
    try:
        _PIDFILE.unlink()
    except Exception:
        pass


def _own_process() -> psutil.Process | None:
    """The process we spawned, if our pidfile is valid and it's still alive. Clears a
    stale pidfile (dead pid, or a reused pid from an unrelated process) automatically."""
    d = _load_pidfile()
    if not d:
        return None
    try:
        p = psutil.Process(d["pid"])
        if p.is_running() and abs(p.create_time() - d.get("create_time", 0)) < 2:
            return p
    except Exception:
        pass
    _clear_pidfile()
    return None


def _port_up(port: int = _HTTP_PORT) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False
    finally:
        s.close()


def _neo4j_bin() -> str | None:
    return shutil.which("neo4j")


def _config() -> dict:
    try:
        return json.loads(_CONFIG.read_text())
    except Exception:
        return {}


def _save_config(cfg: dict) -> None:
    _DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG.write_text(json.dumps(cfg))


def kg_enabled() -> bool:
    return bool(_config().get("enabled", False))


def set_enabled(enabled: bool) -> dict:
    cfg = _config()
    cfg["enabled"] = bool(enabled)
    _save_config(cfg)
    return {"ok": True, "enabled": cfg["enabled"]}


def status() -> dict:
    """Current state — safe to call frequently; does a real process + port + reachability
    probe each time (no caching), same as orbit.ping() already does for /v1/orbit/status."""
    owned = _own_process()
    port_up = _port_up()

    if owned and port_up:
        phase, pid, is_owned = "running_ours", owned.pid, True
    elif owned and not port_up:
        phase, pid, is_owned = ("starting" if _transitional else "error"), owned.pid, True
    elif port_up:
        phase, pid, is_owned = "running_external", None, False
    elif _transitional:
        phase, pid, is_owned = "starting", None, False
    elif not _neo4j_bin():
        phase, pid, is_owned = "not_installed", None, False
    else:
        phase, pid, is_owned = "unreachable", None, False

    detail, reachable = None, False
    if port_up:
        ping = orbit.ping()
        reachable = bool(ping.get("ok"))
        if not reachable:
            detail = ping.get("error")
            if phase in ("running_ours", "running_external"):
                phase = "error"

    return {
        "state": phase,
        "reachable": reachable,
        "pid": pid,
        "owned": is_owned,
        "url": f"http://127.0.0.1:{_HTTP_PORT}",
        "since": _since,
        "detail": detail,
        "can_restart": phase in ("running_ours", "error") and owned is not None,
        "can_reconnect": phase == "running_external",
        "kg_enabled": kg_enabled(),
    }


def _spawn() -> dict:
    global _since
    bin_path = _neo4j_bin()
    if not bin_path:
        return {"ok": False, "error": "neo4j not installed — brew install neo4j"}
    try:
        proc = subprocess.Popen(
            [bin_path, "console"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,  # own process group, mirrors server/projects.py
            env={**os.environ},
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    _save_pidfile(proc.pid)
    _since = time.time()
    return {"ok": True, "pid": proc.pid}


def autostart() -> None:
    """Called once from FastAPI's lifespan as a fire-and-forget background task — MUST
    NOT be awaited by the server's own readiness path. Neo4j/JVM cold start can take
    5-30s+; the native app polls /health to dismiss its boot screen and must never wait
    on this. Adopts an already-running instance (ours or external) untouched; only
    spawns when nothing answers the port at all."""
    global _transitional
    with _lock:
        if _own_process() or _port_up():
            return  # already up — nothing to do, don't touch it
        if not _neo4j_bin():
            return  # not installed — stays in not_installed state, no auto-install
        _transitional = True
    try:
        _spawn()
        for _ in range(60):  # bounded wait so this thread doesn't run forever; status() polls live regardless
            if _port_up():
                break
            time.sleep(1)
    finally:
        _transitional = False


def _kill_owned(proc: psutil.Process) -> None:
    try:
        proc.terminate()
    except Exception:
        pass
    for _ in range(30):
        if not proc.is_running():
            break
        time.sleep(0.2)
    if proc.is_running():
        try:
            proc.kill()
        except Exception:
            pass
    _clear_pidfile()


def restart() -> dict:
    """Kill+relaunch — ONLY when Scroll owns the process. A running_external instance
    is never SIGKILLed; this degrades to a harmless re-probe instead."""
    global _transitional
    with _lock:
        if _transitional:
            return {"ok": False, "error": "already starting/stopping", "state": status()}
        owned = _own_process()
        cur = status()
        if not owned and cur["state"] == "running_external":
            return {"ok": True, "reconnected": True,
                     "note": "Neo4j is managed outside Scroll — reconnected, not restarted", "state": cur}
        _transitional = True
    try:
        if owned:
            _kill_owned(owned)
        r = _spawn()
        return {**r, "state": status()}
    finally:
        _transitional = False


def shutdown_hook() -> None:
    """Deliberately a no-op. Neo4j is a shared resource (orbit.py's docstring: shared
    with other domains on this machine) — killing it because Scroll is quitting costs
    other consumers and buys Scroll nothing. Unlike the native ServerManager's
    killPort() for uvicorn (exclusively Scroll's), Neo4j must outlive us."""
    return None
