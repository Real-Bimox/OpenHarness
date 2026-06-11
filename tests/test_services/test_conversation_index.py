"""Tests for the derived conversation search index."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openharness.services.conversation_index import (
    ConversationIndex,
    sanitize_fts_query,
)


def _snapshot(session_id: str, texts: list[str], *, cwd: str = "/tmp/projA", roles=None):
    roles = roles or ["user", "assistant"] * len(texts)
    return {
        "session_id": session_id,
        "cwd": cwd,
        "model": "test-model",
        "summary": texts[0][:40] if texts else "",
        "created_at": 1000.0,
        "messages": [
            {"role": roles[i % len(roles)], "content": [{"type": "text", "text": text}]}
            for i, text in enumerate(texts)
        ],
    }


@pytest.fixture()
def index(tmp_path: Path):
    idx = ConversationIndex(db_path=tmp_path / "ci.db")
    yield idx
    idx.close()


def test_index_and_discover_roundtrip(index: ConversationIndex):
    index.index_snapshot(_snapshot("s1", [
        "let us refactor the billing module",
        "sure, the billing module refactor is done",
        "now write tests for it",
        "tests written and passing",
    ]))
    index.index_snapshot(_snapshot("s2", ["unrelated chatter about lunch"]))

    result = index.search("billing refactor", project="/tmp/projA")
    assert "error" not in result
    assert len(result["hits"]) == 1
    hit = result["hits"][0]
    assert hit["session_id"] == "s1"
    assert ">>>" in hit["snippet"] or "billing" in hit["snippet"]
    assert isinstance(hit["messages_before"], int)
    assert isinstance(hit["messages_after"], int)
    assert any(m.get("anchor") for m in hit["messages"])


def test_secrets_are_redacted_before_indexing(index: ConversationIndex):
    secret = "sk-" + "a" * 24
    index.index_snapshot(
        {
            "session_id": "s3",
            "cwd": "/tmp/projA",
            "model": "m",
            "summary": "",
            "created_at": 1.0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "content": f"export OPENAI_API_KEY={secret}"}
                    ],
                }
            ],
        }
    )
    hit_by_secret = index.search(secret, project="all", role_filter=["user"])
    assert hit_by_secret["hits"] == []
    redacted = index.search("redacted", project="all", role_filter=["user"])
    assert redacted["hits"], "redaction marker should be searchable"
    assert secret not in json.dumps(redacted)


def test_output_budget_truncates_messages(index: ConversationIndex):
    index.index_snapshot(_snapshot("s4", ["needle " + "x" * 7_000]))
    result = index.search("needle", project="all")
    message = result["hits"][0]["messages"][0]
    assert message["truncated"] is True
    assert len(message["content"]) <= 2_000


def test_sanitizer_handles_abuse():
    assert sanitize_fts_query('AND OR NOT') is None
    assert sanitize_fts_query('"unbalanced') == '"unbalanced"'
    assert sanitize_fts_query("term1 AND term2") == '"term1" AND "term2"'
    assert sanitize_fts_query("pre*") == '"pre"*'
    assert sanitize_fts_query('a:b (c) "x y"') == '"a:b" "(c)" "x y"'


def test_empty_query_after_sanitize_is_explicit_error(index: ConversationIndex):
    index.index_snapshot(_snapshot("s5", ["hello world content"]))
    result = index.search('AND OR', project="all")
    assert "error" in result


def test_incremental_and_shrink_reindex(index: ConversationIndex):
    snapshot = _snapshot("s6", ["alpha first message", "beta second message"])
    index.index_snapshot(snapshot)
    snapshot["messages"].append(
        {"role": "user", "content": [{"type": "text", "text": "gamma third message"}]}
    )
    index.index_snapshot(snapshot)
    assert index.search("gamma", project="all")["hits"]
    # Shrink (compaction rewrote history) forces a clean reindex.
    shrunk = _snapshot("s6", ["delta replaces everything"])
    index.index_snapshot(shrunk)
    assert index.search("alpha", project="all")["hits"] == []
    assert index.search("delta", project="all")["hits"]


def test_exclude_current_session(index: ConversationIndex):
    index.index_snapshot(_snapshot("active", ["shared keyword findme"]))
    index.index_snapshot(_snapshot("other", ["shared keyword findme"]))
    result = index.search("findme", project="all", exclude_session="active")
    assert [h["session_id"] for h in result["hits"]] == ["other"]


def test_read_and_around_and_browse(index: ConversationIndex):
    index.index_snapshot(_snapshot("s7", [f"message number {i}" for i in range(40)]))
    read = index.read_session("s7")
    assert read["truncated"] is True
    assert len(read["messages"]) == 30
    anchor = read["messages"][0]["id"]
    around = index.around("s7", anchor, window=2)
    assert "error" not in around
    assert around["messages_before"] == 0  # first message: honest global count
    assert index.around("s7", 999_999)["error"]
    assert index.read_session("nope")["error"]
    browse = index.browse(project="all")
    assert browse["sessions"][0]["session_id"] == "s7"


def test_rebuild_from_snapshots(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    from openharness.api.usage import UsageSnapshot
    from openharness.engine.messages import ConversationMessage, TextBlock
    from openharness.services.session_storage import save_session_snapshot

    save_session_snapshot(
        cwd=tmp_path / "proj",
        model="m",
        system_prompt="sp",
        messages=[ConversationMessage(role="user", content=[TextBlock(text="rebuild me please")])],
        usage=UsageSnapshot(),
        session_id="rb1",
    )
    idx = ConversationIndex(db_path=tmp_path / "ci.db")
    try:
        count = idx.rebuild()
        assert count >= 1
        assert idx.search("rebuild", project="all")["hits"]
    finally:
        idx.close()


def test_save_snapshot_feeds_index(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    from openharness.api.usage import UsageSnapshot
    from openharness.engine.messages import ConversationMessage, TextBlock
    from openharness.services.conversation_index import (
        flush_index_queue,
        get_conversation_index,
    )
    from openharness.services.session_storage import save_session_snapshot

    save_session_snapshot(
        cwd=tmp_path / "proj",
        model="m",
        system_prompt="sp",
        messages=[ConversationMessage(role="user", content=[TextBlock(text="hooked into saves")])],
        usage=UsageSnapshot(),
        session_id="hk1",
    )
    flush_index_queue()
    assert get_conversation_index().search("hooked", project="all")["hits"]
