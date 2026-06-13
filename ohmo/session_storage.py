"""Session persistence for ``ohmo``."""

from __future__ import annotations

import json
import hashlib
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, sanitize_conversation_messages
from openharness.services import session_format
from openharness.services.session_backend import SessionBackend
from openharness.services.session_storage import (
    _persistable_tool_metadata,
    _sanitize_snapshot_payload,
    session_ids_on_disk,
)
from openharness.utils.file_lock import exclusive_file_lock
from openharness.utils.fs import atomic_write_text

from ohmo.workspace import get_sessions_dir


def get_session_dir(workspace: str | Path | None = None) -> Path:
    """Return the ohmo sessions directory."""
    session_dir = get_sessions_dir(workspace)
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def _session_key_token(session_key: str) -> str:
    return hashlib.sha1(session_key.encode("utf-8")).hexdigest()[:12]


def _session_key_latest_path(workspace: str | Path | None, session_key: str) -> Path:
    session_dir = get_session_dir(workspace)
    token = _session_key_token(session_key)
    return session_dir / f"latest-{token}.json"


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
        "session_key": payload.get("session_key"),
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
    entries = sorted(entries, key=lambda item: item.get("created_at", 0), reverse=True)
    atomic_write_text(
        _session_index_path(session_dir),
        json.dumps({"version": 1, "sessions": entries}, indent=2) + "\n",
    )


def _update_session_index(session_dir: Path, entry: dict[str, Any]) -> None:
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


# In-process per-session persistence state, mirroring openharness (C.4 /
# C.5-trigger): the crash-correct cursor source that replaces head.message_count,
# plus the durable-prefix content fingerprint that detects in-place compaction
# (R-001). Process-local, LRU-bounded (R-004); an evicted session re-seeds from
# its transcript on the next save.
_V2_CURSOR_CACHE_MAX = 1024
_v2_persisted_count: dict[tuple[str, str], int] = {}
_v2_persisted_prefix_fp: dict[tuple[str, str], str] = {}


def _v2_remember_persisted(key: tuple[str, str], count: int, prefix_fp: str) -> None:
    """Record the durable (count, fingerprint) for a session, bounding the cache (R-004)."""
    _v2_persisted_count.pop(key, None)
    _v2_persisted_prefix_fp.pop(key, None)
    _v2_persisted_count[key] = count
    _v2_persisted_prefix_fp[key] = prefix_fp
    while len(_v2_persisted_count) > _V2_CURSOR_CACHE_MAX:
        oldest = next(iter(_v2_persisted_count))
        _v2_persisted_count.pop(oldest, None)
        _v2_persisted_prefix_fp.pop(oldest, None)


def _load_ohmo_v2_payload(
    session_dir: Path, session_id: str, *, session_key: str | None = None
) -> dict[str, Any] | None:
    # Mirror openharness V2_HEADLESS recovery (C.6 / R-002a): if the head was lost
    # in a crash but the transcript is durable, resume STILL recovers history off
    # the transcript (the original code returned None here, losing the whole ohmo
    # session — the "twin in lockstep" gap). Returns None only when BOTH are absent.
    head = session_format.read_head(session_dir, session_id)
    raw_messages = session_format.load_v2_snapshot(session_dir, session_id)
    if head is None and not raw_messages:
        return None
    if head is not None:
        payload = dict(head)
    else:
        # Head-less degradation contract (C.6): history is preserved; head-only
        # fields fall back to runtime defaults. `app` is the constant "ohmo";
        # `session_key` was head-only, so it is re-injected by the session-key
        # lookup path (it knows the key) and is otherwise absent.
        payload = {"app": "ohmo", "session_id": session_id, "message_count": len(raw_messages)}
    payload["messages"] = raw_messages
    payload.setdefault("system_prompt", "")
    if session_key and not payload.get("session_key"):
        payload["session_key"] = session_key
    return _sanitize_snapshot_payload(payload)


def _load_ohmo_snapshot_in_dir(session_dir: Path, session_id: str) -> dict[str, Any] | None:
    """Dir-based ohmo loader (sniffer → v2/v1), shared by load_by_id and listing."""
    fmt = session_format.detect_session_format(session_dir, session_id)
    if fmt == "v2":
        return _load_ohmo_v2_payload(session_dir, session_id)
    if fmt == "v1":
        path = session_dir / f"session-{session_id}.json"
        return _sanitize_snapshot_payload(json.loads(path.read_text(encoding="utf-8")))
    return None


def save_session_snapshot(
    *,
    cwd: str | Path,
    workspace: str | Path | None = None,
    model: str,
    system_prompt: str,
    messages: list[ConversationMessage],
    usage: UsageSnapshot,
    session_id: str | None = None,
    session_key: str | None = None,
    tool_metadata: dict[str, object] | None = None,
) -> Path:
    """Persist the latest ohmo session snapshot."""
    session_dir = get_session_dir(workspace)
    sid = session_id or uuid4().hex[:12]
    now = time.time()
    messages = sanitize_conversation_messages(messages)
    summary = ""
    for msg in messages:
        if msg.role == "user" and msg.text.strip():
            summary = msg.text.strip()[:80]
            break

    from openharness.config import load_settings

    fmt = load_settings().session_storage_format
    if fmt == "v2":
        # Cursor + fingerprint from the durable transcript, not the non-fsync'd
        # head (C.4 / C.5-trigger / P1-001); seeded once, maintained in-process
        # (single writer per id — C.2). Mirrors openharness exactly (R-001).
        prior_head = session_format.read_head(session_dir, sid)
        created_at = prior_head.get("created_at", now) if prior_head else now
        key = (str(session_dir), sid)
        last_persisted = _v2_persisted_count.get(key)
        persisted_fp = _v2_persisted_prefix_fp.get(key)
        if last_persisted is None or persisted_fp is None:
            durable = session_format.load_v2_snapshot(session_dir, sid)
            last_persisted = len(durable)
            persisted_fp = session_format.fingerprint_messages(durable)
        # In-place compaction keeps the count but rewrites content (R-001) — detect
        # via the durable-prefix content fingerprint, not a count shrink.
        compacted = (
            len(messages) < last_persisted
            or session_format.fingerprint_messages(messages[:last_persisted]) != persisted_fp
        )
        if compacted:
            session_format.rewrite_transcript(session_dir, sid, messages)
        else:
            session_format.append_messages_to_transcript(
                session_dir, sid, messages, last_persisted_count=last_persisted
            )
        # transcript durable; maintain cursor + fingerprint (C.4), bounded (R-004)
        _v2_remember_persisted(
            key, len(messages), session_format.fingerprint_messages(messages)
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
        # Pointer is derived/rename-only per C.1 (the transcript, fsynced once by
        # the append/rewrite primitives above, is the durable artifact).
        pointer = json.dumps({"session_id": sid}) + "\n"
        latest_path = session_dir / "latest.json"
        atomic_write_text(latest_path, pointer, fsync=False)
        if session_key:
            atomic_write_text(_session_key_latest_path(workspace, session_key), pointer, fsync=False)
        # Store-wide index read-modify-write under the same lock as openharness
        # (C.2); ohmo has no retention, so the index is its only locked store write.
        with exclusive_file_lock(session_dir / ".sessions.lock"):
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
    # ohmo's index is multi-writer too (many chat channels); serialise its RMW
    # under the same store lock (C.2). ohmo has no retention, so a direct
    # call-site lock is sufficient (no core/wrapper split needed).
    with exclusive_file_lock(session_dir / ".sessions.lock"):
        _update_session_index(session_dir, _session_index_entry(payload, session_path))
    return latest_path


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
        # Pass session_key so a head-less recovery (R-002a) still carries it — the
        # lookup knows the key even when the crashed-away head did not survive.
        return _load_ohmo_v2_payload(get_session_dir(workspace), sid, session_key=session_key) if sid else None
    return _sanitize_snapshot_payload(raw)


def list_snapshots(workspace: str | Path | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """List ohmo sessions, newest first.

    Mirrors openharness (PMR-002): trusts the index and surfaces any on-disk
    session it is missing — v1 ``session-*.json`` AND v2 head/transcript (incl.
    head-less) — via the shared sniffer + the v2-aware loader, persisting the
    merged index once under the store lock (C.7). Subsequent lists are index-only.
    """
    session_dir = get_session_dir(workspace)
    by_id: dict[str, dict[str, Any]] = {}
    for entry in _load_session_index(session_dir):
        sid = str(entry.get("session_id") or "")
        if sid:
            by_id[sid] = entry
    derived_any = False
    for sid in session_ids_on_disk(session_dir):
        if sid in by_id:
            continue
        payload = _load_ohmo_snapshot_in_dir(session_dir, sid)
        if payload is None:
            continue
        fmt = session_format.detect_session_format(session_dir, sid)
        sp = session_dir / (f"session-{sid}.head.json" if fmt == "v2" else f"session-{sid}.json")
        by_id[sid] = _session_index_entry(payload, sp)
        derived_any = True
    if derived_any:
        with exclusive_file_lock(session_dir / ".sessions.lock"):
            _write_session_index(session_dir, list(by_id.values()))
    sessions: list[dict[str, Any]] = []
    for sid, item in by_id.items():
        if session_format.detect_session_format(session_dir, sid) is None:
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


def load_by_id(workspace: str | Path | None, session_id: str) -> dict[str, Any] | None:
    session_dir = get_session_dir(workspace)
    snap = _load_ohmo_snapshot_in_dir(session_dir, session_id)
    if snap is not None:
        return snap
    latest = load_latest(workspace)
    if latest and (latest.get("session_id") == session_id or session_id == "latest"):
        return latest
    return None


def export_session_markdown(
    *,
    cwd: str | Path,
    workspace: str | Path | None = None,
    messages: list[ConversationMessage],
) -> Path:
    path = get_session_dir(workspace) / "transcript.md"
    parts = ["# ohmo Session Transcript"]
    for message in messages:
        parts.append(f"\n## {message.role.capitalize()}\n")
        text = message.text.strip()
        if text:
            parts.append(text)
    atomic_write_text(path, "\n".join(parts).strip() + "\n")
    return path


class OhmoSessionBackend(SessionBackend):
    """Session backend rooted in ``.ohmo/sessions``."""

    def __init__(self, workspace: str | Path | None = None) -> None:
        self._workspace = workspace

    def get_session_dir(self, cwd: str | Path) -> Path:
        return get_session_dir(self._workspace)

    def save_snapshot(
        self,
        *,
        cwd: str | Path,
        model: str,
        system_prompt: str,
        messages: list[ConversationMessage],
        usage: UsageSnapshot,
        session_id: str | None = None,
        session_key: str | None = None,
        tool_metadata: dict[str, object] | None = None,
    ) -> Path:
        return save_session_snapshot(
            cwd=cwd,
            workspace=self._workspace,
            model=model,
            system_prompt=system_prompt,
            messages=messages,
            usage=usage,
            session_id=session_id,
            session_key=session_key,
            tool_metadata=tool_metadata,
        )

    def load_latest(self, cwd: str | Path) -> dict[str, Any] | None:
        return load_latest(self._workspace)

    def list_snapshots(self, cwd: str | Path, limit: int = 20) -> list[dict[str, Any]]:
        return list_snapshots(self._workspace, limit=limit)

    def load_by_id(self, cwd: str | Path, session_id: str) -> dict[str, Any] | None:
        return load_by_id(self._workspace, session_id)

    def load_latest_for_session_key(self, session_key: str) -> dict[str, Any] | None:
        return load_latest_for_session_key(self._workspace, session_key)

    def export_markdown(
        self,
        *,
        cwd: str | Path,
        messages: list[ConversationMessage],
    ) -> Path:
        return export_session_markdown(cwd=cwd, workspace=self._workspace, messages=messages)
