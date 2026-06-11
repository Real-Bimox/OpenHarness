"""Bounded local diagnostics recorder.

Spec: docs/proposals/observability-metrics.md §§1-3. Hard constraint from
the v0.1.17 executor-hang lesson: the hot path is a plain synchronous append
to a bounded ``collections.deque`` under a small lock — it never touches
asyncio, ``to_thread``, or any executor. A plain daemon thread drains the
deque to daily JSONL files (no per-event fsync) and must never keep process
teardown alive. Best-effort throughout: a failing writer disables itself for
the process after one stderr warning.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Iterator

from openharness.diagnostics import context as diag_context
from openharness.diagnostics.schema import build_error, build_event

QUEUE_MAX = 10_000
FLUSH_INTERVAL_SECONDS = 0.5
FLUSH_BATCH = 200
# Low-priority events are dropped first under queue pressure.
DROPPABLE_EVENTS = ("started", "heartbeat")


class Recorder:
    """Process-wide diagnostics recorder. Use :func:`get_recorder`."""

    def __init__(self, *, events_dir: Path, enabled: bool, max_daily_mb: float, retention_days: int) -> None:
        self.events_dir = events_dir
        self.enabled = enabled
        self.max_daily_bytes = int(max_daily_mb * 1024 * 1024)
        self.retention_days = retention_days
        self._queue: deque[dict[str, Any]] = deque()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._writer: threading.Thread | None = None
        self._stopping = False
        self._failed = False
        self._current_date = ""
        self._current_size = 0
        self.events_written = 0
        self.events_dropped = 0
        self.redactions = 0

    # -- hot path (synchronous, executor-free) -------------------------------

    def record(
        self,
        component: str,
        operation: str,
        event: str = "completed",
        *,
        level: str = "info",
        status: str = "ok",
        duration_ms: float | None = None,
        attrs: dict[str, Any] | None = None,
        counters: dict[str, int | float] | None = None,
        error: dict[str, Any] | None = None,
        session_id: str | None = None,
        request_id: str | None = None,
        turn_id: str | None = None,
        api_call_id: str | None = None,
        tool_use_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        if not self.enabled or self._failed:
            return
        merged_attrs = dict(attrs or {})
        correlation = diag_context.correlation_id_var.get()
        if correlation and "correlation_id" not in merged_attrs:
            merged_attrs["correlation_id"] = correlation
        payload, dropped_attrs = build_event(
            component=component,
            operation=operation,
            event=event,
            level=level,
            status=status,
            duration_ms=duration_ms,
            run_id=diag_context.run_id(),
            pid=diag_context.pid(),
            session_id=session_id if session_id is not None else diag_context.session_id_var.get(),
            request_id=request_id if request_id is not None else diag_context.request_id_var.get(),
            turn_id=turn_id if turn_id is not None else diag_context.turn_id_var.get(),
            api_call_id=api_call_id,
            tool_use_id=tool_use_id,
            task_id=task_id,
            attrs=merged_attrs,
            counters=counters,
            error=error,
        )
        with self._lock:
            self.redactions += dropped_attrs
            if len(self._queue) >= QUEUE_MAX:
                if not self._drop_one_locked():
                    self.events_dropped += 1
                    return
            self._queue.append(payload)
            should_wake = len(self._queue) >= FLUSH_BATCH
        self._ensure_writer()
        if should_wake:
            self._wake.set()

    def _drop_one_locked(self) -> bool:
        """Drop one low-priority queued event; True if room was made."""
        for index, queued in enumerate(self._queue):
            if queued.get("event") in DROPPABLE_EVENTS:
                del self._queue[index]
                self.events_dropped += 1
                return True
        return False

    @contextlib.contextmanager
    def span(
        self,
        component: str,
        operation: str,
        *,
        attrs: dict[str, Any] | None = None,
        emit_started: bool = False,
        level: str = "info",
        **ids: str | None,
    ) -> Iterator[dict[str, Any]]:
        """Time an operation; emits completed/failed with duration_ms.

        The yielded dict lets the body attach result attrs/counters:
        ``span_data["attrs"]["hits"] = 3`` / ``span_data["counters"][...]``.
        """
        if emit_started:
            self.record(component, operation, "started", level="debug", attrs=attrs, **ids)
        span_data: dict[str, Any] = {"attrs": dict(attrs or {}), "counters": {}, "status": "ok"}
        start = time.monotonic()
        try:
            yield span_data
        except BaseException as exc:
            self.record(
                component,
                operation,
                "failed",
                level="error",
                status="error",
                duration_ms=(time.monotonic() - start) * 1000.0,
                attrs=span_data["attrs"],
                counters=span_data["counters"],
                error=build_error(exc),
                **ids,
            )
            raise
        self.record(
            component,
            operation,
            "completed",
            level=level,
            status=span_data.get("status", "ok"),
            duration_ms=(time.monotonic() - start) * 1000.0,
            attrs=span_data["attrs"],
            counters=span_data["counters"],
            error=span_data.get("error"),
            **ids,
        )

    # -- writer (daemon thread, never blocks teardown) ------------------------

    def _ensure_writer(self) -> None:
        if self._writer is not None or self._failed or self._stopping:
            return
        with self._lock:
            if self._writer is not None:
                return
            self._writer = threading.Thread(
                target=self._writer_loop, name="diagnostics-writer", daemon=True
            )
            self._writer.start()

    def _writer_loop(self) -> None:
        while not self._stopping:
            self._wake.wait(timeout=FLUSH_INTERVAL_SECONDS)
            self._wake.clear()
            self.flush()

    def flush(self) -> None:
        """Drain the queue to disk. Called by the writer and on shutdown."""
        if self._failed:
            with self._lock:
                self.events_dropped += len(self._queue)
                self._queue.clear()
            return
        with self._lock:
            if not self._queue:
                return
            batch = list(self._queue)
            self._queue.clear()
        try:
            self._write_batch(batch)
        except Exception as exc:
            self._failed = True
            with self._lock:
                self.events_dropped += len(batch)
            print(
                f"openharness diagnostics: recorder disabled for this process ({exc})",
                file=sys.stderr,
            )

    def _write_batch(self, batch: list[dict[str, Any]]) -> None:
        today = time.strftime("%Y-%m-%d")
        path = self.events_dir / f"{today}.jsonl"
        # Recreate per batch: the parent can vanish under us (temp dirs,
        # purges); one mkdir per flush is negligible and keeps us alive.
        self.events_dir.mkdir(parents=True, exist_ok=True)
        if today != self._current_date:
            self._current_date = today
            self._current_size = path.stat().st_size if path.exists() else 0
        if self._current_size >= self.max_daily_bytes:
            with self._lock:
                self.events_dropped += len(batch)
            return
        lines = "".join(json.dumps(event, separators=(",", ":")) + "\n" for event in batch)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(lines)
        self._current_size += len(lines.encode("utf-8"))
        self.events_written += len(batch)

    def sweep_retention(self) -> int:
        """Delete event files older than the retention window."""
        removed = 0
        cutoff = time.time() - self.retention_days * 86400
        try:
            for path in self.events_dir.glob("*.jsonl"):
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
                    removed += 1
        except OSError:
            pass
        return removed

    def health(self) -> dict[str, Any]:
        with self._lock:
            queued = len(self._queue)
        return {
            "enabled": self.enabled and not self._failed,
            "events_written": self.events_written,
            "events_dropped": self.events_dropped,
            "attrs_redacted": self.redactions,
            "queued": queued,
        }

    def shutdown(self) -> None:
        self._stopping = True
        self._wake.set()
        self.flush()


_RECORDER: Recorder | None = None
_RECORDER_LOCK = threading.Lock()


def get_recorder() -> Recorder:
    """Return the process recorder, building it from settings lazily."""
    global _RECORDER
    if _RECORDER is not None:
        return _RECORDER
    with _RECORDER_LOCK:
        if _RECORDER is None:
            _RECORDER = _build_from_settings()
            if _RECORDER.enabled:
                _RECORDER.sweep_retention()
                atexit.register(_RECORDER.shutdown)
        return _RECORDER


def _build_from_settings() -> Recorder:
    from openharness.config.paths import get_data_dir

    enabled = True
    max_daily_mb = 25.0
    retention_days = 14
    try:
        from openharness.config import load_settings

        diagnostics = load_settings().diagnostics
        enabled = bool(diagnostics.enabled and diagnostics.event_log_enabled)
        max_daily_mb = float(diagnostics.max_daily_mb)
        retention_days = int(diagnostics.retention_days)
    except Exception:
        pass
    return Recorder(
        events_dir=get_data_dir() / "diagnostics" / "events",
        enabled=enabled,
        max_daily_mb=max_daily_mb,
        retention_days=retention_days,
    )


def reset_recorder() -> None:
    """Tear down the singleton (tests)."""
    global _RECORDER
    with _RECORDER_LOCK:
        if _RECORDER is not None:
            _RECORDER.shutdown()
        _RECORDER = None


def record(component: str, operation: str, event: str = "completed", **kwargs: Any) -> None:
    """Module-level convenience wrapper; never raises."""
    try:
        get_recorder().record(component, operation, event, **kwargs)
    except Exception:
        pass
