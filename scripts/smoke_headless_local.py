#!/usr/bin/env python3
"""Smoke-check the local OpenHarness headless JSONL control loop."""

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


def _load_events(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        events.append(json.loads(line))
    return events


def _require_event(events: list[dict[str, Any]], event_type: str, request_id: str | None = None) -> dict[str, Any]:
    for event in events:
        if event.get("type") != event_type:
            continue
        if request_id is not None and event.get("request_id") != request_id:
            continue
        return event
    suffix = f" request_id={request_id!r}" if request_id else ""
    raise AssertionError(f"missing event type={event_type!r}{suffix}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--command",
        help="Command prefix for OpenHarness. Defaults to 'python -m openharness'.",
    )
    parser.add_argument("--timeout", type=float, default=20.0, help="Process timeout in seconds.")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="openharness-headless-smoke-") as tmp:
        root = Path(tmp)
        workspace = root / "workspace"
        config = root / "config"
        data = root / "data"
        workspace.mkdir()
        config.mkdir()
        data.mkdir()

        command = _command_prefix(args.command)
        command.extend(["--headless", "--bare", "--cwd", str(workspace)])
        requests = [
            {"type": "status", "request_id": "status-1"},
            {"type": "list_sessions", "request_id": "sessions-1"},
            {"type": "shutdown", "request_id": "shutdown-1"},
        ]
        env = os.environ.copy()
        env["OPENHARNESS_CONFIG_DIR"] = str(config)
        env["OPENHARNESS_DATA_DIR"] = str(data)
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            stdout, stderr = process.communicate(
                "".join(_json_line(request) for request in requests),
                timeout=args.timeout,
            )
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            print(stdout, file=sys.stdout, end="")
            print(stderr, file=sys.stderr, end="")
            print(f"headless smoke timed out after {args.timeout:.1f}s", file=sys.stderr)
            return 124

    if process.returncode != 0:
        print(stdout, file=sys.stdout, end="")
        print(stderr, file=sys.stderr, end="")
        print(f"headless smoke failed: process exited {process.returncode}", file=sys.stderr)
        return process.returncode

    try:
        events = _load_events(stdout)
        ready = _require_event(events, "process_ready")
        status = _require_event(events, "state_snapshot", "status-1")
        sessions = _require_event(events, "sessions", "sessions-1")
        shutdown = _require_event(events, "shutdown", "shutdown-1")
        assert ready.get("protocol_version") == 1
        assert status.get("busy") is False
        assert status.get("session_id") is None
        assert isinstance(sessions.get("sessions"), list)
        assert shutdown.get("session_id") == ""
    except Exception as exc:
        print(stdout, file=sys.stdout, end="")
        print(stderr, file=sys.stderr, end="")
        print(f"headless smoke failed: {exc}", file=sys.stderr)
        return 1

    print("headless smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
