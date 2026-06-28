import shlex
import subprocess
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

from agent.runtime.sandbox_policy import (
    assess_shell_command,
)

SAFE_DEFAULT_CWD = Path(".")
DEFAULT_TIMEOUT = 20  # seconds


@tool
def safe_bash(cmd: str, cwd: Optional[str] = None, timeout: int = DEFAULT_TIMEOUT) -> str:
    """
    Run a shell command with security guard (shares sandbox_policy's command denylist).

    Args:
        cmd: command string (must pass sandbox policy assessment)
        cwd: optional working directory (default repo root)
        timeout: seconds before kill
    """
    allowed, reason = assess_shell_command(cmd)
    if not allowed:
        return f"Error: {reason}"

    workdir = Path(cwd).resolve() if cwd else SAFE_DEFAULT_CWD.resolve()
    try:
        parts = shlex.split(cmd)
    except ValueError as exc:
        return f"Error: invalid shell syntax: {exc}"

    try:
        result = subprocess.run(
            parts,
            shell=False,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        if result.returncode != 0:
            return f"[exit {result.returncode}] {out}\n{err}"
        return out or "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: command timed out"
    except FileNotFoundError:
        return f"Error: command not found: {parts[0] if parts else cmd}"
    except PermissionError as e:
        return f"Error: permission denied: {e}"
    except Exception as e:
        return f"Error: {e}"
