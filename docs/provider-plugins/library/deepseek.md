---
type: provider
id: deepseek
name: DeepSeek · V3
kind: http
endpoint: https://api.deepseek.com/chat/completions
model: deepseek-chat
secret_env: DEEPSEEK_API_KEY
---

# DeepSeek · V3

DeepSeek V3 — open-source, excellent at code + reasoning (use deepseek-reasoner for R1).

OpenAI-compatible chat endpoint. Add your API key once (stored in the macOS Keychain, never in
this file) — get one at https://platform.deepseek.com. Then it shows up everywhere a built-in operative does: the model
picker, an `@`-target, and as an Escalate option.
