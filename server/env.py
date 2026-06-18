"""
Login-shell PATH resolution.

Scroll.app is a GUI app (launched by Finder/launchd), so the server it spawns inherits
the *minimal* launchd PATH (/usr/bin:/bin:/usr/sbin:/sbin) — NOT the PATH a Terminal.app
login shell sees. That's why `npm`, `node`, `redacted`, Homebrew tools, etc. come back as
"command not found" in the embedded terminal even though they work fine in Terminal.app.

We fix it at the root: on startup we ask the user's login shell for its real PATH (the
exact one Terminal.app uses) and graft it onto this process, so every subprocess — the
embedded terminal, git, redacted-cli, npm — resolves binaries the same way.
"""
from __future__ import annotations

import os
import subprocess

# Belt-and-suspenders: if the login shell can't be queried (or returns something thin),
# make sure the usual macOS tool dirs are present.
_FALLBACK_DIRS = [
    "/opt/homebrew/bin", "/opt/homebrew/sbin",
    "/usr/local/bin", "/usr/local/sbin",
    os.path.expanduser("~/.local/bin"),
    "/usr/bin", "/bin", "/usr/sbin", "/sbin",
]

_SENTINEL = "__SCROLL_PATH__"

# Cache only a SUCCESSFUL probe — so a transient boot-time failure (slow/locked login shell)
# doesn't pin the degraded fallback for the whole process lifetime; the next call retries.
_CACHED_PATH: str | None = None


def _merge(path: str) -> str:
    parts = [d for d in path.split(":") if d]
    for d in _FALLBACK_DIRS:
        if d not in parts and os.path.isdir(d):
            parts.append(d)
    return ":".join(parts)


def login_path() -> str:
    """The PATH a Terminal.app login shell sees (memoized once we get a real answer)."""
    global _CACHED_PATH
    if _CACHED_PATH is not None:
        return _CACHED_PATH
    shell = os.environ.get("SHELL") or "/bin/zsh"
    try:
        # -l (login) sources .zprofile/.zshenv (Homebrew shellenv); -i (interactive) picks
        # up .zshrc where nvm-style PATH edits usually live. The sentinel isolates the PATH
        # line from any shell banner/job-control noise on stderr.
        r = subprocess.run(
            [shell, "-lic", 'printf "%s%s\\n" "{}" "$PATH"'.format(_SENTINEL)],
            capture_output=True, text=True, timeout=8,
        )
        for line in (r.stdout or "").splitlines():
            if line.startswith(_SENTINEL):
                cand = line[len(_SENTINEL):].strip()
                if "/" in cand:
                    _CACHED_PATH = _merge(cand)
                    return _CACHED_PATH
    except Exception:
        pass
    return _merge(os.environ.get("PATH", ""))  # fallback is NOT cached → retried next call


def login_env() -> dict:
    """A copy of os.environ with PATH replaced by the login-shell PATH."""
    e = dict(os.environ)
    e["PATH"] = login_path()
    return e


def apply_to_process() -> str:
    """Graft the login-shell PATH onto this process so every subprocess inherits it.

    Returns the resolved PATH (for a startup log line)."""
    p = login_path()
    os.environ["PATH"] = p
    return p
