"""
Scroll — CLI REPL
Usage: uv run python cli.py [--cwd PATH] [--model MODEL]
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import click
import httpx
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from rich.theme import Theme

THEME = Theme(
    {
        "tool.name": "bold #5E5CEB",
        "tool.arg": "#888898",
        "tool.result": "#4CAF50",
        "tool.error": "bold red",
        "prompt.user": "bold #E8E8F0",
        "prompt.arrow": "#5E5CEB",
        "info": "#555566",
        # Dual-agent phases
        "phase.primary":  "bold #5E5CEB",   # purple
        "phase.critic":   "bold #E09B3D",   # amber
        "phase.revision": "bold #4CAF50",   # green
        "verdict.lgtm":   "bold #4CAF50",
        "verdict.issues": "bold #E09B3D",
        "verdict.critical": "bold red",
    }
)

PHASE_COLORS = {
    "primary":  ("#5E5CEB", "ARCHITECT"),
    "critic":   ("#E09B3D", "CRITIC"),
    "revision": ("#4CAF50", "REVISION"),
}

VERDICT_STYLES = {
    "LGTM":     ("verdict.lgtm",     "✓ LGTM — no issues found"),
    "ISSUES":   ("verdict.issues",   "! ISSUES — revision pass starting"),
    "CRITICAL": ("verdict.critical", "✗ CRITICAL — revision pass starting"),
}

console = Console(theme=THEME, highlight=False)

BASE_URL = f"http://127.0.0.1:{os.environ.get('MLX_PORT', '8080')}"


def _detect_language(text: str) -> str | None:
    """Guess language for syntax highlighting from a fenced code block."""
    if text.startswith("```"):
        lang = text[3 : text.find("\n")].strip().lower()
        return lang or None
    return None


def _render_event(evt: dict, full_text_ref: list[str]) -> None:
    """Render a single AgentEvent dict to the console. full_text_ref is a 1-item list used as mutable ref."""
    t = evt.get("type")

    if t == "phase":
        name = evt.get("name", "")
        color, label = PHASE_COLORS.get(name, ("#888", name.upper()))
        console.print(f"\n[{color}]── {label} ──────────────────────────────────[/]")

    elif t == "verdict":
        v = evt.get("verdict", "ISSUES")
        style, msg = VERDICT_STYLES.get(v, ("info", v))
        console.print(f"\n[{style}]{msg}[/]")

    elif t == "token":
        tok = evt["content"]
        full_text_ref[0] += tok
        console.print(tok, end="", markup=False)

    elif t == "tool_call":
        console.print()
        name = evt["name"]
        args_str = json.dumps(evt["arguments"], indent=2)
        console.print(Panel(
            Syntax(args_str, "json", theme="monokai", line_numbers=False),
            title=f"[tool.name]⬡ {name}[/]",
            border_style="#2A2A40",
            padding=(0, 1),
        ))

    elif t == "tool_result":
        output = evt["output"]
        is_err = evt.get("error", False)
        style = "tool.error" if is_err else "tool.result"
        lines = output.splitlines()
        preview = "\n".join(lines[:40])
        if len(lines) > 40:
            preview += f"\n… ({len(lines)} lines total)"
        console.print(Panel(
            Text(preview, style=style, overflow="fold"),
            title=f"[{style}]{'✗' if is_err else '✓'} {evt['name']}[/]",
            border_style="#1A1A28",
            padding=(0, 1),
        ))

    elif t == "error":
        console.print(f"\n[tool.error]Agent error: {evt['message']}[/]")

    elif t == "done":
        tokens = evt.get("total_tokens", 0)
        console.print(f"\n[info]─── {tokens} tokens ───[/]")


async def stream_response(
    messages: list[dict],
    cwd: str,
    max_tokens: int,
    temperature: float,
    endpoint: str = "/v1/agent",
) -> str:
    """Stream agent events from server, render to console, return full assistant text."""
    extra: dict = {}
    if endpoint == "/v1/dual":
        extra = {"revision": True}

    payload = {
        "messages": messages,
        "cwd": cwd,
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": temperature,
        **extra,
    }

    full_text_ref = [""]

    async with httpx.AsyncClient(timeout=None) as client:
        try:
            async with client.stream(
                "POST", f"{BASE_URL}{endpoint}", json=payload
            ) as resp:
                if resp.status_code != 200:
                    console.print(f"[tool.error]Server error: HTTP {resp.status_code}[/]")
                    return ""

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        evt = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    _render_event(evt, full_text_ref)

        except httpx.ConnectError:
            console.print(
                "[tool.error]Cannot connect to server. "
                f"Start it with: uv run uvicorn server.main:app --port {os.environ.get('MLX_PORT', '8080')}[/]"
            )

    console.print()
    return full_text_ref[0]


def check_server() -> bool:
    try:
        r = httpx.get(f"{BASE_URL}/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


@click.group(invoke_without_command=True)
@click.option("--cwd", default=".", help="Working directory for tool execution")
@click.option("--max-tokens", default=8192, help="Max tokens per turn")
@click.option("--temperature", default=0.15, help="Sampling temperature")
@click.pass_context
def main(ctx, cwd: str, max_tokens: int, temperature: float):
    """Scroll — 100% local inference on Apple Silicon.

    Run with no subcommand for the chat REPL, or use `key` to manage cloud
    API keys, e.g.  python cli.py key set anthropic
    """
    if ctx.invoked_subcommand is not None:
        return
    cwd = str(Path(cwd).resolve())

    console.print(
        Panel(
            "[bold #5E5CEB]Scroll[/]\n"
            f"[info]model  {os.environ.get('MLX_MODEL', 'Qwen2.5-Coder-32B-Instruct-4bit')}[/]\n"
            f"[info]critic {os.environ.get('MLX_CRITIC_MODEL', 'Qwen2.5-Coder-7B-Instruct-4bit')}[/]\n"
            f"[info]cwd    {cwd}[/]\n"
            "[info]/clear  /cwd <path>  /model  /dual <msg>  /quit[/]",
            border_style="#2A2A40",
            padding=(0, 1),
        )
    )

    if not check_server():
        console.print(
            "[tool.error]Server not running. In another terminal:[/]\n"
            f"  [bold]cd {Path(__file__).parent} && uv run uvicorn server.main:app --port "
            f"{os.environ.get('MLX_PORT', '8080')}[/]\n"
        )

    messages: list[dict] = []

    while True:
        try:
            console.print("[prompt.arrow]▸[/] ", end="")
            user_input = input().strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[info]Goodbye.[/]")
            break

        if not user_input:
            continue

        # Slash commands
        if user_input == "/quit" or user_input == "/exit":
            break
        if user_input == "/clear":
            messages = []
            console.clear()
            continue
        if user_input.startswith("/cwd "):
            new_cwd = user_input[5:].strip()
            cwd = str(Path(new_cwd).expanduser().resolve())
            console.print(f"[info]cwd → {cwd}[/]")
            continue
        if user_input == "/model":
            try:
                r = httpx.get(f"{BASE_URL}/v1/model", timeout=2)
                console.print(f"[info]{r.json()}[/]")
            except Exception:
                console.print("[tool.error]Server unavailable[/]")
            continue

        # /dual <message> — runs through the 32B → 7B critic → 32B revision pipeline
        if user_input.startswith("/dual "):
            dual_text = user_input[6:].strip()
            if not dual_text:
                console.print("[info]Usage: /dual <your task>[/]")
                continue
            console.print(
                f"\n[phase.primary]Dual-agent mode[/] [info](32B → critic 7B → revision)[/]\n"
            )
            dual_msgs = list(messages) + [{"role": "user", "content": dual_text}]
            assistant_text = asyncio.run(
                stream_response(dual_msgs, cwd, max_tokens, temperature, endpoint="/v1/dual")
            )
            if assistant_text:
                messages.append({"role": "user", "content": dual_text})
                messages.append({"role": "assistant", "content": assistant_text})
            continue

        messages.append({"role": "user", "content": user_input})

        assistant_text = asyncio.run(
            stream_response(messages, cwd, max_tokens, temperature)
        )

        if assistant_text:
            messages.append({"role": "assistant", "content": assistant_text})


# ── Secure API-key management ──────────────────────────────────────────────────
@main.group()
def key():
    """Manage cloud-provider API keys (stored in the macOS Keychain)."""


@key.command("set")
@click.argument("provider", type=click.Choice(["anthropic", "openai", "gemini", "groq", "openrouter", "cerebras"]))
def key_set(provider: str):
    """Securely store a key, e.g.  python cli.py key set anthropic

    The key is read from a hidden prompt and written to the macOS Keychain —
    never echoed, never saved to a plaintext config, never sent to the browser.
    """
    import getpass
    from server import secrets

    val = getpass.getpass(f"Paste your {provider} API key (input hidden): ").strip()
    if not val:
        console.print("[tool.error]No key entered — nothing saved.[/]")
        return
    try:
        secrets.set_key(provider, val)
    except Exception as exc:
        console.print(f"[tool.error]Could not store key: {exc}[/]")
        return
    where = "the macOS Keychain" if secrets._IS_MAC else str(secrets._FILE)
    console.print(f"[tool.result]✓ {provider} key stored in {where}.[/]")
    console.print("[info]It loads automatically next time the server starts.[/]")


@key.command("list")
def key_list():
    """Show which providers have a key set."""
    from server import secrets
    for p, has in secrets.list_providers_with_keys().items():
        mark = "[tool.result]✓ set[/]" if has else "[info]— not set[/]"
        console.print(f"  {p:<10} {mark}")


@key.command("rm")
@click.argument("provider", type=click.Choice(["anthropic", "openai", "gemini", "groq", "openrouter", "cerebras"]))
def key_rm(provider: str):
    """Remove a stored key."""
    from server import secrets
    secrets.delete_key(provider)
    console.print(f"[info]Removed {provider} key.[/]")


if __name__ == "__main__":
    main()
