---
type: provider
id: kimi
name: Kimi · K2
kind: http
endpoint: https://api.moonshot.cn/v1/chat/completions
model: kimi-k2-0905-preview
secret_env: MOONSHOT_API_KEY
---

# Kimi · K2

Moonshot Kimi K2 — large open-weights MoE, long context.

OpenAI-compatible chat endpoint. Add your API key once (stored in the macOS Keychain, never in
this file) — get one at https://platform.moonshot.cn. Then it shows up everywhere a built-in operative does: the model
picker, an `@`-target, and as an Escalate option.
