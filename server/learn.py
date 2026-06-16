"""
Critic trust + learned-context loop.

When the user ACCEPTS a critic finding it (a) becomes a persistent correction the
PRIMARY model is told to honor, and (b) raises the critic's trust score. When a
finding is REJECTED the critic's trust drops. The critic earns its voice.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

_DIR = Path.home() / ".scroll"
_TRUST = _DIR / "trust.json"
_LEARNED = _DIR / "learned.json"


def _load(p: Path, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def _save(p: Path, data) -> None:
    _DIR.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data))


def trust() -> dict:
    t = _load(_TRUST, {})
    return t.get("critic", {"score": 50, "accepted": 0, "rejected": 0})


def _set_trust(t: dict) -> None:
    d = _load(_TRUST, {})
    d["critic"] = t
    _save(_TRUST, d)


def accept(project: str, note: str) -> dict:
    t = trust()
    t["accepted"] = t.get("accepted", 0) + 1
    t["score"] = min(100, t.get("score", 50) + 5)
    _set_trust(t)
    if note:
        d = _load(_LEARNED, {})
        key = project or "."
        d.setdefault(key, [])
        d[key].append({"note": note[:300], "ts": time.time()})
        d[key] = d[key][-50:]
        _save(_LEARNED, d)
    return t


def reject(project: str, note: str) -> dict:
    t = trust()
    t["rejected"] = t.get("rejected", 0) + 1
    t["score"] = max(0, t.get("score", 50) - 8)  # rejections cost more than accepts earn
    _set_trust(t)
    return t


def learned(project: str | None) -> list[str]:
    d = _load(_LEARNED, {})
    return [x["note"] for x in d.get(project or ".", [])]
