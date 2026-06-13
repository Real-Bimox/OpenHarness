import json
from pathlib import Path

from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, TextBlock

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
