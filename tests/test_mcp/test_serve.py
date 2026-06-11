"""Tests for the MCP server mode (oh --mcp-serve)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _seed(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))


@pytest.mark.asyncio
async def test_server_lists_expected_tools(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    from openharness.mcp.serve import build_server

    server = build_server()
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert {"search_sessions", "list_sessions", "skill_loop_status", "run_skill_curator", "recovery_status"} <= names


@pytest.mark.asyncio
async def test_search_sessions_tool_returns_index_hits(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    from openharness.mcp.serve import build_server
    from openharness.services.conversation_index import get_conversation_index

    get_conversation_index().index_snapshot(
        {
            "session_id": "mcp1",
            "cwd": str(tmp_path),
            "model": "m",
            "summary": "mcp talk",
            "created_at": 1.0,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "expose via mcp please"}]}],
        }
    )
    server = build_server()
    result = await server.call_tool("search_sessions", {"query": "expose mcp", "project": "all"})
    payload = _extract_json(result)
    assert payload["mode"] == "discover"
    assert payload["hits"][0]["session_id"] == "mcp1"


@pytest.mark.asyncio
async def test_recovery_status_reflects_settings(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "settings.json").write_text(
        json.dumps(
            {
                "fallback_providers": [{"provider": "openai", "model": "gpt-x"}],
                "credential_pools": {"anthropic": ["k1", "k2"]},
            }
        )
    )
    from openharness.mcp.serve import build_server

    server = build_server()
    result = await server.call_tool("recovery_status", {})
    payload = _extract_json(result)
    assert payload["fallback_providers"][0]["provider"] == "openai"
    assert payload["credential_pools"]["anthropic"] == 2


@pytest.mark.asyncio
async def test_skill_loop_status_tool(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    from openharness.mcp.serve import build_server
    from openharness.skills import usage
    from openharness.skills.loader import get_user_skills_dir

    skills_dir = get_user_skills_dir()
    (skills_dir / "s").mkdir(parents=True)
    (skills_dir / "s" / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\n\nbody")
    usage.bump_use("s", skills_dir)
    server = build_server()
    result = await server.call_tool("skill_loop_status", {})
    payload = _extract_json(result)
    assert payload["skills"]["s"]["use_count"] == 1


def _extract_json(result) -> dict:
    """FastMCP call_tool returns (content_list, ...) or content_list across versions."""
    content = result[0] if isinstance(result, tuple) else result
    # structured-content dict form
    if isinstance(result, tuple) and len(result) > 1 and isinstance(result[1], dict):
        if "result" in result[1]:
            return json.loads(result[1]["result"]) if isinstance(result[1]["result"], str) else result[1]["result"]
    item = content[0] if isinstance(content, (list, tuple)) else content
    text = getattr(item, "text", None) or (item.get("text") if isinstance(item, dict) else str(item))
    return json.loads(text)
