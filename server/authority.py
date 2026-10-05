"""
AGMO authority model (AEM Chapter 3) — classifies what an agent may DO (action
authority), distinct from what context it may see (see ACCESS_POLICY in
server/main.py, which governs context, not actions).

Authority gates the tool set an agent may call. Levels are inclusive (a higher
level includes every lower-level tool). Deny-by-default: a tool not listed in ANY
level is authorized nowhere, so a newly registered tool must be explicitly
classified before any agent can call it.

Defaults (overridable per run via the `agmo` kwarg):
- Local MLX = A4 (read + write + shell) — preserves the current local agent's
  behavior, which legitimately runs tests/builds/git.
- Cloud providers = A2 (read-only) — so wiring a cloud model into the tool loop
  can never, by default, hand it write or shell access. Raising a cloud agent to
  A3/A4 is an explicit, recorded human decision (the AEM "critical asymmetry").
- A5 is reserved and never a default.
"""
from __future__ import annotations

_READ = {"read_file", "list_dir", "grep_codebase", "find_files", "fetch_url", "read_data", "read_pdf"}
_WRITE = _READ | {"write_file", "edit_file", "append_file"}
_SHELL = _WRITE | {"run_command"}

_LEVELS: dict[str, set[str]] = {
    "A1": set(),     # advisory — no tools
    "A2": _READ,     # read-only
    "A3": _WRITE,    # + write (gated by review-before-apply in the UI)
    "A4": _SHELL,    # + shell (per-command approval)
    "A5": _SHELL,    # full — reserved; never a default, never cloud
}

CLOUD_DEFAULT = "A2"

DEFAULT_BY_PROVIDER = {
    "mlx": "A4",   # local workhorse — current behavior preserved
    "openai": "A2", "anthropic": "A2", "gemini": "A2",
    "groq": "A2", "openrouter": "A2", "cerebras": "A2",
}


def normalize(level: str | None) -> str:
    l = (level or "").strip().upper()
    return l if l in _LEVELS else CLOUD_DEFAULT


def allowed(level: str | None, tool: str) -> bool:
    """True if `tool` is authorized at `level` (deny-by-default for unknown tools)."""
    return tool in _LEVELS.get(normalize(level), set())


def default_for(provider: str | None) -> str:
    return DEFAULT_BY_PROVIDER.get((provider or "").lower(), CLOUD_DEFAULT)


def allowlist(level: str | None) -> set[str]:
    return set(_LEVELS.get(normalize(level), set()))
