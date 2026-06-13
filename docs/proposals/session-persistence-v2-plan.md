# Session Persistence v2 (WS4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the O(n²) full-history rewrite in OpenHarness session persistence with an append-only per-session transcript plus a trusted index and a retention policy, behind a `session_storage_format=v2` setting, while keeping every legacy file readable forever and every loader's public dict shape unchanged.

**Architecture:** Each session writes an append-only `session-<id>.jsonl` transcript (one JSON line per message, deltas-from-last-persisted-index appends) plus a small `session-<id>.head.json` (model, system-prompt hash + rebuild inputs, usage, tool_metadata, message_count) rewritten per turn; `latest.json` becomes a `{"session_id": ...}` pointer. The index is trusted whenever it exists (one-time backfill migrates legacy files, stale entries compacted on write). All new writes are gated behind `session_storage_format=v2` (default on, revert switch to `v1`), and a format sniffer keeps legacy `latest.json` / `session-*.json` readable indefinitely. The transcript append gets one fsync/turn; head/index use atomic-rename without per-write fsync.

**Tech Stack:** Python, pytest, pydantic; existing OpenHarness persistence modules.

---

## File Structure

| File | Create / Modify | Responsibility |
|---|---|---|
| `src/openharness/config/settings.py` | Modify (add fields to `Settings`, `settings.py:614-677` block) | New `session_storage_format: str = "v2"`, `session_retention_max_files: int = 50`, `session_retention_max_age_days: int = 30` fields. |
| `src/openharness/utils/fs.py` | Modify (`atomic_write_bytes`, `fs.py:39-78`; new `append_jsonl_line`, `read_jsonl_complete_lines`) | Add a parent-dir fsync to `atomic_write_bytes` (close the documented gap at `fs.py:57-62`); add an append-with-fsync helper and a crash-safe JSONL reader that stops at the last complete line. |
| `src/openharness/services/session_format.py` | **Create** | Format sniffer (`detect_session_format`), v2 transcript read/write primitives (`append_messages_to_transcript`, `write_head`, `read_head`, `load_v2_snapshot`, `rewrite_transcript`), and the system-prompt hash helper (`system_prompt_fingerprint`). Pure functions, no settings access. |
| `src/openharness/services/session_storage.py` | Modify (`session_storage.py:114-310`) | Route `save_session_snapshot` through v1 or v2 by setting; make `load_session_snapshot` / `load_session_by_id` resolve the `latest.json` pointer and sniff format; make `list_session_snapshots` trust the index unconditionally + one-time backfill; add retention pruning; single-pass resume load. |
| `src/openharness/services/session_backend.py` | Unchanged | Protocol/shape stays identical — confirmed by a no-op shape test in Task 14. |
| `ohmo/session_storage.py` | Modify (`ohmo/session_storage.py:92-209`) | Apply the same head+append pattern via the shared `session_format` primitives; keep `session_key` plumbing and the `latest-<token>.json` pointer. |
| `tests/test_utils/test_fs.py` | Modify | Tests for parent-dir fsync, `append_jsonl_line`, and crash-safe `read_jsonl_complete_lines`. |
| `tests/test_services/test_session_format.py` | **Create** | Unit tests for the sniffer, transcript primitives, hash helper, and crash-consistency (truncated mid-line). |
| `tests/test_services/test_session_storage.py` | Modify | v2 save/load round-trip, pointer `latest.json`, index-trust + backfill, retention, byte-budget, legacy-format fixtures, format-flag revert. |
| `tests/test_ohmo/test_ohmo_session_storage.py` | Modify | v2 ohmo save/load round-trip, legacy fixture, `session_key` pointer under v2. |

---

## Phase 0 — Pre-work

### Task 0: Branch and baseline

**Files:** none (git only).

1. - [ ] Create the implementation branch from current `main`:
   ```bash
   git checkout -b proposal/session-persistence-v2 main
   ```
2. - [ ] Run the two existing persistence suites to confirm a green baseline before any change:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py tests/test_ohmo/test_ohmo_session_storage.py tests/test_utils/test_fs.py -q
   ```
   Expected: all pass (this is the regression set every later task must keep green).
3. - [ ] Commit nothing yet; proceed to Phase 1.

---

## Phase 1 — Settings and fs primitives

### Task 1: Add the `session_storage_format` and retention settings

**Files:**
- Modify: `src/openharness/config/settings.py` (the `Settings` class field block, after `conversation_index_enabled` at `settings.py:664-666`)
- Test: `tests/test_services/test_session_storage.py`

1. - [ ] Write the failing test. Add to `tests/test_services/test_session_storage.py`:
   ```python
   def test_settings_session_storage_defaults():
       from openharness.config.settings import Settings

       settings = Settings()
       assert settings.session_storage_format == "v2"
       assert settings.session_retention_max_files == 50
       assert settings.session_retention_max_age_days == 30
   ```
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py::test_settings_session_storage_defaults -q
   ```
   Expected: `AttributeError: 'Settings' object has no attribute 'session_storage_format'`.
3. - [ ] Write minimal implementation. In `src/openharness/config/settings.py`, immediately after the `conversation_index_enabled` field (`settings.py:666`), add:
   ```python
       # Session persistence on-disk format. "v2" = append-only transcript +
       # head file + pointer latest.json (default). "v1" = legacy full-history
       # rewrite. Revert switch only; v1 files are always readable regardless.
       session_storage_format: str = "v2"
       # Retention: prune oldest saved sessions on save. Never prunes the active
       # session or the one latest.json points at. 0 disables the limit.
       session_retention_max_files: int = 50
       session_retention_max_age_days: int = 30
   ```
4. - [ ] Run, verify pass:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py::test_settings_session_storage_defaults -q
   ```
   Expected: 1 passed.
5. - [ ] Commit:
   ```bash
   git add src/openharness/config/settings.py tests/test_services/test_session_storage.py && git commit -m "Add session_storage_format and retention settings"
   ```

### Task 2: Parent-directory fsync in `atomic_write_bytes`

**Files:**
- Modify: `src/openharness/utils/fs.py` (`atomic_write_bytes`, `fs.py:39-78`)
- Test: `tests/test_utils/test_fs.py`

**Design decision (proposal left this open — "fix or document"):** We *fix* it. After `os.replace`, when `fsync=True`, fsync the parent directory so the rename itself reaches stable storage (a rename is only durable once the directory entry is flushed). Best-effort: wrapped in `try/except OSError` because some platforms (Windows, certain network mounts) cannot open a directory fd. When `fsync=False` (the per-line state-cache path) the dir fsync is skipped — consistent with the existing "crash may lose the newest version" contract.

1. - [ ] Write the failing test. Add to `tests/test_utils/test_fs.py`:
   ```python
   def test_atomic_write_fsyncs_parent_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
       """With fsync=True the parent directory is fsynced so the rename is durable."""
       synced_fds: list[int] = []
       real_fsync = os.fsync

       def _record(fd: int) -> None:
           synced_fds.append(fd)
           real_fsync(fd)

       monkeypatch.setattr("openharness.utils.fs.os.fsync", _record)
       path = tmp_path / "out.txt"
       atomic_write_text(path, "payload", fsync=True)
       # One fsync for the file, one for the parent directory.
       assert len(synced_fds) == 2
       assert path.read_text() == "payload"


   def test_atomic_write_no_dir_fsync_when_fsync_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
       synced_fds: list[int] = []
       monkeypatch.setattr("openharness.utils.fs.os.fsync", lambda fd: synced_fds.append(fd))
       atomic_write_text(tmp_path / "out.txt", "payload", fsync=False)
       assert synced_fds == []
   ```
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_utils/test_fs.py::test_atomic_write_fsyncs_parent_dir -q
   ```
   Expected: `AssertionError: assert 1 == 2` (only the file is fsynced today).
3. - [ ] Write minimal implementation. In `src/openharness/utils/fs.py`, replace the body of the `try:` block in `atomic_write_bytes` (`fs.py:67-78`) with:
   ```python
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            tmp_file.write(data)
            tmp_file.flush()
            if fsync:
                os.fsync(tmp_file.fileno())
        _apply_mode(tmp_path, target_mode)
        os.replace(tmp_path, dst)
        if fsync:
            _fsync_dir(dst.parent)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise
   ```
   Then add this helper at the end of the module (after `_apply_mode`, `fs.py:122`):
   ```python
   def _fsync_dir(directory: Path) -> None:
       """Fsync a directory so a contained rename reaches stable storage.

       A rename is only durable once the directory entry is flushed. Best
       effort: opening a directory fd is not possible on every platform
       (Windows, some network mounts), so failures are swallowed — the
       payload file itself was already fsynced before the rename.
       """
       try:
           dir_fd = os.open(str(directory), os.O_RDONLY)
       except OSError:
           return
       try:
           os.fsync(dir_fd)
       except OSError:
           pass
       finally:
           os.close(dir_fd)
   ```
4. - [ ] Run, verify pass (and the whole fs suite stays green):
   ```bash
   python -m pytest tests/test_utils/test_fs.py -q
   ```
   Expected: all passed (the two new tests plus the existing ones).
5. - [ ] Commit:
   ```bash
   git add src/openharness/utils/fs.py tests/test_utils/test_fs.py && git commit -m "Fsync parent dir on durable atomic writes"
   ```

### Task 3: Append-only JSONL write + crash-safe read helpers

**Files:**
- Modify: `src/openharness/utils/fs.py` (`__all__` at `fs.py:36`; new `append_jsonl_line`, `read_jsonl_complete_lines`)
- Test: `tests/test_utils/test_fs.py`

**Design decision:** A crash mid-append can leave a partial final line. The reader returns only *complete* lines (a complete line is one terminated by `\n`); a trailing partial line is silently dropped. This is the crash-recovery contract relied on by the loader.

1. - [ ] Write the failing test. Add to `tests/test_utils/test_fs.py`:
   ```python
   def test_append_jsonl_line_appends_and_fsyncs(tmp_path: Path) -> None:
       from openharness.utils.fs import append_jsonl_line, read_jsonl_complete_lines

       path = tmp_path / "t.jsonl"
       append_jsonl_line(path, '{"a": 1}')
       append_jsonl_line(path, '{"a": 2}')
       assert path.read_text() == '{"a": 1}\n{"a": 2}\n'
       assert read_jsonl_complete_lines(path) == ['{"a": 1}', '{"a": 2}']


   def test_read_jsonl_drops_trailing_partial_line(tmp_path: Path) -> None:
       from openharness.utils.fs import read_jsonl_complete_lines

       path = tmp_path / "t.jsonl"
       # Simulate a crash mid-append: last line has no terminating newline.
       path.write_bytes(b'{"a": 1}\n{"a": 2}\n{"a": 3')
       assert read_jsonl_complete_lines(path) == ['{"a": 1}', '{"a": 2}']


   def test_read_jsonl_missing_file_is_empty(tmp_path: Path) -> None:
       from openharness.utils.fs import read_jsonl_complete_lines

       assert read_jsonl_complete_lines(tmp_path / "nope.jsonl") == []
   ```
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_utils/test_fs.py::test_append_jsonl_line_appends_and_fsyncs -q
   ```
   Expected: `ImportError: cannot import name 'append_jsonl_line'`.
3. - [ ] Write minimal implementation. In `src/openharness/utils/fs.py`, extend `__all__` (`fs.py:36`):
   ```python
   __all__ = [
       "atomic_write_bytes",
       "atomic_write_text",
       "read_text_tail",
       "append_jsonl_line",
       "read_jsonl_complete_lines",
   ]
   ```
   Then add after `read_text_tail` (`fs.py:108`):
   ```python
   def append_jsonl_line(
       path: str | os.PathLike[str],
       line: str,
       *,
       encoding: str = "utf-8",
       fsync: bool = True,
   ) -> None:
       """Append one newline-terminated line to a JSONL file durably.

       ``line`` must not already contain a trailing newline; exactly one is
       added. With ``fsync=True`` (default) the file is flushed to stable
       storage after the write — this is the single per-turn durability point
       for the v2 transcript. The parent directory is created on first write.
       """
       dst = Path(path)
       dst.parent.mkdir(parents=True, exist_ok=True)
       payload = (line + "\n").encode(encoding)
       with open(dst, "ab") as handle:
           handle.write(payload)
           handle.flush()
           if fsync:
               os.fsync(handle.fileno())


   def read_jsonl_complete_lines(
       path: str | os.PathLike[str],
       *,
       encoding: str = "utf-8",
   ) -> list[str]:
       """Return every complete (newline-terminated) line of a JSONL file.

       A line is "complete" only when it ends in ``\\n``. A trailing partial
       line — the signature of a crash mid-append — is dropped, so callers
       recover to the last fully-written record. A missing file yields ``[]``.
       """
       src = Path(path)
       try:
           raw = src.read_bytes()
       except FileNotFoundError:
           return []
       text = raw.decode(encoding, errors="replace")
       if not text:
           return []
       lines = text.split("\n")
       # split() leaves a trailing "" when the file ends in "\n" (complete);
       # a non-empty trailing element means the last line was not terminated.
       if lines and lines[-1] == "":
           lines.pop()
       else:
           lines.pop()  # drop the incomplete trailing line
       return [line for line in lines if line]
   ```
4. - [ ] Run, verify pass:
   ```bash
   python -m pytest tests/test_utils/test_fs.py -q
   ```
   Expected: all passed.
5. - [ ] Commit:
   ```bash
   git add src/openharness/utils/fs.py tests/test_utils/test_fs.py && git commit -m "Add append-only JSONL write and crash-safe read helpers"
   ```

---

## Phase 2 — The format module (sniffer, hash, v2 primitives)

### Task 4: Format sniffer

**Files:**
- Create: `src/openharness/services/session_format.py`
- Test: `tests/test_services/test_session_format.py`

**Design decision:** Format is detected from on-disk shape, not a settings flag (so a v1 file is still read correctly even when the setting is `v2`, and vice versa). Rule: a `latest.json` whose only meaningful key is `session_id` (no `messages` and no `model`) is a **v2 pointer**; the presence of a sibling `session-<id>.head.json` for an id marks that id as **v2**; everything else is **v1** (legacy full-format). The function operates on a parsed dict for `latest.json` and on a session dir + id for per-session detection.

1. - [ ] Write the failing test. Create `tests/test_services/test_session_format.py`:
   ```python
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
   ```
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_services/test_session_format.py -q
   ```
   Expected: `ModuleNotFoundError: No module named 'openharness.services.session_format'`.
3. - [ ] Write minimal implementation. Create `src/openharness/services/session_format.py`:
   ```python
   """On-disk session format primitives shared by openharness and ohmo.

   Two formats coexist:

   * **v1** (legacy): a single ``session-<id>.json`` (and a full ``latest.json``)
     holding the entire history, system prompt, usage, and metadata. Rewritten
     in full on every save.
   * **v2**: an append-only ``session-<id>.jsonl`` transcript (one message per
     line) plus a small ``session-<id>.head.json`` (model, system-prompt hash +
     rebuild inputs, usage, tool_metadata, message_count, summary, created_at),
     and a pointer ``latest.json`` of the form ``{"session_id": ...}``.

   Loaders always sniff the on-disk shape, so a v1 file is read as v1 even when
   the active ``session_storage_format`` is ``v2`` and vice versa. These are
   pure functions with no settings access.
   """

   from __future__ import annotations

   from pathlib import Path
   from typing import Any


   def detect_latest_format(payload: dict[str, Any]) -> str:
       """Classify a parsed ``latest.json`` payload as ``"v1"`` or ``"v2"``.

       A v2 pointer carries ``session_id`` and nothing load-bearing else (no
       ``messages``, no ``model``). Anything richer is a legacy full payload.
       """
       if "messages" in payload or "model" in payload:
           return "v1"
       if "session_id" in payload:
           return "v2"
       return "v1"


   def detect_session_format(session_dir: Path, session_id: str) -> str | None:
       """Classify a stored session by id, or ``None`` when neither file exists.

       A ``session-<id>.head.json`` marks v2; a lone ``session-<id>.json`` is v1.
       """
       if (session_dir / f"session-{session_id}.head.json").exists():
           return "v2"
       if (session_dir / f"session-{session_id}.json").exists():
           return "v1"
       return None
   ```
4. - [ ] Run, verify pass:
   ```bash
   python -m pytest tests/test_services/test_session_format.py -q
   ```
   Expected: 6 passed.
5. - [ ] Commit:
   ```bash
   git add src/openharness/services/session_format.py tests/test_services/test_session_format.py && git commit -m "Add session format sniffer"
   ```

### Task 5: System-prompt fingerprint helper

**Files:**
- Modify: `src/openharness/services/session_format.py`
- Test: `tests/test_services/test_session_format.py`

**Design decision (sub-item i):** v2 stores `system_prompt_sha256` (a hex digest of the built prompt) plus the *rebuild inputs* already available at save time — `model` and the persistable `tool_metadata` are already in the head; we add nothing the runtime can't already reconstruct. The full prompt text is **not** stored. This is safe because no loader ever reads `system_prompt` back into a runtime (verified: `build_runtime` always rebuilds it via `build_runtime_system_prompt_with_cache_boundary`, `runtime.py:491`); the only readers of the stored `system_prompt` were the writers and tests. The hash is retained purely for diagnostics/debugging ("did the prompt change between turns?").

1. - [ ] Write the failing test. Add to `tests/test_services/test_session_format.py`:
   ```python
   def test_system_prompt_fingerprint_is_stable_sha256():
       from openharness.services.session_format import system_prompt_fingerprint

       fp = system_prompt_fingerprint("You are a helpful assistant.")
       assert fp == system_prompt_fingerprint("You are a helpful assistant.")
       assert len(fp) == 64  # sha256 hex digest
       assert fp != system_prompt_fingerprint("different")


   def test_system_prompt_fingerprint_empty():
       from openharness.services.session_format import system_prompt_fingerprint

       assert len(system_prompt_fingerprint("")) == 64
   ```
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_services/test_session_format.py::test_system_prompt_fingerprint_is_stable_sha256 -q
   ```
   Expected: `ImportError: cannot import name 'system_prompt_fingerprint'`.
3. - [ ] Write minimal implementation. Add to the top imports of `src/openharness/services/session_format.py`:
   ```python
   from hashlib import sha256
   ```
   Then add the function after `detect_session_format`:
   ```python
   def system_prompt_fingerprint(system_prompt: str) -> str:
       """Return the sha256 hex digest of a built system prompt.

       v2 persists this digest instead of the full prompt text. The prompt is
       always rebuilt on resume from ``model`` + ``tool_metadata`` (the rebuild
       inputs already in the head), so the text itself is never needed on disk;
       the digest is kept only as a debugging signal for prompt drift.
       """
       return sha256(system_prompt.encode("utf-8")).hexdigest()
   ```
4. - [ ] Run, verify pass:
   ```bash
   python -m pytest tests/test_services/test_session_format.py -q
   ```
   Expected: 8 passed.
5. - [ ] Commit:
   ```bash
   git add src/openharness/services/session_format.py tests/test_services/test_session_format.py && git commit -m "Add system_prompt_fingerprint helper for v2 heads"
   ```

### Task 6: v2 head read/write

**Files:**
- Modify: `src/openharness/services/session_format.py`
- Test: `tests/test_services/test_session_format.py`

1. - [ ] Write the failing test. Add to `tests/test_services/test_session_format.py`:
   ```python
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
   ```
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_services/test_session_format.py::test_write_and_read_head_round_trip -q
   ```
   Expected: `ImportError: cannot import name 'write_head'`.
3. - [ ] Write minimal implementation. Add to the imports of `src/openharness/services/session_format.py`:
   ```python
   import json

   from openharness.utils.fs import atomic_write_text
   ```
   Then add:
   ```python
   def head_path(session_dir: Path, session_id: str) -> Path:
       return session_dir / f"session-{session_id}.head.json"


   def transcript_path(session_dir: Path, session_id: str) -> Path:
       return session_dir / f"session-{session_id}.jsonl"


   def write_head(session_dir: Path, session_id: str, head: dict[str, Any]) -> None:
       """Atomically rewrite the per-session head file.

       Atomic-rename without per-write fsync: a crash loses at most cosmetic
       head metadata (the transcript stays durable), so the durability cost of
       an fsync per turn is not paid here.
       """
       atomic_write_text(
           head_path(session_dir, session_id),
           json.dumps(head, indent=2) + "\n",
           fsync=False,
       )


   def read_head(session_dir: Path, session_id: str) -> dict[str, Any] | None:
       path = head_path(session_dir, session_id)
       if not path.exists():
           return None
       try:
           payload = json.loads(path.read_text(encoding="utf-8"))
       except (json.JSONDecodeError, OSError):
           return None
       return payload if isinstance(payload, dict) else None
   ```
4. - [ ] Run, verify pass:
   ```bash
   python -m pytest tests/test_services/test_session_format.py -q
   ```
   Expected: 10 passed.
5. - [ ] Commit:
   ```bash
   git add src/openharness/services/session_format.py tests/test_services/test_session_format.py && git commit -m "Add v2 head read/write primitives"
   ```

### Task 7: v2 transcript append, full-load, and compaction rewrite

**Files:**
- Modify: `src/openharness/services/session_format.py`
- Test: `tests/test_services/test_session_format.py`

**Design decision (delta append + compaction):** Engine messages are append-only between compactions. The save path appends only the messages past `last_persisted_count` (read from the head's `message_count`). On compaction the message list shrinks/changes, which the caller signals by passing `compacted=True`; the transcript is then rewritten in full once and a `{"__compacted_at__": <ts>}` marker line is written first so a reader can see a rewrite boundary. `load_v2_snapshot` reads every complete transcript line, ignores marker lines, and keeps only the records *after the last marker* (the live history), guaranteeing the loaded history matches the post-compaction state.

1. - [ ] Write the failing test. Add to `tests/test_services/test_session_format.py`:
   ```python
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
   ```
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_services/test_session_format.py::test_append_messages_delta_only -q
   ```
   Expected: `ImportError: cannot import name 'append_messages_to_transcript'`.
3. - [ ] Write minimal implementation. Add to the imports of `src/openharness/services/session_format.py`:
   ```python
   from openharness.engine.messages import ConversationMessage
   from openharness.utils.fs import (
       append_jsonl_line,
       atomic_write_text,
       read_jsonl_complete_lines,
   )
   ```
   (replace the earlier `from openharness.utils.fs import atomic_write_text` line with the grouped import above). Then add:
   ```python
   _COMPACTION_MARKER = "__compacted_at__"


   def append_messages_to_transcript(
       session_dir: Path,
       session_id: str,
       messages: list[ConversationMessage],
       *,
       last_persisted_count: int,
   ) -> None:
       """Append only the messages past ``last_persisted_count`` (one fsync).

       The whole batch is written as individual lines and the file is fsynced
       once at the end via the final ``append_jsonl_line`` call — the single
       per-turn durability point.
       """
       session_dir.mkdir(parents=True, exist_ok=True)
       new_messages = messages[last_persisted_count:]
       if not new_messages:
           return
       path = transcript_path(session_dir, session_id)
       last = len(new_messages) - 1
       for index, message in enumerate(new_messages):
           line = json.dumps(message.model_dump(mode="json"), separators=(",", ":"))
           append_jsonl_line(path, line, fsync=(index == last))


   def rewrite_transcript(
       session_dir: Path,
       session_id: str,
       messages: list[ConversationMessage],
   ) -> None:
       """Rewrite the transcript after a compaction.

       Writes a compaction marker line followed by the post-compaction history,
       atomically replacing the file. Readers keep only records after the last
       marker, so the loaded history always matches the compacted state.
       """
       import time

       lines = [json.dumps({_COMPACTION_MARKER: time.time()}, separators=(",", ":"))]
       lines.extend(
           json.dumps(message.model_dump(mode="json"), separators=(",", ":"))
           for message in messages
       )
       atomic_write_text(
           transcript_path(session_dir, session_id),
           "\n".join(lines) + "\n",
           fsync=True,
       )


   def load_v2_snapshot(session_dir: Path, session_id: str) -> list[dict[str, Any]]:
       """Return raw message dicts from a v2 transcript, post-last-compaction.

       Skips marker lines, discards everything up to and including the last
       marker, and ignores any malformed line. The result feeds the same
       sanitize/validate path as v1 messages.
       """
       records: list[dict[str, Any]] = []
       for raw in read_jsonl_complete_lines(transcript_path(session_dir, session_id)):
           try:
               obj = json.loads(raw)
           except json.JSONDecodeError:
               continue
           if not isinstance(obj, dict):
               continue
           if _COMPACTION_MARKER in obj:
               records.clear()  # drop pre-compaction history
               continue
           records.append(obj)
       return records
   ```
4. - [ ] Run, verify pass:
   ```bash
   python -m pytest tests/test_services/test_session_format.py -q
   ```
   Expected: 13 passed.
5. - [ ] Commit:
   ```bash
   git add src/openharness/services/session_format.py tests/test_services/test_session_format.py && git commit -m "Add v2 transcript append, load, and compaction rewrite"
   ```

---

## Phase 3 — Wire v2 into openharness session_storage

### Task 8: v2 save path behind the format flag

**Files:**
- Modify: `src/openharness/services/session_storage.py` (`save_session_snapshot`, `session_storage.py:114-174`; new `_save_session_snapshot_v2`)
- Test: `tests/test_services/test_session_storage.py`

**Design decision:** `save_session_snapshot` reads `load_settings().session_storage_format`; on `"v2"` it routes to `_save_session_snapshot_v2`, otherwise it keeps the existing v1 body verbatim (the revert switch). v2 computes the delta from the head's prior `message_count`, appends, rewrites the head (with `system_prompt_sha256`, not the prompt), writes the `latest.json` pointer, updates the index, and feeds the conversation index with the *same* payload shape v1 produced (so `_update_conversation_index` is unchanged). Return value stays `latest_path` (the pointer file) — callers only use it as a truthy Path.

1. - [ ] Write the failing test. Add to `tests/test_services/test_session_storage.py`:
   ```python
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
   ```
   > **Verified mechanism:** `get_config_file_path()` (`config/paths.py:44`) returns `get_config_dir() / "settings.json"`, and `get_config_dir()` (`config/paths.py:28-41`) honors `OPENHARNESS_CONFIG_DIR`. Setting that env var to a temp dir and writing `settings.json` inside it makes `load_settings()` pick up the format flag. (`OPENHARNESS_DATA_DIR` controls only the *sessions* dir, not the settings file, so the two env vars point at different temp dirs above.)
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py::test_v2_save_creates_transcript_head_and_pointer -q
   ```
   Expected: fails — `session-v2sess.jsonl` does not exist (v1 path still runs).
3. - [ ] Write minimal implementation. In `src/openharness/services/session_storage.py`, add to the imports (after `session_storage.py:15`):
   ```python
   from openharness.services import session_format
   ```
   Replace the body of `save_session_snapshot` from `session_dir = get_project_session_dir(cwd)` (`session_storage.py:125`) down to `return latest_path` (`session_storage.py:174`) with a router that keeps the existing v1 body in a helper:
   ```python
       from openharness.config import load_settings

       session_dir = get_project_session_dir(cwd)
       sid = session_id or uuid4().hex[:12]
       now = time.time()
       messages = sanitize_conversation_messages(messages)
       summary = ""
       for msg in messages:
           if msg.role == "user" and msg.text.strip():
               summary = msg.text.strip()[:80]
               break

       fmt = load_settings().session_storage_format
       if fmt == "v2":
           return _save_session_snapshot_v2(
               session_dir=session_dir,
               sid=sid,
               cwd=cwd,
               model=model,
               system_prompt=system_prompt,
               messages=messages,
               usage=usage,
               tool_metadata=tool_metadata,
               summary=summary,
               now=now,
           )
       return _save_session_snapshot_v1(
           session_dir=session_dir,
           sid=sid,
           cwd=cwd,
           model=model,
           system_prompt=system_prompt,
           messages=messages,
           usage=usage,
           tool_metadata=tool_metadata,
           summary=summary,
           now=now,
       )
   ```
   Add the two helpers immediately after `save_session_snapshot`. `_save_session_snapshot_v1` is the *old body* lifted verbatim (payload build + watchdog + dual atomic_write + index + conversation index + record + `return latest_path`):
   ```python
   def _save_session_snapshot_v1(
       *,
       session_dir: Path,
       sid: str,
       cwd: str | Path,
       model: str,
       system_prompt: str,
       messages: list[ConversationMessage],
       usage: UsageSnapshot,
       tool_metadata: dict[str, object] | None,
       summary: str,
       now: float,
   ) -> Path:
       payload = {
           "session_id": sid,
           "cwd": str(Path(cwd).resolve()),
           "model": model,
           "system_prompt": system_prompt,
           "messages": [message.model_dump(mode="json") for message in messages],
           "usage": usage.model_dump(),
           "tool_metadata": _persistable_tool_metadata(tool_metadata),
           "created_at": now,
           "summary": summary,
           "message_count": len(messages),
       }
       data = json.dumps(payload, indent=2) + "\n"

       from openharness.diagnostics import watchdog

       with watchdog.track("snapshot_write", session_id=sid):
           latest_path = session_dir / "latest.json"
           atomic_write_text(latest_path, data, fsync=False)
           session_path = session_dir / f"session-{sid}.json"
           atomic_write_text(session_path, data, fsync=False)
           _update_session_index(session_dir, _session_index_entry(payload, session_path))
           _update_conversation_index(payload)

       from openharness.diagnostics import record

       record(
           "storage",
           "snapshot_save",
           "completed",
           duration_ms=(time.time() - now) * 1000.0,
           session_id=sid,
           attrs={"app": "openharness", "size_bytes": len(data), "message_count": len(messages)},
       )
       return latest_path


   def _save_session_snapshot_v2(
       *,
       session_dir: Path,
       sid: str,
       cwd: str | Path,
       model: str,
       system_prompt: str,
       messages: list[ConversationMessage],
       usage: UsageSnapshot,
       tool_metadata: dict[str, object] | None,
       summary: str,
       now: float,
   ) -> Path:
       from openharness.diagnostics import watchdog

       prior_head = session_format.read_head(session_dir, sid)
       last_persisted = int(prior_head.get("message_count", 0)) if prior_head else 0
       created_at = prior_head.get("created_at", now) if prior_head else now
       # A shrink (or any non-append edit) means the history was compacted —
       # rewrite the transcript in full; otherwise append the delta.
       compacted = last_persisted > len(messages)

       with watchdog.track("snapshot_write", session_id=sid):
           if compacted or last_persisted == 0 and prior_head is not None:
               session_format.rewrite_transcript(session_dir, sid, messages)
           elif compacted:
               session_format.rewrite_transcript(session_dir, sid, messages)
           else:
               session_format.append_messages_to_transcript(
                   session_dir, sid, messages, last_persisted_count=last_persisted
               )

           head = {
               "session_id": sid,
               "cwd": str(Path(cwd).resolve()),
               "model": model,
               "system_prompt_sha256": session_format.system_prompt_fingerprint(system_prompt),
               "usage": usage.model_dump(),
               "tool_metadata": _persistable_tool_metadata(tool_metadata),
               "created_at": created_at,
               "summary": summary,
               "message_count": len(messages),
           }
           session_format.write_head(session_dir, sid, head)

           latest_path = session_dir / "latest.json"
           atomic_write_text(
               latest_path, json.dumps({"session_id": sid}) + "\n", fsync=False
           )

           index_payload = {**head, "messages": []}
           _update_session_index(session_dir, _session_index_entry(index_payload, session_dir / f"session-{sid}.head.json"))
           _update_conversation_index({**head, "messages": [m.model_dump(mode="json") for m in messages]})

       from openharness.diagnostics import record

       record(
           "storage",
           "snapshot_save",
           "completed",
           duration_ms=(time.time() - now) * 1000.0,
           session_id=sid,
           attrs={"app": "openharness", "message_count": len(messages), "format": "v2"},
       )
       return latest_path
   ```
   > **Note:** the `compacted` branch above is written verbosely for clarity; collapse the redundant `if/elif` in review to a single `if compacted: rewrite else: append` — the extra clause is a no-op kept only to make the compaction path explicit during first implementation. Remove it once Task 9/12 tests pass.
4. - [ ] Run, verify pass (both new tests + the existing v1 round-trip):
   ```bash
   python -m pytest tests/test_services/test_session_storage.py::test_v2_save_creates_transcript_head_and_pointer tests/test_services/test_session_storage.py::test_v1_revert_switch_writes_full_session_file -q
   ```
   Expected: 2 passed.
5. - [ ] Commit:
   ```bash
   git add src/openharness/services/session_storage.py tests/test_services/test_session_storage.py && git commit -m "Route session save through v2 format behind the flag"
   ```

### Task 9: v2 load path — pointer resolution + sniff + single-pass

**Files:**
- Modify: `src/openharness/services/session_storage.py` (`_sanitize_snapshot_payload` `session_storage.py:191-201`, `load_session_snapshot` `session_storage.py:204-209`, `load_session_by_id` `session_storage.py:297-310`; new `_load_v2_payload`)
- Test: `tests/test_services/test_session_storage.py`

**Design decision (single-pass resume — sub-item g):** the current `_sanitize_snapshot_payload` does validate → dump → re-validate (`session_storage.py:196-199`); messages are dumped back to dicts after sanitizing. We keep the *public shape* (messages as list-of-dicts) but drop the wasteful second dump by sanitizing once and reusing the already-`model_dump`ed list. Functionally identical output, half the pydantic work.

1. - [ ] Write the failing test. Add to `tests/test_services/test_session_storage.py`:
   ```python
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
   ```
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py::test_v2_load_latest_via_pointer_round_trip -q
   ```
   Expected: fails — `load_session_snapshot` reads the pointer's `{"session_id": ...}` as the payload, so `model`/`messages` are missing.
3. - [ ] Write minimal implementation. In `src/openharness/services/session_storage.py`:
   First, make `_sanitize_snapshot_payload` single-pass — replace its body (`session_storage.py:191-201`):
   ```python
   def _sanitize_snapshot_payload(payload: dict[str, Any]) -> dict[str, Any]:
       """Normalize persisted messages for forward compatibility (single pass)."""
       raw_messages = payload.get("messages", [])
       if not isinstance(raw_messages, list):
           return payload
       sanitized = sanitize_conversation_messages(
           [ConversationMessage.model_validate(item) for item in raw_messages]
       )
       payload = dict(payload)
       payload["messages"] = [message.model_dump(mode="json") for message in sanitized]
       payload["message_count"] = len(sanitized)
       return payload
   ```
   Add a v2 payload assembler after `_sanitize_snapshot_payload`:
   ```python
   def _load_v2_payload(session_dir: Path, session_id: str) -> dict[str, Any] | None:
       """Reassemble a v1-shaped snapshot dict from a v2 head + transcript."""
       head = session_format.read_head(session_dir, session_id)
       if head is None:
           return None
       raw_messages = session_format.load_v2_snapshot(session_dir, session_id)
       payload = dict(head)
       payload["messages"] = raw_messages
       # system_prompt is rebuilt by build_runtime; loaders never read it back.
       payload.setdefault("system_prompt", "")
       return _sanitize_snapshot_payload(payload)
   ```
   Replace `load_session_snapshot` (`session_storage.py:204-209`):
   ```python
   def load_session_snapshot(cwd: str | Path) -> dict[str, Any] | None:
       """Load the most recent session snapshot for the project."""
       session_dir = get_project_session_dir(cwd)
       path = session_dir / "latest.json"
       if not path.exists():
           return None
       try:
           raw = json.loads(path.read_text(encoding="utf-8"))
       except (json.JSONDecodeError, OSError):
           return None
       if session_format.detect_latest_format(raw) == "v2":
           sid = str(raw.get("session_id") or "")
           return _load_v2_payload(session_dir, sid) if sid else None
       return _sanitize_snapshot_payload(raw)
   ```
   Replace `load_session_by_id` (`session_storage.py:297-310`):
   ```python
   def load_session_by_id(cwd: str | Path, session_id: str) -> dict[str, Any] | None:
       """Load a specific session by ID."""
       session_dir = get_project_session_dir(cwd)
       fmt = session_format.detect_session_format(session_dir, session_id)
       if fmt == "v2":
           return _load_v2_payload(session_dir, session_id)
       if fmt == "v1":
           path = session_dir / f"session-{session_id}.json"
           return _sanitize_snapshot_payload(json.loads(path.read_text(encoding="utf-8")))
       # Fallback to latest.json if it resolves to this id.
       snap = load_session_snapshot(cwd)
       if snap is not None and (snap.get("session_id") == session_id or session_id == "latest"):
           return snap
       return None
   ```
4. - [ ] Run, verify pass (new tests + the legacy-sanitize test must still pass):
   ```bash
   python -m pytest tests/test_services/test_session_storage.py -q
   ```
   Expected: all passed.
5. - [ ] Commit:
   ```bash
   git add src/openharness/services/session_storage.py tests/test_services/test_session_storage.py && git commit -m "Resolve latest.json pointer and load v2 snapshots single-pass"
   ```

### Task 10: Trust the index unconditionally + one-time backfill + compact stale entries

**Files:**
- Modify: `src/openharness/services/session_storage.py` (`list_session_snapshots` `session_storage.py:212-294`; `_write_session_index` `session_storage.py:92-98` for stale compaction; new `_backfill_index`)
- Test: `tests/test_services/test_session_storage.py`

**Design decision (sub-item d):** when the index file exists, `list_session_snapshots` returns its entries (filtered to those whose backing file still exists) regardless of count — dropping the `len(sessions) >= limit` gate at `session_storage.py:234`. When the index does *not* exist, a one-time backfill scans both `session-*.json` (v1) and `session-*.head.json` (v2) files, builds the index, and writes it once; subsequent lists are index-only. Stale entries (backing file gone) are compacted out at the *next save's* `_write_session_index` (they are currently filtered on read but never removed, `session_storage.py:190`/`220`). `latest.json` is no longer scanned as a pseudo-session under v2 (it is a pointer); the legacy `latest.json` fallback is kept only when the index is empty AND it is a v1 full payload.

1. - [ ] Write the failing test. Add to `tests/test_services/test_session_storage.py`:
   ```python
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
   ```
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py::test_backfill_builds_index_from_legacy_files -q
   ```
   Expected: passes the listing assert but fails `assert (session_dir / "sessions-index.json").exists()` (no backfill is persisted today).
3. - [ ] Write minimal implementation. In `src/openharness/services/session_storage.py`:
   Make `_write_session_index` drop stale entries — replace its body (`session_storage.py:92-98`):
   ```python
   def _write_session_index(session_dir: Path, entries: list[dict[str, Any]]) -> None:
       live = [
           entry
           for entry in entries
           if (session_dir / str(entry.get("path") or "")).exists()
       ]
       live = sorted(live, key=lambda item: item.get("created_at", 0), reverse=True)
       atomic_write_text(
           _session_index_path(session_dir),
           json.dumps({"version": 1, "sessions": live}, indent=2) + "\n",
           fsync=False,
       )
   ```
   Add a backfill builder after `_update_session_index` (`session_storage.py:111`):
   ```python
   def _backfill_index(session_dir: Path) -> list[dict[str, Any]]:
       """Build the index once from legacy v1 and v2 files, then persist it."""
       entries: list[dict[str, Any]] = []
       seen: set[str] = set()
       for head_file in session_dir.glob("session-*.head.json"):
           sid = head_file.stem[len("session-"):-len(".head")]
           head = session_format.read_head(session_dir, sid)
           if head is None or sid in seen:
               continue
           seen.add(sid)
           entries.append(_session_index_entry({**head, "messages": []}, head_file))
       for json_file in session_dir.glob("session-*.json"):
           if json_file.name.endswith(".head.json"):
               continue
           sid = json_file.stem.replace("session-", "")
           if sid in seen:
               continue
           try:
               data = json.loads(json_file.read_text(encoding="utf-8"))
           except (json.JSONDecodeError, OSError):
               continue
           seen.add(sid)
           entries.append(_session_index_entry(data, json_file))
       if entries:
           _write_session_index(session_dir, entries)
       return entries
   ```
   Replace `list_session_snapshots` (`session_storage.py:212-294`) with the index-trusting version:
   ```python
   def list_session_snapshots(cwd: str | Path, limit: int = 20) -> list[dict[str, Any]]:
       """List saved sessions for the project, newest first.

       Trusts the index whenever it exists (any count). Builds it once via a
       backfill when absent, then lists from the index forever after.
       """
       session_dir = get_project_session_dir(cwd)
       indexed = _load_session_index(session_dir)
       if not indexed:
           indexed = _backfill_index(session_dir)
       sessions: list[dict[str, Any]] = []
       for item in indexed:
           if not (session_dir / str(item.get("path") or "")).exists():
               continue
           sessions.append(
               {
                   "session_id": item.get("session_id", ""),
                   "summary": item.get("summary", ""),
                   "message_count": item.get("message_count", 0),
                   "model": item.get("model", ""),
                   "created_at": item.get("created_at", 0),
               }
           )
       sessions.sort(key=lambda item: item.get("created_at", 0), reverse=True)
       return sessions[:limit]
   ```
4. - [ ] Run, verify pass (new tests + the existing `test_list_session_snapshots_merges_index_with_legacy_files`):
   ```bash
   python -m pytest tests/test_services/test_session_storage.py -q
   ```
   Expected: all passed.
5. - [ ] Commit:
   ```bash
   git add src/openharness/services/session_storage.py tests/test_services/test_session_storage.py && git commit -m "Trust the session index, backfill legacy files, compact stale entries"
   ```

### Task 11: Retention pruning on save

**Files:**
- Modify: `src/openharness/services/session_storage.py` (new `_prune_sessions`; call it from both save helpers)
- Test: `tests/test_services/test_session_storage.py`

**Design decision (sub-item e):** after a successful save, prune oldest-first by `created_at` from the index down to `session_retention_max_files`, and drop anything older than `session_retention_max_age_days`. **Never** prune the session just saved (the active one) or the id `latest.json` currently points at. Pruning deletes the backing files (v2: `.jsonl` + `.head.json`; v1: `.json`) and rewrites the index. `0` for either limit disables that rule. Pruning is wrapped so a failure never breaks the save (best-effort, like the conversation index).

1. - [ ] Write the failing test. Add to `tests/test_services/test_session_storage.py`:
   ```python
   import time as _time


   def test_retention_prunes_oldest_keeps_active_and_latest(tmp_path: Path, monkeypatch):
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
       project = tmp_path / "repo"
       project.mkdir()
       config_dir = tmp_path / "cfg"
       config_dir.mkdir()
       (config_dir / "settings.json").write_text('{"session_retention_max_files": 2, "session_retention_max_age_days": 0}', encoding="utf-8")
       monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(config_dir))

       for i in range(4):
           save_session_snapshot(
               cwd=project, model="m", system_prompt="s", session_id=f"s{i}",
               messages=[ConversationMessage(role="user", content=[TextBlock(text=f"m{i}")])],
               usage=UsageSnapshot(),
           )
           _time.sleep(0.01)  # distinct created_at ordering

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

       session_dir = get_project_session_dir(project)
       # Inject an ancient v1 session directly into the index.
       (session_dir / "session-ancient.json").write_text(
           json.dumps({"session_id": "ancient", "summary": "old", "message_count": 1,
                       "model": "m", "created_at": 1.0, "messages": []}),
           encoding="utf-8",
       )
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
   ```
   > Uses the same verified `OPENHARNESS_CONFIG_DIR` mechanism as Task 8.
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py::test_retention_prunes_oldest_keeps_active_and_latest -q
   ```
   Expected: fails — all 4 sessions are retained (no pruning yet).
3. - [ ] Write minimal implementation. In `src/openharness/services/session_storage.py`, add after `_backfill_index`:
   ```python
   def _delete_session_files(session_dir: Path, session_id: str) -> None:
       for suffix in (".jsonl", ".head.json", ".json"):
           candidate = session_dir / f"session-{session_id}{suffix}"
           try:
               candidate.unlink()
           except FileNotFoundError:
               pass
           except OSError:
               pass


   def _prune_sessions(session_dir: Path, *, active_id: str, settings: Any) -> None:
       """Prune oldest/aged-out sessions, never the active or latest-pointed one."""
       max_files = int(getattr(settings, "session_retention_max_files", 0) or 0)
       max_age_days = int(getattr(settings, "session_retention_max_age_days", 0) or 0)
       if max_files <= 0 and max_age_days <= 0:
           return

       protected = {active_id}
       latest_path = session_dir / "latest.json"
       if latest_path.exists():
           try:
               raw = json.loads(latest_path.read_text(encoding="utf-8"))
               pointed = str(raw.get("session_id") or "")
               if pointed:
                   protected.add(pointed)
           except (json.JSONDecodeError, OSError):
               pass

       entries = sorted(
           _load_session_index(session_dir),
           key=lambda item: item.get("created_at", 0),
           reverse=True,
       )
       to_delete: list[str] = []
       cutoff = time.time() - max_age_days * 86400 if max_age_days > 0 else None
       kept = 0
       for entry in entries:
           sid = str(entry.get("session_id") or "")
           if sid in protected:
               kept += 1
               continue
           created = float(entry.get("created_at", 0) or 0)
           too_old = cutoff is not None and created < cutoff
           over_count = max_files > 0 and kept >= max_files
           if too_old or over_count:
               to_delete.append(sid)
           else:
               kept += 1
       if not to_delete:
           return
       for sid in to_delete:
           _delete_session_files(session_dir, sid)
       remaining = [
           entry
           for entry in entries
           if str(entry.get("session_id") or "") not in set(to_delete)
       ]
       _write_session_index(session_dir, remaining)
   ```
   Then call it (best-effort) at the end of **both** save helpers, just before each `return latest_path`:
   ```python
       try:
           from openharness.config import load_settings

           _prune_sessions(session_dir, active_id=sid, settings=load_settings())
       except Exception:
           pass
       return latest_path
   ```
4. - [ ] Run, verify pass:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py -q
   ```
   Expected: all passed.
5. - [ ] Commit:
   ```bash
   git add src/openharness/services/session_storage.py tests/test_services/test_session_storage.py && git commit -m "Add session retention pruning on save"
   ```

### Task 12: Byte-budget benchmark + end-to-end compaction round-trip

**Files:**
- Test only: `tests/test_services/test_session_storage.py`

**Design decision:** the acceptance criterion "bytes/line drops to O(new-turn size)" is asserted by counting bytes written across two consecutive saves of a large history. We measure the *delta* written on the second save (one extra short message) by intercepting `append_jsonl_line` and the head/index writes via a tmpdir size diff. We assert the transcript-append delta is bounded (< 50 KB for a 200-message session whose append is a single short message).

1. - [ ] Write the failing test (it will pass once v2 is wired — this is the guardrail). Add to `tests/test_services/test_session_storage.py`:
   ```python
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
   ```
2. - [ ] Run it, verify it passes (v2 is already wired from Tasks 8–9; these lock the behavior). If `test_v2_append_delta_is_bounded` fails because the whole history was rewritten, the `compacted` logic in `_save_session_snapshot_v2` is wrong — fix it so a pure append (no shrink) takes the append branch:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py::test_v2_append_delta_is_bounded tests/test_services/test_session_storage.py::test_v2_compaction_shrink_round_trip -q
   ```
   Expected: 2 passed.
3. - [ ] (If green on first run, no implementation change needed.) Simplify the redundant `if/elif` left in `_save_session_snapshot_v2` from Task 8 down to:
   ```python
           if compacted:
               session_format.rewrite_transcript(session_dir, sid, messages)
           else:
               session_format.append_messages_to_transcript(
                   session_dir, sid, messages, last_persisted_count=last_persisted
               )
   ```
4. - [ ] Run the full file again, verify pass:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py -q
   ```
   Expected: all passed.
5. - [ ] Commit:
   ```bash
   git add src/openharness/services/session_storage.py tests/test_services/test_session_storage.py && git commit -m "Assert v2 append byte-budget and compaction round-trip"
   ```

---

## Phase 4 — Crash-consistency and legacy fixtures

### Task 13: Crash-consistency at the storage layer (truncated transcript)

**Files:**
- Test only: `tests/test_services/test_session_storage.py`

**Design decision:** Task 7 proved the *primitive* recovers from a truncated line; this proves the full `load_session_by_id` path recovers a usable snapshot when the live transcript is truncated mid-append. The loader must return the last complete history and a valid dict shape.

1. - [ ] Write the test. Add to `tests/test_services/test_session_storage.py`:
   ```python
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
   ```
2. - [ ] Run it, verify it passes (the crash-safe reader from Task 3 + the v2 loader from Task 9 already deliver this):
   ```bash
   python -m pytest tests/test_services/test_session_storage.py::test_crash_truncated_transcript_loads_last_complete_history -q
   ```
   Expected: 1 passed. If it fails, the regression is in `read_jsonl_complete_lines` (Task 3) or `load_v2_snapshot` (Task 7) — fix there, not here.
3. - [ ] Commit:
   ```bash
   git add tests/test_services/test_session_storage.py && git commit -m "Assert v2 loader recovers from a truncated transcript"
   ```

### Task 14: Legacy v1 fixtures still load + backend shape unchanged

**Files:**
- Test only: `tests/test_services/test_session_storage.py`

**Design decision:** the strongest compat guarantee — a *full* v1 `latest.json` AND a v1 `session-<id>.json` (the exact shapes written before this change) load identically through the public functions, and the `OpenHarnessSessionBackend` returns the same dict keys it always did. This is the no-interface-break test.

1. - [ ] Write the test. Add to `tests/test_services/test_session_storage.py`:
   ```python
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
   ```
2. - [ ] Run it, verify it passes:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py::test_legacy_v1_full_latest_still_loads tests/test_services/test_session_storage.py::test_legacy_v1_session_file_loads_by_id tests/test_services/test_session_storage.py::test_backend_load_shape_unchanged -q
   ```
   Expected: 3 passed.
3. - [ ] Run the **whole** openharness storage + fs + format suite to confirm no regression:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py tests/test_services/test_session_format.py tests/test_utils/test_fs.py -q
   ```
   Expected: all passed.
4. - [ ] Commit:
   ```bash
   git add tests/test_services/test_session_storage.py && git commit -m "Assert legacy v1 fixtures load and backend shape unchanged"
   ```

---

## Phase 5 — ohmo twin (sub-item h)

### Task 15: v2 ohmo save + load via shared primitives

**Files:**
- Modify: `ohmo/session_storage.py` (`save_session_snapshot` `ohmo/session_storage.py:92-137`, `load_latest`/`load_latest_for_session_key`/`load_by_id` `ohmo/session_storage.py:140-209`)
- Test: `tests/test_ohmo/test_ohmo_session_storage.py`

**Design decision:** ohmo reuses `session_format` for the transcript/head/pointer primitives but keeps its extra surface: the `app: "ohmo"` and `session_key` fields go into the head; `latest.json` AND `latest-<token>.json` become pointers `{"session_id": ...}`; `load_latest_for_session_key` resolves the token pointer then loads the v2 payload. Gated by the same `session_storage_format` setting (ohmo reads it via `openharness.config.load_settings`, the same source). Legacy ohmo files remain readable via the sniffer. We add an `_load_ohmo_v2_payload` mirroring `_load_v2_payload` but injecting `app`/`session_key` from the head.

1. - [ ] Write the failing test. Add to `tests/test_ohmo/test_ohmo_session_storage.py`:
   ```python
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
   ```
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_ohmo/test_ohmo_session_storage.py::test_ohmo_v2_save_and_load_round_trip -q
   ```
   Expected: fails — `session-o2.jsonl` does not exist (ohmo still writes full v1).
3. - [ ] Write minimal implementation. In `ohmo/session_storage.py`, add to the imports (after `ohmo/session_storage.py:19`):
   ```python
   from openharness.services import session_format
   ```
   Replace the body of `save_session_snapshot` from `payload = {` (`ohmo/session_storage.py:115`) through `return latest_path` (`ohmo/session_storage.py:137`) with a v1/v2 router:
   ```python
       from openharness.config import load_settings

       fmt = load_settings().session_storage_format
       if fmt == "v2":
           prior_head = session_format.read_head(session_dir, sid)
           last_persisted = int(prior_head.get("message_count", 0)) if prior_head else 0
           created_at = prior_head.get("created_at", now) if prior_head else now
           compacted = last_persisted > len(messages)
           if compacted:
               session_format.rewrite_transcript(session_dir, sid, messages)
           else:
               session_format.append_messages_to_transcript(
                   session_dir, sid, messages, last_persisted_count=last_persisted
               )
           head = {
               "app": "ohmo",
               "session_id": sid,
               "session_key": session_key,
               "cwd": str(Path(cwd).resolve()),
               "model": model,
               "system_prompt_sha256": session_format.system_prompt_fingerprint(system_prompt),
               "usage": usage.model_dump(),
               "tool_metadata": _persistable_tool_metadata(tool_metadata),
               "created_at": created_at,
               "summary": summary,
               "message_count": len(messages),
           }
           session_format.write_head(session_dir, sid, head)
           pointer = json.dumps({"session_id": sid}) + "\n"
           latest_path = session_dir / "latest.json"
           atomic_write_text(latest_path, pointer)
           if session_key:
               atomic_write_text(_session_key_latest_path(workspace, session_key), pointer)
           _update_session_index(
               session_dir,
               _session_index_entry({**head, "messages": []}, session_dir / f"session-{sid}.head.json"),
           )
           return latest_path

       payload = {
           "app": "ohmo",
           "session_id": sid,
           "session_key": session_key,
           "cwd": str(Path(cwd).resolve()),
           "model": model,
           "system_prompt": system_prompt,
           "messages": [message.model_dump(mode="json") for message in messages],
           "usage": usage.model_dump(),
           "tool_metadata": _persistable_tool_metadata(tool_metadata),
           "created_at": now,
           "summary": summary,
           "message_count": len(messages),
       }
       data = json.dumps(payload, indent=2) + "\n"
       latest_path = session_dir / "latest.json"
       atomic_write_text(latest_path, data)
       if session_key:
           atomic_write_text(_session_key_latest_path(workspace, session_key), data)
       session_path = session_dir / f"session-{sid}.json"
       atomic_write_text(session_path, data)
       _update_session_index(session_dir, _session_index_entry(payload, session_path))
       return latest_path
   ```
   Add the v2 payload assembler after `_update_session_index` (`ohmo/session_storage.py:89`):
   ```python
   def _load_ohmo_v2_payload(session_dir: Path, session_id: str) -> dict[str, Any] | None:
       head = session_format.read_head(session_dir, session_id)
       if head is None:
           return None
       raw_messages = session_format.load_v2_snapshot(session_dir, session_id)
       payload = dict(head)
       payload["messages"] = raw_messages
       payload.setdefault("system_prompt", "")
       return _sanitize_snapshot_payload(payload)
   ```
   Replace `load_latest` (`ohmo/session_storage.py:140-144`):
   ```python
   def load_latest(workspace: str | Path | None = None) -> dict[str, Any] | None:
       session_dir = get_session_dir(workspace)
       path = session_dir / "latest.json"
       if not path.exists():
           return None
       try:
           raw = json.loads(path.read_text(encoding="utf-8"))
       except (json.JSONDecodeError, OSError):
           return None
       if session_format.detect_latest_format(raw) == "v2":
           sid = str(raw.get("session_id") or "")
           return _load_ohmo_v2_payload(session_dir, sid) if sid else None
       return _sanitize_snapshot_payload(raw)
   ```
   Replace `load_latest_for_session_key` (`ohmo/session_storage.py:147-151`):
   ```python
   def load_latest_for_session_key(workspace: str | Path | None, session_key: str) -> dict[str, Any] | None:
       path = _session_key_latest_path(workspace, session_key)
       if not path.exists():
           return None
       try:
           raw = json.loads(path.read_text(encoding="utf-8"))
       except (json.JSONDecodeError, OSError):
           return None
       if session_format.detect_latest_format(raw) == "v2":
           sid = str(raw.get("session_id") or "")
           return _load_ohmo_v2_payload(get_session_dir(workspace), sid) if sid else None
       return _sanitize_snapshot_payload(raw)
   ```
   Replace `load_by_id` (`ohmo/session_storage.py:202-209`):
   ```python
   def load_by_id(workspace: str | Path | None, session_id: str) -> dict[str, Any] | None:
       session_dir = get_session_dir(workspace)
       fmt = session_format.detect_session_format(session_dir, session_id)
       if fmt == "v2":
           return _load_ohmo_v2_payload(session_dir, session_id)
       if fmt == "v1":
           path = session_dir / f"session-{session_id}.json"
           return _sanitize_snapshot_payload(json.loads(path.read_text(encoding="utf-8")))
       latest = load_latest(workspace)
       if latest and (latest.get("session_id") == session_id or session_id == "latest"):
           return latest
       return None
   ```
4. - [ ] Run, verify pass (new tests + the existing ohmo legacy/sanitize tests):
   ```bash
   python -m pytest tests/test_ohmo/test_ohmo_session_storage.py -q
   ```
   Expected: all passed.
5. - [ ] Commit:
   ```bash
   git add ohmo/session_storage.py tests/test_ohmo/test_ohmo_session_storage.py && git commit -m "Apply v2 head+append pattern to ohmo session storage"
   ```

### Task 16: ohmo crash-consistency + legacy fixture

**Files:**
- Test only: `tests/test_ohmo/test_ohmo_session_storage.py`

1. - [ ] Write the test. Add to `tests/test_ohmo/test_ohmo_session_storage.py`:
   ```python
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
   ```
2. - [ ] Run it, verify it passes:
   ```bash
   python -m pytest tests/test_ohmo/test_ohmo_session_storage.py -q
   ```
   Expected: all passed.
3. - [ ] Commit:
   ```bash
   git add tests/test_ohmo/test_ohmo_session_storage.py && git commit -m "Assert ohmo v2 crash recovery and legacy fixture load"
   ```

---

## Phase 6 — Full regression and proposal status sync

### Task 17: Full regression gate

**Files:** none (verification only).

1. - [ ] Run the complete persistence-touching surface plus the headless and CLI session paths (the loaders in `cli.py` / `ui/app.py` are unchanged, so these must pass without edits):
   ```bash
   python -m pytest tests/test_services/test_session_storage.py tests/test_services/test_session_format.py tests/test_services/test_conversation_index.py tests/test_ohmo/test_ohmo_session_storage.py tests/test_utils/test_fs.py tests/test_ui/test_headless_control.py tests/test_commands/test_cli.py -q
   ```
   Expected: all passed.
2. - [ ] Run the broader suite to catch any unexpected coupling:
   ```bash
   python -m pytest tests/test_services tests/test_ohmo tests/test_utils -q
   ```
   Expected: all passed.
3. - [ ] Manual smoke (optional, requires a real model — use the `harness-eval` skill if running it): start a session, send 3 messages, `oh --resume`, confirm context is intact and `~/.openharness/data/sessions/<project>/` contains a `.jsonl` + `.head.json` + pointer `latest.json`.

### Task 18: Update the proposal status

**Files:**
- Modify: `docs/proposals/performance-hardening-roadmap.md` (status block `performance-hardening-roadmap.md:7-14`)

1. - [ ] Update the status table row and the partial-implementation note to record WS4 as implemented behind the `session_storage_format=v2` flag, citing this plan. Change the line at `performance-hardening-roadmap.md:14` to note "WS4 (append-only session persistence + retention) shipped behind `session_storage_format=v2`; WS5 remains unscheduled," and adjust `performance-hardening-roadmap.md:7` Status if appropriate.
2. - [ ] Commit:
   ```bash
   git add docs/proposals/performance-hardening-roadmap.md && git commit -m "Mark WS4 session persistence v2 as implemented"
   ```

---

## Design decisions made here that the proposal left open

1. **Parent-dir fsync: fixed, not just documented.** Proposal said "fix or document" (`performance-hardening-roadmap.md:260`); this plan adds a best-effort directory fsync on durable writes (Task 2).
2. **Format detection from on-disk shape, not the setting.** Loaders sniff (Task 4) so a v1 file loads correctly even when the active format is `v2`; the setting gates *writes* only.
3. **Compaction signaled by message-count shrink.** v2 detects compaction as `last_persisted_count > len(messages)` and rewrites once with a marker line (Tasks 7–8). The proposal described a `compacted_at` marker but not the trigger; a shrink is the simplest robust signal given engine messages are append-only otherwise.
4. **System prompt: store sha256 + rely on rebuild inputs already in the head.** No new "rebuild inputs" field is added because `model` + `tool_metadata` (already persisted) are the inputs `build_runtime` uses; the full text is dropped (Task 5). Verified safe: no loader reads `system_prompt` back into a runtime.
5. **Retention runs on save, best-effort, oldest-first by `created_at`, protecting the active id and the `latest.json`-pointed id.** `0` disables each limit (Task 11).
6. **`latest.json` fallback narrowed.** The old `list_session_snapshots` treated `latest.json` as a pseudo-session; under v2 it is a pointer, so it is no longer listed separately (the pointed session is already in the index). The legacy full `latest.json` is still loaded by `load_session_snapshot` (Task 9).
7. **Index entry `path` points at the head file for v2** (`session-<id>.head.json`), at the `.json` for v1 — so the existing existence check (`session_storage.py:220`) and the new stale-compaction both work uniformly.

## Spec items and how each maps to a task

| WS4 sub-item (proposal) | Task(s) |
|---|---|
| (a) `session_storage_format` setting + format sniffer keeping legacy readable | 1, 4 |
| (b) append-only `.jsonl` + `.head.json`, delta append, compaction rewrite | 3, 6, 7, 8, 12 |
| (c) `latest.json` becomes a pointer | 8, 9 |
| (d) trust the index + one-time backfill + compact stale on write | 10 |
| (e) retention policy (max_files=50, max_age_days=30), oldest-first, never active/latest | 1, 11 |
| (f) fsync policy: 1 fsync/turn on transcript append; atomic-rename no per-write fsync for head/index; fix parent-dir fsync | 2, 3, 6, 8 |
| (g) single-pass resume load (drop validate→dump→re-validate) | 9 |
| (h) head+append pattern in `ohmo/session_storage.py` | 15, 16 |
| (i) stop persisting full built system prompt (hash + rebuild inputs) | 5, 8 |
| crash-consistency tests (truncate mid-append → recover to last complete line) | 3, 7, 13, 16 |
| legacy-format fixture tests (v1 files still load) | 10, 14, 16 |
| public dict shapes unchanged (no interface break) | 9, 14 |

## Assumptions left in the plan (clearly marked)

- **The `prior_head is not None and last_persisted == 0` edge clause** left in Task 8's `_save_session_snapshot_v2` is explicitly flagged inline as a no-op to be simplified in Task 12 step 3. It is the only deliberately-temporary code in the plan.
- **`watchdog.track` / `record` diagnostics calls** in the v2 save helper mirror the v1 ones verbatim; if the `watchdog` module's `track` signature has changed since `session_storage.py:152`, copy the current call exactly (it is lifted unmodified from the existing body, so it cannot drift unless the existing code does).

No other open assumptions: the settings-override mechanism (`OPENHARNESS_CONFIG_DIR`), the no-read-back of `system_prompt` on resume, and the public loader dict shapes were all verified against the current code while writing this plan.
