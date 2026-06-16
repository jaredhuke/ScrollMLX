# Scroll — native macOS app

A native shell that turns the local agent into a one-click product. It boots the
Python services, shows a live boot screen, then hosts the HTML UI in a WKWebView —
and adds native superpowers the browser can't reach.

## What the native layer adds

- **One-click boot** (`ServerManager`) — locates `uv`, **checks for updates**, launches
  `uvicorn`, polls `/health`, and reports each step on the boot screen. Quit stops it cleanly.
- **Self-update** — checks the git remote; `Update & restart` does `git pull --ff-only` and relaunches.
- **Deep context engine** (`ContextEngine`) — gathers macOS local context every 20s and POSTs it to
  `/v1/context`, where the agent picks it up as live context:
  cwd · frontmost app/window · clipboard · recent files (Spotlight) · Finder selection ·
  git branch/dirty/last-commit · today's calendar + reminders (EventKit) · CPU / memory / thermal.
  Permission-gated pieces degrade gracefully if access isn't granted.
- **Keychain** (`Keychain`) — API keys in the login Keychain under the **same `scroll` service**
  as the Python CLI, so `python cli.py key set …` and the app share keys. Rotation uses a **native**
  secure field — the secret never enters the web layer.
- **Security gate** (`SecurityGate`) — install / download / network / delete actions require an explicit
  human "Allow once". The boundary is the human, not a self-judging agent.
- **JS bridge** (`WebHostView`) — the web UI can call native capabilities:
  `await window.webkit.messageHandlers.native.postMessage({action})` →
  `gatherContext` · `keyStatus` · `rotateKey` · `confirm` · `reveal`. `window.__nativeShell === true`
  when running inside the app.

## Build & run

Requires **full Xcode** (not just Command Line Tools) and [`xcodegen`](https://github.com/yonsm/XcodeGen).

```bash
cd Scroll
xcodegen generate          # regenerate the .xcodeproj from project.yml
open Scroll.xcodeproj     # then Run (⌘R) in Xcode
```

On first launch macOS will ask for Calendar / Reminders / Automation (Finder) and, optionally,
Accessibility (for the focused window title) — all used only to enrich local context, which stays on
your Mac. The app expects the repo at `~/scroll`.

## Files

```
Sources/Scroll/
  ScrollApp.swift        # @main · boot screen ↔ web UI · menubar
  AppState.swift           # owns ServerManager + ContextEngine
  Services/
    ServerManager.swift    # boot steps · uvicorn lifecycle · updates
    ContextEngine.swift    # deep macOS local context → /v1/context
    Keychain.swift         # shared 'scroll' Keychain service
    SecurityGate.swift     # human-gated approvals
  Views/
    BootView.swift         # one-click boot status
    WebHostView.swift      # WKWebView host + native JS bridge
```
