# Scroll — Product Requirements

**Scroll** is a 100%-local AI coding agent for macOS. A FastAPI server runs an on-device
MLX model (Qwen2.5-Coder 32B/7B); a web UI (`static/index.html`), a CLI, and a native
SwiftUI app (a WKWebView shell) sit on top. Cloud models are used only when the user
explicitly escalates — and only ever see code, never personal context.

## Vision
Let a *novice "vibe coder"* describe what they want and get a working thing, on their
own Mac, privately — while making the agent's work legible and trustworthy through a
calm, visual instrument rather than a wall of logs.

## Identity & non-negotiable laws
- **100% local & private by default.** Cloud is opt-in, code-only, per-escalation.
- **Everything visual / object-oriented** — every action is a button; no terminal-first flows.
- **Pen-and-ink "ch'an calm"** aesthetic: black-and-white with **red/orange** accents;
  **teal is reserved for the completion "moment" only.** Color marks the actionable.
- **No monospace** outside code blocks. **No silent failures** — errors surface clearly.
- Helpers are **operatives**, not "agents": PRIMARY (author) + CRITIC (reviews every
  reply) + optional CLOUD (escalation), shown on the **Agentic Mesh**.
- The system **learns the user** (standing/learned context, vigilance friction).

## The Agentic Mesh (the instrument)
- A calm radial instrument: completion core (teal at 100%), burn (amber), drift, vigilance.
- **Operatives:** ACTIVE ones sit on the inner ring (warm); IDLE ones are parked at the
  right edge, cool/dim — click to **enlist** (critic→review on, a cloud op→escalation target).
- **Skills** feeding the workflow render as small teal nodes wired to the core (click → manage).
- **Output artifacts** render as page-glyphs in a lower zone — visually distinct from the
  agentic circles; click → preview.
- **Requests in flight:** one thin rotating arc per concurrent stream (run / fan-out / escalate).
- Background is abstracted (sparse marks + broken orbital arcs), not a literal compass.
- The core shows only the % (no overset); the label + in-flight detail appear on hover.

## Core features
- **Chat** with streaming; **@-target** models (`@claude`, `@all`); multi-model fan-out.
- **Artifacts:** code/HTML/SVG/CSS/React cards — Download-first, Open-in-browser (native
  bridge to the OS browser), and an in-app **Preview** that renders HTML+CSS, inlines added
  images, and transpiles JSX/TSX via React+Babel. The model is told to use **inline SVG**
  for graphics (it's local & text-only — no raster generation).
- **Projects:** a project = a git repo + a runnable product (launch/stop/logs, inline
  details). **Each project has its own conversation** — switching swaps the whole chat.
- **Skills/Tools/Plugins:** drop a `.md` file or folder (or paste a path) to add skills;
  enabled skills inject into every run and show ON-state + on the mesh.
- **Standing/learned context** per project; **vigilance** shows *why* it dropped (edits/
  challenges) and feeds learned corrections.
- **Onboarding** with one-click local-model install (progress) + starter prompts.
- **Notifications:** stacked, severity-coded; errors persist. A health banner when the
  local engine is down.
- **File versioning:** every write keeps the real filename and snapshots a complete,
  readable copy (`index.v1.html`, `index.v2.html`, …) — see `GET /v1/versions`.
- **Self-improvement:** ScrollMLX is a registered project with CI (`.github/workflows/ci.yml`),
  `scripts/check.sh`, and a workflow in `CONTRIBUTING.md`.

## Roadmap (from the VS Code / Cursor gap analysis)
Scroll leads on local+private, the operative mesh, and learns-the-user. Highest-leverage
gaps to close, on its own terms:
1. **Diff before Apply** — red/green review + per-hunk accept/reject (top gap).
2. **Per-turn checkpoint + one-click Undo** (backup machinery exists; wire restore).
3. **File tree / project map** — see the whole codebase, not just produced files.
4. **Local codebase index / semantic retrieval** (private, on-device embeddings).
Deliberately skipped (anti-identity): breakpoint debugger, remote dev/SSH, tab autocomplete,
multi-root/settings-sync.

## Constraints
- The on-device model is **text-only**; raster images need cloud vision (`/v1/vision`),
  graphics should be inline SVG.
- The macOS app needs a **full-Xcode build** (`Scroll/build.sh`); the web UI is live on reload;
  `server/*.py` changes need a server restart.
