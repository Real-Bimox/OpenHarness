"""Diagnostic event schema, attrs allowlists, and redaction.

Spec: docs/proposals/observability-metrics.md §1. Every event is a compact
JSON object with bounded enums and component-allowlisted attrs; free-form
payloads are structurally impossible — unknown attr keys are dropped (and
counted) at build time, and error message previews pass through the shared
secret redaction before being capped.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1

COMPONENTS = frozenset(
    {
        "startup",
        "headless",
        "print",
        "ui",
        "mcp",
        "engine",
        "api",
        "tool",
        "permission",
        "storage",
        "index",
        "memory",
        "skill",
        "cron",
        "autopilot",
        "task",
        "swarm",
        "channel",
        "gateway",
        "diagnostics",
    }
)

EVENTS = frozenset(
    {
        "started",
        "completed",
        "failed",
        "cancelled",
        "timeout",
        "retry",
        "fallback",
        "denied",
        "dropped",
        "heartbeat",
        "snapshot",
    }
)

_COMMON_ATTRS = frozenset({"mode", "status", "reason"})

# Per-component attrs allowlist (proposal §1: "attrs must be allowlisted by
# component; free-form raw payloads are not allowed").
ATTRS_ALLOWLIST: dict[str, frozenset[str]] = {
    "startup": _COMMON_ATTRS | frozenset({"version", "python", "platform", "probe"}),
    "headless": _COMMON_ATTRS
    | frozenset({"request_type", "event_type", "queue_depth", "active", "correlation_id"}),
    "print": _COMMON_ATTRS | frozenset({"output_format"}),
    "ui": _COMMON_ATTRS,
    "mcp": _COMMON_ATTRS | frozenset({"tool_name"}),
    "engine": _COMMON_ATTRS | frozenset({"model", "message_count", "model_turn_count"}),
    "api": _COMMON_ATTRS
    | frozenset(
        {
            "provider",
            "api_format",
            "model",
            "status_code",
            "retryable",
            "from_model",
            "to_model",
            "request_message_count",
            "tool_schema_count",
            "max_tokens_effective",
        }
    ),
    "tool": _COMMON_ATTRS
    | frozenset({"tool_name", "output_chars", "offloaded", "arg_count", "input_size_chars"}),
    "permission": _COMMON_ATTRS | frozenset({"tool_name"}),
    "storage": _COMMON_ATTRS | frozenset({"app", "size_bytes", "message_count"}),
    "index": _COMMON_ATTRS | frozenset({"fts_enabled", "hits", "operation_kind", "db_size_bytes"}),
    "memory": _COMMON_ATTRS,
    "skill": _COMMON_ATTRS | frozenset({"skill_name", "action"}),
    "cron": _COMMON_ATTRS | frozenset({"job_name", "exit_code"}),
    "autopilot": _COMMON_ATTRS | frozenset({"card_count"}),
    "task": _COMMON_ATTRS | frozenset({"backend"}),
    "swarm": _COMMON_ATTRS | frozenset({"backend", "agent_count"}),
    "channel": _COMMON_ATTRS | frozenset({"channel", "message_type", "media_type", "decision"}),
    "gateway": _COMMON_ATTRS | frozenset({"queue", "session_count"}),
    "diagnostics": _COMMON_ATTRS
    | frozenset({"file", "rule", "events_dropped", "operation_age_ms", "probe"}),
}

_ERROR_PREVIEW_CAP = 200

_PROCESS_T0 = time.monotonic()


def redact_preview(text: str) -> str:
    """Secret-redacted, capped error preview (never raw provider bodies)."""
    from openharness.memory.team import SECRET_RULES

    for rule_id, _label, pattern in SECRET_RULES:
        text = pattern.sub(f"[redacted:{rule_id}]", text)
    return text[:_ERROR_PREVIEW_CAP]


def build_error(exc_or_message: BaseException | str, *, reason: str | None = None, status_code: int | None = None) -> dict[str, Any]:
    """Build the bounded error sub-object."""
    if isinstance(exc_or_message, BaseException):
        return {
            "type": exc_or_message.__class__.__name__,
            "reason": reason,
            "message_preview": redact_preview(str(exc_or_message)),
            "status_code": status_code,
        }
    return {
        "type": "error",
        "reason": reason,
        "message_preview": redact_preview(exc_or_message),
        "status_code": status_code,
    }


def build_event(
    *,
    component: str,
    operation: str,
    event: str,
    level: str = "info",
    status: str = "ok",
    duration_ms: float | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
    turn_id: str | None = None,
    api_call_id: str | None = None,
    tool_use_id: str | None = None,
    task_id: str | None = None,
    attrs: dict[str, Any] | None = None,
    counters: dict[str, int | float] | None = None,
    error: dict[str, Any] | None = None,
    pid: int | None = None,
) -> tuple[dict[str, Any], int]:
    """Build one schema-conformant event dict.

    Returns ``(event_dict, dropped_attr_count)`` — non-allowlisted attrs are
    removed, never stored.
    """
    if component not in COMPONENTS:
        component = "diagnostics"
    if event not in EVENTS:
        event = "completed"
    allow = ATTRS_ALLOWLIST.get(component, _COMMON_ATTRS)
    clean_attrs: dict[str, Any] = {}
    dropped = 0
    for key, value in (attrs or {}).items():
        if key in allow and (value is None or isinstance(value, (str, int, float, bool))):
            clean_attrs[key] = value
        else:
            dropped += 1
    now = datetime.now(tz=timezone.utc)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ts": now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
        "monotonic_ms": round((time.monotonic() - _PROCESS_T0) * 1000.0, 2),
        "run_id": run_id,
        "pid": pid,
        "component": component,
        "operation": operation,
        "event": event,
        "level": level,
        "status": status,
        "duration_ms": round(duration_ms, 2) if duration_ms is not None else None,
        "session_id": session_id,
        "request_id": request_id,
        "turn_id": turn_id,
        "api_call_id": api_call_id,
        "tool_use_id": tool_use_id,
        "task_id": task_id,
        "attrs": clean_attrs,
        "counters": dict(counters or {}),
        "error": error,
    }
    return payload, dropped
