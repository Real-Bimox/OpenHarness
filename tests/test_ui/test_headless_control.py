"""Tests for local headless control surfaces."""

from __future__ import annotations

import io
import json
import asyncio
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from openharness.api.client import ApiMessageCompleteEvent
from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, TextBlock, ToolUseBlock
from openharness.services.session_storage import save_session_snapshot
from openharness.ui.app import run_headless_control, run_print_mode
from openharness.ui.runtime import build_runtime, close_runtime


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


def _json_lines(output: str) -> list[dict]:
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def test_headless_cli_consumes_piped_stdin(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    workspace = tmp_path / "repo"
    workspace.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "openharness",
            "--headless",
            "--bare",
            "--cwd",
            str(workspace),
        ],
        input=(
            '{"type":"status","request_id":"status-1"}\n'
            '{"type":"list_sessions","request_id":"sessions-1"}\n'
            '{"type":"shutdown","request_id":"shutdown-1"}\n'
        ),
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    events = _json_lines(result.stdout)
    assert events[0]["type"] == "process_ready"
    assert any(
        event["type"] == "state_snapshot" and event.get("request_id") == "status-1"
        for event in events
    )
    assert any(
        event["type"] == "sessions" and event.get("request_id") == "sessions-1"
        for event in events
    )
    assert events[-1]["type"] == "shutdown"
    assert events[-1]["request_id"] == "shutdown-1"


@pytest.mark.asyncio
async def test_build_runtime_accepts_restored_session_id(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    bundle = await build_runtime(
        cwd=str(tmp_path),
        api_client=StaticApiClient("unused"),
        session_id="saved123",
    )
    try:
        assert bundle.session_id == "saved123"
        assert bundle.engine.tool_metadata["session_id"] == "saved123"
    finally:
        await close_runtime(bundle)


@pytest.mark.asyncio
async def test_print_mode_json_includes_session_id(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    exit_code = await run_print_mode(
        prompt="hello",
        output_format="json",
        cwd=str(tmp_path),
        api_client=StaticApiClient("world"),
        session_id="print123",
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["type"] == "result"
    assert payload["session_id"] == "print123"
    assert payload["text"] == "world"
    assert payload["is_error"] is False
    assert payload["errors"] == []
    assert payload["permission_denials"] == []
    assert payload["usage"]["input_tokens"] >= 1


@pytest.mark.asyncio
async def test_headless_control_submit_and_shutdown(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    input_stream = io.StringIO(
        '{"type":"submit","prompt":"hello","request_id":"submit-1"}\n'
        '{"type":"shutdown","request_id":"shutdown-1"}\n'
    )
    output_stream = io.StringIO()

    await run_headless_control(
        cwd=str(tmp_path),
        api_client=StaticApiClient("world"),
        input_stream=input_stream,
        output_stream=output_stream,
    )

    events = _json_lines(output_stream.getvalue())
    assert events[0]["type"] == "process_ready"
    ready = next(event for event in events if event["type"] == "ready")
    assert any(
        event["type"] == "assistant_complete"
        and event["text"] == "world"
        and event["request_id"] == "submit-1"
        for event in events
    )
    assert any(
        event["type"] == "line_complete"
        and event["request_id"] == "submit-1"
        and event["session_id"] == ready["session_id"]
        for event in events
    )
    assert events[-1]["type"] == "shutdown"
    assert events[-1]["request_id"] == "shutdown-1"


@pytest.mark.asyncio
async def test_headless_control_resume_preserves_session_id(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()
    save_session_snapshot(
        cwd=project,
        model="test-model",
        system_prompt="system",
        messages=[ConversationMessage(role="user", content=[TextBlock(text="earlier")])],
        usage=UsageSnapshot(input_tokens=1, output_tokens=1),
        session_id="saved123",
    )

    input_stream = io.StringIO(
        '{"type":"resume","session_id":"saved123","prompt":"continue","request_id":"resume-1"}\n'
    )
    output_stream = io.StringIO()

    await run_headless_control(
        cwd=str(project),
        api_client=StaticApiClient("continued"),
        input_stream=input_stream,
        output_stream=output_stream,
    )

    events = _json_lines(output_stream.getvalue())
    resumed_ready = [event for event in events if event["type"] == "ready" and event.get("resumed")]
    assert len(resumed_ready) == 1
    assert resumed_ready[0]["session_id"] == "saved123"
    assert any(event["type"] == "line_complete" and event["session_id"] == "saved123" for event in events)


@pytest.mark.asyncio
async def test_headless_control_denies_default_permission_prompts(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    client = SequenceApiClient(
        [
            ConversationMessage(
                role="assistant",
                content=[
                    ToolUseBlock(
                        id="toolu_1",
                        name="bash",
                        input={"command": "touch denied.txt"},
                    )
                ],
            ),
            ConversationMessage(role="assistant", content=[TextBlock(text="done")]),
        ]
    )

    input_stream = io.StringIO('{"type":"submit","prompt":"mutate","request_id":"submit-1"}\n')
    output_stream = io.StringIO()

    await run_headless_control(
        cwd=str(tmp_path),
        api_client=client,
        input_stream=input_stream,
        output_stream=output_stream,
    )

    events = _json_lines(output_stream.getvalue())
    assert any(
        event["type"] == "permission_denied"
        and event["tool_name"] == "bash"
        and event["request_id"] == "submit-1"
        for event in events
    )


@pytest.mark.asyncio
async def test_headless_control_lists_sessions_and_status(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()
    save_session_snapshot(
        cwd=project,
        model="test-model",
        system_prompt="system",
        messages=[ConversationMessage(role="user", content=[TextBlock(text="earlier")])],
        usage=UsageSnapshot(input_tokens=1, output_tokens=1),
        session_id="listed123",
    )

    input_stream = io.StringIO(
        '{"type":"list_sessions","request_id":"list-1"}\n'
        '{"type":"status","request_id":"status-1"}\n'
        '{"type":"shutdown","request_id":"shutdown-1"}\n'
    )
    output_stream = io.StringIO()

    await run_headless_control(
        cwd=str(project),
        api_client=StaticApiClient("unused"),
        input_stream=input_stream,
        output_stream=output_stream,
    )

    events = _json_lines(output_stream.getvalue())
    sessions = next(event for event in events if event["type"] == "sessions")
    assert sessions["request_id"] == "list-1"
    assert sessions["sessions"][0]["session_id"] == "listed123"
    status = next(event for event in events if event["type"] == "state_snapshot")
    assert status["request_id"] == "status-1"
    assert status["session_id"] is None
    assert status["busy"] is False


@pytest.mark.asyncio
async def test_headless_control_interrupts_active_request(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    started = threading.Event()

    async def _blocking_handle_line(*args, **kwargs):
        del args, kwargs
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("openharness.ui.app.handle_line", _blocking_handle_line)

    class _Input:
        def __init__(self) -> None:
            self._submit_sent = False
            self._shutdown_sent = False

        def readline(self) -> str:
            if not self._submit_sent:
                self._submit_sent = True
                return '{"type":"submit","prompt":"block","request_id":"submit-1"}\n'
            started.wait(timeout=2)
            if not hasattr(self, "_status_sent"):
                self._status_sent = True
                return '{"type":"status","request_id":"status-1"}\n'
            if not self._shutdown_sent:
                self._shutdown_sent = True
                return '{"type":"interrupt","request_id":"interrupt-1"}\n'
            return '{"type":"shutdown","request_id":"shutdown-1"}\n'

    output_stream = io.StringIO()

    await run_headless_control(
        cwd=str(tmp_path),
        api_client=StaticApiClient("unused"),
        input_stream=_Input(),
        output_stream=output_stream,
    )

    events = _json_lines(output_stream.getvalue())
    assert any(
        event["type"] == "state_snapshot"
        and event["request_id"] == "status-1"
        and event["busy"] is True
        for event in events
    )
    assert any(event["type"] == "interrupting" and event["request_id"] == "interrupt-1" for event in events)
    assert any(event["type"] == "interrupted" and event["request_id"] == "submit-1" for event in events)
    assert events[-1]["type"] == "shutdown"


class FailingApiClient:
    async def stream_message(self, request):
        del request
        raise RuntimeError("synthetic provider failure")
        yield  # pragma: no cover - makes this an async generator


class BlockingThenStaticApiClient:
    """First stream blocks until cancelled; later streams return text."""

    def __init__(self, text: str, started: threading.Event) -> None:
        self._text = text
        self._started = started
        self._calls = 0

    async def stream_message(self, request):
        del request
        self._calls += 1
        if self._calls == 1:
            self._started.set()
            await asyncio.Event().wait()
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=self._text)]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


class _ScriptedInput:
    """stdin stand-in that can gate lines on a threading.Event."""

    def __init__(self, steps) -> None:
        # steps: list of str or (threading.Event, str)
        self._steps = list(steps)

    def readline(self) -> str:
        if not self._steps:
            return ""
        step = self._steps.pop(0)
        if isinstance(step, tuple):
            event, line = step
            event.wait(timeout=5)
            return line
        return step


@pytest.mark.asyncio
async def test_headless_submit_events_include_usage(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    input_stream = io.StringIO(
        '{"type":"submit","prompt":"hello","request_id":"submit-1"}\n'
        '{"type":"shutdown","request_id":"shutdown-1"}\n'
    )
    output_stream = io.StringIO()
    await run_headless_control(
        cwd=str(tmp_path),
        api_client=StaticApiClient("world"),
        input_stream=input_stream,
        output_stream=output_stream,
    )

    events = _json_lines(output_stream.getvalue())
    complete = next(event for event in events if event["type"] == "assistant_complete")
    assert complete["usage"]["input_tokens"] >= 1
    line_complete = next(event for event in events if event["type"] == "line_complete")
    assert line_complete["usage"]["input_tokens"] >= 1


@pytest.mark.asyncio
async def test_headless_force_shutdown_cancels_and_discards_queue(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    started = threading.Event()

    input_stream = _ScriptedInput(
        [
            '{"type":"submit","prompt":"block","request_id":"submit-1"}\n',
            (started, '{"type":"submit","prompt":"queued","request_id":"submit-2"}\n'),
            '{"type":"shutdown","force":true,"request_id":"shutdown-1"}\n',
        ]
    )
    output_stream = io.StringIO()
    await run_headless_control(
        cwd=str(tmp_path),
        api_client=BlockingThenStaticApiClient("late", started),
        input_stream=input_stream,
        output_stream=output_stream,
    )

    events = _json_lines(output_stream.getvalue())
    assert any(event["type"] == "interrupted" and event.get("request_id") == "submit-1" for event in events)
    assert any(
        event["type"] == "error"
        and event.get("request_id") == "submit-2"
        and "shutting down" in event["message"]
        for event in events
    )
    assert events[-1]["type"] == "shutdown"
    assert events[-1]["request_id"] == "shutdown-1"


@pytest.mark.asyncio
async def test_headless_continue_restores_latest_session(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()
    save_session_snapshot(
        cwd=project,
        model="test-model",
        system_prompt="system",
        messages=[ConversationMessage(role="user", content=[TextBlock(text="earlier")])],
        usage=UsageSnapshot(input_tokens=1, output_tokens=1),
        session_id="cont123",
    )

    input_stream = io.StringIO('{"type":"continue","prompt":"again","request_id":"cont-1"}\n')
    output_stream = io.StringIO()
    await run_headless_control(
        cwd=str(project),
        api_client=StaticApiClient("resumed"),
        input_stream=input_stream,
        output_stream=output_stream,
    )

    events = _json_lines(output_stream.getvalue())
    ready = next(event for event in events if event["type"] == "ready")
    assert ready["resumed"] is True
    assert ready["session_id"] == "cont123"
    assert any(event["type"] == "assistant_complete" and event["text"] == "resumed" for event in events)
    assert any(event["type"] == "line_complete" and event["session_id"] == "cont123" for event in events)


@pytest.mark.asyncio
async def test_headless_errors_are_recoverable(tmp_path: Path, monkeypatch):
    """Malformed requests and bad session ids emit errors without killing the loop."""
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    input_stream = io.StringIO(
        "this is not json\n"
        '{"type":"resume","session_id":"missing","request_id":"r-1"}\n'
        '{"type":"resume","request_id":"r-2"}\n'
        '{"type":"submit","prompt":"hi","session_id":"ghost","request_id":"s-1"}\n'
        '{"type":"submit","request_id":"s-2"}\n'
        '{"type":"permission_response","request_id":"p-1"}\n'
        '{"type":"shutdown","request_id":"shutdown-1"}\n'
    )
    output_stream = io.StringIO()
    await run_headless_control(
        cwd=str(tmp_path),
        api_client=StaticApiClient("unused"),
        input_stream=input_stream,
        output_stream=output_stream,
    )

    events = _json_lines(output_stream.getvalue())
    errors = [event for event in events if event["type"] == "error"]
    assert any("Invalid request" in event["message"] for event in errors)
    assert any(event.get("request_id") == "r-1" and "Session not found: missing" in event["message"] for event in errors)
    assert any(event.get("request_id") == "r-2" and "non-empty session_id" in event["message"] for event in errors)
    assert any(event.get("request_id") == "s-1" and "No active session" in event["message"] for event in errors)
    assert any(event.get("request_id") == "s-2" and "non-empty prompt" in event["message"] for event in errors)
    assert any(event.get("request_id") == "p-1" for event in errors)
    assert events[-1]["type"] == "shutdown"


@pytest.mark.asyncio
async def test_headless_submit_session_id_mismatch(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    input_stream = io.StringIO(
        '{"type":"submit","prompt":"hello","request_id":"submit-1"}\n'
        '{"type":"submit","prompt":"oops","session_id":"nope","request_id":"submit-2"}\n'
        '{"type":"shutdown","request_id":"shutdown-1"}\n'
    )
    output_stream = io.StringIO()
    await run_headless_control(
        cwd=str(tmp_path),
        api_client=StaticApiClient("world"),
        input_stream=input_stream,
        output_stream=output_stream,
    )

    events = _json_lines(output_stream.getvalue())
    assert any(
        event["type"] == "error"
        and event.get("request_id") == "submit-2"
        and "session_id mismatch" in event["message"]
        for event in events
    )


@pytest.mark.asyncio
async def test_headless_full_auto_approves_tools(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    client = SequenceApiClient(
        [
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id="toolu_1", name="bash", input={"command": "echo approved"})],
            ),
            ConversationMessage(role="assistant", content=[TextBlock(text="done")]),
        ]
    )

    input_stream = io.StringIO('{"type":"submit","prompt":"mutate","request_id":"submit-1"}\n')
    output_stream = io.StringIO()
    await run_headless_control(
        cwd=str(tmp_path),
        api_client=client,
        permission_mode="full_auto",
        input_stream=input_stream,
        output_stream=output_stream,
    )

    events = _json_lines(output_stream.getvalue())
    assert not any(event["type"] == "permission_denied" for event in events)
    completed = [event for event in events if event["type"] == "tool_completed"]
    assert completed and completed[0]["tool_name"] == "bash"
    assert any(event["type"] == "assistant_complete" and event["text"] == "done" for event in events)


@pytest.mark.asyncio
async def test_headless_fifo_processes_queued_submits_in_order(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    client = SequenceApiClient(
        [
            ConversationMessage(role="assistant", content=[TextBlock(text="one")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="two")]),
        ]
    )

    input_stream = io.StringIO(
        '{"type":"submit","prompt":"first","request_id":"submit-1"}\n'
        '{"type":"submit","prompt":"second","request_id":"submit-2"}\n'
        '{"type":"shutdown","request_id":"shutdown-1"}\n'
    )
    output_stream = io.StringIO()
    await run_headless_control(
        cwd=str(tmp_path),
        api_client=client,
        input_stream=input_stream,
        output_stream=output_stream,
    )

    events = _json_lines(output_stream.getvalue())
    completes = [event for event in events if event["type"] == "assistant_complete"]
    assert [event["text"] for event in completes] == ["one", "two"]
    assert [event["request_id"] for event in completes] == ["submit-1", "submit-2"]
    line_completes = [event for event in events if event["type"] == "line_complete"]
    assert [event["request_id"] for event in line_completes] == ["submit-1", "submit-2"]
    assert events[-1]["type"] == "shutdown"


@pytest.mark.asyncio
async def test_headless_interrupt_cancels_engine_and_allows_resubmit(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    started = threading.Event()

    input_stream = _ScriptedInput(
        [
            '{"type":"submit","prompt":"block","request_id":"submit-1"}\n',
            (started, '{"type":"interrupt","request_id":"interrupt-1"}\n'),
            '{"type":"submit","prompt":"retry","request_id":"submit-2"}\n',
            '{"type":"shutdown","request_id":"shutdown-1"}\n',
        ]
    )
    output_stream = io.StringIO()
    await run_headless_control(
        cwd=str(tmp_path),
        api_client=BlockingThenStaticApiClient("after", started),
        input_stream=input_stream,
        output_stream=output_stream,
    )

    events = _json_lines(output_stream.getvalue())
    assert any(event["type"] == "interrupting" and event["request_id"] == "interrupt-1" for event in events)
    assert any(event["type"] == "interrupted" and event.get("request_id") == "submit-1" for event in events)
    # The session survives interruption: the next submit completes normally.
    assert any(
        event["type"] == "assistant_complete"
        and event["text"] == "after"
        and event["request_id"] == "submit-2"
        for event in events
    )
    # The interrupted exchange was persisted for resume.
    from openharness.services.session_storage import load_session_snapshot

    snapshot = load_session_snapshot(str(tmp_path))
    assert snapshot is not None
    assert any(
        message.get("role") == "user"
        and any(block.get("text") == "block" for block in message.get("content", []) if block.get("type") == "text")
        for message in snapshot["messages"]
    )


@pytest.mark.asyncio
async def test_print_mode_stream_json_includes_session_and_usage(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    exit_code = await run_print_mode(
        prompt="hello",
        output_format="stream-json",
        cwd=str(tmp_path),
        api_client=StaticApiClient("world"),
        session_id="stream123",
    )

    events = _json_lines(capsys.readouterr().out)
    assert exit_code == 0
    assert events, "expected stream-json events"
    assert all(event.get("session_id") == "stream123" for event in events)
    line_complete = next(event for event in events if event["type"] == "line_complete")
    assert line_complete["usage"]["input_tokens"] >= 1


@pytest.mark.asyncio
async def test_print_mode_json_reports_errors_and_exit_code(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    exit_code = await run_print_mode(
        prompt="hello",
        output_format="json",
        cwd=str(tmp_path),
        api_client=FailingApiClient(),
        session_id="err123",
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["is_error"] is True
    assert payload["errors"] and "synthetic provider failure" in payload["errors"][0]


@pytest.mark.asyncio
async def test_print_mode_json_reports_permission_denials(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    client = SequenceApiClient(
        [
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id="toolu_1", name="bash", input={"command": "touch denied.txt"})],
            ),
            ConversationMessage(role="assistant", content=[TextBlock(text="done")]),
        ]
    )

    exit_code = await run_print_mode(
        prompt="mutate",
        output_format="json",
        cwd=str(tmp_path),
        api_client=client,
        session_id="deny123",
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["is_error"] is False
    assert payload["permission_denials"][0]["tool_name"] == "bash"


@pytest.mark.asyncio
async def test_headless_force_shutdown_during_restore_skips_followup_turn(tmp_path: Path, monkeypatch):
    """A force shutdown arriving while a resume rebuilds must not start a new turn."""
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()
    save_session_snapshot(
        cwd=project,
        model="test-model",
        system_prompt="system",
        messages=[ConversationMessage(role="user", content=[TextBlock(text="earlier")])],
        usage=UsageSnapshot(input_tokens=1, output_tokens=1),
        session_id="slowres",
    )

    building = threading.Event()
    release = threading.Event()
    import openharness.ui.app as app_module

    real_build_runtime = app_module.build_runtime

    async def slow_build_runtime(**kwargs):
        building.set()
        await asyncio.to_thread(release.wait, 5)
        return await real_build_runtime(**kwargs)

    monkeypatch.setattr("openharness.ui.app.build_runtime", slow_build_runtime)

    class _Input:
        def __init__(self) -> None:
            self._sent = 0

        def readline(self) -> str:
            self._sent += 1
            if self._sent == 1:
                return (
                    '{"type":"resume","session_id":"slowres","prompt":"SHOULD-NOT-RUN","request_id":"r-1"}\n'
                )
            if self._sent == 2:
                building.wait(timeout=5)
                line = '{"type":"shutdown","force":true,"request_id":"d-force"}\n'
                release.set()
                return line
            return ""

    output_stream = io.StringIO()
    await run_headless_control(
        cwd=str(project),
        api_client=StaticApiClient("SHOULD-NOT-RUN"),
        input_stream=_Input(),
        output_stream=output_stream,
    )

    events = _json_lines(output_stream.getvalue())
    # Depending on which side wins the race, the follow-up turn is either
    # rejected before it starts or cancelled immediately — never completed.
    assert not any(event["type"] == "assistant_complete" for event in events)
    assert any(
        (
            event["type"] == "error"
            and event.get("request_id") == "r-1"
            and "shutting down" in event["message"]
        )
        or (event["type"] == "interrupted" and event.get("request_id") == "r-1")
        for event in events
    )
    assert events[-1]["type"] == "shutdown"


@pytest.mark.asyncio
async def test_headless_graceful_shutdown_rejects_later_requests(tmp_path: Path, monkeypatch):
    """Requests queued behind a plain shutdown get an explicit error, not silence."""
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    input_stream = io.StringIO(
        '{"type":"submit","prompt":"hello","request_id":"submit-1"}\n'
        '{"type":"shutdown","request_id":"shutdown-1"}\n'
        '{"type":"submit","prompt":"too late","request_id":"submit-2"}\n'
    )
    output_stream = io.StringIO()
    await run_headless_control(
        cwd=str(tmp_path),
        api_client=StaticApiClient("world"),
        input_stream=input_stream,
        output_stream=output_stream,
    )

    events = _json_lines(output_stream.getvalue())
    assert any(
        event["type"] == "assistant_complete" and event["request_id"] == "submit-1" for event in events
    )
    assert any(
        event["type"] == "error"
        and event.get("request_id") == "submit-2"
        and "shutting down" in event["message"]
        for event in events
    )
    assert events[-1]["type"] == "shutdown"
    assert events[-1]["request_id"] == "shutdown-1"


@pytest.mark.asyncio
async def test_print_mode_json_surfaces_max_turns_notice(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    client = SequenceApiClient(
        [
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id="toolu_1", name="bash", input={"command": "echo a"})],
            ),
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id="toolu_2", name="bash", input={"command": "echo b"})],
            ),
        ]
    )

    exit_code = await run_print_mode(
        prompt="loop forever",
        output_format="json",
        cwd=str(tmp_path),
        api_client=client,
        session_id="turns123",
        permission_mode="full_auto",
        max_turns=1,
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert any("max_turns" in message for message in payload["system_messages"])
