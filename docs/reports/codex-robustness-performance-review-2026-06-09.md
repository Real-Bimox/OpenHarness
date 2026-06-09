# OpenHarness Robustness and Performance Review

Date: 2026-06-09
Scope: Python backend, ohmo gateway surfaces, task/bridge runtime, channel adapters, tools, and terminal frontend.

This report lists source-verified robustness and speed issues found during review. It intentionally excludes broad claims that were not confirmed against the current tree.

## Verification Notes

- `env PYTHONPATH=src:. python -m compileall -q src ohmo` passed.
- `python -m pytest ...` could not run because the local Python environment does not have `pytest` installed.
- The working tree already contained untracked report files under `docs/reports/`; they were not modified.

## High Severity

### 1. Channel adapters and config schema are out of sync

The compatibility config schema defines only a small subset of fields per channel, while adapters dereference many more fields directly. Enabling a channel with the schema defaults can fail with `AttributeError` or silently skip intended behavior.

References:

- `src/openharness/config/schema.py:42` defines `SlackConfig` with only `bot_token`, `app_token`, and `signing_secret`, but `src/openharness/channels/impl/slack.py:169` uses `reply_in_thread`, `src/openharness/channels/impl/slack.py:176` uses `react_emoji`, and `src/openharness/channels/impl/slack.py:208` uses nested `dm`.
- `src/openharness/config/schema.py:48` defines `DiscordConfig` with only `token`, but `src/openharness/channels/impl/discord.py:51` uses `gateway_url` and `src/openharness/channels/impl/discord.py:269` uses `group_policy`.
- `src/openharness/config/schema.py:71` defines `EmailConfig` without IMAP or consent fields, but `src/openharness/channels/impl/email.py:65` uses `consent_granted`, `src/openharness/channels/impl/email.py:78` uses `poll_interval_seconds`, and `src/openharness/channels/impl/email.py:157` uses `imap_host`.
- `src/openharness/config/schema.py:79` defines `QQConfig.app_secret`, but `src/openharness/channels/impl/qq.py:69` checks `self.config.secret`.
- `src/openharness/config/schema.py:91` defines `WhatsAppConfig` without `bridge_url`, but `src/openharness/channels/impl/whatsapp.py:38` uses it.
- `src/openharness/channels/impl/matrix.py:40` imports `openharness.config.loader`, which is not present in the repository.

Impact: Several channel integrations are not robustly startable from the declared configuration model.

Recommended fix: make the schema authoritative by adding adapter fields with defaults, update adapters to use existing names consistently, and add import/startup tests for each enabled channel path.

### 2. Long-running tool, MCP, and question waits can hang indefinitely

The main query loop awaits tool execution without an overall timeout. MCP calls and frontend questions similarly wait without a deadline.

References:

- `src/openharness/engine/query.py:970` awaits `tool.execute(...)` directly.
- `src/openharness/mcp/client.py:139` awaits `session.call_tool(...)` directly.
- `src/openharness/mcp/client.py:166` awaits `session.read_resource(...)` directly.
- `src/openharness/ui/backend_host.py:840` awaits a question response future with no timeout.

Impact: one hung tool, MCP server, or frontend crash can permanently stall the agent turn or UI session.

Recommended fix: wrap external/tool waits in bounded `asyncio.wait_for`, use per-tool configurable deadlines, and return structured timeout errors.

### 3. Parallel mutating tool calls can race and lose file updates

The engine runs multiple tool calls concurrently, but file-editing tools perform plain read-modify-write operations with no per-file lock or optimistic version check.

References:

- `src/openharness/engine/query.py:853` executes multiple tool calls with `asyncio.gather(...)`.
- `src/openharness/tools/file_write_tool.py:46` reads existing content for approval and `src/openharness/tools/file_write_tool.py:53` writes with `path.write_text(...)`.
- `src/openharness/tools/file_edit_tool.py:48` reads original content and `src/openharness/tools/file_edit_tool.py:63` writes the updated content.

Impact: if a model emits two edits to the same file in one turn, one update can overwrite the other. A crash during `write_text` can also leave a partial file.

Recommended fix: serialize mutating file tools per resolved path and use atomic writes. For edits, verify the file has not changed between approval and write.

### 4. Background task and bridge logs are unbounded and expensive to read

Task and bridge output logs append indefinitely. Tail reads load the full file into memory before slicing.

References:

- `src/openharness/tasks/manager.py:281` appends every subprocess output chunk to the task log.
- `src/openharness/tasks/manager.py:234` reads the full task log before returning the tail.
- `src/openharness/bridge/manager.py:92` appends bridge output chunks.
- `src/openharness/bridge/manager.py:74` reads the full bridge log before returning the tail.
- `src/openharness/bridge/manager.py:30` stores session dictionaries with no cleanup path after process completion.

Impact: a noisy background task can consume disk, and later `task_output` or bridge-output reads can become slow or memory-heavy.

Recommended fix: rotate or cap logs, implement true tail reads from the end of the file, and prune completed bridge/session records.

### 5. Channel message queues are unbounded

The channel bus uses unbounded queues for both inbound and outbound messages.

References:

- `src/openharness/channels/bus/queue.py:17`
- `src/openharness/channels/bus/queue.py:18`

Impact: a remote message flood or stuck consumer can grow memory until the process is killed.

Recommended fix: set bounded queue sizes, apply backpressure, and define drop/reject behavior for overload.

## Medium Severity

### 6. File and image tools read whole files before applying limits

Several tools load complete files into memory even when the user requested a small slice or when only a bounded payload should be accepted.

References:

- `src/openharness/tools/file_read_tool.py:52` reads all bytes before slicing lines.
- `src/openharness/tools/grep_tool.py:127` reads each candidate file fully in the Python fallback.
- `src/openharness/tools/image_to_text_tool.py:160` reads the full image file before base64 encoding.
- `src/openharness/tools/image_generation_tool.py:303` reads full input images before base64 encoding.

Impact: large files can cause high memory usage or slow tool execution.

Recommended fix: stream line-limited reads, enforce maximum input file sizes, and make fallback grep line-streaming with timeout support.

### 7. Glob fallback can traverse and sort too much

The Python glob fallback sorts all matches before applying `limit`.

Reference:

- `src/openharness/tools/glob_tool.py:172`

Impact: recursive or broad patterns can be slow and memory-heavy when `rg` is unavailable.

Recommended fix: use an iterator with early stop, skip heavy directories consistently, and avoid sorting unbounded result sets.

### 8. LSP workspace operations rescan and reparse every file per request

Workspace symbol, definition, hover, and reference flows repeatedly walk and parse/read Python files with no cache.

References:

- `src/openharness/services/lsp/__init__.py:42`
- `src/openharness/services/lsp/__init__.py:68`
- `src/openharness/services/lsp/__init__.py:89`
- `src/openharness/services/lsp/__init__.py:142`

Impact: code-intelligence operations become slow on large repositories.

Recommended fix: maintain a workspace symbol index keyed by path, mtime, and size; invalidate changed files only.

### 9. Plugin markdown discovery follows symlinks without cycle controls

Plugin command/agent discovery walks markdown files with `followlinks=True` and no visited inode set, depth limit, or file count limit.

References:

- `src/openharness/plugins/loader.py:207`
- `src/openharness/plugins/loader.py:438`

Impact: a symlink cycle or large linked tree can hang or greatly slow startup/plugin loading.

Recommended fix: avoid following symlinks by default, or track visited directories and enforce traversal limits.

### 10. Terminal frontend stores unbounded transcript/history and reparses streaming Markdown

The Ink frontend only renders recent transcript rows, but keeps full transcript state and reparses the whole streaming assistant buffer on each flush.

References:

- `frontend/terminal/src/hooks/useBackendSession.ts:75` appends transcript items without a cap.
- `frontend/terminal/src/hooks/useBackendSession.ts:62` grows `assistantBufferRef.current` as one string.
- `frontend/terminal/src/hooks/useBackendSession.ts:306` appends all Codex-style deltas to the buffer.
- `frontend/terminal/src/components/MarkdownText.tsx:309` lexes the full content whenever `content` changes.
- `frontend/terminal/src/App.tsx:56` stores user command history without a cap.

Impact: long sessions and long streamed responses can become progressively slower and memory-heavy.

Recommended fix: cap retained transcript/history, store large transcript segments outside React state, and render streaming text incrementally instead of reparsing full Markdown on every flush.

### 11. Malformed backend protocol lines can crash the terminal UI

The frontend parses backend protocol lines without error handling.

Reference:

- `frontend/terminal/src/hooks/useBackendSession.ts:134`

Impact: one malformed `OHJSON:` line can crash the frontend instead of showing an error and continuing.

Recommended fix: wrap protocol parsing in `try/catch`, add an error transcript item, and keep reading subsequent lines.

### 12. Bad settings files or numeric env vars crash startup

Settings loading parses JSON and numeric environment variables without recovery.

References:

- `src/openharness/config/settings.py:1062` parses `settings.json`.
- `src/openharness/config/settings.py:964` parses `OPENHARNESS_MAX_TOKENS`.
- `src/openharness/config/settings.py:968` parses `OPENHARNESS_TIMEOUT`.

Impact: a corrupted settings file or typo in environment variables can prevent startup entirely.

Recommended fix: catch `JSONDecodeError`/`ValidationError` and invalid env values, report actionable diagnostics, and preserve a backup of invalid settings.

### 13. OAuth token refresh leaks the previous Anthropic client

Refreshing Claude OAuth auth replaces the client object without closing the old HTTP client.

Reference:

- `src/openharness/api/client.py:163`

Impact: long-running sessions that refresh credentials can leak HTTP resources.

Recommended fix: close the previous client before replacing it, or recreate clients through a managed async context.

### 14. `todo_write` path handling can escape the workspace

`todo_write` joins `context.cwd` with user-supplied `path` but does not resolve/validate that the result stays in the workspace or sandbox policy.

References:

- `src/openharness/tools/todo_write_tool.py:28`
- `src/openharness/tools/todo_write_tool.py:45`

Impact: an agent-controlled `path` such as `../../somewhere` can write outside the project tree.

Recommended fix: resolve the path, reject paths outside `context.cwd` unless explicitly allowed, and use the same sandbox/path validation pattern as other file tools.

### 15. Atomic write helper temporarily changes process umask

For new files, `_resolve_target_mode` reads the process umask by setting it to zero and restoring it.

Reference:

- `src/openharness/utils/fs.py:86`

Impact: `umask` is process-global, so concurrent file creation in another thread can observe the temporary zero umask.

Recommended fix: avoid changing process umask at runtime, or guard this operation with a process-wide lock.

## Suggested Priority Order

1. Fix channel schema/adapter mismatches and add channel startup tests.
2. Add bounded timeouts for tool execution, MCP calls, and frontend question prompts.
3. Serialize file mutations per path and switch write/edit/todo writes to atomic writes.
4. Cap and rotate task/bridge logs, then implement true tail reads.
5. Add memory and traversal limits to file/image/glob/plugin/LSP paths.
6. Harden the terminal frontend against malformed backend messages and long streaming responses.
7. Add recovery diagnostics for corrupted settings and invalid environment overrides.
