#!/usr/bin/env python3
"""Small local client for the OpenHarness headless JSONL protocol."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from typing import Any


def _command_prefix(value: str | None) -> list[str]:
    if value:
        return shlex.split(value)
    env_value = os.environ.get("OPENHARNESS_OH_COMMAND", "").strip()
    if env_value:
        return shlex.split(env_value)
    return [sys.executable, "-m", "openharness"]


def _json_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":")) + "\n"


def _build_requests(args: argparse.Namespace) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = [
        {"type": "status", "request_id": "status-before"},
        {"type": "list_sessions", "request_id": "sessions-before"},
    ]
    if args.resume:
        request: dict[str, Any] = {
            "type": "resume",
            "session_id": args.resume,
            "request_id": "resume-1",
        }
        if args.prompt:
            request["prompt"] = args.prompt
        requests.append(request)
    elif args.continue_latest:
        request = {"type": "continue", "request_id": "continue-1"}
        if args.prompt:
            request["prompt"] = args.prompt
        requests.append(request)
    elif args.prompt:
        requests.append({"type": "submit", "prompt": args.prompt, "request_id": "submit-1"})
    requests.extend(
        [
            {"type": "status", "request_id": "status-after"},
            {"type": "shutdown", "request_id": "shutdown-1"},
        ]
    )
    return requests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", default=".", help="Workspace directory for the headless session.")
    parser.add_argument("--prompt", help="Optional prompt to submit.")
    parser.add_argument("--resume", help="Resume this session ID before submitting the prompt.")
    parser.add_argument(
        "--continue-latest",
        action="store_true",
        help="Continue the latest saved session before submitting the prompt.",
    )
    parser.add_argument(
        "--permission-mode",
        default="default",
        choices=["default", "plan", "full_auto"],
        help="Permission mode for the headless process.",
    )
    parser.add_argument("--model", help="Optional model override passed to OpenHarness.")
    parser.add_argument("--api-format", help="Optional API format override passed to OpenHarness.")
    parser.add_argument("--base-url", help="Optional local API base URL override.")
    parser.add_argument("--api-key", help="Optional API key override.")
    parser.add_argument(
        "--state-dir",
        help="Directory used for example config/data state. Defaults to a temporary directory.",
    )
    parser.add_argument(
        "--use-existing-state",
        action="store_true",
        help="Use OPENHARNESS_CONFIG_DIR/OPENHARNESS_DATA_DIR or OpenHarness defaults instead of temporary state.",
    )
    parser.add_argument(
        "--command",
        help="Command prefix for OpenHarness. Defaults to 'python -m openharness'.",
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="Process timeout in seconds.")
    parser.add_argument("--no-bare", action="store_true", help="Do not pass --bare to OpenHarness.")
    args = parser.parse_args()

    if args.resume and args.continue_latest:
        parser.error("--resume and --continue-latest are mutually exclusive")

    temp_state = None
    env = os.environ.copy()
    if args.state_dir:
        state_dir = Path(args.state_dir).expanduser().resolve()
        config_dir = state_dir / "config"
        data_dir = state_dir / "data"
        config_dir.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)
        env["OPENHARNESS_CONFIG_DIR"] = str(config_dir)
        env["OPENHARNESS_DATA_DIR"] = str(data_dir)
    elif not args.use_existing_state and not (args.resume or args.continue_latest):
        temp_state = tempfile.TemporaryDirectory(prefix="openharness-headless-client-")
        state_dir = Path(temp_state.name)
        config_dir = state_dir / "config"
        data_dir = state_dir / "data"
        config_dir.mkdir()
        data_dir.mkdir()
        env["OPENHARNESS_CONFIG_DIR"] = str(config_dir)
        env["OPENHARNESS_DATA_DIR"] = str(data_dir)

    cwd = str(Path(args.cwd).expanduser().resolve())
    command = _command_prefix(args.command)
    command.extend(["--headless", "--cwd", cwd, "--permission-mode", args.permission_mode])
    if not args.no_bare:
        command.append("--bare")
    if args.model:
        command.extend(["--model", args.model])
    if args.api_format:
        command.extend(["--api-format", args.api_format])
    if args.base_url:
        command.extend(["--base-url", args.base_url])
    if args.api_key:
        command.extend(["--api-key", args.api_key])

    stdin_payload = "".join(_json_line(request) for request in _build_requests(args))
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        stdout, stderr = process.communicate(stdin_payload, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        print(f"Timed out after {args.timeout:.1f}s", file=sys.stderr)
        if stderr:
            print(stderr, file=sys.stderr, end="")
        if temp_state is not None:
            temp_state.cleanup()
        return 124

    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            print(f"non-json stdout: {line}", file=sys.stderr)
            continue
        print(json.dumps(event, indent=2, sort_keys=True))

    if stderr:
        print(stderr, file=sys.stderr, end="")
    if temp_state is not None:
        temp_state.cleanup()
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
