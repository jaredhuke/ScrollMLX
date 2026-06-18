---
type: provider
id: llama-or
name: Llama 3.3 70B (OpenRouter)
kind: http
endpoint: https://openrouter.ai/api/v1/chat/completions
model: meta-llama/llama-3.3-70b-instruct
secret_env: OPENROUTER_API_KEY
---

# Llama 3.3 70B (OpenRouter)

Meta Llama 3.3 70B via OpenRouter — open-weights, broadly capable.

OpenAI-compatible chat endpoint. Add your API key once (stored in the macOS Keychain, never in
this file) — get one at https://openrouter.ai/keys. Then it shows up everywhere a built-in operative does: the model
picker, an `@`-target, and as an Escalate option.
