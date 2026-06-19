"""
Scroll → ORBIT 3 push: mirror a Scroll project into ORBIT's knowledge graph as
WorkStream / Requirement / Agent / CodeCommit, over Neo4j's HTTP transactional endpoint.

The Neo4j instance is SHARED with another domain (ReactorStatus, InspectionFinding, …), so this
is strictly additive: every node is MERGE-by-id with a `scroll:`-namespaced id and source='scroll',
and there are NO deletes/purges. ORBIT's own Requirement/Agent ids never collide with ours.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.request

_URL = os.environ.get("NEO4J_TX_URL") or "http://localhost:7474/db/neo4j/tx/commit"


def _post(statements: list[dict], timeout: int = 15) -> dict:
    body = json.dumps({"statements": statements}).encode()
    req = urllib.request.Request(_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    if data.get("errors"):
        raise RuntimeError(data["errors"][0].get("message", "neo4j error"))
    return data


def ping() -> dict:
    """Is ORBIT's graph reachable? Returns Scroll's existing workstream count too."""
    try:
        d = _post([{"statement":
            "MATCH (ws:WorkStream {source:'scroll'}) RETURN count(ws) AS n"}], timeout=6)
        n = d["results"][0]["data"][0]["row"][0] if d.get("results") and d["results"][0]["data"] else 0
        return {"ok": True, "url": _URL, "scroll_workstreams": n}
    except Exception as exc:
        return {"ok": False, "url": _URL, "error": str(exc)[:200]}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "project").lower()).strip("-") or "project"


def _requirements(cwd: str, name: str) -> list[dict]:
    from server import prd
    text = prd.read(cwd, name).get("content", "") or ""
    out = []
    for i, line in enumerate(text.splitlines()):
        m = re.match(r"\s*[-*]\s*\[([ xX])\]\s*(.+)", line)
        if m:
            title = m.group(2).strip()
            out.append({"id": f"scroll:req:{_slug(name)}:{i}", "title": title[:200],
                        "status": "Done" if m.group(1).lower() == "x" else "Open"})
    return out


def _commits(cwd: str, limit: int = 20) -> list[dict]:
    try:
        r = subprocess.run(
            ["git", "-C", cwd, "log", f"-{limit}", "--pretty=%H%x1f%s%x1f%an%x1f%b%x1e"],
            capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            return []
    except Exception:
        return []
    out = []
    for rec in r.stdout.split("\x1e"):
        rec = rec.strip("\n")
        if not rec.strip():
            continue
        parts = rec.split("\x1f")
        sha, subj, author, body = (parts + ["", "", "", ""])[:4]
        if not sha:
            continue
        out.append({"id": f"scroll:commit:{sha}", "sha": sha[:12], "message": subj[:200],
                    "author": author, "isAgent": "Co-Authored-By: Claude" in body})
    return out


def push(cwd: str, name: str) -> dict:
    """Mirror the project into ORBIT's KG. Additive MERGE-by-id; never deletes."""
    name = name or "Ad hoc"
    ws_id = f"scroll:ws:{_slug(name)}"
    reqs = _requirements(cwd, name)
    commits = _commits(cwd)
    done = sum(1 for q in reqs if q["status"] == "Done")
    pct = round(done / len(reqs) * 100) if reqs else 0
    agents = [
        {"id": "scroll:agent:primary", "level": "A2", "status": "active"},
        {"id": "scroll:agent:critic", "level": "A1", "status": "idle"},
    ]
    stmts = [
        {"statement":
            "MERGE (ws:WorkStream {id:$id}) "
            "SET ws.title=$title, ws.status='active', ws.completionPct=$pct, "
            "ws.source='scroll', ws.updatedAt=timestamp()",
         "parameters": {"id": ws_id, "title": name, "pct": pct}},
        {"statement":
            "UNWIND $reqs AS rq MERGE (r:Requirement {id:rq.id}) "
            "SET r.title=rq.title, r.reviewStatus=rq.status, r.source='scroll' "
            "WITH r MATCH (ws:WorkStream {id:$ws}) MERGE (ws)-[:INCLUDES]->(r)",
         "parameters": {"reqs": reqs, "ws": ws_id}},
        {"statement":
            "UNWIND $cs AS c MERGE (cc:CodeCommit {id:c.id}) "
            "SET cc.sha=c.sha, cc.message=c.message, cc.author=c.author, cc.isAgent=c.isAgent, cc.source='scroll' "
            "WITH cc MATCH (ws:WorkStream {id:$ws}) MERGE (cc)-[:ADDRESSES]->(ws)",
         "parameters": {"cs": commits, "ws": ws_id}},
        {"statement":
            "UNWIND $ags AS a MERGE (ag:Agent {id:a.id}) "
            "SET ag.agmoLevel=a.level, ag.status=a.status, ag.source='scroll' "
            "WITH ag MATCH (ws:WorkStream {id:$ws}) MERGE (ag)-[:ASSIGNED_TO]->(ws)",
         "parameters": {"ags": agents, "ws": ws_id}},
    ]
    # drop empty UNWINDs (Neo4j is fine with empty lists, but skip for tidiness)
    stmts = [s for s in stmts if "UNWIND" not in s["statement"] or s["parameters"].get("reqs") or s["parameters"].get("cs") or s["parameters"].get("ags")]
    try:
        _post(stmts)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
    return {"ok": True, "workstream": ws_id, "requirements": len(reqs),
            "commits": len(commits), "agents": len(agents), "completionPct": pct}
