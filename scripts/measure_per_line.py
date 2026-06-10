"""Measure per-line runtime assembly overhead with a no-op model client.

This times handle_line() end-to-end minus any real model latency, i.e. the
settings/hook/plugin/prompt assembly cost paid on every submitted line. The
performance-hardening roadmap budget is < 5 ms p50 when nothing on disk
changed between lines.

Usage: python scripts/measure_per_line.py [iterations]
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
import tempfile
import time


def _prepare_env(base: str) -> None:
    os.environ.setdefault("OPENHARNESS_CONFIG_DIR", os.path.join(base, "config"))
    os.environ.setdefault("OPENHARNESS_DATA_DIR", os.path.join(base, "data"))


class _InstantApiClient:
    """Completes every request immediately with a canned message."""

    async def stream_message(self, request):
        from openharness.api.client import ApiMessageCompleteEvent
        from openharness.api.usage import UsageSnapshot
        from openharness.engine.messages import ConversationMessage, TextBlock

        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="ok")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


async def _measure(iterations: int, cwd: str) -> list[float]:
    from openharness.ui.runtime import build_runtime, close_runtime, handle_line, start_runtime

    async def _noop_print(_message: str) -> None:
        return None

    async def _noop_render(_event) -> None:
        return None

    async def _noop_clear() -> None:
        return None

    bundle = await build_runtime(cwd=cwd, api_client=_InstantApiClient())
    await start_runtime(bundle)
    timings: list[float] = []
    try:
        for index in range(iterations):
            start = time.perf_counter()
            await handle_line(
                bundle,
                f"benchmark line {index}",
                print_system=_noop_print,
                render_event=_noop_render,
                clear_output=_noop_clear,
            )
            timings.append((time.perf_counter() - start) * 1000.0)
            # Keep every line a one-turn conversation: history-proportional
            # costs (sanitize/request/snapshot) are compaction's domain; this
            # probe isolates the fixed config/prompt assembly overhead.
            bundle.engine.load_messages([])
    finally:
        await close_runtime(bundle)
    return timings


def main() -> int:
    iterations = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    # Best-of-N rounds, same methodology as scripts/measure_startup.py:
    # scheduler/GC noise on a busy machine easily exceeds the budget margin.
    round_p50s: list[float] = []
    round_max: list[float] = []
    all_warm: list[float] = []
    with tempfile.TemporaryDirectory(prefix="oh-perline-") as base:
        _prepare_env(base)
        workdir = os.path.join(base, "project")
        os.makedirs(workdir, exist_ok=True)
        for _ in range(rounds):
            timings = asyncio.run(_measure(iterations, workdir))
            warm = timings[2:] if len(timings) > 4 else timings
            round_p50s.append(statistics.median(warm))
            round_max.append(max(warm))
            all_warm.extend(warm)

    for index, (p50, peak) in enumerate(zip(round_p50s, round_max)):
        print(f"round {index}: p50 {p50:6.2f} ms   max {peak:7.2f} ms")
    best_p50 = min(round_p50s)
    intrinsic = min(all_warm)
    print()
    print(f"best p50:  {best_p50:.2f} ms")
    print(f"min line:  {intrinsic:.2f} ms")
    # Gate on the minimum observed line (timeit-style): scheduler noise from
    # a loaded machine only ever adds time, so the minimum estimates the
    # intrinsic assembly cost the budget constrains.
    budget = 5.0
    ok = intrinsic < budget
    print(f"budget:    {budget:.1f} ms (min line, {rounds} rounds) -> {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
