import subprocess
from pathlib import Path


def grep_codebase(
    pattern: str,
    cwd: str,
    path: str | None = None,
    include: str | None = None,
    case_sensitive: bool = True,
    max_results: int = 50,
) -> str:
    search_path = str(Path(path or cwd))
    args = ["grep", "-rn", "--color=never"]
    if not case_sensitive:
        args.append("-i")
    if include:
        args += ["--include", include]
    args += [pattern, search_path]

    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=15)
        lines = result.stdout.strip().splitlines()
        if not lines:
            return "No matches found."
        if len(lines) > max_results:
            lines = lines[:max_results]
            lines.append(f"... (truncated to {max_results} results)")
        return "\n".join(lines)
    except subprocess.TimeoutExpired:
        return "ERROR: grep timed out"
    except Exception as e:
        return f"ERROR: {e}"


def find_files(pattern: str, cwd: str, path: str | None = None) -> str:
    root = Path(path or cwd)
    try:
        matches = sorted(root.rglob(pattern))
        if not matches:
            return "No files found."
        lines = [str(m.relative_to(root)) for m in matches[:100]]
        if len(matches) > 100:
            lines.append(f"... ({len(matches)} total, showing first 100)")
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: {e}"
