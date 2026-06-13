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

from hashlib import sha256
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
