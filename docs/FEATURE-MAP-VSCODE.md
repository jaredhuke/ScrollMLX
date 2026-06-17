# Feature map — VS Code ↔ Scroll

How Scroll (a local-first, agent-first coding tool) maps to VS Code (a manual-first
editor/IDE platform). They are different *kinds* of tool — VS Code is where a human
types code with AI assist; Scroll is where an operative writes code under human
oversight. The table is about capability coverage, not parity.

Legend: **✅ Have** · **◑ Partial** · **○ Missing** · **▣ Different by design**

| VS Code area | Scroll | Notes |
|---|---|---|
| Text editor (multi-cursor, minimap, folding) | ▣ | No manual editor surface; you direct an operative. Code appears as collapsible code-cards with live preview + Apply. *Biggest deliberate divergence.* |
| Command palette (⌘⇧P) | ○ | Closest is the composer + header buttons + full-screen views. A palette over all actions is a clear opportunity. |
| Extensions / marketplace | ◑ | Skills/tools drawer + MCP connectors + extension loader. No marketplace/browse UI. |
| Integrated terminal | ✅ | Embedded terminal panel (bottom of composer) → `/v1/shell`, runs in the active project dir. |
| Source control / git | ◑ | Artifact snapshot/backup = git commits; clone repos; GitHub mesh node lights on read/push/PR; phone git-relay; pushes. No visual diff/staging UI yet. |
| Diff viewer | ○ | Agent edits apply directly (with snapshot-to-revert). A review-diff-before-apply view is a strong opportunity. |
| Debugging (DAP) | ○ | No debugger/breakpoints. |
| Tasks / run / build | ✅▣ | Projects launch & manage dev servers as tracked process groups (start/stop/logs/open), auto-detected start command — Scroll's take on tasks. |
| Settings + Settings Sync | ◑ | Settings drawer + standing-context file (per project) + Keychain keys. Cross-device sync only via git-relay/iCloud (planned), not a sync service. |
| Multi-root workspaces | ✅ | Projects = many repos/workspaces; "Use here" switches the active one. |
| Remote dev (SSH / Containers / WSL) | ▣ | No remote *editing*; instead a phone **git-relay** (dispatch from anywhere via a GitHub repo). Different shape of "remote". |
| Live Share (collaboration) | ○ | No real-time co-editing. |
| Language servers (LSP) / IntelliSense | ○ | No in-editor completion; the model supplies code. Could add LSP-backed lint/preview for artifacts. |
| Global search & replace | ◑ | Agent has grep/search/glob tools; no dedicated search UI. |
| Snippets | ▣ | Standing-context file + learned (critic-accepted) notes play the "reusable intent" role. |
| Themes / customization | ◑ | One considered pen-and-ink design (dark, teal accent); not user-themable by design. |
| Notebooks (.ipynb) | ○ | Not supported. |
| AI (Copilot / Chat / agent mode) | ✅✅ | Scroll's core and where it leads — see "Where Scroll is ahead". |

## Opportunities — adopt from VS Code
1. **Command palette** — one fuzzy launcher over every action (open project, add key, escalate, snapshot, full-screen ledger/operatives…).
2. **Diff-before-apply** — show the agent's change as a reviewable diff with accept/reject per hunk, not just snapshot-and-revert.
3. **Lightweight manual edit** — an inline CodeMirror on an artifact for a quick human tweak without round-tripping the model.
4. **Global search UI** — a real find/replace across the project, surfacing the agent's grep results visually.
5. **Settings/keys sync** — finish the iCloud path so config + Keychain refs follow the user across Macs.
6. **Skill marketplace** — browse/install skills + MCP connectors from the drawer.

## Where Scroll is ahead / differentiated
- **Agent-first with governance** — operatives, the agentic mesh, per-agent AEM metrics, and **AGMO/HUMO** autonomy/oversight modes from the Agentic Experience Manual. VS Code has no governance layer.
- **100% local model** with a visible **access barrier** — local gets full context (code + personal), cloud is escalation-only and **code-only**.
- **Token/burn ledger** per project per prompt + efficiency analysis — cost is a first-class, visible metric.
- **Multi-model fan-out** — tag several models, get parallel responses; on-demand cloud escalation.
- **Always-on critic + trust loop** — accepted findings become standing corrections.
- **Phone git-relay** — dispatch and get notified from your phone, no inbound network.
- **Projects = repos + runnable products** launched/managed in-app.
- **Vision** via cloud escalation; **standing-context file**; native macOS app (boots the server, deep macOS context, biometric gate, menu-bar burn sparkline).
