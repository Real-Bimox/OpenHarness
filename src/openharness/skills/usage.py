"""Skill usage telemetry and lifecycle, modeled on hermes-agent's
``tools/skill_usage.py`` sidecar (spec: docs/proposals/skill-learning-loop.md).

A single ``.usage.json`` next to the user skills holds one record per skill:
counters, lifecycle state (active -> stale -> archived), pinning, and
provenance (``created_by: "agent"`` marks background-review creations — the
only skills the curator may touch). All bumps are best-effort: telemetry
must never break a tool call. Archive is the maximum destructive action and
is always reversible.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from openharness.skills.loader import get_user_skills_dir
from openharness.utils.file_lock import exclusive_file_lock
from openharness.utils.fs import atomic_write_text

log = logging.getLogger(__name__)

SIDECAR_NAME = ".usage.json"
ARCHIVE_DIR_NAME = ".archive"

STATE_ACTIVE = "active"
STATE_STALE = "stale"
STATE_ARCHIVED = "archived"


def _sidecar_path(skills_dir: Path | None = None) -> Path:
    return (skills_dir or get_user_skills_dir()) / SIDECAR_NAME


def _empty_record(now: float | None = None) -> dict[str, Any]:
    return {
        "created_by": None,
        "use_count": 0,
        "patch_count": 0,
        "last_used_at": None,
        "last_patched_at": None,
        "created_at": now if now is not None else time.time(),
        "state": STATE_ACTIVE,
        "pinned": False,
        "archived_at": None,
    }


def load_records(skills_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    path = _sidecar_path(skills_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def _save_records(records: dict[str, dict[str, Any]], skills_dir: Path | None = None) -> None:
    atomic_write_text(
        _sidecar_path(skills_dir), json.dumps(records, indent=2, sort_keys=True) + "\n", fsync=False
    )


def _mutate(name: str, fn, skills_dir: Path | None = None) -> None:
    """Apply ``fn(record)`` under the cross-process sidecar lock, best-effort."""
    root = skills_dir or get_user_skills_dir()
    lock = root / (SIDECAR_NAME + ".lock")
    try:
        with exclusive_file_lock(lock):
            records = load_records(root)
            record = records.get(name) or _empty_record()
            fn(record)
            records[name] = record
            _save_records(records, root)
    except Exception as exc:
        log.debug("skill usage update failed for %s: %s", name, exc)


def bump_use(name: str, skills_dir: Path | None = None) -> None:
    def _apply(record: dict[str, Any]) -> None:
        record["use_count"] = int(record.get("use_count") or 0) + 1
        record["last_used_at"] = time.time()
        if record.get("state") == STATE_STALE:
            record["state"] = STATE_ACTIVE

    _mutate(name, _apply, skills_dir)


def bump_patch(name: str, skills_dir: Path | None = None) -> None:
    def _apply(record: dict[str, Any]) -> None:
        record["patch_count"] = int(record.get("patch_count") or 0) + 1
        record["last_patched_at"] = time.time()
        if record.get("state") == STATE_STALE:
            record["state"] = STATE_ACTIVE

    _mutate(name, _apply, skills_dir)


def mark_agent_created(name: str, skills_dir: Path | None = None) -> None:
    _mutate(name, lambda record: record.update(created_by="agent"), skills_dir)


def forget(name: str, skills_dir: Path | None = None) -> None:
    root = skills_dir or get_user_skills_dir()
    lock = root / (SIDECAR_NAME + ".lock")
    try:
        with exclusive_file_lock(lock):
            records = load_records(root)
            if records.pop(name, None) is not None:
                _save_records(records, root)
    except Exception as exc:
        log.debug("skill usage forget failed for %s: %s", name, exc)


def is_pinned(name: str, skills_dir: Path | None = None) -> bool:
    return bool((load_records(skills_dir).get(name) or {}).get("pinned"))


def set_pinned(name: str, pinned: bool, skills_dir: Path | None = None) -> None:
    _mutate(name, lambda record: record.update(pinned=bool(pinned)), skills_dir)


def is_agent_created(name: str, skills_dir: Path | None = None) -> bool:
    return (load_records(skills_dir).get(name) or {}).get("created_by") == "agent"


def _activity_anchor(record: dict[str, Any]) -> float:
    """Last activity, else created_at. Creation alone never counts as use."""
    candidates = [
        record.get("last_used_at"),
        record.get("last_patched_at"),
    ]
    activity = max((c for c in candidates if isinstance(c, (int, float))), default=None)
    if activity is not None:
        return float(activity)
    created = record.get("created_at")
    return float(created) if isinstance(created, (int, float)) else time.time()


def archive_skill(name: str, skills_dir: Path | None = None) -> bool:
    """Move a skill directory into ``.archive/`` — never delete."""
    root = skills_dir or get_user_skills_dir()
    source = root / name
    if not source.is_dir():
        return False
    archive_root = root / ARCHIVE_DIR_NAME
    archive_root.mkdir(parents=True, exist_ok=True)
    target = archive_root / name
    if target.exists():
        target = archive_root / f"{name}-{int(time.time())}"
    shutil.move(str(source), str(target))
    _mutate(name, lambda record: record.update(state=STATE_ARCHIVED, archived_at=time.time()), root)
    return True


def restore_skill(name: str, skills_dir: Path | None = None) -> bool:
    root = skills_dir or get_user_skills_dir()
    candidates = sorted((root / ARCHIVE_DIR_NAME).glob(f"{name}*"))
    if not candidates:
        return False
    target = root / name
    if target.exists():
        return False
    shutil.move(str(candidates[-1]), str(target))
    _mutate(name, lambda record: record.update(state=STATE_ACTIVE, archived_at=None), root)
    return True


def apply_automatic_transitions(
    skill_names: list[str],
    *,
    skills_dir: Path | None = None,
    stale_after_days: float = 30.0,
    archive_after_days: float = 90.0,
    now: float | None = None,
) -> dict[str, list[str]]:
    """Pure lifecycle pass over curator-eligible skills (no LLM).

    First sight of a skill seeds its clock and defers — non-use is measured
    from first observation, never from epoch, so an upgrade can never mass
    archive. Pinned skills are skipped entirely.
    """
    root = skills_dir or get_user_skills_dir()
    now = now if now is not None else time.time()
    staled: list[str] = []
    archived: list[str] = []
    lock = root / (SIDECAR_NAME + ".lock")
    with exclusive_file_lock(lock):
        records = load_records(root)
        changed = False
        for name in skill_names:
            record = records.get(name)
            if record is None:
                records[name] = _empty_record(now)
                changed = True
                continue
            if record.get("pinned"):
                continue
            anchor = _activity_anchor(record)
            state = record.get("state") or STATE_ACTIVE
            if state != STATE_ARCHIVED and now - anchor >= archive_after_days * 86400:
                # State is recorded by archive_skill after the move succeeds.
                archived.append(name)
            elif state == STATE_ACTIVE and now - anchor >= stale_after_days * 86400:
                record["state"] = STATE_STALE
                staled.append(name)
                changed = True
            elif state == STATE_STALE and now - anchor < stale_after_days * 86400:
                record["state"] = STATE_ACTIVE
                changed = True
        if changed:
            _save_records(records, root)
    for name in archived:
        try:
            archive_skill(name, root)
        except OSError as exc:
            log.warning("failed to archive skill %s: %s", name, exc)
    return {"staled": staled, "archived": archived}
