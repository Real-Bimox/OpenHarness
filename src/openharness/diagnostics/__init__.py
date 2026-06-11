"""Local-first observability (proposal: observability-metrics)."""

from openharness.diagnostics.recorder import get_recorder, record, reset_recorder
from openharness.diagnostics.schema import build_error

__all__ = ["get_recorder", "record", "reset_recorder", "build_error"]
