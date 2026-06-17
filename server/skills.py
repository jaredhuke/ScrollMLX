"""
Skill files — drop-in .md instructions the agent always follows.

Each skill is a markdown file in ~/.scroll/skills/. Enabled skills are injected as
a system message into every PRIMARY run (like standing context, but reusable and
toggleable). This is the "add a skills.md" path: name + markdown, on/off per skill.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_DIR = Path.home() / ".scroll" / "skills"
_META = Path.home() / ".scroll" / "skills.json"


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9._-]+", "-", (name or "").lower()).strip("-")
    return s[:60] or "skill"


def _meta() -> dict:
    try:
        return json.loads(_META.read_text())
    except Exception:
        return {}


def _save_meta(d: dict) -> None:
    _META.parent.mkdir(parents=True, exist_ok=True)
    _META.write_text(json.dumps(d))


def list_skills() -> list[dict]:
    _DIR.mkdir(parents=True, exist_ok=True)
    m = _meta()
    out = []
    for p in sorted(_DIR.glob("*.md")):
        name = p.stem
        try:
            n = len(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            n = 0
        out.append({"name": name, "chars": n, "enabled": m.get(name, {}).get("enabled", True)})
    return out


def get(name: str) -> str:
    p = _DIR / f"{_slug(name)}.md"
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def add(name: str, content: str) -> str:
    _DIR.mkdir(parents=True, exist_ok=True)
    slug = _slug(name)
    (_DIR / f"{slug}.md").write_text(content or "", encoding="utf-8")
    m = _meta()
    m.setdefault(slug, {"enabled": True})
    _save_meta(m)
    return slug


def set_enabled(name: str, enabled: bool) -> None:
    slug = _slug(name)
    m = _meta()
    m.setdefault(slug, {})
    m[slug]["enabled"] = bool(enabled)
    _save_meta(m)


def remove(name: str) -> None:
    slug = _slug(name)
    p = _DIR / f"{slug}.md"
    if p.exists():
        p.unlink()
    m = _meta()
    m.pop(slug, None)
    _save_meta(m)


def as_system_text() -> str:
    """Combined enabled skills for injection into a run."""
    parts = []
    for s in list_skills():
        if s["enabled"]:
            c = get(s["name"]).strip()
            if c:
                parts.append(f"## Skill: {s['name']}\n{c}")
    if not parts:
        return ""
    return "Active skills — capabilities & rules to follow:\n\n" + "\n\n".join(parts)
