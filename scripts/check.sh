#!/usr/bin/env bash
# Scroll's one-command check — mirrors CI. Run before every push.
#   ./scripts/check.sh
# Safe to run anywhere: the heavy MLX import is only attempted when MLX is installed.
set -euo pipefail
cd "$(dirname "$0")/.."

py() { if command -v uv >/dev/null 2>&1; then uv run python "$@"; else python3 "$@"; fi; }

echo "▸ Python byte-compile (server + cli)"
py -m py_compile server/*.py server/tools/*.py cli.py relay.py 2>/dev/null || py -m py_compile server/*.py server/tools/*.py

echo "▸ Core modules import (no MLX required)"
py -c "import server.config, server.models, server.skills, server.steering, server.projects, server.secrets, server.ledger, server.learn; print('  core ok')"

echo "▸ Full server import (only if MLX is present)"
py - <<'PY'
import importlib.util as u
if u.find_spec("mlx_lm"):
    import server.main  # noqa: F401
    print("  server.main ok")
else:
    print("  skipped (mlx_lm not installed)")
PY

echo "▸ Web UI JavaScript syntax"
py - <<'PY' > /tmp/scroll_inline.js
import re; print(re.findall(r'<script(?![^>]*src=)[^>]*>(.*?)</script>', open('static/index.html').read(), re.S)[0])
PY
node --check /tmp/scroll_inline.js && echo "  js ok"

echo "▸ Web UI headless smoke (only if jsdom is available)"
if [ -f tests/smoke_web.mjs ] && node -e "require.resolve('jsdom')" >/dev/null 2>&1; then
  node tests/smoke_web.mjs
else
  echo "  skipped (run: npm i --no-save jsdom)"
fi

echo "✓ all checks passed"
