"""Session storage backend abstractions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage
from openharness.services import session_storage


class SessionBackend(Protocol):
    """Interface for persisting and restoring session state."""

    def get_session_dir(self, cwd: str | Path) -> Path:
        """Return the backing directory for session files."""

    def save_snapshot(
        self,
        *,
        cwd: str | Path,
        model: str,
        system_prompt: str,
        messages: list[ConversationMessage],
        usage: UsageSnapshot,
        session_id: str | None = None,
        tool_metadata: dict[str, object] | None = None,
    ) -> Path:
        """Persist a session snapshot and return its path."""

    def load_latest(self, cwd: str | Path) -> dict | None:
        """Load the latest session snapshot."""

    def list_snapshots(self, cwd: str | Path, limit: int = 20) -> list[dict]:
        """List recent snapshots."""

    def load_by_id(self, cwd: str | Path, session_id: str) -> dict | None:
        """Load a snapshot by ID."""

    def export_markdown(
        self,
        *,
        cwd: str | Path,
        messages: list[ConversationMessage],
    ) -> Path:
        """Export the current transcript as markdown."""

    def export_snapshot_json(self, *, cwd: str | Path, dest: Path) -> Path:
        """Write a full v1-shaped snapshot (loader-built, v2-aware) to ``dest``."""


@dataclass(frozen=True)
class OpenHarnessSessionBackend:
    """Default session backend backed by ``~/.openharness/data/sessions``."""

    def get_session_dir(self, cwd: str | Path) -> Path:
        return session_storage.get_project_session_dir(cwd)

    def save_snapshot(
        self,
        *,
        cwd: str | Path,
        model: str,
        system_prompt: str,
        messages: list[ConversationMessage],
        usage: UsageSnapshot,
        session_id: str | None = None,
        tool_metadata: dict[str, object] | None = None,
    ) -> Path:
        return session_storage.save_session_snapshot(
            cwd=cwd,
            model=model,
            system_prompt=system_prompt,
            messages=messages,
            usage=usage,
            session_id=session_id,
            tool_metadata=tool_metadata,
        )

    def load_latest(self, cwd: str | Path) -> dict | None:
        return session_storage.load_session_snapshot(cwd)

    def list_snapshots(self, cwd: str | Path, limit: int = 20) -> list[dict]:
        return session_storage.list_session_snapshots(cwd, limit=limit)

    def load_by_id(self, cwd: str | Path, session_id: str) -> dict | None:
        return session_storage.load_session_by_id(cwd, session_id)

    def export_markdown(
        self,
        *,
        cwd: str | Path,
        messages: list[ConversationMessage],
    ) -> Path:
        return session_storage.export_session_markdown(cwd=cwd, messages=messages)

    def export_snapshot_json(self, *, cwd: str | Path, dest: Path) -> Path:
        """Write a full v1-shaped snapshot (loader-built, v2-aware) to ``dest``.

        Resolves the just-saved session through the v2-aware loader, so a tag
        export is a real full snapshot under both formats — never the v2
        pointer ``latest.json`` (PMR-001).
        """
        payload = session_storage.load_session_snapshot(cwd)
        if payload is None:
            raise FileNotFoundError("no session to export")
        dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return dest


DEFAULT_SESSION_BACKEND: SessionBackend = OpenHarnessSessionBackend()
