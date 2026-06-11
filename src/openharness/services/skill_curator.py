"""Weekly skill-library consolidation.

Modeled on hermes-agent's ``agent/curator.py`` (spec/deviations in
docs/proposals/skill-learning-loop.md). Two parts: a pure deterministic
lifecycle pass (stale/archive, no LLM) and a gated LLM consolidation pass
that merges agent-created skills into class-level umbrellas.

Deliberate differences from hermes: the curator fork has NO shell — archival
is an internal move, and the fork's registry holds only skill tools; there
is no "fewer than 10 archives means you stopped too early" quota; archive is
always the maximum action and is reversible. Only agent-created skills are
candidates; bundled, plugin, and user-authored skills are never touched.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from openharness.config.paths import get_data_dir
from openharness.utils.fs import atomic_write_text

log = logging.getLogger(__name__)

_STATE_FILE = "skill_curator_state.json"

CURATOR_PROMPT = """You are consolidating an agent-grown skill library into \
class-level umbrella skills. This is a consolidation pass, not an audit and \
not a duplicate-hunt.

You may ONLY call the `skill` and `skill_manage` tools.

Goal shape: a small number of rich, class-level skills (each with SKILL.md + \
references/), NOT a long flat list where each skill captures one session's \
specifics. Hundreds of narrow skills is a failure of the library.

Method:
- Read candidates with `skill` to understand them.
- Find clusters that are really one class of task. Merge them: pick or create \
an umbrella skill, fold the others' durable content into it (as sections or \
references/ files), then delete each absorbed skill with \
absorbed_into='<umbrella>'.
- A skill whose name only describes one session (a PR number, an error \
string, a "fix-X-today") should be merged into the relevant umbrella or, if \
nothing of lasting value remains, deleted with absorbed_into=''.
- Do not flatten a skill that has references/ or scripts/ without re-homing \
those files first.

Hard rules:
- Touch ONLY the candidate skills listed below. Never edit bundled, plugin, \
or pinned skills.
- Deletion is allowed only to complete a consolidation you have already \
written into the umbrella; never delete content that exists nowhere else.
- There is no quota. Consolidate what genuinely belongs together and stop. \
Making no change is acceptable if the library is already well-shaped.

End with a one-paragraph human summary of what you consolidated and why."""


def _state_path() -> Path:
    return get_data_dir() / _STATE_FILE


def load_state() -> dict[str, Any]:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    atomic_write_text(_state_path(), json.dumps(state, indent=2) + "\n", fsync=False)


def candidate_skills(skills_dir: Path | None = None) -> list[str]:
    """Agent-created, non-pinned skills present on disk — the only candidates."""
    from openharness.skills import usage
    from openharness.skills.loader import get_user_skills_dir

    root = skills_dir or get_user_skills_dir()
    records = usage.load_records(root)
    names = []
    for name, record in records.items():
        if record.get("created_by") != "agent" or record.get("pinned"):
            continue
        if (root / name / "SKILL.md").exists():
            names.append(name)
    return sorted(names)


def should_run_now(settings, *, now: float | None = None) -> bool:
    skills = getattr(settings, "skills", None)
    if skills is None or not skills.curator_enabled:
        return False
    now = now if now is not None else time.time()
    state = load_state()
    last = state.get("last_run_at")
    if not isinstance(last, (int, float)):
        # First sight: seed and defer one full interval (no surprise pass).
        state["last_run_at"] = now
        _save_state(state)
        return False
    return now - last >= skills.curator_interval_hours * 3600


def run_lifecycle(settings, *, skills_dir: Path | None = None, now: float | None = None) -> dict[str, list[str]]:
    """Deterministic stale/archive pass over agent-created candidates."""
    from openharness.skills import usage

    skills = settings.skills
    return usage.apply_automatic_transitions(
        candidate_skills(skills_dir),
        skills_dir=skills_dir,
        stale_after_days=skills.stale_after_days,
        archive_after_days=skills.archive_after_days,
        now=now,
    )


async def run_curator(
    *,
    bundle=None,
    settings=None,
    api_client=None,
    cwd: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run the lifecycle pass and (unless dry-run) the LLM consolidation pass.

    Returns a run report dict. Safe to call with an explicit api_client for
    CLI use, or with a bundle for the in-session path.
    """
    from openharness.config import load_settings

    settings = settings or (bundle.current_settings() if bundle is not None else load_settings())
    transitions = run_lifecycle(settings, now=None)
    report: dict[str, Any] = {
        "ran_at": time.time(),
        "staled": transitions["staled"],
        "archived": transitions["archived"],
        "consolidation": "skipped (no candidates)",
        "summary": "",
    }
    candidates = candidate_skills()
    if candidates and not dry_run:
        report.update(await _run_llm_pass(bundle, settings, api_client, cwd, candidates))

    state = load_state()
    if not dry_run:
        state["last_run_at"] = report["ran_at"]
        state["last_report"] = {k: report[k] for k in ("staled", "archived", "consolidation")}
        state["run_count"] = int(state.get("run_count") or 0) + 1
        _save_state(state)
    _write_report(report)
    return report


async def _run_llm_pass(bundle, settings, api_client, cwd, candidates) -> dict[str, Any]:
    from openharness.engine import QueryEngine
    from openharness.permissions import PermissionChecker
    from openharness.skills import provenance, usage
    from openharness.skills.loader import get_user_skills_dir
    from openharness.tools import ToolRegistry
    from openharness.tools.skill_manage_tool import SkillManageTool
    from openharness.tools.skill_tool import SkillTool

    resolved_client = api_client if api_client is not None else (bundle.api_client if bundle else None)
    if resolved_client is None:
        return {"consolidation": "skipped (no api client)"}
    model = settings.skills.curator_model or (bundle.engine.model if bundle else settings.model)
    work_cwd = cwd or (bundle.cwd if bundle else str(Path.cwd()))

    registry = ToolRegistry()
    registry.register(SkillTool())
    registry.register(SkillManageTool())

    records = usage.load_records(get_user_skills_dir())
    listing = "\n".join(
        f"- {name}: state={records.get(name, {}).get('state', 'active')}, "
        f"uses={records.get(name, {}).get('use_count', 0)}, "
        f"patches={records.get(name, {}).get('patch_count', 0)}"
        for name in candidates
    )

    async def _auto_deny(tool_name: str, reason: str) -> bool:
        del tool_name, reason
        return False

    async def _noop_ask(_q: str) -> str:
        return ""

    engine = QueryEngine(
        api_client=resolved_client,
        tool_registry=registry,
        permission_checker=PermissionChecker(settings.permission),
        cwd=work_cwd,
        model=model,
        system_prompt="You are the OpenHarness skill curator.",
        max_turns=100,
        permission_prompt=_auto_deny,
        ask_user_prompt=_noop_ask,
        settings=settings,
        tool_metadata={"_suppress_next_user_goal": True},
    )
    prompt = f"{CURATOR_PROMPT}\n\nCandidate skills:\n{listing}"
    token = provenance.set_origin("background_review")
    summary_parts: list[str] = []
    try:
        from openharness.engine.stream_events import AssistantTurnComplete

        async for event in engine.submit_message(prompt):
            if isinstance(event, AssistantTurnComplete) and event.message.text.strip():
                summary_parts.append(event.message.text.strip())
    except Exception as exc:
        provenance.reset_origin(token)
        return {"consolidation": f"failed: {exc}"}
    provenance.reset_origin(token)
    return {"consolidation": "ran", "summary": summary_parts[-1] if summary_parts else ""}


def _write_report(report: dict[str, Any]) -> None:
    try:
        reports_dir = get_data_dir() / "reports" / "skill-curator"
        reports_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        atomic_write_text(reports_dir / f"{stamp}.json", json.dumps(report, indent=2) + "\n", fsync=False)
    except OSError as exc:
        log.debug("failed to write curator report: %s", exc)
