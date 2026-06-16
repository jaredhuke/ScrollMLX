"""
Dynamic tool registry — manage built-ins, extensions, and connectors at runtime.

Patches server.tools.TOOL_REGISTRY and TOOL_DEFINITIONS in-place so agent.py
picks up changes automatically without restart.

Extension file contract:
    TOOLS = [
        ("tool_name", callable, schema_dict),
        ...
    ]
    where schema_dict is an OpenAI function-calling dict:
    {
        "type": "function",
        "function": {
            "name": "...", "description": "...",
            "parameters": {"type": "object", "properties": {...}}
        }
    }

Connector types:
    - "mcp"    — MCP-compatible HTTP server
    - "openai" — OpenAI-compatible tools endpoint
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Callable

from server.tools import TOOL_DEFINITIONS, TOOL_REGISTRY

# ── Internal entry ────────────────────────────────────────────────────────────

class _Entry:
    __slots__ = ("name", "fn", "schema", "source", "enabled")

    def __init__(self, name: str, fn: Callable, schema: dict, source: str) -> None:
        self.name = name
        self.fn = fn
        self.schema = schema
        self.source = source
        self.enabled = True


# ── ToolManager ───────────────────────────────────────────────────────────────

class ToolManager:
    """
    Runtime registry wrapping the live TOOL_REGISTRY dict and TOOL_DEFINITIONS list.
    All changes immediately take effect for the running agent loop.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        # Seed from built-ins
        for name, fn in list(TOOL_REGISTRY.items()):
            schema = self._find_schema(name)
            self._entries[name] = _Entry(name, fn, schema, "builtin")

    # ── Schema lookup ─────────────────────────────────────────────────────────

    @staticmethod
    def _find_schema(name: str) -> dict:
        for s in TOOL_DEFINITIONS:
            fn_block = s.get("function", s)
            if fn_block.get("name") == name:
                return s
        return {
            "type": "function",
            "function": {"name": name, "description": f"Built-in tool: {name}", "parameters": {}},
        }

    # ── Core CRUD ─────────────────────────────────────────────────────────────

    def register(self, name: str, fn: Callable, schema: dict, source: str = "extension") -> None:
        """Add or replace a tool. Immediately visible to the agent loop."""
        entry = _Entry(name, fn, schema, source)
        self._entries[name] = entry
        TOOL_REGISTRY[name] = fn
        # Replace schema in definitions list if already present
        for i, s in enumerate(TOOL_DEFINITIONS):
            if s.get("function", s).get("name") == name:
                TOOL_DEFINITIONS[i] = schema
                return
        TOOL_DEFINITIONS.append(schema)

    def unregister(self, name: str) -> None:
        """Remove a non-builtin tool."""
        entry = self._entries.get(name)
        if not entry:
            raise KeyError(f"Tool {name!r} not found")
        if entry.source == "builtin":
            raise ValueError(f"Cannot unregister built-in {name!r} — use disable() instead")
        del self._entries[name]
        TOOL_REGISTRY.pop(name, None)
        for i, s in enumerate(TOOL_DEFINITIONS):
            if s.get("function", s).get("name") == name:
                TOOL_DEFINITIONS.pop(i)
                break

    def enable(self, name: str) -> None:
        entry = self._entries.get(name)
        if not entry:
            raise KeyError(f"Tool {name!r} not found")
        entry.enabled = True
        TOOL_REGISTRY[name] = entry.fn
        if not any(s.get("function", s).get("name") == name for s in TOOL_DEFINITIONS):
            TOOL_DEFINITIONS.append(entry.schema)

    def disable(self, name: str) -> None:
        entry = self._entries.get(name)
        if not entry:
            raise KeyError(f"Tool {name!r} not found")
        entry.enabled = False
        TOOL_REGISTRY.pop(name, None)
        for i, s in enumerate(TOOL_DEFINITIONS):
            if s.get("function", s).get("name") == name:
                TOOL_DEFINITIONS.pop(i)
                break

    # ── Listing ───────────────────────────────────────────────────────────────

    def list(self) -> list[dict]:
        return [
            {
                "name": e.name,
                "source": e.source,
                "enabled": e.enabled,
                "description": e.schema.get("function", e.schema).get("description", ""),
            }
            for e in self._entries.values()
        ]

    # ── Extension loader ──────────────────────────────────────────────────────

    def load_extension(self, path: str) -> list[str]:
        """
        Load tools from a Python file that defines:
            TOOLS = [(name, callable, schema_dict), ...]
        Returns list of registered tool names.
        """
        p = Path(path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"Extension file not found: {p}")

        spec = importlib.util.spec_from_file_location("_mlx_ext", p)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

        loaded: list[str] = []
        for item in getattr(mod, "TOOLS", []):
            name, fn, schema = item
            self.register(name, fn, schema, source=f"extension:{p.name}")
            loaded.append(name)

        if not loaded:
            raise ValueError(f"No TOOLS list found in {p.name} (or it was empty)")
        return loaded

    # ── MCP connector ─────────────────────────────────────────────────────────

    def load_mcp(self, server_url: str) -> list[str]:
        """
        Fetch tool definitions from an MCP-compatible HTTP server and register
        proxy functions that POST to {server_url}/tools/{name}/run.
        """
        try:
            import httpx  # noqa: PLC0415
            resp = httpx.get(f"{server_url}/tools", timeout=5)
            resp.raise_for_status()
            tool_defs = resp.json().get("tools", [])
        except Exception as exc:
            raise RuntimeError(f"Cannot reach MCP server at {server_url}: {exc}") from exc

        loaded: list[str] = []
        for td in tool_defs:
            name = td.get("name", "")
            if not name:
                continue

            def _caller(cwd: str = ".", *, _url=server_url, _n=name, **kwargs: Any) -> str:
                import httpx as _httpx  # noqa: PLC0415
                r = _httpx.post(f"{_url}/tools/{_n}/run", json={"args": kwargs}, timeout=30)
                return r.text

            schema: dict = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": td.get("description", "MCP tool"),
                    "parameters": td.get("inputSchema") or td.get("parameters") or {},
                },
            }
            self.register(name, _caller, schema, source=f"mcp:{server_url}")
            loaded.append(name)
        return loaded

    # ── OpenAI-compatible connector ───────────────────────────────────────────

    def load_openai_connector(self, tool_defs: list[dict], base_url: str, api_key: str = "") -> list[str]:
        """
        Register tools from an OpenAI-compatible tool server.
        tool_defs: list of OpenAI function-calling schemas.
        base_url: calls POST {base_url}/{tool_name}.
        """
        loaded: list[str] = []
        for td in tool_defs:
            fn_block = td.get("function", td)
            name = fn_block.get("name", "")
            if not name:
                continue

            def _caller(cwd: str = ".", *, _n=name, _bu=base_url, _ak=api_key, **kwargs: Any) -> str:
                import httpx as _httpx  # noqa: PLC0415
                headers = {"Authorization": f"Bearer {_ak}"} if _ak else {}
                r = _httpx.post(f"{_bu}/{_n}", json=kwargs, headers=headers, timeout=30)
                return r.text

            self.register(name, _caller, td, source=f"openai-connector:{base_url}")
            loaded.append(name)
        return loaded


# ── Singleton ─────────────────────────────────────────────────────────────────
tool_manager = ToolManager()
