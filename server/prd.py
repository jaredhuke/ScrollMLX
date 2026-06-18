"""Per-project living PRD — a human-readable PRD.md in the project folder that the operative
keeps current (requirements + status). It travels with the project (git-versioned), not a hidden
state file. Goals/requirements/status in plain language; checkbox items track done/total."""
from __future__ import annotations

import re
from pathlib import Path


def _path(cwd: str | None) -> Path:
    return (Path(cwd or ".").expanduser() / "PRD.md")


def scaffold(name: str) -> str:
    return (
        f"# {name} — Plan\n\n"
        "_A living plan. Scroll keeps this current as work gets done._\n\n"
        "## Goal\n_What are we building, and for whom?_\n\n"
        "## Requirements\n"
        "- [ ] (add the first requirement)\n\n"
        "## Notes & context\n_decisions, constraints, links_\n\n"
        "## Status\n_what's done, what's next_\n"
    )


def _counts(text: str) -> tuple[int, int]:
    done = len(re.findall(r"(?m)^\s*[-*]\s*\[[xX]\]", text))
    todo = len(re.findall(r"(?m)^\s*[-*]\s*\[\s?\]", text))
    return done, done + todo


def read(cwd: str | None, name: str = "this project") -> dict:
    p = _path(cwd)
    if p.is_file():
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            t = ""
        d, tot = _counts(t)
        return {"exists": True, "content": t, "done": d, "total": tot}
    sc = scaffold(name)
    d, tot = _counts(sc)
    return {"exists": False, "content": sc, "done": d, "total": tot}


def write(cwd: str | None, content: str) -> dict:
    p = _path(cwd)
    base = Path(cwd or ".").expanduser().resolve()
    try:
        if not p.resolve().is_relative_to(base):   # the plan must live inside the project folder
            return {"ok": False, "error": "refused — outside the project folder"}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content or "", encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    d, tot = _counts(content or "")
    return {"ok": True, "done": d, "total": tot}
