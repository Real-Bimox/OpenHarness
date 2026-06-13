"""Tests for session persistence."""

from __future__ import annotations

import json
import time as _time
from pathlib import Path

from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, TextBlock
from openharness.services import session_format
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


def test_v2_load_latest_via_pointer_round_trip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()

    save_session_snapshot(
        cwd=project,
        model="claude-test",
        system_prompt="system",
        session_id="v2sess",
        messages=[
            ConversationMessage(role="user", content=[TextBlock(text="hello")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="world")]),
        ],
        usage=UsageSnapshot(input_tokens=3, output_tokens=4),
        tool_metadata={"recent_verified_work": ["did a thing"]},
    )

    snap = load_session_snapshot(project)
    assert snap is not None
    assert snap["session_id"] == "v2sess"
    assert snap["model"] == "claude-test"
    assert snap["message_count"] == 2
    assert [m["role"] for m in snap["messages"]] == ["user", "assistant"]
    assert snap["usage"]["output_tokens"] == 4
    assert snap["tool_metadata"]["recent_verified_work"] == ["did a thing"]


def test_v2_load_by_id_round_trip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()
    save_session_snapshot(
        cwd=project,
        model="claude-test",
        system_prompt="system",
        session_id="byid",
        messages=[ConversationMessage(role="user", content=[TextBlock(text="x")])],
        usage=UsageSnapshot(),
    )
    from openharness.services.session_storage import load_session_by_id

    snap = load_session_by_id(project, "byid")
    assert snap is not None and snap["session_id"] == "byid"
    assert snap["messages"][0]["content"][0]["text"] == "x"


def test_v2_load_via_pointer_recovers_when_head_missing(tmp_path: Path, monkeypatch):
    # P2-005 / V2_HEADLESS (C.6): the head was lost in a crash but the transcript
    # is durable — resume must still recover the history off the transcript.
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    import openharness.services.session_storage as ss
    from openharness.services.session_format import head_path

    project = tmp_path / "repo"
    project.mkdir()
    save_session_snapshot(
        cwd=project, model="claude-test", system_prompt="s", session_id="hl",
        messages=[ConversationMessage(role="user", content=[TextBlock(text="kept")])],
        usage=UsageSnapshot(),
    )
    session_dir = get_project_session_dir(project)
    head_path(session_dir, "hl").unlink()  # simulate the lost-head crash window
    ss._v2_persisted_count.clear()
    ss._v2_persisted_prefix_fp.clear()  # both halves of the in-process cursor vanish on crash

    snap = load_session_snapshot(project)  # resolves the latest.json pointer
    assert snap is not None
    assert [m["content"][0]["text"] for m in snap["messages"]] == ["kept"]


def test_index_trusted_below_limit(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()
    for i in range(3):
        save_session_snapshot(
            cwd=project, model="m", system_prompt="s", session_id=f"s{i}",
            messages=[ConversationMessage(role="user", content=[TextBlock(text=f"m{i}")])],
            usage=UsageSnapshot(),
        )
    # limit far above count: index path must still return all three without
    # falling through to a file scan.
    got = list_session_snapshots(project, limit=50)
    assert {s["session_id"] for s in got} == {"s0", "s1", "s2"}


def test_backfill_builds_index_from_legacy_files(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()
    session_dir = get_project_session_dir(project)
    for sid in ("leg1", "leg2"):
        (session_dir / f"session-{sid}.json").write_text(
            json.dumps({"session_id": sid, "summary": sid, "message_count": 1,
                        "model": "m", "created_at": 1.0, "messages": []}),
            encoding="utf-8",
        )
    assert not (session_dir / "sessions-index.json").exists()
    got = list_session_snapshots(project, limit=10)
    assert {s["session_id"] for s in got} == {"leg1", "leg2"}
    # Backfill persisted the index.
    assert (session_dir / "sessions-index.json").exists()


def test_stale_index_entry_compacted_on_next_write(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()
    save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="keep",
                          messages=[ConversationMessage(role="user", content=[TextBlock(text="a")])],
                          usage=UsageSnapshot())
    session_dir = get_project_session_dir(project)
    # Inject a stale entry pointing at a now-missing file.
    from openharness.services.session_storage import _load_session_index, _write_session_index
    entries = _load_session_index(session_dir)
    entries.append({"session_id": "gone", "path": "session-gone.head.json",
                    "model": "m", "summary": "", "message_count": 0, "created_at": 1.0})
    _write_session_index(session_dir, entries)
    # Next save must compact the stale entry out.
    save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="keep2",
                          messages=[ConversationMessage(role="user", content=[TextBlock(text="b")])],
                          usage=UsageSnapshot())
    ids = {e["session_id"] for e in _load_session_index(session_dir)}
    assert "gone" not in ids
    assert {"keep", "keep2"} <= ids


def test_backfill_dual_format_same_id_prefers_v2_and_is_idempotent(tmp_path: Path, monkeypatch):
    # P1-004 / C.7: a legacy .json and a v2 head for the same id -> v2 wins;
    # re-running the backfill yields the same single entry (idempotent).
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()
    session_dir = get_project_session_dir(project)
    (session_dir / "session-dup.json").write_text(
        json.dumps({"session_id": "dup", "summary": "v1", "message_count": 9,
                    "model": "v1model", "created_at": 1.0, "messages": []}),
        encoding="utf-8",
    )
    from openharness.services.session_format import write_head
    write_head(session_dir, "dup", {"session_id": "dup", "summary": "v2",
               "message_count": 2, "model": "v2model", "created_at": 2.0})

    from openharness.services.session_storage import _backfill_index, _load_session_index
    first = _backfill_index(session_dir)
    dup = [e for e in first if e["session_id"] == "dup"]
    assert len(dup) == 1 and dup[0]["model"] == "v2model"  # v2 won the conflict
    second = _backfill_index(session_dir)  # idempotent
    assert {e["session_id"] for e in second} == {e["session_id"] for e in first}
    assert len(_load_session_index(session_dir)) == len(first)


def test_list_surfaces_v2_session_absent_from_index(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"; project.mkdir()
    # A: indexed via save. B: v2 head+transcript on disk, NOT in index. C: HEAD-LESS v2 (transcript only).
    save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="A",
                          messages=[ConversationMessage(role="user", content=[TextBlock(text="a")])], usage=UsageSnapshot())
    sdir = get_project_session_dir(project)
    session_format.append_messages_to_transcript(sdir, "B", [ConversationMessage(role="user", content=[TextBlock(text="b")])], last_persisted_count=0)
    session_format.write_head(sdir, "B", {"session_id": "B", "message_count": 1, "created_at": 1.0, "model": "m", "summary": ""})
    session_format.append_messages_to_transcript(sdir, "C", [ConversationMessage(role="user", content=[TextBlock(text="c")])], last_persisted_count=0)
    # C has NO head.json — the V2_HEADLESS case; it must still surface via the loader.
    from openharness.services.session_storage import _load_session_index
    ids = {s["session_id"] for s in list_session_snapshots(project, limit=50)}
    assert {"A", "B", "C"} <= ids   # head-less C surfaces too (loader-based derivation, not read_head)
    # .head.json trap: `glob("session-*.json")` ALSO matches `session-<id>.head.json`, yielding phantom
    # ids "B.head" etc. (a lone `.head.json` sniffs as v1). The shared enumerator skips them — assert so.
    assert not any(i.endswith(".head") for i in ids)
    # C.7: the backfill must be PERSISTED to the index under lock, not just returned (merge-on-read
    # would pass the line above yet leave sessions-index.json incomplete). After listing once, the
    # index itself contains the headed (B) AND head-less (C) v2 sessions, and a 2nd listing is index-only.
    indexed_ids = {e["session_id"] for e in _load_session_index(sdir)}
    assert {"A", "B", "C"} <= indexed_ids


def test_backfill_writes_index_exactly_once(tmp_path: Path, monkeypatch):
    # C.7 ATOMICITY: backfilling N missing entries must write the index ONCE (all-or-nothing),
    # NOT once per entry. A per-entry loop (_update_session_index_unlocked per id) writes the
    # whole index on each call, so an interruption between B and C leaves a PARTIALLY backfilled
    # index — violating C.7 ("a crash mid-backfill leaves the OLD index, not a partial one").
    # Instrument _write_session_index AND the lock: exactly one write, while .sessions.lock
    # is held, covering BOTH missing sessions.
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    import contextlib
    import openharness.services.session_storage as ss
    from openharness.services import session_format
    project = tmp_path / "repo"; project.mkdir()
    save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="A",
                          messages=[ConversationMessage(role="user", content=[TextBlock(text="a")])], usage=UsageSnapshot())
    sdir = get_project_session_dir(project)
    for sid in ("B", "C"):  # two index-missing v2 sessions on disk
        session_format.append_messages_to_transcript(sdir, sid, [ConversationMessage(role="user", content=[TextBlock(text=sid)])], last_persisted_count=0)
        session_format.write_head(sdir, sid, {"session_id": sid, "message_count": 1, "created_at": 1.0})
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
    writes: list[set] = []
    write_lock_states: list[bool] = []
    real_write = ss._write_session_index
    def _spy(session_dir, entries):
        writes.append({e["session_id"] for e in entries})
        write_lock_states.append(lock_held["v"])
        return real_write(session_dir, entries)
    monkeypatch.setattr(ss, "_write_session_index", _spy)
    list_session_snapshots(project, limit=50)
    assert len(writes) == 1            # ONE write for the whole backfill, not one per entry
    assert lock_paths == [sdir / ".sessions.lock"]  # and only ONE store-lock acquisition
    assert write_lock_states == [True] # and that write happened WHILE .sessions.lock was held
    assert {"A", "B", "C"} <= writes[0]  # ...persisting both missing entries together (atomic)


def test_retention_prunes_oldest_keeps_active_and_latest(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text('{"session_retention_max_files": 2, "session_retention_max_age_days": 0}', encoding="utf-8")
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(config_dir))

    import os
    for i in range(3):  # s0, s1, s2 — the older sessions
        save_session_snapshot(
            cwd=project, model="m", system_prompt="s", session_id=f"s{i}",
            messages=[ConversationMessage(role="user", content=[TextBlock(text=f"m{i}")])],
            usage=UsageSnapshot(),
        )
        _time.sleep(0.01)  # distinct created_at ordering
    # Age them past the recency window so count-pruning can reclaim them (C.8);
    # without this they would be recency-protected as possibly-active.
    session_dir = get_project_session_dir(project)
    old = _time.time() - 7 * 86400
    for i in range(3):
        for suffix in (".jsonl", ".head.json"):
            os.utime(session_dir / f"session-s{i}{suffix}", (old, old))
    # The active save (s3) triggers the prune; max_files=2 keeps s3 + the newest aged.
    save_session_snapshot(
        cwd=project, model="m", system_prompt="s", session_id="s3",
        messages=[ConversationMessage(role="user", content=[TextBlock(text="m3")])],
        usage=UsageSnapshot(),
    )

    ids = {s["session_id"] for s in list_session_snapshots(project, limit=50)}
    # max_files=2 keeps the two newest; the active save (s3) is always kept.
    assert "s3" in ids
    assert len(ids) == 2
    assert "s0" not in ids


def test_retention_age_prunes_old_sessions(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text('{"session_retention_max_files": 0, "session_retention_max_age_days": 1}', encoding="utf-8")
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(config_dir))

    import os
    session_dir = get_project_session_dir(project)
    # Inject an ancient v1 session directly into the index.
    (session_dir / "session-ancient.json").write_text(
        json.dumps({"session_id": "ancient", "summary": "old", "message_count": 1,
                    "model": "m", "created_at": 1.0, "messages": []}),
        encoding="utf-8",
    )
    # Age its mtime too, so it falls outside the recency window (C.8) and the
    # age limit can reclaim it (a fresh file would be recency-protected).
    old = _time.time() - 5 * 86400
    os.utime(session_dir / "session-ancient.json", (old, old))
    from openharness.services.session_storage import _update_session_index, _session_index_entry
    _update_session_index(session_dir, _session_index_entry(
        {"session_id": "ancient", "summary": "old", "message_count": 1, "model": "m", "created_at": 1.0},
        session_dir / "session-ancient.json"))

    save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="fresh",
                          messages=[ConversationMessage(role="user", content=[TextBlock(text="new")])],
                          usage=UsageSnapshot())
    ids = {s["session_id"] for s in list_session_snapshots(project, limit=50)}
    assert "ancient" not in ids
    assert "fresh" in ids


def test_retention_recency_window_protects_recent_from_count_prune(tmp_path: Path, monkeypatch):
    # C.8 MECHANISM: a session within the recency window is NEVER count-pruned, even when
    # over max_files — so a concurrent worker mid-append on another id is never pruned out
    # from under it (the P2-004 safety). The other retention tests AGE fixtures *past* the
    # window so pruning fires; this asserts the converse, which a count-only impl would fail.
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"; project.mkdir()
    config_dir = tmp_path / "cfg"; config_dir.mkdir()
    (config_dir / "settings.json").write_text(
        '{"session_retention_max_files": 1, "session_retention_max_age_days": 0}', encoding="utf-8")
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(config_dir))

    save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="recent",
                          messages=[ConversationMessage(role="user", content=[TextBlock(text="r")])], usage=UsageSnapshot())
    # 'recent' is freshly written (mtime inside the recency window). The active save of 'active'
    # triggers the prune with max_files=1; both must survive — 'active' (active id) AND 'recent'
    # (within the recency window) — even though the live count (2) exceeds max_files (1).
    save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="active",
                          messages=[ConversationMessage(role="user", content=[TextBlock(text="a")])], usage=UsageSnapshot())
    ids = {s["session_id"] for s in list_session_snapshots(project, limit=50)}
    assert {"recent", "active"} <= ids   # a count-only prune (ignoring the recency window) would drop 'recent'


def test_v2_append_delta_is_bounded(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()
    session_dir = get_project_session_dir(project)

    big = [
        ConversationMessage(role="user", content=[TextBlock(text="x" * 1000)])
        for _ in range(200)
    ]
    save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="big",
                          messages=big, usage=UsageSnapshot())
    transcript = session_dir / "session-big.jsonl"
    size_before = transcript.stat().st_size

    big.append(ConversationMessage(role="assistant", content=[TextBlock(text="ok")]))
    save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="big",
                          messages=big, usage=UsageSnapshot())
    size_after = transcript.stat().st_size

    # Second save appended only the one new short message, not the whole history.
    assert size_after - size_before < 50_000
    assert size_after - size_before > 0


def test_v2_compaction_shrink_round_trip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()
    from openharness.services.session_storage import load_session_by_id

    msgs = [ConversationMessage(role="user", content=[TextBlock(text=f"m{i}")]) for i in range(5)]
    save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="c",
                          messages=msgs, usage=UsageSnapshot())
    # Simulate compaction: history collapses to one summary message.
    compacted = [ConversationMessage(role="user", content=[TextBlock(text="summary")])]
    save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="c",
                          messages=compacted, usage=UsageSnapshot())

    snap = load_session_by_id(project, "c")
    assert snap is not None
    assert [m["content"][0]["text"] for m in snap["messages"]] == ["summary"]


def test_v2_in_place_compaction_same_count_rewrites_not_stale(tmp_path: Path, monkeypatch):
    # R-001 regression: the engine compacts IN PLACE — message *content* is
    # rewritten while the message COUNT stays the same (microcompact clears old
    # tool-result bodies). A count-shrink trigger (`last_persisted > len(messages)`)
    # would take the append path, write nothing, and leave the stale bloated
    # content on disk. The fingerprint trigger must detect the divergence and
    # rewrite the transcript.
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()
    from openharness.services.session_storage import load_session_by_id
    from openharness.services.session_format import transcript_path

    bloated = [
        ConversationMessage(role="user", content=[TextBlock(text="BIG-OUTPUT-" + "x" * 4000)]),
        ConversationMessage(role="assistant", content=[TextBlock(text="b")]),
    ]
    save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="ip",
                          messages=bloated, usage=UsageSnapshot())
    transcript = transcript_path(get_project_session_dir(project), "ip")
    size_before = transcript.stat().st_size

    # In-place compaction: SAME count, the first message's content cleared.
    compacted = [
        ConversationMessage(role="user", content=[TextBlock(text="[cleared]")]),
        ConversationMessage(role="assistant", content=[TextBlock(text="b")]),
    ]
    assert len(compacted) == len(bloated)  # count did NOT shrink — the R-001 trap
    save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="ip",
                          messages=compacted, usage=UsageSnapshot())

    snap = load_session_by_id(project, "ip")
    assert snap is not None
    # Durable history is the COMPACTED content, not the stale bloated text...
    assert [m["content"][0]["text"] for m in snap["messages"]] == ["[cleared]", "b"]
    # ...and the transcript was actually rewritten smaller (a buggy no-op append
    # would leave it unchanged at size_before).
    assert transcript.stat().st_size < size_before


def test_crash_truncated_transcript_loads_last_complete_history(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()
    from openharness.services.session_storage import load_session_by_id

    msgs = [
        ConversationMessage(role="user", content=[TextBlock(text="a")]),
        ConversationMessage(role="assistant", content=[TextBlock(text="b")]),
    ]
    save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="crash",
                          messages=msgs, usage=UsageSnapshot())

    session_dir = get_project_session_dir(project)
    transcript = session_dir / "session-crash.jsonl"
    # Simulate a crash mid-append: tack on a partial third record.
    with open(transcript, "ab") as fh:
        fh.write(b'{"role": "user", "content": [{"type": "text", "text": "c"')

    snap = load_session_by_id(project, "crash")
    assert snap is not None
    # The partial line is dropped; the two complete messages survive.
    assert [m["content"][0]["text"] for m in snap["messages"]] == ["a", "b"]
    assert snap["message_count"] == 2


def test_legacy_v1_full_latest_still_loads(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()
    session_dir = get_project_session_dir(project)
    (session_dir / "latest.json").write_text(
        json.dumps({
            "session_id": "legfull", "cwd": str(project), "model": "claude-legacy",
            "system_prompt": "old system prompt", "summary": "hi", "created_at": 5.0,
            "message_count": 1, "usage": {"input_tokens": 7, "output_tokens": 8},
            "tool_metadata": {"permission_mode": "default"},
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        }),
        encoding="utf-8",
    )
    snap = load_session_snapshot(project)
    assert snap is not None
    assert snap["session_id"] == "legfull"
    assert snap["model"] == "claude-legacy"
    assert snap["usage"]["output_tokens"] == 8
    assert snap["messages"][0]["content"][0]["text"] == "hi"


def test_legacy_v1_session_file_loads_by_id(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()
    from openharness.services.session_storage import load_session_by_id

    session_dir = get_project_session_dir(project)
    (session_dir / "session-legid.json").write_text(
        json.dumps({"session_id": "legid", "model": "m", "summary": "s", "created_at": 1.0,
                    "message_count": 1,
                    "messages": [{"role": "user", "content": [{"type": "text", "text": "z"}]}]}),
        encoding="utf-8",
    )
    snap = load_session_by_id(project, "legid")
    assert snap is not None and snap["session_id"] == "legid"
    assert snap["messages"][0]["content"][0]["text"] == "z"


def test_backend_load_shape_unchanged(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()
    from openharness.services.session_backend import DEFAULT_SESSION_BACKEND

    DEFAULT_SESSION_BACKEND.save_snapshot(
        cwd=project, model="m", system_prompt="s", session_id="shape",
        messages=[ConversationMessage(role="user", content=[TextBlock(text="q")])],
        usage=UsageSnapshot(input_tokens=1, output_tokens=1),
        tool_metadata={"permission_mode": "default"},
    )
    loaded = DEFAULT_SESSION_BACKEND.load_latest(project)
    assert loaded is not None
    # The public dict shape relied on by build_runtime / cli / app.
    for key in ("session_id", "model", "messages", "usage", "tool_metadata", "message_count"):
        assert key in loaded, f"missing key {key}"
    listed = DEFAULT_SESSION_BACKEND.list_snapshots(project, limit=5)
    for key in ("session_id", "summary", "message_count", "model", "created_at"):
        assert key in listed[0], f"missing list key {key}"
