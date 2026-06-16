# Drive Scroll from your phone (git relay)

Run Scroll on your Mac and dispatch work to it from your phone, using a small
GitHub repo as the relay. No inbound network, no ports open to the internet — the
Mac only ever *pulls and pushes* a git repo.

```
phone ──commit prompt──▶  GitHub repo  ◀──poll / push answer──  Mac (relay.py → local model)
   ▲                         (inbox/ , outbox/)                         │
   └────────── commit notification when the answer lands ──────────────┘
```

## One-time setup

1. **Create a small private repo** on GitHub, e.g. `scroll-relay`.
2. **Clone it on your Mac:**
   ```
   git clone git@github.com:<you>/scroll-relay.git ~/.scroll/relay
   ```
3. **Start the Scroll server** (or the macOS app), then **start the relay:**
   ```
   cd ~/scroll && uv run python relay.py        # repo=~/.scroll/relay, port=8080, every 20s
   ```
4. **On your phone:** install the GitHub mobile app, open the repo, and tap **Watch → All Activity** so each answer commit sends a push notification.

## Using it from your phone

- In the GitHub app, **add a file** under `inbox/`, e.g. `inbox/fix-login.md`, with your prompt as the contents. Commit it.
- The Mac picks it up within the poll interval, runs it through your local model, writes the answer to `outbox/fix-login.md`, removes the inbox file, and pushes.
- You get a **commit notification**; open `outbox/fix-login.md` to read the answer.

### Targeting a project
Make the first line a directive:
```
cwd: /Users/you/code/myapp
Review the auth flow and list the top 3 risks.
```

## Notes
- The relay talks to the **already-running** local server (`/v1/agent`), so the model loads once. Keep the server (or the macOS app) running.
- Answers include a short `steps:` trailer listing the tools the agent used.
- `uv run python relay.py --once` processes the inbox a single time (handy for cron).
- Security: the relay repo holds your prompts and answers — keep it **private**. Code never leaves your Mac unless a prompt asks the agent to put it in the answer.
