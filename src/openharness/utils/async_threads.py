"""Async helpers for blocking local work without the default executor."""

from __future__ import annotations

import asyncio
import queue
import threading
from typing import Any, Callable


async def run_sync_daemon_thread(
    fn: Callable[..., Any],
    *args: Any,
    name: str,
    timeout: float | None = None,
    **kwargs: Any,
) -> Any:
    """Run blocking ``fn`` on a daemon thread and await its result.

    This deliberately avoids ``asyncio.to_thread``/``run_in_executor`` for
    local persistence and headless fast paths. Default-executor workers are
    non-daemon, so a wedged executor handoff can hang ``asyncio.run()``
    teardown even after a caller-level timeout.
    """
    result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def _worker() -> None:
        try:
            result = fn(*args, **kwargs)
        except BaseException as exc:
            outcome = (False, exc)
        else:
            outcome = (True, result)
        result_queue.put(outcome)

    threading.Thread(target=_worker, name=name, daemon=True).start()
    deadline = None if timeout is None else asyncio.get_running_loop().time() + timeout
    polls = 0
    while True:
        try:
            ok, value = result_queue.get_nowait()
        except queue.Empty:
            if deadline is None:
                await asyncio.sleep(0 if polls < 200 else 0.001)
                polls += 1
                continue
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            await asyncio.sleep(0 if polls < 200 else min(0.001, remaining))
            polls += 1
            continue
        if ok:
            return value
        raise value
