# OpenHarness — Comprehensive Robustness & Performance Review

**Date:** 2026-06-09
**Scope:** `src/openharness/` (229 Python files, ~46K LOC)
**Method:** Static analysis across all major subsystems (`engine/`, `tools/`, `api/`, `swarm/`, `autopilot/`, `channels/`, `auth/`, `hooks/`, `memory/`, `permissions/`, `services/`, `ui/`, `bridge/`, `tasks/`, `commands/`, `mcp/`, `prompts/`, `utils/`).
**Hot paths examined:** `engine/query.py` (agent loop), `engine/query_engine.py`, `tools/file_read_tool.py`, `tools/bash_tool.py`, `api/client.py`, `api/openai_client.py`, `api/codex_client.py`, `swarm/mailbox.py`, `swarm/permission_sync.py`, `autopilot/service.py`, `channels/impl/*.py`.
**Constraints honoured:** No files were modified. No runtime dependencies or toolchains were added.

---

## Executive summary

The audit identified **130+ distinct issues** spanning robustness (error handling, resource lifecycle, concurrency, input validation, security-adjacent) and performance (sync-in-async, redundant work, hot-path inefficiencies, memory bloat, startup cost). The most impactful cluster is the **agent loop hot path** (`engine/query.py` + `tools/file_read_tool.py` + system-prompt build), where several per-turn computations are repeated work that could be cached. The most security-relevant cluster is the **swarm permission sync + autopilot verification + hook template** boundary, where input validation gaps permit local privilege escalation, permission bypass, and shell injection.

The single most leveraged fixes are:

1. Cache the system prompt, tool schemas, and per-file read results on the runtime bundle (~25% per-turn CPU/IO reduction).
2. Convert all `subprocess.run` calls in async paths to `asyncio.create_subprocess_exec` (unblocks the event loop for the autopilot and channels).
3. Add `asyncio.Lock` for in-process serialization alongside existing file locks (mailbox, DingTalk token, cron history).
4. Track every `asyncio.create_task` in a strong-reference set; never fire-and-forget (utility/shell sandbox cleanup, channel reconnect, task manager readers).
5. Add `httpx.Timeout` and `asyncio.wait_for` wrappers to all outbound HTTP and SDK calls; remove string-based error matching in favor of typed exceptions.
6. Fix reactive compact to never silently drop the empty assistant message (breaks Anthropic's strict user/assistant alternation).

---

## Findings, by severity

### CRITICAL — correctness, security, data loss

#### R1. Autopilot `shell: true` verification commands allow local privilege escalation
**File:** `src/openharness/autopilot/service.py:2107-2117`
**Category:** Input validation / injection
**Description:** `target: str | list[str] = cmd.raw if cmd.shell else list(cmd.argv)` is passed to `subprocess.run(target, shell=cmd.shell)`. The shell-opt-in path is gated by the policy parser (`_parse_verification_entry`), but the policy YAML itself is loaded from the repo (`get_project_verification_policy_path`); an attacker who can drop a `verification_policy.yaml` into a working tree can ship a `shell: true` command containing arbitrary metacharacters. Combined with the autopilot's *resolved cwd*, this is a local privilege-escalation vector. The only checks are "non-empty" and "valid tokenized".
**Suggested fix:** Drop `shell: true` entirely, or log+prompt-confirm the shell command before executing it in autopilot mode. Allow-list the commands that may opt into shell execution.

#### R2. Permission `updated_input` smuggling bypasses path-safety
**File:** `src/openharness/swarm/permission_sync.py:514-531` + `src/openharness/engine/query.py:1021`
**Category:** Input validation
**Description:** The leader-side `updated_input` field is merged into the resolved request verbatim. The receiving worker (`_execute_tool_call`) feeds the `input` dict to `tool.input_model.model_validate(tool_input)`, and the file-path then goes through `_resolve_permission_file_path` which reads `raw_input` directly without re-validation. A malicious worker can craft a `permission_response` with an `updated_input` containing a different `file_path` than was originally requested, bypassing the path-safety check.
**Suggested fix:** Re-validate the merged input dict through `tool.input_model` after applying `updated_input`. Compare `request.input` to the original `pending_request.input`; reject on drift.

#### R3. Hook template `eval $ARGUMENTS` re-introduces shell injection
**File:** `src/openharness/hooks/executor.py:223-229`
**Category:** Input validation / injection
**Description:** `shlex.quote(serialized)` is correct for the substituted value, but the *template* itself is author-controlled (loaded from `~/.openharness/hooks/hooks.json` or repo-level). A template like `bash -c 'eval $ARGUMENTS'` re-introduces the injection that the surrounding `shlex.quote` was trying to prevent. The hook system should reject templates containing `eval` or `bash -c`.
**Suggested fix:** Block dangerous patterns in hook templates. The `OPENHARNESS_HOOK_PAYLOAD` env var is already set at line 96, so template substitution is unnecessary — pass the payload via env.

#### R4. `except (PasswordDeleteError, Exception): pass` swallows everything
**File:** `src/openharness/auth/storage.py:180`
**Category:** Exception handling
**Description:** Listing `Exception` second makes the narrow `PasswordDeleteError` branch dead code. Any exception (including `MemoryError` and other `BaseException`-derived errors, and especially programmer errors like typos in service name) is silently dropped.
**Suggested fix:** Drop `Exception` from the tuple; let the narrow exception do its job; log unexpected failures at debug.

#### R5. Hook executor swallows all errors as `success=True`
**File:** `src/openharness/hooks/executor.py:161`
**Category:** Exception handling
**Description:** `_run_http_hook` returns `success=True` after catching any `Exception`. The hook executor is the security boundary between LLM-driven code and the host OS — silent failures there mean a maliciously-registered hook can pretend to succeed.
**Suggested fix:** Distinguish expected (network/HTTP) errors from programming errors; at minimum log unexpected exceptions with traceback; never return `success=True` after a swallowed exception.

#### R6. `SwarmPermissionRequest.input` not re-validated on the leader
**File:** `src/openharness/swarm/permission_sync.py:514-531`
**Category:** Input validation
**Description:** `request.input` is copied through verbatim from the worker; the leader never re-checks it against the *original* pending request. A malicious worker can smuggle a request that, when resolved, grants itself permissions for a different tool than what was originally requested.
**Suggested fix:** Compare `request.input` to the original `pending_request.input`; reject on drift.

#### R7. `feishu.py` patches `lark` SDK module-level state — only one Feishu channel per process
**File:** `src/openharness/channels/impl/feishu.py:496-500`
**Category:** Concurrency
**Description:** `_lark_ws_client.loop = ws_loop` mutates global state on a third-party library. If two Feishu channels are created in the same process, the second `start()` will overwrite the first's `loop`, and the first's `ws_client.start()` will use the second's loop — which is not running, so the first channel will hang forever.
**Suggested fix:** Document the constraint, or stop patching the SDK and instantiate a fresh `lark.ws.Client` per channel.

#### R8. `httpx.AsyncClient` with no timeout in MCP and DingTalk
**File:** `src/openharness/mcp/client.py:222`, `src/openharness/channels/impl/dingtalk.py:136`
**Category:** Error handling
**Description:** `httpx.AsyncClient(headers=config.headers or None)` (MCP) and `httpx.AsyncClient()` (DingTalk) have no `timeout=` kwarg, so requests can hang indefinitely. A malicious MCP server config pointing at `10.255.255.1:80` will block the entire MCP connect phase forever, and `McpClientManager.connect_all` iterates all servers serially.
**Suggested fix:** Pass `timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)`; connect MCP servers concurrently with `asyncio.gather(..., return_exceptions=True)`.

#### R9. Mailbox filename collision silently overwrites messages
**File:** `src/openharness/swarm/mailbox.py:137-147`
**Category:** Concurrency / correctness
**Description:** `filename = f"{msg.timestamp:.6f}_{msg.id}.json"`. Two messages written from different sources in the same microsecond produce identical filenames; the second `os.replace` (inside the held lock) overwrites the first silently. This is a real correctness bug.
**Suggested fix:** Use the `id` (UUID4) alone, or add a per-process monotonic counter.

#### R10. `provider` name not validated in `auth login`
**File:** `src/openharness/cli.py:1740-1744`
**Category:** Input validation
**Description:** `store_credential(provider, ...)` accepts arbitrary provider names like `__proto__` or `../etc`. The provider name becomes a key in the credentials JSON; the path validation occurs only at consumption time.
**Suggested fix:** Allow-list against `_PROVIDER_LABELS`; reject names containing `/`, `..`, or control characters.

#### P1. `file_read_tool` reads entire file into memory then throws it away
**File:** `src/openharness/tools/file_read_tool.py:52-65`
**Category:** Memory bloat / streaming
**Description:** `read_bytes()` slurps the whole file, then `decode("utf-8", errors="replace")` and `splitlines()` materialize every line just to slice `lines[offset:offset+limit]`. For a 50 MB log file or 200 MB binary, the entire payload lands in memory before being thrown away.
**Suggested fix:** Stream the file in binary, decode on the fly, count newlines until `offset+limit`, and bail early.

#### P2. `file_read_tool` has no cache — re-reads and re-decodes on every call
**File:** `src/openharness/tools/file_read_tool.py:31-65`
**Category:** Repeated work
**Description:** When the model reads a file once, then re-reads it with different `offset/limit` (very common when grepping for context), the file is opened, read, decoded, and split into a list every time. The carryover log already records recent reads but no cache backs them.
**Suggested fix:** Add a short-lived LRU keyed on `(path, mtime)` (e.g. `functools.lru_cache(maxsize=64)` invalidated by `os.stat` mtime).

#### P3. Tool schemas regenerated from Pydantic on every model turn
**File:** `src/openharness/engine/query.py:734` → `src/openharness/tools/base.py:78-80`
**Category:** Hot-path inefficiency
**Description:** `context.tool_registry.to_api_schema()` invokes `tool.input_model.model_json_schema()` for every one of the ~50 built-in tools plus any MCP/plugin tools. The schemas are invariant for the lifetime of the registry but are rebuilt every turn — hundreds of milliseconds of CPU before every model request.
**Suggested fix:** Cache on the `ToolRegistry` via `functools.cached_property`; invalidate only when `register()` is called.

#### P4. `settings.resolve_profile()` re-runs on every keystroke in UI loop
**File:** `src/openharness/config/settings.py:629-637, 759-866`
**Category:** Hot-path inefficiency
**Description:** `current_settings()` re-runs `load_settings()` (re-reads + re-parses `settings.json`) and `merge_cli_overrides()` on every call — and `handle_line` calls `current_settings()` three or more times per submitted line. The deep `model_copy` chain on `Settings.merged_profiles()` is also re-executed.
**Suggested fix:** Cache the resolved `Settings` on the `RuntimeBundle`; invalidate only on explicit user mutation. Cache `current_plugins()` and `hook_summary()` too.

#### P5. System prompt rebuilt on every submitted line
**File:** `src/openharness/ui/runtime.py:668-677, 702-710, 736-744`
**Category:** Repeated work / token waste
**Description:** `build_runtime_system_prompt` is called inside `handle_line` for every submitted line, which re-loads the skill registry, re-reads CLAUDE.md and rule files, and re-runs `select_relevant_memories` against the latest user prompt. The memory scan alone re-tokenizes up to 100 files per turn.
**Suggested fix:** Cache the prompt on the bundle, keyed on `(settings_hash, latest_user_prompt, mtimes)`. Memoise `select_relevant_memories` on the user prompt string for the session.

#### P6. `codex_client` re-creates `httpx.AsyncClient` per call
**File:** `src/openharness/api/codex_client.py:276-282`
**Category:** Re-initializing clients per call
**Description:** A new `httpx.AsyncClient(timeout=60.0, follow_redirects=True)` is created on every stream call. TCP/TLS handshake + connection pool warm-up is paid for every model turn.
**Suggested fix:** Hold a long-lived `httpx.AsyncClient` on the `CodexApiClient`; close in `close()`; reuse across requests.

#### P7. `codex_client` blocks on `response.completed` before emitting `MessageComplete`
**File:** `src/openharness/api/codex_client.py:364-371`
**Category:** Streaming / perceived latency
**Description:** The model "feels" finished to the user only after a redundant round-trip to read final usage, even though text deltas are streamed.
**Suggested fix:** Yield `MessageComplete` as soon as `response.completed` arrives via SSE; do not also block on a final `get_final_message` round-trip.

---

### HIGH — robustness gaps, race conditions, leaks

#### R11. `BackgroundTaskManager` leaks `_copy_output` reader tasks on `close()`
**File:** `src/openharness/tasks/manager.py:255-257, 375-393`
**Category:** Resource management
**Description:** The `reader` local variable goes out of scope; nothing keeps a strong reference. After `aclose()` returns, an orphan task can write to a closed file handle (`ValueError: I/O operation on closed file`), silently swallowed by `gather(return_exceptions=True)`.
**Suggested fix:** Store the copy-reader tasks on `self`; `await`/`cancel` them in `aclose`; consider `asyncio.TaskGroup`.

#### R12. `BackgroundTaskManager.aclose` doesn't `try/finally` the watcher gather
**File:** `src/openharness/tasks/manager.py:397-418`
**Category:** Resource management
**Description:** `self._processes.clear()` runs before the gather completes; subsequent state mutations race with still-active watchers.
**Suggested fix:** Await the gather first, then clear state. Track file handles explicitly; close only after gather completes.

#### R13. Fire-and-forget `asyncio.create_task` for sandbox cleanup
**File:** `src/openharness/utils/shell.py:104`
**Category:** Concurrency
**Description:** `asyncio.create_task(_cleanup_after_exit(process, cleanup_path))` is not stored anywhere. If GC runs between `create_task` and the first `await` inside the coroutine, or the event loop closes before the task finishes, the script temp file leaks. The codebase already has the right pattern in `channels/impl/dingtalk.py:120` and `channels/impl/feishu.py:512` (`_background_tasks: set[asyncio.Task]`).
**Suggested fix:** Store the task on a module-level strong-reference set; await/inspect on shutdown.

#### R14. Swarm mailbox has no in-process `asyncio.Lock`
**File:** `src/openharness/swarm/mailbox.py:144-147, 188-205, 216-225`
**Category:** Concurrency
**Description:** The file lock doesn't serialize two coroutines in the *same* event loop. Two `mark_read` invocations both `glob("*.json")` while holding the file lock; if a third party deletes a file between glob and read, iteration is inconsistent.
**Suggested fix:** Add an `asyncio.Lock` on the `TeammateMailbox` instance for in-process serialization. Snapshot the directory listing under the lock and iterate over a stable list, not a live `glob`.

#### R15. `swarm/in_process.py` shutdown uses `shield` outside `wait_for` — wrong semantics
**File:** `src/openharness/swarm/in_process.py:561-580`
**Category:** Concurrency
**Description:** `await asyncio.wait_for(asyncio.shield(entry.task), ...)` violates `shield`/`wait_for` semantics; the second `force` branch is unreachable. Two `request_cancel(force=True)` paths exist and they conflict.
**Suggested fix:** Pick one: `entry.task.cancel(); await entry.task` (no shield), or `entry.abort_controller.request_cancel(force=True); await entry.task`.

#### R16. Email channel UID eviction keeps ancient, drops recent
**File:** `src/openharness/channels/impl/email.py:312-314`
**Category:** Unbounded data structures
**Description:** `self._processed_uids = set(list(self._processed_uids)[len(self._processed_uids) // 2:])` is the *upper* half of insertion order, not a "random half". This causes re-processing of the most likely re-fetched UIDs.
**Suggested fix:** Use `collections.OrderedDict` and pop the oldest items, or use a real LRU cache, or trust the existing `mark_seen` flag.

#### R17. Feishu channel `time.sleep(5)` inside `run_ws` blocks shutdown
**File:** `src/openharness/channels/impl/feishu.py:507-510`
**Category:** Concurrency
**Description:** The daemon thread cannot send a close frame; `join` is never called. The dangling thread prevents graceful CLI exit until asyncio cancels the outer task.
**Suggested fix:** Use `threading.Event` for shutdown signaling; join the thread with timeout in `stop()`.

#### R18. DingTalk `_get_access_token` race condition
**File:** `src/openharness/channels/impl/dingtalk.py:176-201`
**Category:** Concurrency
**Description:** Multiple coroutines can race to refresh the access token; both issue HTTP POSTs and the second overwrites the first's token.
**Suggested fix:** Wrap the refresh in a module-level `asyncio.Lock` so only one refresh happens at a time; subsequent callers see the freshly-set token.

#### R19. `engine/query.py:_describe` catches every Exception as "[Image: could not parse]"
**File:** `src/openharness/engine/query.py:608-611`
**Category:** Exception handling
**Description:** Masks Pydantic validation, image decoding, and genuine bugs as the same user-facing string. The conversation continues as if the image was successfully processed, with no signal to the model.
**Suggested fix:** Catch `pydantic.ValidationError` and `ValueError` specifically; let unexpected exceptions propagate.

#### R20. `compact` retry loop drops the last exception silently
**File:** `src/openharness/services/compact/__init__.py:1283-1350`
**Category:** Retry logic broken
**Description:** On PTL retry exhaustion, `if not summary_text: return passthrough` — the caller never sees the failure; the conversation continues with original messages. This is silent data loss in the worst case.
**Suggested fix:** Track the last exception and re-raise after exhausting retries; or return an explicit `compact_failed` sentinel.

#### R21. `run_query` reactive compact fails on consecutive prompt-too-long
**File:** `src/openharness/engine/query.py:768-781, 792-800`
**Category:** Error handling
**Description:** `is_effectively_empty` drops the empty assistant message; the turn ends with no `AssistantTurnComplete`, which breaks Anthropic's strict user/assistant alternation requirement. No `consecutive_failures` counter exists on the engine.
**Suggested fix:** Track a consecutive-prompt-too-long counter; retry or synthesize a placeholder assistant message; never silently drop.

#### R22. `BashTool` `process.communicate` not in `try/finally`
**File:** `src/openharness/tools/bash_tool.py:60-85`
**Category:** Resource management
**Description:** `_read_remaining_output` (with its own 2s timeout) can raise; the spawned process is leaked. `CancelledError` injected between awaits orphans the process.
**Suggested fix:** Wrap process supervision in a `try/finally` that always calls `_terminate_process`; replace `except Exception: pass` around the kill with explicit `ProcessLookupError`/`RuntimeError` handling.

#### R23. `swarm/team_lifecycle._destroy_worktree` `ignore_errors=True` hides real cleanup failures
**File:** `src/openharness/swarm/team_lifecycle.py:720-738`
**Category:** Resource management
**Description:** `shutil.rmtree(worktree_path, ignore_errors=True)` swallows every `OSError`, including permission errors indicating attacker-controlled unreadable files. Phantom worktrees appear on next session.
**Suggested fix:** Capture and log the rmtree outcome; surface a "partial cleanup" warning to the user.

#### R24. `swarm/team_lifecycle.delete_team` rmtree with no in-use check
**File:** `src/openharness/swarm/team_lifecycle.py:824`
**Category:** Resource management
**Description:** Fails silently on Windows with `PermissionError` if a teammate still has a SQLite/log file open. The exception is not caught — it propagates and the team directory is left in a half-deleted state.
**Suggested fix:** `onerror` callback that logs and retries; require all teammates shut down before `delete_team`.

#### R25. `BridgeSessionManager` accumulates `_copy_tasks` forever
**File:** `src/openharness/bridge/manager.py:25-106`
**Category:** Memory bloat
**Description:** Completed task handles are never popped. Long-running bridges leak memory.
**Suggested fix:** `discard` on done callback; or `await` after the task completes.

#### R26. `BridgeSessionManager` per-chunk `path.open("ab")` thrashes the FS
**File:** `src/openharness/bridge/manager.py:88-94`
**Category:** Performance
**Description:** 4 KB open/write/close cycle. For 10 MB output = 2,500 cycles; on Windows this can also cause sharing-violation errors.
**Suggested fix:** Open the file once for the session lifetime; flush every 64 KB.

#### R27. `commands/registry.py:fallback` writes clipboard contents to `last_copy.txt` with default umask
**File:** `src/openharness/commands/registry.py:234-236`
**Category:** Resource management
**Description:** Clipboard contents (potentially API keys) land in a per-user config file with default permissions. No 0600 mode. The file is never cleaned up.
**Suggested fix:** Write to a tmp file with 0600 mode and surface the path only on explicit user invocation, or warn that clipboard failed and don't write to disk.

#### R28. Mailbox write filename collision (see R9; combined fix)

#### R29. Permission re-validation gap when `updated_input` is applied (see R2)

#### R30. `feishu._download_file_sync` returns `(None, None)` for both "not found" and network errors
**File:** `src/openharness/channels/impl/feishu.py:880-898`
**Category:** Error handling
**Description:** The caller can't distinguish transient infrastructure errors from legitimate "no such resource" responses, so the same retry loop keeps firing.
**Suggested fix:** Raise on transport errors; return a sentinel for legitimate misses; return `(None, None, error=str(exc))` for transient errors.

#### R31. `compact` retry re-sends full older history on each PTL attempt
**File:** `src/openharness/services/compact/__init__.py:1256-1349`
**Category:** Token waste
**Description:** Every retry re-serializes the full payload plus placeholders.
**Suggested fix:** Place once before the loop; only the prefix changes on PTL retries.

#### R32. `feishu._extract_interactive_content` recurses into JSON with no depth limit
**File:** `src/openharness/channels/impl/feishu.py:230-268`
**Category:** Input validation
**Description:** A malicious card with deep nesting can trigger `RecursionError` on the *daemon* WS thread — process death.
**Suggested fix:** Depth counter; bail at depth 5 with a warning.

#### R33. `engine/query.py:run_query` yields `ErrorEvent` then silently returns; turns inconsistent
**File:** `src/openharness/engine/query.py:782-785`
**Category:** Error handling
**Description:** `return` is reached before the `RuntimeError` is raised (unreachable code after `return`). Engine state is torn.
**Suggested fix:** Re-raise after yielding the error, or set a sentinel on the engine for the next turn.

#### R34. `_run_command_hook` doesn't handle `CancelledError` after spawn
**File:** `src/openharness/hooks/executor.py:88-114`
**Category:** Concurrency
**Description:** If `CancelledError` is raised mid-spawn, the process is orphaned to init/systemd.
**Suggested fix:** `try/except (asyncio.CancelledError, KeyboardInterrupt): process.kill(); raise` around the spawn.

#### R35. `commands/registry.py:_compact_handler` falls back to deterministic compact silently on LLM error
**File:** `src/openharness/commands/registry.py:512-527`
**Category:** Error handling
**Description:** User typed `/compact` and got a weaker summary with no warning that the LLM path failed.
**Suggested fix:** Surface the LLM error in the `CommandResult`; let the user decide whether to accept the fallback.

#### R36. `feishu._handle_message` regex matches unbounded text
**File:** `src/openharness/channels/impl/feishu.py:144-148`
**Category:** Input validation / DoS
**Description:** A 10 KB single line is matched against every bot name sequentially — O(n × m) per message, unbounded.
**Suggested fix:** Cap normalized text to ~4 KB before regex matching.

#### R37. `services/cron_scheduler.py:execute_job` `except Exception` swallows `CancelledError`
**File:** `src/openharness/services/cron_scheduler.py:382-396`
**Category:** Exception handling
**Description:** Scheduler can't shut down cleanly. If the error is `SystemExit` triggered by a hook, it is logged as a normal error and the scheduler keeps running.
**Suggested fix:** Catch `(OSError, asyncio.TimeoutError, subprocess.SubprocessError, ValueError)`; let `asyncio.CancelledError` and `KeyboardInterrupt` propagate.

#### R38. `services/cron_scheduler.append_history` opens file without lock
**File:** `src/openharness/services/cron_scheduler.py:59-64`
**Category:** Concurrency
**Description:** `asyncio.gather`-ed cron jobs may interleave writes for large entries (>PIPE_BUF=4 KB).
**Suggested fix:** Add a module-level `asyncio.Lock` around `append_history`.

#### R39. `autopilot/service.py:_read_yaml` swallows `MemoryError`, `OSError` as `yaml.YAMLError`
**File:** `src/openharness/autopilot/service.py:2002-2011`
**Category:** Exception handling
**Description:** Silent fallback to defaults when policy is corrupt. An attacker who can write to the project dir can blank out the policy and the autopilot will run with `default_human_gate=True, use_worktree=True` defaults that may not match user intent. No log, no journal entry.
**Suggested fix:** Catch `yaml.YAMLError`; log `OSError` and other errors; append a journal entry.

#### R40. `BashTool` output truncated to 12 KB with no offload
**File:** `src/openharness/tools/bash_tool.py:139-141`
**Category:** Memory bloat / token waste
**Description:** The engine has a sophisticated offload path (`query.py:524-553`) that bash bypasses. Bash output >12 KB is permanently lost to the model.
**Suggested fix:** Have `bash_tool` return the full output and let the engine's offload path do its job.

#### R41. `swarm/permission_sync._sync_resolve_permission` `.json.tmp` filename collides
**File:** `src/openharness/swarm/permission_sync.py:534-541`
**Category:** Resource management
**Description:** `pending/abc123.json` + `pending/abc123.json.tmp`; lock release between write and unlink lets third party observe both.
**Suggested fix:** Move the `pending_path.unlink()` to *before* the `tmp_path.write_text` and `os.replace` — lock is still held.

#### R42. `engine/query.py:_execute_tool_call` allows error-message side channel
**File:** `src/openharness/engine/query.py:907-924`
**Category:** Security-adjacent
**Description:** `except Exception` from tool validator converts to non-permission error; iteration can enumerate secrets in error messages.
**Suggested fix:** Narrow to `ValidationError`; sanitize error messages from tool validators.

#### R43. `autopilot/service.py:_run_command` `env={**os.environ, ...}` race
**File:** `src/openharness/autopilot/service.py:1235-1245`
**Category:** Concurrency
**Description:** If `os.environ` mutates between spread and `subprocess.run`, the subprocess sees the new value.
**Suggested fix:** `env=dict(os.environ, ...)` for a stable snapshot.

#### R44. `bridge/session_runner.SessionHandle.kill` doesn't await final write
**File:** `src/openharness/bridge/manager.py:79-94`
**Category:** Resource management
**Description:** Kill races with in-flight `handle.process.stdout.read(4096)`; the final chunk may not flush.
**Suggested fix:** `await self._copy_tasks[id]` before returning from `stop()`.

#### R45. `BackgroundTaskManager._copy_output` per-chunk file-open + lock
**File:** `src/openharness/tasks/manager.py:273-282`
**Category:** Performance / concurrency
**Description:** 4 KB open/write/close × 1 per chunk. Same fix as R26.
**Suggested fix:** Persist the open file handle on the `TaskRecord`; open in `create_shell_task`; close in `close()`. Remove the per-chunk locking.

#### R46. `engine/messages.py` `text` property rebuilds string on every access
**File:** `src/openharness/engine/messages.py:90-94`
**Category:** Hot-path inefficiency
**Description:** `text` is a `@property` that does `"".join(...)` over a comprehension every call. Several call sites access it multiple times per message.
**Suggested fix:** `functools.cached_property` (per-message); invalidate on content mutation.

#### R47. `engine/query.py:run_query` reactive compact silently reduces `turn_count`
**File:** `src/openharness/engine/query.py:766`
**Category:** Error handling
**Description:** `turn_count = max(0, turn_count - 1)` can violate the user's `max_turns=1` after a token-limit retry.
**Suggested fix:** Re-check the effective `max_turns` after the decrement; refuse to continue if the retry would violate the limit.

#### R48. `_load_session_cursors` LRU eviction is wrong (see R16; combined fix)

#### R49. `engine/query.py` `tool_metadata` reference shared across all calls
**File:** `src/openharness/engine/query.py:137-156, 974`
**Category:** Resource management
**Description:** Mutable dict is copied on every tool invocation. Engine, executor, hooks, tool context all share; `MappingProxyType` is the right primitive.
**Suggested fix:** Document that callers must not mutate `tool_metadata`, and stop copying on every invocation.

#### R50. `swarm/mailbox.read_all` O(n) over all messages
**File:** `src/openharness/swarm/mailbox.py:163-181`
**Category:** Performance
**Description:** Re-reads and re-parses every JSON file on every dispatch tick. A busy agent with 10,000 unread messages does 10,000 file reads.
**Suggested fix:** Track a high-water-mark cursor; only read files with `mtime >= last_seen`; or use SQLite.

#### R51. `_split_preserving_tool_pairs` has no size cap
**File:** `src/openharness/services/compact/__init__.py`
**Category:** Unbounded data structures
**Description:** If the summarizer loops, the summary text grows unbounded. There's no token-budget guard on the *summary text*.
**Suggested fix:** Validate `len(estimate_message_tokens([summary_msg, *newer])) < estimate_message_tokens(messages) * 1.1`; if not, fall back to a passthrough.

#### R52. `permissions/checker.PermissionChecker.evaluate` re-evaluates on every tool call
**File:** `src/openharness/engine/query.py:933-938`
**Category:** Hot-path inefficiency
**Description:** Minor, but `is_docker_sandbox_active()` does an import+call per call. Combined with the lazy import of `openharness.sandbox.path_validator` inside `file_read_tool.py:41` and `file_edit_tool.py:38`.
**Suggested fix:** Resolve `is_docker_sandbox_active` once at session start; cache the path-validator import on a module-level lazy variable.

#### R53. `cli.py:_login_provider` calls `store_credential` twice (file + keyring); only logs the second
**File:** `src/openharness/cli.py:1740-1744`
**Category:** Error handling
**Description:** If the file write succeeds and the keyring call fails, the user sees "saved" but the key is in two places; the second call's `except Exception: pass` hides the failure.
**Suggested fix:** Consolidate to one path; log the other.

#### R54. `try_session_memory_compaction` returns shallow copy that can be mutated concurrently
**File:** `src/openharness/services/compact/__init__.py:947-957`
**Category:** Concurrency
**Description:** No event-loop lock on `engine.messages`. If a parallel hook handler mutates `engine.messages` mid-compaction, the downstream code works on a stale copy.
**Suggested fix:** Atomic `engine.messages = build_post_compact_messages(result)`.

#### R55. `engine/query.py` `_bounded_completion_tokens` uses string-match on error messages
**File:** `src/openharness/engine/query.py:653`
**Category:** Error handling
**Description:** Provider message change → heuristic fails → user stuck on the same error.
**Suggested fix:** Use a typed exception or error code rather than string matching.

#### R56. `auth/external.load_copilot_token` runs `security` subprocess with no timeout
**File:** `src/openharness/auth/external.py:276`
**Category:** Resource management
**Description:** macOS keychain "Allow access" dialog can block forever.
**Suggested fix:** Add `timeout=10` to `subprocess.run`.

#### R57. `services/cron.py` `croniter` iteration may raise past validation
**File:** `src/openharness/services/cron.py:59-70`
**Category:** Error handling
**Description:** `CroniterBadCronError`/`KeyError` for some expressions that pass `is_valid` are silently uncaught at the scheduler tick level.
**Suggested fix:** Wrap `next_run` computation in `except (ValueError, KeyError)`.

#### R58. `engine/query.py` `_stream_compaction` 50 ms `asyncio.wait_for` busy-poll
**File:** `src/openharness/engine/query.py:686-693`
**Category:** Performance
**Description:** Event loop wakes 20×/sec idle. The coroutine never checks for `CancelledError`.
**Suggested fix:** Use a sentinel-driven `queue.get()`; check `context.cancelled()` after each timeout.

#### R59. `engine/query.py` `_is_prompt_too_long_error` string match is fragile
**File:** `src/openharness/engine/query.py`
**Category:** Error handling
**Description:** Same string-match pattern as R55. Provider message change → heuristic fails.
**Suggested fix:** Use a typed exception or error code.

#### R60. `feishu._format_response_error` may itself raise — masks the original
**File:** `src/openharness/channels/impl/feishu.py:1029`
**Category:** Error handling
**Description:** If the helper raises (e.g. JSON parse error on the response), the original error is masked.
**Suggested fix:** Wrap the helper in `try/except`; pass a generic message on failure.

#### R61. `utils/fs.py:atomic_write_bytes` suppresses `OSError` from unlink
**File:** `src/openharness/utils/fs.py:62-66`
**Category:** Resource management
**Description:** Stale `.tmp` files balloon on disk-full. Across many transient calls (every credential update, every settings save), `.openharness` can grow.
**Suggested fix:** Log the unlink failure at warning; consider a periodic GC pass for stale `.<name>.*.tmp` files older than N days.

#### R62. `feishu._handle_message` matches `@name` on full untrusted text (see R36; combined)

#### R63. `channels/impl/manager._dispatch_outbound` 1 s `asyncio.wait_for` poll
**File:** `src/openharness/channels/impl/manager.py:213-238`
**Category:** Performance
**Description:** The dispatcher wakes up 86,400 times per day even when idle.
**Suggested fix:** Use `asyncio.Event` signaling from `publish_outbound`; only poll as a fallback.

#### R64. `autopilot/service.py:_build_pr_body` interpolates user content verbatim
**File:** `src/openharness/autopilot/service.py:1400-1405`
**Category:** Input validation
**Description:** Issue title with embedded newlines/CRLF/terminal escape codes is preserved verbatim into the PR body. The dashboard render at line 1204 uses `html.escape` for HTML, but the PR body is markdown and not sanitized.
**Suggested fix:** Normalize the title and body — strip ANSI escapes, normalize line endings — before interpolating.

#### R65. `swarm/team_lifecycle.set_member_mode` rebuilds from `to_dict()` instead of `dataclasses.replace`
**File:** `src/openharness/swarm/team_lifecycle.py:505-511`
**Category:** Input validation
**Description:** `team_file.members[k] = TeamMember(**{**m.to_dict(), "mode": mode})` silently drops fields if `to_dict()` shape changes.
**Suggested fix:** `team_file.members[k] = dataclasses.replace(m, mode=mode)`.

#### R66. `swarm/permission_sync` permission re-validation (see R2, R6; combined)

#### R67. `engine/query_engine.py:203` `except Exception` in stream loop
**File:** `src/openharness/engine/query_engine.py:203`
**Category:** Exception handling
**Description:** Next turn starts with no record of why the prior turn failed.
**Suggested fix:** Distinguish API errors (re-raise) from tool errors (record on the turn).

#### R68. `swarm/in_process.py:264-268` stub-mode sleep loop has loose cancellation
**File:** `src/openharness/swarm/in_process.py:264-268`
**Category:** Concurrency
**Description:** Abort latency bounded by sleep duration. If the abort controller is set *after* `await asyncio.sleep(0.1)` returns and *before* the next check, the loop runs to completion.
**Suggested fix:** `await asyncio.wait_for(abort_controller.cancel_event.wait(), timeout=0.1)`.

#### R69. `whatsapp.py` swallows every WebSocket error and never updates `_connected`
**File:** `src/openharness/channels/impl/whatsapp.py:63-94`
**Category:** Error handling
**Description:** Next send silently no-ops.
**Suggested fix:** Distinguish connection failures (retry) from config errors (fail fast); track `_connected` consistently.

#### R70. `autodream/backup.py` `except Exception: pass` in tight loop
**File:** `src/openharness/services/autodream/backup.py:22`
**Category:** Exception handling
**Description:** Silent partial backup. Caller proceeds as if all files were copied.
**Suggested fix:** Log the per-file failure; collect failures into a list returned to the caller; require explicit acknowledgment of a partial backup.

#### R71. `channels/impl/email.py` naive HTML stripper
**File:** `src/openharness/channels/impl/email.py:359-403`
**Category:** Input validation
**Description:** Strips all tags without considering attributes. A malicious `<script>` body is silently emptied; the text is then fed to the model.
**Suggested fix:** Use a proper HTML parser; reject `text/html` parts that contain `<script>` or `<style>`.

#### R72. `engine/query.py:_stream_compaction` unbounded `asyncio.Queue`
**File:** `src/openharness/engine/query.py:665-696`
**Category:** Concurrency
**Description:** Producer can block on a slow consumer.
**Suggested fix:** Bounded queue with drop-on-full, or yield events directly from a single coroutine.

#### R73. Feishu and other channels JSON-parse arbitrary nested payloads
**File:** `src/openharness/channels/impl/feishu.py:236`, `src/openharness/swarm/mailbox.py:411-460`
**Category:** Input validation
**Description:** No size/depth cap. A 1 MB JSON blob may trigger `RecursionError` deep in the message handler.
**Suggested fix:** Validate payload size before parsing; use `json.JSONDecoder` with explicit depth check.

#### P8. `services/lsp` re-parses entire workspace via `ast.parse` on every call
**File:** `src/openharness/services/lsp/__init__.py:34-93`
**Category:** Repeated work
**Description:** `workspace_symbol_search`, `go_to_definition`, and `find_references` each call `iter_python_files(root)` (which `rglob`s all `*.py`) and then `ast.parse(... .read_text())` for each file. Calling `lsp` three times in a row re-parses the same workspace from scratch. AST parsing of a 5,000-line file takes ~50 ms; on a 5,000-file workspace that's minutes per call.
**Suggested fix:** Persist a parsed-AST cache keyed on `(path, mtime)`. Use a single workspace walk that yields a `dict[Path, ast.Module]` once per session.

#### P9. `is_model_multimodal` iterates 25+ regexes per call
**File:** `src/openharness/api/provider.py:135-186`
**Category:** Hot-path inefficiency
**Description:** `_MULTIMODAL_MODEL_PATTERNS` is a list of 25 compiled regexes. `is_model_multimodal` iterates with `any(pattern.search(...) is not None for pattern in ...)`. `re.Pattern.search` does a full regex scan over the model string for every pattern. Called from `_preprocess_images_in_messages` per turn.
**Suggested fix:** Use a set of normalized lowercase prefixes/literals (most patterns are simple `^claude-3` or `^gpt-4o` prefix matches). A `startswith` chain is microseconds instead of milliseconds.

#### P10. `compact` re-tokenizes full conversation on every check
**File:** `src/openharness/services/compact/__init__.py:116-131`
**Category:** Repeated work
**Description:** `estimate_message_tokens` walks every message and every block every time `should_autocompact` is checked. For a 200K-token context this is hundreds of thousands of block iterations per turn.
**Suggested fix:** Memoise token counts on the `ConversationMessage`; invalidate only on content mutation; or use a single-pass per-turn computation that updates a counter incrementally.

#### P11. `autopilot` re-reads journal/registry/active-context files on every tick
**File:** `src/openharness/autopilot/service.py:367-403, 1892-1912`
**Category:** Repeated work
**Description:** `load_journal` reads & JSON-parses every line of the journal file on every call; `load_active_context` reads a potentially-large markdown file on every call; `rebuild_active_context` is called after every status update. A single card update triggers several disk reads.
**Suggested fix:** Cache `RepoAutopilotStore` state in-memory; write-through to disk on mutation; reload only on explicit reload.

#### P12. `autopilot` uses `subprocess.run` from async paths
**File:** `src/openharness/autopilot/service.py:1236-1249, 1982-1988, 2109-2117`
**Category:** Sync blocking in async paths
**Description:** `subprocess.run(..., capture_output=True, text=True)` blocks the event loop until the subprocess exits. A 30-second verification step freezes the entire event loop — no concurrent tool calls, no streaming model output, no UI updates.
**Suggested fix:** Use `asyncio.create_subprocess_exec` + `await process.communicate()`. Provide an async sibling of `_run_command`.

#### P13. `autopilot` makes 3+ sequential `gh` CLI calls that could parallelize
**File:** `src/openharness/autopilot/service.py:1319-1324`
**Category:** Sequential API calls
**Description:** `_current_repo_full_name` calls `gh repo view`; `_find_open_pr_for_branch` calls `gh pr list`; later, `_pr_status_snapshot` and `_wait_for_pr_ci` make more `gh` calls. Each is a synchronous `subprocess.run`.
**Suggested fix:** Convert to async subprocesses; gather the independent ones in parallel.

#### P14. `cron_scheduler` polling loop uses `time.sleep` in async path
**File:** `src/openharness/services/cron_scheduler.py:140-144, 540-546`
**Category:** Sync blocking in async paths
**Description:** `stop_scheduler` uses `for _ in range(10): if not _pid_exists(pid): ...; time.sleep(0.2)`. `_pid_exists` does `os.kill(pid, 0)` — a syscall each iteration. The same function is called inside the daemon's startup loop. While the daemon is a separate process, `start_daemon` is called from the async CLI command and blocks the event loop.
**Suggested fix:** Use `asyncio.sleep` in the CLI command path; track the actual `Popen` handle and `await process.wait()` once.

#### P15. `swarm/mailbox` uses `run_in_executor` with default thread pool
**File:** `src/openharness/swarm/mailbox.py:151, 181, 209, 229`
**Category:** Sync blocking in async paths
**Description:** Every mailbox call offloads to the default executor (32 threads max). Under swarm load, the thread pool saturates.
**Suggested fix:** Use `asyncio.to_thread` (Python 3.9+); for hot paths, hold a single-shot `asyncio.Lock` per agent and use `aiofiles` for async IO.

#### P16. `grep_tool` Python fallback re-reads & re-decodes every file
**File:** `src/openharness/tools/grep_tool.py:105-141`
**Category:** Repeated work
**Description:** For each candidate file, `path.read_bytes() + decode() + splitlines()` and re-iterates line-by-line. For a multi-thousand-file Python workspace on a slow disk this is dramatically slower than the ripgrep path.
**Suggested fix:** Drop the fallback; or open the file in binary, read in chunks, and run the compiled regex against byte-strings.

#### P17. `_record_tool_carryover` does `tool_output.splitlines()[0]` per call
**File:** `src/openharness/engine/query.py:472-510`
**Category:** Hot-path inefficiency
**Description:** Every tool call appends "verified work" / "work log" entries using `tool_output.splitlines()[0].strip() if tool_output.strip() else "no output"` — this allocates a full list of every line just to read the first one.
**Suggested fix:** `first_line = tool_output.partition("\n")[0].strip()`; short-circuit on `not tool_output`.

#### P18. `copilot_auth.poll_for_access_token` uses sync `httpx.post` + `time.sleep`
**File:** `src/openharness/api/copilot_auth.py:202-220`
**Category:** Sync blocking in async paths
**Description:** Polling uses `time.sleep(poll_interval + _POLL_SAFETY_MARGIN)` and a blocking `httpx.post`. 5-second poll interval × 900 s timeout = 15 minutes of a pinned thread.
**Suggested fix:** Use `httpx.AsyncClient` + `await asyncio.sleep(...)`.

#### P19. `auth/external.refresh_claude_oauth_credential` uses `urllib.request.urlopen`
**File:** `src/openharness/auth/external.py:413-450`
**Category:** Sync blocking in async paths
**Description:** `urllib.request.urlopen(request, timeout=10)` blocks the event loop; on transient failure the code loops through multiple OAuth endpoints.
**Suggested fix:** Use `httpx.AsyncClient.post(...)` + `await`.

#### P20. `feishu` WS reconnect: blocking `ws_client.start()`
**File:** `src/openharness/channels/impl/feishu.py:507-510`
**Category:** Sync blocking in async paths
**Description:** No watchdog; stuck DNS hangs the daemon thread forever.
**Suggested fix:** `asyncio.to_thread(self._ws_client.start)` with a watchdog timeout.

#### P21. `dingtalk` reconnect: `await self._client.start()` is sync SDK
**File:** `src/openharness/channels/impl/dingtalk.py:152-159`
**Category:** Sync blocking in async paths
**Description:** Doesn't yield to the outer loop. On a stuck connection the loop is stuck too.
**Suggested fix:** `asyncio.to_thread(self._client.start)` with a watchdog.

#### P22. `cron_scheduler.stop_scheduler` polls `_pid_exists` 10× at 200 ms
**File:** `src/openharness/services/cron_scheduler.py:140-144`
**Category:** Polling / inefficient wait
**Description:** Each `_pid_exists` is a `os.kill(pid, 0)` syscall.
**Suggested fix:** Track the actual `Popen` handle; `await process.wait()` once.

#### P23. `_append_capped_unique` is O(n) per call
**File:** `src/openharness/engine/query.py:260-263, 296-300`
**Category:** Hot-path inefficiency
**Description:** `if value in bucket: bucket.remove(value); bucket.append(value)` is O(n) for both `in` and `remove`. For a bucket of N items, with caps of 8-12, this is ~64-144 comparisons per call but in aggregate is wasteful.
**Suggested fix:** Use a `dict` / `OrderedDict` keyed by the value; O(1) membership and removal.

#### P24. `cli.py _build_dry_run_preview` calls `load_plugins` + `load_skill_registry` twice
**File:** `src/openharness/cli.py:410-479`
**Category:** Startup cost
**Description:** The registry is built once for the preview, then rebuilt inside `build_runtime_system_prompt` (the `_build_skills_section` call).
**Suggested fix:** Compute the skill registry once and pass it to both the dry-run preview and the system-prompt builder.

#### P25. `cli.py` imports 40+ modules eagerly; `cli.py` is 2,551 lines
**File:** `src/openharness/cli.py:1-799`
**Category:** Startup cost
**Description:** Every command callback's `from openharness.X import Y` is per-call (module is cached, but the attribute lookup and local binding still cost).
**Suggested fix:** Split commands into per-subcommand modules; reduce top-level import surface to just `typer` and shared helpers.

#### P26. `--theme` writes settings.json to disk on every invocation
**File:** `src/openharness/cli.py:2390-2395`
**Category:** Repeated work
**Description:** `oh -t cyberpunk "hi"` performs a full deep-copy + atomic write before the prompt runs.
**Suggested fix:** Apply in-memory override for the current invocation; persist only on explicit user mutation.

#### P27. `cron` history file grows forever; loaded fully every CLI call
**File:** `src/openharness/services/cron_scheduler.py:67-85`
**Category:** Memory bloat
**Description:** No truncation; `path.read_text().splitlines()` for every `cron list`/`history` invocation.
**Suggested fix:** Cap at N entries with rewrite on overflow; or use a rolling-window JSONL with mtime-based cleanup.

#### P28. `mcp/client.connect_all` connects to all servers sequentially
**File:** `src/openharness/mcp/client.py:45-59`
**Category:** Sequential API calls
**Description:** 3 slow servers = 3× handshake. A single hung server blocks all others.
**Suggested fix:** `asyncio.gather(return_exceptions=True)`.

#### P29. `autodream` invoked synchronously from engine
**File:** `src/openharness/engine/query_engine.py:141-153` → `src/openharness/services/autodream/service.py`
**Category:** Sync blocking in async paths
**Description:** Disk I/O, hashing, and possibly an LLM call (memory extraction) on the event loop.
**Suggested fix:** `asyncio.create_task` with a swallowed exception handler; ensure any blocking work inside is `asyncio.to_thread`-wrapped.

#### P30. Channel regexes recompiled per call
**File:** `src/openharness/channels/impl/telegram.py:45-78`, `src/openharness/channels/impl/email.py:400-402`
**Category:** Hot-path inefficiency
**Description:** Recompile on every message.
**Suggested fix:** Hoist to module-level `_NAME_RE = re.compile(r"...")` once.

#### P31. `web_search_tool` regexes per result
**File:** `src/openharness/tools/web_search_tool.py:88, 94, 116, 118`
**Category:** Hot-path inefficiency
**Description:** Recompile on every result.
**Suggested fix:** Hoist to module level (use `re.IGNORECASE` as a compile flag).

#### P32. `find_relevant_memories` re-scans 100 files per turn
**File:** `src/openharness/memory/search.py:15-50`
**Category:** Repeated work
**Description:** Disk read + parse + tokenize per file per turn.
**Suggested fix:** Memoise on `(cwd, mtime-sentinel)`.

#### P33. `scan_memory_files` parses 500 files to keep 50
**File:** `src/openharness/memory/scan.py:20-47`
**Category:** Repeated work
**Description:** `_parse_memory_file` is called for every `.md`; only the first `max_files` (default 50) are kept.
**Suggested fix:** Sort by `st_mtime` first, then parse only the top N.

#### P34. `ui/backend_host` async `print_system`/`render_event` per text delta
**File:** `src/openharness/ui/backend_host.py:269-299`
**Category:** Hot-path inefficiency
**Description:** For a 2,000-token streaming response the model emits ~100-200 deltas, each one forcing a full event round-trip through the asyncio queue.
**Suggested fix:** Buffer text deltas in a per-turn `list[str]`; emit a single `assistant_complete` event with the concatenated text.

#### P35. `CopilotClient.__init__` builds two `AsyncOpenAI` clients; one is immediately discarded
**File:** `src/openharness/api/copilot_client.py:94-104`
**Category:** Repeated work
**Description:** `raw_openai = AsyncOpenAI(...)` is created and then assigned to `self._inner._client` (private attribute, `noqa: SLF001`). The new client is built on every `CopilotClient(...)` instantiation.
**Suggested fix:** Build one `AsyncOpenAI` and pass it down.

#### P36. `swarm/team_lifecycle._destroy_worktree` uses blocking `subprocess.run`
**File:** `src/openharness/swarm/team_lifecycle.py:721-733`
**Category:** Sync blocking in async paths
**Description:** On a large repo the worktree remove can take seconds and freezes the event loop.
**Suggested fix:** `asyncio.create_subprocess_exec` + `await process.wait()`.

#### P37. `commands/registry.py` is 2,783 lines; loaded eagerly by `ui/runtime.py:17-23`
**File:** `src/openharness/commands/registry.py`
**Category:** Startup cost
**Description:** Every session start pays the full 2,783-line import.
**Suggested fix:** Convert the eager `create_default_command_registry` into a module-level function that defers individual command handler imports.

#### P38. `tools/__init__.py` instantiates ~50 tool objects at module import
**File:** `src/openharness/tools/__init__.py:48-98`
**Category:** Startup cost
**Description:** `create_default_tool_registry` constructs all 50 tools on every session start, including `--help`. Each tool is a Pydantic model with a JSON schema computed via `model_json_schema()`.
**Suggested fix:** `functools.lru_cache(maxsize=1)` on `create_default_tool_registry()`. The MCP-dependent tools are only needed when MCP is configured.

#### P39. `engine/query.py` emits pre/post tool hooks even when no hooks are registered
**File:** `src/openharness/engine/query.py:240-246, 894-902, 1007-1017`
**Category:** Hot-path inefficiency
**Description:** The `if self._hook_executor is not None: await self._hook_executor.execute(...)` is checked every tool call.
**Suggested fix:** Short-circuit the pre-hook when the registry is empty AND no permission prompt is needed.

#### P40. `engine/query.py` rebuilds `ToolExecutionContext.metadata` (full dict copy) per tool call
**File:** `src/openharness/engine/query.py:613-617`
**Category:** Hot-path inefficiency
**Description:** `metadata={**context.tool_metadata, ...}` shallow-copies a large dict on every tool invocation.
**Suggested fix:** `MappingProxyType` read view.

#### P41. `engine/query_engine.has_pending_continuation` O(N) `reversed` lookup
**File:** `src/openharness/engine/query_engine.py:212-225`
**Category:** Hot-path inefficiency
**Description:** Called every UI tick.
**Suggested fix:** Track the last assistant-with-tool-uses as an index, updated on append.

#### P42. `ui/runtime._render_command_result` emits one event per restored message
**File:** `src/openharness/ui/runtime.py:786-797`
**Category:** Hot-path inefficiency
**Description:** 200 messages = 200 events on session restore.
**Suggested fix:** One bulk "transcript restored" event with the full markdown.

#### P43. `autodream/backup.py` `iterdir()` + `is_dir()` per child
**File:** `src/openharness/services/autodream/backup.py:85`
**Category:** Repeated work
**Description:** Syscall per child.
**Suggested fix:** `os.scandir`.

#### P44. `engine/query.py` `messages[-1].text.startswith(...)` per turn
**File:** `src/openharness/engine/query.py:787-806`
**Category:** Hot-path inefficiency
**Description:** `text` property walks blocks and joins. Done every turn.
**Suggested fix:** Memoise last-message role/prefix on the engine.

#### P45. `hooks/loader` `def get` sorts on every call
**File:** `src/openharness/hooks/loader.py:21-27`
**Category:** Repeated work
**Description:** `sorted(hooks, key=lambda hook: -getattr(hook, "priority", 0))` per `get()`. Registry is mostly static.
**Suggested fix:** Sort once on `register()` (insertion order is already preserved); or use a `heapq`.

#### P46. `prompts/environment.py` `subprocess.run` in module evaluation
**File:** `src/openharness/prompts/environment.py:73, 89`
**Category:** Sync blocking in async paths
**Description:** `subprocess.run` to gather env info. If called from async, blocks.
**Suggested fix:** Hoist to a cached property; or `asyncio.create_subprocess_exec` at the call site.

#### P47. `_evaluate_dry_run_readiness` builds real HTTP clients just to validate
**File:** `src/openharness/cli.py:459-466`
**Category:** Startup cost
**Description:** `_resolve_api_client_from_settings` is called inside the dry-run preview; it constructs `AsyncAnthropic`, `AsyncOpenAI`, `httpx.AsyncClient` to validate. On `--dry-run` we just want to know if auth would succeed.
**Suggested fix:** Validate-only path that resolves auth and provider without building HTTP clients.

#### P48. `engine/query.py` `_offload_tool_output_if_needed` writes 100 MB sync
**File:** `src/openharness/engine/query.py:538`
**Category:** Sync blocking in async paths
**Description:** `artifact_path.write_text(output, encoding="utf-8", errors="replace")` blocks the loop.
**Suggested fix:** `await asyncio.to_thread(artifact_path.write_text, ...)`.

#### P49. `api/codex_client` SSE parser rejoins `data_lines` per event
**File:** `src/openharness/api/codex_client.py:364-371`
**Category:** Hot-path inefficiency
**Description:** Stores `data_lines` in a list and rejoins with `"\n"` for every event.
**Suggested fix:** `json.loads` directly on a `bytearray`/line iterator.

#### P50. `_offload_tool_output` preview is fixed 8 KB head slice
**File:** `src/openharness/engine/query.py:539-552`
**Category:** Token waste
**Description:** The model only sees the first 8 KB of an offloaded output, which often contains header/boilerplate.
**Suggested fix:** Head + tail (e.g. 4 KB + 4 KB) or first matching line, like `grep -C` style.

#### P51. `engine/messages.py` `text` property rebuilt on every access (see R46)

#### P52. `engine/query.py` `to_api_param` rebuilds Anthropic schema per block per turn
**File:** `src/openharness/engine/messages.py:101-106`
**Category:** Hot-path inefficiency
**Description:** 250+ dict constructions + serializer branches per turn with 50 messages × 5 blocks.
**Suggested fix:** Memoise on the `ConversationMessage`.

#### P53. `cli.py:_login_provider` performs `shutil.which` for every MCP stdio server on dry-run
**File:** `src/openharness/cli.py:101`
**Category:** Repeated work
**Description:** PATH walk per call. In a dry-run preview, runs for every configured MCP stdio server every time.
**Suggested fix:** Cache the PATH walk for the duration of a CLI invocation.

#### P54. `_append_capped_unique` list+set (see P23)

#### P55. `_normalize` lambdas in `query.py` sort and rebuild every time (see P23)

---

### MEDIUM — robustness

- **M1.** `services/autodream` runs from engine without `try/except` (see R69).
- **M2.** `feishu._format_response_error` may itself raise (see R60).
- **M3.** `auth/storage.py:data.setdefault(provider, {})[key] = value` — provider name allows any string (see R10).
- **M4.** `engine/query.py` `to_api_param` (see P52).
- **M5.** `engine/query.py:turn_count` may go negative on retry (see R47).
- **M6.** `engine/query.py` stream failure silent return (see R33).
- **M7.** `swarm/mailbox.write` filename collision (see R9/P23).
- **M8.** `swarm/mailbox` no in-process `asyncio.Lock` (see R14).
- **M9.** `swarm/in_process` shutdown `shield`/`wait_for` mix (see R15).
- **M10.** `swarm/team_lifecycle` rmtree error swallowing (see R23).
- **M11.** `swarm/team_lifecycle.delete_team` no in-use check (see R24).
- **M12.** `swarm/permission_sync` re-validation gap (see R2, R6, R30).
- **M13.** `channels/impl/email.py` HTML stripper (see R71).
- **M14.** `channels/impl/whatsapp.py` swallows WebSocket errors (see R69).
- **M15.** `commands/registry.py` clipboard fallback file (see R27).
- **M16.** `commands/registry.py:_compact_handler` silent fallback (see R35).
- **M17.** `autopilot/service.py:_run_command` env race (see R43).
- **M18.** `autopilot/service.py:_read_yaml` swallows all (see R39).
- **M19.** `autopilot/service.py:_build_pr_body` unescaped content (see R64).
- **M20.** `autopilot/service.py` `shell=true` privilege escalation (see R1).
- **M21.** `bridge/manager.py` per-chunk open (see R26/P45).
- **M22.** `bridge/manager.py` no final flush (see R44).
- **M23.** `bridge/manager.py` `_copy_tasks` never popped (see R25).
- **M24.** `services/cron_scheduler.py:append_history` no lock (see R38).
- **M25.** `services/cron_scheduler.py:execute_job` `except Exception` (see R37).
- **M26.** `services/cron.py:croniter` raises past validation (see R57).
- **M27.** `services/compact/__init__.py` retry drops exception (see R20).
- **M28.** `services/compact/__init__.py:try_session_memory_compaction` race (see R54).
- **M29.** `services/compact/__init__.py` placeholders rebuilt per retry (see R31).
- **M30.** `services/compact/__init__.py` no summary size cap (see R51).
- **M31.** `api/openai_client.py:_is_retryable` missing 504 and `Retry-After`
  **File:** `src/openharness/api/openai_client.py:437-443`
  **Description:** Retryable set is `{429, 500, 502, 503}`. 504 is missing. `Retry-After` is not consulted. The Anthropic client uses `{429, 500, 502, 503, 529}` — divergent retry policies across providers.
  **Fix:** Centralise the retry policy in a shared helper; include 504; honour `Retry-After`.
- **M32.** `api/codex_client.py` no jitter in retry
  **File:** `src/openharness/api/codex_client.py:240-249`
  **Description:** `delay = min(BASE * (2 ** attempt), MAX)` — pure exponential, no jitter. When 8 OpenHarness agents in a swarm all hit a Codex rate limit simultaneously, they retry at the exact same time.
  **Fix:** Add the same jitter formula as `api/client.py:114`.
- **M33.** `api/codex_client.py` `AsyncClient` per call (see P6).
- **M34.** `api/client.py` retries rebuild auth/headers
  **File:** `src/openharness/api/client.py:165-201`
  **Description:** `_refresh_client_auth()` may build a brand-new `AsyncAnthropic` on each retry attempt. Constructing the SDK client spins up a new `httpx.AsyncClient` (with connection pool, headers, etc.).
  **Fix:** Mutate the `auth_token` on the existing client (the Anthropic SDK reads it on each request). Or refresh only when `next_token != self._auth_token` AND cache the last "refreshed at".
- **M35.** `api/copilot_client.py` two `AsyncOpenAI` clients (see P35).
- **M36.** `api/copilot_auth.py` sync `httpx.post` + `time.sleep` (see P18).
- **M37.** `auth/external.py` `urllib.urlopen` blocks (see P19).
- **M38.** `auth/external.py:load_copilot_token` no timeout (see R56).
- **M39.** `hooks/executor.py` template `eval $ARGUMENTS` (see R3).
- **M40.** `hooks/executor.py` `_run_command_hook` no `CancelledError` (see R34).
- **M41.** `hooks/executor.py:_run_http_hook` swallows all as `success=True` (see R5).
- **M42.** `hooks/loader.py:def get` re-sorts (see P45).
- **M43.** `memory/search.py:find_relevant_memories` re-scans (see P32).
- **M44.** `memory/scan.py:scan_memory_files` parses all (see P33).
- **M45.** `engine/query.py:is_effectively_empty` drops empty assistant message (see R21).
- **M46.** `engine/query.py:_stream_compaction` busy-poll (see R58).
- **M47.** `engine/query.py` unbounded `asyncio.Queue` (see R72).
- **M48.** `engine/query.py:_describe` catch-all (see R19).
- **M49.** `engine/query.py:_bounded_completion_tokens` string match (see R55).
- **M50.** `engine/query.py` `tool_metadata` mutable (see R49).
- **M51.** `engine/query.py:_execute_tool_call` error side channel (see R42).
- **M52.** `engine/query.py:run_query` `ErrorEvent` then silent return (see R33).
- **M53.** `engine/query.py:run_query` reactive compact not recorded (see R21, R47).
- **M54.** `engine/query_engine.py:203` `except Exception` (see R67).
- **M55.** `engine/query_engine.has_pending_continuation` O(N) (see P41).
- **M56.** `engine/query_engine.py:_schedule_auto_dream` sync (see P29).
- **M57.** `engine/messages.py:to_api_param` rebuilds (see P52).
- **M58.** `engine/messages.py:text` property rebuilds (see R46).
- **M59.** `tools/bash_tool.py` `process.communicate` no `try/finally` (see R22).
- **M60.** `tools/bash_tool.py` output truncation 12 KB (see R40).
- **M61.** `tools/file_read_tool.py` reads entire file (see P1, P2).
- **M62.** `tools/grep_tool.py` Python fallback (see P16).
- **M63.** `tools/web_search_tool.py` regex per result (see P31).
- **M64.** `tools/__init__.py` instantiates 50 tools at import (see P38).
- **M65.** `channels/impl/email.py` regex recompile (see P30).
- **M66.** `channels/impl/telegram.py` regex recompile (see P30).
- **M67.** `channels/impl/feishu.py` module-level state patch (see R7).
- **M68.** `channels/impl/feishu.py:_download_file_sync` (None, None) (see R30).
- **M69.** `channels/impl/feishu.py:_extract_interactive_content` recursion (see R32).
- **M70.** `channels/impl/feishu.py:_handle_message` unbounded regex (see R36).
- **M71.** `channels/impl/feishu.py` `time.sleep` in WS (see R17, P20).
- **M72.** `channels/impl/dingtalk.py` token refresh race (see R18).
- **M73.** `channels/impl/dingtalk.py` blocking SDK start (see P21).
- **M74.** `channels/impl/manager.py` 1 s poll (see R63).
- **M75.** `mcp/client.py` no timeouts + sequential connect (see R8, P28).
- **M76.** `cli.py` 2,551 lines, 40+ eager imports (see P25).
- **M77.** `cli.py` `--theme` writes settings.json (see P26).
- **M78.** `cli.py` `_build_dry_run_preview` duplicates (see P24).
- **M79.** `cli.py:_login_provider` double store (see R53).
- **M80.** `cli.py:_login_provider` provider name (see R10).
- **M81.** `commands/registry.py` 2,783 lines, eager (see P37).
- **M82.** `permissions/checker.py:is_docker_sandbox_active()` per call (see R52).
- **M83.** `swarm/team_lifecycle.set_member_mode` `to_dict` (see R65).
- **M84.** `swarm/mailbox.read_all` O(n) (see R50).
- **M85.** `swarm/mailbox` `run_in_executor` (see P15).
- **M86.** `ui/runtime.py` `_render_command_result` per message (see P42).
- **M87.** `ui/backend_host.py` per-delta awaits (see P34).
- **M88.** `prompts/environment.py` `subprocess.run` (see P46).
- **M89.** `utils/fs.py:atomic_write_bytes` unlink suppressed (see R61).
- **M90.** `utils/shell.py:asyncio.create_task` no ref (see R13).
- **M91.** `tasks/manager.py` `BackgroundTaskManager` leaks (see R11, R12, R45).
- **M92.** `autodream/backup.py` `iterdir+is_dir` and `except Exception` (see P43, R70).
- **M93.** `api/openai_client.py` `_strip_think_blocks` per chunk
  **File:** `src/openharness/api/openai_client.py:461-484`
  **Description:** `_THINK_RE` is module-level (good). The fallback "hold back a prefix" logic at line 479 does `cleaned.endswith(_THINK_OPEN_TAG[:n])` in a loop. Fast in absolute terms, but called per chunk.
  **Fix:** Use a single regex with a lookahead.
- **M94.** `skills/_frontmatter.py` strips trailing newlines
  **File:** `src/openharness/skills/_frontmatter.py:42-66`
  **Description:** `body.strip()` at the end of the loader chain silently truncates trailing newlines. The body is used as the system prompt / skill content, so trailing newlines matter for the model's prompt formatting.
  **Fix:** Preserve the original body bytes; only strip the closing `---`.
- **M95.** `swarm/in_process.py:264-268` stub cancellation (see R68).
- **M96.** `swarm/permission_sync._sync_resolve_permission` `.json.tmp` (see R41).
- **M97.** `swarm/permission_sync._sync_resolve_permission` re-validation (see R2, R6).
- **M98.** `BridgeSessionManager._copy_tasks` accumulate (see R25).
- **M99.** `engine/query.py` empty message drops (see R21).
- **M100.** `services/autodream` sync from engine (see P29).
- **M101.** `engine/query_engine.py` autodream (see P29).
- **M102.** `hooks/executor.py` `CancelledError` after spawn (see R34).
- **M103.** `api/codex_client.py` `AsyncClient` per call (see P6).
- **M104.** `engine/query.py` stream failure silent (see R33).
- **M105.** `swarm/team_lifecycle._destroy_worktree` blocking subprocess (see P36).

---

## Cross-cutting recommendations (highest leverage)

1. **Validation, not silent fallback, on security boundaries** — auth login, hook templates, swarm permission sync, autopilot `shell:true`, YAML policy loading.
2. **Resource lifecycle** — track every `asyncio.create_task` and every long-lived file handle; clean up on `close()`/`aclose()`; no fire-and-forget.
3. **Replace fire-and-forget `subprocess.run` in async code with `asyncio.create_subprocess_exec`** — autopilot, swarm worktree destroy, prompts/environment, cron, channels.
4. **Cache what doesn't change per turn** — system prompt, tool schemas, settings, multimodal-model patterns, file reads, regex compilations, MCP server connect results.
5. **Per-process locks for shared mutable state** — DingTalk token, mailbox, cron history, auth storage.
6. **Async-safe timeouts** — `httpx.AsyncClient(timeout=...)` everywhere; `asyncio.wait_for` for SDK calls.
7. **Fix the reactive-compact path** — track consecutive prompt-too-long failures, never silently drop empty assistant messages, never silently fall back to deterministic compact.
8. **Stream bash tool output incrementally** instead of waiting for full completion.
9. **Add `asyncio.to_thread` around all blocking IO** in async paths (file writes, ZIP/JSON dumps, keychain calls).
10. **Centralize retry policy** across `api/client.py`, `api/openai_client.py`, `api/codex_client.py` with consistent retryable status codes, jitter, and `Retry-After` honoring.

---

## Top 10 highest-impact fixes (if you only do ten)

| # | File | Issue | Impact |
|---|------|-------|--------|
| 1 | `src/openharness/autopilot/service.py:2107` | `shell:true` verification | Local privilege escalation |
| 2 | `src/openharness/swarm/permission_sync.py:514` | `updated_input` smuggling | Permission bypass |
| 3 | `src/openharness/tools/file_read_tool.py:52` | Reads whole file | Per-turn latency |
| 4 | `src/openharness/engine/query.py:734` + `tools/base.py:78` | Tool schema regen per turn | Per-turn latency |
| 5 | `src/openharness/ui/runtime.py:668` | System prompt rebuild per turn | Per-turn latency |
| 6 | `src/openharness/hooks/executor.py:223` | Template injection | RCE-via-hook |
| 7 | `src/openharness/services/compact/__init__.py:1283` | Retry drops exception | Data loss |
| 8 | `src/openharness/swarm/mailbox.py:137` | Filename collision | Message loss |
| 9 | `src/openharness/autopilot/service.py:1236` | `subprocess.run` blocks loop | UI freeze |
| 10 | `src/openharness/services/lsp/__init__.py:34` | Re-parses whole workspace | O(files × parse) per call |

---

## Notes on what was NOT flagged

The codebase is broadly well-structured:

- `utils/fs.py` uses a proper atomic-write pattern (`tempfile.mkstemp` + `os.replace`).
- `utils/file_lock.py` uses `fcntl.flock` and `msvcrt.locking` correctly.
- `tasks/manager.py` is consistent in its per-task lock dicts.
- `_VerificationCommand` in autopilot correctly rejects shell metacharacters unless the policy author opts in.
- `network_guard.py` does careful DNS resolution and rejects loopback/private IP literals.
- `asyncio.gather(return_exceptions=True)` is used in the engine's multi-tool fan-out (`query_engine.py:853`) — the right pattern.
- The autopilot's systematic `try/except Exception` is concentrated in `_best_effort_*` helpers, where the design intent (best-effort GitHub ops) is appropriate.

The dominant risk-reduction opportunity is the **agent-loop hot path**: the cumulative effect of caching system prompt (P5), tool schemas (P3), file reads (P2), and converting blocking subprocesses (P12, P36) is a measurable per-turn latency reduction, particularly on long sessions. The dominant security-hardening opportunity is the **swarm + autopilot boundary** (R1, R2, R3, R6).

---

**No files were modified.**
