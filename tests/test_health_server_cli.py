from __future__ import annotations

import subprocess
import sys

import pytest


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "openharness", *args],
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_missing_deps_error():
    try:
        import importlib.util

        if importlib.util.find_spec("fastapi") is not None:
            pytest.skip("fastapi installed; cannot test missing-dep error in this environment")
    except ImportError:
        pass
    result = _run_cli("--health-server")
    assert result.returncode != 0
    assert "health-server" in result.stderr


def test_port_without_enable_errors():
    result = _run_cli("--health-server-port", "9090")
    assert result.returncode != 0
    assert "--health-server-port requires --health-server" in result.stderr


def test_port_out_of_range():
    result = _run_cli("--health-server", "--health-server-port", "99999")
    if "requires the 'health-server' extra" in result.stderr:
        return
    assert result.returncode != 0
    assert "out of range" in result.stderr


def test_unsupported_continue_combo():
    result = _run_cli("--health-server", "--continue")
    if "requires the 'health-server' extra" in result.stderr:
        return
    assert result.returncode != 0
    assert "only supported standalone" in result.stderr


def test_unsupported_resume_combo():
    result = _run_cli("--health-server", "--resume", "abc123")
    if "requires the 'health-server' extra" in result.stderr:
        return
    assert result.returncode != 0
    assert "only supported standalone" in result.stderr


def test_unsupported_backend_only_combo():
    result = _run_cli("--health-server", "--backend-only")
    if "requires the 'health-server' extra" in result.stderr:
        return
    assert result.returncode != 0
    assert "only supported standalone" in result.stderr


def test_unsupported_print_combo():
    result = _run_cli("--health-server", "-p", "hello")
    if "requires the 'health-server' extra" in result.stderr:
        return
    assert result.returncode != 0
    assert "only supported standalone" in result.stderr


def test_unsupported_dry_run_combo():
    result = _run_cli("--health-server", "--dry-run")
    if "requires the 'health-server' extra" in result.stderr:
        return
    assert result.returncode != 0
    assert "only supported standalone" in result.stderr
