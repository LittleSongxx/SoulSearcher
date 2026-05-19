from __future__ import annotations

import logging
import re
import shlex
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from common.config import settings

logger = logging.getLogger(__name__)

from agent.workflows.constants import DANGEROUS_COMMANDS as _DANGEROUS_COMMANDS, NETCAT_ALIASES as _NETCAT_ALIASES

_E2B_MODES = {"e2b", "local", "remote", "e2b_remote"}
_PIPE_TO_SHELL_RE = re.compile(r"(curl|wget)\b.*\|\s*(bash|sh|python|zsh)", re.I)


@dataclass(slots=True)
class SandboxAuditRecord:
    tool: str
    action: str
    thread_id: str
    allowed: bool
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_event(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "action": self.action,
            "thread_id": self.thread_id,
            "allowed": self.allowed,
            "reason": self.reason,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


def normalize_sandbox_mode(value: str | None = None) -> str:
    raw = (value if value is not None else settings.sandbox_mode or "").strip().lower()
    if raw in _E2B_MODES:
        return "e2b"
    if raw == "daytona":
        return "daytona"
    if raw in {"none", "disabled", "off"}:
        return "none"
    return "e2b"


def e2b_sandbox_enabled() -> bool:
    return normalize_sandbox_mode() == "e2b"


def daytona_sandbox_enabled() -> bool:
    return normalize_sandbox_mode() == "daytona"


def host_bash_enabled() -> bool:
    return bool(getattr(settings, "host_bash_enabled", False))


def should_expose_host_bash() -> bool:
    """Host shell is opt-in only; remote sandbox is the default execution surface."""
    return host_bash_enabled() and normalize_sandbox_mode() != "none"


def assess_shell_command(command: str) -> tuple[bool, str]:
    text = str(command or "").strip()
    if not text:
        return False, "empty command"
    if _PIPE_TO_SHELL_RE.search(text):
        return False, "pipe-to-shell pattern is denied"
    try:
        parts = shlex.split(text)
    except ValueError as exc:
        return False, f"invalid shell syntax: {exc}"
    for part in parts:
        base = part.rsplit("/", 1)[-1]
        if base in _DANGEROUS_COMMANDS:
            return False, f"command '{base}' requires explicit elevated policy"
        if base in _NETCAT_ALIASES:
            return False, f"command '{base}' is blocked (network utility)"
    return True, "allowed"


def audit(tool: str, action: str, thread_id: str, allowed: bool, reason: str = "", **metadata: Any) -> SandboxAuditRecord:
    record = SandboxAuditRecord(
        tool=tool,
        action=action,
        thread_id=thread_id,
        allowed=allowed,
        reason=reason,
        metadata=metadata,
    )
    log = logger.info if allowed else logger.warning
    log("[sandbox_audit] %s", record.to_event())
    return record
