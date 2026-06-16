"""
Agent loop: wraps mlx-lm inference with tool-use and streaming.

Flow:
  format prompt → stream tokens → detect <tool_call> → execute → inject result → repeat
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Generator, Any

import mlx_lm
from mlx_lm.sample_utils import make_sampler
from server.config import SYSTEM_PROMPT
from server.schemas import (
    AgentEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    DoneEvent,
    ErrorEvent,
)
from server.tools import TOOL_REGISTRY, TOOL_DEFINITIONS

_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
# bare JSON tool calls some models emit without the <tool_call> wrapper
_BARE_TOOL_RE = re.compile(r'\{\s*"(?:name|tool|function)"\s*:\s*"')

# Named model registry — supports multiple models loaded simultaneously
# Keys are slot names (e.g. "primary", "critic"); values are (model, tokenizer)
_registry: dict[str, tuple] = {}

# Compat alias so server/main.py health check still works
@property  # type: ignore[misc]
def _model():
    m = _registry.get("primary")
    return m[0] if m else None


def load_model(model_path: str, slot: str = "primary") -> None:
    if slot not in _registry:
        print(f"[mlx:{slot}] Loading {model_path} …", flush=True)
        _registry[slot] = mlx_lm.load(model_path)
        print(f"[mlx:{slot}] Ready.", flush=True)


def is_loaded(slot: str = "primary") -> bool:
    return slot in _registry


def _get_slot(slot: str) -> tuple:
    assert slot in _registry, f"Model slot '{slot}' not loaded — call load_model() first"
    return _registry[slot]


def _build_prompt(messages: list[dict], tools: bool = True, slot: str = "primary") -> str:
    _, tokenizer = _get_slot(slot)
    try:
        return tokenizer.apply_chat_template(
            messages,
            tools=TOOL_DEFINITIONS if tools else None,
            tokenize=False,
            add_generation_prompt=True,
        )
    except Exception:
        # Fallback: no tool template support — inject tool list in system
        tool_json = json.dumps(TOOL_DEFINITIONS, indent=2)
        msgs = list(messages)
        if msgs and msgs[0]["role"] == "system":
            msgs[0] = {
                "role": "system",
                "content": msgs[0]["content"] + f"\n\nAvailable tools (JSON schema):\n{tool_json}",
            }
        return tokenizer.apply_chat_template(
            msgs,
            tokenize=False,
            add_generation_prompt=True,
        )


def _scan_balanced(text: str, start: int) -> str | None:
    """Return the balanced {...} JSON object starting at `start` (brace+string aware)."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _parse_tool_calls(text: str) -> list[dict[str, Any]]:
    """Parse both <tool_call>-tagged and bare-JSON tool calls (models emit either)."""
    calls = []
    spans: list[tuple[int, int]] = []

    # 1) tagged <tool_call>...</tool_call>
    for m in _TOOL_CALL_RE.finditer(text):
        try:
            data = json.loads(m.group(1))
            calls.append({
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "name": data["name"],
                "arguments": data.get("arguments", data.get("parameters", {})),
            })
            spans.append((m.start(), m.end()))
        except (json.JSONDecodeError, KeyError):
            pass

    # 2) bare JSON tool calls — {"name": "...", "arguments": {...}} with no wrapper
    for m in _BARE_TOOL_RE.finditer(text):
        start = m.start()
        if any(s <= start < e for s, e in spans):
            continue  # already captured inside a tagged span
        blob = _scan_balanced(text, start)
        if not blob:
            continue
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "name" in data:
            calls.append({
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "name": data["name"],
                "arguments": data.get("arguments", data.get("parameters", {})),
            })
    return calls


def _strip_think(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


def run_agent(
    messages: list[dict],
    cwd: str,
    max_tokens: int,
    temperature: float,
    max_iterations: int = 12,
    slot: str = "primary",
    system_prompt: str | None = None,
    tools: bool = True,
) -> Generator[AgentEvent, None, None]:
    """Synchronous generator — yields AgentEvent objects, runs inference on calling thread."""
    model, tokenizer = _get_slot(slot)

    sys_prompt = system_prompt or SYSTEM_PROMPT
    history = [{"role": "system", "content": sys_prompt}] + messages
    total_tokens = 0

    for iteration in range(max_iterations):
        prompt = _build_prompt(history, tools=tools, slot=slot)
        response_text = ""
        suppress = False  # stop streaming once a tool-call begins — never surface code/JSON to the chat

        # Stream tokens from mlx-lm (0.31+ uses sampler= not temperature=)
        for chunk in mlx_lm.stream_generate(
            model,
            tokenizer,
            prompt=prompt,
            max_tokens=max_tokens,
            sampler=make_sampler(temp=temperature),
        ):
            # stream_generate yields str chunks
            tok = chunk if isinstance(chunk, str) else getattr(chunk, "text", str(chunk))
            response_text += tok
            total_tokens += 1

            # Once a tool call (tagged or bare JSON) appears, suppress the rest of the visible stream
            if not suppress and ("<tool_call>" in response_text or _BARE_TOOL_RE.search(response_text)):
                suppress = True
            if suppress:
                continue

            # Strip internal <think> before streaming visible tokens
            visible = _THINK_RE.sub("", tok)
            if visible:
                yield TokenEvent(content=visible)

        # Clean up internal reasoning tokens from stored history
        clean_response = _strip_think(response_text)

        # Check for tool calls
        tool_calls = _parse_tool_calls(clean_response)

        if not tool_calls:
            # No tool calls → agent is done
            history.append({"role": "assistant", "content": clean_response})
            break

        # Add assistant turn (prose before the first tool call — tagged or bare — never the JSON)
        pre_call = _TOOL_CALL_RE.split(clean_response)[0]
        bm = _BARE_TOOL_RE.search(pre_call)
        if bm:
            pre_call = pre_call[: bm.start()]
        pre_call = pre_call.strip()
        history.append({"role": "assistant", "content": pre_call or "(calling a tool)"})

        # Execute each tool call
        for tc in tool_calls:
            name = tc["name"]
            args = tc["arguments"]
            call_id = tc["id"]

            yield ToolCallEvent(id=call_id, name=name, arguments=args)

            fn = TOOL_REGISTRY.get(name)
            if fn is None:
                output = f"ERROR: unknown tool '{name}'"
                error = True
            else:
                try:
                    output = str(fn(cwd=cwd, **args))
                    error = output.startswith("ERROR:")
                except Exception as exc:
                    output = f"ERROR: {exc}"
                    error = True

            yield ToolResultEvent(id=call_id, name=name, output=output, error=error)

            history.append(
                {
                    "role": "tool",
                    "content": output,
                    "tool_call_id": call_id,
                    "name": name,
                }
            )

        if iteration == max_iterations - 1:
            yield ErrorEvent(message="Reached max tool iterations without final answer.")

    yield DoneEvent(total_tokens=total_tokens)
