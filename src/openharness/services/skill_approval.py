"""Staged approval for skill writes.

When ``skills.write_approval`` is on, every ``skill_manage`` mutation is
recorded as a pending JSON record (the exact replayable tool arguments) at
``<data_dir>/pending/skills/``, surviving restarts. The user reviews with
``oh skills pending`` / ``oh skills diff`` and applies with ``oh skills
approve`` — which replays the same arguments through the same tool with the
gate bypassed, so the preview can never diverge from the apply (a fidelity
gap in hermes's equivalent, where the diff used a different patch
algorithm than the apply).
"""

from __future__ import annotations

import difflib
import json
import time
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from openharness.config.paths import get_data_dir
from openharness.utils.fs import atomic_write_text

_GATE_BYPASS: ContextVar[bool] = ContextVar("skill_gate_bypass", default=False)


def gate_bypassed() -> bool:
    return _GATE_BYPASS.get()


def pending_dir() -> Path:
    path = get_data_dir() / "pending" / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def stage_pending_write(arguments: dict[str, Any], *, origin: str) -> dict[str, Any]:
    record = {
        "id": uuid.uuid4().hex[:8],
        "created_at": time.time(),
        "origin": origin,
        "arguments": arguments,
    }
    atomic_write_text(pending_dir() / f"{record['id']}.json", json.dumps(record, indent=2) + "\n")
    return record


def list_pending() -> list[dict[str, Any]]:
    records = []
    for path in sorted(pending_dir().glob("*.json")):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return records


def get_pending(pending_id: str) -> dict[str, Any] | None:
    path = pending_dir() / f"{pending_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def discard_pending(pending_id: str) -> bool:
    path = pending_dir() / f"{pending_id}.json"
    if not path.exists():
        return False
    path.unlink()
    return True


def _current_skill_text(arguments: dict[str, Any]) -> str:
    from openharness.skills.loader import get_user_skills_dir

    skill_dir = get_user_skills_dir() / str(arguments.get("name") or "")
    rel = arguments.get("file_path") or "SKILL.md"
    target = skill_dir / rel
    try:
        return target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def pending_diff(pending_id: str) -> str | None:
    """Unified diff of what approval would apply (same engine as the apply)."""
    record = get_pending(pending_id)
    if record is None:
        return None
    arguments = record["arguments"]
    action = arguments.get("action")
    before = _current_skill_text(arguments)
    if action in {"create", "edit"}:
        after = arguments.get("content") or ""
    elif action == "write_file":
        after = arguments.get("file_content") or ""
    elif action == "patch":
        old = arguments.get("old_string") or ""
        new = arguments.get("new_string") or ""
        if before.count(old) != 1:
            return f"(patch no longer applies cleanly: old_string occurs {before.count(old)} times)"
        after = before.replace(old, new, 1)
    elif action in {"delete", "remove_file"}:
        after = ""
    else:
        return f"(unknown action {action})"
    label = f"{arguments.get('name')}/{arguments.get('file_path') or 'SKILL.md'}"
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{label}",
            tofile=f"b/{label}",
        )
    )


async def apply_pending(pending_id: str) -> dict[str, Any]:
    """Replay the staged arguments through the real tool, gate bypassed."""
    from openharness.tools.base import ToolExecutionContext
    from openharness.tools.skill_manage_tool import SkillManageTool

    record = get_pending(pending_id)
    if record is None:
        return {"success": False, "error": f"No pending write with id {pending_id}."}
    tool = SkillManageTool()
    arguments = tool.input_model.model_validate(record["arguments"])
    token = _GATE_BYPASS.set(True)
    try:
        result = await tool.execute(arguments, ToolExecutionContext(cwd=Path.cwd()))
    finally:
        _GATE_BYPASS.reset(token)
    payload = json.loads(result.output)
    if payload.get("success"):
        discard_pending(pending_id)
    return payload
