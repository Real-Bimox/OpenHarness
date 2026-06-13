"""Session persistence helpers."""

from __future__ import annotations

import json
import time
from hashlib import sha1
from pathlib import Path
from typing import Any
from uuid import uuid4

from openharness.api.usage import UsageSnapshot
from openharness.config.paths import get_sessions_dir
from openharness.engine.messages import ConversationMessage, sanitize_conversation_messages
from openharness.services import session_format
from openharness.utils.file_lock import exclusive_file_lock
from openharness.utils.fs import atomic_write_text


_PERSISTED_TOOL_METADATA_KEYS = (
    "permission_mode",
    "read_file_state",
    "invoked_skills",
    "async_agent_state",
    "async_agent_tasks",
    "recent_work_log",
    "recent_verified_work",
    "task_focus_state",
    "compact_checkpoints",
    "compact_last",
)


def _sanitize_metadata(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _sanitize_metadata(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_metadata(item) for item in value]
    return str(value)


def _persistable_tool_metadata(tool_metadata: dict[str, object] | None) -> dict[str, Any]:
    if not isinstance(tool_metadata, dict):
        return {}
    payload: dict[str, Any] = {}
    for key in _PERSISTED_TOOL_METADATA_KEYS:
        if key in tool_metadata:
            payload[key] = _sanitize_metadata(tool_metadata[key])
    return payload


def get_project_session_dir(cwd: str | Path) -> Path:
    """Return the session directory for a project."""
    path = Path(cwd).resolve()
    digest = sha1(str(path).encode("utf-8")).hexdigest()[:12]
    session_dir = get_sessions_dir() / f"{path.name}-{digest}"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def _session_index_path(session_dir: Path) -> Path:
    return session_dir / "sessions-index.json"


def _session_index_entry(payload: dict[str, Any], session_path: Path) -> dict[str, Any]:
    return {
        "session_id": payload.get("session_id", session_path.stem.replace("session-", "")),
        "summary": payload.get("summary", ""),
        "message_count": payload.get("message_count", len(payload.get("messages", []))),
        "model": payload.get("model", ""),
        "created_at": payload.get("created_at", session_path.stat().st_mtime if session_path.exists() else time.time()),
        "path": session_path.name,
    }


def _load_session_index(session_dir: Path) -> list[dict[str, Any]]:
    path = _session_index_path(session_dir)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    entries = payload.get("sessions") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _write_session_index(session_dir: Path, entries: list[dict[str, Any]]) -> None:
    # Stale-compaction: keep an entry only if its session still exists. Use the
    # sniffer, not the head-file path, so a V2_HEADLESS session (head lost in a
    # crash, transcript still present — C.3) is NOT dropped. Caller holds the
    # store lock (this is part of a read-modify-write).
    live = [
        entry
        for entry in entries
        if session_format.detect_session_format(session_dir, str(entry.get("session_id") or "")) is not None
    ]
    live = sorted(live, key=lambda item: item.get("created_at", 0), reverse=True)
    atomic_write_text(
        _session_index_path(session_dir),
        json.dumps({"version": 1, "sessions": live}, indent=2) + "\n",
        fsync=False,
    )


def _update_session_index_unlocked(session_dir: Path, entry: dict[str, Any]) -> None:
    # The read-modify-write core — NO lock; the caller must hold
    # session_dir / ".sessions.lock".
    session_id = str(entry.get("session_id") or "")
    if not session_id:
        return
    entries = [
        existing
        for existing in _load_session_index(session_dir)
        if str(existing.get("session_id") or "") != session_id
    ]
    entries.append(entry)
    _write_session_index(session_dir, entries)


def _update_session_index(session_dir: Path, entry: dict[str, Any]) -> None:
    # Locking wrapper for callers not already under the store lock (e.g. v1).
    with exclusive_file_lock(session_dir / ".sessions.lock"):
        _update_session_index_unlocked(session_dir, entry)


# In-process per-session persistence state, keyed by (session_dir, session_id):
#   _v2_persisted_count     -> count of live records the owning writer has
#                              persisted (the crash-correct append cursor —
#                              C.4 / P1-001; seeded from the durable transcript,
#                              NEVER from the non-durable head).
#   _v2_persisted_prefix_fp -> content fingerprint of those same persisted live
#                              records, used to detect an *in-place* compaction
#                              that rewrites content WITHOUT changing the count
#                              (C.5-trigger / R-001). Always written together
#                              with the count, via _v2_remember_persisted.
# Process-local: a crash discards both and the next process re-seeds from the
# transcript. Bounded (R-004): a long-lived foreground process that resumes many
# sessions evicts the oldest entry past _V2_CURSOR_CACHE_MAX; an evicted session
# simply re-seeds (count + fp) from its transcript on its next save — correct,
# one extra read.
_V2_CURSOR_CACHE_MAX = 1024
_v2_persisted_count: dict[tuple[str, str], int] = {}
_v2_persisted_prefix_fp: dict[tuple[str, str], str] = {}


def _v2_remember_persisted(key: tuple[str, str], count: int, prefix_fp: str) -> None:
    """Record the durable (count, fingerprint) for a session, bounding the cache.

    Re-inserts the key at the most-recent position (write-LRU) and evicts the
    oldest entries past the cap; eviction is safe because the next save for an
    evicted id re-seeds from the transcript (the cold-seed path).
    """
    _v2_persisted_count.pop(key, None)
    _v2_persisted_prefix_fp.pop(key, None)
    _v2_persisted_count[key] = count
    _v2_persisted_prefix_fp[key] = prefix_fp
    while len(_v2_persisted_count) > _V2_CURSOR_CACHE_MAX:
        oldest = next(iter(_v2_persisted_count))
        _v2_persisted_count.pop(oldest, None)
        _v2_persisted_prefix_fp.pop(oldest, None)


def session_ids_on_disk(session_dir: Path) -> list[str]:
    """v1 + v2 session ids present on disk (deduped; v2 and v1 share the id space).

    Shared enumerator for listing (PMR-002) and conversation-index rebuild
    (PMR-003). ``glob("session-*.json")`` also matches ``session-<id>.head.json``,
    so derive v1 ids precisely by skipping the head files.
    """
    ids: dict[str, None] = {}  # insertion-ordered set
    for p in session_dir.glob("session-*.head.json"):      # v2 head
        ids.setdefault(p.name[len("session-"):-len(".head.json")], None)
    for p in session_dir.glob("session-*.jsonl"):           # v2 transcript (headless too)
        ids.setdefault(p.stem[len("session-"):], None)
    for p in session_dir.glob("session-*.json"):            # v1 — but this ALSO matches *.head.json
        if p.name.endswith(".head.json"):
            continue
        ids.setdefault(p.stem[len("session-"):], None)
    return list(ids)


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
        if sid in seen:  # a v2 head already claimed this id — v2 wins (C.3 / C.7)
            continue
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        seen.add(sid)
        entries.append(_session_index_entry(data, json_file))
    if entries:
        # Write the whole index once under the store lock (C.7): atomic rename
        # means a reader sees the old or the new index, never a torn one; a
        # crash mid-backfill leaves the prior state and the next trigger re-runs.
        with exclusive_file_lock(session_dir / ".sessions.lock"):
            _write_session_index(session_dir, entries)
    return entries


def save_session_snapshot(
    *,
    cwd: str | Path,
    model: str,
    system_prompt: str,
    messages: list[ConversationMessage],
    usage: UsageSnapshot,
    session_id: str | None = None,
    tool_metadata: dict[str, object] | None = None,
) -> Path:
    """Persist a session snapshot. Saves both by ID and as latest."""
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
    created_at = prior_head.get("created_at", now) if prior_head else now
    # Cursor + fingerprint invariant (C.4 / C.5-trigger): the append cursor is
    # the count of live records already durable in the transcript — NOT
    # head.message_count, which is rename-written (no fsync) after the fsync'd
    # transcript and so can be lost in a crash (P1-001). Alongside it we keep a
    # content fingerprint of that durable prefix. Both are seeded once from the
    # transcript (a single load_v2_snapshot read — same I/O the count seed used)
    # and then maintained in-process (single writer per C.2).
    key = (str(session_dir), sid)
    last_persisted = _v2_persisted_count.get(key)
    persisted_fp = _v2_persisted_prefix_fp.get(key)
    if last_persisted is None or persisted_fp is None:
        durable = session_format.load_v2_snapshot(session_dir, sid)
        last_persisted = len(durable)
        persisted_fp = session_format.fingerprint_messages(durable)
    # R-001: count alone is a LOSSY proxy for "was the history compacted". The
    # engine compacts IN PLACE — microcompact clears old tool-result bodies and
    # context-collapse shrinks text, both rewriting message *content* while the
    # count stays the same (verified: compact/__init__.py:854 / :348). A count
    # test would take the append path and leave stale content on disk. Compare
    # the durable prefix's content fingerprint instead: the prefix is stale iff
    # the in-memory prefix no longer matches what we persisted, OR the history
    # shrank. Either way rewrite the transcript in full (C.5 marker + full
    # history); otherwise append only the delta (C.3).
    compacted = (
        len(messages) < last_persisted
        or session_format.fingerprint_messages(messages[:last_persisted]) != persisted_fp
    )

    with watchdog.track("snapshot_write", session_id=sid):
        if compacted:
            session_format.rewrite_transcript(session_dir, sid, messages)
        else:
            session_format.append_messages_to_transcript(
                session_dir, sid, messages, last_persisted_count=last_persisted
            )
        # Transcript is now durable; record the new live count AND the fingerprint
        # of the now-persisted history (the whole list, after either path) before
        # the derived head/pointer/index writes (C.4 matrix). Bounded cache (R-004).
        _v2_remember_persisted(
            key, len(messages), session_format.fingerprint_messages(messages)
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
        # Store-wide critical section (C.2): the index read-modify-write — and,
        # added in Task 11, the retention prune — run under ONE acquisition of
        # the store lock. Call the *_unlocked core, never the locking
        # _update_session_index (flock is per-open-description → a second
        # acquire in this process self-deadlocks).
        with exclusive_file_lock(session_dir / ".sessions.lock"):
            _update_session_index_unlocked(
                session_dir,
                _session_index_entry(index_payload, session_dir / f"session-{sid}.head.json"),
            )
            # Task 11 inserts the retention prune here, inside this same lock.
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


def _update_conversation_index(payload: dict[str, Any]) -> None:
    """Feed the derived search index; never let it break a save."""
    try:
        from openharness.config import load_settings

        if not load_settings().conversation_index_enabled:
            return
        from openharness.services.conversation_index import index_snapshot_best_effort

        index_snapshot_best_effort(payload)
    except Exception:
        pass


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


def _load_v2_payload(session_dir: Path, session_id: str) -> dict[str, Any] | None:
    """Reassemble a v1-shaped snapshot dict from a v2 head + transcript.

    Handles V2_HEADLESS (C.3 / C.6): if the head was lost in a crash but the
    transcript is durable, resume still works off the transcript and the head
    is rebuilt on the next save. Returns None only when BOTH are absent.
    """
    head = session_format.read_head(session_dir, session_id)
    raw_messages = session_format.load_v2_snapshot(session_dir, session_id)
    if head is None and not raw_messages:
        return None
    # Head-less branch: history is preserved; head-only fields (model, usage,
    # tool_metadata) are deliberately omitted and degrade per the C.6 contract
    # (R-002) — model falls back to the runtime default, NOT null.
    payload = dict(head) if head is not None else {
        "session_id": session_id,
        "message_count": len(raw_messages),
    }
    payload["messages"] = raw_messages
    # system_prompt is rebuilt by build_runtime; loaders never read it back.
    payload.setdefault("system_prompt", "")
    return _sanitize_snapshot_payload(payload)


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


def list_session_snapshots(cwd: str | Path, limit: int = 20) -> list[dict[str, Any]]:
    """List saved sessions for the project, newest first.

    Trusts the index whenever it exists, builds it once via a backfill when
    absent, and surfaces any on-disk session a present-but-incomplete index is
    still missing — v1 ``session-*.json`` AND v2 head/transcript (incl. a
    head-less transcript) — via the sniffer + loader, persisting the merged
    index once under the store lock (C.7 lazy one-time backfill, all-or-nothing).
    Subsequent lists are index-only.
    """
    session_dir = get_project_session_dir(cwd)
    indexed = _load_session_index(session_dir)
    if not indexed:
        indexed = _backfill_index(session_dir)
    by_id: dict[str, dict[str, Any]] = {}
    for entry in indexed:
        sid = str(entry.get("session_id") or "")
        if sid:
            by_id[sid] = entry
    # Surface on-disk sessions a present-but-incomplete index is missing
    # (PMR-002). Derive each via the v2-aware loader core so a head-less v2
    # session (transcript present, head lost — C.6) still surfaces, degraded.
    derived_any = False
    for sid in session_ids_on_disk(session_dir):
        if sid in by_id:
            continue
        payload = _load_snapshot_in_dir(session_dir, sid)
        if payload is None:
            continue
        fmt = session_format.detect_session_format(session_dir, sid)
        sp = session_dir / (f"session-{sid}.head.json" if fmt == "v2" else f"session-{sid}.json")
        by_id[sid] = _session_index_entry(payload, sp)
        derived_any = True
    if derived_any:
        # C.7: persist the merged index ONCE under .sessions.lock (atomic, not
        # per-entry, not merge-on-read), so the next listing is index-only.
        with exclusive_file_lock(session_dir / ".sessions.lock"):
            _write_session_index(session_dir, list(by_id.values()))
    sessions: list[dict[str, Any]] = []
    for sid, item in by_id.items():
        if session_format.detect_session_format(session_dir, sid) is None:
            continue  # session truly gone (V2_HEADLESS counts as live — C.3)
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


def _load_snapshot_in_dir(session_dir: Path, session_id: str) -> dict[str, Any] | None:
    """Load a session by id from a known session dir (sniffer → v2/v1).

    The dir-based core shared by ``load_session_by_id`` and the conversation
    index rebuild (PMR-003). Returns None when neither a v2 nor a v1 session
    exists for the id.
    """
    fmt = session_format.detect_session_format(session_dir, session_id)
    if fmt == "v2":
        return _load_v2_payload(session_dir, session_id)
    if fmt == "v1":
        path = session_dir / f"session-{session_id}.json"
        return _sanitize_snapshot_payload(json.loads(path.read_text(encoding="utf-8")))
    return None


def load_session_by_id(cwd: str | Path, session_id: str) -> dict[str, Any] | None:
    """Load a specific session by ID."""
    session_dir = get_project_session_dir(cwd)
    snap = _load_snapshot_in_dir(session_dir, session_id)
    if snap is not None:
        return snap
    # Fallback to latest.json if it resolves to this id.
    snap = load_session_snapshot(cwd)
    if snap is not None and (snap.get("session_id") == session_id or session_id == "latest"):
        return snap
    return None


def export_session_markdown(
    *,
    cwd: str | Path,
    messages: list[ConversationMessage],
) -> Path:
    """Export the session transcript as Markdown."""
    session_dir = get_project_session_dir(cwd)
    path = session_dir / "transcript.md"
    parts: list[str] = ["# OpenHarness Session Transcript"]
    for message in messages:
        parts.append(f"\n## {message.role.capitalize()}\n")
        text = message.text.strip()
        if text:
            parts.append(text)
        for block in message.tool_uses:
            parts.append(f"\n```tool\n{block.name} {json.dumps(block.input, ensure_ascii=True)}\n```")
        for block in message.content:
            if getattr(block, "type", "") == "tool_result":
                parts.append(f"\n```tool-result\n{block.content}\n```")
    atomic_write_text(path, "\n".join(parts).strip() + "\n")
    return path
