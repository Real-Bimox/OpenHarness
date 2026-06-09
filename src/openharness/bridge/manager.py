"""Track spawned bridge sessions for UI and commands."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from openharness.config.paths import get_data_dir
from openharness.bridge.session_runner import SessionHandle, spawn_session

_MAX_BRIDGE_LOG_BYTES = 1_000_000
_MAX_COMPLETED_BRIDGE_SESSIONS = 100
_BRIDGE_TRUNCATION_MARKER = b"[OpenHarness] Earlier bridge output truncated.\n"


@dataclass(frozen=True)
class BridgeSessionRecord:
    """UI-safe bridge session snapshot."""

    session_id: str
    command: str
    cwd: str
    pid: int
    status: str
    started_at: float
    output_path: str


class BridgeSessionManager:
    """Manage bridge-run child sessions and capture their output."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionHandle] = {}
        self._commands: dict[str, str] = {}
        self._output_paths: dict[str, Path] = {}
        self._copy_tasks: dict[str, asyncio.Task[None]] = {}

    async def spawn(self, *, session_id: str, command: str, cwd: str | Path) -> SessionHandle:
        handle = await spawn_session(session_id=session_id, command=command, cwd=cwd)
        self._sessions[session_id] = handle
        self._commands[session_id] = command
        output_dir = get_data_dir() / "bridge"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{session_id}.log"
        output_path.write_text("", encoding="utf-8")
        self._output_paths[session_id] = output_path
        self._copy_tasks[session_id] = asyncio.create_task(self._copy_output(session_id, handle))
        return handle

    def list_sessions(self) -> list[BridgeSessionRecord]:
        items: list[BridgeSessionRecord] = []
        for session_id, handle in self._sessions.items():
            process = handle.process
            if process.returncode is None:
                status = "running"
            elif process.returncode == 0:
                status = "completed"
            else:
                status = "failed"
            items.append(
                BridgeSessionRecord(
                    session_id=session_id,
                    command=self._commands.get(session_id, ""),
                    cwd=str(handle.cwd),
                    pid=process.pid or 0,
                    status=status,
                    started_at=handle.started_at,
                    output_path=str(self._output_paths[session_id]),
                )
            )
        return sorted(items, key=lambda item: item.started_at, reverse=True)

    def read_output(self, session_id: str, *, max_bytes: int = 12000) -> str:
        path = self._output_paths.get(session_id)
        if path is None or not path.exists():
            return ""
        return _read_tail_text(path, max_bytes=max_bytes)

    async def stop(self, session_id: str) -> None:
        handle = self._sessions.get(session_id)
        if handle is None:
            raise ValueError(f"Unknown bridge session: {session_id}")
        await handle.kill()
        copy_task = self._copy_tasks.get(session_id)
        if copy_task is not None:
            try:
                await asyncio.wait_for(copy_task, timeout=2.0)
            except asyncio.TimeoutError:
                copy_task.cancel()

    async def _copy_output(self, session_id: str, handle: SessionHandle) -> None:
        path = self._output_paths[session_id]
        stream = path.open("ab")
        try:
            if handle.process.stdout is not None:
                while True:
                    chunk = await handle.process.stdout.read(4096)
                    if not chunk:
                        break
                    stream.write(chunk)
                    stream.flush()
                    if stream.tell() > _MAX_BRIDGE_LOG_BYTES:
                        stream.close()
                        _cap_output_file(path, max_bytes=_MAX_BRIDGE_LOG_BYTES)
                        stream = path.open("ab")
            await handle.process.wait()
        finally:
            stream.close()
            self._copy_tasks.pop(session_id, None)
            self._prune_completed_sessions()

    def _prune_completed_sessions(self) -> None:
        completed = [
            (session_id, handle)
            for session_id, handle in self._sessions.items()
            if handle.process.returncode is not None
        ]
        completed.sort(key=lambda item: item[1].started_at, reverse=True)
        for session_id, _handle in completed[_MAX_COMPLETED_BRIDGE_SESSIONS:]:
            self._sessions.pop(session_id, None)
            self._commands.pop(session_id, None)
            self._output_paths.pop(session_id, None)


def _read_tail_text(path: Path, *, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(size - max_bytes, 0), os.SEEK_SET)
        data = handle.read(max_bytes)
    return data.decode("utf-8", errors="replace")


def _cap_output_file(path: Path, *, max_bytes: int) -> None:
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= max_bytes:
        return
    keep_bytes = max(max_bytes - len(_BRIDGE_TRUNCATION_MARKER), 0)
    with path.open("rb") as handle:
        handle.seek(max(size - keep_bytes, 0), os.SEEK_SET)
        tail = handle.read(keep_bytes)
    with path.open("wb") as handle:
        handle.write(_BRIDGE_TRUNCATION_MARKER)
        handle.write(tail)


_DEFAULT_MANAGER: BridgeSessionManager | None = None


def get_bridge_manager() -> BridgeSessionManager:
    """Return the singleton bridge manager."""
    global _DEFAULT_MANAGER
    if _DEFAULT_MANAGER is None:
        _DEFAULT_MANAGER = BridgeSessionManager()
    return _DEFAULT_MANAGER
