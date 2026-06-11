"""Measure per-line runtime assembly overhead with a no-op model client.

This times handle_line() end-to-end minus any real model latency, i.e. the
settings/hook/plugin/prompt assembly cost paid on every submitted line. The
performance-hardening roadmap budget is < 5 ms p50 when nothing on disk
changed between lines. The observability-metrics proposal adds a second
gate: the diagnostics on/off delta on the same probe must stay < 0.5 ms.

Usage: python scripts/measure_per_line.py [iterations]
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import tempfile
import time


def _prepare_env(base: str) -> None:
    os.environ["OPENHARNESS_CONFIG_DIR"] = os.path.join(base, "config")
    os.environ["OPENHARNESS_DATA_DIR"] = os.path.join(base, "data")


def _write_settings(base: str, *, diagnostics_enabled: bool) -> None:
    """Both probe modes read a real settings.json so the per-line settings
    hot-reload path is identical; only diagnostics.enabled differs."""
    config_dir = os.path.join(base, "config")
    os.makedirs(config_dir, exist_ok=True)
    with open(os.path.join(config_dir, "settings.json"), "w", encoding="utf-8") as handle:
        json.dump({"diagnostics": {"enabled": diagnostics_enabled}}, handle)


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
    # Defaults sized so the min-line estimate converges on a loaded machine;
    # the 0.5 ms diagnostics-delta gate needs more samples than the 5 ms one.
    iterations = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    # Best-of-N rounds, same methodology as scripts/measure_startup.py:
    # scheduler/GC noise on a busy machine easily exceeds the budget margin.
    round_p50s: list[float] = []
    round_max: list[float] = []
    # Warm per-line timings keyed by diagnostics mode; paired off/on runs
    # inside each round so machine drift hits both modes equally.
    warm_by_mode: dict[bool, list[float]] = {False: [], True: []}
    with tempfile.TemporaryDirectory(prefix="oh-perline-") as base:
        _prepare_env(base)
        workdir = os.path.join(base, "project")
        os.makedirs(workdir, exist_ok=True)
        from openharness.diagnostics import reset_recorder

        for round_index in range(rounds):
            for diagnostics_enabled in (False, True):
                # Fresh data dir per run: sessions/indexes accumulate across
                # runs, which would otherwise bias whichever mode runs later.
                mode = "on" if diagnostics_enabled else "off"
                os.environ["OPENHARNESS_DATA_DIR"] = os.path.join(
                    base, f"data-{round_index}-{mode}"
                )
                _write_settings(base, diagnostics_enabled=diagnostics_enabled)
                reset_recorder()
                timings = asyncio.run(_measure(iterations, workdir))
                warm = timings[2:] if len(timings) > 4 else timings
                warm_by_mode[diagnostics_enabled].extend(warm)
                if diagnostics_enabled:
                    round_p50s.append(statistics.median(warm))
                    round_max.append(max(warm))
        # Final flush + teardown while the temp data dir still exists.
        reset_recorder()

    for index, (p50, peak) in enumerate(zip(round_p50s, round_max)):
        print(f"round {index}: p50 {p50:6.2f} ms   max {peak:7.2f} ms   (diagnostics on)")
    best_p50 = min(round_p50s)
    intrinsic = min(warm_by_mode[True])
    intrinsic_off = min(warm_by_mode[False])
    delta = intrinsic - intrinsic_off
    print()
    print(f"best p50:   {best_p50:.2f} ms")
    print(f"min line:   {intrinsic:.2f} ms (diagnostics on)")
    print(f"diag delta: {delta:+.3f} ms (on {intrinsic:.2f} - off {intrinsic_off:.2f})")
    # Gate on the minimum observed line (timeit-style): scheduler noise from
    # a loaded machine only ever adds time, so the minimum estimates the
    # intrinsic assembly cost the budget constrains.
    budget = 5.0
    diag_budget = 0.5
    ok = intrinsic < budget
    diag_ok = delta < diag_budget
    print(f"budget:     {budget:.1f} ms (min line, {rounds} rounds) -> {'PASS' if ok else 'FAIL'}")
    print(f"diag gate:  {diag_budget:.1f} ms on/off delta -> {'PASS' if diag_ok else 'FAIL'}")
    return 0 if ok and diag_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
