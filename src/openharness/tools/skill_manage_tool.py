"""Agent write path for the user skill library.

Modeled on hermes-agent's ``skill_manage`` (spec and deviations:
docs/proposals/skill-learning-loop.md). Differences are deliberate:
mutations are structurally confined to the *user* skills directory, so
bundled and plugin skills cannot be edited at all (hermes protects them only
by prompt); write scanning is on by default; patches are exact-match with a
no-match preview, consistent with ``edit_file``.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import os
from pathlib import Path

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_SKILL_CONTENT_CHARS = 100_000
MAX_SKILL_FILE_BYTES = 1_048_576
ALLOWED_SUBDIRS = {"references", "templates", "scripts", "assets"}

_ACTIONS = {"create", "edit", "patch", "delete", "write_file", "remove_file"}

# Persistence/injection markers that should never enter a skill the agent
# wrote for itself: a skill loads into future system prompts, so adversarial
# tool output that reaches a write would otherwise become standing orders.
_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("override-instructions", re.compile(r"(?i)ignore (all |any )?(previous|prior|above) (instructions|rules)")),
    ("exfiltrate", re.compile(r"(?i)\b(exfiltrate|send (all |the )?(secrets|credentials|keys))\b")),
    ("hidden-directive", re.compile(r"(?i)do not (tell|inform|mention to) the user")),
    ("self-preservation", re.compile(r"(?i)(prevent|avoid|resist) (being )?(shut ?down|disabled|deleted)")),
)


class SkillManageInput(BaseModel):
    action: str = Field(description="create | edit | patch | delete | write_file | remove_file")
    name: str = Field(description="Skill name (lowercase letters, digits, . _ -; max 64 chars)")
    content: str | None = Field(default=None, description="Full SKILL.md content for create/edit.")
    old_string: str | None = Field(default=None, description="Exact text to replace (patch).")
    new_string: str | None = Field(default=None, description="Replacement text (patch).")
    file_path: str | None = Field(
        default=None,
        description="Support file path inside the skill (references/, templates/, scripts/, assets/), or SKILL.md for patch.",
    )
    file_content: str | None = Field(default=None, description="Content for write_file.")
    absorbed_into: str | None = Field(
        default=None,
        description="On delete: umbrella skill that absorbed this content, or '' for an explicit prune.",
    )


def _error(message: str, **extra: object) -> ToolResult:
    return ToolResult(output=json.dumps({"success": False, "error": message, **extra}), is_error=True)


def _ok(**payload: object) -> ToolResult:
    return ToolResult(output=json.dumps({"success": True, **payload}, ensure_ascii=False))


def _validate_name(name: str) -> str | None:
    if not name:
        return "Skill name is required."
    if len(name) > MAX_NAME_LENGTH:
        return f"Skill name exceeds {MAX_NAME_LENGTH} characters."
    if not VALID_NAME_RE.match(name):
        return (
            f"Invalid skill name '{name}'. Use lowercase letters, numbers, hyphens,"
            " dots, and underscores; it must start with a letter or digit."
        )
    return None


def _validate_frontmatter(content: str) -> str | None:
    if not content.startswith("---"):
        return "SKILL.md must start with YAML frontmatter (---). See existing skills for the format."
    closing = re.search(r"\n---\s*\n", content)
    if closing is None:
        return "SKILL.md frontmatter is missing its closing --- line."
    header = content[3 : closing.start()]
    import yaml

    try:
        meta = yaml.safe_load(header)
    except yaml.YAMLError as exc:
        return f"Frontmatter is not valid YAML: {exc}"
    if not isinstance(meta, dict):
        return "Frontmatter must be a YAML mapping."
    if not str(meta.get("name") or "").strip():
        return "Frontmatter must include a non-empty 'name'."
    description = str(meta.get("description") or "")
    if not description.strip():
        return "Frontmatter must include a non-empty 'description'."
    if len(description) > MAX_DESCRIPTION_LENGTH:
        return f"Description exceeds {MAX_DESCRIPTION_LENGTH} characters."
    if not content[closing.end():].strip():
        return "SKILL.md must have a markdown body after the frontmatter."
    return None


def scan_skill_text(text: str) -> list[str]:
    """Return findings that should block a skill write (secrets + injection)."""
    from openharness.memory.team import SECRET_RULES

    findings: list[str] = []
    for rule_id, label, pattern in SECRET_RULES:
        if pattern.search(text):
            findings.append(f"secret:{rule_id} ({label})")
    for marker, pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            findings.append(f"injection:{marker}")
    return findings


def _validate_file_path(raw: str) -> tuple[Path | None, str | None]:
    path = Path(raw)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        return None, f"Path escapes the skill directory: {raw}"
    if path.name == "SKILL.md" and len(path.parts) == 1:
        return path, None
    if not path.parts or path.parts[0] not in ALLOWED_SUBDIRS:
        return None, (
            f"Support files must live under one of {sorted(ALLOWED_SUBDIRS)} (got '{raw}')."
        )
    if len(path.parts) < 2:
        return None, "Provide a filename, not just a directory."
    return path, None


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class SkillManageTool(BaseTool):
    name = "skill_manage"
    description = (
        "Create, improve, or retire skills in the user skill library. Use it to "
        "persist how to do a class of task: patch the skill that was in play "
        "when you learned something; keep skills class-level (never named after "
        "one session's bug or PR); add session detail as references/ files. "
        "Bundled and plugin skills cannot be modified — only user skills. "
        "Pinned skills can be improved but not deleted. Confirm with the user "
        "before deleting. On delete, pass absorbed_into='<umbrella>' when the "
        "content was merged elsewhere, or absorbed_into='' for an explicit prune."
    )
    input_model = SkillManageInput

    async def execute(self, arguments: SkillManageInput, context: ToolExecutionContext) -> ToolResult:
        import asyncio

        return await asyncio.to_thread(self._execute_sync, arguments, context)

    def _execute_sync(self, arguments: SkillManageInput, context: ToolExecutionContext) -> ToolResult:
        from openharness.config import load_settings
        from openharness.skills import provenance, usage
        from openharness.skills.loader import (
            get_user_skills_dir,
            invalidate_skill_registry_cache,
            load_skill_registry,
        )

        action = (arguments.action or "").strip()
        if action not in _ACTIONS:
            return _error(f"Unknown action '{action}'. Use one of: {sorted(_ACTIONS)}.")
        name_error = _validate_name(arguments.name)
        if name_error:
            return _error(name_error)

        try:
            settings = load_settings()
        except Exception:
            settings = None
        guard_writes = bool(getattr(getattr(settings, "skills", None), "guard_writes", True))

        skills_dir = get_user_skills_dir()
        skill_dir = skills_dir / arguments.name

        # Approval gate: stage instead of write when enabled.
        from openharness.services.skill_approval import gate_bypassed, stage_pending_write

        approval_on = settings is not None and getattr(
            getattr(settings, "skills", None), "write_approval", False
        )
        if approval_on and not gate_bypassed():
            # Every skill mutation stages (matching hermes: skill diffs are too
            # large for inline approval, and background writes have no user).
            pending = stage_pending_write(arguments.model_dump(), origin=provenance.current_origin())
            return _ok(
                staged=True,
                pending_id=pending["id"],
                message=(
                    "Write approval is enabled: this change was staged for review."
                    " The user can inspect it with `oh skills pending`."
                ),
            )

        result = self._apply(arguments, action, skills_dir, skill_dir, guard_writes)
        payload = json.loads(result.output)
        if payload.get("success"):
            invalidate_skill_registry_cache()
            if action == "create" and provenance.is_background_review():
                usage.mark_agent_created(arguments.name, skills_dir)
            elif action in {"patch", "edit", "write_file", "remove_file"}:
                usage.bump_patch(arguments.name, skills_dir)
            elif action == "delete":
                usage.forget(arguments.name, skills_dir)
        # Confirm the registry still loads; surfaces breakage immediately.
        try:
            load_skill_registry(str(context.cwd))
        except Exception:
            pass
        return result

    def _apply(
        self,
        arguments: SkillManageInput,
        action: str,
        skills_dir: Path,
        skill_dir: Path,
        guard_writes: bool,
    ) -> ToolResult:
        from openharness.skills import usage

        skill_md = skill_dir / "SKILL.md"

        def _resolve_inside(rel: Path) -> tuple[Path | None, str | None]:
            target = (skill_dir / rel).resolve()
            try:
                target.relative_to(skill_dir.resolve())
            except ValueError:
                return None, f"Path escapes the skill directory: {rel}"
            return target, None

        def _scan_or_none(text: str, *, source_desc: str) -> ToolResult | None:
            if not guard_writes:
                return None
            findings = scan_skill_text(text)
            if findings:
                return _error(
                    f"Write blocked by the skill guard ({source_desc}): {', '.join(findings)}."
                    " Remove the flagged content; secrets and instruction-override"
                    " phrasing must never be persisted into skills.",
                    findings=findings,
                )
            return None

        if action == "create":
            if arguments.content is None:
                return _error("create requires 'content' (full SKILL.md).")
            if len(arguments.content) > MAX_SKILL_CONTENT_CHARS:
                return _error(
                    f"SKILL.md exceeds {MAX_SKILL_CONTENT_CHARS} characters; split detail into references/ files."
                )
            fm_error = _validate_frontmatter(arguments.content)
            if fm_error:
                return _error(fm_error)
            if skill_dir.exists():
                return _error(f"Skill '{arguments.name}' already exists. Use edit or patch.")
            blocked = _scan_or_none(arguments.content, source_desc="create")
            if blocked:
                return blocked
            _atomic_write(skill_md, arguments.content)
            return _ok(action="create", name=arguments.name, hint="Add support files with write_file.")

        if not skill_dir.is_dir() or not skill_md.exists():
            return _error(
                f"Skill '{arguments.name}' not found in the user skill library."
                " Only user skills can be modified; bundled and plugin skills are read-only."
            )

        if action == "edit":
            if arguments.content is None:
                return _error("edit requires 'content' (full SKILL.md).")
            if len(arguments.content) > MAX_SKILL_CONTENT_CHARS:
                return _error(f"SKILL.md exceeds {MAX_SKILL_CONTENT_CHARS} characters.")
            fm_error = _validate_frontmatter(arguments.content)
            if fm_error:
                return _error(fm_error)
            blocked = _scan_or_none(arguments.content, source_desc="edit")
            if blocked:
                return blocked
            _atomic_write(skill_md, arguments.content)
            return _ok(action="edit", name=arguments.name)

        if action == "patch":
            if arguments.old_string is None or arguments.new_string is None:
                return _error("patch requires 'old_string' and 'new_string'.")
            rel = Path(arguments.file_path) if arguments.file_path else Path("SKILL.md")
            checked, path_error = _validate_file_path(str(rel))
            if path_error:
                return _error(path_error)
            target, contain_error = _resolve_inside(checked)
            if contain_error:
                return _error(contain_error)
            if not target.exists():
                return _error(f"File not found in skill: {rel}")
            text = target.read_text(encoding="utf-8", errors="replace")
            occurrences = text.count(arguments.old_string)
            if occurrences == 0:
                return _error(
                    "old_string not found. Read the file first and copy the exact text.",
                    file_preview=text[:500],
                )
            if occurrences > 1:
                return _error(
                    f"old_string matches {occurrences} places; include more surrounding context."
                )
            patched = text.replace(arguments.old_string, arguments.new_string, 1)
            if target.name == "SKILL.md":
                if len(patched) > MAX_SKILL_CONTENT_CHARS:
                    return _error(f"Patch result exceeds {MAX_SKILL_CONTENT_CHARS} characters.")
                fm_error = _validate_frontmatter(patched)
                if fm_error:
                    return _error(f"Patch would break SKILL.md structure: {fm_error}")
            blocked = _scan_or_none(arguments.new_string, source_desc="patch")
            if blocked:
                return blocked
            _atomic_write(target, patched)
            return _ok(action="patch", name=arguments.name, file=str(rel))

        if action == "write_file":
            if not arguments.file_path or arguments.file_content is None:
                return _error("write_file requires 'file_path' and 'file_content'.")
            checked, path_error = _validate_file_path(arguments.file_path)
            if path_error or checked is None:
                return _error(path_error or "Invalid path.")
            if checked.name == "SKILL.md":
                return _error("Use edit or patch for SKILL.md.")
            if len(arguments.file_content.encode("utf-8")) > MAX_SKILL_FILE_BYTES:
                return _error(f"File exceeds {MAX_SKILL_FILE_BYTES} bytes.")
            target, contain_error = _resolve_inside(checked)
            if contain_error:
                return _error(contain_error)
            blocked = _scan_or_none(arguments.file_content, source_desc="write_file")
            if blocked:
                return blocked
            _atomic_write(target, arguments.file_content)
            return _ok(action="write_file", name=arguments.name, file=arguments.file_path)

        if action == "remove_file":
            if not arguments.file_path:
                return _error("remove_file requires 'file_path'.")
            checked, path_error = _validate_file_path(arguments.file_path)
            if path_error or checked is None or checked.name == "SKILL.md":
                return _error(path_error or "SKILL.md cannot be removed; use delete.")
            target, contain_error = _resolve_inside(checked)
            if contain_error:
                return _error(contain_error)
            if not target.exists():
                available = [
                    str(p.relative_to(skill_dir))
                    for sub in ALLOWED_SUBDIRS
                    for p in (skill_dir / sub).rglob("*")
                    if p.is_file()
                ]
                return _error(f"File not found: {arguments.file_path}", available_files=available)
            target.unlink()
            parent = target.parent
            if parent != skill_dir and not any(parent.iterdir()):
                parent.rmdir()
            return _ok(action="remove_file", name=arguments.name, file=arguments.file_path)

        # delete
        if usage.is_pinned(arguments.name, skills_dir):
            return _error(
                f"Skill '{arguments.name}' is pinned and cannot be deleted."
                f" Ask the user to run `oh skills unpin {arguments.name}` first."
                " Patches and edits remain allowed on pinned skills."
            )
        if arguments.absorbed_into:
            umbrella = skills_dir / arguments.absorbed_into
            if arguments.absorbed_into == arguments.name:
                return _error("absorbed_into cannot equal the deleted skill.")
            if not (umbrella / "SKILL.md").exists():
                return _error(
                    f"absorbed_into='{arguments.absorbed_into}' does not exist."
                    " Create or patch the umbrella skill first, then retry the delete."
                )
        shutil.rmtree(skill_dir)
        return _ok(
            action="delete",
            name=arguments.name,
            absorbed_into=arguments.absorbed_into,
        )
