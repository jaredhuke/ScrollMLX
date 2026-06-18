"""
Agent loop: wraps mlx-lm inference with tool-use and streaming.

Flow:
  format prompt → stream tokens → detect <tool_call> → execute → inject result → repeat
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from typing import Generator, Any

import mlx_lm
from mlx_lm.sample_utils import make_sampler
try:
    import mlx.core as mx  # noqa: PLC0415
except Exception:  # pragma: no cover
    mx = None
from server.config import SYSTEM_PROMPT

# Serialize ALL local MLX generation. mlx_lm/Metal is NOT safe for concurrent generate calls on
# the same device — two overlapping runs (a queued message, the background narration/clarify call,
# the local provider, dual primary+critic) hard-crash the process. One generation at a time.
_GEN_LOCK = threading.Lock()


def free_mem() -> None:
    """Release the KV/Metal cache between generations — the main guard against the
    Metal out-of-memory abort that shows up as 'Services quit unexpectedly'."""
    if mx is None:
        return
    for fn in ("clear_cache", "reset_peak_memory"):
        f = getattr(mx, fn, None) or getattr(getattr(mx, "metal", None), fn, None)
        try:
            if f:
                f()
        except Exception:
            pass
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


_mem_tuned = False


def _tune_memory() -> None:
    """Keep MLX under the GPU's recommended working set so a command buffer never
    fails mid-generation — that failure throws on a Metal queue and aborts the
    whole process (SIGABRT, 'Services quit unexpectedly'). Can't be caught; only
    prevented."""
    global _mem_tuned
    if _mem_tuned or mx is None:
        return
    try:
        _di = getattr(mx, "device_info", None) or getattr(getattr(mx, "metal", None), "device_info", None)
        info = _di() if _di else {}
        rec = int(info.get("max_recommended_working_set_size", 0))
        if rec:
            # evict cache before crossing the safe ceiling; allow wiring up to it
            for setter in ("set_memory_limit", "set_wired_limit"):
                fn = getattr(mx, setter, None)
                if fn:
                    try:
                        fn(rec)
                    except Exception:
                        pass
            # keep the reusable cache modest so it doesn't crowd out live tensors
            try:
                (getattr(mx, "set_cache_limit", None) or (lambda *_: None))(int(rec * 0.25))
            except Exception:
                pass
        _mem_tuned = True
    except Exception:
        _mem_tuned = True


def load_model(model_path: str, slot: str = "primary") -> None:
    if slot not in _registry:
        print(f"[mlx:{slot}] Loading {model_path} …", flush=True)
        _registry[slot] = mlx_lm.load(model_path)
        _tune_memory()
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


_HISTORY_CHAR_BUDGET = 48_000   # ~12k tokens — keeps activation/KV memory well under the GPU ceiling
_TOOL_OUTPUT_CAP = 6_000        # a single huge file read must not balloon the context


def _trim_history(history: list[dict]) -> list[dict]:
    """Drop the oldest non-system turns once the running context exceeds the budget.
    Unbounded tool loops are the usual path to a Metal OOM abort."""
    if not history:
        return history
    has_sys = history[0].get("role") == "system"
    head = history[:1] if has_sys else []
    rest = history[len(head):]

    def total(msgs):
        return sum(len(m.get("content", "") or "") for m in msgs)

    while total(head + rest) > _HISTORY_CHAR_BUDGET and len(rest) > 2:
        rest.pop(0)
    return head + rest


def run_agent(*args, **kwargs) -> Generator[AgentEvent, None, None]:
    """Public entry — serializes local generation so two runs never hit Metal at once.

    A short blocking wait covers the common case (a quick background gen finishing); a busy
    long run (e.g. Max effort) rejects the new request cleanly instead of crashing the process.
    The lock is released even if the consumer abandons the generator (GeneratorExit → finally)."""
    if not _GEN_LOCK.acquire(timeout=1.0):
        yield ErrorEvent(message="The local model is busy with another request — it runs one at a time. Try again in a moment.")
        return
    try:
        yield from _run_agent(*args, **kwargs)
    finally:
        _GEN_LOCK.release()


def _run_agent(
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
    _tune_memory()
    max_tokens = max(256, min(int(max_tokens), 8192))  # bound generation KV so Metal can't OOM-abort (raised so Deep/Max effort can finish long files)
    max_iterations = max(1, min(int(max_iterations), 60))  # honor the composer's Effort control, with a hard safety cap

    sys_prompt = system_prompt or SYSTEM_PROMPT
    history = [{"role": "system", "content": sys_prompt}] + messages
    total_tokens = 0

    for iteration in range(max_iterations):
        history = _trim_history(history)           # keep the working set bounded across tool loops
        prompt = _build_prompt(history, tools=tools, slot=slot)
        response_text = ""
        suppress = False  # stop streaming once a tool-call begins — never surface code/JSON to the chat

        # Stream tokens from mlx-lm (0.31+ uses sampler= not temperature=)
        try:
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
        except Exception as exc:
            free_mem()
            yield ErrorEvent(message=f"Generation failed ({type(exc).__name__}: {exc}). Memory was cleared — try again or shorten the request.")
            return
        finally:
            free_mem()  # release this turn's KV cache before the next iteration

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

            hist_out = output
            if len(hist_out) > _TOOL_OUTPUT_CAP:  # full output already streamed to the UI; cap what re-enters context
                hist_out = hist_out[:_TOOL_OUTPUT_CAP] + f"\n…[truncated {len(output) - _TOOL_OUTPUT_CAP} chars]"
            history.append(
                {
                    "role": "tool",
                    "content": hist_out,
                    "tool_call_id": call_id,
                    "name": name,
                }
            )

        if iteration == max_iterations - 1:
            yield ErrorEvent(message="Reached max tool iterations without final answer.")

    yield DoneEvent(total_tokens=total_tokens)
