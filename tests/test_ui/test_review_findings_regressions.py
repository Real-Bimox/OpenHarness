"""Regressions pinned from the v0.1.15 post-release review."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

OH = str(Path(sys.executable).parent / "oh")


def test_headless_search_sessions_first_run_empty_index_real_subprocess(tmp_path: Path):
    """First run, empty index, real subprocess: must answer and exit, never hang."""
    batch = (
        '{"type":"search_sessions","query":"anything at all","project":"all","request_id":"q-1"}\n'
        '{"type":"search_sessions","request_id":"q-2"}\n'
        '{"type":"shutdown","request_id":"d-1"}\n'
    )
    result = subprocess.run(
        [OH, "--headless", "--bare", "--cwd", str(tmp_path),
         "--api-format", "openai", "--api-key", "k",
         "--base-url", "http://127.0.0.1:9/v1", "--model", "m"],
        input=batch, capture_output=True, text=True, timeout=60,
        env={"OPENHARNESS_CONFIG_DIR": str(tmp_path / "c"),
             "OPENHARNESS_DATA_DIR": str(tmp_path / "d"),
             "PATH": "/usr/bin:/bin"},
    )
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    types = [e["type"] for e in events]
    assert types.count("session_search_results") == 2, result.stdout
    assert types[-1] == "shutdown"
    assert result.returncode == 0


@pytest.mark.asyncio
async def test_headless_search_sessions_respects_disabled_setting(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "settings.json").write_text(
        json.dumps({"conversation_index_enabled": False})
    )
    from openharness.api.client import ApiMessageCompleteEvent
    from openharness.api.usage import UsageSnapshot
    from openharness.engine.messages import ConversationMessage, TextBlock
    from openharness.ui.app import run_headless_control

    class _Client:
        async def stream_message(self, request):
            del request
            yield ApiMessageCompleteEvent(
                message=ConversationMessage(role="assistant", content=[TextBlock(text="x")]),
                usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                stop_reason=None,
            )

    input_stream = io.StringIO(
        '{"type":"search_sessions","query":"x","request_id":"q-1"}\n{"type":"shutdown","request_id":"d-1"}\n'
    )
    output_stream = io.StringIO()
    await run_headless_control(
        cwd=str(tmp_path), api_client=_Client(), input_stream=input_stream, output_stream=output_stream
    )
    events = [json.loads(line) for line in output_stream.getvalue().splitlines() if line.strip()]
    error = next(e for e in events if e["type"] == "error" and e.get("request_id") == "q-1")
    assert "disabled" in error["message"]
    assert not any(e["type"] == "session_search_results" for e in events)


def test_cli_sessions_respects_disabled_setting(tmp_path: Path, monkeypatch):
    from typer.testing import CliRunner

    import openharness.cli as cli

    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "settings.json").write_text(
        json.dumps({"conversation_index_enabled": False})
    )
    runner = CliRunner()
    for args in (["sessions", "list"], ["sessions", "search", "x"], ["sessions", "reindex"]):
        result = runner.invoke(cli.app, args)
        assert result.exit_code == 1, args


@pytest.mark.asyncio
async def test_mcp_search_respects_disabled_setting(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "settings.json").write_text(
        json.dumps({"conversation_index_enabled": False})
    )
    from openharness.mcp.serve import build_server

    server = build_server()
    for tool_name, args in (("search_sessions", {"query": "x"}), ("list_sessions", {})):
        result = await server.call_tool(tool_name, args)
        content = result[0] if isinstance(result, tuple) else result
        item = content[0] if isinstance(content, (list, tuple)) else content
        text = getattr(item, "text", str(item))
        assert "disabled" in text, tool_name


def test_mcp_serve_rejects_conflicting_flags(tmp_path: Path, monkeypatch):
    from typer.testing import CliRunner

    import openharness.cli as cli

    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    runner = CliRunner()
    for extra in (["--headless"], ["--task-worker"], ["-p", "hi"], ["--dry-run"], ["--continue"]):
        result = runner.invoke(cli.app, ["--mcp-serve", *extra])
        assert result.exit_code == 1, extra
