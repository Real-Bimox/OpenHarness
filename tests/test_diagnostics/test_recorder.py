"""Phase 1 tests: schema, redaction, retention, overflow, disabled mode."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from openharness.diagnostics.recorder import Recorder
from openharness.diagnostics.schema import build_error, build_event, redact_preview


def _recorder(tmp_path: Path, **kwargs) -> Recorder:
    defaults = dict(events_dir=tmp_path / "events", enabled=True, max_daily_mb=25.0, retention_days=14)
    defaults.update(kwargs)
    return Recorder(**defaults)


def test_event_schema_stability():
    event, dropped = build_event(
        component="headless",
        operation="submit",
        event="completed",
        duration_ms=12.345,
        run_id="r1",
        attrs={"request_type": "submit", "not_allowlisted": "x"},
        counters={"input_tokens": 10},
    )
    assert dropped == 1
    assert "not_allowlisted" not in event["attrs"]
    assert event["attrs"]["request_type"] == "submit"
    assert event["schema_version"] == 1
    assert event["duration_ms"] == 12.35
    assert set(event) == {
        "schema_version", "ts", "monotonic_ms", "run_id", "pid", "component",
        "operation", "event", "level", "status", "duration_ms", "session_id",
        "request_id", "turn_id", "api_call_id", "tool_use_id", "task_id",
        "attrs", "counters", "error",
    }


def test_unknown_component_and_event_are_bounded():
    event, _ = build_event(component="nonsense", operation="x", event="exploded")
    assert event["component"] == "diagnostics"
    assert event["event"] == "completed"


def test_error_preview_redacts_secrets():
    secret = "sk-" + "a" * 24
    error = build_error(RuntimeError(f"upstream said {secret} try later"), status_code=429)
    assert secret not in error["message_preview"]
    assert "redacted" in error["message_preview"]
    assert error["status_code"] == 429
    assert len(redact_preview("x" * 500)) == 200


def test_write_and_read_back(tmp_path: Path):
    recorder = _recorder(tmp_path)
    recorder.record("api", "call", "completed", duration_ms=5.0, attrs={"model": "m"})
    recorder.flush()
    files = list((tmp_path / "events").glob("*.jsonl"))
    assert len(files) == 1
    event = json.loads(files[0].read_text().splitlines()[0])
    assert event["component"] == "api"
    assert recorder.health()["events_written"] == 1


def test_disabled_mode_writes_nothing(tmp_path: Path):
    recorder = _recorder(tmp_path, enabled=False)
    recorder.record("api", "call")
    recorder.flush()
    assert not (tmp_path / "events").exists()
    assert recorder.health()["events_written"] == 0


def test_queue_overflow_drops_low_priority_first(tmp_path: Path):
    import openharness.diagnostics.recorder as rec_mod

    recorder = _recorder(tmp_path)
    # Fill the queue without a writer draining it.
    recorder._writer = object()  # block writer creation
    for i in range(rec_mod.QUEUE_MAX - 1):
        recorder.record("api", "call", "completed")
    recorder.record("api", "call", "started")  # droppable
    assert len(recorder._queue) == rec_mod.QUEUE_MAX
    recorder.record("api", "call", "completed")  # overflow: drops the started
    health = recorder.health()
    assert health["events_dropped"] == 1
    assert all(e["event"] != "started" for e in recorder._queue)
    # Overflow with no droppable events: the NEW event is dropped.
    recorder.record("api", "call", "completed")
    assert recorder.health()["events_dropped"] == 2


def test_daily_cap_drops(tmp_path: Path):
    recorder = _recorder(tmp_path, max_daily_mb=0.0001)  # ~100 bytes
    for _ in range(5):
        recorder.record("api", "call", "completed", attrs={"model": "m" * 50})
    recorder.flush()
    recorder.record("api", "call", "completed")
    recorder.flush()
    assert recorder.health()["events_dropped"] >= 1


def test_retention_sweep(tmp_path: Path):
    recorder = _recorder(tmp_path, retention_days=1)
    events = tmp_path / "events"
    events.mkdir(parents=True)
    old = events / "2020-01-01.jsonl"
    old.write_text("{}\n")
    import os

    os.utime(old, (time.time() - 10 * 86400, time.time() - 10 * 86400))
    fresh = events / "2099-01-01.jsonl"
    fresh.write_text("{}\n")
    removed = recorder.sweep_retention()
    assert removed == 1
    assert not old.exists() and fresh.exists()


def test_writer_failure_disables_recorder(tmp_path: Path, capsys):
    recorder = _recorder(tmp_path)
    recorder.events_dir = tmp_path / "events"
    recorder.events_dir.mkdir()
    # Make the events dir unwritable by pointing at a file path.
    recorder.events_dir = tmp_path / "events" / "not-a-dir"
    recorder.events_dir.write_text("file blocks dir creation")
    recorder.record("api", "call")
    recorder.flush()
    assert recorder._failed is True
    assert "disabled" in capsys.readouterr().err
    # Subsequent records are cheap no-ops.
    recorder.record("api", "call")
    recorder.flush()


def test_span_records_duration_and_failure(tmp_path: Path):
    recorder = _recorder(tmp_path)
    with recorder.span("index", "search") as span:
        span["attrs"]["hits"] = 3
    with pytest.raises(ValueError):
        with recorder.span("index", "search"):
            raise ValueError("boom")
    recorder.flush()
    lines = [json.loads(line) for line in (tmp_path / "events" / time.strftime("%Y-%m-%d")).with_suffix(".jsonl").read_text().splitlines()]
    completed = [e for e in lines if e["event"] == "completed"]
    failed = [e for e in lines if e["event"] == "failed"]
    assert completed[0]["attrs"]["hits"] == 3
    assert completed[0]["duration_ms"] >= 0
    assert failed[0]["error"]["type"] == "ValueError"


def test_correlation_context_flows(tmp_path: Path):
    from openharness.diagnostics import context as diag_context

    recorder = _recorder(tmp_path)
    token = diag_context.correlation_id_var.set("consumer-42")
    try:
        recorder.record("headless", "submit", "completed")
    finally:
        diag_context.correlation_id_var.reset(token)
    recorder.flush()
    files = list((tmp_path / "events").glob("*.jsonl"))
    event = json.loads(files[0].read_text().splitlines()[-1])
    assert event["attrs"]["correlation_id"] == "consumer-42"
    assert event["run_id"]
