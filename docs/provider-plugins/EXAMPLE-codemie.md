---
type: provider
id: codemie-plugin
name: CodeMie · Claude (plugin)
kind: command
command: codemie-claude -p {prompt}
secret_env: CODEMIE_TOKEN
---

# Provider plugin example — CodeMie (command kind)

This is a **droppable provider plugin**: one markdown file with a frontmatter block.
Drop it into Scroll (Skills & Plugins → *Add a provider plugin* → paste this file's path),
add your password if it asks, and it shows up as a cloud operative — in the model picker,
as an `@`-target, and as an Escalate option. No code, no API key in the file.

## Frontmatter fields
- `type: provider` (required)
- `id:` short unique id (required) — also the keychain account for the secret
- `name:` what's shown in the UI
- `kind: command` — Scroll runs `command`, substituting `{prompt}` (or appends the prompt
  if `{prompt}` is absent), in a throwaway temp dir, and streams stdout back.
- `kind: http` — Scroll POSTs an OpenAI-style body to `endpoint:` with
  `Authorization: Bearer <secret>` and reads `choices[0].message.content`. Needs `model:`.
- `secret_env:` (optional) the "password". You add it once in the app; it's stored in the
  macOS Keychain (never in this file) and injected into the command's env / the Bearer header.

## HTTP example
```
---
type: provider
id: myapi
name: My API · GPT-4o
kind: http
endpoint: https://api.example.com/v1/chat/completions
model: gpt-4o
secret_env: MYAPI_KEY
---
```
