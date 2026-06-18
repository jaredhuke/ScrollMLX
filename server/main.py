"""
FastAPI server — OpenAI-compatible /v1/chat/completions + /v1/agent endpoints.
Run with: uv run uvicorn server.main:app --port 8080
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from typing import AsyncGenerator

from pathlib import Path

# Persistent log so an unexpected exit ("Services quit unexpectedly") leaves a trail.
_LOG_DIR = Path.home() / ".scroll"
try:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _fh = RotatingFileHandler(_LOG_DIR / "server.log", maxBytes=2_000_000, backupCount=2)
    _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    _root = logging.getLogger()
    if not any(isinstance(h, RotatingFileHandler) for h in _root.handlers):
        _root.addHandler(_fh)
        _root.setLevel(logging.INFO)
    _log = logging.getLogger("scroll")

    def _excepthook(t, v, tb):
        _log.error("uncaught exception", exc_info=(t, v, tb))
        sys.__excepthook__(t, v, tb)
    sys.excepthook = _excepthook
except Exception:
    _log = logging.getLogger("scroll")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from server import agent as agent_mod
from server import dual_agent as dual_mod
from server import secrets as secrets_mod
from server.config import CRITIC_MODEL, MAX_TOKENS, MODEL, PORT, TEMPERATURE, SYSTEM_PROMPT
from server.schemas import (
    AgentEvent, ChatRequest, DualAgentRequest, ErrorEvent, PhaseEvent,
    ExtensionLoadRequest, MCPConnectorRequest,
    OpenAIConnectorRequest, ProviderSelectRequest,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_event_loop()
    loaded = secrets_mod.load_into_env()  # pull cloud keys from Keychain → env
    if loaded:
        print(f"[secrets] loaded keys for: {', '.join(loaded)}")
    # Only preload the local model if it's ALREADY downloaded — otherwise boot would
    # block on an 18 GB download and the onboarding UI could never appear. When it's
    # not cached, the server serves immediately and onboarding installs it on demand.
    from server import models as models_mod
    if models_mod.is_cached(MODEL):
        await loop.run_in_executor(None, agent_mod.load_model, MODEL, "primary")
    else:
        print("[mlx] primary model not downloaded yet — onboarding will install it; serving now.")
    yield


app = FastAPI(title="Scroll", lifespan=lifespan)

_STATIC = Path(__file__).parent.parent / "static"


@app.get("/")
async def root():
    return FileResponse(_STATIC / "index.html")


@app.get("/logo.svg")
async def logo():
    return FileResponse(_STATIC / "logo.svg", media_type="image/svg+xml")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _stream_agent(req: ChatRequest) -> AsyncGenerator[str, None]:
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

    def _run():
        try:
            if not agent_mod.is_loaded("primary"):
                agent_mod.load_model(req.model or MODEL, "primary")  # lazy load (downloads only if onboarding was skipped)
            messages = [m.model_dump(exclude_none=True) for m in req.messages]
            from server import steering, skills as skills_mod
            steer = steering.as_system_text(req.cwd)  # standing context file: user section + learned
            if steer:
                messages = [{"role": "system", "content": steer}] + messages
            sk = skills_mod.as_system_text()  # enabled skill .md files
            if sk:
                messages = [{"role": "system", "content": sk}] + messages
            # (the client also sends the user section so /v1/dual and /v1/escalate honor it)
            ctx = _context_system_msg()  # live macOS context from the native app
            if ctx:
                messages = [ctx] + messages
            for event in agent_mod.run_agent(
                messages=messages,
                cwd=req.cwd,
                max_tokens=req.max_tokens,
                temperature=req.temperature,
            ):
                asyncio.run_coroutine_threadsafe(queue.put(event), loop)
        except Exception as exc:
            from server.schemas import ErrorEvent
            asyncio.run_coroutine_threadsafe(
                queue.put(ErrorEvent(message=str(exc))), loop
            )
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop)

    import threading
    t = threading.Thread(target=_run, daemon=True)
    t.start()

    total = 0
    tool_calls = 0
    while True:
        event = await queue.get()
        if event is None:
            break
        if event.type == "done":
            total = event.total_tokens
        elif event.type == "tool_call":
            tool_calls += 1
        yield f"data: {event.model_dump_json()}\n\n"

    from server import ledger
    prompt = next((m.content for m in reversed(req.messages) if m.role == "user"), "") or ""
    ledger.record(req.cwd, prompt, total, mode="local", tools=tool_calls)
    yield "data: [DONE]\n\n"


@app.post("/v1/agent")
async def agent_endpoint(req: ChatRequest):
    """SSE stream of AgentEvent objects."""
    if not req.stream:
        # Collect all events and return the final assistant text
        if not agent_mod.is_loaded("primary"):
            agent_mod.load_model(req.model or MODEL, "primary")
        messages = [m.model_dump(exclude_none=True) for m in req.messages]
        text_parts = []
        for event in agent_mod.run_agent(
            messages=messages,
            cwd=req.cwd,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
        ):
            if event.type == "token":
                text_parts.append(event.content)
        return {"content": "".join(text_parts)}

    return StreamingResponse(
        _stream_agent(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _stream_dual(req: DualAgentRequest) -> AsyncGenerator[str, None]:
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

    def _run():
        try:
            # Lazy-load critic model on first /v1/dual request
            if not agent_mod.is_loaded("critic"):
                agent_mod.load_model(req.critic_model, "critic")

            messages = [m.model_dump(exclude_none=True) for m in req.messages]
            for event in dual_mod.run_dual_agent(
                messages=messages,
                cwd=req.cwd,
                max_tokens=req.max_tokens,
                temperature=req.temperature,
                revision=req.revision,
            ):
                asyncio.run_coroutine_threadsafe(queue.put(event), loop)
        except Exception as exc:
            asyncio.run_coroutine_threadsafe(
                queue.put(ErrorEvent(message=str(exc))), loop
            )
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop)

    import threading
    from server.schemas import ErrorEvent
    t = threading.Thread(target=_run, daemon=True)
    t.start()

    while True:
        event = await queue.get()
        if event is None:
            break
        yield f"data: {event.model_dump_json()}\n\n"
    yield "data: [DONE]\n\n"


@app.post("/v1/dual")
async def dual_endpoint(req: DualAgentRequest):
    """SSE stream for the dual-agent critique pipeline."""
    return StreamingResponse(
        _stream_dual(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/v1/model")
async def get_model():
    return {
        "primary": MODEL,
        "critic": CRITIC_MODEL,
        "loaded": {k: True for k in agent_mod._registry},
    }


@app.get("/health")
async def health():
    return {"status": "ok", "loaded_slots": list(agent_mod._registry.keys())}


# ── Provider endpoints ────────────────────────────────────────────────────────

@app.get("/v1/providers")
async def list_providers_endpoint():
    from server.providers import list_providers
    return {"providers": list_providers()}


@app.post("/v1/providers/select")
async def select_provider(req: ProviderSelectRequest):
    from server.providers import set_active
    try:
        set_active(req.provider)
        return {"active": req.provider}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── Tool management endpoints ─────────────────────────────────────────────────

@app.get("/v1/tools")
async def list_tools():
    from server.tool_registry import tool_manager
    return {"tools": tool_manager.list()}


@app.post("/v1/tools/{name}/enable")
async def enable_tool(name: str):
    from server.tool_registry import tool_manager
    try:
        tool_manager.enable(name)
        return {"ok": True, "name": name, "enabled": True}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/v1/tools/{name}/disable")
async def disable_tool(name: str):
    from server.tool_registry import tool_manager
    try:
        tool_manager.disable(name)
        return {"ok": True, "name": name, "enabled": False}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/v1/tools/{name}/unregister")
async def unregister_tool(name: str):
    from server.tool_registry import tool_manager
    try:
        tool_manager.unregister(name)
        return {"ok": True, "name": name}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── Extension / connector loaders ─────────────────────────────────────────────

@app.post("/v1/extensions")
async def load_extension(req: ExtensionLoadRequest):
    from server.tool_registry import tool_manager
    try:
        loaded = tool_manager.load_extension(req.path)
        return {"loaded": loaded}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/v1/connectors/mcp")
async def load_mcp_connector(req: MCPConnectorRequest):
    from server.tool_registry import tool_manager
    try:
        loaded = tool_manager.load_mcp(req.server_url)
        return {"loaded": loaded, "source": f"mcp:{req.server_url}"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/v1/connectors/openai")
async def load_openai_connector(req: OpenAIConnectorRequest):
    from server.tool_registry import tool_manager
    loaded = tool_manager.load_openai_connector(req.tool_defs, req.base_url, req.api_key)
    return {"loaded": loaded, "source": f"openai-connector:{req.base_url}"}


# ── Cloud escalation: re-run the last exchange on a frontier model ─────────────

@app.get("/v1/keys")
async def keys_status():
    """Which cloud providers have a key available (env or Keychain)."""
    return {"providers": secrets_mod.list_providers_with_keys()}


@app.post("/v1/keys/set")
async def keys_set(payload: dict):
    """Store a provider API key in the OS keychain and activate it immediately.

    The key is written straight to the macOS Keychain (or a 0600 file off-Mac)
    and loaded into this process's env. It is never logged, never persisted in
    plaintext by the app, and never returned to the browser.
    """
    provider = (payload.get("provider") or "").lower().strip()
    key = (payload.get("key") or "").strip()
    if provider not in secrets_mod.ENV_VARS:
        raise HTTPException(400, "unknown provider")
    if len(key) < 8:
        return {"ok": False, "error": "that key looks too short"}
    try:
        secrets_mod.set_key(provider, key)
        os.environ[secrets_mod.ENV_VARS[provider]] = key  # live now, no restart
    except Exception as exc:
        return {"ok": False, "error": f"could not save to keychain: {exc}"}
    return {"ok": True, "provider": provider}


@app.post("/v1/keys/delete")
async def keys_delete(payload: dict):
    """Remove a stored key from the keychain and this process's env."""
    provider = (payload.get("provider") or "").lower().strip()
    if provider not in secrets_mod.ENV_VARS:
        raise HTTPException(400, "unknown provider")
    try:
        secrets_mod.delete_key(provider)
        os.environ.pop(secrets_mod.ENV_VARS[provider], None)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "provider": provider}


async def _stream_escalate(payload: dict) -> AsyncGenerator[str, None]:
    from server import providers
    prov_name = (payload.get("provider") or "anthropic").lower()
    prov = providers.get_provider(prov_name)
    msgs = [{"role": m.get("role"), "content": m.get("content", "")}
            for m in payload.get("messages", []) if m.get("role") != "system"]
    mt = int(payload.get("max_tokens", 4096))
    temp = float(payload.get("temperature", 0.2))

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def _run():
        try:
            full = [{"role": "system", "content": SYSTEM_PROMPT}]
            ctx = _context_system_msg("code")  # cloud sees code only — never personal context
            if ctx:
                full.append(ctx)
            full += msgs
            for evt in prov.stream(full, mt, temp, tools=None):
                asyncio.run_coroutine_threadsafe(queue.put(evt), loop)
        except Exception as exc:
            asyncio.run_coroutine_threadsafe(queue.put(ErrorEvent(message=str(exc))), loop)
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop)

    import threading
    threading.Thread(target=_run, daemon=True).start()

    model_id = getattr(prov, "model", prov_name)
    yield f"data: {PhaseEvent(name='escalation', model=model_id).model_dump_json()}\n\n"
    while True:
        evt = await queue.get()
        if evt is None:
            break
        yield f"data: {evt.model_dump_json()}\n\n"
    yield "data: [DONE]\n\n"


@app.post("/v1/escalate")
async def escalate_endpoint(payload: dict):
    """Stream a fresh answer to the same conversation from a cloud model (e.g. Claude)."""
    from server import providers
    prov_name = (payload.get("provider") or "anthropic").lower()
    try:
        prov = providers.get_provider(prov_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not prov.available():
        raise HTTPException(
            400, f"{prov_name} unavailable — add a key with:  python cli.py key set {prov_name}")
    return StreamingResponse(
        _stream_escalate(payload), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Embedded CLI + Apply-code ──────────────────────────────────────────────────

@app.post("/v1/shell")
async def shell_endpoint(payload: dict):
    """Run a shell command in the working dir (same guard as the agent's tool)."""
    from server.tools.shell import run_command
    cmd = (payload.get("command") or "").strip()
    if not cmd:
        raise HTTPException(400, "empty command")
    out = run_command(cmd, cwd=payload.get("cwd") or ".")
    return {"ok": True, "output": out}


@app.post("/v1/artifacts/write")
async def artifacts_write(payload: dict):
    """Apply a code block to a file (the 'Apply' action on code artifacts)."""
    from server.tools.filesystem import write_file
    path = payload.get("path")
    if not path:
        raise HTTPException(400, "missing path")
    try:
        res = write_file(path=path, content=payload.get("content", ""), cwd=payload.get("cwd") or ".")
        return {"ok": True, "path": path, "result": res}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/v1/versions")
async def artifacts_versions(path: str = "", cwd: str = "."):
    """Readable version history for a file (newest first); the live filename is unchanged."""
    from server import versions
    return {"versions": versions.list_versions(path, cwd)} if path else {"versions": []}


@app.post("/v1/artifacts/read")
async def artifacts_read(payload: dict):
    """Read an artifact's content so the UI can preview it (live render or source)."""
    from server.tools.filesystem import _resolve
    path = payload.get("path")
    if not path:
        raise HTTPException(400, "missing path")
    try:
        p = _resolve(path, payload.get("cwd") or ".")
        if not p.exists() or not p.is_file():
            return {"ok": False, "error": "file not found"}
        content = p.read_text(encoding="utf-8", errors="replace")[:200000]
        return {"ok": True, "path": path, "ext": p.suffix.lower().lstrip("."), "content": content}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Add files / add a repo (for review · summarize · recommend · edit) ──────────

@app.post("/v1/upload")
async def upload_files(payload: dict):
    """Save text/code files the user added so the agent can review/edit them."""
    from server.tools.filesystem import write_file
    cwd = payload.get("cwd") or "."
    saved = []
    for f in payload.get("files", []):
        name = f.get("name")
        if not name:
            continue
        try:
            write_file(path=name, content=f.get("content", ""), cwd=cwd)
            saved.append(name)
        except Exception:
            pass
    return {"ok": bool(saved), "saved": saved}


@app.post("/v1/repo/clone")
async def repo_clone(payload: dict):
    """Shallow-clone a git repo into the working dir so the agent can review/edit it."""
    import subprocess
    url = (payload.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "missing url")
    cwd = Path(payload.get("cwd") or ".").expanduser()
    name = url.rstrip("/").split("/")[-1].replace(".git", "") or "repo"
    dest = cwd / name
    try:
        r = subprocess.run(["git", "clone", "--depth", "1", url, str(dest)],
                           capture_output=True, text=True, timeout=240)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout).strip()[:400]}
    return {"ok": True, "name": name, "path": str(dest)}


# ── Vision: the local model is text-only, so route images to a multimodal model ─

def _vision_call(provider: str, b64: str, mime: str, prompt: str) -> str:
    """Send one image + prompt to a multimodal cloud model and return its text."""
    import base64 as _b64
    raw = _b64.b64decode(b64)
    if provider == "gemini":
        from google import genai
        from google.genai import types
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY not set")
        client = genai.Client(api_key=key)
        img = types.Part.from_bytes(data=raw, mime_type=mime)
        r = client.models.generate_content(model="gemini-2.0-flash", contents=[prompt, img])
        return r.text or "(no description returned)"
    if provider in ("openai", "openrouter"):
        import openai
        if provider == "openai":
            key = os.environ.get("OPENAI_API_KEY"); base = None; model = "gpt-4o"
        else:
            key = os.environ.get("OPENROUTER_API_KEY"); base = "https://openrouter.ai/api/v1"
            model = "google/gemini-2.0-flash-exp:free"
        if not key:
            raise RuntimeError(f"{provider.upper()}_API_KEY not set")
        client = openai.OpenAI(api_key=key, base_url=base)
        r = client.chat.completions.create(
            model=model, max_tokens=1024,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]}],
        )
        return r.choices[0].message.content or "(no description returned)"
    raise RuntimeError(f"{provider} has no vision support — use gemini, openai or openrouter")


VISION_PROVIDERS = ("gemini", "openai", "openrouter")


@app.post("/v1/vision")
async def vision(payload: dict):
    """Read an image with a multimodal model (image leaves the machine — user-initiated)."""
    provider = (payload.get("provider") or "gemini").lower()
    if provider not in VISION_PROVIDERS:
        return {"ok": False, "error": f"{provider} can't see images; use {', '.join(VISION_PROVIDERS)}"}
    prompt = payload.get("prompt") or "Describe this image in detail. If it contains text, transcribe it verbatim."
    b64 = payload.get("image_b64")
    mime = payload.get("mime") or "image/jpeg"
    if not b64 and payload.get("path"):
        from server.tools.filesystem import _resolve
        import base64 as _b64
        import mimetypes
        p = _resolve(payload["path"], payload.get("cwd") or ".")
        if not p.exists() or not p.is_file():
            return {"ok": False, "error": "image not found"}
        b64 = _b64.b64encode(p.read_bytes()).decode()
        mime = mimetypes.guess_type(str(p))[0] or "image/jpeg"
    if not b64:
        return {"ok": False, "error": "no image supplied"}
    loop = asyncio.get_event_loop()
    try:
        text = await loop.run_in_executor(None, _vision_call, provider, b64, mime, prompt)
        return {"ok": True, "provider": provider, "text": text}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Projects — a git repo + a runnable "product"; launch & manage from here ─────

@app.get("/v1/projects")
async def projects_list():
    from server import projects
    return {"projects": projects.list_projects()}


@app.post("/v1/projects/add")
async def projects_add(payload: dict):
    from server import projects
    if not (payload.get("name") or payload.get("repo") or payload.get("path")):
        raise HTTPException(400, "need a name, path, or repo")
    try:
        return {"ok": True, "project": projects.add(
            name=payload.get("name", ""), path=payload.get("path", ""),
            repo=payload.get("repo", ""), start=payload.get("start", ""),
            port=int(payload.get("port") or 0))}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/v1/projects/update")
async def projects_update(payload: dict):
    from server import projects
    p = projects.update(payload.get("id", ""), **payload)
    return {"ok": bool(p), "project": p}


@app.post("/v1/projects/launch")
async def projects_launch(payload: dict):
    from server import projects
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, projects.launch, payload.get("id", ""))


@app.post("/v1/projects/stop")
async def projects_stop(payload: dict):
    from server import projects
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, projects.stop, payload.get("id", ""))


@app.post("/v1/projects/remove")
async def projects_remove(payload: dict):
    from server import projects
    return {"ok": projects.remove(payload.get("id", ""))}


@app.get("/v1/projects/logs")
async def projects_logs(id: str = "", n: int = 80):
    from server import projects
    return {"logs": projects.logs(id, n)}


# ── Phone relay — start/stop from a UI button (no terminal) ─────────────────────

@app.get("/v1/relay/status")
async def relay_status():
    import relay
    return relay.ctl_status()


@app.post("/v1/relay/clone")
async def relay_clone(payload: dict):
    import relay
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, relay.ctl_clone, payload.get("url", ""))


@app.post("/v1/relay/start")
async def relay_start(payload: dict):
    import relay
    return relay.ctl_start(payload.get("repo", ""), PORT,
                           payload.get("cwd", "."), int(payload.get("interval") or 20))


@app.post("/v1/relay/stop")
async def relay_stop():
    import relay
    return relay.ctl_stop()


# ── Deep local context engine (fed by the native macOS app) ────────────────────

_LATEST_CONTEXT: dict = {}


@app.post("/v1/context")
async def set_context(payload: dict):
    """Receive a macOS local-context snapshot from the native app."""
    global _LATEST_CONTEXT
    _LATEST_CONTEXT = payload or {}
    return {"ok": True}


@app.get("/v1/context")
async def get_context():
    """Latest local-context snapshot (UI reads CPU/mem/thermal etc. from here)."""
    return _LATEST_CONTEXT


# ── Standing context — a persistent, incremental file the agent always honors ───

@app.get("/v1/standing")
async def standing_get(project: str = "."):
    from server import steering
    return steering.view(project)


@app.post("/v1/standing")
async def standing_post(payload: dict):
    """add: append one user line · set: replace user section · remove: drop by index."""
    from server import steering
    project = payload.get("project") or "."
    if "set" in payload:
        steering.set_user(project, payload.get("set") or "")
    elif payload.get("remove") is not None:
        steering.remove_user(project, int(payload["remove"]))
    elif payload.get("add"):
        steering.add_user(project, payload["add"])
    elif payload.get("learn"):
        from server import learn
        learn.note(project, payload["learn"])  # edit/challenge friction → learned correction
    return steering.view(project)


# ── Skill files — drop-in .md instructions the agent follows ───────────────────

@app.get("/v1/skills")
async def skills_list():
    from server import skills
    return {"skills": skills.list_skills()}


@app.get("/v1/skills/get")
async def skills_get(name: str = ""):
    from server import skills
    return {"name": name, "content": skills.get(name)}


@app.post("/v1/skills/add")
async def skills_add(payload: dict):
    from server import skills
    name = (payload.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "name required"}
    return {"ok": True, "name": skills.add(name, payload.get("content", ""))}


@app.post("/v1/skills/import")
async def skills_import(payload: dict):
    """Import skill(s) by pointing at a local file or folder; name from the filename."""
    from server import skills
    return skills.import_path(payload.get("path", ""))


@app.post("/v1/skills/toggle")
async def skills_toggle(payload: dict):
    from server import skills
    skills.set_enabled(payload.get("name", ""), bool(payload.get("enabled", True)))
    return {"ok": True}


@app.post("/v1/skills/remove")
async def skills_remove(payload: dict):
    from server import skills
    skills.remove(payload.get("name", ""))
    return {"ok": True}


# ── Access barriers (crystal-clear, enforced) ──────────────────────────────────
# CODE scope is safe to share with cloud models. PERSONAL scope (contacts, calendar,
# clipboard, what app you're in) NEVER leaves the machine — only the local MLX model
# sees it. This is enforced here, not just drawn in the UI.
_CODE_KEYS = {"cwd", "git", "recent_files", "finder_selection"}
_PERSONAL_KEYS = {"clipboard", "calendar_today", "reminders", "frontmost_app", "frontmost_window", "contacts"}

ACCESS_POLICY = {
    "scopes": {"code": sorted(_CODE_KEYS), "personal": sorted(_PERSONAL_KEYS)},
    "agents": {
        "local": {"label": "Local · MLX", "access": ["code", "personal"]},
        "cloud": {"label": "Cloud · Claude / GPT / Gemini", "access": ["code"]},
    },
}


@app.get("/v1/policy")
async def access_policy():
    """Who can see what — the UI renders the visible access barriers from this."""
    return ACCESS_POLICY


def _context_system_msg(scope: str = "full") -> dict | None:
    """Build the live-context message. scope='code' (cloud) omits all personal context."""
    c = _LATEST_CONTEXT
    if not c:
        return None
    allow = _CODE_KEYS if scope == "code" else (_CODE_KEYS | _PERSONAL_KEYS)
    lines = []
    if "cwd" in allow and c.get("cwd"):
        lines.append(f"Working directory: {c['cwd']}")
    g = c.get("git") or {}
    if "git" in allow and g.get("branch"):
        lines.append(f"Git: {g.get('branch')} · {g.get('dirty', 0)} changed · {g.get('last_commit', '')}")
    if "finder_selection" in allow and c.get("finder_selection"):
        lines.append("Selected in Finder: " + ", ".join(c["finder_selection"][:5]))
    if "recent_files" in allow and c.get("recent_files"):
        lines.append("Recently edited: " + ", ".join(p.split("/")[-1] for p in c["recent_files"][:8]))
    if "frontmost_app" in allow and c.get("frontmost_app"):
        win = f" — {c['frontmost_window']}" if c.get("frontmost_window") else ""
        lines.append(f"Frontmost app: {c['frontmost_app']}{win}")
    if "calendar_today" in allow and c.get("calendar_today"):
        lines.append("Today's calendar: " + "; ".join(c["calendar_today"][:5]))
    if "clipboard" in allow and c.get("clipboard"):
        lines.append("Clipboard (excerpt): " + str(c["clipboard"])[:200])
    if not lines:
        return None
    tag = "code-only" if scope == "code" else "full"
    return {"role": "system", "content": f"Live local machine context ({tag} — use only when relevant):\n- " + "\n- ".join(lines)}


# ── Always-on critic (judges every response, in plain language) ────────────────

_CRITIQUE_SYS = (
    "You are a terse senior reviewer. Judge the assistant's answer for correctness, "
    "completeness, and whether it actually did what the user asked. "
    "Only raise an issue if it GENUINELY matters — no nitpicks, no noise. If the answer "
    "is fine, reply LGTM. Reply in EXACTLY this format and nothing else:\n"
    "VERDICT: LGTM | ISSUES | CRITICAL\n"
    "NOTE: one or two plain-language sentences. No code."
)


def _do_critique(payload: dict) -> dict:
    from server import agent as agent_mod
    # Reuse the already-loaded PRIMARY model as the reviewer. Loading a second model
    # alongside the 32B exhausts unified memory (Metal OOM), so the critic is a role,
    # not a separate model — fits memory and runs on every prompt.
    convo = payload.get("messages", [])
    response = (payload.get("response", "") or "")[:6000]
    last_user = next((m.get("content", "") for m in reversed(convo) if m.get("role") == "user"), "")
    judge = f"User asked:\n{last_user}\n\nThe assistant answered:\n{response}\n\nJudge the answer."
    text = ""
    for ev in agent_mod.run_agent(
        messages=[{"role": "user", "content": judge}],
        cwd=payload.get("cwd", "."), max_tokens=240, temperature=0.2,
        slot="primary", system_prompt=_CRITIQUE_SYS, tools=False,
    ):
        if ev.type == "token":
            text += ev.content
    verdict = "ISSUES"
    m = re.search(r"VERDICT:\s*(LGTM|ISSUES|CRITICAL)", text, re.I)
    if m:
        verdict = m.group(1).upper()
    note = ""
    n = re.search(r"NOTE:\s*(.+)", text, re.S)
    if n:
        note = re.sub(r"\s+", " ", n.group(1)).strip()[:280]
    return {"verdict": verdict, "note": note}


@app.post("/v1/critique")
async def critique_endpoint(payload: dict):
    """A quick critic pass over the latest answer — runs on every prompt."""
    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(None, _do_critique, payload)
    from server import learn
    res["trust"] = learn.trust()
    return res


@app.post("/v1/critic/accept")
async def critic_accept(payload: dict):
    """User agreed with the critic → remember it for PRIMARY + raise critic trust."""
    from server import learn
    return learn.accept(payload.get("project") or ".", payload.get("note", ""))


@app.post("/v1/critic/reject")
async def critic_reject(payload: dict):
    """User disagreed → lower critic trust (it gets quieter / earns its voice back)."""
    from server import learn
    return learn.reject(payload.get("project") or ".", payload.get("note", ""))


@app.get("/v1/critic/trust")
async def critic_trust():
    from server import learn
    return learn.trust()


# ── Onboarding: read the system, recommend which agents to use for what ─────────

def _system_info() -> dict:
    import subprocess
    import platform as _pf

    def sysctl(k: str) -> str:
        try:
            return subprocess.run(["sysctl", "-n", k], capture_output=True, text=True, timeout=3).stdout.strip()
        except Exception:
            return ""

    mem = sysctl("hw.memsize")
    ram_gb = round(int(mem) / (1024 ** 3)) if mem.isdigit() else None
    chip = sysctl("machdep.cpu.brand_string") or _pf.processor() or _pf.machine()
    return {"ram_gb": ram_gb, "chip": chip or "unknown", "cpus": os.cpu_count(), "platform": _pf.platform()}


def _recommendations(sysinfo: dict, keys: dict) -> list[dict]:
    ram = sysinfo.get("ram_gb") or 16
    recs: list[dict] = []
    if ram >= 64:
        recs += [
            {"agent": "PRIMARY", "model": "Qwen2.5-Coder 32B · local", "role": "Author — all coding", "why": f"{ram} GB fits the 32B comfortably."},
            {"agent": "CRITIC", "model": "Qwen2.5-Coder 7B · local", "role": "Reviews every reply", "why": f"{ram} GB can hold a second small model alongside the 32B."},
        ]
    elif ram >= 24:
        recs += [
            {"agent": "PRIMARY", "model": "Qwen2.5-Coder 32B · local", "role": "Author — all coding", "why": f"{ram} GB runs the 32B, but not a second model at once."},
            {"agent": "CRITIC", "model": "reuses PRIMARY (32B)", "role": "Reviews every reply", "why": "A separate 7B would exhaust memory, so the critic runs on the primary."},
        ]
    else:
        recs += [
            {"agent": "PRIMARY", "model": "Qwen2.5-Coder 7B/14B · local", "role": "Author — lighter work", "why": f"{ram} GB is tight for the 32B; use a smaller local model."},
            {"agent": "CRITIC", "model": "reuses PRIMARY", "role": "Reviews every reply", "why": "Keeps memory free for the primary."},
        ]
    if keys.get("anthropic"):
        recs.append({"agent": "ESCALATION", "model": "Claude · cloud", "role": "Hard reasoning, big refactors, images", "why": "Anthropic key is set — best for tough problems and multimodal."})
    if keys.get("gemini"):
        recs.append({"agent": "CLOUD", "model": "Gemini · cloud", "role": "Long-context, images & audio, cheap bulk", "why": "Gemini key is set — great for huge context and multimodal."})
    if keys.get("openai"):
        recs.append({"agent": "CLOUD", "model": "GPT-4o · cloud", "role": "General escalation & vision", "why": "OpenAI key is set."})
    if not any(keys.values()):
        recs.append({"agent": "ESCALATION", "model": "not connected", "role": "Add a cloud key for hard problems & images", "why": "Run:  python cli.py key set anthropic"})
    return recs


try:
    import psutil as _psutil
    _psutil.cpu_percent(interval=None)  # prime the non-blocking sampler
except Exception:
    _psutil = None


@app.get("/v1/sys")
async def sys_stats():
    """Live machine stats for the header gauges. CPU/MEM via psutil; temp/fan/thermal
    come from the native macOS app's context snapshot when it's running."""
    out = {"cpu": None, "cores": None, "mem_pct": None, "mem_used_gb": None,
           "mem_total_gb": None, "temp_c": None, "fan_rpm": None, "thermal": None, "load": None}
    if _psutil is not None:
        try:
            out["cpu"] = round(_psutil.cpu_percent(interval=None), 1)
            out["cores"] = _psutil.cpu_count()
            vm = _psutil.virtual_memory()
            out["mem_pct"] = round(vm.percent, 1)
            out["mem_used_gb"] = round(vm.used / 1e9, 1)
            out["mem_total_gb"] = round(vm.total / 1e9, 1)
            out["load"] = round(_psutil.getloadavg()[0], 2)
        except Exception:
            pass
    sysc = (_LATEST_CONTEXT or {}).get("system") or {}
    out["thermal"] = sysc.get("thermal")
    out["temp_c"] = sysc.get("temp_c")
    out["fan_rpm"] = sysc.get("fan_rpm")
    if out["cpu"] is None and sysc.get("cpu_pct") is not None:
        out["cpu"] = sysc.get("cpu_pct")
    if out["mem_pct"] is None and sysc.get("mem_total_gb"):
        out["mem_used_gb"] = sysc.get("mem_used_gb")
        out["mem_total_gb"] = sysc.get("mem_total_gb")
        out["mem_pct"] = round(100 * sysc.get("mem_used_gb", 0) / sysc.get("mem_total_gb", 1), 1)
    return out


@app.get("/v1/onboard")
async def onboard():
    """System scan + agent recommendations for the onboarding wizard."""
    from server import secrets as secrets_mod
    sysinfo = _system_info()
    keys = secrets_mod.list_providers_with_keys()
    return {"system": sysinfo, "keys": keys, "loaded": list(agent_mod._registry.keys()),
            "recommendations": _recommendations(sysinfo, keys)}


# ── One-click local model install (onboarding) ─────────────────────────────────

@app.get("/v1/models")
async def models_list():
    """Local model catalog with download/cache state + the pick for this machine."""
    from server import models
    sysinfo = _system_info()
    return {"models": models.catalog(), "recommended": models.recommended(sysinfo.get("ram_gb")),
            "ram_gb": sysinfo.get("ram_gb"), "loaded": list(agent_mod._registry.keys())}


@app.post("/v1/models/download")
async def models_download(payload: dict):
    """Start (or resume) a background download of an MLX model's weights."""
    from server import models
    repo = (payload.get("repo") or "").strip()
    if not repo:
        return {"ok": False, "error": "repo required"}
    return {"ok": True, **models.start_download(repo)}


@app.get("/v1/models/progress")
async def models_progress(repo: str = ""):
    """Download progress for one repo: idle | downloading (pct) | done | error."""
    from server import models
    return models.progress(repo)


# ── Token/burn ledger + AIS efficiency analysis ────────────────────────────────

@app.get("/v1/ledger")
async def get_ledger(project: str = ""):
    from server import ledger
    proj = project or None
    return {"entries": list(reversed(ledger.entries(proj)))[:80], "summary": ledger.summary(proj)}


@app.get("/v1/status")
async def status():
    """Lightweight live status for the macOS menu-bar thin-line widget."""
    from server import ledger
    s = ledger.summary()
    spark = [int(e.get("tokens", 0)) for e in ledger.entries()][-24:]
    return {
        "ready": agent_mod.is_loaded("primary"),
        "model": MODEL.split("/")[-1],
        "prompts": s.get("prompts", 0),
        "total_tokens": s.get("total_tokens", 0),
        "avg_tokens": s.get("avg_tokens", 0),
        "spark": spark,
    }


_ANALYZE_SYS = (
    "You are a prompting-efficiency analyst. Given a user's recent prompts and their token "
    "costs, give EXACTLY 3 short, specific, numbered tips to get the same results with fewer "
    "tokens. Plain language, one sentence each. No code, no preamble."
)


def _do_analyze(project: str | None) -> dict:
    from server import ledger, agent as agent_mod
    s = ledger.summary(project)
    ents = ledger.entries(project)[-25:]
    if not ents:
        return {"summary": s, "tips": ["No prompts logged yet — send a few and I'll analyze your token usage."]}
    sample = "\n".join(f"- ({x.get('tokens', 0)} tok, {x.get('tools', 0)} tools) {x.get('prompt', '')}" for x in ents)
    msg = (f"Recent prompts (avg {s['avg_tokens']} tokens, {s['tool_calls']} tool calls total):\n"
           f"{sample}\n\nGive 3 efficiency tips.")
    text = ""
    for ev in agent_mod.run_agent(messages=[{"role": "user", "content": msg}], cwd=".",
                                  max_tokens=320, temperature=0.3, slot="primary",
                                  system_prompt=_ANALYZE_SYS, tools=False):
        if ev.type == "token":
            text += ev.content
    tips = [re.sub(r"^\s*\d+[.)]\s*", "", l).strip() for l in text.splitlines() if l.strip()]
    tips = [t for t in tips if len(t) > 8][:3]
    return {"summary": s, "tips": tips or [re.sub(r"\s+", " ", text)[:280]]}


@app.post("/v1/analyze")
async def analyze_endpoint(payload: dict):
    """AIS efficiency analysis — recommends how to prompt with fewer tokens."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _do_analyze, payload.get("project") or None)


# ── Artifacts: lineage-friendly recoverability (git snapshot + local backup) ───

def _art_cwd(payload: dict) -> str:
    return str(Path(payload.get("cwd") or ".").expanduser())


@app.post("/v1/artifacts/status")
async def artifacts_status(payload: dict):
    """Report git state of the working dir and any local backups."""
    import subprocess
    cwd = _art_cwd(payload)

    def git(*args):
        return subprocess.run(["git", "-C", cwd, *args],
                              capture_output=True, text=True, timeout=8)

    is_repo = False
    try:
        r = git("rev-parse", "--is-inside-work-tree")
        is_repo = r.returncode == 0 and r.stdout.strip() == "true"
    except Exception:
        is_repo = False

    dirty: dict[str, str] = {}
    branch = last_commit = None
    if is_repo:
        try:
            for ln in git("status", "--porcelain").stdout.splitlines():
                if len(ln) > 3:
                    code, p = ln[:2], ln[3:].strip()
                    dirty[p] = "untracked" if code.strip() == "??" else "modified"
            branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or None
            last_commit = git("log", "-1", "--pretty=%h %s").stdout.strip() or None
        except Exception:
            pass

    backups = []
    bdir = Path(cwd) / ".mlx-backups"
    if bdir.exists():
        backups = sorted((p.name for p in bdir.iterdir() if p.is_dir()), reverse=True)[:5]

    return {"cwd": cwd, "is_repo": is_repo, "branch": branch,
            "last_commit": last_commit, "dirty": dirty, "backups": backups}


@app.post("/v1/artifacts/snapshot")
async def artifacts_snapshot(payload: dict):
    """git add -A && commit in the working dir (optionally git init first)."""
    import subprocess
    cwd = _art_cwd(payload)
    msg = payload.get("message") or "MLX agent snapshot"
    do_init = bool(payload.get("init"))

    def git(*args):
        return subprocess.run(["git", "-C", cwd, *args],
                              capture_output=True, text=True, timeout=25)

    r = git("rev-parse", "--is-inside-work-tree")
    if not (r.returncode == 0 and r.stdout.strip() == "true"):
        if not do_init:
            return {"ok": False, "reason": "not a git repository", "can_init": True}
        gi = git("init")
        if gi.returncode != 0:
            raise HTTPException(400, f"git init failed: {gi.stderr.strip()}")

    add = git("add", "-A")
    if add.returncode != 0:
        raise HTTPException(400, f"git add failed: {add.stderr.strip()}")
    cm = git("commit", "-m", msg)
    if cm.returncode != 0:
        blob = (cm.stdout + cm.stderr).lower()
        if "nothing to commit" in blob:
            return {"ok": True, "nothing": True}
        raise HTTPException(400, f"git commit failed: {cm.stderr.strip() or cm.stdout.strip()}")
    return {"ok": True, "commit": git("rev-parse", "--short", "HEAD").stdout.strip(), "message": msg}


@app.post("/v1/artifacts/backup")
async def artifacts_backup(payload: dict):
    """Copy the given files into .mlx-backups/<timestamp>/ preserving structure."""
    import shutil, time
    cwd = Path(_art_cwd(payload))
    paths = payload.get("paths") or []
    dest = cwd / ".mlx-backups" / time.strftime("%Y%m%d-%H%M%S")
    copied = []
    for rel in paths:
        try:
            src = cwd / rel
            if src.is_file() and src.resolve().is_relative_to(cwd.resolve()):
                d = dest / rel
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, d)
                copied.append(rel)
        except Exception:
            pass
    if not copied:
        return {"ok": False, "reason": "no files to copy"}
    return {"ok": True, "dir": str(dest), "count": len(copied), "files": copied}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT)
