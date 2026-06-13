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

import hashlib
import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from openharness.engine.messages import ConversationMessage
from openharness.utils.fs import (
    append_jsonl_line,
    atomic_write_text,
    read_jsonl_complete_lines,
)


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
    """Classify a stored session by id, or ``None`` when no files exist.

    A ``session-<id>.head.json`` OR a ``session-<id>.jsonl`` transcript marks
    v2 — this covers V2_HEADLESS (transcript present, head lost) and makes v2
    win a v1+v2 CONFLICT (C.3). Only a lone ``session-<id>.json`` is v1.
    """
    head = (session_dir / f"session-{session_id}.head.json").exists()
    transcript = (session_dir / f"session-{session_id}.jsonl").exists()
    if head or transcript:
        return "v2"
    if (session_dir / f"session-{session_id}.json").exists():
        return "v1"
    return None


def system_prompt_fingerprint(system_prompt: str) -> str:
    """Return the sha256 hex digest of a built system prompt.

    v2 persists this digest instead of the full prompt text. The prompt is
    always rebuilt on resume from ``model`` + ``tool_metadata`` (the rebuild
    inputs already in the head), so the text itself is never needed on disk;
    the digest is kept only as a debugging signal for prompt drift.
    """
    return sha256(system_prompt.encode("utf-8")).hexdigest()


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
        # Typed dispatch (C.5, P2-003): a record is a compaction marker iff it
        # carries the marker key AND lacks "role". Message records always have
        # "role", so a message is never mistaken for a marker.
        if _COMPACTION_MARKER in obj and "role" not in obj:
            records.clear()  # drop pre-compaction history
            continue
        records.append(obj)
    return records


def transcript_live_count(session_dir: Path, session_id: str) -> int:
    """Count the live records durable in the transcript (post-last-marker).

    The crash-correct seed for the append cursor (C.4): it reflects what is
    actually fsync'd in the transcript, independent of the non-durable head.
    Called once per process per session — the writer then maintains the count
    in-process (Task 8), so it is not an O(n) per-save read.
    """
    return len(load_v2_snapshot(session_dir, session_id))


def fingerprint_messages(messages: list[ConversationMessage] | list[dict[str, Any]]) -> str:
    """Stable content fingerprint of an ordered message history (R-001 / C.5-trigger).

    Detects an in-place compaction that rewrites message *content* without
    changing the count: two histories fingerprint equal iff their ordered,
    canonicalized JSON content is identical. Accepts either ConversationMessage
    objects (the in-memory list) or already-serialized dicts (``load_v2_snapshot``'s
    output) and canonicalizes both the same way — a message and the dict it was
    persisted as (``json.loads`` of ``json.dumps(model_dump(mode="json"))``) round-trip
    to equal ``sort_keys`` JSON, so an in-memory prefix compares equal to the durable
    prefix it became. Pure; no I/O, no settings.
    """
    digest = hashlib.blake2b(digest_size=16)
    for message in messages:
        payload = message.model_dump(mode="json") if hasattr(message, "model_dump") else message
        digest.update(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\x1e")  # record separator: makes the hash order- and boundary-sensitive
    return digest.hexdigest()
