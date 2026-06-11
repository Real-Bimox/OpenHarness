# Changelog

All notable changes to OpenHarness should be recorded in this file.

The format is based on Keep a Changelog, and this project currently tracks changes in a lightweight, repository-oriented way.

## [0.1.17] - 2026-06-11

### Fixed

- Headless and agent session search now run the local index read directly instead of through the default asyncio thread executor, fixing the deterministic first-run search hang seen in executor-restricted environments.
- Programmatic headless input streams now read directly instead of using the thread executor, so injected stream tests and embedded callers do not inherit the same executor startup failure.

## [0.1.16] - 2026-06-11

### Fixed

- Conversation-search surfaces are bounded by a hard 20s timeout with worker-stack diagnostics on expiry — the protocol can no longer hang even if the index layer blocks (reported first-run hang; not reproducible locally in nine configurations, fixed structurally and pinned by tests).
- `conversation_index_enabled=false` is honored by every surface (CLI exits 1, headless answers an error event, MCP returns an error payload) via one shared gate.
- `--mcp-serve` rejects conflicting mode flags with exit 1 instead of silently starting the server.
- `ResilientApiClient` classifies translated terminal errors and falls back only when the classifier says so, instead of blanket-fallbacking under a hardcoded `auth` reason.
- Lint clean (`ruff check .`); disallowed emoji removed from README; five implemented proposals marked IMPLEMENTED and their merged branches archived per AGENTS.md.

## [0.1.15] - 2026-06-11

### Added

- **Conversation search.** A rebuildable SQLite FTS5 index over saved session snapshots powers a `session_search` agent tool (discover/read/scroll/browse, zero LLM cost), an `oh sessions list|search|reindex` CLI, a headless `search_sessions` request, and an MCP tool. Secrets are redacted before indexing; message bodies are budgeted; queries are sanitized into valid FTS5. Gated by `conversation_index_enabled` (default on). Learned from hermes-agent; see `docs/proposals/conversation-search.md`.
- **Skill learning loop.** A `skill_manage` agent tool (create/edit/patch/delete/write_file/remove_file, structurally confined to user skills, write scanning on by default), a post-turn background review fork that may create or improve skills, usage telemetry with an active/stale/archived lifecycle and pinning, a weekly LLM curator (no shell, no archive quota), and staged write approval with faithful diffs. New `SkillSettings` group; `oh skills usage|pin|unpin|pending|diff|approve|discard|curator` CLI; headless `skill_loop_status`. See `docs/proposals/skill-learning-loop.md`.
- **Error recovery, fallback chains, and credential rotation.** A typed, declarative error classifier; a resilient wrapper client running the recovery state machine (rotate/fallback/backoff/restore) with one hard attempt budget; provider fallback chains (`oh fallback list|add|remove|clear`) with mid-turn switching; per-provider API-key pools with cooldowns; new `ProviderFallbackEvent`/`CredentialRotatedEvent` surfaced through stream-json and headless. See `docs/proposals/error-recovery.md`.
- **MCP server mode** (`oh --mcp-serve`): a stdio MCP server exposing `search_sessions`, `list_sessions`, `skill_loop_status`, `run_skill_curator`, and `recovery_status` on the official SDK (no new dependency), wrapping the same internal operations as the headless protocol. See `docs/proposals/mcp-server-mode.md`.
- Comparative study and honest parity accounting in `docs/reports/openharness-vs-hermes-agent.md` and `docs/reports/learning-search-resilience-parity.md` (one documented capability gap: multi-account OAuth credential pools).

## [0.1.14] - 2026-06-10

### Added

- Anthropic prompt-caching breakpoints: `cache_control` on the stable system-prompt prefix, the tool array, and the previous turn's last block, so providers cache everything that does not change between requests. Per-line relevant-memories content stays outside the cached prefix via a stable-prefix boundary threaded through the engine. Gated by the new `prompt_caching_enabled` setting (default on); OpenAI-format and Codex clients unchanged.
- `UsageSnapshot` (and therefore headless events, print-mode results, and state snapshots) carries `cache_creation_input_tokens` / `cache_read_input_tokens`.
- `scripts/measure_per_line.py` gates the per-line assembly budget (< 5 ms, timeit-style minimum) added to release checks.

### Changed (performance)

- Per-line runtime assembly dropped from ~45-60 ms to ~4 ms intrinsic: settings files, inline `--settings` sources, keybindings, plugins, skill registries, CLAUDE.md chains, git environment info, the base system prompt, and the skills section are all cached behind stat/identity fingerprints with hot-reload semantics preserved (plugin/skill walks revalidate at most once per second; plugin install/uninstall/reload invalidate explicitly).
- `current_settings()`/hook-registry rebuilds collapse to a stat via bundle-level identity caches; hooks always load with plugin hooks included (the plugin-less `HookReloader` path was removed).
- Command-context hook/plugin/MCP summaries are computed lazily — plain prompts never pay for them (gateway included).
- Auth status resolution moved off the line path behind a 30 s TTL cache, eliminating per-line keyring roundtrips and synchronous OAuth refreshes; provider/profile commands refresh immediately.
- Memory relevance parses `usage_index.json` once per directory and records recall usage on the executor instead of blocking the prompt build.
- Per-line state writes (session snapshot, session index, session-memory checkpoint) keep rename atomicity but no longer fsync; durable-write policy moves to the persistence workstream.

## [0.1.13] - 2026-06-10

### Added

- Background task workers (`--task-worker`) are now persistent: one process serves all coordinator follow-ups until EOF, a terminating command, or the new `task_worker_idle_timeout_s` setting (default 600 s) elapses. The task manager injects a stable per-task session id (`OPENHARNESS_TASK_SESSION_ID`), and workers save/restore their conversation under it — crash or idle restarts resume with full context instead of an empty conversation.
- New `memory.extract_model` setting routes the optional durable-memory extraction pass to a cheaper model than the session model.

### Changed (performance)

- Removed a fixed ~50 ms per-turn latency floor: the compaction progress loop now races the worker task instead of polling on a timeout.
- Tool API schemas are cached on the registry and regenerate only when a tool is registered, instead of running pydantic schema generation for every tool on every model turn.
- The per-turn autocompact token estimate is incremental (only newly appended messages are counted) instead of re-scanning and re-stringifying the entire history each turn.
- Durable memory extraction runs as a background task (one in flight, skipped when the conversation has not grown) instead of delaying every turn's completion by a full model call; session-memory checkpoint writes moved off the event loop.
- `CodexApiClient` reuses one HTTP connection pool and one decoded-JWT header set across turns instead of a TLS handshake and JWT decode per request, and closes the pool on shutdown.
- The OpenAI-compatible client accumulates streamed text/reasoning/tool-call arguments in lists joined once, removing quadratic copying on large streamed arguments.
- The Anthropic client closes the replaced connection pool on auth refresh instead of leaking it across token rotations.
- Oversized tool-output artifact writes moved off the event loop so concurrent sibling tools and streaming are not stalled.
- `compact_checkpoints` is capped at 10 entries and excluded from hook payloads, bounding hook subprocess environment size over long sessions.
- Swarm mailbox `mark_read` targets messages by their filename-embedded id instead of parsing the whole archive under the write lock; auto-dream periodic scans read session ids from filenames instead of parsing every snapshot.
- Coordinator drain wakes on task-manager completion listeners instead of 100 ms polling; swarm permission polling backs off 0.2 s → 2 s; the React backend host emits tasks/status snapshot frames only when their content changed.

## [0.1.10] - 2026-06-10

### Added

- `oh --headless` runs a local JSONL control protocol over stdin/stdout with `submit`, `resume`, `continue`, `list_sessions`, `status`, `interrupt`, and `shutdown` requests. Requests are processed FIFO; `status`/`list_sessions`/`interrupt` are answered immediately even while a turn is active.
- Headless `shutdown` is graceful by default (the active turn and requests queued ahead of it finish first); requests queued behind any shutdown are rejected with explicit `error` events. `{"type":"shutdown","force":true}` additionally cancels the active turn (including a follow-up turn of an in-flight `resume`/`continue`). Closing stdin is equivalent to a graceful shutdown.
- Headless events now carry token usage: `assistant_complete.usage` (per-turn) plus `line_complete.usage` and `state_snapshot.usage` (cumulative).
- A headless `submit` carrying a `session_id` is validated against the active session and rejected with an `error` event on mismatch.
- `oh -p --output-format json` results now include `is_error`, `errors`, `permission_denials`, `system_messages` (e.g. the max-turns truncation notice), and `usage`; `oh -p` exits non-zero when an engine error occurred (all output formats), and `stream-json`'s `line_complete` includes `usage`.
- An interrupted headless turn is persisted to the session snapshot before `interrupted` is emitted, so `resume` keeps the interrupted exchange.
- Hooks now support a `priority` field (default `0`). Within an event, hooks run highest-priority first, and hooks sharing a priority keep their registration order. This lets users order, for example, a security-check hook ahead of a logging hook regardless of where each is declared in settings or contributed by plugins.
- `edit_file` and `write_file` in the React TUI now preview a unified diff before applying file changes, let users approve once or for the rest of the session, and skip the extra prompt automatically in `full_auto` mode.

### Fixed

- `oh --headless` no longer cancels an in-flight turn when a `shutdown` request arrives on stdin, so piping a `submit` + `shutdown` batch returns the full response.
- Headless `resume`/`continue` failures (missing or corrupt snapshot, runtime build errors) now emit a recoverable `error` event instead of crashing the control process, and the stdin reader survives request-handling exceptions.
- Explicit CLI `--model` now wins over the model stored in a session snapshot for `-p --resume/--continue`, headless `resume`/`continue`, and interactive resume.
- Conflicting mode flags now error instead of resolving silently: `--headless` with `--task-worker`, `--backend-only`, or `--output-format`, and `--dry-run` with `--headless`.
- `--bare` combined with `--mcp-config` now prints a warning that MCP stays disabled, and the `--bare` help text lists everything it disables.
- `--max-turns` is now honored when resuming an interactive session with `--continue`/`--resume`.
- Inline/file `--settings` sources no longer overwrite a user-supplied `profiles` entry or explicit `active_profile` when synthesizing a profile from flat fields.
- Codex subscription requests now pass reasoning effort separately, enabling `gpt-5.5` with `xhigh` effort instead of treating `gpt-5.5 xhigh` as an unsupported model name.
- Telegram channel now delivers replies again under `ohmo init --no-interactive` and other configs that do not write a `reply_to_message` field. `TelegramConfig` declares `reply_to_message: bool = True` so the attribute access in `TelegramChannel.send` no longer raises `AttributeError` and outbound progress/tool-hint/final messages are sent as expected. See issue #243.

## [0.1.9] - 2026-05-07

### Added

- Added a bundled `skill-creator` skill for creating, improving, and verifying OpenHarness/ohmo skills.
- User-invocable skills can now be triggered directly as slash commands, with support for skill-specific arguments and model override metadata.

### Fixed

- `oh setup` can now update the API key for an already-configured API-key provider profile instead of only changing the model.
- `oh provider edit <profile> --api-key <key>` can now replace a saved profile API key, and `oh provider add ... --api-key <key>` can store one during profile creation.

## [0.1.8] - 2026-05-06

### Added

- Built-in `nvidia` provider profile so `oh setup` offers NVIDIA NIM as a first-class OpenAI-compatible provider choice, with `NVIDIA_API_KEY` auth source, `openai/gpt-oss-120b` as the default model, and the NVIDIA NIM endpoint.
- Built-in `qwen` provider profile so `oh setup` offers Qwen (DashScope) as a first-class provider choice, with `dashscope_api_key` auth source, `qwen-plus` as the default model, and the DashScope OpenAI-compatible endpoint.
- Plugin tool discovery: plugins can now provide `BaseTool` subclasses in a `<plugin>/tools/` directory and they are auto-discovered, instantiated, and registered in the tool registry at runtime. Add `tools_dir` to `plugin.json` (defaults to `"tools"`).
- `oh --dry-run` safe preview mode for inspecting resolved runtime settings, auth state, prompt assembly, commands, skills, tools, and configured MCP servers without executing the model or tools.
- Built-in `minimax` provider profile so `oh setup` offers MiniMax as a first-class provider choice, with `MINIMAX_API_KEY` auth source, `MiniMax-M2.7` as the default model, and `MiniMax-M2.7-highspeed` in the model picker.
- Docker as an alternative sandbox backend (`sandbox.backend = "docker"`) for stronger execution isolation with configurable resource limits, network isolation, and automatic image management.
- Built-in `gemini` provider profile so `oh setup` offers Google Gemini as a first-class provider choice, with `gemini_api_key` auth source and `gemini-2.5-flash` as the default model.
- `diagnose` skill: trace agent run failures and regressions using structured evidence from run artifacts.
- OpenAI-compatible API client (`--api-format openai`) supporting any provider that implements the OpenAI `/v1/chat/completions` format, including Alibaba DashScope, DeepSeek, GitHub Models, Groq, Together AI, Ollama, and more.
- `OPENHARNESS_API_FORMAT` environment variable for selecting the API format.
- `OPENAI_API_KEY` fallback when using OpenAI-format providers.
- GitHub Actions CI workflow for Python linting, tests, and frontend TypeScript checks.
- `CONTRIBUTING.md` with local setup, validation commands, and PR expectations.
- `docs/SHOWCASE.md` with concrete OpenHarness usage patterns and demo commands.
- GitHub issue templates and a pull request template.
- React TUI assistant messages now render structured Markdown blocks, including headings, lists, code fences, blockquotes, links, and tables.
- Built-in `codex` output style for compact, low-noise transcript rendering in React TUI.

### Fixed

- Subprocess teammate spawn (`agent` tool, `task_create`) now works on Windows under Git Bash. `subprocess_backend.spawn` builds a direct-exec `argv` list and passes it through new `argv=` and `env=` kwargs on `BackgroundTaskManager.create_agent_task` / `create_shell_task`; `_start_process` then runs the executable via `asyncio.create_subprocess_exec(*argv)` with no shell in between. Previously the spawn command was a single string interpreted by `bash -lc`, which on Windows could not reliably exec a Windows-pathed Python interpreter (e.g. `C:\Users\...\python.exe`) — Git Bash's escape parser consumed the backslashes from the embedded env-prefix and, even with proper quoting, bash launched via `asyncio.create_subprocess_exec` returned `command not found` for Windows-pathed binaries that worked perfectly when invoked interactively. Bypassing the shell sidesteps the entire class of cross-platform quoting and path-translation hazard. The legacy shell-evaluated `command=` path is preserved for callers (e.g. `BashTool`) that legitimately want shell semantics. See issue #230.
- Bundled skill loader now uses `yaml.safe_load` for SKILL.md frontmatter, matching the user-skill loader. The shared parser is extracted to `openharness.skills._frontmatter` so bundled and user skills handle YAML block scalars (`>`, `|`), quoted values, and other standard YAML constructs the same way.
- Compaction now detects llama.cpp/OpenAI-compatible context overflow errors, accounts for image blocks in auto-compact token estimates, and strips image payloads from summarizer-only compaction requests.
- Large tool results are now bounded in conversation history: oversized outputs are saved under `tool_artifacts`, old MCP results become microcompactable, and context collapse trims stale tool-result payloads.
- ohmo now keeps personal memory isolated from OpenHarness project memory: `/memory` in ohmo sessions targets the ohmo workspace memory store, and ohmo runtime prompt refreshes no longer inject project memory unless explicitly requested.
- Fixed `glob` and `grep` tools hanging indefinitely when the `rg` subprocess produced enough stderr output to fill the OS pipe buffer. `stderr` is now redirected to `DEVNULL` so it is discarded rather than blocking the child process.
- Fixed `bash_tool` hanging after a timed-out command when the subprocess stdout stream stayed open. `_read_remaining_output` now applies a 2-second `asyncio.wait_for` timeout so the tool always returns promptly.
- Fixed `session_runner` background task deadlock caused by an unread `stderr=PIPE` stream. The subprocess now uses `stderr=STDOUT` so all output merges into the single readable stdout pipe.
- React TUI prompt input now treats the raw DEL byte (`0x7f`) as backward delete while preserving true forward-delete escape sequences, fixing backspace failures seen in some macOS terminal environments.
- `todo_write` tool now updates an existing unchecked item in-place when `checked=True` instead of appending a duplicate `[x]` line.

- Built-in `Explore` and `claude-code-guide` agents no longer hard-code `model="haiku"`, which caused them to fail for users on non-Anthropic providers (OpenAI, Bedrock, custom base URLs, etc.). Both agents now use `model="inherit"` so they run with whatever model the parent session is using. `build_inherited_cli_flags` is also fixed to skip the `--model` flag entirely when the value is `"inherit"`, letting the subprocess correctly inherit the parent model via the `OPENHARNESS_MODEL` environment variable instead of receiving the literal string `"inherit"` as a model name.

- React TUI spinner now stays visible throughout the entire agent turn: `assistant_complete` no longer resets `busy` state prematurely, and `tool_started` explicitly sets `busy=true` so the status bar remains active even when tool calls follow an assistant message. `line_complete` is the sole signal that ends the turn and clears the spinner.
- Skill loader now uses `yaml.safe_load` to parse SKILL.md frontmatter, correctly handling YAML block scalars (`>`, `|`), quoted values, and other standard YAML constructs instead of naive line-by-line splitting.
- `BackendHostConfig` was missing the `cwd` field, causing `AttributeError: 'BackendHostConfig' object has no attribute 'cwd'` on startup when `oh` was run after the runtime refactor that added `cwd` support to `build_runtime`.
- Shell-escape `$ARGUMENTS` substitution in command hooks to prevent shell injection from payload values containing metacharacters like `$(...)` or backticks.
- Swarm `_READ_ONLY_TOOLS` now uses actual registered tool names (snake_case) instead of PascalCase, fixing read-only auto-approval in `handle_permission_request`.
- Memory scanner now parses YAML frontmatter (`name`, `description`, `type`) instead of returning raw `---` as description.
- Memory search matches against body content in addition to metadata, with metadata weighted higher for relevance.
- Memory search tokenizer handles Han characters for multilingual queries.
- Fixed duplicate response in React TUI caused by double Enter key submission in the input handler.
- Fixed concurrent permission modals overwriting each other in TUI default mode when the LLM returns multiple tool calls in one response; `_ask_permission` now serialises callers via an `asyncio.Lock` so each modal is shown and resolved before the next one is emitted.
- Fixed React TUI Markdown tables to size columns from rendered cell text so inline formatting like code spans and bold text no longer breaks alignment.
- Fixed grep tool crashing with `ValueError` / `LimitOverrunError` when ripgrep outputs a line longer than 64 KB (e.g. minified assets or lock files). The asyncio subprocess stream limit is now 8 MB and oversized lines are skipped rather than terminating the session.
- Fixed React TUI exit leaving the shell prompt concatenated with the last TUI line. The terminal cleanup handler now writes a trailing newline (`\n`) alongside the cursor-show escape sequence so the shell prompt always starts on a fresh line.
- Reduced React TUI redraw pressure when `output_style=codex` by avoiding token-level assistant buffer flushes during streaming.

### Changed

- ohmo Feishu group routing now supports managed group creation, gateway-scoped provider/model commands, and stricter group mention handling so group conversations only wake ohmo when explicitly addressed.
- Dry-run output now reports a `ready` / `warning` / `blocked` readiness verdict, concrete `next_actions`, likely matching skills/tools for normal prompts, and richer slash-command previews for read-only vs stateful command paths.
- React TUI now groups consecutive `tool` + `tool_result` transcript rows into a single compound row: success shows the result line count inline (e.g. `→ 24L`), errors show a red icon and up to 5 lines of error detail beneath the tool row. Standalone successful tool results are suppressed to reduce transcript noise; standalone errors are still surfaced.
- README now links to contribution docs, changelog, showcase material, and provider compatibility guidance.
- README quick start now includes a one-command demo and clearer provider compatibility notes.
- README provider compatibility section updated to include OpenAI-format providers.

## [0.1.7] - 2026-04-18

### Fixed

- Install script now links `oh`, `ohmo`, and `openharness` into `~/.local/bin` instead of prepending the virtualenv `bin` directory to `PATH`, which avoids overriding Conda-managed shells while preserving global command discovery.
- React TUI prompt now supports `Shift+Enter` for inserting a newline without submitting the current prompt.
- React TUI busy-state animation is less error-prone on Windows terminals: the extra pseudo-animation line was removed, Windows now uses conservative ASCII spinner frames, and the spinner interval was slightly slowed to reduce flashing.

## [0.1.0] - 2026-04-01

### Added

- Initial public release of OpenHarness.
- Core agent loop, tool registry, permission system, hooks, skills, plugins, MCP support, and terminal UI.
