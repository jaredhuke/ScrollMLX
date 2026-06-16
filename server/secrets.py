"""
Secure local storage for cloud-provider API keys.

On macOS the key lives in the login Keychain (via `security`), never in a
plaintext file. On other platforms it falls back to ~/.config/scroll/keys.json
locked to 0600. The server calls `load_into_env()` at startup so providers pick
the key up through their normal `*_API_KEY` env vars — the key is never logged
and never sent to the browser.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

SERVICE = "scroll"
# provider id -> the env var that provider's SDK reads
ENV_VARS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    # free / generous-tier hosts of open-source models
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
}
_FILE = Path.home() / ".config" / "scroll" / "keys.json"
_IS_MAC = sys.platform == "darwin"


# ── macOS Keychain ────────────────────────────────────────────────────────────
def _kc_set(account: str, secret: str) -> None:
    subprocess.run(
        ["security", "add-generic-password", "-U", "-s", SERVICE, "-a", account, "-w", secret],
        check=True, capture_output=True, text=True,
    )


def _kc_get(account: str) -> str | None:
    r = subprocess.run(
        ["security", "find-generic-password", "-s", SERVICE, "-a", account, "-w"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def _kc_del(account: str) -> None:
    subprocess.run(
        ["security", "delete-generic-password", "-s", SERVICE, "-a", account],
        capture_output=True, text=True,
    )


# ── File fallback (non-macOS) ─────────────────────────────────────────────────
def _file_load() -> dict:
    if _FILE.exists():
        try:
            return json.loads(_FILE.read_text())
        except Exception:
            return {}
    return {}


def _file_save(d: dict) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(d))
    os.chmod(_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0600


# ── Public API ────────────────────────────────────────────────────────────────
def set_key(provider: str, secret: str) -> None:
    provider = provider.lower()
    if _IS_MAC:
        _kc_set(provider, secret)
    else:
        d = _file_load(); d[provider] = secret; _file_save(d)


def get_key(provider: str) -> str | None:
    provider = provider.lower()
    return _kc_get(provider) if _IS_MAC else _file_load().get(provider)


def delete_key(provider: str) -> None:
    provider = provider.lower()
    if _IS_MAC:
        _kc_del(provider)
    else:
        d = _file_load(); d.pop(provider, None); _file_save(d)


def list_providers_with_keys() -> dict[str, bool]:
    return {p: bool(get_key(p)) for p in ENV_VARS}


def load_into_env() -> list[str]:
    """Populate *_API_KEY env vars from stored keys if not already set."""
    loaded = []
    for prov, var in ENV_VARS.items():
        if not os.environ.get(var):
            k = get_key(prov)
            if k:
                os.environ[var] = k
                loaded.append(prov)
    return loaded
