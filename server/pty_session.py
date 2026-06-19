"""
A real PTY-backed shell session for the embedded terminal.

The old terminal POSTed one command at a time to /v1/shell (subprocess.run) — no persistent
shell, no `cd` memory, no interactive programs. This bridges a genuine pseudo-terminal to a
WebSocket, so Scroll's terminal is as capable as Terminal.app: a persistent login shell,
interactive TUIs (vim, htop, ssh), colors, job control, the works.
"""
from __future__ import annotations

import fcntl
import os
import pty
import signal
import struct
import termios
from pathlib import Path


def set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def spawn(cwd: str | None = None, cols: int = 80, rows: int = 24) -> tuple[int, int]:
    """Fork a login shell attached to a fresh PTY. Returns (pid, master_fd).

    The child immediately execs the shell, so forking inside the async server is safe."""
    from server import env as env_mod
    shell = os.environ.get("SHELL") or "/bin/zsh"
    pid, fd = pty.fork()
    if pid == 0:  # ── child ──
        try:
            os.environ["PATH"] = env_mod.login_path()  # same PATH Terminal.app sees
            os.environ["TERM"] = "xterm-256color"
            if not os.environ.get("LANG"):
                os.environ["LANG"] = "en_US.UTF-8"
            d = Path(cwd).expanduser() if cwd else Path.home()
            if d.is_dir():
                os.chdir(d)
        except Exception:
            pass
        try:
            os.execvp(shell, [shell, "-l"])
        except Exception:
            os.execvp("/bin/sh", ["/bin/sh"])
        os._exit(127)
    # ── parent ──
    try:
        set_winsize(fd, rows, cols)
    except Exception:
        pass
    return pid, fd


def close(pid: int, fd: int) -> None:
    try:
        os.close(fd)
    except Exception:
        pass
    for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except Exception:
            break
    try:
        os.waitpid(pid, os.WNOHANG)
    except Exception:
        pass
