# Scroll — Multi-Model Orchestration Plan

> One orchestrator, four models — Claude, GPT, DeepSeek, local Qwen — each used where it's
> strongest, with per-model token + cost accounting and composable orchestration primitives.

## TL;DR

- **Today:** only local Qwen can actually *do* work (read / edit / run). Claude, GPT, and
  DeepSeek can only *talk* — they generate text but never execute tools.
- **The fix is three phases:**
  1. **Unified tool-calling** — give every model the same read→edit→test loop Qwen already has.
  2. **Agents + orchestrator** — a named `Agent` (model + role + tools + budget) and composable
     Router / Pipeline / Fan-out primitives.
  3. **Budgets & ledger** — per-model tokens + dollars, enforced.
- **Everything hangs on Phase 1.** Without it, three of the four models are spectators.

---

## 1. North-star architecture

```
                ┌──────────────────────────────────────────────┐
                │              Orchestrator                    │
                │   Router · Pipeline · Fan-out (composable)   │
                └───────────────┬──────────────────────────────┘
                                │  dispatch a Task to an Agent
        ┌───────────────┬───────┴────────┬───────────────┐
        ▼               ▼                ▼               ▼
   Agent: Qwen     Agent: DeepSeek   Agent: GPT      Agent: Claude
   (mlx/local)     (openai-compat)  (openai)        (anthropic)
        │               │                │               │
        └───────────────┴────────────────┴───────────────┘
                        │  shared ToolExecutor
                        ▼
        read_file · write_file · run_command · grep · search · …
                        │
                        ▼
              Ledger (tokens + $ per agent)  ·  Budgets (caps + alerts)
```

One idea drives the whole design: **the "agent loop" and the "model" are separate concerns.**
Today they're fused — the loop is hardwired to MLX. We split them so any model can drive the
loop, and any loop can be composed into an orchestration graph.

---

## 2. Current state (ground truth)

| Concern | Where it lives today | Status |
|---|---|---|
| Tool loop (read/edit/run/loop) | [`server/agent.py`](/Volumes/Code/scroll-mlx/server/agent.py) `_run_agent` | ✅ MLX-only |
| Model slots | `agent._registry` (`"primary"`, `"critic"`) | ✅ two slots |
| Providers | [`server/providers.py`](/Volumes/Code/scroll-mlx/server/providers.py) `_REGISTRY` | ✅ 7 built-ins, **text-only** |
| Provider plugins (DeepSeek, Kimi, …) | [`server/provider_plugins.py`](/Volumes/Code/scroll-mlx/server/provider_plugins.py) | ✅ markdown-driven, **text-only** |
| Tool registry | [`server/tool_registry.py`](/Volumes/Code/scroll-mlx/server/tool_registry.py) | ✅ enable/disable/register, MCP/OpenAI/extension |
| Multi-agent | [`server/dual_agent.py`](/Volumes/Code/scroll-mlx/server/dual_agent.py) | ⚠️ one hardcoded pipeline (architect→critic→revision) |
| Token ledger | [`server/ledger.py`](/Volumes/Code/scroll-mlx/server/ledger.py) | ⚠️ tokens only, no $, no per-model split |
| Context compaction | `agent._trim_history` | ⚠️ drops old turns (lossy), no summarize |
| Budgets / spend | — | ❌ none |

**The two real gaps:** (1) cloud providers can't execute tools, and (2) there's no `Agent`
object that composes model + role + tools + budget.

---

## 3. Design principles

1. **One tool executor, many models.** Tool execution (resolve → run → capture → compact → inject)
   is written once and shared by local *and* cloud loops.
2. **Models are pluggable behind one interface.** Local MLX, OpenAI-compatible (OpenAI, DeepSeek,
   Groq, OpenRouter, Cerebras, http-plugins), and Anthropic all expose the same
   `run(turn) -> events` contract.
3. **Orchestration is a graph, not an if/else.** Router, Pipeline, and Fan-out are three small
   primitives that compose; no more bespoke pipelines.
4. **Cost is first-class.** Every `done` event carries `provider`, `model`, `prompt_tokens`,
   `completion_tokens`, and `cost_usd`; the ledger stores it at write time.
5. **Local is the default; cloud is escalation.** Keep Qwen as the free workhorse; climb the
   ladder only when a task needs it.

---

## 4. Phase 1 — Unified tool-calling (make cloud models able to *do*)

**Goal:** Claude, GPT, and DeepSeek get the same read→edit→run→repeat loop Qwen has.

### 4.1 Extract a shared `ToolExecutor`

Pull the tool-execution half out of `_run_agent` ([agent.py:332-364](/Volumes/Code/scroll-mlx/server/agent.py#L332)):

- `ToolExecutor.execute(name, args, cwd) -> (output, error)` — resolve from `TOOL_REGISTRY`,
  run, classify `ERROR:` results.
- `ToolExecutor.compact(name, output) -> str` — semantic trimming (exit code + head/tail for
  commands, matched region for reads) instead of the flat `_TOOL_OUTPUT_CAP`.
- `ContextStore` — history append + compaction (see 4.5) + token budget, shared by all loops.

The local `run_agent` becomes a client of `ToolExecutor`; cloud loops use the same one.

### 4.2 OpenAI-compatible runner (OpenAI, Groq, OpenRouter, Cerebras, DeepSeek)

Use the OpenAI SDK's **native function calling** (the providers already pass `tools`, they just
never loop). The loop:

1. Send `messages` + `tools` (the schemas in `TOOL_DEFINITIONS`).
2. If the reply contains `tool_calls`, execute each via `ToolExecutor`.
3. Append `role:"tool"` results and repeat (bounded by `max_iterations`).
4. Stream visible text as `token` events; emit `tool_call` / `tool_result` events exactly like
   the local loop, so the UI/CLI don't change.

### 4.3 Anthropic runner

Anthropic already gets its schemas converted to `input_schema` ([providers.py:152](/Volumes/Code/scroll-mlx/server/providers.py#L152)). Add the loop over `tool_use` blocks → `ToolExecutor` → `tool_result` blocks → repeat.

### 4.4 DeepSeek + http plugins

`_HttpPlugin.stream` ([provider_plugins.py:158](/Volumes/Code/scroll-mlx/server/provider_plugins.py#L158)) posts messages and reads text only. Two options:

- **Recommended:** route all OpenAI-compatible `http` plugins (DeepSeek, Kimi, …) through the
  same 4.2 runner by giving them `tools` support. One code path, no duplication.
- Fallback: enhance `_HttpPlugin` in place with a tool loop.

### 4.5 Upgrade compaction (do it here, it's cheap)

Replace drop-oldest `_trim_history` ([agent.py:215](/Volumes/Code/scroll-mlx/server/agent.py#L215)) with
**summarize-middle**: keep system + first user turn + last N turns; replace the middle with one
`assistant` message "Earlier context (summarized): …" produced by a cheap model (the critic slot
or a 7B). This is the single highest-leverage token fix and applies to *every* model.

**Acceptance criteria**
- [ ] `claude`, `gpt`, `deepseek` each complete a task that requires reading, editing, and running
      a test in a real repo.
- [ ] Tool events from cloud models render identically to local ones in the UI and CLI.
- [ ] Long conversations summarize instead of truncating.

---

## 5. Phase 2 — Agents + orchestrator

**Goal:** compose the four models into named, reusable agents and route work across them.

### 5.1 The `Agent` object

A plain data object that composes what already exists:

| Field | Meaning | Source today |
|---|---|---|
| `name` | `"architect"`, `"reviewer"`, `"qwen"` … | new |
| `provider` | `mlx` / `openai` / `anthropic` / plugin id | `providers._REGISTRY` |
| `model` | concrete model id | provider default |
| `system_prompt` | role instructions | `agent.SYSTEM_PROMPT` + role text |
| `tools` | subset of tool names | `tool_manager` |
| `token_budget` | per-run cap | new |
| `cwd` | working dir | request |

An `Agent` is *also* serializable so it can be defined in a file (mirroring the
markdown-plugin pattern — "an agent is a markdown file" is already half-true here).

### 5.2 Orchestration primitives (composable)

Three small building blocks, each a function from `Task -> AgentEvents`:

- **Router (escalate ladder).** Try the cheapest capable agent first (Qwen). Escalate to the next
  rung on a clear signal: `max_iterations` exhausted, an explicit low-confidence/error result, a
  critic verdict ≠ LGTM, or a task-type match (e.g. images → Gemini/GPT). Ladder is configurable,
  e.g. `Qwen → DeepSeek → GPT → Claude`.
- **Pipeline.** Ordered stages; each stage passes a *compacted* handoff
  `{task, prior result, tool log}` to the next. Generalizes the fixed dual pipeline
  ([dual_agent.py](/Volumes/Code/scroll-mlx/server/dual_agent.py)) into
  `architect(Claude) → implementer(Qwen) → reviewer(DeepSeek) → fix(Qwen)`.
- **Fan-out.** Run one task against N agents concurrently; a `merge` step either picks the best
  (via a critic) or concatenates the results. Cloud models run truly in parallel; local Qwen
  participates serially (see 5.3).

**Composition rule:** a Router can dispatch to a Pipeline; a Fan-out branch can be a Router.
One tiny graph — no bespoke code per workflow.

### 5.3 Concurrency model

- Local MLX is **serialized** by `agent._GEN_LOCK` (one generation at a time — a Metal safety
  constraint, not an oversight).
- Cloud providers are **concurrent** — fan-out across Claude/GPT/DeepSeek is genuinely parallel.
- Orchestration must therefore be **async**, with local runs queued and cloud runs `await`ed in
  parallel. The SSE endpoints already bridge sync inference → async via `asyncio.Queue`.

### 5.4 Handoff payload

Every handoff is a compact object — never the full raw transcript — to keep the multi-agent
"coordination tax" low:

- `task` (the original request)
- `result` (the previous agent's output, capped)
- `tool_log` (name → OK/ERROR + first ~300 chars, as the dual pipeline already does)
- `verdict` (when a critic is involved)

**Acceptance criteria**
- [ ] Define agents in files; list/add/run them from CLI and UI.
- [ ] Router climbs the ladder on failure and shows *which* model answered.
- [ ] A `Claude → Qwen → DeepSeek → Qwen` pipeline runs end-to-end.
- [ ] Fan-out across Claude + GPT + DeepSeek returns in parallel with a merged result.
- [ ] Tokens are attributed **per agent**, not just per run.

---

## 6. Phase 3 — Budgets & ledger

**Goal:** know what each model cost and cap it.

### 6.1 Pricing table

Per `(provider, model)` with separate input/output rates. Local MLX is `$0` cash (tracked
separately as time/compute). Store defaults in `server/pricing.py`, overridable per user.

### 6.2 Token accounting

- Capture **prompt_tokens + completion_tokens** on every `done` (real `usage` where the SDK
  provides it; `len//4` estimate for local/plugins).
- Extend each ledger entry with `provider`, `model`, `cost_usd`, `wall_s`, `tok_per_s`, and a
  `pricing_version`.

### 6.3 Budgets + enforcement

`~/.scroll/budgets.json` — per `scope` (global / project / provider), per `period`
(daily / weekly / monthly), with `limit_usd` and/or `limit_tokens`, and `action` = `warn` or
`block`. One `spend.check(provider, project, est)` gate runs before every cloud call and before
local runs.

**Acceptance criteria**
- [ ] `scroll spend` / `scroll budget` / `/burn` show live per-model spend.
- [ ] A `block` budget refuses the call; a `warn` budget surfaces a banner.
- [ ] `by_mode` in the ledger becomes a real cash vs. local-compute breakdown.

---

## 7. API surface (new endpoints)

| Method | Path | Purpose |
|---|---|---|
| `GET/POST` | `/v1/agents` | List / define agents |
| `POST` | `/v1/agents/{name}/run` | Run one agent (stream) |
| `POST` | `/v1/orchestrate` | Run a Router / Pipeline / Fan-out spec (stream) |
| `GET` | `/v1/budgets` | List budgets + current-period spend |
| `POST/PUT/DELETE` | `/v1/budgets/{id}` | Manage budgets |
| `GET` | `/v1/spend` | Current-period spend by scope/provider |

Existing `/v1/agent` gains optional `agent` / `provider` / `model` fields (the schema already
has `model`); `/v1/dual` is re-expressed as a Pipeline spec and kept for compatibility.

---

## 8. CLI & UI surface

**CLI** (extending [`cli.py`](/Volumes/Code/scroll-mlx/cli.py)):

- `agent list | add | run <name> <task>`
- `route <task>` / `pipeline <task>` / `fanout <task> --models claude,gpt,deepseek`
- `provider list | use <id>`
- `budget list | set | rm` · `spend [--period]` · `/burn`
- `project list | add | launch | stop | logs` · `repo list | status | pull | push`

**UI:** the operatives/model-picker already exists; it gains the ability to (a) compose a
pipeline/fan-out and (b) see per-model cost inline. No new UI paradigm — just wiring.

---

## 9. Sequencing (milestones)

| # | Milestone | Depends on | Ships |
|---|---|---|---|
| M1 | `ToolExecutor` extraction + summarize-compaction | — | shared tool loop, better memory |
| M2 | OpenAI-compatible tool runner (incl. DeepSeek) | M1 | GPT + DeepSeek can edit files |
| M3 | Anthropic tool runner | M1 | Claude can edit files |
| M4 | `Agent` object + registry + `/v1/agents` | M2, M3 | named, reusable agents |
| M5 | Orchestrator primitives (Router/Pipeline/Fan-out) | M4 | multi-model workflows |
| M6 | Pricing + token accounting + budgets | M4 | per-model cost & caps |
| M7 | CLI + UI wiring | M5, M6 | the whole thing, end-to-end |

M1–M3 are the enabling layer; M4 is the linchpin; M5–M7 build on top.

---

## 10. Risks & open questions

**Risks**
- **Memory (Metal OOM)** — a second local model beside the 32B can abort the process. Keep the
  critic as a role on primary unless RAM is ample (already the safe default).
- **Cloud tool-loop cost** — tool calling multiplies round-trips and tokens; the compaction and
  token budgets in Phase 1/3 are what keep this affordable.
- **Provider API drift** — OpenAI/Anthropic tool-call shapes differ; isolate each behind the
  runner interface so a breaking change stays in one file.

**Open questions (decide during M4)**
1. Should a plain message default to Qwen (free) with auto-escalate, or to a user-chosen default?
2. Do local runs count toward a *token* budget, a *time* budget, or both?
3. Should agents be file-defined (`.md`) to match the provider-plugin pattern, or registry-only?

---

## Summary

The system you already have is ~70% of the way there: slots, providers, tools, ledger, memory,
and a markdown plugin pattern are all in place. This plan adds exactly two missing pieces —
**cloud tool-calling** and an **Agent abstraction** — and composes what remains into
Router / Pipeline / Fan-out. Build M1 first.
