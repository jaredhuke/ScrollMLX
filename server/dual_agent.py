"""
Dual-agent pipeline: Primary (large) → Critic (small) → Revision (large, if needed).

Primary solves the task with full tool use.
Critic reviews the output, can run commands to verify, issues a VERDICT.
Revision (if VERDICT != LGTM) gives Primary the critic's findings to fix.
"""
from __future__ import annotations

import re
from typing import Generator

from server import agent as agent_mod
from server.schemas import (
    AgentEvent,
    DoneEvent,
    ErrorEvent,
    PhaseEvent,
    TokenEvent,
    VerdictEvent,
)

_VERDICT_RE = re.compile(r"VERDICT:\s*(LGTM|ISSUES|CRITICAL)", re.IGNORECASE)

CRITIC_SYSTEM_PROMPT = """\
You are a sharp, concise code reviewer. You receive a coding task and an AI agent's solution.

Review for:
- Bugs and logic errors
- Unhandled edge cases
- Security issues
- Whether the solution actually solves the stated task

You may use tools (run_command, read_file, grep_codebase) to verify the code runs correctly.

End your review with exactly one of these lines:
  VERDICT: LGTM
  VERDICT: ISSUES
  VERDICT: CRITICAL

Then list numbered specific issues (if any). No style nitpicks — only real problems.\
"""

REVISION_SYSTEM_PROMPT = """\
You are an expert coding assistant. A code reviewer found issues in your previous solution.
Fix only the problems listed. Do not rewrite things that are already correct.
Be surgical — minimal targeted changes.\
"""


def run_dual_agent(
    messages: list[dict],
    cwd: str,
    max_tokens: int,
    temperature: float,
    primary_slot: str = "primary",
    critic_slot: str = "critic",
    revision: bool = True,
) -> Generator[AgentEvent, None, None]:
    """Three-phase synchronous generator. Yields AgentEvent objects."""
    primary_model = agent_mod._registry[primary_slot][0].__class__.__name__  # just for display
    critic_model_name = critic_slot

    total_tokens = 0

    # ── Phase 1: Primary ────────────────────────────────────────────────────
    yield PhaseEvent(name="primary", model=primary_slot)

    primary_text = ""
    primary_tool_summary: list[str] = []

    for event in agent_mod.run_agent(
        messages=messages,
        cwd=cwd,
        max_tokens=max_tokens,
        temperature=temperature,
        slot=primary_slot,
        tools=True,
    ):
        if event.type == "token":
            primary_text += event.content
        elif event.type == "tool_result":
            primary_tool_summary.append(
                f"Tool `{event.name}` → {'ERROR' if event.error else 'OK'}: "
                + event.output[:300]
            )
        elif event.type == "done":
            total_tokens += event.total_tokens
            continue  # don't forward the done yet
        yield event

    # ── Phase 2: Critic ─────────────────────────────────────────────────────
    yield PhaseEvent(name="critic", model=critic_slot)

    task_text = messages[-1]["content"] if messages else "(no task)"
    tool_log = "\n".join(primary_tool_summary) if primary_tool_summary else "(no tools used)"

    critic_messages = [
        {
            "role": "user",
            "content": (
                f"## Original task\n{task_text}\n\n"
                f"## Agent's solution\n{primary_text}\n\n"
                f"## Tool actions taken\n{tool_log}\n\n"
                "Review the solution above. Use tools if you want to verify the code runs."
            ),
        }
    ]

    critic_text = ""
    for event in agent_mod.run_agent(
        messages=critic_messages,
        cwd=cwd,
        max_tokens=max_tokens // 2,
        temperature=0.1,  # critic should be deterministic
        slot=critic_slot,
        system_prompt=CRITIC_SYSTEM_PROMPT,
        tools=True,
    ):
        if event.type == "token":
            critic_text += event.content
        elif event.type == "done":
            total_tokens += event.total_tokens
            continue
        yield event

    # Parse verdict
    m = _VERDICT_RE.search(critic_text)
    verdict = m.group(1).upper() if m else "ISSUES"
    yield VerdictEvent(verdict=verdict)

    # ── Phase 3: Revision (only if needed) ──────────────────────────────────
    if revision and verdict != "LGTM":
        yield PhaseEvent(name="revision", model=primary_slot)

        revision_messages = list(messages) + [
            {"role": "assistant", "content": primary_text},
            {
                "role": "user",
                "content": (
                    f"A code reviewer found these issues (verdict: {verdict}):\n\n"
                    f"{critic_text}\n\n"
                    "Fix the specific problems listed. Be surgical — only change what's broken."
                ),
            },
        ]

        for event in agent_mod.run_agent(
            messages=revision_messages,
            cwd=cwd,
            max_tokens=max_tokens,
            temperature=temperature,
            slot=primary_slot,
            system_prompt=REVISION_SYSTEM_PROMPT,
            tools=True,
        ):
            if event.type == "done":
                total_tokens += event.total_tokens
                continue
            yield event

    yield DoneEvent(total_tokens=total_tokens)
