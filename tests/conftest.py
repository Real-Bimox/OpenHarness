"""Shared test fixtures."""

from __future__ import annotations

import pytest
import pytest_asyncio

from openharness.tasks.manager import shutdown_task_manager


@pytest_asyncio.fixture(autouse=True)
async def _reset_background_task_manager():
    yield
    await shutdown_task_manager()


@pytest.fixture(autouse=True)
def _isolate_fs_caches():
    """Reset module-level filesystem caches so tests cannot observe each
    other's (or their own earlier) cached loads across monkeypatched dirs."""
    yield
    from openharness.config import paths as config_paths
    from openharness.config import settings as config_settings
    from openharness.keybindings import loader as keybindings_loader
    from openharness.plugins import loader as plugins_loader
    from openharness.prompts import claudemd, environment, system_prompt
    from openharness.skills import bundled as skills_bundled
    from openharness.skills import loader as skills_loader
    from openharness.ui import runtime as ui_runtime

    config_settings._SETTINGS_FILE_CACHE.clear()
    config_settings._INLINE_SETTINGS_CACHE.clear()
    config_paths._ENSURED_DIRS.clear()
    keybindings_loader._KEYBINDINGS_CACHE = None
    plugins_loader._PLUGINS_CACHE.clear()
    skills_loader._SKILL_REGISTRY_CACHE.clear()
    skills_bundled._BUNDLED_CACHE = None
    claudemd._CLAUDE_MD_CACHE.clear()
    environment._GIT_INFO_CACHE.clear()
    environment._ENV_INFO_CACHE.clear()
    system_prompt._SYSTEM_PROMPT_CACHE.clear()
    ui_runtime._AUTH_STATUS_CACHE.clear()
    from openharness.services import conversation_index

    conversation_index.reset_conversation_index()
    from openharness.diagnostics import reset_recorder

    reset_recorder()
