"""Acceptance tests for the observability surfaces.

Each ``test_diagnostics_*`` name below is pinned in the acceptance
traceability table of docs/proposals/observability-metrics.md — renaming one
breaks the documented criterion mapping.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

from openharness.api.client import ApiMessageCompleteEvent, ProviderFallbackEvent
from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, TextBlock, ToolUseBlock
from openharness.ui.app import run_headless_control


class StaticApiClient:
    def __init__(self, text: str) -> None:
        self._text = text

    async def stream_message(self, request):
        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=self._text)]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


class SequenceApiClient:
    def __init__(self, messages: list[ConversationMessage]) -> None:
        self._messages = list(messages)

    async def stream_message(self, request):
        del request
        yield ApiMessageCompleteEvent(
            message=self._messages.pop(0),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


class FallbackThenCompleteClient:
    async def stream_message(self, request):
        del request
        yield ProviderFallbackEvent(
            reason="rate_limited",
            from_model="primary-model",
            to_provider="backup",
            to_model="backup-model",
        )
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="recovered")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


def _json_lines(output: str) -> list[dict]:
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def _isolate(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data" / "diagnostics"


def _recorded_events(diag_dir: Path) -> list[dict]:
    from openharness.diagnostics import get_recorder

    get_recorder().flush()
    events = []
    for path in sorted((diag_dir / "events").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            events.append(json.loads(line))
    return events


async def _run_headless(tmp_path: Path, client, requests: str) -> list[dict]:
    output_stream = io.StringIO()
    await run_headless_control(
        cwd=str(tmp_path),
        api_client=client,
        input_stream=io.StringIO(requests),
        output_stream=output_stream,
    )
    return _json_lines(output_stream.getvalue())


# -- acceptance criterion 1 ---------------------------------------------------


@pytest.mark.asyncio
async def test_diagnostics_headless_timeline(tmp_path: Path, monkeypatch):
    diag_dir = _isolate(tmp_path, monkeypatch)
    target = tmp_path / "note.txt"
    target.write_text("file body that must never appear in diagnostics\n")
    client = SequenceApiClient(
        [
            ConversationMessage(
                role="assistant",
                content=[
                    ToolUseBlock(id="toolu_1", name="read_file", input={"path": str(target)})
                ],
            ),
            ConversationMessage(role="assistant", content=[TextBlock(text="secret-assistant-reply")]),
        ]
    )
    await _run_headless(
        tmp_path,
        client,
        '{"type":"submit","prompt":"secret-user-prompt","request_id":"submit-1"}\n'
        '{"type":"shutdown","request_id":"shutdown-1"}\n',
    )

    events = _recorded_events(diag_dir)
    components = {event["component"] for event in events}
    assert {"headless", "engine", "api", "tool", "storage"} <= components
    run_ids = {event["run_id"] for event in events}
    assert len(run_ids) == 1 and None not in run_ids
    raw = json.dumps(events)
    assert "secret-user-prompt" not in raw
    assert "secret-assistant-reply" not in raw
    assert "file body that must never appear" not in raw


# -- acceptance criterion 2 ---------------------------------------------------


class _GatedInput:
    """Releases the diagnostics request only after line_complete is emitted.

    The diagnostics request is answered on the headless fast path (readable
    even mid-turn, for hang debugging), so an ungated stream would race the
    turn it is trying to summarize.
    """

    def __init__(self, output: io.StringIO) -> None:
        self._output = output
        self._lines = [
            '{"type":"submit","prompt":"hello","request_id":"submit-1"}\n',
            '{"type":"diagnostics","request_id":"diag-1"}\n',
            '{"type":"shutdown","request_id":"shutdown-1"}\n',
        ]

    def readline(self) -> str:
        if not self._lines:
            return ""
        if len(self._lines) == 2:
            deadline = time.monotonic() + 10.0
            while "line_complete" not in self._output.getvalue():
                if time.monotonic() > deadline:  # pragma: no cover - hang guard
                    return ""
                time.sleep(0.01)
        return self._lines.pop(0)


@pytest.mark.asyncio
async def test_diagnostics_usage_matches_line_complete(tmp_path: Path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    output_stream = io.StringIO()
    await run_headless_control(
        cwd=str(tmp_path),
        api_client=StaticApiClient("world"),
        input_stream=_GatedInput(output_stream),
        output_stream=output_stream,
    )
    events = _json_lines(output_stream.getvalue())
    line_complete = next(e for e in events if e["type"] == "line_complete")
    snapshot = next(e for e in events if e["type"] == "diagnostics_snapshot")
    assert snapshot["request_id"] == "diag-1"
    assert snapshot["run_id"]
    counters = snapshot["summary"]["counters"]
    assert counters.get("input_tokens") == line_complete["usage"]["input_tokens"]
    assert counters.get("output_tokens") == line_complete["usage"]["output_tokens"]
    assert snapshot["recorder"]["enabled"] is True


# -- acceptance criterion 3 ---------------------------------------------------


@pytest.mark.asyncio
async def test_diagnostics_forced_fallback_double_emission(tmp_path: Path, monkeypatch):
    diag_dir = _isolate(tmp_path, monkeypatch)
    events = await _run_headless(
        tmp_path,
        FallbackThenCompleteClient(),
        '{"type":"submit","prompt":"go","request_id":"submit-1"}\n'
        '{"type":"shutdown","request_id":"shutdown-1"}\n',
    )
    # User-facing stream event ...
    assert any(
        e["type"] == "status" and "fallback provider backup" in e.get("message", "")
        for e in events
    )
    # ... and the diagnostic event from the same call site.
    recorded = _recorded_events(diag_dir)
    fallback = [e for e in recorded if e["component"] == "api" and e["event"] == "fallback"]
    assert len(fallback) == 1
    assert fallback[0]["attrs"]["reason"] == "rate_limited"
    assert fallback[0]["attrs"]["from_model"] == "primary-model"
    assert fallback[0]["attrs"]["to_model"] == "backup-model"
    assert "recovered" not in json.dumps(recorded)


# -- acceptance criterion 4 ---------------------------------------------------


@pytest.mark.asyncio
async def test_diagnostics_permission_denial_redacted(tmp_path: Path, monkeypatch):
    diag_dir = _isolate(tmp_path, monkeypatch)
    client = SequenceApiClient(
        [
            ConversationMessage(
                role="assistant",
                content=[
                    ToolUseBlock(
                        id="toolu_1",
                        name="bash",
                        input={"command": "echo super-secret-command-argument"},
                    )
                ],
            ),
            ConversationMessage(role="assistant", content=[TextBlock(text="done")]),
        ]
    )
    await _run_headless(
        tmp_path,
        client,
        '{"type":"submit","prompt":"mutate","request_id":"submit-1"}\n',
    )
    recorded = _recorded_events(diag_dir)
    denials = [e for e in recorded if e["component"] == "permission" and e["event"] == "denied"]
    assert denials, "expected a permission denial diagnostic event"
    assert denials[0]["attrs"]["tool_name"] == "bash"
    assert denials[0]["attrs"]["reason"] in {"user_denied", "policy_blocked"}
    assert "super-secret-command-argument" not in json.dumps(recorded)


# -- acceptance criterion 5 ---------------------------------------------------


@pytest.mark.asyncio
async def test_diagnostics_session_search_metrics(tmp_path: Path, monkeypatch):
    diag_dir = _isolate(tmp_path, monkeypatch)
    await _run_headless(
        tmp_path,
        StaticApiClient("unused"),
        '{"type":"search_sessions","query":"alpha","request_id":"search-1"}\n'
        '{"type":"shutdown","request_id":"shutdown-1"}\n',
    )
    recorded = _recorded_events(diag_dir)
    searches = [e for e in recorded if e["component"] == "index" and e["operation"] == "index_search"]
    assert searches, "expected an index_search diagnostic event"
    attrs = searches[0]["attrs"]
    assert attrs["operation_kind"] == "discover"
    assert isinstance(attrs["fts_enabled"], bool)
    assert isinstance(attrs["hits"], int)
    assert isinstance(searches[0]["duration_ms"], (int, float))


# -- acceptance criteria 6 and 7 ----------------------------------------------


def _seed_events_with_secret(diag_dir: Path) -> None:
    from openharness.diagnostics import get_recorder
    from openharness.diagnostics.runinfo import write_current_run

    write_current_run("headless")
    recorder = get_recorder()
    recorder.record("engine", "turn", "completed", duration_ms=12.5)
    recorder.flush()
    # Defense-in-depth path: a secret somehow already sitting in an event file.
    events_dir = diag_dir / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    day_file = sorted(events_dir.glob("*.jsonl"))[0]
    with day_file.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"component": "api", "attrs": {"leak": "sk-ant-FAKE0123456789012345678901"}})
            + "\n"
        )


def test_diagnostics_export_bundle_manifest(tmp_path: Path, monkeypatch):
    diag_dir = _isolate(tmp_path, monkeypatch)
    _seed_events_with_secret(diag_dir)
    from openharness.diagnostics.export import export_bundle

    result = export_bundle(output=tmp_path / "bundle.tar.gz", since_seconds=24 * 3600.0)
    with tarfile.open(result["path"], "r:gz") as archive:
        names = archive.getnames()
        manifest = json.loads(archive.extractfile("manifest.json").read())
        status = json.loads(archive.extractfile("status.json").read())
        report = json.loads(archive.extractfile("redaction-report.json").read())
    for required in ("manifest.json", "status.json", "release-info.json", "redaction-report.json", "current-run.json"):
        assert required in names
    assert any(name.startswith("events/") and name.endswith(".jsonl") for name in names)
    assert sorted(manifest["files"]) == sorted(names)
    assert manifest["bundle_format_version"] == 1
    assert status["status_schema_version"] == 1
    assert "total" in report and "rules" in report


def test_diagnostics_export_redacts_seeded_secrets(tmp_path: Path, monkeypatch):
    diag_dir = _isolate(tmp_path, monkeypatch)
    _seed_events_with_secret(diag_dir)
    from openharness.diagnostics import get_recorder
    from openharness.diagnostics.export import export_bundle

    # A secret flowing through the supported path: an exception message.
    get_recorder().record(
        "api",
        "model_call",
        "failed",
        status="error",
        error={
            "type": "AuthError",
            "reason": "auth",
            "message_preview": "boom AKIAABCDEFGHIJKLMNOP boom",
            "status_code": 401,
        },
    )
    result = export_bundle(output=tmp_path / "bundle.tar.gz", since_seconds=24 * 3600.0)
    contents = []
    with tarfile.open(result["path"], "r:gz") as archive:
        for member in archive.getmembers():
            contents.append(archive.extractfile(member).read().decode("utf-8"))
    blob = "\n".join(contents)
    assert "sk-ant-FAKE0123456789012345678901" not in blob
    assert "AKIAABCDEFGHIJKLMNOP" not in blob
    assert "[redacted:" in blob
    assert result["redactions"]["total"] >= 2
    # Forbidden attribution strings must never ride along in a bundle.
    assert "Co-Authored-By" not in blob
    assert "Generated with" not in blob


# -- acceptance criterion 10 --------------------------------------------------


def test_diagnostics_status_json_schema(tmp_path: Path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from typer.testing import CliRunner

    from openharness.cli import app

    result = CliRunner().invoke(app, ["diagnostics", "status", "--json"])
    assert result.exit_code == 0, result.output
    status = json.loads(result.output)
    assert status["status_schema_version"] == 1
    for key in (
        "generated_at",
        "version",
        "run_id",
        "run",
        "settings",
        "auth",
        "recorder",
        "queue_depths",
        "index",
        "summary",
        "recent_errors",
        "thread_probe",
    ):
        assert key in status, f"missing status key: {key}"
    assert status["thread_probe"]["status"] in {"ok", "failed", "timeout"}
    assert isinstance(status["recorder"]["events_written"], int)
    assert isinstance(status["summary"]["by_component"], dict)
    secrets_view = json.dumps(status.get("auth"))
    assert "api_key" not in secrets_view


def test_diagnostics_status_cli_process_exits_cleanly(tmp_path: Path):
    """Regression guard for the first release review: status --json must not
    strand a default-executor worker and hang process teardown."""
    env = {
        **os.environ,
        "OPENHARNESS_CONFIG_DIR": str(tmp_path / "config"),
        "OPENHARNESS_DATA_DIR": str(tmp_path / "data"),
    }
    result = subprocess.run(
        [sys.executable, "-m", "openharness", "diagnostics", "status", "--json"],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    status = json.loads(result.stdout)
    assert status["thread_probe"]["status"] in {"ok", "failed", "timeout"}


# -- supporting surfaces (not in the traceability table) -----------------------


@pytest.mark.asyncio
async def test_headless_correlation_id_flows_into_diagnostics(tmp_path: Path, monkeypatch):
    diag_dir = _isolate(tmp_path, monkeypatch)
    await _run_headless(
        tmp_path,
        StaticApiClient("world"),
        '{"type":"submit","prompt":"hello","request_id":"submit-1","correlation_id":"consumer-run-42"}\n'
        '{"type":"shutdown","request_id":"shutdown-1"}\n',
    )
    recorded = _recorded_events(diag_dir)
    tagged = [e for e in recorded if (e.get("attrs") or {}).get("correlation_id") == "consumer-run-42"]
    assert tagged, "expected diagnostics events tagged with the external correlation_id"
    # Correlation id is diagnostics-only; the protocol routes by request_id.
    assert all(e["component"] in {"headless", "engine", "api", "tool", "storage", "index", "memory", "diagnostics", "permission"} for e in tagged)


def test_watchdog_emits_slow_operation_and_heartbeat(tmp_path: Path, monkeypatch):
    diag_dir = _isolate(tmp_path, monkeypatch)
    from openharness.diagnostics import watchdog

    monkeypatch.setitem(watchdog.SLOW_THRESHOLDS_MS, "index_search", 0.0)
    with watchdog.track("index_search", request_id="req-1"):
        time.sleep(0.01)
        watchdog.scan_once(heartbeat=True)
    recorded = _recorded_events(diag_dir)
    slow = [e for e in recorded if e["operation"] == "slow_operation"]
    assert slow and slow[0]["attrs"]["reason"] == "index_search"
    assert slow[0]["request_id"] == "req-1"
    heartbeats = [e for e in recorded if e["event"] == "heartbeat"]
    assert heartbeats and heartbeats[0]["counters"]["active_operations"] == 1


def test_watchdog_hard_threshold_dumps_stacks(tmp_path: Path, monkeypatch):
    diag_dir = _isolate(tmp_path, monkeypatch)
    from openharness.diagnostics import watchdog

    monkeypatch.setitem(watchdog.SLOW_THRESHOLDS_MS, "tool_call", 0.001)
    with watchdog.track("tool_call", tool_use_id="toolu_9"):
        time.sleep(0.05)
        watchdog.scan_once(heartbeat=False)
    recorded = _recorded_events(diag_dir)
    hard = [e for e in recorded if e["operation"] == "slow_operation" and e["level"] == "error"]
    assert hard, "expected a hard-threshold slow_operation event"
    stack_file = hard[0]["attrs"].get("file")
    assert stack_file
    stack_path = diag_dir / "stacks" / stack_file
    assert stack_path.exists()
    stack_text = stack_path.read_text(encoding="utf-8")
    assert "thread" in stack_text.lower()
    assert "most recent call first" in stack_text


def test_diagnostics_package_is_executor_free():
    """Hard constraint: nothing in the diagnostics package may touch the
    asyncio default executor. Its workers are non-daemon, so in broken
    environments a wedged worker hangs asyncio.run() teardown: the exact
    failure mode this subsystem exists to diagnose (release blocker on the
    first review of this branch)."""
    import openharness.diagnostics as pkg

    for path in Path(pkg.__file__).parent.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        # Call sites only; docstrings naming the hazard are allowed.
        for forbidden in ("to_thread(", "run_in_executor("):
            assert forbidden not in text, f"{path.name} uses {forbidden}"


def test_thread_probe_works_without_executor(tmp_path: Path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    import asyncio

    def _executor_used(*args, **kwargs):  # pragma: no cover - guard
        raise AssertionError("diagnostics probe touched the asyncio executor")

    monkeypatch.setattr(asyncio, "to_thread", _executor_used)
    from openharness.diagnostics.snapshot import thread_probe

    result = thread_probe(timeout=2.0)
    assert result["status"] == "ok"
    assert isinstance(result["duration_ms"], (int, float))


def test_watchdog_does_not_start_in_print_mode(tmp_path: Path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from openharness.diagnostics import watchdog

    assert watchdog.start_watchdog("print") is False
    assert watchdog.start_watchdog("headless") is True
    watchdog.stop_watchdog()
