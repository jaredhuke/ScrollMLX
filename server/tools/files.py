"""Extra file skills: edit-in-place, append, structured data, PDF."""
from __future__ import annotations

import csv
import io
import json

from .filesystem import _resolve


def edit_file(path: str, find: str, replace: str, cwd: str) -> str:
    """Replace exact text in a file (all occurrences). Safer than rewriting the whole file."""
    p = _resolve(path, cwd)
    if not p.exists():
        return f"ERROR: {path} does not exist"
    text = p.read_text(encoding="utf-8", errors="replace")
    n = text.count(find)
    if n == 0:
        return f"ERROR: text not found in {path} (nothing changed)"
    p.write_text(text.replace(find, replace), encoding="utf-8")
    return f"Edited {path} — replaced {n} occurrence{'s' if n != 1 else ''}."


def append_file(path: str, content: str, cwd: str) -> str:
    """Append content to the end of a file (creates it if missing)."""
    p = _resolve(path, cwd)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(content)
    return f"Appended {len(content)} chars to {path}."


def read_data(path: str, cwd: str, max_rows: int = 20) -> str:
    """Read structured data (CSV/TSV/JSON/JSONL) and return a compact summary + sample."""
    p = _resolve(path, cwd)
    if not p.exists():
        return f"ERROR: {path} does not exist"
    from .media import describe_media  # image/binary guard
    media = describe_media(p)
    if media:
        return media
    ext = p.suffix.lower()
    raw = p.read_text(encoding="utf-8", errors="replace")
    try:
        if ext == ".json":
            data = json.loads(raw)
            if isinstance(data, list):
                head = json.dumps(data[:5], indent=2)[:2000]
                return f"JSON array · {len(data)} items\nFirst items:\n{head}"
            if isinstance(data, dict):
                keys = list(data.keys())
                return f"JSON object · keys: {keys}\n{json.dumps(data, indent=2)[:2000]}"
            return f"JSON value: {str(data)[:500]}"
        if ext == ".jsonl":
            lines = [l for l in raw.splitlines() if l.strip()]
            sample = "\n".join(lines[:5])[:2000]
            return f"JSONL · {len(lines)} records\nFirst records:\n{sample}"
        if ext in (".csv", ".tsv"):
            delim = "\t" if ext == ".tsv" else ","
            rdr = csv.reader(io.StringIO(raw), delimiter=delim)
            rows = list(rdr)
            if not rows:
                return f"{ext} file is empty"
            header, body = rows[0], rows[1:]
            preview = "\n".join(delim.join(r) for r in body[:max_rows])
            return f"{ext.upper()} · {len(body)} rows · columns: {header}\nFirst {min(max_rows, len(body))} rows:\n{preview}"
        return f"Unsupported data type {ext}. Use read_file for plain text."
    except Exception as exc:
        return f"ERROR parsing {path}: {exc}"


def read_pdf(path: str, cwd: str, max_chars: int = 8000) -> str:
    """Extract text from a PDF. Needs pypdf (uv add pypdf) — degrades gracefully if absent."""
    p = _resolve(path, cwd)
    if not p.exists():
        return f"ERROR: {path} does not exist"
    try:
        from pypdf import PdfReader  # noqa: PLC0415
    except ImportError:
        return "ERROR: pypdf not installed. Run: uv add pypdf"
    try:
        reader = PdfReader(str(p))
        parts = []
        for i, page in enumerate(reader.pages):
            parts.append(page.extract_text() or "")
            if sum(len(x) for x in parts) > max_chars:
                break
        text = "\n".join(parts)[:max_chars]
        return f"PDF · {len(reader.pages)} pages\n{text}"
    except Exception as exc:
        return f"ERROR reading PDF: {exc}"
