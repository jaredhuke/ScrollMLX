<!-- Keep PRs small and single-purpose. -->

## What & why
<!-- One or two sentences: what this changes and the reason. -->

## How it was verified
- [ ] `./scripts/check.sh` passes locally
- [ ] Web change verified headless (jsdom) or noted N/A
- [ ] Server change: `server/*.py` restarts cleanly; new endpoints curl-checked
- [ ] Native (Swift) change: parses clean; noted that it needs an app rebuild

## Scope check
- [ ] Honors the UX laws (visual/object-oriented, ch'an-calm, B&W + red/orange accents, color for actionable only)
- [ ] No new monospace outside code blocks
- [ ] No silent failures — errors surface clearly to the user

## Notes
<!-- Anything reviewers should know: trade-offs, follow-ups, screenshots. -->
