"""Tests for the session_search agent tool and headless surface."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from openharness.tools.base import ToolExecutionContext


def _seed_index(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    from openharness.services.conversation_index import get_conversation_index

    index = get_conversation_index()
    index.index_snapshot(
        {
            "session_id": "hist1",
            "cwd": str(tmp_path),
            "model": "m",
            "summary": "deploy talk",
            "created_at": 1.0,
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "how do we deploy the gateway"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "use the compose file"}]},
            ],
        }
    )
    return index


@pytest.mark.asyncio
async def test_tool_discover_and_read_only(tmp_path: Path, monkeypatch):
    _seed_index(tmp_path, monkeypatch)
    from openharness.tools.session_search_tool import SessionSearchTool

    tool = SessionSearchTool()
    args = tool.input_model.model_validate({"query": "deploy gateway", "project": "all"})
    assert tool.is_read_only(args) is True
    result = await tool.execute(args, ToolExecutionContext(cwd=tmp_path, metadata={"session_id": "current"}))
    payload = json.loads(result.output)
    assert payload["success"] is True
    assert payload["mode"] == "discover"
    assert payload["hits"][0]["session_id"] == "hist1"


@pytest.mark.asyncio
async def test_tool_excludes_active_session_and_browse(tmp_path: Path, monkeypatch):
    _seed_index(tmp_path, monkeypatch)
    from openharness.tools.session_search_tool import SessionSearchTool

    tool = SessionSearchTool()
    ctx = ToolExecutionContext(cwd=tmp_path, metadata={"session_id": "hist1"})
    read_self = await tool.execute(
        tool.input_model.model_validate({"session_id": "hist1"}), ctx
    )
    assert json.loads(read_self.output)["success"] is False
    browse = await tool.execute(tool.input_model.model_validate({"project": "all"}), ctx)
    payload = json.loads(browse.output)
    assert payload["mode"] == "browse"
    assert all(row["session_id"] != "hist1" for row in payload["sessions"])


@pytest.mark.asyncio
async def test_tool_disabled_by_setting(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "settings.json").write_text(
        json.dumps({"conversation_index_enabled": False})
    )
    from openharness.tools.session_search_tool import SessionSearchTool

    tool = SessionSearchTool()
    result = await tool.execute(
        tool.input_model.model_validate({"query": "anything"}),
        ToolExecutionContext(cwd=tmp_path),
    )
    payload = json.loads(result.output)
    assert payload["success"] is False
    assert "disabled" in payload["error"]


@pytest.mark.asyncio
async def test_headless_search_sessions_request(tmp_path: Path, monkeypatch):
    _seed_index(tmp_path, monkeypatch)
    from openharness.api.client import ApiMessageCompleteEvent
    from openharness.api.usage import UsageSnapshot
    from openharness.engine.messages import ConversationMessage, TextBlock
    from openharness.ui.app import run_headless_control

    class StaticApiClient:
        async def stream_message(self, request):
            del request
            yield ApiMessageCompleteEvent(
                message=ConversationMessage(role="assistant", content=[TextBlock(text="unused")]),
                usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                stop_reason=None,
            )

    input_stream = io.StringIO(
        json.dumps({"type": "search_sessions", "query": "deploy gateway", "project": "all", "request_id": "q-1"})
        + "\n"
        + '{"type":"shutdown","request_id":"d-1"}\n'
    )
    output_stream = io.StringIO()
    await run_headless_control(
        cwd=str(tmp_path),
        api_client=StaticApiClient(),
        input_stream=input_stream,
        output_stream=output_stream,
    )
    events = [json.loads(line) for line in output_stream.getvalue().splitlines() if line.strip()]
    result = next(event for event in events if event["type"] == "session_search_results")
    assert result["request_id"] == "q-1"
    assert result["mode"] == "discover"
    assert result["hits"][0]["session_id"] == "hist1"
