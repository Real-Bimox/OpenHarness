"""Tests for the React terminal launcher path."""

from __future__ import annotations

import asyncio
import pytest
from types import SimpleNamespace

from openharness.ui.app import run_print_mode, run_repl, run_task_worker
from openharness.engine.stream_events import AssistantTurnComplete
from openharness.engine.messages import ConversationMessage, TextBlock
from openharness.ui.react_launcher import build_backend_command


class _AsyncIterator:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


def test_build_backend_command_includes_flags():
    command = build_backend_command(
        cwd="/tmp/demo",
        model="kimi-k2.5",
        base_url="https://api.moonshot.cn/anthropic",
        system_prompt="system",
        api_key="secret",
        append_system_prompt="extra",
        allowed_tools=["read_file"],
        denied_tools=["bash"],
        settings_source='{"model":"test-model"}',
        bare=True,
        resume_session_id="saved123",
    )
    assert command[:3] == [command[0], "-m", "openharness"]
    assert "--backend-only" in command
    assert "--cwd" in command
    assert "--model" in command
    assert "--base-url" in command
    assert "--system-prompt" in command
    assert "--api-key" in command
    assert "--append-system-prompt" in command
    assert "--allowed-tools" in command
    assert "--disallowed-tools" in command
    assert "--settings" in command
    assert "--bare" in command
    assert "--resume" in command
    assert "saved123" in command


@pytest.mark.asyncio
async def test_run_repl_uses_react_launcher_by_default(monkeypatch):
    seen = {}

    async def _launch(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr("openharness.ui.app.launch_react_tui", _launch)
    await run_repl(prompt="hi", cwd="/tmp/demo", model="kimi-k2.5")

    assert seen["prompt"] == "hi"
    assert seen["cwd"] == "/tmp/demo"
    assert seen["model"] == "kimi-k2.5"


@pytest.mark.asyncio
async def test_run_print_mode_passes_cwd_to_build_runtime(monkeypatch):
    seen = {}

    async def _build_runtime(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            app_state=SimpleNamespace(get=lambda: None),
            mcp_manager=SimpleNamespace(list_statuses=lambda: []),
            commands=SimpleNamespace(list_commands=lambda: []),
            events=_AsyncIterator(),
            session_id="s1",
        )

    async def _start_runtime(_bundle):
        return None

    async def _handle_line(*_args, **_kwargs):
        return None

    async def _close_runtime(_bundle):
        return None

    monkeypatch.setattr("openharness.ui.app.build_runtime", _build_runtime)
    monkeypatch.setattr("openharness.ui.app.start_runtime", _start_runtime)
    monkeypatch.setattr("openharness.ui.app.handle_line", _handle_line)
    monkeypatch.setattr("openharness.ui.app.close_runtime", _close_runtime)

    await run_print_mode(prompt="hi", cwd="/tmp/demo")

    assert seen["cwd"] == "/tmp/demo"


@pytest.mark.asyncio
async def test_run_task_worker_reads_one_shot_json_line(monkeypatch):
    seen = []

    class _FakeStdin:
        def __init__(self):
            self._lines = iter([
                '{"text":"follow up from coordinator","from":"coordinator"}\n',
            ])

        def readline(self):
            return next(self._lines, "")

    async def _build_runtime(**kwargs):
        return SimpleNamespace(
            cwd=kwargs.get("cwd"),
            engine=SimpleNamespace(),
            external_api_client=False,
            extra_skill_dirs=(),
            extra_plugin_roots=(),
            current_settings=lambda: None,
            current_plugins=lambda: [],
            hook_summary=lambda: "",
            plugin_summary=lambda: "",
            mcp_summary=lambda: "",
            app_state=SimpleNamespace(set=lambda **_kwargs: None),
            mcp_manager=SimpleNamespace(close=lambda: None, list_statuses=lambda: []),
            hook_executor=SimpleNamespace(execute=lambda *_args, **_kwargs: None, update_registry=lambda *_a, **_k: None),
            commands=SimpleNamespace(lookup=lambda _line: None),
            session_backend=SimpleNamespace(save_snapshot=lambda **_kwargs: None),
            enforce_max_turns=False,
            session_id="s1",
        )

    async def _start_runtime(_bundle):
        return None

    async def _handle_line(bundle, line, **kwargs):
        del bundle, kwargs
        seen.append(line)
        return True

    async def _close_runtime(_bundle):
        return None

    monkeypatch.setattr("openharness.ui.app.build_runtime", _build_runtime)
    monkeypatch.setattr("openharness.ui.app.start_runtime", _start_runtime)
    monkeypatch.setattr("openharness.ui.app.handle_line", _handle_line)
    monkeypatch.setattr("openharness.ui.app.close_runtime", _close_runtime)
    monkeypatch.setattr("openharness.ui.app.sys.stdin", _FakeStdin())

    await run_task_worker(cwd="/tmp/demo")

    assert seen == ["follow up from coordinator"]


@pytest.mark.asyncio
async def test_run_task_worker_decodes_multiline_json_payload(monkeypatch):
    seen = []

    class _FakeStdin:
        def __init__(self):
            self._lines = iter([
                '{"text":"line 1\\nline 2\\nline 3","from":"coordinator"}\n',
            ])

        def readline(self):
            return next(self._lines, "")

    async def _build_runtime(**kwargs):
        return SimpleNamespace(
            cwd=kwargs.get("cwd"),
            engine=SimpleNamespace(),
            external_api_client=False,
            extra_skill_dirs=(),
            extra_plugin_roots=(),
            current_settings=lambda: None,
            current_plugins=lambda: [],
            hook_summary=lambda: "",
            plugin_summary=lambda: "",
            mcp_summary=lambda: "",
            app_state=SimpleNamespace(set=lambda **_kwargs: None),
            mcp_manager=SimpleNamespace(close=lambda: None, list_statuses=lambda: []),
            hook_executor=SimpleNamespace(execute=lambda *_args, **_kwargs: None, update_registry=lambda *_a, **_k: None),
            commands=SimpleNamespace(lookup=lambda _line: None),
            session_backend=SimpleNamespace(save_snapshot=lambda **_kwargs: None),
            enforce_max_turns=False,
            session_id="s1",
        )

    async def _start_runtime(_bundle):
        return None

    async def _handle_line(bundle, line, **kwargs):
        del bundle, kwargs
        seen.append(line)
        return True

    async def _close_runtime(_bundle):
        return None

    monkeypatch.setattr("openharness.ui.app.build_runtime", _build_runtime)
    monkeypatch.setattr("openharness.ui.app.start_runtime", _start_runtime)
    monkeypatch.setattr("openharness.ui.app.handle_line", _handle_line)
    monkeypatch.setattr("openharness.ui.app.close_runtime", _close_runtime)
    monkeypatch.setattr("openharness.ui.app.sys.stdin", _FakeStdin())

    await run_task_worker(cwd="/tmp/demo")

    assert seen == ["line 1\nline 2\nline 3"]


@pytest.mark.asyncio
async def test_run_print_mode_waits_for_coordinator_async_agents(monkeypatch):
    class _FakeEngine:
        def __init__(self) -> None:
            self.tool_metadata = {
                "async_agent_tasks": [
                    {
                        "agent_id": "worker@default",
                        "task_id": "task_123",
                        "description": "Inspect CI",
                        "notification_sent": False,
                    }
                ]
            }
            self.messages = []
            self.total_usage = SimpleNamespace(
                input_tokens=0,
                output_tokens=0,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
            )
            self.model = "claude-test"
            self.max_turns = 200

        def set_max_turns(self, value):
            self.max_turns = value

        def set_system_prompt(self, _value):
            return None

        async def submit_message(self, message):
            self.messages.append(ConversationMessage.from_user_text(message))
            yield AssistantTurnComplete(
                message=ConversationMessage(role="assistant", content=[TextBlock(text="final synthesis")]),
                usage=None,
            )

    engine = _FakeEngine()
    saved_snapshots: list[dict] = []

    async def _build_runtime(**kwargs):
        return SimpleNamespace(
            cwd=kwargs.get("cwd"),
            engine=engine,
            external_api_client=False,
            extra_skill_dirs=(),
            extra_plugin_roots=(),
            current_settings=lambda: SimpleNamespace(model="claude-test", max_turns=200),
            current_plugins=lambda: [],
            hook_summary=lambda: "",
            plugin_summary=lambda: "",
            mcp_summary=lambda: "",
            app_state=SimpleNamespace(set=lambda **_kwargs: None),
            mcp_manager=SimpleNamespace(close=lambda: None, list_statuses=lambda: []),
            hook_executor=SimpleNamespace(execute=lambda *_args, **_kwargs: None, update_registry=lambda *_a, **_k: None),
            commands=SimpleNamespace(lookup=lambda _line: None),
            session_backend=SimpleNamespace(save_snapshot=lambda **kwargs: saved_snapshots.append(kwargs)),
            enforce_max_turns=True,
            session_id="s1",
        )

    async def _start_runtime(_bundle):
        return None

    async def _handle_line(bundle, line, **kwargs):
        del kwargs
        bundle.engine.messages.append(ConversationMessage.from_user_text(line))
        return True

    async def _close_runtime(_bundle):
        return None

    class _FakeTaskManager:
        def __init__(self) -> None:
            self._calls = 0

        def get_task(self, task_id):
            self._calls += 1
            status = "running" if self._calls == 1 else "completed"
            return SimpleNamespace(id=task_id, status=status, return_code=0)

        def read_task_output(self, task_id, *, max_bytes=12000):
            del task_id, max_bytes
            return "worker result <ready>"

    fake_manager = _FakeTaskManager()

    monkeypatch.setattr("openharness.ui.app.build_runtime", _build_runtime)
    monkeypatch.setattr("openharness.ui.app.start_runtime", _start_runtime)
    monkeypatch.setattr("openharness.ui.app.handle_line", _handle_line)
    monkeypatch.setattr("openharness.ui.app.close_runtime", _close_runtime)
    monkeypatch.setattr("openharness.ui.coordinator_drain.get_task_manager", lambda: fake_manager)
    monkeypatch.setattr("openharness.ui.app.is_coordinator_mode", lambda: True)
    monkeypatch.setattr(
        "openharness.ui.coordinator_drain.build_runtime_system_prompt",
        lambda *args, **kwargs: "coordinator",
    )

    async def _sleep(_seconds):
        return None

    monkeypatch.setattr("openharness.ui.coordinator_drain.asyncio.sleep", _sleep)

    await run_print_mode(prompt="research this", cwd="/tmp/demo")

    assert len(engine.messages) == 2
    assert engine.messages[1].text.startswith("<task-notification>")
    assert "&lt;ready&gt;" in engine.messages[1].text
    assert "worker@default" in engine.messages[1].text
    assert saved_snapshots


def _worker_bundle(seen_kwargs: dict | None = None):
    """Minimal RuntimeBundle stand-in for run_task_worker tests."""
    from types import SimpleNamespace

    settings = SimpleNamespace(task_worker_idle_timeout_s=600.0)
    return SimpleNamespace(
        cwd="/tmp/demo",
        engine=SimpleNamespace(),
        external_api_client=False,
        extra_skill_dirs=(),
        extra_plugin_roots=(),
        current_settings=lambda: settings,
        current_plugins=lambda: [],
        hook_summary=lambda: "",
        plugin_summary=lambda: "",
        mcp_summary=lambda: "",
        app_state=SimpleNamespace(set=lambda **_kwargs: None),
        mcp_manager=SimpleNamespace(close=lambda: None, list_statuses=lambda: []),
        hook_executor=SimpleNamespace(
            execute=lambda *_args, **_kwargs: None,
            update_registry=lambda *_a, **_k: None,
        ),
        commands=SimpleNamespace(lookup=lambda _line: None),
        session_backend=SimpleNamespace(save_snapshot=lambda **_kwargs: None),
        enforce_max_turns=False,
        session_id="s1",
    )


@pytest.mark.asyncio
async def test_run_task_worker_serves_multiple_messages_in_one_process(monkeypatch):
    """Follow-up messages must not require a worker restart (WS1)."""
    seen: list[str] = []
    builds: list[dict] = []

    class _FakeStdin:
        def __init__(self):
            self._lines = iter([
                '{"text":"first","from":"coordinator"}\n',
                '{"text":"second","from":"coordinator"}\n',
                '{"text":"third","from":"coordinator"}\n',
            ])

        def readline(self):
            return next(self._lines, "")

    async def _build_runtime(**kwargs):
        builds.append(kwargs)
        return _worker_bundle()

    async def _start_runtime(_bundle):
        return None

    async def _handle_line(bundle, line, **kwargs):
        del bundle, kwargs
        seen.append(line)
        return True

    async def _close_runtime(_bundle):
        return None

    monkeypatch.setattr("openharness.ui.app.sys.stdin", _FakeStdin())
    monkeypatch.setattr("openharness.ui.app.build_runtime", _build_runtime)
    monkeypatch.setattr("openharness.ui.app.start_runtime", _start_runtime)
    monkeypatch.setattr("openharness.ui.app.handle_line", _handle_line)
    monkeypatch.setattr("openharness.ui.app.close_runtime", _close_runtime)

    from openharness.ui.app import run_task_worker

    await run_task_worker(cwd="/tmp/demo")

    assert seen == ["first", "second", "third"]
    assert len(builds) == 1


@pytest.mark.asyncio
async def test_run_task_worker_exits_on_idle_timeout(monkeypatch):
    import threading
    from types import SimpleNamespace

    release = threading.Event()

    class _BlockingStdin:
        def readline(self):
            # Block until the test finishes, like a quiet coordinator pipe.
            release.wait(timeout=10)
            return ""

    bundle = _worker_bundle()
    bundle.current_settings = lambda: SimpleNamespace(task_worker_idle_timeout_s=0.2)
    closed = []

    async def _build_runtime(**kwargs):
        return bundle

    async def _start_runtime(_bundle):
        return None

    async def _close_runtime(_bundle):
        closed.append(True)

    monkeypatch.setattr("openharness.ui.app.sys.stdin", _BlockingStdin())
    monkeypatch.setattr("openharness.ui.app.build_runtime", _build_runtime)
    monkeypatch.setattr("openharness.ui.app.start_runtime", _start_runtime)
    monkeypatch.setattr("openharness.ui.app.close_runtime", _close_runtime)

    from openharness.ui.app import run_task_worker

    await asyncio.wait_for(run_task_worker(cwd="/tmp/demo"), timeout=5)
    release.set()

    assert closed == [True]


@pytest.mark.asyncio
async def test_run_task_worker_restores_session_from_env(tmp_path, monkeypatch):
    from openharness.api.usage import UsageSnapshot
    from openharness.engine.messages import ConversationMessage, TextBlock
    from openharness.services.session_storage import save_session_snapshot

    monkeypatch.setenv("OPENHARNESS_TASK_SESSION_ID", "task-t123")
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    save_session_snapshot(
        cwd=tmp_path,
        model="test-model",
        system_prompt="system",
        messages=[ConversationMessage(role="user", content=[TextBlock(text="earlier work")])],
        usage=UsageSnapshot(input_tokens=1, output_tokens=1),
        session_id="task-t123",
    )

    class _FakeStdin:
        def readline(self):
            return ""

    builds: list[dict] = []

    async def _build_runtime(**kwargs):
        builds.append(kwargs)
        return _worker_bundle()

    async def _start_runtime(_bundle):
        return None

    async def _close_runtime(_bundle):
        return None

    monkeypatch.setattr("openharness.ui.app.sys.stdin", _FakeStdin())
    monkeypatch.setattr("openharness.ui.app.build_runtime", _build_runtime)
    monkeypatch.setattr("openharness.ui.app.start_runtime", _start_runtime)
    monkeypatch.setattr("openharness.ui.app.close_runtime", _close_runtime)

    from openharness.ui.app import run_task_worker

    await run_task_worker(cwd=str(tmp_path))

    assert builds[0]["session_id"] == "task-t123"
    restored = builds[0]["restore_messages"]
    assert restored and restored[0]["role"] == "user"


@pytest.mark.asyncio
async def test_wait_for_completed_entries_wakes_on_listener(monkeypatch):
    """Completion listener must wake the drain well before the heartbeat."""
    import time
    from types import SimpleNamespace

    from openharness.ui.coordinator_drain import wait_for_completed_async_agent_entries

    listeners = []

    class _FakeTaskManager:
        def __init__(self):
            self.status = "running"

        def get_task(self, task_id):
            return SimpleNamespace(id=task_id, status=self.status, return_code=0)

        def register_completion_listener(self, listener):
            listeners.append(listener)
            return lambda: listeners.remove(listener)

    manager = _FakeTaskManager()
    monkeypatch.setattr(
        "openharness.ui.coordinator_drain.get_task_manager", lambda: manager
    )

    metadata = {
        "async_agent_tasks": [
            {"task_id": "t1", "agent_id": "w1", "notification_sent": False}
        ]
    }

    async def _complete_soon():
        await asyncio.sleep(0.05)
        manager.status = "completed"
        for listener in list(listeners):
            await listener(SimpleNamespace(id="t1", status="completed"))

    start = time.monotonic()
    completer = asyncio.create_task(_complete_soon())
    completed = await asyncio.wait_for(
        wait_for_completed_async_agent_entries(metadata, poll_interval_seconds=30.0),
        timeout=5,
    )
    await completer
    elapsed = time.monotonic() - start

    assert completed and completed[0]["task_id"] == "t1"
    assert elapsed < 1.0, f"listener wake took {elapsed:.2f}s"
    assert not listeners, "listener must be unregistered after drain"
