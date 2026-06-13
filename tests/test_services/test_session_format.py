"""Tests for the v1/v2 session format primitives."""

from __future__ import annotations

from pathlib import Path

from openharness.services.session_format import detect_latest_format, detect_session_format


def test_detect_latest_pointer_is_v2():
    assert detect_latest_format({"session_id": "abc123"}) == "v2"


def test_detect_latest_full_payload_is_v1():
    assert detect_latest_format({"session_id": "abc", "model": "m", "messages": []}) == "v1"


def test_detect_latest_empty_is_v1():
    assert detect_latest_format({}) == "v1"


def test_detect_session_format_head_present_is_v2(tmp_path: Path):
    (tmp_path / "session-abc.head.json").write_text("{}", encoding="utf-8")
    assert detect_session_format(tmp_path, "abc") == "v2"


def test_detect_session_format_only_json_is_v1(tmp_path: Path):
    (tmp_path / "session-abc.json").write_text("{}", encoding="utf-8")
    assert detect_session_format(tmp_path, "abc") == "v1"


def test_detect_session_format_missing_is_none(tmp_path: Path):
    assert detect_session_format(tmp_path, "ghost") is None


def test_detect_session_format_headless_transcript_is_v2(tmp_path: Path):
    # V2_HEADLESS (C.3): transcript present, head lost in a crash -> still v2.
    (tmp_path / "session-abc.jsonl").write_text("", encoding="utf-8")
    assert detect_session_format(tmp_path, "abc") == "v2"


def test_detect_session_format_v1_v2_conflict_prefers_v2(tmp_path: Path):
    # CONFLICT (C.3): a legacy .json and v2 files coexist -> v2 wins.
    (tmp_path / "session-abc.json").write_text("{}", encoding="utf-8")
    (tmp_path / "session-abc.head.json").write_text("{}", encoding="utf-8")
    assert detect_session_format(tmp_path, "abc") == "v2"


def test_detect_session_format_ignores_the_setting(tmp_path: Path, monkeypatch):
    # C.3 / Design decision 2: the sniffer reads on-disk SHAPE ONLY, never the
    # session_storage_format setting (the setting gates WRITES, not reads). Set the
    # setting to CONTRADICT the on-disk shape, both ways, and assert shape always wins —
    # this is what keeps a v1 session readable when the setting is "v2" and vice versa
    # ("every legacy file readable forever"). A sniffer that consulted the setting fails here.
    import json as _json
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(cfg))
    # setting=v1, on-disk is v2 (head present) -> must still detect v2
    (cfg / "settings.json").write_text(_json.dumps({"session_storage_format": "v1"}), encoding="utf-8")
    (tmp_path / "session-shaped_v2.head.json").write_text("{}", encoding="utf-8")
    assert detect_session_format(tmp_path, "shaped_v2") == "v2"
    # setting=v2, on-disk is v1 (lone .json) -> must still detect v1
    (cfg / "settings.json").write_text(_json.dumps({"session_storage_format": "v2"}), encoding="utf-8")
    (tmp_path / "session-shaped_v1.json").write_text("{}", encoding="utf-8")
    assert detect_session_format(tmp_path, "shaped_v1") == "v1"


def test_system_prompt_fingerprint_is_stable_sha256():
    from openharness.services.session_format import system_prompt_fingerprint

    fp = system_prompt_fingerprint("You are a helpful assistant.")
    assert fp == system_prompt_fingerprint("You are a helpful assistant.")
    assert len(fp) == 64  # sha256 hex digest
    assert fp != system_prompt_fingerprint("different")


def test_system_prompt_fingerprint_empty():
    from openharness.services.session_format import system_prompt_fingerprint

    assert len(system_prompt_fingerprint("")) == 64


def test_write_and_read_head_round_trip(tmp_path: Path):
    from openharness.services.session_format import read_head, write_head

    head = {
        "session_id": "abc123",
        "model": "claude-test",
        "system_prompt_sha256": "deadbeef" * 8,
        "usage": {"input_tokens": 1, "output_tokens": 2},
        "tool_metadata": {"permission_mode": "default"},
        "message_count": 3,
        "summary": "hello",
        "created_at": 1.0,
    }
    write_head(tmp_path, "abc123", head)
    assert (tmp_path / "session-abc123.head.json").exists()
    loaded = read_head(tmp_path, "abc123")
    assert loaded == head


def test_read_head_missing_returns_none(tmp_path: Path):
    from openharness.services.session_format import read_head

    assert read_head(tmp_path, "ghost") is None


def _msgs(*texts):
    from openharness.engine.messages import ConversationMessage, TextBlock

    return [ConversationMessage(role="user", content=[TextBlock(text=t)]) for t in texts]


def test_append_messages_delta_only(tmp_path: Path):
    from openharness.services.session_format import (
        append_messages_to_transcript,
        load_v2_snapshot,
    )

    append_messages_to_transcript(tmp_path, "s1", _msgs("a", "b"), last_persisted_count=0)
    append_messages_to_transcript(tmp_path, "s1", _msgs("a", "b", "c"), last_persisted_count=2)
    snap = load_v2_snapshot(tmp_path, "s1")
    assert [m["content"][0]["text"] for m in snap] == ["a", "b", "c"]


def test_compaction_rewrites_and_load_keeps_post_marker(tmp_path: Path):
    from openharness.services.session_format import (
        append_messages_to_transcript,
        load_v2_snapshot,
        rewrite_transcript,
    )

    append_messages_to_transcript(tmp_path, "s1", _msgs("a", "b", "c"), last_persisted_count=0)
    # Compaction collapses history to a single summary message.
    rewrite_transcript(tmp_path, "s1", _msgs("summary"))
    snap = load_v2_snapshot(tmp_path, "s1")
    assert [m["content"][0]["text"] for m in snap] == ["summary"]


def test_load_v2_recovers_from_truncated_final_line(tmp_path: Path):
    from openharness.services.session_format import load_v2_snapshot, transcript_path

    # Two complete records then a crash mid-third (no newline).
    transcript_path(tmp_path, "s1").write_bytes(
        b'{"role": "user", "content": [{"type": "text", "text": "a"}]}\n'
        b'{"role": "user", "content": [{"type": "text", "text": "b"}]}\n'
        b'{"role": "user", "content": [{"type": "text", "text": "c"'
    )
    snap = load_v2_snapshot(tmp_path, "s1")
    assert [m["content"][0]["text"] for m in snap] == ["a", "b"]


def test_record_with_marker_key_but_role_is_a_message_not_a_marker(tmp_path: Path):
    # Typed dispatch (C.5, P2-003): a record carrying both the marker key and a
    # "role" is a message, not a marker, so it must NOT wipe history. Written as
    # a raw line to bypass the message schema and exercise the discriminator.
    from openharness.services.session_format import load_v2_snapshot, transcript_path

    transcript_path(tmp_path, "s1").write_bytes(
        b'{"role": "user", "content": [{"type": "text", "text": "a"}]}\n'
        b'{"__compacted_at__": 123, "role": "user", "content": [{"type": "text", "text": "b"}]}\n'
    )
    snap = load_v2_snapshot(tmp_path, "s1")
    assert [m["content"][0]["text"] for m in snap] == ["a", "b"]


def test_transcript_live_count_counts_post_marker_records(tmp_path: Path):
    from openharness.services.session_format import (
        append_messages_to_transcript,
        rewrite_transcript,
        transcript_live_count,
    )

    assert transcript_live_count(tmp_path, "s1") == 0  # absent transcript
    append_messages_to_transcript(tmp_path, "s1", _msgs("a", "b"), last_persisted_count=0)
    assert transcript_live_count(tmp_path, "s1") == 2
    rewrite_transcript(tmp_path, "s1", _msgs("summary"))  # compaction
    assert transcript_live_count(tmp_path, "s1") == 1  # only the post-marker record


def test_fingerprint_messages_detects_in_place_content_change(tmp_path: Path):
    # R-001: the signal a count test misses. Same message COUNT, changed content
    # (an in-place compaction) MUST yield a different fingerprint; and a message
    # object must fingerprint equal to the dict it was persisted as (the seed path
    # reads dicts via load_v2_snapshot, the compare path uses live objects).
    from openharness.services.session_format import (
        append_messages_to_transcript,
        fingerprint_messages,
        load_v2_snapshot,
    )

    original = _msgs("tool-output-aaaa", "b")
    cleared_same_count = _msgs("cleared", "b")  # in place: count unchanged, content changed
    assert len(original) == len(cleared_same_count)
    assert fingerprint_messages(original) != fingerprint_messages(cleared_same_count)
    assert fingerprint_messages(original) == fingerprint_messages(original)  # stable
    append_messages_to_transcript(tmp_path, "s1", original, last_persisted_count=0)
    assert fingerprint_messages(load_v2_snapshot(tmp_path, "s1")) == fingerprint_messages(original)
