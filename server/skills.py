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


# Built-in skills seeded once on first run. The user can edit or delete them; we
# track a seed flag in meta so a deleted built-in is not silently re-created.
_BUILTINS = {
    "plan-and-clarify": """# Plan & Clarify

Set the intention before doing substantial work:

1. Restate the goal in one sentence so we're aligned.
2. If the request is genuinely ambiguous, or a choice is expensive to reverse, ask
   1-3 specific clarifying questions and wait for the answer — don't guess.
3. If it's clear, give a short numbered plan (2-5 steps), then carry it out fully.

Keep it lightweight: for small, unambiguous tasks a single-line plan is enough — don't
over-ask. Always surface the assumptions you made so they can be corrected.
""",
}


def _ensure_builtins() -> None:
    _DIR.mkdir(parents=True, exist_ok=True)
    m = _meta()
    seeded = m.get("__seeded__", [])
    changed = False
    for slug, body in _BUILTINS.items():
        if slug in seeded:
            continue
        path = _DIR / f"{slug}.md"
        if not path.exists():
            path.write_text(body, encoding="utf-8")
        m.setdefault(slug, {"enabled": True})
        seeded.append(slug)
        changed = True
    if changed:
        m["__seeded__"] = seeded
        _save_meta(m)


def list_skills() -> list[dict]:
    _DIR.mkdir(parents=True, exist_ok=True)
    _ensure_builtins()
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


_SKILL_EXTS = {".md", ".markdown", ".txt"}


def import_path(raw: str) -> dict:
    """Import skill(s) by pointing at a file OR a folder. Name comes from the filename.

    A local-first alternative to the browser file picker: the server reads the path
    directly (it already has filesystem access), so it works regardless of the
    WKWebView/file-dialog plumbing.
    """
    p = Path(raw or "").expanduser()
    if not p.exists():
        return {"ok": False, "error": f"path not found: {p}"}
    files: list[Path] = []
    if p.is_file():
        files = [p]
    else:
        for ext in _SKILL_EXTS:
            files.extend(p.rglob(f"*{ext}"))
    files = [f for f in files if f.suffix.lower() in _SKILL_EXTS]
    added, names, skipped = 0, [], 0
    for f in sorted(set(files)):
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            skipped += 1
            continue
        if not content.strip():
            skipped += 1
            continue
        add(f.stem, content)
        added += 1
        names.append(f.stem)
    if added == 0:
        return {"ok": False, "error": "no non-empty .md/.markdown/.txt files found", "skipped": skipped}
    return {"ok": True, "added": added, "names": names, "skipped": skipped}


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
