#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}▸${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}!${NC} $*"; }

echo ""
echo "  Scroll — installer"
echo ""

# ── 1. uv ──────────────────────────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
    info "Installing uv…"
    brew install uv
fi
ok "uv $(uv --version)"

# ── 2. Python deps ─────────────────────────────────────────────────────────
info "Installing Python dependencies…"
uv sync
ok "Python environment ready at .venv/"

# ── 3. Model download ───────────────────────────────────────────────────────
MODEL="${MLX_MODEL:-mlx-community/Qwen2.5-Coder-32B-Instruct-4bit}"
MODEL_CACHE="$HOME/.cache/huggingface/hub"

info "Checking model: $MODEL"
if [ -d "$MODEL_CACHE/models--$(echo "$MODEL" | tr '/' '--')" ]; then
    ok "Model already cached."
else
    warn "Model not found locally. Downloading ~19 GB — this will take a while."
    warn "Tip: set MLX_MODEL=mlx-community/Qwen2.5-Coder-7B-Instruct-4bit for a faster ~4 GB download."
    echo ""
    read -p "Download $MODEL now? [y/N] " -n 1 -r; echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        uv run python -c "from mlx_lm import load; load('$MODEL')"
        ok "Model downloaded."
    else
        warn "Skipping download. Set MLX_MODEL env var to choose a model."
    fi
fi

# ── 4. XcodeGen + Xcode project ────────────────────────────────────────────
if [ -d "Scroll" ]; then
    info "Generating Xcode project…"
    if ! command -v xcodegen &>/dev/null; then
        brew install xcodegen
    fi
    (cd Scroll && xcodegen generate --spec project.yml)
    ok "Xcode project generated at Scroll/Scroll.xcodeproj"
fi

# ── 5. pyproject.toml entry points ─────────────────────────────────────────
echo ""
echo -e "${GREEN}Done.${NC} Next steps:"
echo ""
echo "  Start the inference server:"
echo "    uv run uvicorn server.main:app --port 8080"
echo ""
echo "  Run the CLI (in a new terminal):"
echo "    uv run python cli.py --cwd /path/to/your/project"
echo ""
echo "  Open the macOS app:"
echo "    open Scroll/Scroll.xcodeproj"
echo "    (Build & Run in Xcode — the app auto-starts the server)"
echo ""
