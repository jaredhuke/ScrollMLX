# Contributing to ScrollMLX

Scroll can improve itself: open the **ScrollMLX** project in the app, "Use here", and
task an operative against this repo. Whether a human or an operative is editing, the
same workflow keeps `main` healthy.

## The loop

1. **Branch** off `main` — never commit straight to it:
   `git switch -c feat/<short-name>` (or `fix/…`, `chore/…`, `docs/…`).
2. **Make one focused change.** Match the surrounding code's style; respect the UX laws
   (visual/object-oriented, ch'an-calm, black-&-white + red/orange accents, color only on
   actionable elements, no monospace outside code blocks, no silent failures).
3. **Verify** before every commit:
   ```bash
   ./scripts/check.sh        # byte-compile · core import · full import (if MLX) · JS syntax · web smoke
   ```
   For web changes, also run the headless harness: `npm i --no-save jsdom && node tests/smoke_web.mjs`.
4. **Commit** with a conventional, imperative subject and the trailer:
   ```
   feat: add inline diff to the apply flow

   Co-Authored-By: <operative or author>
   ```
   Types: `feat` `fix` `refactor` `chore` `docs` `test` `perf`.
5. **Open a PR** into `main` (the template prompts for verification + scope). CI
   (`.github/workflows/ci.yml`, macOS) runs the same checks; keep it green.
6. **Merge** once CI is green and the PR is reviewed.

## What runs where (after a change)

| Layer | File(s) | To take effect |
|------|---------|----------------|
| Web UI | `static/index.html` | reload the page (served from disk per request) |
| Server | `server/*.py` | restart `uvicorn` (the app's ServerManager does this on relaunch) |
| Native macOS | `Scroll/Sources/**` | rebuild the `.app` (`Scroll/build.sh`, needs full Xcode) |

## Safety rails for self-improvement

- The agent works on a **branch** and must run `./scripts/check.sh` before proposing the change.
- Apply writes go through the artifact card; snapshot to git before large edits.
- Never weaken the privacy posture (100% local by default; cloud only on explicit escalate).
- Keep changes reversible: small commits, green CI, a PR for review.
