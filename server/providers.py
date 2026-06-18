"""
LLM provider abstraction — route inference to MLX (local), OpenAI, or Anthropic.

Usage:
    from server.providers import get_active, set_active, list_providers

The agent loop in agent.py always uses the MLX slot directly; this module adds
a second path for providers that don't need the full tool-call loop (chat-only
or when you wire tool use yourself via the /v1/chat endpoint).
"""
from __future__ import annotations

import os
from typing import Generator

from server.schemas import AgentEvent, DoneEvent, ErrorEvent, TokenEvent


# ── Base class ────────────────────────────────────────────────────────────────

class Provider:
    id: str = ""
    display_name: str = ""

    def available(self) -> bool:  # noqa: D401
        raise NotImplementedError

    def stream(
        self,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        tools: list[dict] | None = None,
    ) -> Generator[AgentEvent, None, None]:
        raise NotImplementedError


# ── MLX (local Apple Silicon) ────────────────────────────────────────────────

class MLXProvider(Provider):
    id = "mlx"
    display_name = "MLX (local)"

    def __init__(self, slot: str = "primary") -> None:
        self.slot = slot

    def available(self) -> bool:
        from server import agent as ag
        return ag.is_loaded(self.slot)

    def stream(self, messages, max_tokens, temperature, tools=None):
        from server import agent as ag
        yield from ag.run_agent(
            messages=messages,
            cwd=".",
            max_tokens=max_tokens,
            temperature=temperature,
            slot=self.slot,
            tools=tools is not None,
        )


# ── OpenAI ────────────────────────────────────────────────────────────────────

class OpenAIProvider(Provider):
    id = "openai"
    display_name = "OpenAI"

    def __init__(self, model: str = "gpt-4o") -> None:
        self.model = model

    def available(self) -> bool:
        return bool(os.environ.get("OPENAI_API_KEY"))

    def stream(self, messages, max_tokens, temperature, tools=None):
        try:
            import openai  # noqa: PLC0415
        except ImportError:
            yield ErrorEvent(message="openai not installed — run: uv add openai")
            return

        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            yield ErrorEvent(message="OPENAI_API_KEY env var not set")
            return

        client = openai.OpenAI(api_key=api_key)
        kwargs: dict = dict(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
        )
        if tools:
            kwargs["tools"] = tools

        total = 0
        try:
            for chunk in client.chat.completions.create(**kwargs):
                delta = chunk.choices[0].delta
                if delta.content:
                    total += 1
                    yield TokenEvent(content=delta.content)
        except Exception as exc:
            yield ErrorEvent(message=f"OpenAI error: {exc}")
            return

        yield DoneEvent(total_tokens=total)


# ── Anthropic ─────────────────────────────────────────────────────────────────

class AnthropicProvider(Provider):
    id = "anthropic"
    display_name = "Anthropic"

    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        self.model = model

    def available(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    def stream(self, messages, max_tokens, temperature, tools=None):
        try:
            import anthropic  # noqa: PLC0415
        except ImportError:
            yield ErrorEvent(message="anthropic not installed — run: uv add anthropic")
            return

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            yield ErrorEvent(message="ANTHROPIC_API_KEY env var not set")
            return

        client = anthropic.Anthropic(api_key=api_key)

        # Anthropic separates system prompt from message list
        system = ""
        msgs: list[dict] = []
        for m in messages:
            if m["role"] == "system":
                system = m.get("content", "")
            else:
                msgs.append({"role": m["role"], "content": m.get("content", "")})

        kwargs: dict = dict(model=self.model, max_tokens=max_tokens, messages=msgs)
        if system:
            kwargs["system"] = system
        if tools:
            # Convert OpenAI-style tool defs to Anthropic format
            kwargs["tools"] = [
                {
                    "name": t.get("function", t).get("name", ""),
                    "description": t.get("function", t).get("description", ""),
                    "input_schema": t.get("function", t).get("parameters", {"type": "object", "properties": {}}),
                }
                for t in tools
            ]

        total = 0
        try:
            with client.messages.stream(**kwargs) as s:
                for text in s.text_stream:
                    total += 1
                    yield TokenEvent(content=text)
        except Exception as exc:
            yield ErrorEvent(message=f"Anthropic error: {exc}")
            return

        yield DoneEvent(total_tokens=total)


# ── Gemini ────────────────────────────────────────────────────────────────────

class GeminiProvider(Provider):
    id = "gemini"
    display_name = "Gemini"

    def __init__(self, model: str = "gemini-2.0-flash") -> None:
        self.model = model

    def available(self) -> bool:
        return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))

    def stream(self, messages, max_tokens, temperature, tools=None):
        try:
            from google import genai  # noqa: PLC0415
            from google.genai import types  # noqa: PLC0415
        except ImportError:
            yield ErrorEvent(message="google-genai not installed — run: uv add google-genai")
            return
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
        if not key:
            yield ErrorEvent(message="GEMINI_API_KEY not set")
            return
        client = genai.Client(api_key=key)
        parts = []
        for m in messages:
            role = m["role"]
            prefix = "System: " if role == "system" else ("User: " if role == "user" else "Assistant: ")
            parts.append(prefix + m.get("content", ""))
        total = 0
        try:
            for chunk in client.models.generate_content_stream(
                model=self.model, contents="\n\n".join(parts),
                config=types.GenerateContentConfig(max_output_tokens=max_tokens, temperature=temperature),
            ):
                if getattr(chunk, "text", None):
                    total += 1
                    yield TokenEvent(content=chunk.text)
        except Exception as exc:
            yield ErrorEvent(message=f"Gemini error: {exc}")
            return
        yield DoneEvent(total_tokens=total)


# ── OpenAI-compatible hosts (free / open-source model hosts) ───────────────────

class OpenAICompatProvider(Provider):
    """Any OpenAI-compatible endpoint — Groq, OpenRouter, Cerebras, Together, etc."""

    def __init__(self, id: str, display_name: str, base_url: str, env_var: str, model: str) -> None:
        self.id = id
        self.display_name = display_name
        self.base_url = base_url
        self.env_var = env_var
        self.model = model

    def available(self) -> bool:
        return bool(os.environ.get(self.env_var))

    def stream(self, messages, max_tokens, temperature, tools=None):
        try:
            import openai  # noqa: PLC0415
        except ImportError:
            yield ErrorEvent(message="openai SDK not installed — run: uv add openai")
            return
        key = os.environ.get(self.env_var, "")
        if not key:
            yield ErrorEvent(message=f"{self.env_var} not set")
            return
        client = openai.OpenAI(api_key=key, base_url=self.base_url)
        total = 0
        try:
            for chunk in client.chat.completions.create(
                model=self.model, messages=messages,
                max_tokens=max_tokens, temperature=temperature, stream=True,
            ):
                delta = chunk.choices[0].delta
                if delta.content:
                    total += 1
                    yield TokenEvent(content=delta.content)
        except Exception as exc:
            yield ErrorEvent(message=f"{self.display_name} error: {exc}")
            return
        yield DoneEvent(total_tokens=total)


# ── redacted (EPAM) — Claude via the corporate CLI/SSO, no API key ──────────────

class redactedProvider(Provider):
    """Routes Claude (Opus) through EPAM redacted's `redacted-cli` CLI in print mode.
    Auth is the CLI's own SSO session (no API key); spending goes to the corporate budget.
    Runs in a throwaway working dir so an escalation answer can't touch the user's files —
    the code it needs is already in the prompt."""
    id = "redacted"
    display_name = "redacted · Claude"

    def __init__(self, model: str = "claude-opus (redacted)") -> None:
        self.model = model

    @staticmethod
    def _bin() -> str | None:
        import shutil
        from server import env as env_mod
        return shutil.which("redacted-cli", path=env_mod.login_path()) or shutil.which("redacted-cli")

    def available(self) -> bool:
        return self._bin() is not None

    def stream(self, messages, max_tokens, temperature, tools=None):
        import subprocess, tempfile, os, select, time
        binp = self._bin()
        if not binp:
            yield ErrorEvent(message="redacted CLI not found — install it (npm i -g @redacted/code; redacted install claude) then sign in (redacted profile login)")
            return
        parts = []
        for m in messages:
            role, c = m.get("role", "user"), (m.get("content") or "").strip()
            if not c:
                continue
            parts.append(c if role in ("system", "user") else f"Assistant (earlier): {c}")
        prompt = "\n\n".join(parts) or "Respond."
        from server import env as env_mod
        # Stream the CLI's stdout as it's produced (os.read on the fd → whatever is available),
        # so token burn updates in REAL TIME instead of spiking once at the end.
        total = 0
        try:
            with tempfile.TemporaryDirectory() as td:
                proc = subprocess.Popen([binp, "-p", prompt], cwd=td,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        env=env_mod.login_env())
                fd = proc.stdout.fileno()
                deadline = time.monotonic() + 600
                while True:
                    if time.monotonic() > deadline:
                        proc.kill()
                        yield ErrorEvent(message="redacted timed out after 600s")
                        return
                    rl, _, _ = select.select([fd], [], [], 0.5)
                    if rl:
                        chunk = os.read(fd, 4096)
                        if not chunk:
                            break
                        total += len(chunk)
                        yield TokenEvent(content=chunk.decode("utf-8", "replace"))
                    elif proc.poll() is not None:
                        break
                rest = proc.stdout.read()
                if rest:
                    total += len(rest)
                    yield TokenEvent(content=rest.decode("utf-8", "replace"))
                rc = proc.wait()
        except Exception as exc:
            yield ErrorEvent(message=f"redacted error: {exc}")
            return
        if rc != 0 and total == 0:
            err = ""
            try:
                err = (proc.stderr.read() or b"").decode("utf-8", "replace").strip()[:400]
            except Exception:
                pass
            yield ErrorEvent(message="redacted failed: " + (err or "non-zero exit") +
                             " — your SSO may have expired (run: redacted profile login)")
            return
        yield DoneEvent(total_tokens=max(1, total // 4))


# ── Registry ──────────────────────────────────────────────────────────────────

_REGISTRY: dict[str, Provider] = {
    "redacted":    redactedProvider(),
    "mlx":        MLXProvider(),
    "openai":     OpenAIProvider(),
    "anthropic":  AnthropicProvider(),
    "gemini":     GeminiProvider(),
    # Free / generous-tier hosts of open-source models (OpenAI-compatible APIs):
    "groq":       OpenAICompatProvider("groq", "Groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", "llama-3.3-70b-versatile"),
    "openrouter": OpenAICompatProvider("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "meta-llama/llama-3.3-70b-instruct:free"),
    "cerebras":   OpenAICompatProvider("cerebras", "Cerebras", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", "llama-3.3-70b"),
}
_active: str = "mlx"


def get_provider(name: str) -> Provider:
    if name not in _REGISTRY:
        raise ValueError(f"Unknown provider {name!r}. Available: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def get_active() -> Provider:
    return _REGISTRY[_active]


def set_active(name: str) -> None:
    global _active  # noqa: PLW0603
    get_provider(name)  # validate
    _active = name


def register_provider(name: str, provider: Provider) -> None:
    _REGISTRY[name] = provider


def list_providers() -> list[dict]:
    return [
        {
            "id": p.id,
            "display_name": p.display_name,
            "available": p.available(),
            "active": name == _active,
        }
        for name, p in _REGISTRY.items()
    ]
