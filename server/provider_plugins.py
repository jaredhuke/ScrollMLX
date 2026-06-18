"""
Provider plugins — add a cloud model to Scroll by DROPPING A MARKDOWN FILE (+ a password).

A plugin is a single .md with a small frontmatter block. Two kinds:

    ---
    type: provider
    id: redacted
    name: redacted · Claude Opus
    kind: command
    command: redacted-cli -p {prompt}
    secret_env: redacted_TOKEN        # optional — the "password"; injected into the env
    ---
    Docs / notes for humans.

    ---
    type: provider
    id: myapi
    name: My API · GPT
    kind: http
    endpoint: https://api.example.com/v1/chat/completions
    model: gpt-4o
    secret_env: MYAPI_KEY            # sent as  Authorization: Bearer <secret>
    ---

Files live in ~/.scroll/provider-plugins/*.md. The secret is stored in the OS keychain
(via server.secrets), never in the .md. Each plugin registers itself as a provider so it
shows up everywhere a built-in provider does (operatives, model picker, @target, escalate).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

from server.schemas import AgentEvent, DoneEvent, ErrorEvent, TokenEvent

_DIR = Path.home() / ".scroll" / "provider-plugins"
# A curated, bundled library of agentic options shipped AS .md plugin files (GLM + open-source
# models, etc.). One-click install copies them into _DIR — every operative is just a markdown file.
_LIB = Path(__file__).resolve().parent.parent / "docs" / "provider-plugins" / "library"


def _parse(text: str) -> dict | None:
    m = re.match(r"^\s*---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return None
    spec, body = {}, m.group(1)
    for line in body.splitlines():
        if ":" not in line or line.strip().startswith("#"):
            continue
        k, v = line.split(":", 1)
        spec[k.strip().lower()] = v.strip()
    if spec.get("type") != "provider" or not spec.get("id"):
        return None
    spec["doc"] = (m.group(2) or "").strip()
    return spec


def load() -> list[dict]:
    _DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(_DIR.glob("*.md")):
        try:
            spec = _parse(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            spec = None
        if spec:
            spec["_file"] = str(p)
            out.append(spec)
    return out


def _secret(spec: dict) -> str:
    env = spec.get("secret_env")
    if not env:
        return ""
    if os.environ.get(env):
        return os.environ[env]
    try:
        from server import secrets as secrets_mod
        return secrets_mod.get_key(spec["id"]) or ""   # stored under the plugin id
    except Exception:
        return ""


class _CommandPlugin:
    def __init__(self, spec):
        self.spec = spec; self.id = spec["id"]; self.display_name = spec.get("name", spec["id"])
        self.model = spec.get("model", spec.get("name", spec["id"]))

    def available(self):
        cmd = self.spec.get("command", "")
        if not cmd:
            return False
        import shutil
        bin0 = shlex.split(cmd)[0] if cmd else ""
        if shutil.which(bin0) is None:
            return False
        return True if not self.spec.get("secret_env") else bool(_secret(self.spec))

    def stream(self, messages, max_tokens, temperature, tools=None):
        prompt = "\n\n".join((m.get("content") or "").strip() for m in messages if (m.get("content") or "").strip())
        cmd = self.spec.get("command", "")
        try:
            argv = [a.replace("{prompt}", prompt) for a in shlex.split(cmd)]
            if "{prompt}" not in cmd:
                argv.append(prompt)
            env = dict(os.environ)
            sec = _secret(self.spec)
            if self.spec.get("secret_env") and sec:
                env[self.spec["secret_env"]] = sec
            with tempfile.TemporaryDirectory() as td:
                r = subprocess.run(argv, cwd=td, capture_output=True, text=True, timeout=600, env=env)
        except subprocess.TimeoutExpired:
            yield ErrorEvent(message=f"{self.display_name} timed out (600s)"); return
        except Exception as exc:
            yield ErrorEvent(message=f"{self.display_name} error: {exc}"); return
        out = (r.stdout or "").strip()
        if r.returncode != 0 and not out:
            yield ErrorEvent(message=f"{self.display_name} failed: {(r.stderr or 'non-zero exit').strip()[:300]}"); return
        for i in range(0, len(out), 400):
            yield TokenEvent(content=out[i:i + 400])
        yield DoneEvent(total_tokens=max(1, len(out) // 4))


class _HttpPlugin:
    def __init__(self, spec):
        self.spec = spec; self.id = spec["id"]; self.display_name = spec.get("name", spec["id"])
        self.model = spec.get("model", "")

    def available(self):
        return bool(self.spec.get("endpoint")) and (not self.spec.get("secret_env") or bool(_secret(self.spec)))

    def stream(self, messages, max_tokens, temperature, tools=None):
        import urllib.request
        sec = _secret(self.spec)
        body = json.dumps({"model": self.spec.get("model", ""), "max_tokens": max_tokens,
                           "temperature": temperature,
                           "messages": [{"role": m.get("role", "user"), "content": m.get("content", "")} for m in messages]}).encode()
        headers = {"Content-Type": "application/json"}
        if sec:
            headers["Authorization"] = "Bearer " + sec
        try:
            req = urllib.request.Request(self.spec["endpoint"], data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as exc:
            yield ErrorEvent(message=f"{self.display_name} error: {exc}"); return
        txt = ""
        try:
            txt = data["choices"][0]["message"]["content"]
        except Exception:
            txt = json.dumps(data)[:2000]
        for i in range(0, len(txt), 400):
            yield TokenEvent(content=txt[i:i + 400])
        yield DoneEvent(total_tokens=max(1, len(txt) // 4))


def register_all() -> list[dict]:
    """(Re)register every plugin as a provider. Returns the public list for the UI."""
    from server import providers
    pub = []
    for spec in load():
        prov = _HttpPlugin(spec) if spec.get("kind") == "http" else _CommandPlugin(spec)
        try:
            providers.register_provider(spec["id"], prov)
            pub.append({"id": spec["id"], "name": prov.display_name, "kind": spec.get("kind", "command"),
                        "available": prov.available(), "needs_secret": bool(spec.get("secret_env")),
                        "secret_set": bool(_secret(spec)), "file": spec.get("_file", "")})
        except Exception:
            pass
    return pub


def _slug(pid: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", (pid or "").lower())


def library() -> list[dict]:
    """The bundled model library (every option is a .md plugin), with an `installed` flag."""
    installed = {p.stem for p in _DIR.glob("*.md")} if _DIR.exists() else set()
    items = []
    if _LIB.is_dir():
        for p in sorted(_LIB.glob("*.md")):
            spec = _parse(p.read_text(encoding="utf-8", errors="replace"))
            if not spec:
                continue
            items.append({
                "id": spec["id"], "name": spec.get("name", spec["id"]),
                "kind": spec.get("kind", "command"), "model": spec.get("model", ""),
                "needs_secret": bool(spec.get("secret_env")),
                "doc": (spec.get("doc", "").strip()[:220]),
                "installed": _slug(spec["id"]) in installed,
            })
    return items


def install(plugin_id: str) -> dict:
    """Install a bundled library model by id (copies its .md into _DIR + registers it)."""
    if not _LIB.is_dir():
        return {"ok": False, "error": "no library bundled"}
    for p in _LIB.glob("*.md"):
        spec = _parse(p.read_text(encoding="utf-8", errors="replace"))
        if spec and spec["id"] == plugin_id:
            return import_path(str(p))
    return {"ok": False, "error": f"unknown library id {plugin_id!r}"}


def import_path(raw: str) -> dict:
    p = Path(raw or "").expanduser()
    if not p.exists() or p.suffix.lower() not in (".md", ".markdown"):
        return {"ok": False, "error": "point at a .md plugin file"}
    spec = _parse(p.read_text(encoding="utf-8", errors="replace"))
    if not spec:
        return {"ok": False, "error": "not a valid provider plugin (need frontmatter with type: provider, id, kind)"}
    _DIR.mkdir(parents=True, exist_ok=True)
    dest = _DIR / (re.sub(r"[^a-z0-9._-]+", "-", spec["id"].lower()) + ".md")
    dest.write_text(p.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    register_all()
    return {"ok": True, "id": spec["id"], "name": spec.get("name", spec["id"]), "needs_secret": bool(spec.get("secret_env"))}


def set_secret(plugin_id: str, secret: str) -> dict:
    try:
        from server import secrets as secrets_mod
        secrets_mod.set_key(plugin_id, secret)
        register_all()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
