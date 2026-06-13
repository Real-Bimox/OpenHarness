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
    cfg = tmp_path / "cfg"; cfg.mkdir()
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(cfg))
    # setting=v1, on-disk is v2 (head present) -> must still detect v2
    (cfg / "settings.json").write_text(_json.dumps({"session_storage_format": "v1"}), encoding="utf-8")
    (tmp_path / "session-shaped_v2.head.json").write_text("{}", encoding="utf-8")
    assert detect_session_format(tmp_path, "shaped_v2") == "v2"
    # setting=v2, on-disk is v1 (lone .json) -> must still detect v1
    (cfg / "settings.json").write_text(_json.dumps({"session_storage_format": "v2"}), encoding="utf-8")
    (tmp_path / "session-shaped_v1.json").write_text("{}", encoding="utf-8")
    assert detect_session_format(tmp_path, "shaped_v1") == "v1"
