# OpenHarness — Comprehensive API Coverage Audit

**Date:** 2026-06-09
**Scope:** Full codebase — CLI, tools, channels, config, plugins, hooks, permissions, internal modules, tests, documentation

---

## Executive Summary

| Category | Coverage | Grade |
|----------|----------|-------|
| CLI Commands | 95% implemented, 25% documented | C+ |
| Slash Commands | 100% implemented, 32% documented | B- |
| Tools (AI-facing) | 42 built-in, 85% file I/O, 0% MCP server | B |
| Channel Integrations | 10 channels, 60-90% feature coverage per channel | B |
| API Clients | 4 providers, 22 provider profiles, streaming only | B- |
| Configuration | 60+ options, 36% documented | C |
| Plugin API | 6 artifact types, no lifecycle hooks | B- |
| Hook API | 10 events, 4 hook types, limited events | C+ |
| Permission API | 3 modes, path rules, limited flexibility | C |
| Internal Module API | 72% docstring coverage, incomplete `__all__` | B- |
| Test Coverage | 1,129 test functions, 6 modules untested | B |
| Documentation | README + CHANGELOG, no API reference | C |

**Overall API Coverage: ~65%** — Functional but with significant documentation gaps, missing features, and inconsistencies.

---

## 1. CLI Command API

### 1.1 Main CLI Flags (28 total)

| Status | Count | Percentage |
|--------|-------|------------|
| Implemented | 28 | 100% |
| Documented in README | 10 | 36% |
| Tested | ~8 | 29% |

**Undocumented flags:** `--name`, `--effort`, `--verbose`, `--dangerously-skip-permissions`, `--allowed-tools`, `--disallowed-tools`, `--append-system-prompt`, `--settings`, `--base-url`, `--api-key`, `--bare`, `--api-format`, `--theme`, `--debug`, `--mcp-config`

**Hidden flags:** `--cwd`, `--backend-only`, `--task-worker`

### 1.2 CLI Subcommands (42 total across 7 groups)

| Group | Subcommands | Documented | Tested |
|-------|-------------|------------|--------|
| `oh` (root) | `setup` | Yes | Yes |
| `oh mcp` | `list`, `add`, `remove` | Partial | No |
| `oh plugin` | `list`, `install`, `uninstall` | Partial | No |
| `oh cron` | `start`, `stop`, `status`, `list`, `toggle`, `history`, `logs` | **None** | No |
| `oh autopilot` | `status`, `list`, `add`, `context`, `journal`, `scan`, `run-next`, `tick`, `install-cron`, `export-dashboard` | Partial | 4/10 |
| `oh auth` | `login`, `status`, `logout`, `switch`, `copilot-login`, `codex-login`, `claude-login`, `copilot-logout` | Partial | No |
| `oh config` | `show`, `set` | No | No |
| `oh provider` | `list`, `use`, `add`, `edit`, `remove` | Partial | 2/5 |

### 1.3 Slash Commands (63 built-in + dynamic)

| Status | Count | Percentage |
|--------|-------|------------|
| Implemented | 63 | 100% |
| Documented in `/help` | ~20 | 32% |
| Tested | ~35 | 56% |

**Notable undocumented commands:** `/hooks`, `/onboarding`, `/plan`, `/fast`, `/effort`, `/passes`, `/continue`, `/theme`, `/output-style`, `/keybindings`, `/vim`, `/pr_comments`, `/privacy-settings`, `/rate-limit-options`, `/release-notes`, `/upgrade`

**Commands with hidden sub-actions:**
- `/memory` — 10+ sub-actions (`add`, `remove`, `list`, `edit`, `validate`, `extract`, `session`, `team`, `agent`, `migrate`) but help shows only 5
- `/autopilot` — 15+ sub-actions barely hinted at
- `/bridge` — 7 sub-commands (`encode`, `decode`, `sdk`, `spawn`, `list`, `output`, `stop`)
- `/session` — 4 sub-commands (`tag`, `ls`, `path`, `clear`)
- `/tasks` — 4 sub-commands (`run`, `stop`, `update`, `output`)
- `/model` — 4 sub-commands (`add`, `remove`, `clear`, `list`)

### 1.4 CLI Gaps & Issues

| Issue | Severity |
|-------|----------|
| README claims "54 Commands" but 63+ are registered | Medium |
| No comprehensive slash command reference anywhere | High |
| `oh cron` (7 subcommands) completely undocumented | Medium |
| `/stop` is non-functional — always returns static message | Medium |
| `/cost` only supports 3 Claude model families | Low |
| `/pr_comments` uses underscore; all others use hyphens | Low |
| Version string inconsistency: `cli.py` says `0.1.9`, `/version` falls back to `0.1.7` | Low |

---

## 2. Tool & MCP API

### 2.1 Built-in Tools (42 total)

| Category | Tools | Coverage |
|----------|-------|----------|
| **File I/O** | `read_file`, `write_file`, `edit_file`, `multi_edit`, `insert_content` | 85% — missing delete/move/copy |
| **Search** | `glob`, `grep` (with ripgrep) | 90% — no semantic search |
| **Shell** | `bash` (with sandbox, timeout, interactive detection) | 95% — best-in-class |
| **Web** | `web_search`, `web_fetch` | 70% — GET only, no POST/auth |
| **Code Intelligence** | `lsp` (Python only) | 60% — no TS/JS/Go/Rust |
| **Image** | `image_generation`, `image_to_text` | 90% — multi-provider |
| **Agent Orchestration** | `agent`, `task_create`, `task_update`, `task_list`, `task_output`, `task_stop`, `task_wait` | 95% |
| **Scheduling** | `cron_create`, `cron_list`, `cron_delete`, `cron_toggle` | 95% |
| **Notebook** | `notebook_edit`, `notebook_read` | 80% |
| **Planning** | `enter_plan_mode`, `exit_plan_mode` | 90% |
| **Worktree** | `enter_worktree`, `exit_worktree` | 80% |
| **Misc** | `config`, `todo_read`, `todo_write`, `remote_trigger`, `skill`, `mcp_auth` | 80% |

### 2.2 Missing Tool Categories

| Gap | Common in Other AI Assistants | Workaround |
|-----|-------------------------------|------------|
| File delete/move/copy | Claude Code, Cursor | `bash` with `rm`/`mv`/`cp` |
| Directory create | Claude Code, Cursor | `bash mkdir` or `write_file` auto-creates |
| Git operations (dedicated) | Claude Code | `bash` with git commands |
| Memory/context management | Claude Code `/memory` | Only slash commands, no tool |
| Semantic/embedding search | Cursor | `grep` for text only |
| Database query | Some assistants | `bash` with CLI tools |
| HTTP request (generic) | Postman MCP | `bash` with `curl` |
| Process management | Some assistants | `bash` only |
| Environment variables | Some assistants | `bash` with `env` |

### 2.3 MCP API Coverage

| Capability | Client | Server |
|------------|--------|--------|
| Tool calling | Yes | **No (0%)** |
| Resource reading | Yes | **No** |
| Prompt listing | **No** | **No** |
| Sampling | **No** | **No** |
| Stdio transport | Yes | Yes |
| HTTP/SSE transport | Yes | **No** |
| WebSocket transport | Defined but **not implemented** | **No** |

**Critical gap:** OpenHarness cannot expose its tools to other MCP clients. No MCP server mode exists.

### 2.4 Tool Issues

| Issue | Severity |
|-------|----------|
| No MCP server mode — can't expose tools to other clients | High |
| No dedicated file management tools (delete/move/copy) | Medium |
| No memory tool — rich backend exists but inaccessible to AI | Medium |
| Python-only LSP — no TypeScript/JS/Go/Rust | Medium |
| MCP-adapted tools always `is_read_only=False` — unnecessary confirmations | Low |
| Code duplication: `_resolve_path()` and `_compute_diff()` copy-pasted across 7+ files | Low |
| `agent` vs `task_create` overlap — unclear distinction | Low |

---

## 3. Channel & Integration API

### 3.1 Channel Feature Matrix

| Feature | Telegram | Discord | Slack | Email | DingTalk | Feishu | Matrix | Mochat | QQ | WhatsApp |
|---------|----------|---------|-------|-------|----------|--------|--------|--------|-----|----------|
| Send text | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Receive text | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Send media | Yes | **No** | Yes | **No** | Yes | Yes | Yes | **No** | **No** | **No** |
| Receive media | Yes | Yes | **No** | **No** | **No** | Yes | Yes | **No** | **No** | **No** |
| Reactions | **No** | **No** | Add only | **No** | **No** | Yes | **No** | **No** | **No** | **No** |
| Threads | **No** | **No** | Yes | **No** | **No** | Yes | Yes | **No** | **No** | **No** |
| Typing indicator | Yes | Yes | **No** | **No** | **No** | **No** | Yes | **No** | **No** | **No** |
| Voice/audio | Yes (full) | **No** | **No** | **No** | **No** | **No** | **No** | **No** | **No** | Partial |
| Markdown | HTML | **No** | mrkdwn | **No** | Yes | Card/Post | HTML | **No** | **No** | **No** |
| Auto-reconnect | Via lib | Yes | **No** | Polling | Yes | Yes | Yes | Yes | Yes | Yes |

### 3.2 Channel Implementation Quality

| Channel | Lines | Assessment | Key Gaps |
|---------|-------|------------|----------|
| **Feishu** | 1,342 | Most feature-rich | No message editing, no read receipts |
| **Telegram** | 525 | Most mature consumer | No reactions, no inline keyboards |
| **Matrix** | 700 | Strong privacy (E2EE) | No reactions, no message editing/redaction |
| **Mochat** | 897 | Complex session management | Text-only, no media |
| **DingTalk** | 445 | Robust media pipeline | Private chat only, no group replies |
| **Email** | 410 | Async-first design | No attachments, no HTML outbound |
| **Discord** | 311 | Solid basics | No outbound files, no reactions, no threads |
| **Slack** | 284 | Good enterprise features | No inbound file download, no Block Kit |
| **WhatsApp** | 159 | Bridge-dependent minimal | Text-only, extremely minimal |
| **QQ** | 141 | Minimal | No group messages, no media, very bare-bones |

### 3.3 Cross-Channel Inconsistencies

| Issue | Severity |
|-------|----------|
| Each channel uses different outbound formatting — no unified formatting layer | Medium |
| Each channel stores different metadata keys — no standardized metadata contract | Medium |
| Session key derivation used inconsistently across channels | Medium |
| Error reporting differs — some raise, some swallow, some send fallback messages | Medium |
| No circuit breaker pattern — failing channels retry indefinitely | Medium |

### 3.4 API Client Coverage

| Capability | Anthropic | OpenAI | Copilot | Codex |
|------------|-----------|--------|---------|-------|
| Streaming | Yes | Yes | Yes | Yes (SSE) |
| Non-streaming | **No** | **No** | **No** | **No** |
| Tool/function calling | Yes | Yes | Yes | Yes |
| Image/vision input | **No** | Yes | Yes | Yes |
| Retry logic | Yes (3x, +jitter) | Yes (3x) | Inherited | Yes (3x) |
| Retry-After header | Yes | **No** | **No** | **No** |
| OAuth support | Yes | **No** | Yes | Yes |
| Reasoning/thinking | **No** | Yes | **No** | Yes |
| Close/cleanup | Yes | Yes | Yes | **No** |

**Provider Registry:** 22 providers configured (GitHub Copilot, OpenRouter, Anthropic, OpenAI, DeepSeek, Gemini, DashScope, Moonshot, MiniMax, Zhipu AI, Groq, Mistral, StepFun, Baidu, AWS Bedrock, Vertex AI, Ollama, vLLM, AiHubMix, SiliconFlow, VolcEngine, ModelScope)

### 3.5 API Client Gaps

| Gap | Impact |
|-----|--------|
| No non-streaming API — all clients streaming-only | Can't do single-shot completions |
| No prompt caching | Missing Anthropic/OpenAI caching features |
| No extended thinking for Anthropic | Claude's extended thinking not exposed |
| No batch API | Can't use Anthropic/OpenAI batch endpoints |
| No response format control | No JSON mode, no structured output |
| No web search/file search tools | Can't use built-in tool types |
| No token counting | Can't enumerate tokens before sending |
| No model listing | Can't enumerate available models |
| Inconsistent retry behavior | Only Anthropic respects Retry-After |
| CodexApiClient has no `close()` | Resource leak |

### 3.6 Bridge API Coverage

| Capability | Status |
|------------|--------|
| Work secret encode/decode | Yes |
| SDK URL building | Yes |
| Subprocess session spawning | Yes |
| Session lifecycle (spawn/list/stop) | Yes |
| Output capture to log files | Yes |
| Process termination | Yes |
| **Stdin forwarding** | **No** |
| **Stderr separation** | **No** |
| **Environment variable passing** | **No** |
| **Resource limits** | **No** |
| **Health check mechanism** | **No** |
| **WebSocket ingress** | **No** |
| **Session pause/resume** | **No** |
| **Output streaming API** | **No** |
| **Input injection after spawn** | **No** |

---

## 4. Configuration & Plugin API

### 4.1 Configuration Options

| Category | Options | Documented | Validated |
|----------|---------|------------|-----------|
| API/Model | 12 | 4 (33%) | 8 (67%) |
| Behavior | 10 | 3 (30%) | 10 (100%) |
| UI | 8 | 0 (0%) | 7 (88%) |
| Memory | 12 | 0 (0%) | 12 (100%) |
| Sandbox | 9+ | 0 (0%) | 7 (78%) |
| Web | 3 | 1 (33%) | 1 (33%) |
| Vision | 3 | 0 (0%) | 3 (100%) |
| Image Gen | 6 | 0 (0%) | 6 (100%) |
| **Total** | **63+** | **~8 (13%)** | **54 (86%)** |

### 4.2 Environment Variables

| Status | Count |
|--------|-------|
| Total implemented | 35+ |
| Documented in README | 4 (11%) |
| Undocumented | 31+ (89%) |

**Key undocumented env vars:** `OPENHARNESS_CONFIG_DIR`, `OPENHARNESS_DATA_DIR`, `OPENHARNESS_MODEL`, `OPENHARNESS_MAX_TOKENS`, `OPENHARNESS_TIMEOUT`, `OPENHARNESS_MAX_TURNS`, `OPENHARNESS_PROVIDER`, `OPENHARNESS_SANDBOX_*`, `OPENHARNESS_VISION_*`, `OPENHARNESS_IMAGE_GENERATION_*`, all provider API keys

### 4.3 Plugin API

**Plugin artifacts supported:**

| Artifact | Loading | Configuration | Testing |
|----------|---------|---------------|---------|
| Skills (SKILL.md) | Yes | No | No |
| Commands (.md with frontmatter) | Yes | No | No |
| Agents (.md with frontmatter) | Yes | No | No |
| Tools (.py with BaseTool) | Yes | No | No |
| Hooks (hooks.json) | Yes | No | No |
| MCP Servers (mcp.json) | Yes | No | No |

**Missing plugin capabilities:**
- No lifecycle hooks (`on_load`, `on_unload`, `on_enable`, `on_disable`)
- No plugin configuration schema
- No plugin dependencies
- No plugin version constraints
- No plugin update mechanism
- No plugin marketplace
- No plugin sandboxing
- No plugin resource limits
- No plugin telemetry
- No plugin SDK or testing framework
- No plugin authoring guide
- No example plugins

### 4.4 Hook API

**Hook events (10 total):**

| Event | Trigger | Payload |
|-------|---------|---------|
| `SESSION_START` | Session begin | `{cwd, event}` |
| `SESSION_END` | Session end | `{cwd, event}` |
| `PRE_COMPACT` | Before compaction | `{event, ...}` |
| `POST_COMPACT` | After compaction | `{event, ...}` |
| `PRE_TOOL_USE` | Before tool execution | `{tool_name, tool_input, event}` |
| `POST_TOOL_USE` | After tool execution | `{tool_name, tool_input, tool_output, event}` |
| `USER_PROMPT_SUBMIT` | User submits prompt | `{prompt, event}` |
| `NOTIFICATION` | Notification event | `{message, event}` |
| `STOP` | Stop event | `{event, ...}` |
| `SUBAGENT_STOP` | Subagent stops | `{event, ...}` |

**Hook types (4):** Command (shell), HTTP (webhook), Prompt (LLM validation), Agent (LLM thorough)

**Missing hook events:**
- `PRE_RESPONSE` / `POST_RESPONSE`
- `PRE_SKILL_LOAD` / `POST_SKILL_LOAD`
- `PRE_COMMAND` / `POST_COMMAND`
- `PRE_MCP_CALL` / `POST_MCP_CALL`
- `ERROR`
- `PRE_AUTH` / `POST_AUTH`
- `PRE_SAVE` / `POST_SAVE`

**Missing hook capabilities:**
- No hook chaining (can't pass results between hooks)
- No hook conditions (complex conditions)
- No hook transformation (modify payload)
- No hook retry
- No hook caching
- No hook filtering by tool type (only name pattern)
- No hook error recovery
- No hook metrics

### 4.5 Permission API

**Permission modes (3):**

| Mode | Behavior |
|------|----------|
| `DEFAULT` | Ask before write/execute |
| `PLAN` | Block all writes |
| `FULL_AUTO` | Allow everything |

**Permission rules:**
- `allowed_tools` — always allow list
- `denied_tools` — always deny list
- `path_rules` — glob-based path rules
- `denied_commands` — command pattern deny list
- Sensitive path protection (`.ssh`, `.aws`, `.gnupg`, etc.)

**Missing permission features:**
- No time-based permissions
- No rate-based permissions
- No cost-based permissions (budget limits)
- No network permissions
- No resource permissions (CPU/memory)
- No temporary permissions
- No conditional permissions
- No permission inheritance
- No permission delegation
- No permission audit/tracking
- No permission templates
- No permission import/export
- No permission testing/debugging

---

## 5. Internal Module API

### 5.1 Module Size & Complexity

| Module | Files | Lines | Largest File |
|--------|-------|-------|--------------|
| `engine` | 6 | ~1,779 | `query.py` (1,057) |
| `coordinator` | 3 | ~1,507 | `agent_definitions.py` (975) |
| `services` | 17 | ~4,200+ | `compact/__init__.py` (1,725) |
| `state` | 3 | ~76 | `store.py` (40) |
| `memory` | 13 | ~1,534 | `schema.py` (443) |
| **Total** | **42** | **~9,096** | |

### 5.2 Public API Surface

| Module | `__all__` Defined | Exports | Complete? |
|--------|-------------------|---------|-----------|
| `engine` | Yes | 10 | No — missing `MaxTurnsExceeded`, `QueryContext`, `remember_user_goal` |
| `coordinator` | Yes | 5 | No — missing 10+ public functions |
| `services` | Yes | 8 | No — missing session, cron, compact exports |
| `state` | Yes | 2 | Minimal |
| `memory` | Yes | 11 | No — missing schema, usage, team, agent exports |

### 5.3 Docstring Coverage

| Module | Public Symbols | With Docstrings | Coverage |
|--------|---------------|-----------------|----------|
| `engine` | ~35 | ~28 | 80% |
| `coordinator` | ~20 | ~14 | 70% |
| `services` | ~80 | ~55 | 69% |
| `state` | ~8 | ~5 | 63% |
| `memory` | ~60 | ~45 | 75% |
| **Overall** | **~203** | **~147** | **72%** |

### 5.4 Internal API Issues

| Issue | Severity |
|-------|----------|
| `services/compact/__init__.py` is 1,725 lines — should be split into submodules | High |
| `estimate_message_tokens` name collision between `token_estimation.py` and `compact/__init__.py` (different signatures) | Medium |
| `engine/messages.py` should be extracted to top-level `types` package (soft cycle with services) | Medium |
| Many modules lack `__all__` (`session_memory`, `cron`, `cron_scheduler`, `tool_outputs`, `token_estimation`) | Medium |
| Untyped dicts used for session snapshots, cron jobs, usage records — should be dataclasses | Medium |
| Missing custom exception types (`ToolExecutionError`, `CompactionError`, `CronJobError`) | Medium |
| `tool_metadata` property returns mutable internal dict — callers can corrupt engine state | Medium |
| `_remember_*` functions (500+ lines) should be extracted from `query.py` | Low |
| System prompts embedded in code (520+ lines) should be externalized | Low |

---

## 6. Test & Documentation Coverage

### 6.1 Test Coverage by Module

| Module | Source Files | Test Functions | Assessment |
|--------|-------------|----------------|------------|
| `swarm` | 11 | 114 | Excellent |
| `services` | 17 | 89 | Excellent |
| `tools` | 44 | 86 | Good |
| `api` | 10 | 84 | Excellent |
| `commands` | 2 | 80 | Excellent |
| `config` | 4 | 76 | Excellent |
| `ohmo` | 11 | 96 | Good |
| `ui` | 11 | 58 | Good |
| `engine` | 6 | 35 | Good |
| `sandbox` | 6 | 35 | Good |
| `utils` | 6 | 37 | Good |
| `coordinator` | 3 | 41 | Excellent |
| `memory` | 13 | 25 | Decent |
| `prompts` | 5 | 24 | Good |
| `auth` | 5 | 22 | Good |
| `skills` | 6 | 22 | Good |
| `channels` | 18 | 20 | Decent |
| `permissions` | 3 | 17 | Good |
| `mcp` | 4 | 17 | Good |
| `autopilot` | 3 | 16 | Good |
| `tasks` | 6 | 13 | Decent |
| `personalization` | 4 | 13 | Decent |
| `hooks` | 7 | 11 | Decent |
| `plugins` | 6 | 9 | **Low** |
| `bridge` | 5 | 6 | **Low** |

**Modules with ZERO test coverage:**
- `keybindings` (5 files)
- `output_styles` (2 files)
- `state` (3 files)
- `themes` (4 files)
- `vim` (2 files)
- `voice` (4 files)

### 6.2 Documentation Coverage

| Document | Status |
|----------|--------|
| `README.md` (879 lines) | Comprehensive overview, install, quick start, provider table, tools table |
| `CONTRIBUTING.md` | Dev setup, local checks, PR expectations |
| `CHANGELOG.md` | Detailed v0.1.0 to v0.1.9 history |
| `docs/SHOWCASE.md` | 6 usage patterns |
| `docs/proposals/` | 3 design docs (all DRAFT) |
| `docs/reports/` | 4 provider performance audits |
| Release notes | v0.1.8, v0.1.9 |
| Chinese README | Yes |

**Missing documentation:**
- No API reference documentation (no `docs/api/`, no auto-generated docs)
- No usage guides or tutorials beyond README quick start
- No ohmo-specific documentation
- No configuration reference for `settings.json`
- No individual tool documentation beyond summary table
- No extension development guide beyond brief README examples
- No architecture decision records (ADRs)
- All proposal docs are DRAFT — none approved or implemented

### 6.3 Type Hint Coverage

| Metric | Value |
|--------|-------|
| Files with `from __future__ import annotations` | 188/229 (82%) |
| Methods with return type annotations | 378/395 (96%) |
| `mypy` config | `strict = true`, `python_version = "3.11"` |
| Pydantic models for tool inputs | All 42+ tools |
| Protocol classes | `SupportsStreamingMessages` for API clients |

**Assessment:** Type hint coverage is **high** (96% return types, 82% annotations import). However, `mypy` strict is configured but not enforced in CI.

---

## 7. Priority Recommendations

### Critical (Fix Immediately)

1. **Add MCP server mode** — OpenHarness should be able to expose its tools to other MCP clients
2. **Add timeouts to all tool execution** — Prevent indefinite hangs
3. **Fix sandbox path validation** — Enforce regardless of Docker backend
4. **Document all CLI flags and slash commands** — Create comprehensive reference
5. **Add error handling for corrupted config files** — Don't crash on bad JSON

### High Priority (Next Sprint)

6. **Add dedicated file management tools** — delete, move, copy with sandbox validation
7. **Add memory tool** — Expose rich memory backend to AI
8. **Expand LSP to TypeScript/JavaScript** — Most common web development languages
9. **Fix API client resource leaks** — Close HTTP clients properly
10. **Add hook events for responses and errors** — `PRE_RESPONSE`, `POST_RESPONSE`, `ERROR`

### Medium Priority (Backlog)

11. **Split `compact/__init__.py`** — 1,725 lines is unmaintainable
12. **Add plugin lifecycle hooks** — `on_load`, `on_unload`, etc.
13. **Add permission audit logging** — Track what was allowed/denied
14. **Document all environment variables** — 89% are undocumented
15. **Add tests for untested modules** — keybindings, output_styles, state, themes, vim, voice

### Low Priority (Nice to Have)

16. **Add semantic search** — Embedding-based memory search
17. **Add generic HTTP tool** — POST/PUT with headers and body
18. **Unify channel formatting layer** — Consistent markdown rendering
19. **Add API reference docs** — Auto-generated from docstrings
20. **Create plugin SDK and testing framework** — Help plugin authors

---

## 8. Coverage Scorecard Summary

| Domain | Score | Notes |
|--------|-------|-------|
| File I/O | 85% | Read/write/edit excellent. Missing delete/move/copy. |
| Search | 90% | Glob + grep with ripgrep is excellent. No semantic search. |
| Shell | 95% | Best-in-class. Interactive detection, sandboxing, timeout. |
| Web | 70% | Basic search + fetch. No POST/API calls, no auth, no JS. |
| Code Intelligence | 60% | Python-only LSP. No TS/JS/Go/Rust. No AST parsing. |
| Image | 90% | Generation + vision. Multi-provider. |
| Agent Orchestration | 95% | Comprehensive task/team/agent lifecycle. |
| Scheduling | 95% | Full cron CRUD + trigger. |
| MCP Client | 80% | Tools + resources. Missing WebSocket, prompts, sampling. |
| MCP Server | 0% | Not implemented at all. |
| Memory | 40% | Rich backend but no dedicated tool — only slash commands. |
| Configuration | 60% | Pydantic validation but poor documentation. |
| Plugin API | 70% | Multiple artifact types but no lifecycle or SDK. |
| Hook API | 65% | Multiple types but limited events and capabilities. |
| Permission API | 55% | Multiple modes but limited flexibility. |
| Channel Integrations | 75% | 10 channels, 60-90% feature coverage each. |
| API Clients | 70% | 4 providers but streaming-only, missing features. |
| Test Coverage | 80% | 1,129 tests but 6 modules untested. |
| Documentation | 50% | Good README but no API reference or guides. |
| **Overall** | **65%** | Functional but significant gaps remain. |
