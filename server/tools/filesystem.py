from pathlib import Path


def _resolve(path: str, cwd: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = Path(cwd) / p
    return p.resolve()


def read_file(path: str, cwd: str, start_line: int | None = None, end_line: int | None = None) -> str:
    p = _resolve(path, cwd)
    if not p.exists():
        return f"ERROR: file not found: {p}"
    if not p.is_file():
        return f"ERROR: not a file: {p}"
    from server.tools.media import describe_media  # image/binary guard — never dump bytes
    media = describe_media(p)
    if media:
        return media
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"ERROR: {e}"

    if start_line is not None or end_line is not None:
        lines = text.splitlines(keepends=True)
        s = (start_line or 1) - 1
        e = end_line or len(lines)
        lines = lines[s:e]
        text = "".join(lines)
        prefix = f"[lines {s+1}–{min(e, s+len(lines))} of {p}]\n"
    else:
        prefix = f"[{p}]\n"

    return prefix + text


def write_file(path: str, content: str, cwd: str) -> str:
    p = _resolve(path, cwd)
    try:
        # snapshot the prior content first — keeps the real filename, saves a readable vN copy
        try:
            from server import versions
            versions.save_version(p, cwd)
        except Exception:
            pass
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Written {len(content)} bytes to {p}"
    except Exception as e:
        return f"ERROR: {e}"


def list_dir(path: str, cwd: str, recursive: bool = False) -> str:
    p = _resolve(path or ".", cwd)
    if not p.exists():
        return f"ERROR: path not found: {p}"
    try:
        if recursive:
            entries = sorted(p.rglob("*"))
        else:
            entries = sorted(p.iterdir())
        lines = []
        for e in entries:
            rel = e.relative_to(p)
            suffix = "/" if e.is_dir() else ""
            lines.append(f"{rel}{suffix}")
        return "\n".join(lines) or "(empty)"
    except Exception as e:
        return f"ERROR: {e}"
