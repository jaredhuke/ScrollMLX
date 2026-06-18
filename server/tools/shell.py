import subprocess
import shlex
from server.config import SHELL_TIMEOUT, SHELL_BLOCKED
from server.env import login_env


def run_command(command: str, cwd: str, timeout: int | None = None) -> str:
    timeout = timeout or SHELL_TIMEOUT

    # Naive safety check against obviously destructive patterns
    for blocked in SHELL_BLOCKED:
        if blocked in command:
            return f"BLOCKED: command matches dangerous pattern '{blocked}'"

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=login_env(),  # GUI-app PATH is minimal — use the Terminal.app PATH so npm/node/git resolve
        )
        out = result.stdout
        err = result.stderr
        parts = []
        if out:
            parts.append(out)
        if err:
            parts.append(f"[stderr]\n{err}")
        if result.returncode != 0:
            parts.append(f"[exit code {result.returncode}]")
        return "\n".join(parts) or "(no output)"
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {timeout}s"
    except Exception as e:
        return f"ERROR: {e}"
