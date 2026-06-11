"""Post-turn background skill review.

Modeled on hermes-agent's ``agent/background_review.py`` (spec and deviations
in docs/proposals/skill-learning-loop.md). After a turn completes, and at
most once every ``skills.review_interval_turns`` turns, a background task
replays the conversation through a restricted runtime (skill tools only,
auto-deny permissions, the session model or ``skills.review_model``) that may
create or improve skills.

Deliberate differences from hermes: this fork is skills-only (memory capture
is already handled by services/memory_extract); there is no "be active"
quota; the curator (skill_curator.py) carries no shell. The fork reuses the
live API client so it rides the provider's prompt cache, the same lever
hermes uses to discount the extra call.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

REVIEW_PROMPT = """You are running a short, background self-improvement review \
AFTER the user's turn already completed. The user is not waiting on you.

Your only job: decide whether the conversation above taught something worth \
persisting as a SKILL, and if so, write it with the skill_manage tool. You may \
ONLY call the `skill` and `skill_manage` tools; anything else is denied.

How to decide and act, in priority order:
1. If a skill that was loaded/used this turn was missing something you had to \
work out, PATCH that skill. It was in play, so it is the right one to extend.
2. If an existing skill covers this class of task, improve it (read it with \
`skill` first).
3. If the detail is session-specific, add it as a references/ file under an \
existing skill and add a one-line pointer in SKILL.md.
4. Only CREATE a new skill when no existing skill covers the CLASS of task. \
The name must describe the class, never one session's artifact: not a PR \
number, error string, feature codename, or "fix-X/debug-Y-today". If the name \
only makes sense for today's task, it is wrong — fall back to 1-3.

Treat user corrections about your style, tone, format, or verbosity \
("stop doing X", "too verbose", "just give the answer", "remember this") as \
first-class skill signals: embed the preference so the next session starts \
knowing it.

Do NOT capture: environment-dependent failures; negative claims about tools \
("browser tools don't work") — capture the FIX instead, never a standing \
refusal; transient errors that resolved; one-off task narratives.

Keep skills class-level with rich SKILL.md + references/, not a flat list of \
one-session entries. A small, high-confidence update is good. "Nothing to \
save." is a legitimate outcome — say it and stop rather than inventing work."""

_REVIEW_TOOLS = ("skill", "skill_manage")

_active_tasks: set[asyncio.Task[Any]] = set()


def should_review(bundle, *, turns_since_review: int) -> bool:
    """Return True when a review should fire now.

    Ordered cheapest-first: the interval gate runs before the registry scan
    so a not-yet-due turn never pays for the tool-name lookup.
    """
    settings = bundle.current_settings()
    skills = getattr(settings, "skills", None)
    if skills is None or not skills.review_enabled:
        return False
    interval = int(skills.review_interval_turns or 0)
    if interval <= 0 or turns_since_review < interval:
        return False
    return any(tool.name == "skill_manage" for tool in bundle.tool_registry.list_tools())


def schedule_review(bundle, *, on_summary=None) -> None:
    """Fire-and-forget a background review of the current conversation."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    snapshot = list(bundle.engine.messages)
    if not snapshot:
        return
    task = loop.create_task(_run_review(bundle, snapshot, on_summary))
    _active_tasks.add(task)
    task.add_done_callback(_active_tasks.discard)


async def _run_review(bundle, messages_snapshot, on_summary) -> None:
    from openharness.engine import QueryEngine
    from openharness.engine.messages import sanitize_conversation_messages
    from openharness.permissions import PermissionChecker
    from openharness.skills import provenance
    from openharness.tools import ToolRegistry

    try:
        settings = bundle.current_settings()
        model = settings.skills.review_model or bundle.engine.model

        # Restricted registry: only skill tools, so a confused review can
        # touch nothing else even though it advertises the same tools[] for
        # cache parity (see below).
        review_registry = ToolRegistry()
        for tool in bundle.tool_registry.list_tools():
            if tool.name in _REVIEW_TOOLS:
                review_registry.register(tool)
        if "skill_manage" not in {t.name for t in review_registry.list_tools()}:
            return

        async def _auto_deny(tool_name: str, reason: str) -> bool:
            del tool_name, reason
            return False

        async def _noop_ask(_question: str) -> str:
            return ""

        review_engine = QueryEngine(
            api_client=bundle.api_client,  # reuse pool + provider prompt cache
            tool_registry=review_registry,
            permission_checker=PermissionChecker(settings.permission),
            cwd=bundle.cwd,
            model=model,
            system_prompt=bundle.engine.system_prompt,
            max_tokens=bundle.engine.max_tokens,
            max_turns=8,
            permission_prompt=_auto_deny,
            ask_user_prompt=_noop_ask,
            settings=settings,
            tool_metadata={"session_id": bundle.session_id, "_suppress_next_user_goal": True},
        )
        restored = sanitize_conversation_messages(list(messages_snapshot))
        review_engine.load_messages(restored)

        token = provenance.set_origin("background_review")
        actions: list[str] = []
        try:
            async for event in review_engine.submit_message(REVIEW_PROMPT):
                _collect_action(event, actions)
        finally:
            provenance.reset_origin(token)

        if actions and on_summary is not None:
            try:
                on_summary("Skill review: " + "; ".join(dict.fromkeys(actions)))
            except Exception:
                pass
    except Exception as exc:
        log.debug("background skill review failed: %s", exc)


def _collect_action(event, actions: list[str]) -> None:
    from openharness.engine.stream_events import ToolExecutionCompleted

    if isinstance(event, ToolExecutionCompleted) and event.tool_name == "skill_manage" and not event.is_error:
        try:
            import json

            payload = json.loads(event.output)
        except (ValueError, TypeError):
            return
        if payload.get("success") and payload.get("action"):
            label = payload["action"]
            if payload.get("name"):
                label += f" {payload['name']}"
            actions.append(label)
