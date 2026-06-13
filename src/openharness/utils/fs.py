"""Atomic file-write helpers for persistent state.

Every file under ``~/.openharness/`` that is rewritten during normal use —
credentials, settings, session snapshots, cron registry, memory index — must
be written atomically. A crash, SIGKILL, power loss, or out-of-disk error
during a naive :meth:`pathlib.Path.write_text` leaves a truncated file on
disk, and the next read silently returns ``{}`` (for credentials) or raises
:class:`json.JSONDecodeError` (for sessions). Both outcomes are recoverable
only by manual intervention.

The pattern implemented here is the standard temp-file-plus-rename dance:

1. Create a same-directory temp file (so the final :func:`os.replace` is a
   rename on the same filesystem, never a cross-filesystem copy).
2. Write the payload, ``flush`` and ``fsync``.
3. Apply the target POSIX mode while the file is still private.
4. :func:`os.replace` atomically swaps the temp file into place. On POSIX
   the kernel guarantees that any concurrent reader sees either the old
   inode or the new one, never a half-written one. Since Python 3.3
   :func:`os.replace` provides the same guarantee on Windows.

For read-modify-write sequences on shared files (credentials, settings, cron
registry), pair atomic writes with :func:`exclusive_file_lock` from
:mod:`openharness.swarm.lockfile` so two concurrent ``oh`` processes cannot
clobber each other's updates.
"""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
from pathlib import Path

__all__ = [
    "atomic_write_bytes",
    "atomic_write_text",
    "read_text_tail",
    "append_jsonl_line",
    "read_jsonl_complete_lines",
]


def atomic_write_bytes(
    path: str | os.PathLike[str],
    data: bytes,
    *,
    mode: int | None = None,
    fsync: bool = True,
) -> None:
    """Write ``data`` to ``path`` atomically.

    When ``mode`` is given, the final file is created with that POSIX mode
    even if it did not previously exist. When ``mode`` is ``None``, the
    existing file's mode is preserved; for new files the current umask
    determines the mode, matching the historical behaviour of
    :meth:`pathlib.Path.write_text`.

    ``fsync=False`` keeps the rename atomicity (readers never observe a
    partial file) but skips flushing to stable storage. Use it for
    high-frequency state that is recreated from memory on the next write,
    where a crash losing the newest version is acceptable.
    """
    dst = Path(path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    target_mode = _resolve_target_mode(dst, mode)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{dst.name}.", suffix=".tmp", dir=str(dst.parent)
    )
    tmp_path = Path(tmp_name)
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


def atomic_write_text(
    path: str | os.PathLike[str],
    data: str,
    *,
    encoding: str = "utf-8",
    mode: int | None = None,
    fsync: bool = True,
) -> None:
    """Text variant of :func:`atomic_write_bytes`."""
    atomic_write_bytes(path, data.encode(encoding), mode=mode, fsync=fsync)


def read_text_tail(
    path: str | os.PathLike[str],
    *,
    max_bytes: int,
    encoding: str = "utf-8",
) -> str:
    """Return up to ``max_bytes`` from the end of a text file."""
    if max_bytes <= 0:
        return ""
    src = Path(path)
    with src.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes), os.SEEK_SET)
        return handle.read(max_bytes).decode(encoding, errors="replace")


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
    for the v2 transcript. The parent directory is created on first write,
    and the directory entry is fsynced on that first write so the *create*
    itself is durable (otherwise a crash could lose a brand-new transcript
    whose data was fsynced but whose directory entry was not).
    """
    dst = Path(path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    is_new = not dst.exists()
    payload = (line + "\n").encode(encoding)
    with open(dst, "ab") as handle:
        handle.write(payload)
        handle.flush()
        if fsync:
            os.fsync(handle.fileno())
    if fsync and is_new:
        # Only on create: a new file's directory entry is not durable until the
        # parent dir is flushed. Appends to an existing transcript keep the
        # one-fsync-per-turn cost (the dir entry is already durable).
        _fsync_dir(dst.parent)


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
    # split() always leaves a final element after the last "\n": "" when the
    # file ended in "\n" (all records complete), or the unterminated trailing
    # line (the signature of a crash mid-append). Either way it is not a
    # complete record, so drop it unconditionally.
    lines.pop()
    return [line for line in lines if line]


def _resolve_target_mode(path: Path, explicit_mode: int | None) -> int:
    if explicit_mode is not None:
        return explicit_mode
    try:
        st = path.stat()
    except FileNotFoundError:
        current_umask = os.umask(0)
        os.umask(current_umask)
        return 0o666 & ~current_umask
    return stat.S_IMODE(st.st_mode)


def _apply_mode(path: Path, target_mode: int) -> None:
    try:
        os.chmod(path, target_mode)
    except OSError:
        # chmod can fail on Windows / FAT / some network mounts. The payload
        # is still intact; only permission enforcement is weakened.
        pass


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
