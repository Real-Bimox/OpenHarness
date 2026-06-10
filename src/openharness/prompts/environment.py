"""Environment detection for system prompt construction.

Gathers OS, shell, platform, working directory, date, and git info.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class EnvironmentInfo:
    """Snapshot of the current runtime environment."""

    os_name: str
    os_version: str
    platform_machine: str
    shell: str
    cwd: str
    home_dir: str
    date: str
    python_version: str
    python_executable: str
    virtual_env: str | None
    is_git_repo: bool
    git_branch: str | None = None
    hostname: str = ""
    extra: dict[str, str] = field(default_factory=dict)


def detect_os() -> tuple[str, str]:
    """Return (os_name, os_version) for the current platform."""
    system = platform.system()
    if system == "Linux":
        try:
            import distro  # type: ignore[import-untyped]
            return "Linux", distro.version(pretty=True) or platform.release()
        except ImportError:
            return "Linux", platform.release()
    elif system == "Darwin":
        mac_ver = platform.mac_ver()[0]
        return "macOS", mac_ver or platform.release()
    elif system == "Windows":
        win_ver = platform.version()
        return "Windows", win_ver
    return system, platform.release()


def detect_shell() -> str:
    """Detect the user's shell."""
    shell = os.environ.get("SHELL", "")
    if shell:
        return Path(shell).name

    # Fallback: check for common shells on PATH
    for candidate in ("bash", "zsh", "fish", "sh"):
        if shutil.which(candidate):
            return candidate

    return "unknown"


def _git_head_fingerprint(cwd: str) -> tuple[str, int] | None:
    """Cheap stat-based key for git state: (.git/HEAD path, mtime_ns).

    HEAD's mtime changes on branch switches and new commits, which is what
    the prompt's git section reflects. Returns None when no .git is found
    in cwd or its ancestors.
    """
    current = Path(cwd).resolve()
    for candidate in (current, *current.parents):
        head = candidate / ".git" / "HEAD"
        try:
            return (str(head), head.stat().st_mtime_ns)
        except OSError:
            continue
    return None


_GIT_INFO_CACHE: dict[str, tuple[tuple[str, int] | None, tuple[bool, str | None]]] = {}


def detect_git_info(cwd: str) -> tuple[bool, str | None]:
    """Check if cwd is inside a git repo and return (is_git_repo, branch_name).

    Spawning git twice per call is too expensive for the per-line prompt
    rebuild, so results are cached per cwd and invalidated by .git/HEAD's
    mtime (branch switch / new commit). Non-repo results are cached on the
    absence of any .git ancestor.
    """
    fingerprint = _git_head_fingerprint(cwd)
    cached = _GIT_INFO_CACHE.get(cwd)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]
    result = _detect_git_info_uncached(cwd)
    if len(_GIT_INFO_CACHE) > 64:
        _GIT_INFO_CACHE.clear()
    _GIT_INFO_CACHE[cwd] = (fingerprint, result)
    return result


def _detect_git_info_uncached(cwd: str) -> tuple[bool, str | None]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
        is_git = result.returncode == 0 and result.stdout.strip() == "true"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, None

    if not is_git:
        return False, None

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
        branch = result.stdout.strip() if result.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        branch = None

    return True, branch


# Snapshots keyed per cwd, validated by the git HEAD fingerprint and the
# UTC date (the only inputs that change while a session runs).
_ENV_INFO_CACHE: dict[str, tuple[tuple, EnvironmentInfo]] = {}


def get_environment_info(cwd: str | None = None) -> EnvironmentInfo:
    """Gather all environment information into an EnvironmentInfo snapshot."""
    if cwd is None:
        cwd = os.getcwd()

    validator = (
        _git_head_fingerprint(cwd),
        datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
        os.environ.get("VIRTUAL_ENV"),
    )
    hit = _ENV_INFO_CACHE.get(cwd)
    if hit is not None and hit[0] == validator:
        return hit[1]
    info = _get_environment_info_uncached(cwd)
    if len(_ENV_INFO_CACHE) > 16:
        _ENV_INFO_CACHE.clear()
    _ENV_INFO_CACHE[cwd] = (validator, info)
    return info


def _get_environment_info_uncached(cwd: str) -> EnvironmentInfo:
    python_executable = str(Path(sys.executable).resolve())
    virtual_env = os.environ.get("VIRTUAL_ENV")
    if not virtual_env:
        executable_path = Path(python_executable)
        candidate = executable_path.parent.parent
        if executable_path.parent.name in {"bin", "Scripts"} and (candidate / "pyvenv.cfg").exists():
            virtual_env = str(candidate)

    os_name, os_version = detect_os()
    shell = detect_shell()
    is_git, branch = detect_git_info(cwd)

    return EnvironmentInfo(
        os_name=os_name,
        os_version=os_version,
        platform_machine=platform.machine(),
        shell=shell,
        cwd=cwd,
        home_dir=str(Path.home()),
        date=datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
        python_version=platform.python_version(),
        python_executable=python_executable,
        virtual_env=virtual_env,
        is_git_repo=is_git,
        git_branch=branch,
        hostname=platform.node(),
    )
