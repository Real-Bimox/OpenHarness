# OpenHarness — Comprehensive Robustness & Performance Audit

**Date:** 2026-06-09
**Scope:** Full codebase (Python backend, TypeScript frontend, ohmo gateway, autopilot, swarm)

---

## Summary

| Severity | Count |
|----------|-------|
| **HIGH** | ~35 |
| **MEDIUM** | ~60 |
| **LOW** | ~40 |
| **Total** | ~135 |

---

## HIGH Severity Issues

### 1. Missing Timeouts — Pervasive Indefinite Hangs

The single largest class of critical issues. Many async operations have **no timeout**, meaning a single hung network call, subprocess, or API stream blocks the entire system indefinitely.

| Location | Operation |
|----------|-----------|
| `src/openharness/engine/query.py:970` | `tool.execute()` — any misbehaving tool blocks the query loop |
| `src/openharness/api/client.py:138-151` | Anthropic streaming — no timeout on `stream_api.stream()` |
| `src/openharness/api/openai_client.py:267-277` | `AsyncOpenAI` created without default timeout |
| `src/openharness/mcp/client.py:129-178,252-298` | All MCP session ops (`call_tool`, `read_resource`, `initialize`) |
| `src/openharness/tools/mcp_tool.py:28-33` | MCP tool calls with no timeout |
| `src/openharness/autopilot/service.py:627-1150` | `run_card()` — entire autopilot flow, no overall timeout |
| `src/openharness/autopilot/service.py:1236-1299` | All git/gh subprocess calls (`push`, `fetch`, etc.) |
| `src/openharness/swarm/worktree.py:87-102,195-200` | All git worktree operations |
| `src/openharness/swarm/in_process.py:335-395` | Query loop in swarm teammate |
| `src/openharness/tasks/manager.py:249-271` | `process.wait()` with no timeout |
| `src/openharness/tasks/manager.py:215-229` | `process.stdin.drain()` with no timeout |
| `src/openharness/hooks/executor.py:169-212` | Prompt-like hook streaming with no timeout |
| `ohmo/runtime.py:108-116,135-144` | `npm install` and frontend process with no timeout |
| `ohmo/gateway/runtime.py:224-503` | `stream_message()` engine loop with no timeout |

### 2. Security Vulnerabilities

| Location | Issue |
|----------|-------|
| `src/openharness/tools/file_read_tool.py:38-45`, `file_write_tool.py:35-42`, `file_edit_tool.py:36-43` | **Sandbox path validation skipped** when Docker is inactive — any file on the filesystem can be read/written |
| `src/openharness/tools/todo_write_tool.py:28` | **Path traversal** — `../../etc/crontab` escapes project directory |
| `src/openharness/bridge/work_secret.py:37` | **Substring match for localhost** — `notlocalhost.example.com` matches, can send tokens over cleartext `ws://` |
| `src/openharness/bridge/work_secret.py:17-32` | **No URL validation** on decoded `api_base_url` — tokens sent to attacker-controlled endpoint |
| `src/openharness/sandbox/docker_backend.py:98-125` | **Docker socket exposure** — `extra_mounts` can expose `/var/run/docker.sock` |
| `src/openharness/plugins/loader.py:691-729` | **Arbitrary code execution** — any `.py` in plugin `tools/` dir is imported and executed |
| `src/openharness/ui/react_launcher.py:107` | **API key in process listing** — `--api-key` visible via `ps` |
| `src/openharness/tasks/manager.py:136-141` | **API key in argv** — same `ps` exposure for task subprocesses |
| `src/openharness/utils/fs.py:86-88` | **Thread-unsafe umask** — `os.umask(0)` creates world-readable files from other threads |
| `src/openharness/auth/external.py:478-502` | **Race condition in credential write** — no file locking, lost updates |

### 3. Resource Leaks & Memory Issues

| Location | Issue |
|----------|-------|
| `src/openharness/api/client.py:163` | **HTTP client leaked on token refresh** — previous `AsyncAnthropic` never closed |
| `src/openharness/channels/bus/queue.py:17-18` | **Unbounded async queues** — `asyncio.Queue()` with no `maxsize`, OOM under flood |
| `src/openharness/channels/adapter.py:78-92` | **Head-of-line blocking** — sequential message processing, one slow call blocks all channels |
| `src/openharness/bridge/manager.py:30-44` | **Session dicts never cleaned** — monotonic growth, memory leak |
| `src/openharness/tools/file_read_tool.py:52-58` | **Entire file read before slicing** — multi-GB file causes OOM |
| `src/openharness/state/store.py:23` | **Mutable state exposed via `get()`** — bypasses listener notification |
| `src/openharness/state/store.py:28-29` | **Listener crash propagation** — one failing listener blocks all subsequent |
| `ohmo/gateway/runtime.py:121` | **RuntimeBundle dict unbounded** — never evicted, memory leak |

### 4. Crash-on-Corruption — Missing Error Handling

| Location | Issue |
|----------|-------|
| `src/openharness/config/settings.py:1062` | Corrupted `settings.json` crashes startup (`json.JSONDecodeError`) |
| `src/openharness/config/settings.py:962-980` | Bad env vars (`OPENHARNESS_MAX_TOKENS=abc`) crash with `ValueError` |
| `src/openharness/memory/search.py:38`, `memory/usage.py:127` | **N+1 disk reads** — `usage_index.json` re-read per memory file (100x amplification) |
| `frontend/terminal/src/hooks/useBackendSession.ts:134` | `JSON.parse` with no try/catch — malformed backend message crashes frontend |
| `ohmo/session_storage.py:89-127` | All session JSON reads lack error handling |
| `ohmo/gateway/config.py:13-18` | Corrupted gateway config crashes startup |

### 5. Frontend Critical Issues

| Location | Issue |
|----------|-------|
| `frontend/terminal/src/hooks/useBackendSession.ts:115-177` | **Stale closure** — `handleEvent` captures initial `busy=false`, status events never update |
| `src/openharness/ui/backend_host.py:840` | `_ask_question` has no timeout — frontend crash = permanent hang |
| `src/openharness/commands/registry.py:208-210,2599` | Synchronous `subprocess.run` in async handlers — blocks event loop (git, editor) |

---

## MEDIUM Severity Issues

### Performance

| # | Location | Issue |
|---|----------|-------|
| 1 | `src/openharness/services/lsp/__init__.py:42-93` | LSP workspace ops are O(n*m) with no caching — extremely slow on large codebases |
| 2 | `src/openharness/engine/query.py:264,271,283` | Full message list copied every turn — O(n) per turn |
| 3 | `src/openharness/services/compact/__init__.py:116-131` | O(n*m) token estimation on every compact check |
| 4 | `src/openharness/ui/textual_app.py:329-330` | O(N^2) rendering — full buffer re-rendered on every token delta |
| 5 | `src/openharness/memory/scan.py:31-47` | All memory .md files re-read on every call, no caching |
| 6 | `src/openharness/utils/network_guard.py:157-162` | New `httpx.AsyncClient` per HTTP request — no connection pooling |
| 7 | `src/openharness/api/codex_client.py:276` | New `httpx.AsyncClient` per API call — no connection reuse |
| 8 | `src/openharness/config/settings.py:614-627` | `merged_profiles()` deep-copies all profiles on every call |
| 9 | `src/openharness/utils/network_guard.py:206` | `load_settings()` called on every HTTP fetch |
| 10 | `src/openharness/hooks/executor.py:64-78` | Hooks execute sequentially — should parallelize independent hooks |
| 11 | `src/openharness/hooks/executor.py:144-167` | New HTTP client per hook invocation |
| 12 | `src/openharness/swarm/mailbox.py:153-209` | O(n) filesystem scan per mailbox poll |
| 13 | `src/openharness/bridge/manager.py:85-94` | File opened/closed per 4KB chunk — O(n) syscalls |
| 14 | `src/openharness/services/session_storage.py:138` | O(n log n) `stat()` calls during session listing |

### Blocking I/O in Async Context

| # | Location | Issue |
|---|----------|-------|
| 15 | `src/openharness/engine/query.py:538` | Synchronous file write in `_offload_tool_output_if_needed` |
| 16 | `src/openharness/engine/query_engine.py:179-184` | Synchronous file I/O in `_update_session_memory` |
| 17 | `src/openharness/services/cron_scheduler.py:63-64` | Synchronous file write in `append_history` |
| 18 | `src/openharness/channels/impl/discord.py:244` | Blocking file write in Discord attachment download |
| 19 | `src/openharness/channels/impl/telegram.py:258,403` | Blocking file I/O in Telegram send/download |
| 20 | `src/openharness/tools/enter_worktree_tool.py:47-53` | Synchronous `subprocess.run` in async `execute()` |
| 21 | `src/openharness/commands/registry.py:929-960` | `rglob("*")` traverses entire tree synchronously |
| 22 | `ohmo/gateway/runtime.py:623-648` | Synchronous session save in async method |
| 23 | `src/openharness/utils/shell.py:131-140` | Blocking `subprocess.run` for bash validation (up to 5s) |

### Race Conditions & Concurrency

| # | Location | Issue |
|---|----------|-------|
| 24 | `src/openharness/services/autodream/lock.py:52-75` | TOCTOU race in lock acquisition |
| 25 | `src/openharness/auth/storage.py:83-110` | Keyring availability check not thread-safe |
| 26 | `src/openharness/channels/impl/dingtalk.py:176-201` | Token refresh without locking |
| 27 | `src/openharness/channels/impl/email.py:314` | Non-deterministic set eviction — random half evicted |
| 28 | `src/openharness/sandbox/path_validator.py:18-24` | TOCTOU symlink race — sandbox escape |
| 29 | `ohmo/workspace.py:264-278` | Workspace init without file locking |
| 30 | `src/openharness/autopilot/service.py:396-398,1901-1908` | Journal/registry writes without file locking |

### Missing Reconnection / Cleanup

| # | Location | Issue |
|---|----------|-------|
| 31 | `src/openharness/channels/impl/slack.py:61-64` | No reconnection — Slack channel permanently deaf after network blip |
| 32 | `src/openharness/channels/impl/feishu.py:494-513` | No clean shutdown — daemon thread lingers |
| 33 | `src/openharness/mcp/client.py:103-109` | `close()` loop aborts on unexpected exception — remaining stacks leaked |
| 34 | `src/openharness/tasks/manager.py:375-393` | `process.kill()` without `await process.wait()` — zombie processes |
| 35 | `src/openharness/autopilot/service.py:675-684` | Worktree not cleaned on all failure paths |

### Other Notable Medium Issues

| # | Location | Issue |
|---|----------|-------|
| 36 | `src/openharness/api/codex_client.py:240`, `openai_client.py:299` | Retry backoff lacks jitter — thundering herd |
| 37 | `src/openharness/api/copilot_auth.py:160-219` | Synchronous blocking OAuth in async context |
| 38 | `src/openharness/tools/file_write_tool.py:59`, `file_edit_tool.py:67` | Non-atomic file writes — corruption on crash |
| 39 | `src/openharness/tools/file_edit_tool.py:17` | Empty `old_str` accepted — silently prepends content |
| 40 | `src/openharness/tools/glob_tool.py:172-175`, `grep_tool.py:127-132` | Python fallback has no timeout / reads entire files |
| 41 | `src/openharness/tools/image_generation_tool.py:198-226` | `AsyncOpenAI` clients never closed |
| 42 | `src/openharness/plugins/loader.py:207` | `os.walk(followlinks=True)` — infinite loop on symlink cycle |
| 43 | `src/openharness/plugins/loader.py:670` | Shell variable injection in hook commands |
| 44 | `src/openharness/config/schema.py:15` | `extra="allow"` silently accepts typos in config keys |
| 45 | `src/openharness/permissions/checker.py:122` | Case-sensitive `fnmatch` — `RM -RF /` bypasses `rm -rf *` deny |
| 46 | `src/openharness/ui/coordinator_drain.py:68` | Unbounded 100ms polling loop with no max wait |
| 47 | `src/openharness/cli.py:2390-2395` | `--theme` silently persists to disk |
| 48 | `autopilot-dashboard/src/App.tsx` | No React Error Boundary; no fetch abort on unmount |
| 49 | `frontend/terminal/src/components/PromptInput.tsx:35` | Uses Ink internal API `internal_eventEmitter` |
| 50 | `ohmo/runtime.py:160-161` | `os.chdir()` in async function — not coroutine-safe |
| 51 | `src/openharness/api/copilot_client.py:94-104` | Copilot client leaks original `OpenAICompatibleClient` HTTP session |
| 52 | `src/openharness/api/codex_client.py:358-383` | SSE event iterator has no per-line read timeout (slowloris) |
| 53 | `src/openharness/channels/impl/discord.py:297` | `_start_typing` crashes if `self._http` is None after stop |
| 54 | `src/openharness/channels/impl/manager.py:226-233` | Outbound messages silently dropped for unknown channels |
| 55 | `src/openharness/tools/image_generation_tool.py:213-214` | File handle leak on error path |
| 56 | `src/openharness/tools/image_to_text_tool.py:160-161` | Entire image loaded into memory with no size bound |
| 57 | `src/openharness/memory/relevance.py:80,94` | Scans up to 200 memory files without caching |
| 58 | `src/openharness/memory/manager.py:118` | Substring match for index dedup — false positives |
| 59 | `src/openharness/permissions/checker.py:109-117` | Path rules can deny but never explicitly allow |
| 60 | `src/openharness/permissions/checker.py:129-130` | `FULL_AUTO` mode allows all tools unconditionally |

---

## LOW Severity Issues

| # | Location | Issue |
|---|----------|-------|
| 1 | `src/openharness/engine/query.py:686-696` | Busy-wait polling (50ms timeout on queue.get) |
| 2 | `src/openharness/engine/query.py:159-163` | O(n) list ops where deque would be O(1) |
| 3 | `src/openharness/engine/messages.py:146,169` | O(n^2) sanitization worst case |
| 4 | `src/openharness/engine/query_engine.py:147-153` | Fire-and-forget task without stored reference |
| 5 | `src/openharness/engine/query_engine.py:237` | Sanitization called on every submit — O(n) rebuild |
| 6 | `src/openharness/coordinator/agent_definitions.py:735` | No size limit on agent file reads |
| 7 | `src/openharness/coordinator/agent_definitions.py:942-943` | `except Exception: pass` silently swallows plugin errors |
| 8 | `src/openharness/coordinator/agent_definitions.py:950-953` | O(n) agent lookup — should use dict |
| 9 | `src/openharness/coordinator/coordinator_mode.py:129-156` | Fragile XML parsing with regex |
| 10 | `src/openharness/coordinator/coordinator_mode.py:207-209` | Non-thread-safe `os.environ` modification |
| 11 | `src/openharness/services/compact/__init__.py:808-856` | Many temporary object allocations in microcompact |
| 12 | `src/openharness/services/compact/__init__.py:1419` | Fragile `locals()` usage for retry tracking |
| 13 | `src/openharness/services/cron_scheduler.py:73` | Entire history file read into memory |
| 14 | `src/openharness/services/cron_scheduler.py:343-346` | Hardcoded 300s job timeout |
| 15 | `src/openharness/services/autodream/service.py:201-202` | Silent exception swallowing in auth resolution |
| 16 | `src/openharness/services/autodream/service.py:306-313` | Fire-and-forget task without reference |
| 17 | `src/openharness/auth/external.py:260-267` | No timeout on macOS keychain subprocess |
| 18 | `src/openharness/auth/external.py:48,356-385` | Global caches without thread safety |
| 19 | `src/openharness/channels/impl/dingtalk.py:136` | HTTP client has no explicit timeout |
| 20 | `src/openharness/channels/impl/manager.py:213` | Dispatch loop relies solely on external cancellation |
| 21 | `src/openharness/tools/config_tool.py:48-50` | Crashes on non-numeric values |
| 22 | `src/openharness/tools/web_search_tool.py:45` | Agent-controlled search endpoint override |
| 23 | `src/openharness/tools/notebook_edit_tool.py:71` | No JSON validation for notebook files |
| 24 | `src/openharness/plugins/loader.py:709` | `sys.modules` pollution from plugin tools |
| 25 | `src/openharness/plugins/installer.py:23-30` | No size limit on plugin install |
| 26 | `src/openharness/plugins/installer.py:28` | `shutil.rmtree` without confirmation or backup |
| 27 | `src/openharness/sandbox/docker_image.py:29-41,44-90` | No timeout on Docker image check/build |
| 28 | `src/openharness/sandbox/adapter.py:134-148` | Temp file leak on SIGKILL |
| 29 | `src/openharness/sandbox/session.py:16` | Module-level global session not thread-safe |
| 30 | `src/openharness/state/store.py:36-38` | O(n) unsubscribe with first-match removal |
| 31 | `src/openharness/config/paths.py` (multiple) | No error handling for `mkdir` on read-only filesystems |
| 32 | `src/openharness/memory/scan.py:91` | Redundant `stat()` call after `read_text()` |
| 33 | `src/openharness/memory/schema.py:99` | Translation table re-created per call |
| 34 | `src/openharness/memory/schema.py:415-418` | Theoretical infinite loop in ID generation |
| 35 | `src/openharness/utils/file_lock.py:81` | Windows lock has fixed 10s timeout |
| 36 | `src/openharness/utils/shell.py:104` | Fire-and-forget cleanup task may be GC'd |
| 37 | `src/openharness/ui/backend_host.py:41-43` | Duplicate logger initialization |
| 38 | `src/openharness/ui/app.py:243` | Unbounded string concatenation in print mode |
| 39 | `src/openharness/ui/backend_host.py:169` | No input validation on line length |
| 40 | `src/openharness/cli.py:1003-1004` | Entire log file read for tail operation |
| 41 | `src/openharness/commands/registry.py:228-233` | Synchronous `subprocess.run` for clipboard |
| 42 | `src/openharness/commands/registry.py:1196-1197` | No error handling on feedback log write |
| 43 | `src/openharness/config/settings.py:57` | No validation of `denied_commands` patterns at load time |
| 44 | `swarm/permission_sync.py:422,566,615` | Deprecated `get_event_loop()` — use `get_running_loop()` |
| 45 | `swarm/permission_sync.py:1035-1074` | Hardcoded 0.5s polling with no backoff |
| 46 | `swarm/team_lifecycle.py:609` | Module-level mutable set without synchronization |
| 47 | `swarm/team_lifecycle.py:263-268` | `rename()` not atomic on Windows — use `os.replace()` |
| 48 | `swarm/registry.py:400-405` | Singleton creation not thread-safe |
| 49 | `swarm/subprocess_backend.py:87-110` | Stale task IDs for crashed agents |
| 50 | `hooks/loader.py:20-27` | `sorted()` on every `get()` — should cache |
| 51 | `hooks/hot_reload.py:22-30` | Config reload without file locking |
| 52 | `ohmo/gateway/bridge.py:79-83` | Tight 1s polling with no backoff |
| 53 | `ohmo/group_registry.py:76` | Non-atomic write for group records |
| 54 | `autopilot/service.py:2109-2117` | No overall timeout for verification suite |
| 55 | `frontend/terminal/src/App.tsx:488-501` | Stale closure in scripted automation |
| 56 | `frontend/terminal/src/components/ConversationView.tsx:55,64` | Array index as React key |
| 57 | `frontend/terminal/src/components/TodoPanel.tsx:31-35` | `useInput` always active regardless of focus |
| 58 | `frontend/terminal/src/components/SwarmPanel.tsx:50-54` | `useInput` always active regardless of focus |
| 59 | `frontend/terminal/src/index.tsx:40` | `JSON.parse` of env var with no try/catch |
| 60 | `frontend/terminal/src/hooks/useBackendSession.ts:437-461` | `useMemo` with 15 dependencies defeats purpose |
| 61 | `frontend/terminal/src/components/StatusBar.tsx:22-32` | `prevMode` anti-pattern causes double render |
| 62 | `frontend/terminal/src/components/MarkdownText.tsx:139` | `MarkdownBlock` not memoized |
| 63 | `frontend/terminal/src/components/TranscriptPane.tsx:19` | Unstable key construction |
| 64 | `autopilot-dashboard/src/App.tsx:55,116` | Array index as React key |
| 65 | `autopilot-dashboard/src/App.tsx:30,79,105` | Components not memoized |

---

## Top 5 Priority Recommendations

### 1. Add Timeouts Everywhere

Wrap all `tool.execute()`, subprocess calls, API streams, and MCP operations with `asyncio.wait_for()`. This single change eliminates ~35 high-severity hang vectors.

**Example pattern:**
```python
result = await asyncio.wait_for(tool.execute(...), timeout=120.0)
```

### 2. Fix Security Gaps

- Enforce sandbox path validation regardless of backend (not just when Docker is active)
- Add URL validation in `build_sdk_url` — parse the URL properly instead of substring matching
- Pass API keys via environment variables instead of command-line arguments
- Add file locking for credential writes in `auth/external.py`
- Validate `extra_mounts` in Docker sandbox to reject `/var/run/docker.sock`

### 3. Move Blocking I/O to Thread Pool

Use `asyncio.to_thread()` for all synchronous file operations and `subprocess.run()` calls in async contexts. This fixes ~15 event-loop-blocking issues.

**Example pattern:**
```python
content = await asyncio.to_thread(path.read_text, encoding="utf-8")
```

### 4. Close HTTP Clients and Store Task References

- Fix resource leaks in API clients (Anthropic, OpenAI, Codex, image generation)
- Use `async with` for HTTP clients or explicitly call `.close()`
- Store `create_task()` references to prevent GC and handle exceptions
- Add cleanup for bridge session dicts and gateway RuntimeBundles

### 5. Add Caching for Hot-Path Operations

- Cache memory scan results (invalidate on file write)
- Cache LSP symbol indexes
- Cache usage index (load once, not per memory file)
- Cache settings profiles (avoid redundant deep copies)
- Cache hook sort order (invalidate on register)

---

## Architecture-Level Observations

1. **No structured error recovery:** The codebase catches broad `Exception` in many places but has no retry/backoff strategy for transient failures.

2. **Missing cancellation propagation:** Many async loops and subprocess waits do not respect `asyncio.CancelledError`, making graceful shutdown unreliable.

3. **No observability:** Beyond basic logging, there are no metrics, tracing, or health checks. Issues like unbounded queue growth or memory leaks are invisible until they cause OOM.

4. **Inconsistent async patterns:** Some modules use `asyncio.to_thread()` correctly, others use blocking calls. A project-wide convention is needed.

5. **No integration tests for failure modes:** The test suite does not cover timeout scenarios, network partitions, or corrupted config files.
