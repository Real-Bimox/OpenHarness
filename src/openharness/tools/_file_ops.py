"""Shared file-tool guardrails."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

from openharness.utils.fs import atomic_write_text

MAX_IMAGE_INPUT_BYTES = 20 * 1024 * 1024

_PATH_LOCKS: dict[str, asyncio.Lock] = {}


def lock_for_path(path: Path) -> asyncio.Lock:
    """Return the in-process lock for one resolved path."""
    key = str(path)
    lock = _PATH_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _PATH_LOCKS[key] = lock
    return lock


def resolve_workspace_path(base: Path, candidate: str) -> Path:
    """Resolve a candidate path and require it to stay inside ``base``."""
    root = Path(base).resolve()
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes workspace: {candidate}") from exc
    return resolved


def read_text_lines(path: Path, *, offset: int, limit: int) -> list[str]:
    """Read a bounded line slice without loading the whole file."""
    with path.open("rb") as raw:
        sample = raw.read(8192)
        if b"\x00" in sample:
            raise ValueError(f"Binary file cannot be read as text: {path}")
        raw.seek(0)
        text_stream = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline=None)
        selected: list[str] = []
        stop_at = offset + limit
        for line_no, line in enumerate(text_stream, start=1):
            index = line_no - 1
            if index < offset:
                continue
            if index >= stop_at:
                break
            selected.append(line.rstrip("\r\n"))
        return selected


def check_max_file_size(path: Path, *, max_bytes: int) -> None:
    """Raise if ``path`` exceeds a bounded input size."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValueError(f"Cannot stat file: {path}") from exc
    if size > max_bytes:
        raise ValueError(
            f"File is too large: {path} is {size} bytes, limit is {max_bytes} bytes"
        )


def file_signature(path: Path) -> tuple[int, int] | None:
    """Return a lightweight change token for optimistic write checks."""
    try:
        stat_result = path.stat()
    except FileNotFoundError:
        return None
    return (stat_result.st_mtime_ns, stat_result.st_size)


def atomic_write_utf8(path: Path, content: str) -> None:
    """Write UTF-8 text atomically."""
    atomic_write_text(path, content, encoding="utf-8")
