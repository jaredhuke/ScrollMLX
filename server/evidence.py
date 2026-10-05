"""
Evidence spine — the single, append-only, replayable record of what the agentic
system did. This is the substrate for AEM-030 rule 6 (one record, three filters)
and rule 1 (surfaces render platform-recorded EVENTS, never agent narrative).

Design:
- Append-only JSONL: one JSON object per line, newest appended last. No rewrite,
  no delete — the file is a ledger of what actually happened, in order.
- Thread-safe: the agent loop runs on background threads (see server/main.py).
- Correlated: every event carries a `trace_id` (one logical run: a prompt, a
  pipeline leg, a fan-out branch) and optionally a `workflow_id` (the orchestrated
  unit that groups traces). This is the seam for per-agent/per-leg cost and
  oversight attribution.
- Replayable: `replay(trace_id)` returns the chronological events for a trace,
  which is the cold-replay acceptance primitive from AEM-030.

Events here are free-form (a `type` string + keyword fields) so callers can add
dimensions without a schema migration. Use the module-level constants so control
surfaces and replay stay consistent.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

_DEFAULT = Path.home() / ".scroll" / "evidence.jsonl"

# Event type constants — the controlled vocabulary for the spine.
RUN_START = "run.start"
RUN_DONE = "run.done"
RUN_ERROR = "run.error"
TOOL_CALL = "tool.call"
TOOL_RESULT = "tool.result"
PHASE = "phase"
VERDICT = "verdict"
HALT = "halt"                  # ungated operator stop (AEM-030 rule 5)
INTERACTION = "interaction"    # human oversight: review / override / apply / halt
AGMO = "agmo"                  # authority classification and drift
BUDGET = "budget.check"        # spend/budget gate outcomes

_lock = threading.Lock()


def _path() -> Path:
    """Evidence file path. Overridable via SCROLL_EVIDENCE_PATH for tests/relocation."""
    p = os.environ.get("SCROLL_EVIDENCE_PATH")
    return Path(p).expanduser() if p else _DEFAULT


def new_workflow() -> str:
    """Start an orchestrated unit (pipeline / fan-out); returns workflow_id."""
    return uuid.uuid4().hex[:12]


def new_trace(workflow_id: str | None = None, agent_id: str | None = None) -> str:
    """Start a correlated run; returns the trace_id to pass to event()."""
    trace_id = uuid.uuid4().hex[:12]
    rec = {"ts": time.time(), "trace_id": trace_id, "type": RUN_START}
    if workflow_id:
        rec["workflow_id"] = workflow_id
    if agent_id:
        rec["agent_id"] = agent_id
    _append(rec)
    return trace_id


def event(trace_id: str, type_: str, **fields) -> dict:
    """Append one event to the spine and return it (for callers that chain)."""
    rec = {"ts": time.time(), "trace_id": trace_id, "type": type_, **fields}
    _append(rec)
    return rec


def _append(rec: dict) -> None:
    with _lock:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def events(
    trace_id: str | None = None,
    type_: str | None = None,
    limit: int = 0,
) -> list[dict]:
    """Read recorded events (chronological). Filter by trace and/or type."""
    p = _path()
    if not p.exists():
        return []
    out: list[dict] = []
    with _lock:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if trace_id is not None and rec.get("trace_id") != trace_id:
                continue
            if type_ is not None and rec.get("type") != type_:
                continue
            out.append(rec)
    return out[-limit:] if limit else out


def replay(trace_id: str) -> list[dict]:
    """Chronological events for one trace — the cold-replay acceptance primitive."""
    return events(trace_id=trace_id)
