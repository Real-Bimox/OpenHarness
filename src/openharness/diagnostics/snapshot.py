"""Status, summary, and tail readers over the local diagnostics store.

Spec: docs/proposals/observability-metrics.md §6. ``build_status()`` is the
canonical v1 diagnostics surface shared by ``oh diagnostics status --json``,
the headless ``diagnostics`` request, and the MCP ``diagnostics_status``
tool, so the three surfaces cannot drift. Everything here is read-only and
best-effort: a missing or partially-written store yields empty sections,
never an exception.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

STATUS_SCHEMA_VERSION = 1
_SUMMARY_WINDOW_SECONDS = 3600.0
_RECENT_ERRORS_LIMIT = 10


def diagnostics_dir() -> Path:
    from openharness.config.paths import get_data_dir

    return get_data_dir() / "diagnostics"


def _ts_cutoff(since_seconds: float) -> str:
    """ISO-8601 cutoff string; event ``ts`` values compare lexicographically."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(seconds=since_seconds)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%S.") + f"{cutoff.microsecond // 1000:03d}Z"


def read_events(
    *,
    since_seconds: float | None = None,
    component: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Parsed events (oldest first), filtered by window/component/limit.

    Daily files are scanned newest-first so a bare ``limit`` (tail) never
    reads the whole store.
    """
    events_dir = diagnostics_dir() / "events"
    cutoff_ts = _ts_cutoff(since_seconds) if since_seconds is not None else None
    cutoff_date = cutoff_ts[:10] if cutoff_ts else None
    collected: list[dict[str, Any]] = []
    try:
        files = sorted(events_dir.glob("*.jsonl"), reverse=True)
    except OSError:
        return []
    for path in files:
        if cutoff_date is not None and path.stem < cutoff_date:
            break
        day_events: list[dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if component is not None and event.get("component") != component:
                continue
            if cutoff_ts is not None and str(event.get("ts") or "") < cutoff_ts:
                continue
            day_events.append(event)
        collected = day_events + collected
        if limit is not None and cutoff_ts is None and len(collected) >= limit:
            break
    if limit is not None:
        return collected[-limit:]
    return collected


_TOKEN_COUNTER_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def summarize_events(events: list[dict[str, Any]], *, window_seconds: float | None = None) -> dict[str, Any]:
    """Aggregate counts/durations/token counters over a list of events.

    Token counters sum only ``api.model_call`` completions — the canonical
    per-call source. Turn events carry the same tokens cumulatively, so
    summing every event's counters would double-count.
    """
    by_component: dict[str, dict[str, int]] = {}
    counters: dict[str, float] = {}
    last_turn_ms: float | None = None
    last_api_call_ms: float | None = None
    for event in events:
        comp = str(event.get("component") or "unknown")
        bucket = by_component.setdefault(comp, {"total": 0, "errors": 0})
        bucket["total"] += 1
        if event.get("status") == "error" or event.get("event") == "failed":
            bucket["errors"] += 1
        if comp == "api" and event.get("operation") == "model_call" and event.get("event") == "completed":
            for key in _TOKEN_COUNTER_KEYS:
                value = (event.get("counters") or {}).get(key)
                if isinstance(value, (int, float)):
                    counters[key] = counters.get(key, 0) + value
        duration = event.get("duration_ms")
        if isinstance(duration, (int, float)):
            if comp == "engine" and event.get("operation") == "turn":
                last_turn_ms = float(duration)
            elif comp == "api" and event.get("operation") == "model_call":
                last_api_call_ms = float(duration)
    return {
        "window_seconds": window_seconds,
        "events": len(events),
        "by_component": by_component,
        "counters": counters,
        "last_turn_duration_ms": last_turn_ms,
        "last_api_call_duration_ms": last_api_call_ms,
    }


def recent_errors(events: list[dict[str, Any]], *, limit: int = _RECENT_ERRORS_LIMIT) -> list[dict[str, Any]]:
    """Compact view of the most recent error events (newest last)."""
    errors = [e for e in events if e.get("status") == "error" or e.get("event") == "failed"]
    compact = []
    for event in errors[-limit:]:
        error = event.get("error") or {}
        compact.append(
            {
                "ts": event.get("ts"),
                "component": event.get("component"),
                "operation": event.get("operation"),
                "event": event.get("event"),
                "type": error.get("type"),
                "reason": error.get("reason") or (event.get("attrs") or {}).get("reason"),
                "message_preview": error.get("message_preview"),
            }
        )
    return compact


def thread_probe(timeout: float = 2.0) -> dict[str, Any]:
    """Bounded daemon-thread round-trip; diagnostic only (§ watchdog).

    Deliberately NOT ``asyncio.to_thread``/``run_in_executor``: the default
    executor's workers are non-daemon, so in an environment where thread
    handoff is broken, exactly what this probe detects, a stuck worker
    makes ``asyncio.run()`` hang forever in ``shutdown_default_executor()``
    at teardown, even after a ``wait_for`` timeout. A raw daemon thread can
    time out the same way but can never block process exit.
    """
    import threading

    start = time.perf_counter()
    done = threading.Event()
    try:
        threading.Thread(target=done.set, name="diagnostics-probe", daemon=True).start()
        status = "ok" if done.wait(timeout) else "timeout"
    except Exception:
        status = "failed"
    result = {"status": status, "duration_ms": round((time.perf_counter() - start) * 1000.0, 2)}
    from openharness.diagnostics import record

    record(
        "diagnostics",
        "thread_probe",
        "completed" if status == "ok" else status if status == "timeout" else "failed",
        level="info" if status == "ok" else "warning",
        status="ok" if status == "ok" else "error",
        duration_ms=result["duration_ms"],
        attrs={"probe": "thread_spawn", "status": status},
    )
    return result


def _read_current_run() -> dict[str, Any] | None:
    try:
        raw = (diagnostics_dir() / "current-run.json").read_text(encoding="utf-8")
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (OSError, ValueError):
        return None


def _index_health() -> dict[str, Any]:
    try:
        from openharness.config import load_settings

        if not load_settings().conversation_index_enabled:
            return {"enabled": False}
        from openharness.services.conversation_index import get_conversation_index

        index = get_conversation_index()
        health: dict[str, Any] = {"enabled": True, "fts_enabled": bool(index.fts_enabled)}
        db_path = getattr(index, "db_path", None)
        if db_path is not None:
            try:
                health["db_size_bytes"] = Path(db_path).stat().st_size
            except OSError:
                health["db_size_bytes"] = None
        return health
    except Exception as exc:
        return {"enabled": None, "error": str(exc)[:120]}


def build_status(*, probe: bool = False) -> dict[str, Any]:
    """Canonical diagnostics status document (acceptance criterion 10)."""
    from openharness.cli import __version__
    from openharness.diagnostics import context as diag_context
    from openharness.diagnostics.recorder import get_recorder

    settings_view: dict[str, Any] = {}
    auth_view: dict[str, Any] = {}
    try:
        from openharness.config import load_settings

        settings = load_settings()
        settings_view = settings.diagnostics.model_dump()
        auth_view = {
            "active_profile": settings.active_profile or "",
            "provider": settings.provider,
            "model": settings.model,
        }
    except Exception:
        pass
    events = read_events(since_seconds=_SUMMARY_WINDOW_SECONDS)
    recorder_health = get_recorder().health()
    status: dict[str, Any] = {
        "status_schema_version": STATUS_SCHEMA_VERSION,
        "generated_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": __version__,
        "run_id": diag_context.run_id(),
        "run": _read_current_run(),
        "settings": settings_view,
        "auth": auth_view,
        "recorder": recorder_health,
        "queue_depths": {"recorder": recorder_health.get("queued", 0)},
        "index": _index_health(),
        "summary": summarize_events(events, window_seconds=_SUMMARY_WINDOW_SECONDS),
        "recent_errors": recent_errors(events),
        "thread_probe": thread_probe() if probe else None,
    }
    return status
