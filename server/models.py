"""One-click local model install — download MLX weights with live progress.

Powers the onboarding "Download & install" step. The download runs in a daemon
thread (huggingface_hub.snapshot_download); progress is estimated from the blob
cache size vs a known one-time size. Pre-downloading populates the same cache
mlx_lm.load() uses, so a later load() finds the weights already present.
"""
from __future__ import annotations

import threading
from pathlib import Path

from server import config

# size_gb = one-time download size of the 4-bit MLX weights (approximate, offline-safe).
CATALOG = [
    {"slot": "primary", "repo": config.MODEL, "label": "Qwen2.5-Coder 32B", "size_gb": 18.0, "role": "Author — all coding"},
    {"slot": "critic", "repo": config.CRITIC_MODEL, "label": "Qwen2.5-Coder 7B", "size_gb": 4.3, "role": "Critic — reviews replies"},
]
_SIZE = {m["repo"]: m["size_gb"] for m in CATALOG}
_state: dict[str, dict] = {}
_lock = threading.Lock()


def _cache_dir(repo: str) -> Path:
    from huggingface_hub.constants import HF_HUB_CACHE
    return Path(HF_HUB_CACHE) / ("models--" + repo.replace("/", "--"))


def _dir_size(p: Path) -> int:
    if not p.exists():
        return 0
    try:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except Exception:
        return 0


def is_cached(repo: str) -> bool:
    """True once the weights + config are fully present in the HF cache."""
    snaps = _cache_dir(repo) / "snapshots"
    if not snaps.exists():
        return False
    try:
        has_w = any(snaps.rglob("*.safetensors"))
        has_c = any(snaps.rglob("config.json"))
        return has_w and has_c
    except Exception:
        return False


def _total_bytes(repo: str) -> int:
    return int(_SIZE.get(repo, 8.0) * 1_000_000_000)


def start_download(repo: str) -> dict:
    """Kick off a background download (idempotent while one is running)."""
    with _lock:
        st = _state.get(repo)
        if st and st.get("status") == "downloading":
            return st
        _state[repo] = {"status": "downloading", "total": _total_bytes(repo), "error": None}

    def _run() -> None:
        try:
            from huggingface_hub import snapshot_download
            snapshot_download(repo_id=repo)
            with _lock:
                _state[repo] = {"status": "done", "total": _total_bytes(repo), "error": None}
        except Exception as e:  # noqa: BLE001
            with _lock:
                _state[repo] = {"status": "error", "total": _total_bytes(repo), "error": str(e)[:240]}

    threading.Thread(target=_run, daemon=True, name=f"dl-{repo[:24]}").start()
    return _state[repo]


def progress(repo: str) -> dict:
    total = _total_bytes(repo)
    tg = round(total / 1e9, 1)
    if is_cached(repo):
        return {"repo": repo, "status": "done", "pct": 100, "downloaded_gb": tg, "total_gb": tg}
    st = _state.get(repo)
    if not st:
        return {"repo": repo, "status": "idle", "pct": 0, "downloaded_gb": 0, "total_gb": tg}
    if st.get("status") == "error":
        return {"repo": repo, "status": "error", "pct": 0, "error": st.get("error"), "total_gb": tg}
    if st.get("status") == "done":
        return {"repo": repo, "status": "done", "pct": 100, "downloaded_gb": tg, "total_gb": tg}
    dl = _dir_size(_cache_dir(repo) / "blobs")
    pct = min(int(dl / max(total, 1) * 100), 99)
    return {"repo": repo, "status": "downloading", "pct": pct, "downloaded_gb": round(dl / 1e9, 2), "total_gb": tg}


def catalog() -> list[dict]:
    return [{**m, "cached": is_cached(m["repo"])} for m in CATALOG]


def recommended(ram_gb: int | None) -> str:
    """The primary repo that fits this machine."""
    return config.MODEL if (ram_gb or 16) >= 24 else config.CRITIC_MODEL
