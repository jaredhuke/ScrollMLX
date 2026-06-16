"""
Standing context — a real, persistent file per project that the agent always honors.

Two sections:
  • "From you"  — what you type; appended incrementally, fully editable.
  • "Learned"   — notes the critic earned acceptance for (mirrors learn.learned()).

Stored as readable markdown at ~/.scroll/context/<slug>.md so it survives restarts
and you can open/version it. Injected into every PRIMARY run.
"""
from __future__ import annotations

import re
from pathlib import Path

_DIR = Path.home() / ".scroll" / "context"


def _slug(project: str | None) -> str:
    p = (project or ".").strip()
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", p).strip("-").lower() or "default"
    return s[:80]


def _path(project: str | None) -> Path:
    return _DIR / f"{_slug(project)}.md"


def get_user(project: str | None) -> list[str]:
    p = _path(project)
    if not p.exists():
        return []
    out, raw = [], p.read_text(encoding="utf-8", errors="replace")
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("- "):
            out.append(line[2:].strip())
    return [x for x in out if x]


def _write(project: str | None, lines: list[str]) -> None:
    _DIR.mkdir(parents=True, exist_ok=True)
    body = "# Standing context\n\n## From you\n"
    body += ("\n".join(f"- {l}" for l in lines) if lines else "_(nothing yet)_")
    body += "\n"
    _path(project).write_text(body, encoding="utf-8")


def add_user(project: str | None, line: str) -> list[str]:
    line = (line or "").strip()
    if not line:
        return get_user(project)
    lines = get_user(project)
    if line not in lines:
        lines.append(line)
    _write(project, lines)
    return lines


def remove_user(project: str | None, index: int) -> list[str]:
    lines = get_user(project)
    if 0 <= index < len(lines):
        lines.pop(index)
        _write(project, lines)
    return lines


def set_user(project: str | None, text: str) -> list[str]:
    """Replace the whole user section from free text (one item per non-empty line)."""
    lines = []
    for raw in (text or "").splitlines():
        s = raw.strip().lstrip("-").strip()
        if s and not s.startswith("#"):
            lines.append(s)
    _write(project, lines)
    return lines


def view(project: str | None) -> dict:
    from server import learn
    return {
        "project": project or ".",
        "path": str(_path(project)),
        "user": get_user(project),
        "learned": learn.learned(project),
    }


def as_system_text(project: str | None) -> str:
    """Combined standing context for injection into a run (user + learned)."""
    v = view(project)
    parts = []
    if v["user"]:
        parts.append("Standing context from the user (always honor):\n- " + "\n- ".join(v["user"]))
    if v["learned"]:
        parts.append("Learned corrections (honor these):\n- " + "\n- ".join(v["learned"]))
    return "\n\n".join(parts)
