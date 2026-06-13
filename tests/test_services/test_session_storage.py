"""Tests for session persistence."""

from __future__ import annotations

import json
from pathlib import Path

from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, TextBlock
from openharness.services.session_storage import (
    export_session_markdown,
    get_project_session_dir,
    list_session_snapshots,
    load_session_snapshot,
    save_session_snapshot,
)


def test_save_and_load_session_snapshot(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()

    path = save_session_snapshot(
        cwd=project,
        model="claude-test",
        system_prompt="system",
        messages=[ConversationMessage(role="user", content=[TextBlock(text="hello")])],
        usage=UsageSnapshot(input_tokens=1, output_tokens=2),
        tool_metadata={
            "task_focus_state": {"goal": "Fix compact carry-over"},
            "recent_verified_work": ["Focused session storage test passed"],
        },
    )

    assert path.exists()
    snapshot = load_session_snapshot(project)
    assert snapshot is not None
    assert snapshot["model"] == "claude-test"
    assert snapshot["usage"]["output_tokens"] == 2
    assert snapshot["tool_metadata"]["task_focus_state"]["goal"] == "Fix compact carry-over"
    assert snapshot["tool_metadata"]["recent_verified_work"] == ["Focused session storage test passed"]


def test_save_session_snapshot_updates_listing_index(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()

    save_session_snapshot(
        cwd=project,
        model="claude-test",
        system_prompt="system",
        session_id="indexed-session",
        messages=[ConversationMessage(role="user", content=[TextBlock(text="indexed hello")])],
        usage=UsageSnapshot(input_tokens=1, output_tokens=2),
    )

    session_dir = get_project_session_dir(project)
    index_path = session_dir / "sessions-index.json"
    assert index_path.exists()
    assert list_session_snapshots(project, limit=1)[0]["session_id"] == "indexed-session"


def test_list_session_snapshots_merges_index_with_legacy_files(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()

    session_dir = get_project_session_dir(project)
    (session_dir / "session-legacy-session.json").write_text(
        json.dumps(
            {
                "session_id": "legacy-session",
                "summary": "legacy",
                "message_count": 1,
                "model": "claude-legacy",
                "created_at": 1.0,
                "messages": [],
            }
        ),
        encoding="utf-8",
    )

    save_session_snapshot(
        cwd=project,
        model="claude-test",
        system_prompt="system",
        session_id="indexed-session",
        messages=[ConversationMessage(role="user", content=[TextBlock(text="indexed hello")])],
        usage=UsageSnapshot(input_tokens=1, output_tokens=2),
    )

    session_ids = {item["session_id"] for item in list_session_snapshots(project, limit=10)}
    assert session_ids == {"indexed-session", "legacy-session"}


def test_export_session_markdown(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()

    path = export_session_markdown(
        cwd=project,
        messages=[
            ConversationMessage(role="user", content=[TextBlock(text="hello")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="world")]),
        ],
    )

    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "OpenHarness Session Transcript" in content
    assert "hello" in content
    assert "world" in content


def test_load_session_snapshot_sanitizes_legacy_empty_assistant_messages(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()

    target_dir = get_project_session_dir(project)
    payload = {
        "session_id": "legacy123",
        "cwd": str(project),
        "model": "claude-test",
        "system_prompt": "system",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            {"role": "assistant", "content": None},
            {"role": "assistant", "content": []},
            {"role": "assistant", "content": [{"type": "text", "text": "world"}]},
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "tool_metadata": {},
        "created_at": 1.0,
        "summary": "hello",
        "message_count": 4,
    }
    (target_dir / "latest.json").write_text(json.dumps(payload), encoding="utf-8")

    snapshot = load_session_snapshot(project)
    assert snapshot is not None
    assert snapshot["message_count"] == 2
    assert [message["role"] for message in snapshot["messages"]] == ["user", "assistant"]
    assert snapshot["messages"][1]["content"][0]["text"] == "world"


def test_settings_session_storage_defaults():
    from openharness.config.settings import Settings

    settings = Settings()
    assert settings.session_storage_format == "v2"
    assert settings.session_retention_max_files == 50
    assert settings.session_retention_max_age_days == 30


def test_v2_save_creates_transcript_head_and_pointer(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()

    save_session_snapshot(
        cwd=project,
        model="claude-test",
        system_prompt="SYSTEM PROMPT TEXT",
        session_id="v2sess",
        messages=[ConversationMessage(role="user", content=[TextBlock(text="hi")])],
        usage=UsageSnapshot(input_tokens=1, output_tokens=2),
        tool_metadata={"permission_mode": "default"},
    )

    session_dir = get_project_session_dir(project)
    assert (session_dir / "session-v2sess.jsonl").exists()
    assert (session_dir / "session-v2sess.head.json").exists()
    # latest.json is a pointer, not a full payload.
    latest = json.loads((session_dir / "latest.json").read_text(encoding="utf-8"))
    assert latest == {"session_id": "v2sess"}
    # The full system prompt text is not persisted; only its hash.
    head = json.loads((session_dir / "session-v2sess.head.json").read_text(encoding="utf-8"))
    assert "SYSTEM PROMPT TEXT" not in json.dumps(head)
    assert len(head["system_prompt_sha256"]) == 64


def test_v1_revert_switch_writes_full_session_file(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text('{"session_storage_format": "v1"}', encoding="utf-8")
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(config_dir))

    save_session_snapshot(
        cwd=project,
        model="claude-test",
        system_prompt="system",
        session_id="v1sess",
        messages=[ConversationMessage(role="user", content=[TextBlock(text="hi")])],
        usage=UsageSnapshot(),
    )
    session_dir = get_project_session_dir(project)
    assert (session_dir / "session-v1sess.json").exists()
    assert not (session_dir / "session-v1sess.jsonl").exists()


def test_v2_lost_head_does_not_duplicate_on_next_save(tmp_path: Path, monkeypatch):
    # P1-001 (behavioral). A crash that loses the non-fsync'd head between two
    # saves must NOT make the next save re-append already-durable messages.
    # Fails with the old head-derived cursor (re-appends -> a,b,a,b,c);
    # passes with the transcript-derived cursor (C.4).
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    import openharness.services.session_storage as ss
    from openharness.services.session_format import head_path, load_v2_snapshot

    project = tmp_path / "repo"
    project.mkdir()

    def save(texts):
        save_session_snapshot(
            cwd=project, model="claude-test", system_prompt="s", session_id="s1",
            messages=[ConversationMessage(role="user", content=[TextBlock(text=t)]) for t in texts],
            usage=UsageSnapshot(),
        )

    save(["a", "b"])  # transcript durable with 2 records; head + cache reflect 2
    session_dir = get_project_session_dir(project)
    # Simulate the crash window: the head write (no fsync) is lost AND the
    # in-process cursor cache is gone (a fresh process would have neither).
    head_path(session_dir, "s1").unlink()
    ss._v2_persisted_count.clear()
    ss._v2_persisted_prefix_fp.clear()  # both halves of the in-process cursor vanish on crash

    save(["a", "b", "c"])  # cursor re-seeds from the transcript (=2); appends only "c"

    assert [m["content"][0]["text"] for m in load_v2_snapshot(session_dir, "s1")] == ["a", "b", "c"]


def test_v2_cursor_ignores_head_message_count_even_when_head_present(tmp_path: Path, monkeypatch):
    # C.4 MECHANISM (not just outcome): the cursor is ALWAYS the transcript live-count,
    # NEVER head.message_count — even when the head EXISTS. The lost-head test above deletes
    # the head, so a two-tier cursor (head primary, transcript fallback) would still pass it.
    # Here the head is present but its message_count is corrupted to 0; a cold re-seed that
    # trusts head.message_count would re-append the whole history (-> a,b,a,b,c). The
    # transcript-derived cursor appends only "c".
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    import json as _json
    import openharness.services.session_storage as ss
    from openharness.services.session_format import head_path, load_v2_snapshot

    project = tmp_path / "repo"; project.mkdir()

    def save(texts):
        save_session_snapshot(
            cwd=project, model="claude-test", system_prompt="s", session_id="s2",
            messages=[ConversationMessage(role="user", content=[TextBlock(text=t)]) for t in texts],
            usage=UsageSnapshot(),
        )

    save(["a", "b"])
    session_dir = get_project_session_dir(project)
    # Head present but LYING: message_count=0. Clear the in-process cache to force a cold re-seed.
    hp = head_path(session_dir, "s2")
    head = _json.loads(hp.read_text(encoding="utf-8")); head["message_count"] = 0
    hp.write_text(_json.dumps(head), encoding="utf-8")
    ss._v2_persisted_count.clear(); ss._v2_persisted_prefix_fp.clear()

    save(["a", "b", "c"])  # must re-seed the cursor from the transcript (=2), NOT head.message_count (=0)
    assert [m["content"][0]["text"] for m in load_v2_snapshot(session_dir, "s2")] == ["a", "b", "c"]


def test_v2_save_uses_unlocked_index_core_not_the_locking_wrapper(tmp_path: Path, monkeypatch):
    # C.2 MECHANISM: the v2 save runs the lock-free *_unlocked index core EXACTLY ONCE and
    # WHILE .sessions.lock is held. Three failure modes this must catch:
    #  (a) calling the locking _update_session_index wrapper from inside the critical section
    #      (re-acquires flock, per-open-description -> self-deadlock);
    #  (b) calling the unlocked core OUTSIDE the lock (no serialization — passes a wrapper-only test);
    #  (c) acquiring/writing more than once.
    # The 12-thread test proves serialization but none of (a)-(c). We wrap exclusive_file_lock
    # to track whether it is active, and record that state at the moment the core runs.
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    import contextlib
    import openharness.services.session_storage as ss

    lock_held = {"v": False}
    lock_paths: list[Path] = []
    real_lock = ss.exclusive_file_lock
    @contextlib.contextmanager
    def _tracking_lock(path):
        with real_lock(path):
            lock_paths.append(path)
            lock_held["v"] = True
            try:
                yield
            finally:
                lock_held["v"] = False
    monkeypatch.setattr(ss, "exclusive_file_lock", _tracking_lock)

    wrapper_calls: list[int] = []
    core_lock_states: list[bool] = []
    real_core = ss._update_session_index_unlocked
    monkeypatch.setattr(ss, "_update_session_index", lambda *a, **k: wrapper_calls.append(1))
    def _spy_core(*a, **k):
        core_lock_states.append(lock_held["v"])  # was .sessions.lock held when the core ran?
        return real_core(*a, **k)
    monkeypatch.setattr(ss, "_update_session_index_unlocked", _spy_core)

    project = tmp_path / "repo"; project.mkdir()
    save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="x",
                          messages=[ConversationMessage(role="user", content=[TextBlock(text="x")])], usage=UsageSnapshot())
    assert wrapper_calls == []          # never the locking wrapper (would self-deadlock under flock)
    assert lock_paths == [get_project_session_dir(project) / ".sessions.lock"]  # exactly one store-lock acquisition
    assert core_lock_states == [True]   # core ran EXACTLY ONCE, and the lock WAS held while it ran (C.2)


def test_concurrent_v2_saves_preserve_all_index_entries(tmp_path: Path, monkeypatch):
    # P1-003 (behavioral). Many concurrent savers each add a distinct session id;
    # the store lock must serialise the index read-modify-write so no entry is
    # lost. Without the lock, concurrent RMW drops updates and the set is short.
    import threading
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()

    def worker(n: int):
        save_session_snapshot(
            cwd=project, model="m", system_prompt="s", session_id=f"c{n}",
            messages=[ConversationMessage(role="user", content=[TextBlock(text=str(n))])],
            usage=UsageSnapshot(),
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    from openharness.services.session_storage import _load_session_index
    ids = {e["session_id"] for e in _load_session_index(get_project_session_dir(project))}
    assert ids == {f"c{i}" for i in range(12)}
