"""Tests for local headless control surfaces."""

from __future__ import annotations

import io
import json
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

    await run_print_mode(
        prompt="hello",
        output_format="json",
        cwd=str(tmp_path),
        api_client=StaticApiClient("world"),
        session_id="print123",
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"type": "result", "session_id": "print123", "text": "world"}


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
    assert events[0]["type"] == "ready"
    assert any(
        event["type"] == "assistant_complete"
        and event["text"] == "world"
        and event["request_id"] == "submit-1"
        for event in events
    )
    assert any(
        event["type"] == "line_complete"
        and event["request_id"] == "submit-1"
        and event["session_id"] == events[0]["session_id"]
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
