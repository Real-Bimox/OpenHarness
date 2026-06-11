"""Write current-run.json: support-safe process metadata (proposal section 2)."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
import time
from typing import Any


def write_current_run(mode: str) -> None:
    """Best-effort write of process metadata safe for support bundles."""
    try:
        from openharness.cli import __version__
        from openharness.config import load_settings
        from openharness.config.paths import get_config_dir, get_data_dir
        from openharness.diagnostics import context as diag_context
        from openharness.utils.fs import atomic_write_text
        from pathlib import Path

        settings = load_settings()
        payload: dict[str, Any] = {
            "version": __version__,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "mode": mode,
            "run_id": diag_context.run_id(),
            "pid": diag_context.pid(),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "cwd_hash": hashlib.sha1(str(Path.cwd()).encode()).hexdigest()[:12],
            "data_dir": str(get_data_dir()),
            "config_dir": str(get_config_dir()),
            "active_profile": settings.active_profile or "",
            "model": settings.model,
            "provider": settings.provider,
            "flags": {
                "prompt_caching_enabled": settings.prompt_caching_enabled,
                "conversation_index_enabled": settings.conversation_index_enabled,
                "memory_enabled": settings.memory.enabled,
                "skills_review_enabled": settings.skills.review_enabled,
                "diagnostics_enabled": settings.diagnostics.enabled,
            },
        }
        diag_dir = get_data_dir() / "diagnostics"
        diag_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(diag_dir / "current-run.json", json.dumps(payload, indent=2) + "\n", fsync=False)
    except Exception:
        pass
