#!/usr/bin/env python3
"""Measure common OpenHarness startup paths.

This intentionally records elapsed wall time only. Use it as a lightweight
before/after guardrail for import and CLI startup changes without depending on
platform-specific memory tooling.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Probe:
    name: str
    command: list[str]


def _probes(python: str) -> list[Probe]:
    bin_dir = Path(python).expanduser().absolute().parent
    oh = str(bin_dir / "oh")
    ohmo = str(bin_dir / "ohmo")
    return [
        Probe("import openharness.tools", [python, "-c", "import openharness.tools"]),
        Probe(
            "import openharness.commands.registry",
            [python, "-c", "import openharness.commands.registry"],
        ),
        Probe(
            "create_default_tool_registry",
            [
                python,
                "-c",
                "from openharness.tools import create_default_tool_registry; "
                "create_default_tool_registry(include_network_tools=False)",
            ],
        ),
        Probe(
            "create_default_command_registry",
            [
                python,
                "-c",
                "from openharness.commands.registry import create_default_command_registry; "
                "create_default_command_registry()",
            ],
        ),
        Probe("oh --help", [oh, "--help"]),
        Probe("ohmo --help", [ohmo, "--help"]),
    ]


def _run_probe(probe: Probe, repeats: int) -> tuple[str, list[float]]:
    timings: list[float] = []
    last_status = "ok"
    for _ in range(repeats):
        start = time.perf_counter()
        try:
            completed = subprocess.run(
                probe.command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except FileNotFoundError:
            return "missing", timings or [0.0]
        timings.append(time.perf_counter() - start)
        last_status = "ok" if completed.returncode == 0 else f"rc={completed.returncode}"
    return last_status, timings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="Python executable to use")
    parser.add_argument("--repeats", type=int, default=3, help="Probe repeat count")
    args = parser.parse_args()

    repeats = max(1, args.repeats)
    print(f"{'probe':40} {'status':>6} {'best':>9} {'avg':>9}")
    for probe in _probes(args.python):
        status, timings = _run_probe(probe, repeats)
        best = min(timings)
        avg = sum(timings) / len(timings)
        print(f"{probe.name:40} {status:>6} {best:8.3f}s {avg:8.3f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
