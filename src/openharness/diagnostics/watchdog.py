"""Hang and slow-operation watchdog (proposal § "Hang And Slow-Operation Diagnostics").

In-flight operations register through :func:`track` (a cheap sync context
manager: one dict insert/remove under a lock). A daemon thread — started
only in long-lived modes — scans every five seconds, emits one
``slow_operation`` event per operation that exceeds its threshold, dumps a
stack snapshot via :mod:`faulthandler` when an operation crosses the hard
threshold (slow x 4), and emits a heartbeat with the active-operation count.
The thread is a daemon and never keeps process teardown alive.
"""

from __future__ import annotations

import contextlib
import itertools
import threading
import time
from typing import Any, Iterator

# Defaults from the proposal's slow-threshold table (milliseconds).
SLOW_THRESHOLDS_MS: dict[str, float] = {
    "headless_status": 1_000.0,
    "headless_list": 1_000.0,
    "headless_search": 1_000.0,
    "headless_submit": 30_000.0,
    "api_first_token": 20_000.0,
    "tool_call": 30_000.0,
    "snapshot_write": 2_000.0,
    "index_search": 500.0,
    "index_update": 2_000.0,
    "mcp_tool_call": 5_000.0,
    "channel_media": 30_000.0,
    "cron_job": 60_000.0,
}
HARD_MULTIPLIER = 4.0
SCAN_INTERVAL_SECONDS = 5.0
# Heartbeats run in long-lived modes only — never one-shot print mode.
LONG_LIVED_MODES = frozenset({"headless", "mcp", "gateway", "ui", "task-worker"})

_ops: dict[int, dict[str, Any]] = {}
_ops_lock = threading.Lock()
_op_counter = itertools.count(1)
_thread: threading.Thread | None = None
_stop = threading.Event()
_mode: str | None = None


@contextlib.contextmanager
def track(kind: str, **ids: str | None) -> Iterator[None]:
    """Register an in-flight operation for the duration of the body."""
    token = next(_op_counter)
    entry = {
        "kind": kind,
        "started": time.monotonic(),
        "ids": {k: v for k, v in ids.items() if v},
        "slow_emitted": False,
        "hard_emitted": False,
    }
    with _ops_lock:
        _ops[token] = entry
    try:
        yield
    finally:
        with _ops_lock:
            _ops.pop(token, None)


def _thresholds() -> dict[str, float]:
    merged = dict(SLOW_THRESHOLDS_MS)
    try:
        from openharness.config import load_settings

        for key, value in load_settings().diagnostics.slow_thresholds.items():
            merged[key] = float(value)
    except Exception:
        pass
    return merged


def _dump_stacks(kind: str, token_label: str) -> str | None:
    """Write a faulthandler stack snapshot; returns the file name or None."""
    import faulthandler

    try:
        from openharness.diagnostics.snapshot import diagnostics_dir

        stacks_dir = diagnostics_dir() / "stacks"
        stacks_dir.mkdir(parents=True, exist_ok=True)
        name = f"{time.strftime('%Y%m%d-%H%M%S')}-{kind}-{token_label}.txt"
        with (stacks_dir / name).open("w", encoding="utf-8") as handle:
            faulthandler.dump_traceback(file=handle, all_threads=True)
        return name
    except Exception:
        return None


def scan_once(*, heartbeat: bool = True) -> None:
    """One watchdog pass; separated from the thread loop for tests."""
    from openharness.diagnostics import record

    thresholds = _thresholds()
    now = time.monotonic()
    with _ops_lock:
        snapshot = [(token, dict(entry)) for token, entry in _ops.items()]
    oldest_age_ms = 0.0
    for token, entry in snapshot:
        age_ms = (now - entry["started"]) * 1000.0
        oldest_age_ms = max(oldest_age_ms, age_ms)
        threshold = thresholds.get(entry["kind"])
        if threshold is None:
            continue
        ids = entry["ids"]
        if age_ms > threshold * HARD_MULTIPLIER and not entry["hard_emitted"]:
            stack_file = _dump_stacks(entry["kind"], str(token))
            with _ops_lock:
                live = _ops.get(token)
                if live is not None:
                    live["hard_emitted"] = True
                    live["slow_emitted"] = True
            record(
                "diagnostics",
                "slow_operation",
                "timeout",
                level="error",
                status="error",
                duration_ms=age_ms,
                attrs={"reason": entry["kind"], "operation_age_ms": round(age_ms, 1), "file": stack_file},
                **ids,
            )
        elif age_ms > threshold and not entry["slow_emitted"]:
            with _ops_lock:
                live = _ops.get(token)
                if live is not None:
                    live["slow_emitted"] = True
            record(
                "diagnostics",
                "slow_operation",
                "timeout",
                level="warning",
                status="error",
                duration_ms=age_ms,
                attrs={"reason": entry["kind"], "operation_age_ms": round(age_ms, 1)},
                **ids,
            )
    if heartbeat:
        record(
            "diagnostics",
            "watchdog",
            "heartbeat",
            level="debug",
            attrs={"operation_age_ms": round(oldest_age_ms, 1)},
            counters={"active_operations": len(snapshot)},
        )


def _watchdog_loop(heartbeat_enabled: bool) -> None:
    while not _stop.wait(timeout=SCAN_INTERVAL_SECONDS):
        try:
            scan_once(heartbeat=heartbeat_enabled)
        except Exception:
            return


def start_watchdog(mode: str) -> bool:
    """Start the daemon scanner for a long-lived mode; True if started."""
    global _thread, _mode
    if mode not in LONG_LIVED_MODES or _thread is not None:
        return False
    heartbeat_enabled = True
    try:
        from openharness.config import load_settings

        diagnostics = load_settings().diagnostics
        if not diagnostics.enabled:
            return False
        heartbeat_enabled = bool(diagnostics.heartbeat_enabled)
    except Exception:
        pass
    _stop.clear()
    _mode = mode
    _thread = threading.Thread(
        target=_watchdog_loop, args=(heartbeat_enabled,), name="diagnostics-watchdog", daemon=True
    )
    _thread.start()
    return True


def stop_watchdog() -> None:
    """Stop the scanner and clear in-flight state (tests/teardown)."""
    global _thread, _mode
    _stop.set()
    _thread = None
    _mode = None
    with _ops_lock:
        _ops.clear()
