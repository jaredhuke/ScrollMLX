# Scroll

100% local coding agent powered by [mlx-lm](https://github.com/ml-explore/mlx-lm) on Apple Silicon.  
Default model: **Qwen2.5-Coder-32B-Instruct** at 4-bit (~19 GB, fits 48 GB M4 Pro).

## Setup

```bash
cd ~/scroll
./install.sh
```

The installer will:
1. Install `uv` and Python deps
2. Offer to download the model (~19 GB)
3. Generate the Xcode project via XcodeGen

## Running

### macOS App (recommended)

```bash
open Scroll/Scroll.xcodeproj
# Build & Run — the app launches the server automatically
```

### CLI + Server (two terminals)

**Terminal 1 — inference server:**
```bash
uv run uvicorn server.main:app --port 8080
```

**Terminal 2 — interactive REPL:**
```bash
uv run python cli.py --cwd /path/to/your/project
```

## Configuration

| Env var | Default | Description |
|---|---|---|
| `MLX_MODEL` | `mlx-community/Qwen2.5-Coder-32B-Instruct-4bit` | HuggingFace model path |
| `MLX_PORT` | `8080` | Server port |
| `MLX_MAX_TOKENS` | `8192` | Max tokens per generation |
| `MLX_TEMPERATURE` | `0.15` | Sampling temperature |
| `MLX_SHELL_TIMEOUT` | `60` | Shell command timeout (seconds) |

### Smaller/faster models

```bash
# 7B — fast, ~4 GB
MLX_MODEL=mlx-community/Qwen2.5-Coder-7B-Instruct-4bit uv run uvicorn server.main:app --port 8080

# 14B — good balance
MLX_MODEL=mlx-community/Qwen2.5-Coder-14B-Instruct-4bit uv run uvicorn server.main:app --port 8080
```

## CLI commands

| Command | Action |
|---|---|
| `/clear` | Clear conversation history |
| `/cwd <path>` | Change working directory |
| `/model` | Show loaded model info |
| `/quit` | Exit |

## Agent tools

- `read_file` — read file contents (with optional line range)
- `write_file` — write/create files
- `list_dir` — list directory contents
- `run_command` — execute shell commands (tests, builds, git, etc.)
- `grep_codebase` — search with regex across files
- `find_files` — find files by name pattern
- `fetch_url` — fetch documentation or raw files from URLs

## Project structure

```
scroll/
├── server/
│   ├── main.py          # FastAPI server (SSE streaming)
│   ├── agent.py         # Agent loop + tool calling
│   ├── config.py        # Configuration
│   ├── schemas.py       # Pydantic event types
│   └── tools/           # Tool implementations
├── cli.py               # Interactive REPL
├── Scroll/            # SwiftUI macOS app
│   ├── project.yml      # XcodeGen spec
│   └── Sources/Scroll/
│       ├── AppState.swift
│       ├── Services/    # ServerManager, APIClient
│       └── Views/       # ChatView, ToolCallView, etc.
└── install.sh
```
