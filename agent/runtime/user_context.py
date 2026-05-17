"""User context resolution — determines the effective user_id."""

from __future__ import annotations

import contextvars
import os

DEFAULT_USER_ID = "default"

_effective_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "effective_user_id", default=None
)


def get_effective_user_id() -> str:
    """Return the effective user_id for the current context.

    Checks: context var → env → default
    """
    ctx_val = _effective_user_id.get()
    if ctx_val:
        return ctx_val
    return os.getenv("WEAVER_USER_ID", DEFAULT_USER_ID)


def set_effective_user_id(user_id: str) -> None:
    _effective_user_id.set(user_id)
