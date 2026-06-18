"""File version history — keep the real filename, snapshot readable copies on each write.

`index.html` stays `index.html`. Before it's overwritten, the previous content is saved
as a complete, openable copy (`index.v1.html`, `index.v2.html`, …) under
~/.scroll/versions/<project>/<filename>/, with a manifest. No version suffix ever
touches the working file, so paths/links keep working.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

_ROOT = Path.home() / ".scroll" / "versions"


def _slug(cwd: str | None) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", str(cwd or ".")).strip("-").lower()
    return s[:80] or "default"


def _dir(path: Path, cwd: str | None) -> Path:
    return _ROOT / _slug(cwd) / path.name


def save_version(path: Path, cwd: str | None) -> dict | None:
    """Snapshot the CURRENT (about-to-be-replaced) content of `path`. No-op if it
    doesn't exist yet (nothing to version) or is unchanged from the latest snapshot."""
    if not path.exists() or not path.is_file():
        return None
    try:
        cur = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    d = _dir(path, cwd)
    d.mkdir(parents=True, exist_ok=True)
    man = d / "versions.json"
    try:
        meta = json.loads(man.read_text())
    except Exception:
        meta = []
    if meta:  # skip if identical to the most recent snapshot
        last = d / meta[-1]["file"]
        try:
            if last.read_text(encoding="utf-8", errors="replace") == cur:
                return None
        except Exception:
            pass
    n = len(meta) + 1
    ext = path.suffix or ""
    vfile = d / f"{path.stem}.v{n}{ext}"   # readable + keeps the real extension
    try:
        vfile.write_text(cur, encoding="utf-8")
    except Exception:
        return None
    meta.append({"v": n, "file": vfile.name, "ts": time.time(), "bytes": len(cur)})
    man.write_text(json.dumps(meta, indent=2))
    return {"v": n, "file": str(vfile)}


def list_versions(path: str, cwd: str | None) -> list[dict]:
    d = _dir(Path(path), cwd)
    try:
        meta = json.loads((d / "versions.json").read_text())
    except Exception:
        return []
    for m in meta:
        m["path"] = str(d / m["file"])
    return list(reversed(meta))  # newest first
