"""Ongoing burn/token ledger — one entry per prompt, grouped by project."""
from __future__ import annotations

import json
import time
from pathlib import Path

_DIR = Path.home() / ".scroll"
_PATH = _DIR / "ledger.json"


def _load() -> list:
    try:
        return json.loads(_PATH.read_text())
    except Exception:
        return []


def _save(entries: list) -> None:
    _DIR.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(entries))


def record(project: str, prompt: str, tokens: int, mode: str = "local",
           tools: int = 0, iterations: int = 0) -> None:
    e = _load()
    e.append({
        "ts": time.time(),
        "project": project or ".",
        "prompt": (prompt or "")[:160],
        "tokens": int(tokens or 0),
        "mode": mode,
        "tools": int(tools or 0),
        "iterations": int(iterations or 0),
    })
    _save(e[-3000:])  # cap


def entries(project: str | None = None) -> list:
    e = _load()
    return [x for x in e if x.get("project") == project] if project else e


def summary(project: str | None = None) -> dict:
    e = entries(project)
    n = len(e)
    tot = sum(x.get("tokens", 0) for x in e)
    by_mode: dict[str, int] = {}
    for x in e:
        by_mode[x.get("mode", "?")] = by_mode.get(x.get("mode", "?"), 0) + x.get("tokens", 0)
    return {
        "prompts": n,
        "total_tokens": tot,
        "avg_tokens": (tot // n if n else 0),
        "tool_calls": sum(x.get("tools", 0) for x in e),
        "by_mode": by_mode,
    }
