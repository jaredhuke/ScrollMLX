"""
KG staging log — the always-on capture path for the agent-memory knowledge graph.

Every KG-worthy event (a PRD requirement marked done, a new commit, a learned
correction) is appended here immediately and unconditionally, regardless of whether
Neo4j is reachable or the KG toggle is on. Nothing reaches Neo4j itself until an
explicit, user-confirmed /v1/kg/merge call — capture and merge are always separate
steps. Layout mirrors server/versions.py's snapshot convention: rooted under
~/.scroll, one append-only log + a cursor manifest per project.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from server import learn, orbit

_ROOT = Path.home() / ".scroll" / "orbit" / "staging"


def _slug(cwd: str | None) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", str(cwd or ".")).strip("-").lower()
    return s[:80] or "default"


def _dir(cwd: str | None) -> Path:
    d = _ROOT / _slug(cwd)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log_path(cwd: str | None) -> Path:
    return _dir(cwd) / "log.jsonl"


def _cursor_path(cwd: str | None) -> Path:
    return _dir(cwd) / "cursor.json"


def _load_cursor(cwd: str | None) -> dict:
    try:
        return json.loads(_cursor_path(cwd).read_text())
    except Exception:
        return {}


def _save_cursor(cwd: str | None, cur: dict) -> None:
    _cursor_path(cwd).write_text(json.dumps(cur))


def _hash8(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()[:8]


def _record(cwd: str | None, kind: str, title: str, graph_hint: dict, source) -> dict:
    return {
        "id": f"stg:{kind}:{_slug(cwd)}:{_hash8(kind + title)}",
        "type": kind,
        "cwd": cwd or ".",
        "title": (title or "")[:200],
        "captured_at": time.time(),
        "source": source,
        "graph_hint": graph_hint,
        "merged_at": None,
        "merged_ws": None,
    }


def _append(cwd: str | None, records: list[dict]) -> None:
    if not records:
        return
    with _log_path(cwd).open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def capture(cwd: str | None, name: str | None = None) -> dict:
    """Idempotent — safe to call from any mutation path at any time. Diffs live sources
    (PRD checkboxes, git log, learned corrections) against the last-seen cursor and
    appends only what's new, so repeated calls never duplicate a record."""
    name = name or "Ad hoc"
    cursor = _load_cursor(cwd)
    new_records: list[dict] = []

    # requirements — reuse orbit's own PRD-checkbox extraction, diff against the done-set we've seen
    try:
        reqs = orbit._requirements(cwd, name)
    except Exception:
        reqs = []
    seen_done = set(cursor.get("done_reqs") or [])
    for r in reqs:
        if r["status"] == "Done" and r["id"] not in seen_done:
            new_records.append(_record(
                cwd, "requirement_done", r["title"],
                {"label": "Requirement", "props": {"id": r["id"], "title": r["title"], "reviewStatus": "Done"}},
                r))
            seen_done.add(r["id"])
    cursor["done_reqs"] = sorted(seen_done)

    # commits — reuse orbit's git-log extraction, diff against the last SHA we've seen
    try:
        commits = orbit._commits(cwd)
    except Exception:
        commits = []
    last_sha = cursor.get("last_sha")
    fresh = []
    for c in commits:
        if last_sha and c["sha"] == last_sha:
            break
        fresh.append(c)
    for c in fresh:
        new_records.append(_record(
            cwd, "commit", c["message"],
            {"label": "CodeCommit", "props": {"id": c["id"], "sha": c["sha"], "message": c["message"],
                                               "author": c["author"], "isAgent": c["isAgent"]}},
            c))
    if commits:
        cursor["last_sha"] = commits[0]["sha"]

    # learned corrections — diff against the count we've seen for this project
    try:
        notes = learn.learned(name)
    except Exception:
        notes = []
    seen_n = int(cursor.get("learned_count") or 0)
    for note_text in notes[seen_n:]:
        new_records.append(_record(
            cwd, "correction", note_text,
            {"label": "Insight", "props": {"title": note_text, "kind": "correction"}},
            {"note": note_text}))
    cursor["learned_count"] = len(notes)

    _append(cwd, new_records)
    _save_cursor(cwd, cursor)
    return {"ok": True, "captured": len(new_records)}


def pending(cwd: str | None) -> list[dict]:
    """Unmerged staged records. Refreshes (captures) first, so this is never stale even
    if a mutation path's inline capture() call was missed."""
    capture(cwd)
    out = []
    try:
        for line in _log_path(cwd).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not rec.get("merged_at"):
                out.append(rec)
    except Exception:
        pass
    return out


def mark_merged(cwd: str | None, ids: set, ws_id: str | None) -> int:
    """Flip merged_at/merged_ws for the given record ids. A full-file rewrite — these
    per-project logs stay small, so this is simpler and safer than in-place patching."""
    p = _log_path(cwd)
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except Exception:
        return 0
    n = 0
    out = []
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            out.append(line)
            continue
        if rec.get("id") in ids and not rec.get("merged_at"):
            rec["merged_at"] = time.time()
            rec["merged_ws"] = ws_id
            n += 1
        out.append(json.dumps(rec))
    p.write_text("\n".join(out) + ("\n" if out else ""))
    return n
