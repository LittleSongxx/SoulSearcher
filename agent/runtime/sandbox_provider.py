"""DeerFlow-aligned SandboxProvider abstraction.

Unifies E2B and Daytona sandbox backends behind a common interface.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from common.config import settings

logger = logging.getLogger(__name__)


class SandboxProvider(ABC):
    """Abstract sandbox provider with acquire/get/release lifecycle."""

    @abstractmethod
    def acquire(self, thread_id: str | None = None) -> str:
        """Acquire a sandbox and return its ID."""
        ...

    @abstractmethod
    def get(self, sandbox_id: str):
        """Get the sandbox instance for an ID."""
        ...

    @abstractmethod
    def release(self, sandbox_id: str) -> None:
        """Release a sandbox."""
        ...

    def reset(self) -> None:
        """Clear cached state."""
        pass


class E2BSandboxProvider(SandboxProvider):
    """E2B remote sandbox provider."""

    def __init__(self):
        self._sandboxes: dict[str, Any] = {}
        self._e2b = None

    def _get_e2b(self):
        if self._e2b is None:
            import importlib
            try:
                self._e2b = importlib.import_module("e2b")
            except ImportError:
                raise RuntimeError("E2B SDK not installed. Run: pip install e2b")
        return self._e2b

    def acquire(self, thread_id: str | None = None) -> str:
        sandbox_id = thread_id or "default"
        if sandbox_id not in self._sandboxes:
            logger.info("E2BSandboxProvider: creating sandbox %s", sandbox_id)
            self._sandboxes[sandbox_id] = None  # Lazy init
        return sandbox_id

    def get(self, sandbox_id: str):
        return self._sandboxes.get(sandbox_id)

    def release(self, sandbox_id: str) -> None:
        if sandbox_id in self._sandboxes:
            del self._sandboxes[sandbox_id]


class DaytonaSandboxProvider(SandboxProvider):
    """Daytona remote sandbox provider."""

    def __init__(self):
        self._sandboxes: dict[str, Any] = {}

    def acquire(self, thread_id: str | None = None) -> str:
        sandbox_id = thread_id or "default"
        if sandbox_id not in self._sandboxes:
            logger.info("DaytonaSandboxProvider: acquiring sandbox %s", sandbox_id)
            self._sandboxes[sandbox_id] = None
        return sandbox_id

    def get(self, sandbox_id: str):
        return self._sandboxes.get(sandbox_id)

    def release(self, sandbox_id: str) -> None:
        if sandbox_id in self._sandboxes:
            del self._sandboxes[sandbox_id]


_default_provider: SandboxProvider | None = None


def get_sandbox_provider() -> SandboxProvider:
    global _default_provider
    if _default_provider is None:
        mode = getattr(settings, "sandbox_mode", "e2b")
        if mode in ("daytona",):
            _default_provider = DaytonaSandboxProvider()
        else:
            _default_provider = E2BSandboxProvider()
    return _default_provider


def reset_sandbox_provider() -> None:
    global _default_provider
    if _default_provider is not None:
        _default_provider.reset()
        _default_provider = None
