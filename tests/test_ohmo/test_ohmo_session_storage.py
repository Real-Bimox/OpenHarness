import json
from pathlib import Path

from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage

from ohmo.session_storage import OhmoSessionBackend, get_session_dir, list_snapshots, save_session_snapshot
from ohmo.workspace import initialize_workspace


def test_ohmo_session_backend_uses_workspace_sessions(tmp_path: Path):
    workspace = tmp_path / ".ohmo-home"
    initialize_workspace(workspace)
    backend = OhmoSessionBackend(workspace)
    message = ConversationMessage.from_user_text("hello ohmo")
    backend.save_snapshot(
        cwd=tmp_path,
        model="gpt-5.4",
        system_prompt="system",
        messages=[message],
        usage=UsageSnapshot(),
        session_id="abc123",
    )

    session_dir = get_session_dir(workspace)
    assert session_dir == workspace / "sessions"
    assert (session_dir / "latest.json").exists()
    assert backend.load_by_id(tmp_path, "abc123") is not None


def test_ohmo_session_backend_loads_latest_for_session_key(tmp_path: Path):
    workspace = tmp_path / ".ohmo-home"
    initialize_workspace(workspace)
    backend = OhmoSessionBackend(workspace)
    message = ConversationMessage.from_user_text("hello thread")
    backend.save_snapshot(
        cwd=tmp_path,
        model="gpt-5.4",
        system_prompt="system",
        messages=[message],
        usage=UsageSnapshot(),
        session_id="abc123",
        session_key="feishu:chat-1",
        tool_metadata={
            "task_focus_state": {"goal": "Continue the same Feishu task"},
            "recent_verified_work": ["Verified the compact attachment order"],
        },
    )

    loaded = backend.load_latest_for_session_key("feishu:chat-1")
    assert loaded is not None
    assert loaded["session_id"] == "abc123"
    assert loaded["session_key"] == "feishu:chat-1"
    assert loaded["tool_metadata"]["task_focus_state"]["goal"] == "Continue the same Feishu task"


def test_ohmo_list_snapshots_merges_index_with_legacy_files(tmp_path: Path):
    workspace = tmp_path / ".ohmo-home"
    initialize_workspace(workspace)
    session_dir = get_session_dir(workspace)
    (session_dir / "session-legacy-session.json").write_text(
        json.dumps(
            {
                "session_id": "legacy-session",
                "summary": "legacy",
                "message_count": 1,
                "model": "gpt-legacy",
                "created_at": 1.0,
                "messages": [],
            }
        ),
        encoding="utf-8",
    )

    save_session_snapshot(
        cwd=tmp_path,
        workspace=workspace,
        model="gpt-5.4",
        system_prompt="system",
        messages=[ConversationMessage.from_user_text("indexed hello")],
        usage=UsageSnapshot(),
        session_id="indexed-session",
    )

    session_ids = {item["session_id"] for item in list_snapshots(workspace, limit=10)}
    assert session_ids == {"indexed-session", "legacy-session"}


def test_ohmo_session_backend_sanitizes_legacy_empty_assistant_messages(tmp_path: Path):
    workspace = tmp_path / ".ohmo-home"
    initialize_workspace(workspace)
    backend = OhmoSessionBackend(workspace)
    session_dir = get_session_dir(workspace)
    (session_dir / "latest.json").write_text(
        json.dumps(
            {
                "app": "ohmo",
                "session_id": "abc123",
                "session_key": "feishu:chat-1",
                "cwd": str(tmp_path),
                "model": "gpt-5.4",
                "system_prompt": "system",
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "hello"}]},
                    {"role": "assistant", "content": None},
                    {"role": "assistant", "content": []},
                ],
                "usage": {"input_tokens": 0, "output_tokens": 0},
                "tool_metadata": {},
                "created_at": 1.0,
                "summary": "hello",
                "message_count": 3,
            }
        ),
        encoding="utf-8",
    )

    loaded = backend.load_latest(tmp_path)
    assert loaded is not None
    assert loaded["message_count"] == 1
    assert loaded["messages"][0]["role"] == "user"


def test_ohmo_v2_save_and_load_round_trip(tmp_path: Path):
    from ohmo.session_storage import load_by_id, load_latest, save_session_snapshot
    from ohmo.workspace import initialize_workspace
    from openharness.engine.messages import ConversationMessage, TextBlock
    from openharness.api.usage import UsageSnapshot

    workspace = tmp_path / ".ohmo-home"
    initialize_workspace(workspace)
    save_session_snapshot(
        cwd=tmp_path, workspace=workspace, model="gpt-5.4", system_prompt="SYS",
        session_id="o2", session_key="feishu:chat-9",
        messages=[ConversationMessage(role="user", content=[TextBlock(text="hi")])],
        usage=UsageSnapshot(input_tokens=2, output_tokens=3),
        tool_metadata={"permission_mode": "default"},
    )
    from ohmo.session_storage import get_session_dir

    session_dir = get_session_dir(workspace)
    assert (session_dir / "session-o2.jsonl").exists()
    assert (session_dir / "session-o2.head.json").exists()
    import json
    assert json.loads((session_dir / "latest.json").read_text())["session_id"] == "o2"

    latest = load_latest(workspace)
    assert latest is not None and latest["session_id"] == "o2"
    assert latest["messages"][0]["content"][0]["text"] == "hi"
    assert latest["usage"]["output_tokens"] == 3
    byid = load_by_id(workspace, "o2")
    assert byid is not None and byid["session_id"] == "o2"


def test_ohmo_v2_session_key_pointer_round_trip(tmp_path: Path):
    from ohmo.session_storage import load_latest_for_session_key, save_session_snapshot
    from ohmo.workspace import initialize_workspace
    from openharness.engine.messages import ConversationMessage, TextBlock
    from openharness.api.usage import UsageSnapshot

    workspace = tmp_path / ".ohmo-home"
    initialize_workspace(workspace)
    save_session_snapshot(
        cwd=tmp_path, workspace=workspace, model="gpt-5.4", system_prompt="SYS",
        session_id="o3", session_key="feishu:chat-7",
        messages=[ConversationMessage(role="user", content=[TextBlock(text="yo")])],
        usage=UsageSnapshot(),
    )
    loaded = load_latest_for_session_key(workspace, "feishu:chat-7")
    assert loaded is not None
    assert loaded["session_id"] == "o3"
    assert loaded["session_key"] == "feishu:chat-7"
    assert loaded["messages"][0]["content"][0]["text"] == "yo"


def test_ohmo_v2_recovers_from_truncated_transcript(tmp_path: Path):
    from ohmo.session_storage import get_session_dir, load_by_id, save_session_snapshot
    from ohmo.workspace import initialize_workspace
    from openharness.engine.messages import ConversationMessage, TextBlock
    from openharness.api.usage import UsageSnapshot

    workspace = tmp_path / ".ohmo-home"
    initialize_workspace(workspace)
    save_session_snapshot(
        cwd=tmp_path, workspace=workspace, model="gpt-5.4", system_prompt="s",
        session_id="oc",
        messages=[
            ConversationMessage(role="user", content=[TextBlock(text="a")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="b")]),
        ],
        usage=UsageSnapshot(),
    )
    transcript = get_session_dir(workspace) / "session-oc.jsonl"
    with open(transcript, "ab") as fh:
        fh.write(b'{"role": "user", "content": [{"type": "text", "text": "c"')
    snap = load_by_id(workspace, "oc")
    assert snap is not None
    assert [m["content"][0]["text"] for m in snap["messages"]] == ["a", "b"]


def test_ohmo_v2_recovers_when_head_lost(tmp_path: Path):
    # R-002a: a lost-head crash must NOT lose the whole ohmo session — the
    # V2_HEADLESS recovery is now mirrored from openharness. History recovers off
    # the durable transcript; the session-key lookup re-injects the key.
    from ohmo.session_storage import (
        get_session_dir, load_by_id, load_latest_for_session_key, save_session_snapshot,
    )
    from ohmo.workspace import initialize_workspace
    from openharness.engine.messages import ConversationMessage, TextBlock
    from openharness.api.usage import UsageSnapshot
    from openharness.services.session_format import head_path

    workspace = tmp_path / ".ohmo-home"
    initialize_workspace(workspace)
    save_session_snapshot(
        cwd=tmp_path, workspace=workspace, model="gpt-5.4", system_prompt="s",
        session_id="oh", session_key="feishu:chat-3",
        messages=[ConversationMessage(role="user", content=[TextBlock(text="kept")])],
        usage=UsageSnapshot(),
    )
    # Lost-head crash window: the head (rename, no fsync) is gone; transcript durable.
    head_path(get_session_dir(workspace), "oh").unlink()

    by_id = load_by_id(workspace, "oh")
    assert by_id is not None  # was None before R-002a — the whole session was lost
    assert by_id["app"] == "ohmo"
    assert [m["content"][0]["text"] for m in by_id["messages"]] == ["kept"]

    by_key = load_latest_for_session_key(workspace, "feishu:chat-3")
    assert by_key is not None
    assert by_key["session_key"] == "feishu:chat-3"  # re-injected on head-less recovery
    assert [m["content"][0]["text"] for m in by_key["messages"]] == ["kept"]


def test_ohmo_legacy_v1_latest_still_loads(tmp_path: Path):
    import json
    from ohmo.session_storage import get_session_dir, load_latest
    from ohmo.workspace import initialize_workspace

    workspace = tmp_path / ".ohmo-home"
    initialize_workspace(workspace)
    (get_session_dir(workspace) / "latest.json").write_text(
        json.dumps({
            "app": "ohmo", "session_id": "oleg", "session_key": "feishu:chat-1",
            "cwd": str(tmp_path), "model": "gpt-legacy", "system_prompt": "old",
            "summary": "hi", "created_at": 1.0, "message_count": 1,
            "usage": {"input_tokens": 1, "output_tokens": 1}, "tool_metadata": {},
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        }),
        encoding="utf-8",
    )
    snap = load_latest(workspace)
    assert snap is not None
    assert snap["session_id"] == "oleg"
    assert snap["messages"][0]["content"][0]["text"] == "hi"


def test_ohmo_v1_revert_supersedes_existing_v2_files_for_same_id(tmp_path: Path, monkeypatch):
    # Revert safety (ohmo mirror): a v1-revert save of an existing id must drop the
    # stale v2 files so load_by_id does not keep serving the old v2 content.
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(config_dir))
    import openharness.config.settings as _cfg
    from ohmo.session_storage import get_session_dir, load_by_id, save_session_snapshot
    from ohmo.workspace import initialize_workspace
    from openharness.engine.messages import ConversationMessage, TextBlock
    from openharness.api.usage import UsageSnapshot

    workspace = tmp_path / ".ohmo-home"
    initialize_workspace(workspace)
    save_session_snapshot(cwd=tmp_path, workspace=workspace, model="m", system_prompt="s", session_id="same",
                          messages=[ConversationMessage(role="user", content=[TextBlock(text="old v2")])],
                          usage=UsageSnapshot())
    session_dir = get_session_dir(workspace)
    assert (session_dir / "session-same.jsonl").exists()

    (config_dir / "settings.json").write_text('{"session_storage_format": "v1"}', encoding="utf-8")
    _cfg._SETTINGS_FILE_CACHE.clear()
    _cfg._INLINE_SETTINGS_CACHE.clear()
    save_session_snapshot(cwd=tmp_path, workspace=workspace, model="m", system_prompt="s", session_id="same",
                          messages=[ConversationMessage(role="user", content=[TextBlock(text="new v1")])],
                          usage=UsageSnapshot())
    assert (session_dir / "session-same.json").exists()
    assert not (session_dir / "session-same.jsonl").exists()
    assert load_by_id(workspace, "same")["messages"][0]["content"][0]["text"] == "new v1"


def test_ohmo_v2_pointer_falls_back_to_legacy_v1_when_v2_target_absent(tmp_path: Path):
    # C.6 step 4 (ohmo mirror): a v2 pointer whose v2 target is gone falls back to
    # a same-id legacy session-<id>.json, not None.
    import json
    from ohmo.session_storage import get_session_dir, load_latest
    from ohmo.workspace import initialize_workspace

    workspace = tmp_path / ".ohmo-home"
    initialize_workspace(workspace)
    session_dir = get_session_dir(workspace)
    (session_dir / "latest.json").write_text(json.dumps({"session_id": "same"}), encoding="utf-8")
    (session_dir / "session-same.json").write_text(
        json.dumps({"app": "ohmo", "session_id": "same", "model": "v1", "message_count": 1,
                    "messages": [{"role": "user", "content": [{"type": "text", "text": "legacy"}]}]}),
        encoding="utf-8",
    )
    snap = load_latest(workspace)
    assert snap is not None
    assert snap["messages"][0]["content"][0]["text"] == "legacy"
