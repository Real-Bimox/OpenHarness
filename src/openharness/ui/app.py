"""Interactive session entry points."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import time
import threading
from dataclasses import asdict
from typing import Any, TextIO

from openharness.coordinator.coordinator_mode import is_coordinator_mode

from openharness.api.client import SupportsStreamingMessages
from openharness.engine.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    CompactProgressEvent,
    ErrorEvent,
    StatusEvent,
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from openharness.ui.backend_host import run_backend_host
from openharness.ui.coordinator_drain import drain_coordinator_async_agents
from openharness.ui.headless_protocol import HEADLESS_PROTOCOL_VERSION, HeadlessRequest
from openharness.ui.react_launcher import launch_react_tui
from openharness.ui.runtime import (
    build_runtime,
    close_runtime,
    handle_line,
    save_runtime_snapshot,
    start_runtime,
)


_VALID_PRINT_OUTPUT_FORMATS = {"text", "json", "stream-json"}


def _decode_task_worker_line(raw: str) -> str:
    """Normalize one stdin line for the headless task worker.

    Task-manager driven agent workers receive either:
    - a plain text line (initial prompt or simple follow-up), or
    - a JSON object from ``send_message`` / teammate backends with a ``text`` field.
    """
    stripped = raw.strip()
    if not stripped:
        return ""
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped
    if isinstance(payload, dict):
        text = payload.get("text")
        if isinstance(text, str):
            return text.strip()
    return stripped


def _attach_session(payload: dict[str, Any], session_id: str | None) -> dict[str, Any]:
    if session_id:
        return {**payload, "session_id": session_id}
    return payload


def _stream_event_payload(event: StreamEvent, *, session_id: str | None = None) -> dict[str, Any] | None:
    """Convert engine stream events into stable JSONL event objects."""
    if isinstance(event, AssistantTextDelta):
        return _attach_session({"type": "assistant_delta", "text": event.text}, session_id)
    if isinstance(event, AssistantTurnComplete):
        return _attach_session(
            {
                "type": "assistant_complete",
                "text": event.message.text.strip(),
                "usage": event.usage.model_dump(),
            },
            session_id,
        )
    if isinstance(event, ToolExecutionStarted):
        return _attach_session(
            {"type": "tool_started", "tool_name": event.tool_name, "tool_input": event.tool_input},
            session_id,
        )
    if isinstance(event, ToolExecutionCompleted):
        return _attach_session(
            {
                "type": "tool_completed",
                "tool_name": event.tool_name,
                "output": event.output,
                "is_error": event.is_error,
            },
            session_id,
        )
    if isinstance(event, ErrorEvent):
        return _attach_session(
            {"type": "error", "message": event.message, "recoverable": event.recoverable},
            session_id,
        )
    if isinstance(event, CompactProgressEvent):
        return _attach_session(
            {
                "type": "compact_progress",
                "phase": event.phase,
                "trigger": event.trigger,
                "attempt": event.attempt,
                "message": event.message,
            },
            session_id,
        )
    if isinstance(event, StatusEvent):
        return _attach_session({"type": "status", "message": event.message}, session_id)
    return None


def _write_jsonl(stream: TextIO, payload: dict[str, Any]) -> None:
    print(json.dumps(payload), file=stream, flush=True)


def _headless_permission_allowed(permission_mode: str | None) -> bool:
    return permission_mode == "full_auto"


def _app_state_payload(bundle) -> dict[str, Any]:
    state = bundle.app_state.get()
    payload = asdict(state)
    payload["session_id"] = bundle.session_id
    return payload


async def run_repl(
    *,
    prompt: str | None = None,
    cwd: str | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    effort: str | None = None,
    base_url: str | None = None,
    system_prompt: str | None = None,
    api_key: str | None = None,
    api_format: str | None = None,
    api_client: SupportsStreamingMessages | None = None,
    backend_only: bool = False,
    restore_messages: list[dict] | None = None,
    restore_tool_metadata: dict[str, object] | None = None,
    session_id: str | None = None,
    append_system_prompt: str | None = None,
    permission_mode: str | None = None,
    allowed_tools: list[str] | None = None,
    denied_tools: list[str] | None = None,
    settings_source: str | None = None,
    mcp_server_configs: dict[str, object] | None = None,
    bare: bool = False,
    resume_session_id: str | None = None,
) -> None:
    """Run the default OpenHarness interactive application (React TUI)."""
    if backend_only:
        await run_backend_host(
            cwd=cwd,
            model=model,
            max_turns=max_turns,
            effort=effort,
            base_url=base_url,
            system_prompt=system_prompt,
            api_key=api_key,
            api_format=api_format,
            api_client=api_client,
            restore_messages=restore_messages,
            restore_tool_metadata=restore_tool_metadata,
            session_id=session_id,
            append_system_prompt=append_system_prompt,
            enforce_max_turns=max_turns is not None,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
            denied_tools=denied_tools,
            settings_source=settings_source,
            mcp_server_configs=mcp_server_configs,
            bare=bare,
        )
        return

    exit_code = await launch_react_tui(
        prompt=prompt,
        cwd=cwd,
        model=model,
        max_turns=max_turns,
        effort=effort,
        base_url=base_url,
        system_prompt=system_prompt,
        api_key=api_key,
        api_format=api_format,
        permission_mode=permission_mode,
        append_system_prompt=append_system_prompt,
        allowed_tools=allowed_tools,
        denied_tools=denied_tools,
        settings_source=settings_source,
        mcp_server_configs=mcp_server_configs,
        bare=bare,
        resume_session_id=resume_session_id or session_id,
    )
    if exit_code != 0:
        raise SystemExit(exit_code)


async def run_task_worker(
    *,
    cwd: str | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    effort: str | None = None,
    base_url: str | None = None,
    system_prompt: str | None = None,
    api_key: str | None = None,
    api_format: str | None = None,
    api_client: SupportsStreamingMessages | None = None,
    permission_mode: str | None = None,
    append_system_prompt: str | None = None,
    allowed_tools: list[str] | None = None,
    denied_tools: list[str] | None = None,
    settings_source: str | None = None,
    mcp_server_configs: dict[str, object] | None = None,
    bare: bool = False,
) -> None:
    """Run a stdin-driven headless worker for background agent tasks.

    This mode exists for subprocess teammates and other task-manager managed
    agent processes. It intentionally avoids the React TUI / Ink path so it
    can run without a controlling TTY.

    The worker is persistent: it keeps serving stdin lines until EOF, an
    idle timeout (``task_worker_idle_timeout_s``), or a terminating command.
    When the task manager set ``OPENHARNESS_TASK_SESSION_ID``, conversation
    state is restored from (and saved to) that session across restarts, so a
    crashed or idle-reaped worker resumes with its context intact.
    """

    async def _noninteractive_permission(tool_name: str, reason: str) -> bool:
        if _headless_permission_allowed(permission_mode):
            return True
        print(
            f"Permission denied for {tool_name}: {reason}",
            file=sys.stderr,
            flush=True,
        )
        return False

    async def _noop_ask(_question: str) -> str:
        return ""

    async def _print_system(message: str) -> None:
        print(message, flush=True)

    async def _render_event(event: StreamEvent) -> None:
        if isinstance(event, AssistantTextDelta):
            sys.stdout.write(event.text)
            sys.stdout.flush()
        elif isinstance(event, AssistantTurnComplete):
            sys.stdout.write("\n")
            sys.stdout.flush()
        elif isinstance(event, ErrorEvent):
            print(event.message, flush=True)
        elif isinstance(event, StatusEvent) and event.message:
            print(event.message, flush=True)

    async def _clear_output() -> None:
        return None

    # Restore conversation state for managed workers so a restart resumes
    # with full context instead of an empty conversation.
    task_session_id = os.environ.get("OPENHARNESS_TASK_SESSION_ID", "").strip() or None
    restore_messages: list[dict] | None = None
    restore_tool_metadata: dict[str, object] | None = None
    if task_session_id:
        from openharness.services.session_storage import load_session_by_id

        snapshot = load_session_by_id(cwd or ".", task_session_id)
        if snapshot is not None:
            raw_messages = snapshot.get("messages")
            raw_metadata = snapshot.get("tool_metadata")
            restore_messages = raw_messages if isinstance(raw_messages, list) else None
            restore_tool_metadata = raw_metadata if isinstance(raw_metadata, dict) else None

    bundle = await build_runtime(
        cwd=cwd,
        model=model,
        max_turns=max_turns,
        effort=effort,
        base_url=base_url,
        system_prompt=system_prompt,
        api_key=api_key,
        api_format=api_format,
        api_client=api_client,
        permission_prompt=_noninteractive_permission,
        ask_user_prompt=_noop_ask,
        enforce_max_turns=max_turns is not None,
        permission_mode=permission_mode,
        append_system_prompt=append_system_prompt,
        allowed_tools=allowed_tools,
        denied_tools=denied_tools,
        settings_source=settings_source,
        mcp_server_configs=mcp_server_configs,
        bare=bare,
        session_id=task_session_id,
        restore_messages=restore_messages,
        restore_tool_metadata=restore_tool_metadata,
    )
    await start_runtime(bundle)

    idle_timeout = getattr(bundle.current_settings(), "task_worker_idle_timeout_s", 600.0)
    line_queue: asyncio.Queue[str] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _stdin_reader() -> None:
        # Daemon thread: a blocking readline must never keep the executor
        # shutdown (and therefore process exit on idle timeout) waiting.
        while True:
            raw = sys.stdin.readline()
            try:
                loop.call_soon_threadsafe(line_queue.put_nowait, raw)
            except RuntimeError:
                return
            if raw == "":
                return

    threading.Thread(target=_stdin_reader, name="task-worker-stdin", daemon=True).start()
    try:
        while True:
            try:
                raw = await asyncio.wait_for(
                    line_queue.get(),
                    timeout=idle_timeout if idle_timeout and idle_timeout > 0 else None,
                )
            except asyncio.TimeoutError:
                # Idle: exit cleanly. The task manager transparently restarts
                # the worker (with restored context) on the next message.
                break
            if raw == "":
                break
            line = _decode_task_worker_line(raw)
            if not line:
                continue
            should_continue = await handle_line(
                bundle,
                line,
                print_system=_print_system,
                render_event=_render_event,
                clear_output=_clear_output,
            )
            if not should_continue:
                break
    finally:
        await close_runtime(bundle)


async def run_print_mode(
    *,
    prompt: str,
    output_format: str = "text",
    cwd: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    effort: str | None = None,
    system_prompt: str | None = None,
    append_system_prompt: str | None = None,
    api_key: str | None = None,
    api_format: str | None = None,
    api_client: SupportsStreamingMessages | None = None,
    permission_mode: str | None = None,
    max_turns: int | None = None,
    restore_messages: list[dict] | None = None,
    restore_tool_metadata: dict[str, object] | None = None,
    session_id: str | None = None,
    allowed_tools: list[str] | None = None,
    denied_tools: list[str] | None = None,
    settings_source: str | None = None,
    mcp_server_configs: dict[str, object] | None = None,
    bare: bool = False,
) -> int:
    """Non-interactive mode: submit prompt, stream output, exit.

    Returns a process exit code: 0 on success, 1 when any engine error
    occurred, so local orchestrators can rely on exit status.
    """
    if output_format not in _VALID_PRINT_OUTPUT_FORMATS:
        raise ValueError("output_format must be text, json, or stream-json")

    collected_text = ""
    events_list: list[dict] = []
    errors: list[str] = []
    permission_denials: list[dict[str, str]] = []
    system_messages: list[str] = []
    session_ref = {"session_id": session_id or ""}

    async def _noninteractive_permission(tool_name: str, reason: str) -> bool:
        if _headless_permission_allowed(permission_mode):
            return True
        permission_denials.append({"tool_name": tool_name, "reason": reason})
        obj = _attach_session(
            {"type": "permission_denied", "tool_name": tool_name, "reason": reason},
            session_ref["session_id"],
        )
        if output_format == "stream-json":
            _write_jsonl(sys.stdout, obj)
            events_list.append(obj)
        elif output_format == "text":
            print(
                f"Permission denied for {tool_name}: {reason}",
                file=sys.stderr,
                flush=True,
            )
        return False

    async def _noop_ask(question: str) -> str:
        return ""

    bundle = await build_runtime(
        prompt=prompt,
        cwd=cwd,
        model=model,
        max_turns=max_turns,
        effort=effort,
        base_url=base_url,
        system_prompt=system_prompt,
        api_key=api_key,
        api_format=api_format,
        enforce_max_turns=True,
        api_client=api_client,
        permission_prompt=_noninteractive_permission,
        ask_user_prompt=_noop_ask,
        restore_messages=restore_messages,
        restore_tool_metadata=restore_tool_metadata,
        session_id=session_id,
        permission_mode=permission_mode,
        append_system_prompt=append_system_prompt,
        allowed_tools=allowed_tools,
        denied_tools=denied_tools,
        settings_source=settings_source,
        mcp_server_configs=mcp_server_configs,
        bare=bare,
    )
    session_ref["session_id"] = bundle.session_id
    await start_runtime(bundle)

    try:
        async def _print_system(message: str) -> None:
            system_messages.append(message)
            if output_format == "text":
                print(message, file=sys.stderr)
            elif output_format == "stream-json":
                obj = _attach_session({"type": "system", "message": message}, bundle.session_id)
                _write_jsonl(sys.stdout, obj)
                events_list.append(obj)

        async def _render_event(event: StreamEvent) -> None:
            nonlocal collected_text
            if isinstance(event, AssistantTextDelta):
                collected_text += event.text
                if output_format == "text":
                    sys.stdout.write(event.text)
                    sys.stdout.flush()
                elif output_format == "stream-json":
                    obj = _stream_event_payload(event, session_id=bundle.session_id)
                    assert obj is not None
                    _write_jsonl(sys.stdout, obj)
                    events_list.append(obj)
            elif isinstance(event, AssistantTurnComplete):
                if not collected_text and event.message.text:
                    collected_text = event.message.text
                if output_format == "text":
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                elif output_format == "stream-json":
                    obj = _stream_event_payload(event, session_id=bundle.session_id)
                    assert obj is not None
                    _write_jsonl(sys.stdout, obj)
                    events_list.append(obj)
            elif isinstance(event, ToolExecutionStarted):
                if output_format == "stream-json":
                    obj = _stream_event_payload(event, session_id=bundle.session_id)
                    assert obj is not None
                    _write_jsonl(sys.stdout, obj)
                    events_list.append(obj)
            elif isinstance(event, ToolExecutionCompleted):
                if output_format == "stream-json":
                    obj = _stream_event_payload(event, session_id=bundle.session_id)
                    assert obj is not None
                    _write_jsonl(sys.stdout, obj)
                    events_list.append(obj)
            elif isinstance(event, ErrorEvent):
                errors.append(event.message)
                if output_format == "text":
                    print(event.message, file=sys.stderr)
                elif output_format == "stream-json":
                    obj = _stream_event_payload(event, session_id=bundle.session_id)
                    assert obj is not None
                    _write_jsonl(sys.stdout, obj)
                    events_list.append(obj)
            elif isinstance(event, CompactProgressEvent):
                if output_format == "text" and event.message:
                    print(event.message, file=sys.stderr)
                elif output_format == "stream-json":
                    obj = _stream_event_payload(event, session_id=bundle.session_id)
                    assert obj is not None
                    _write_jsonl(sys.stdout, obj)
                    events_list.append(obj)
            elif isinstance(event, StatusEvent):
                if output_format == "text":
                    print(event.message, file=sys.stderr)
                elif output_format == "stream-json":
                    obj = _stream_event_payload(event, session_id=bundle.session_id)
                    assert obj is not None
                    _write_jsonl(sys.stdout, obj)
                    events_list.append(obj)

        async def _clear_output() -> None:
            pass

        await handle_line(
            bundle,
            prompt,
            print_system=_print_system,
            render_event=_render_event,
            clear_output=_clear_output,
        )
        if is_coordinator_mode():
            await drain_coordinator_async_agents(
                bundle,
                prompt_seed=prompt,
                print_system=_print_system,
                render_event=_render_event,
                announce_waiting=output_format == "text",
            )

        if output_format == "stream-json":
            obj = {
                "type": "line_complete",
                "session_id": bundle.session_id,
                "usage": bundle.engine.total_usage.model_dump(),
            }
            _write_jsonl(sys.stdout, obj)
            events_list.append(obj)
        if output_format == "json":
            result = {
                "type": "result",
                "session_id": bundle.session_id,
                "text": collected_text.strip(),
                "is_error": bool(errors),
                "errors": errors,
                "permission_denials": permission_denials,
                "system_messages": system_messages,
                "usage": bundle.engine.total_usage.model_dump(),
            }
            print(json.dumps(result))
    finally:
        await close_runtime(bundle)
    return 1 if errors else 0


async def run_headless_control(
    *,
    cwd: str | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    effort: str | None = None,
    base_url: str | None = None,
    system_prompt: str | None = None,
    api_key: str | None = None,
    api_format: str | None = None,
    api_client: SupportsStreamingMessages | None = None,
    permission_mode: str | None = None,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    append_system_prompt: str | None = None,
    allowed_tools: list[str] | None = None,
    denied_tools: list[str] | None = None,
    settings_source: str | None = None,
    mcp_server_configs: dict[str, object] | None = None,
    bare: bool = False,
) -> None:
    """Run the local JSONL headless control protocol over stdin/stdout.

    Requests are processed sequentially in FIFO order; ``status``,
    ``list_sessions``, and ``interrupt`` are answered immediately by the stdin
    reader even while a turn is active. The process hosts at most one live
    session at a time: ``resume``/``continue`` replace the active session.
    """
    from openharness.services.session_storage import (
        list_session_snapshots,
        load_session_by_id,
        load_session_snapshot,
    )

    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    bundle = None
    stdin_reader: asyncio.StreamReader | None = None
    if input_stream is sys.stdin:
        try:
            stdin_reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(stdin_reader)
            loop = asyncio.get_running_loop()
            await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        except (AttributeError, NotImplementedError, OSError, ValueError):
            stdin_reader = None
    raw_line_queue: asyncio.Queue[str] = asyncio.Queue()
    if stdin_reader is None:
        # Fallback input (injected streams, non-pipe platforms): pump lines
        # from a daemon thread so a blocking readline can never stall the
        # event loop. Deliberately a plain thread, not the default executor
        # (the v0.1.17 hang showed executor handoff itself can fail).
        pump_loop = asyncio.get_running_loop()

        def _stdin_pump() -> None:
            while True:
                try:
                    pumped = input_stream.readline()
                except Exception:
                    pumped = ""
                try:
                    pump_loop.call_soon_threadsafe(raw_line_queue.put_nowait, pumped)
                except RuntimeError:
                    return
                if pumped == "":
                    return

        threading.Thread(target=_stdin_pump, name="headless-stdin", daemon=True).start()
    request_queue: asyncio.Queue[HeadlessRequest] = asyncio.Queue()
    write_lock = asyncio.Lock()
    current_request_id: str | None = None
    active_request_id: str | None = None
    active_request_task: asyncio.Task[bool] | None = None
    rebuilding = False
    force_shutdown = False

    async def _emit(payload: dict[str, Any], *, request_id: str | None = None) -> None:
        if request_id:
            payload = {**payload, "request_id": request_id}
        async with write_lock:
            _write_jsonl(output_stream, payload)

    async def _error(message: str, *, request_id: str | None = None, recoverable: bool = True) -> None:
        await _emit({"type": "error", "message": message, "recoverable": recoverable}, request_id=request_id)

    def _session_lookup_cwd() -> str:
        return bundle.cwd if bundle is not None else (cwd or ".")

    async def _permission(tool_name: str, reason: str) -> bool:
        if _headless_permission_allowed(permission_mode):
            return True
        payload = {"type": "permission_denied", "tool_name": tool_name, "reason": reason}
        if bundle is not None:
            payload["session_id"] = bundle.session_id
        await _emit(payload, request_id=current_request_id)
        return False

    async def _ask_user(question: str) -> str:
        payload = {
            "type": "error",
            "message": "ask_user is unavailable in headless mode",
            "recoverable": True,
            "question": question,
        }
        if bundle is not None:
            payload["session_id"] = bundle.session_id
        await _emit(payload, request_id=current_request_id)
        return ""

    async def _start_bundle(
        snapshot: dict[str, Any] | None = None,
        *,
        request_id: str | None = None,
    ) -> None:
        nonlocal bundle
        if bundle is not None:
            previous, bundle = bundle, None
            await close_runtime(previous)
        session_id = None
        restore_messages = None
        restore_tool_metadata = None
        resolved_model = model
        if snapshot is not None:
            session_id = snapshot.get("session_id")
            restore_messages = snapshot.get("messages")
            restore_tool_metadata = snapshot.get("tool_metadata")
            # Explicit CLI --model wins over the model stored in the snapshot.
            resolved_model = model or snapshot.get("model")
        bundle = await build_runtime(
            cwd=cwd,
            model=resolved_model,
            max_turns=max_turns,
            effort=effort,
            base_url=base_url,
            system_prompt=system_prompt,
            api_key=api_key,
            api_format=api_format,
            api_client=api_client,
            permission_prompt=_permission,
            ask_user_prompt=_ask_user,
            restore_messages=restore_messages,
            restore_tool_metadata=restore_tool_metadata,
            session_id=session_id if isinstance(session_id, str) and session_id else None,
            enforce_max_turns=max_turns is not None,
            permission_mode=permission_mode,
            append_system_prompt=append_system_prompt,
            allowed_tools=allowed_tools,
            denied_tools=denied_tools,
            settings_source=settings_source,
            mcp_server_configs=mcp_server_configs,
            bare=bare,
        )
        await start_runtime(bundle)
        payload = {
            "type": "ready",
            "protocol_version": HEADLESS_PROTOCOL_VERSION,
            "session_id": bundle.session_id,
        }
        if snapshot is not None:
            payload["resumed"] = True
        await _emit(payload, request_id=request_id)

    async def _print_system(message: str) -> None:
        payload = {"type": "system", "message": message}
        if bundle is not None:
            payload["session_id"] = bundle.session_id
        await _emit(payload, request_id=current_request_id)

    async def _render_event(event: StreamEvent) -> None:
        payload = _stream_event_payload(event, session_id=bundle.session_id if bundle is not None else None)
        if payload is not None:
            await _emit(payload, request_id=current_request_id)

    async def _clear_output() -> None:
        payload = {"type": "clear_transcript"}
        if bundle is not None:
            payload["session_id"] = bundle.session_id
        await _emit(payload, request_id=current_request_id)

    def _line_complete_payload() -> dict[str, Any]:
        payload: dict[str, Any] = {"type": "line_complete", "session_id": bundle.session_id}
        usage = getattr(bundle.engine, "total_usage", None)
        if usage is not None:
            payload["usage"] = usage.model_dump()
        return payload

    async def _save_interrupt_snapshot() -> None:
        """Persist the session after a cancelled turn so resume keeps the exchange."""
        if bundle is None:
            return
        try:
            await save_runtime_snapshot(bundle, system_prompt=bundle.engine.system_prompt)
        except Exception:
            pass

    async def _submit(line: str, *, request_id: str | None) -> bool:
        if bundle is None:
            await _start_bundle(request_id=request_id)
        if bundle is None:
            await _error("Headless runtime is not ready", request_id=request_id)
            return True
        should_continue = await handle_line(
            bundle,
            line,
            print_system=_print_system,
            render_event=_render_event,
            clear_output=_clear_output,
        )
        await _emit(_line_complete_payload(), request_id=request_id)
        if not should_continue:
            await _emit({"type": "shutdown", "session_id": bundle.session_id}, request_id=request_id)
        return should_continue

    async def _emit_sessions(request_id: str | None) -> None:
        sessions = list_session_snapshots(_session_lookup_cwd(), limit=20)
        await _emit({"type": "sessions", "sessions": sessions}, request_id=request_id)

    async def _emit_skill_loop_status(request_id: str | None) -> None:
        """Report skill telemetry, pending writes, and last curator run."""

        def _run() -> dict[str, Any]:
            from openharness.services.skill_approval import list_pending
            from openharness.services.skill_curator import load_state
            from openharness.skills.usage import load_records

            records = load_records()
            return {
                "skills": {
                    name: {
                        "state": rec.get("state", "active"),
                        "use_count": rec.get("use_count", 0),
                        "patch_count": rec.get("patch_count", 0),
                        "pinned": bool(rec.get("pinned")),
                        "agent_created": rec.get("created_by") == "agent",
                    }
                    for name, rec in records.items()
                },
                "pending_writes": len(list_pending()),
                "curator": load_state().get("last_report", {}),
            }

        try:
            payload = await asyncio.to_thread(_run)
        except Exception as exc:
            await _error(f"skill_loop_status failed: {exc}", request_id=request_id)
            return
        await _emit({"type": "skill_loop_status", **payload}, request_id=request_id)

    async def _emit_session_search(request: HeadlessRequest) -> None:
        """Answer a search_sessions request from the derived index (read-only)."""
        from openharness.services.conversation_index import (
            INDEX_DISABLED_MESSAGE,
            get_conversation_index,
            index_enabled,
        )

        request_id = request.effective_request_id
        if not index_enabled():
            await _error(INDEX_DISABLED_MESSAGE, request_id=request_id)
            return
        active = bundle.session_id if bundle is not None else None

        def _run() -> dict[str, Any]:
            index = get_conversation_index()
            project = request.project or _session_lookup_cwd()
            if request.session_id and request.around_message_id is not None:
                return {"mode": "scroll", **index.around(
                    request.session_id, request.around_message_id, window=request.window or 5
                )}
            if request.session_id:
                return {"mode": "read", **index.read_session(request.session_id)}
            if not (request.query or "").strip():
                return {"mode": "browse", **index.browse(
                    project=project, limit=request.limit or 10, exclude_session=active
                )}
            roles = (
                [part.strip() for part in request.role_filter.split(",") if part.strip()]
                if request.role_filter
                else None
            )
            return {"mode": "discover", **index.search(
                request.query or "",
                project=project,
                limit=request.limit or 3,
                sort=request.sort,
                role_filter=roles,
                exclude_session=active,
            )}

        try:
            result = _run()
        except Exception as exc:
            await _error(f"Session search failed: {exc}", request_id=request_id)
            return
        if "error" in result:
            await _error(str(result["error"]), request_id=request_id)
            return
        await _emit({"type": "session_search_results", **result}, request_id=request_id)

    def _is_busy() -> bool:
        return rebuilding or (active_request_task is not None and not active_request_task.done())

    async def _emit_status(request_id: str | None) -> None:
        payload: dict[str, Any] = {
            "type": "state_snapshot",
            "protocol_version": HEADLESS_PROTOCOL_VERSION,
            "session_id": None,
            "state": None,
            "busy": _is_busy(),
        }
        if bundle is not None:
            payload["session_id"] = bundle.session_id
            payload["state"] = _app_state_payload(bundle)
            usage = getattr(bundle.engine, "total_usage", None)
            if usage is not None:
                payload["usage"] = usage.model_dump()
        await _emit(payload, request_id=request_id)

    async def _emit_diagnostics_snapshot(request: HeadlessRequest) -> None:
        """Answer a diagnostics request (additive; observability-metrics §6)."""
        from openharness.diagnostics import context as diag_context
        from openharness.diagnostics import get_recorder
        from openharness.diagnostics.snapshot import (
            build_status,
            read_events,
            recent_errors,
            summarize_events,
            thread_executor_probe_async,
        )

        request_id = request.effective_request_id
        payload: dict[str, Any] = {
            "type": "diagnostics_snapshot",
            "run_id": diag_context.run_id(),
        }
        get_recorder().flush()
        if (request.scope or "summary") == "status":
            status = build_status()
            status["executor_probe"] = await thread_executor_probe_async()
            payload["status"] = status
            payload["summary"] = status["summary"]
            payload["recent_errors"] = status["recent_errors"]
            payload["recorder"] = status["recorder"]
        else:
            events = read_events(since_seconds=3600.0)
            payload["summary"] = summarize_events(events, window_seconds=3600.0)
            payload["recent_errors"] = recent_errors(events)
            payload["recorder"] = get_recorder().health()
        await _emit(payload, request_id=request_id)

    async def _run_active_request(awaitable, *, request_id: str | None) -> bool:
        nonlocal active_request_id, active_request_task, current_request_id
        task = asyncio.create_task(awaitable)
        active_request_task = task
        active_request_id = request_id
        current_request_id = request_id
        try:
            return await task
        except asyncio.CancelledError:
            if not task.cancelled():
                # run_headless_control itself was cancelled (e.g. SIGINT
                # teardown); propagate instead of treating it as an interrupt.
                task.cancel()
                raise
            payload = {"type": "interrupted"}
            if bundle is not None:
                payload["session_id"] = bundle.session_id
                await _save_interrupt_snapshot()
            await _emit(payload, request_id=request_id)
            if bundle is not None:
                await _emit(_line_complete_payload(), request_id=request_id)
            return True
        finally:
            if active_request_task is task:
                active_request_task = None
                active_request_id = None
                current_request_id = None

    async def _interrupt_active_request(request_id: str | None) -> None:
        from openharness.diagnostics import record as _diag_int_record

        _diag_int_record(
            "headless", "interrupt", "completed",
            attrs={"active": active_request_task is not None and not active_request_task.done()},
            request_id=request_id,
        )
        if active_request_task is None or active_request_task.done():
            await _emit({"type": "interrupted", "active": False}, request_id=request_id)
            return
        active_request_task.cancel()
        await _emit(
            {"type": "interrupting", "active": True, "active_request_id": active_request_id},
            request_id=request_id,
        )

    async def _restore_session(request: HeadlessRequest, *, request_id: str | None) -> bool:
        """Load a snapshot and rebuild the runtime; returns True on success."""
        nonlocal rebuilding
        session_id = (request.session_id or "").strip()
        if request.type == "resume" and not session_id:
            await _error("resume requires a non-empty session_id", request_id=request_id)
            return False
        rebuilding = True
        try:
            if session_id:
                snapshot = load_session_by_id(_session_lookup_cwd(), session_id)
            else:
                snapshot = load_session_snapshot(_session_lookup_cwd())
            if snapshot is None:
                message = (
                    f"Session not found: {session_id}"
                    if session_id
                    else "No previous session found in this directory."
                )
                await _error(message, request_id=request_id)
                return False
            await _start_bundle(snapshot, request_id=request_id)
            return True
        except Exception as exc:
            await _error(f"Failed to restore session: {exc}", request_id=request_id)
            return False
        finally:
            rebuilding = False

    async def _read_requests() -> None:
        nonlocal force_shutdown
        while True:
            try:
                if stdin_reader is not None:
                    raw_bytes = await stdin_reader.readline()
                    raw = raw_bytes.decode("utf-8", errors="replace")
                else:
                    raw = await raw_line_queue.get()
            except Exception as exc:
                with contextlib.suppress(Exception):
                    await _error(f"stdin read failed: {exc}", recoverable=False)
                await request_queue.put(HeadlessRequest(type="shutdown"))
                return
            if raw == "":
                await request_queue.put(HeadlessRequest(type="shutdown"))
                return
            raw = raw.strip()
            if not raw:
                continue
            try:
                request = HeadlessRequest.model_validate_json(raw)
            except Exception as exc:
                from openharness.diagnostics import record as _diag_parse_record

                _diag_parse_record(
                    "headless", "request_parse", "failed", level="warning", status="error",
                    attrs={"reason": "invalid_json"},
                )
                await _error(f"Invalid request: {exc}", recoverable=True)
                continue
            # External correlation id (if any) flows into diagnostics events
            # only; it is never used for protocol routing.
            _diag_context.correlation_id_var.set(request.correlation_id)
            # Answer read-only and interrupt requests immediately, even while a
            # turn is active; everything else is queued FIFO. Guard so a
            # handling failure can never silently kill stdin processing.
            fast_start = time.monotonic()

            def _record_fast_request() -> None:
                from openharness.diagnostics import record as _diag_fast_record

                _diag_fast_record(
                    "headless",
                    "request",
                    "completed",
                    duration_ms=(time.monotonic() - fast_start) * 1000.0,
                    request_id=request.effective_request_id,
                    attrs={"request_type": request.type},
                )

            try:
                if request.type == "list_sessions":
                    with _watchdog.track("headless_list", request_id=request.effective_request_id):
                        await _emit_sessions(request.effective_request_id)
                    _record_fast_request()
                    continue
                if request.type == "search_sessions":
                    with _watchdog.track("headless_search", request_id=request.effective_request_id):
                        await _emit_session_search(request)
                    _record_fast_request()
                    continue
                if request.type == "skill_loop_status":
                    await _emit_skill_loop_status(request.effective_request_id)
                    _record_fast_request()
                    continue
                if request.type == "status":
                    with _watchdog.track("headless_status", request_id=request.effective_request_id):
                        await _emit_status(request.effective_request_id)
                    _record_fast_request()
                    continue
                if request.type == "diagnostics":
                    await _emit_diagnostics_snapshot(request)
                    _record_fast_request()
                    continue
                if request.type == "interrupt":
                    await _interrupt_active_request(request.effective_request_id)
                    _record_fast_request()
                    continue
                if request.type == "shutdown" and request.force:
                    # Forced shutdown cancels active work; plain shutdown
                    # drains the queue and lets the active turn finish.
                    force_shutdown = True
                    if active_request_task is not None and not active_request_task.done():
                        active_request_task.cancel()
            except Exception as exc:
                with contextlib.suppress(Exception):
                    await _error(f"Request handling failed: {exc}", request_id=request.effective_request_id)
                continue
            await request_queue.put(request)

    from openharness.diagnostics import context as _diag_context
    from openharness.diagnostics import record as _diag_record
    from openharness.diagnostics import watchdog as _watchdog
    from openharness.diagnostics.runinfo import write_current_run
    from openharness.diagnostics.snapshot import thread_executor_probe_async

    write_current_run("headless")
    _diag_record("headless", "process", "started", attrs={"mode": "headless"})
    _watchdog.start_watchdog("headless")
    await _emit({"type": "process_ready", "protocol_version": HEADLESS_PROTOCOL_VERSION})
    # Startup executor probe (diagnostic only, never load-bearing): detects
    # environments where to_thread/executor handoff is unsafe.
    probe_task = asyncio.create_task(thread_executor_probe_async())
    reader = asyncio.create_task(_read_requests())
    try:
        while True:
            request = await request_queue.get()
            request_id = request.effective_request_id
            _diag_context.correlation_id_var.set(request.correlation_id)
            _request_start = time.monotonic()
            _request_type = request.type
            if force_shutdown and request.type != "shutdown":
                await _error("Process is shutting down", request_id=request_id, recoverable=False)
                continue
            def _record_request(status: str = "ok") -> None:
                _diag_record(
                    "headless",
                    "request",
                    "completed" if status == "ok" else "failed",
                    status=status,
                    duration_ms=(time.monotonic() - _request_start) * 1000.0,
                    request_id=request_id,
                    attrs={"request_type": _request_type},
                )

            if request.type in {"submit", "submit_line"}:
                line = request.submitted_text
                if not line:
                    await _error("submit requires a non-empty prompt or line", request_id=request_id)
                    continue
                requested_session = (request.session_id or "").strip()
                if requested_session:
                    if bundle is None:
                        await _error(
                            f"No active session; send a resume request for {requested_session} first",
                            request_id=request_id,
                        )
                        continue
                    if requested_session != bundle.session_id:
                        await _error(
                            f"session_id mismatch: active session is {bundle.session_id}",
                            request_id=request_id,
                        )
                        continue
                with _watchdog.track("headless_submit", request_id=request_id):
                    should_continue = await _run_active_request(
                        _submit(line, request_id=request_id), request_id=request_id
                    )
                _record_request()
                if not should_continue:
                    break
            elif request.type in {"resume", "continue"}:
                if not await _restore_session(request, request_id=request_id):
                    continue
                # A force shutdown may have arrived while the runtime was
                # rebuilding; do not start a new turn afterwards.
                if force_shutdown:
                    await _error("Process is shutting down", request_id=request_id, recoverable=False)
                    continue
                line = request.submitted_text
                if line and not await _run_active_request(_submit(line, request_id=request_id), request_id=request_id):
                    break
            elif request.type == "permission_response":
                await _error(
                    "permission_response is not used by this deterministic headless mode",
                    request_id=request_id,
                )
            elif request.type == "shutdown":
                # Reject anything still queued behind the shutdown so every
                # request_id gets a response instead of silently vanishing.
                while not request_queue.empty():
                    pending = request_queue.get_nowait()
                    if pending.type != "shutdown":
                        await _error(
                            "Process is shutting down",
                            request_id=pending.effective_request_id,
                            recoverable=False,
                        )
                await _emit(
                    {"type": "shutdown", "session_id": bundle.session_id if bundle is not None else ""},
                    request_id=request_id,
                )
                _record_request()
                break
    finally:
        probe_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await probe_task
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader
        _watchdog.stop_watchdog()
        if bundle is not None:
            await close_runtime(bundle)
