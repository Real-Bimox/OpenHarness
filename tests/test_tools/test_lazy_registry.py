"""Tests for lazy built-in tool registration."""

from __future__ import annotations

import subprocess
import sys


def test_importing_tools_package_does_not_import_heavy_tool_modules() -> None:
    code = (
        "import sys, openharness.tools; "
        "print('openharness.tools.web_fetch_tool' in sys.modules); "
        "print('openharness.tools.list_mcp_resources_tool' in sys.modules)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )

    assert completed.stdout.splitlines() == ["False", "False"]
