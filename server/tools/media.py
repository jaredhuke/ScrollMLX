"""
Media detection so the agent never dumps binary into the model.

The local model (Qwen2.5-Coder) is text-only. When a tool is pointed at an image
or other binary, we return a short human descriptor — type, size, and (for images)
dimensions parsed straight from the file header (no Pillow dependency) — plus a
note that viewing it needs a vision model. This is what stops "read foo.jpg" from
streaming 240 KB of mojibake into the context and hanging the run.
"""
from __future__ import annotations

import struct
from pathlib import Path

IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "bmp", "webp", "tiff", "tif", "heic", "heif", "ico", "svg"}
# binary we should never read as text
BINARY_EXTS = {
    "pdf", "zip", "gz", "tar", "tgz", "bz2", "xz", "7z", "rar",
    "mp3", "wav", "flac", "aac", "ogg", "m4a",
    "mp4", "mov", "avi", "mkv", "webm",
    "woff", "woff2", "ttf", "otf", "eot",
    "exe", "dll", "so", "dylib", "bin", "o", "a", "class", "wasm",
    "doc", "docx", "xls", "xlsx", "ppt", "pptx",
    "sqlite", "db", "pyc",
}


def _fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def image_dimensions(p: Path) -> tuple[int, int] | None:
    """Width,height from common image headers — no third-party deps."""
    try:
        with open(p, "rb") as f:
            head = f.read(32)
            if len(head) < 24:
                return None
            # PNG
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                w, h = struct.unpack(">II", head[16:24])
                return int(w), int(h)
            # GIF
            if head[:6] in (b"GIF87a", b"GIF89a"):
                w, h = struct.unpack("<HH", head[6:10])
                return int(w), int(h)
            # BMP
            if head[:2] == b"BM":
                w, h = struct.unpack("<ii", head[18:26])
                return int(w), abs(int(h))
            # JPEG — walk the markers
            if head[:2] == b"\xff\xd8":
                f.seek(2)
                b = f.read(1)
                while b:
                    while b == b"\xff":
                        b = f.read(1)
                    marker = b[0]
                    if 0xC0 <= marker <= 0xC3 or 0xC5 <= marker <= 0xC7 or 0xC9 <= marker <= 0xCB:
                        f.read(3)  # length(2) + precision(1)
                        h, w = struct.unpack(">HH", f.read(4))
                        return int(w), int(h)
                    seg = f.read(2)
                    if len(seg) < 2:
                        break
                    (length,) = struct.unpack(">H", seg)
                    f.seek(length - 2, 1)
                    b = f.read(1)
    except Exception:
        return None
    return None


def describe_media(p: Path) -> str | None:
    """Return a short descriptor if p is an image/binary that must NOT be read as
    text; return None when the file is safe to read as text."""
    ext = p.suffix.lower().lstrip(".")
    try:
        size = p.stat().st_size
    except Exception:
        size = 0
    size_s = _fmt_size(size)

    if ext in IMAGE_EXTS:
        if ext == "svg":  # SVG is text — let it through
            return None
        dims = image_dimensions(p)
        dim_s = f", {dims[0]}×{dims[1]} px" if dims else ""
        return (
            f"[{p.name}] is a {ext.upper()} image ({size_s}{dim_s}).\n"
            "The local model (Qwen2.5-Coder) is text-only and cannot see images. "
            "To actually read this image, use Vision — send it to a multimodal model "
            "(Gemini or GPT-4o) via the image's “Read with vision” action or the "
            "/v1/vision endpoint. Do not attempt to read the raw bytes as text."
        )
    if ext in BINARY_EXTS:
        return (
            f"[{p.name}] is a binary {ext.upper()} file ({size_s}); it is not text and "
            "was not read. Use the matching tool (e.g. read_pdf for PDFs) if one exists."
        )
    return None
