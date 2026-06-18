---
type: provider
id: qwen-coder
name: Qwen2.5-Coder (OpenRouter)
kind: http
endpoint: https://openrouter.ai/api/v1/chat/completions
model: qwen/qwen-2.5-coder-32b-instruct
secret_env: OPENROUTER_API_KEY
---

# Qwen2.5-Coder (OpenRouter)

Qwen2.5-Coder 32B via OpenRouter — the same family as Scroll's local model, in the cloud.

OpenAI-compatible chat endpoint. Add your API key once (stored in the macOS Keychain, never in
this file) — get one at https://openrouter.ai/keys. Then it shows up everywhere a built-in operative does: the model
picker, an `@`-target, and as an Escalate option.
