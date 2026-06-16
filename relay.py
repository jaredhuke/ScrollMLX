"""
Scroll git relay — drive Scroll from your phone, with a GitHub repo as the relay.

Run this on your Mac (where the Scroll server is running). It watches a small git
repo: drop a prompt into `inbox/` from the GitHub mobile app, and the daemon pulls
it, runs it through your local model (via the running server on 127.0.0.1:PORT),
writes the answer to `outbox/`, and pushes. Watch the repo on your phone and every
answer arrives as a commit notification — like Claude dispatch, but on your terms.

    # one-time: make a small private repo on GitHub, then on the Mac:
    git clone git@github.com:<you>/scroll-relay.git ~/.scroll/relay
    # then, with the Scroll server already running:
    uv run python relay.py            # defaults: repo=~/.scroll/relay, port=8080, every 20s

Inbox prompt = any *.md / *.txt file whose name doesn't start with '.'. The first
line may be a directive like `cwd: /path/to/project` to set the working directory.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path

DEFAULT_REPO = Path.home() / ".scroll" / "relay"


def git(repo: Path, *args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=timeout)


def ask_server(port: int, prompt: str, cwd: str) -> str:
    """Stream /v1/agent from the already-running local server and collect the text."""
    body = json.dumps({
        "messages": [{"role": "user", "content": prompt}],
        "cwd": cwd, "stream": True, "max_tokens": 4096, "temperature": 0.15,
    }).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/agent", data=body,
        headers={"Content-Type": "application/json"},
    )
    out, steps = [], []
    with urllib.request.urlopen(req, timeout=900) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload in ("", "[DONE]"):
                continue
            try:
                ev = json.loads(payload)
            except Exception:
                continue
            t = ev.get("type")
            if t == "token":
                out.append(ev.get("content", ""))
            elif t == "tool_call":
                steps.append(f"· {ev.get('name')}")
            elif t == "error":
                out.append(f"\n\n[error] {ev.get('message','')}")
    text = "".join(out).strip()
    if steps:
        text += "\n\n---\nsteps: " + "  ".join(steps[:20])
    return text or "(no output)"


def _inbox_items(repo: Path) -> list[Path]:
    inbox = repo / "inbox"
    if not inbox.exists():
        return []
    return sorted(p for p in inbox.iterdir()
                  if p.is_file() and not p.name.startswith(".") and p.suffix in (".md", ".txt"))


def _parse(p: Path, default_cwd: str) -> tuple[str, str]:
    raw = p.read_text(encoding="utf-8", errors="replace").strip()
    cwd = default_cwd
    lines = raw.splitlines()
    if lines and lines[0].lower().startswith("cwd:"):
        cwd = lines[0].split(":", 1)[1].strip() or default_cwd
        raw = "\n".join(lines[1:]).strip()
    return raw, cwd


def process_once(repo: Path, port: int, default_cwd: str) -> int:
    git(repo, "pull", "--rebase", "--autostash")
    items = _inbox_items(repo)
    if not items:
        return 0
    (repo / "outbox").mkdir(exist_ok=True)
    handled = 0
    for item in items:
        prompt, cwd = _parse(item, default_cwd)
        if not prompt:
            continue
        print(f"[relay] answering {item.name} (cwd={cwd}) …", flush=True)
        try:
            answer = ask_server(port, prompt, cwd)
        except Exception as exc:
            answer = f"[relay error] {exc}"
        out = repo / "outbox" / (item.stem + ".md")
        out.write_text(f"# {item.stem}\n\n**Prompt**\n\n{prompt}\n\n**Answer**\n\n{answer}\n",
                       encoding="utf-8")
        item.unlink()  # consume the inbox item
        git(repo, "add", "-A")
        git(repo, "commit", "-m", f"relay: answered {item.stem}")
        handled += 1
    if handled:
        r = git(repo, "push")
        print(f"[relay] pushed {handled} answer(s)" + ("" if r.returncode == 0 else f" · push error: {r.stderr.strip()[:160]}"), flush=True)
    return handled


# ── In-process control, so the UI can run the relay from a button (no terminal) ─
_CTL = {"thread": None, "stop": False, "running": False, "repo": "",
        "interval": 20, "answered": 0, "logs": deque(maxlen=120)}


def ctl_status() -> dict:
    repo = _CTL["repo"] or str(DEFAULT_REPO)
    return {
        "running": _CTL["running"], "repo": repo,
        "exists": (Path(repo).expanduser() / ".git").exists(),
        "interval": _CTL["interval"], "answered": _CTL["answered"],
        "logs": list(_CTL["logs"])[-40:],
    }


def ctl_clone(url: str) -> dict:
    DEFAULT_REPO.parent.mkdir(parents=True, exist_ok=True)
    if (DEFAULT_REPO / ".git").exists():
        return {"ok": True, "already": True, "repo": str(DEFAULT_REPO)}
    if not url:
        return {"ok": False, "error": "no repo URL"}
    r = subprocess.run(["git", "clone", url, str(DEFAULT_REPO)],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout).strip()[:300]}
    return {"ok": True, "repo": str(DEFAULT_REPO)}


def ctl_start(repo: str, port: int, cwd: str = ".", interval: int = 20) -> dict:
    if _CTL["running"]:
        return {"ok": True, "already": True}
    rp = Path(repo or DEFAULT_REPO).expanduser()
    if not (rp / ".git").exists():
        return {"ok": False, "error": f"{rp} is not a git repo — clone your relay repo first"}
    (rp / "inbox").mkdir(parents=True, exist_ok=True)
    (rp / "outbox").mkdir(parents=True, exist_ok=True)
    _CTL.update(stop=False, running=True, repo=str(rp), interval=int(interval or 20))

    def _loop():
        _CTL["logs"].append("relay started")
        while not _CTL["stop"]:
            try:
                n = process_once(rp, int(port), cwd)
                if n:
                    _CTL["answered"] += n
                    _CTL["logs"].append(f"answered {n} prompt(s)")
            except Exception as exc:
                _CTL["logs"].append(f"error: {exc}")
            for _ in range(int(_CTL["interval"]) * 2):
                if _CTL["stop"]:
                    break
                time.sleep(0.5)
        _CTL["running"] = False
        _CTL["logs"].append("relay stopped")

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    _CTL["thread"] = t
    return {"ok": True, **ctl_status()}


def ctl_stop() -> dict:
    _CTL["stop"] = True
    _CTL["running"] = False
    return {"ok": True}


def main() -> int:
    ap = argparse.ArgumentParser(description="Scroll git relay")
    ap.add_argument("--repo", default=str(DEFAULT_REPO), help="path to the relay git repo")
    ap.add_argument("--port", type=int, default=8080, help="local Scroll server port")
    ap.add_argument("--cwd", default=".", help="default working dir for prompts")
    ap.add_argument("--interval", type=int, default=20, help="poll seconds")
    ap.add_argument("--once", action="store_true", help="process once and exit")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser()
    if not (repo / ".git").exists():
        print(f"[relay] {repo} is not a git repo. Clone your relay repo there first:\n"
              f"    git clone <your-relay-repo-url> {repo}", file=sys.stderr)
        return 1
    (repo / "inbox").mkdir(exist_ok=True)
    (repo / "outbox").mkdir(exist_ok=True)

    # health check
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{args.port}/health", timeout=5) as r:
            if r.status != 200:
                raise RuntimeError("not ready")
    except Exception:
        print(f"[relay] Scroll server not reachable on :{args.port}. Start it first "
              f"(uv run uvicorn server.main:app --port {args.port}).", file=sys.stderr)
        return 1

    print(f"[relay] watching {repo} → 127.0.0.1:{args.port} every {args.interval}s. "
          f"Drop prompts in {repo}/inbox/ from your phone.", flush=True)
    if args.once:
        process_once(repo, args.port, args.cwd)
        return 0
    while True:
        try:
            process_once(repo, args.port, args.cwd)
        except KeyboardInterrupt:
            print("\n[relay] stopped.")
            return 0
        except Exception as exc:
            print(f"[relay] cycle error: {exc}", flush=True)
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
