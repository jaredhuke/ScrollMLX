---
type: provider
id: mistral
name: Mistral · Large
kind: http
endpoint: https://api.mistral.ai/v1/chat/completions
model: mistral-large-latest
secret_env: MISTRAL_API_KEY
---

# Mistral · Large

Mistral Large — open-weights lineage, fast and capable.

OpenAI-compatible chat endpoint. Add your API key once (stored in the macOS Keychain, never in
this file) — get one at https://console.mistral.ai. Then it shows up everywhere a built-in operative does: the model
picker, an `@`-target, and as an Escalate option.
