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
    entries = sorted(entries, key=lambda item: item.get("created_at", 0), reverse=True)
    atomic_write_text(
        _session_index_path(session_dir),
        json.dumps({"version": 1, "sessions": entries}, indent=2) + "\n",
        fsync=False,
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
    session_dir = get_project_session_dir(cwd)
    sid = session_id or uuid4().hex[:12]
    now = time.time()
    messages = sanitize_conversation_messages(messages)
    # Extract a summary from the first user message
    summary = ""
    for msg in messages:
        if msg.role == "user" and msg.text.strip():
            summary = msg.text.strip()[:80]
            break

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

    # Save as latest
    latest_path = session_dir / "latest.json"
    # Per-line state cache: rename-atomic, no fsync (see atomic_write_bytes).
    atomic_write_text(latest_path, data, fsync=False)

    # Save by session ID
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
    """Normalize persisted messages for forward compatibility."""
    raw_messages = payload.get("messages", [])
    if isinstance(raw_messages, list):
        messages = sanitize_conversation_messages(
            [ConversationMessage.model_validate(item) for item in raw_messages]
        )
        payload = dict(payload)
        payload["messages"] = [message.model_dump(mode="json") for message in messages]
        payload["message_count"] = len(messages)
    return payload


def load_session_snapshot(cwd: str | Path) -> dict[str, Any] | None:
    """Load the most recent session snapshot for the project."""
    path = get_project_session_dir(cwd) / "latest.json"
    if not path.exists():
        return None
    return _sanitize_snapshot_payload(json.loads(path.read_text(encoding="utf-8")))


def list_session_snapshots(cwd: str | Path, limit: int = 20) -> list[dict[str, Any]]:
    """List saved sessions for the project, newest first."""
    session_dir = get_project_session_dir(cwd)
    sessions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    indexed = _load_session_index(session_dir)
    if indexed:
        for item in indexed:
            if not (session_dir / str(item.get("path") or "")).exists():
                continue
            sid = str(item.get("session_id") or "")
            if sid:
                seen_ids.add(sid)
            sessions.append(
                {
                    "session_id": item.get("session_id", ""),
                    "summary": item.get("summary", ""),
                    "message_count": item.get("message_count", 0),
                    "model": item.get("model", ""),
                    "created_at": item.get("created_at", 0),
                }
            )
        if len(sessions) >= limit:
            sessions.sort(key=lambda item: item.get("created_at", 0), reverse=True)
            return sessions[:limit]

    # Named session files
    for path in sorted(session_dir.glob("session-*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            sid = data.get("session_id", path.stem.replace("session-", ""))
            if sid in seen_ids:
                continue
            seen_ids.add(sid)
            summary = data.get("summary", "")
            if not summary:
                # Extract from first user message
                for msg in data.get("messages", []):
                    if msg.get("role") == "user":
                        texts = [b.get("text", "") for b in msg.get("content", []) if b.get("type") == "text"]
                        summary = " ".join(texts).strip()[:80]
                        if summary:
                            break
            sessions.append({
                "session_id": sid,
                "summary": summary,
                "message_count": data.get("message_count", len(data.get("messages", []))),
                "model": data.get("model", ""),
                "created_at": data.get("created_at", path.stat().st_mtime),
            })
        except (json.JSONDecodeError, OSError):
            continue
        if len(sessions) >= limit:
            break

    # Also include latest.json if it has no corresponding session file
    latest_path = session_dir / "latest.json"
    if latest_path.exists() and len(sessions) < limit:
        try:
            data = json.loads(latest_path.read_text(encoding="utf-8"))
            sid = data.get("session_id", "latest")
            if sid not in seen_ids:
                summary = data.get("summary", "")
                if not summary:
                    for msg in data.get("messages", []):
                        if msg.get("role") == "user":
                            texts = [b.get("text", "") for b in msg.get("content", []) if b.get("type") == "text"]
                            summary = " ".join(texts).strip()[:80]
                            if summary:
                                break
                sessions.append({
                    "session_id": sid,
                    "summary": summary or "(latest session)",
                    "message_count": data.get("message_count", len(data.get("messages", []))),
                    "model": data.get("model", ""),
                    "created_at": data.get("created_at", latest_path.stat().st_mtime),
                })
        except (json.JSONDecodeError, OSError):
            pass

    # Sort by created_at descending
    sessions.sort(key=lambda s: s.get("created_at", 0), reverse=True)
    return sessions[:limit]


def load_session_by_id(cwd: str | Path, session_id: str) -> dict[str, Any] | None:
    """Load a specific session by ID."""
    session_dir = get_project_session_dir(cwd)
    # Try named session first
    path = session_dir / f"session-{session_id}.json"
    if path.exists():
        return _sanitize_snapshot_payload(json.loads(path.read_text(encoding="utf-8")))
    # Fallback to latest.json if session_id matches
    latest = session_dir / "latest.json"
    if latest.exists():
        data = _sanitize_snapshot_payload(json.loads(latest.read_text(encoding="utf-8")))
        if data.get("session_id") == session_id or session_id == "latest":
            return data
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
