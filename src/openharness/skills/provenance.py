"""Write-origin tracking for skill mutations.

The background review fork sets the origin so ``skill_manage`` can mark its
creations as agent-created (the only skills the curator may later touch) and
the approval gate can distinguish background from foreground writes.
"""

from __future__ import annotations

from contextvars import ContextVar

_ORIGIN: ContextVar[str] = ContextVar("skill_write_origin", default="foreground")


def current_origin() -> str:
    return _ORIGIN.get()


def set_origin(origin: str):
    """Set the origin for the current context; returns the reset token."""
    return _ORIGIN.set(origin)


def reset_origin(token) -> None:
    _ORIGIN.reset(token)


def is_background_review() -> bool:
    return _ORIGIN.get() == "background_review"
