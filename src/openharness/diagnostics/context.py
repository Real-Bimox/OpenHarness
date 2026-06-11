"""Correlation-ID propagation for diagnostics.

One ``run_id`` per process; ``turn_id``/``request_id``/``correlation_id``
flow through contextvars so instrumentation sites never thread IDs manually.
"""

from __future__ import annotations

import os
import time
import uuid
from contextvars import ContextVar

_RUN_ID: str | None = None

turn_id_var: ContextVar[str | None] = ContextVar("diag_turn_id", default=None)
request_id_var: ContextVar[str | None] = ContextVar("diag_request_id", default=None)
correlation_id_var: ContextVar[str | None] = ContextVar("diag_correlation_id", default=None)
session_id_var: ContextVar[str | None] = ContextVar("diag_session_id", default=None)


def run_id() -> str:
    """Process-wide run id, minted on first use."""
    global _RUN_ID
    if _RUN_ID is None:
        _RUN_ID = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    return _RUN_ID


def pid() -> int:
    return os.getpid()


def new_turn_id(counter: int) -> str:
    return f"turn-{counter:04d}"
