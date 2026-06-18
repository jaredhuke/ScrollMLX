from __future__ import annotations
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class ToolCallFunction(BaseModel):
    name: str
    arguments: str  # JSON string


class ToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: ToolCallFunction


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: Optional[str] = None
    tool_calls: Optional[list[ToolCall]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


class ChatRequest(BaseModel):
    messages: list[Message]
    model: str = "mlx-community/Qwen2.5-Coder-32B-Instruct-4bit"
    stream: bool = True
    max_tokens: int = 8192
    temperature: float = 0.15
    cwd: str = "."
    max_iterations: int = 12  # tool-use loops per turn — raised by the composer's Effort control


# SSE event shapes sent to clients
class TokenEvent(BaseModel):
    type: Literal["token"] = "token"
    content: str


class ToolCallEvent(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    arguments: dict[str, Any]


class ToolResultEvent(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    id: str
    name: str
    output: str
    error: bool = False


class DoneEvent(BaseModel):
    type: Literal["done"] = "done"
    total_tokens: int = 0


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str


class PhaseEvent(BaseModel):
    type: Literal["phase"] = "phase"
    name: str   # "primary" | "critic" | "revision"
    model: str


class VerdictEvent(BaseModel):
    type: Literal["verdict"] = "verdict"
    verdict: str   # "LGTM" | "ISSUES" | "CRITICAL"


AgentEvent = TokenEvent | ToolCallEvent | ToolResultEvent | DoneEvent | ErrorEvent | PhaseEvent | VerdictEvent


class DualAgentRequest(BaseModel):
    messages: list[Message]
    cwd: str = "."
    max_tokens: int = 8192
    temperature: float = 0.15
    primary_model: str = "mlx-community/Qwen2.5-Coder-32B-Instruct-4bit"
    critic_model: str = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
    revision: bool = True


# ── Tool / provider management ────────────────────────────────────────────────

class ExtensionLoadRequest(BaseModel):
    path: str  # absolute or ~ path to a Python extension file


class MCPConnectorRequest(BaseModel):
    server_url: str  # base URL of the MCP server


class OpenAIConnectorRequest(BaseModel):
    tool_defs: list[dict]  # OpenAI function-calling schemas
    base_url: str
    api_key: str = ""


class ProviderSelectRequest(BaseModel):
    provider: str  # "mlx" | "openai" | "anthropic"
