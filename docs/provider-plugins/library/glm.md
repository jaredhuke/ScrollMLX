---
type: provider
id: glm
name: GLM · GLM-4.6
kind: http
endpoint: https://open.bigmodel.cn/api/paas/v4/chat/completions
model: glm-4.6
secret_env: ZHIPU_API_KEY
---

# GLM · GLM-4.6

Zhipu AI's GLM-4.6 — strong open-weights bilingual + coding model.

OpenAI-compatible chat endpoint. Add your API key once (stored in the macOS Keychain, never in
this file) — get one at https://open.bigmodel.cn. Then it shows up everywhere a built-in operative does: the model
picker, an `@`-target, and as an Escalate option.
