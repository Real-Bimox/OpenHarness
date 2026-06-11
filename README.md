<h1 align="center">
  <img src="assets/logo.png" alt="OpenHarness" width="64" style="vertical-align: middle;">
  &nbsp;&nbsp;
  <img src="assets/ohmo.png" alt="ohmo" width="64" style="vertical-align: middle;">
  <br>
  <code>oh</code> — OpenHarness &amp; <code>ohmo</code>
</h1>

<p align="center">
  <a href="README.md"><strong>English</strong></a> ·
  <a href="README.zh-CN.md"><strong>简体中文</strong></a>
</p>

**OpenHarness** delivers core lightweight agent infrastructure: tool-use, skills, memory, and multi-agent coordination.

**ohmo** is a personal AI agent built on OpenHarness — not another chatbot, but an assistant that actually works for you over long sessions. Chat with ohmo in Feishu / Slack / Telegram / Discord, and it forks branches, writes code, runs tests, and opens PRs on its own using your configured OpenHarness runtime.

**Join the community**: contribute **Harness** for open agent development.

<p align="center">
  <a href="#-quick-start"><img src="https://img.shields.io/badge/Quick_Start-5_min-blue?style=for-the-badge" alt="Quick Start"></a>
  <a href="#-harness-architecture"><img src="https://img.shields.io/badge/Harness-Architecture-ff69b4?style=for-the-badge" alt="Architecture"></a>
  <a href="#-features"><img src="https://img.shields.io/badge/Tools-43+-green?style=for-the-badge" alt="Tools"></a>
  <a href="#-test-results"><img src="https://img.shields.io/badge/Tests-114_Passing-brightgreen?style=for-the-badge" alt="Tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge" alt="License"></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-≥3.10-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/React+Ink-TUI-61DAFB?logo=react&logoColor=white" alt="React">
  <img src="https://img.shields.io/badge/pytest-1260_pass-brightgreen" alt="Pytest">
  <img src="https://img.shields.io/badge/E2E-6_suites-orange" alt="E2E">
  <img src="https://img.shields.io/badge/output-text_|_json_|_stream--json-blueviolet" alt="Output">
  <a href="https://github.com/Real-Bimox/OpenHarness/actions/workflows/ci.yml"><img src="https://github.com/Real-Bimox/OpenHarness/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/Real-Bimox/OpenHarness"><img src="https://img.shields.io/badge/Project-OpenHarness-E9DBFC?style=flat" alt="OpenHarness"></a>
  <a href="https://github.com/Real-Bimox/OpenHarness"><img src="https://img.shields.io/badge/Fork-Real--Bimox-C5EAB4?style=flat" alt="Real-Bimox"></a>
</p>

One Command (**oh**) to Launch **OpenHarness** and Unlock All Agent Harnesses. 

Supports CLI agent integration including OpenClaw, nanobot, Cursor, and more.

<p align="center">
  <img src="assets/cli-typing.gif" alt="OpenHarness Terminal Demo" width="800">
</p>

---
## ✨ OpenHarness's Key Harness Features

<table align="center" width="100%">
<tr>
<td width="20%" align="center" style="vertical-align: top; padding: 15px;">

<h3>🔄 Agent Loop</h3>

<div align="center">
  <img src="https://img.shields.io/badge/Engine-06B6D4?style=for-the-badge&logo=lightning&logoColor=white" alt="Engine" />
</div>

<img src="assets/scene-agentloop.png" width="140">

<p align="center"><strong>• Streaming Tool-Call Cycle</strong></p>
<p align="center"><strong>• API Retry with Exponential Backoff</strong></p>
<p align="center"><strong>• Parallel Tool Execution</strong></p>
<p align="center"><strong>• Token Counting & Cost Tracking</strong></p>

</td>
<td width="20%" align="center" style="vertical-align: top; padding: 15px;">

<h3>🔧 Harness Toolkit</h3>

<div align="center">
  <img src="https://img.shields.io/badge/43+_Tools-10B981?style=for-the-badge&logo=toolbox&logoColor=white" alt="Toolkit" />
</div>

<img src="assets/scene-toolkit.png" width="140">

<p align="center"><strong>• 43 Tools (File, Shell, Search, Web, MCP)</strong></p>
<p align="center"><strong>• On-Demand Skill Loading (.md)</strong></p>
<p align="center"><strong>• Plugin Ecosystem (Skills + Hooks + Agents)</strong></p>
<p align="center"><strong>• Compatible with anthropics/skills & plugins</strong></p>

</td>
<td width="20%" align="center" style="vertical-align: top; padding: 15px;">

<h3>🧠 Context & Memory</h3>

<div align="center">
  <img src="https://img.shields.io/badge/Persistent-8B5CF6?style=for-the-badge&logo=brain&logoColor=white" alt="Context" />
</div>

<img src="assets/scene-context.png" width="140">

<p align="center"><strong>• CLAUDE.md Discovery & Injection</strong></p>
<p align="center"><strong>• Context Compression (Auto-Compact)</strong></p>
<p align="center"><strong>• MEMORY.md Persistent Memory</strong></p>
<p align="center"><strong>• Session Resume & History</strong></p>

</td>
<td width="20%" align="center" style="vertical-align: top; padding: 15px;">

<h3>🛡️ Governance</h3>

<div align="center">
  <img src="https://img.shields.io/badge/Permissions-F59E0B?style=for-the-badge&logo=shield&logoColor=white" alt="Governance" />
</div>

<img src="assets/scene-governance.png" width="140">

<p align="center"><strong>• Multi-Level Permission Modes</strong></p>
<p align="center"><strong>• Path-Level & Command Rules</strong></p>
<p align="center"><strong>• PreToolUse / PostToolUse Hooks</strong></p>
<p align="center"><strong>• Interactive Approval Dialogs</strong></p>

</td>
<td width="20%" align="center" style="vertical-align: top; padding: 15px;">

<h3>🤝 Swarm Coordination</h3>

<div align="center">
  <img src="https://img.shields.io/badge/Multi--Agent-EC4899?style=for-the-badge&logo=network&logoColor=white" alt="Swarm" />
</div>

<img src="assets/scene-swarm.png" width="140">

<p align="center"><strong>• Subagent Spawning & Delegation</strong></p>
<p align="center"><strong>• Team Registry & Task Management</strong></p>
<p align="center"><strong>• Background Task Lifecycle</strong></p>
<p align="center"><strong>• Multi-agent Team Integration (Roadmap)</strong></p>

</td>
</tr>
</table>

---

## 🤔 What is an Agent Harness?

An **Agent Harness** is the complete infrastructure that wraps around an LLM to make it a functional agent. The model provides intelligence; the harness provides **hands, eyes, memory, and safety boundaries**.

<p align="center">
  <img src="assets/harness-equation.png" alt="Harness = Tools + Knowledge + Observation + Action + Permissions" width="700">
</p>

OpenHarness is an open-source Python implementation designed for **researchers, builders, and the community**:

- **Understand** how production AI agents work under the hood
- **Experiment** with cutting-edge tools, skills, and agent coordination patterns
- **Extend** the harness with custom plugins, providers, and domain knowledge
- **Build** specialized agents on top of proven architecture

---

## 📰 What's New

- **2026-06-11** 🩺 **Unreleased** — Optional health/status HTTP server:
  - Adds an opt-in `openharness-ai[health-server]` extra with FastAPI + plain uvicorn for a local-only JSON health API. The base install remains unchanged.
  - `oh --health-server` serves `GET /health`, `GET /health/detailed`, `GET /api/status`, `GET /api/system/stats`, and `GET /v1/capabilities` on `127.0.0.1`.
  - Long-lived local modes can run it in the background with `oh --headless --health-server`, `oh --task-worker --health-server`, or `oh --mcp-serve --health-server`. Design: [docs/proposals/health-status-http-server.md](docs/proposals/health-status-http-server.md).
- **2026-06-11** 🔍 **v0.1.18** — Local-first observability & diagnostics:
  - Bounded, redacted, structured diagnostic events for every turn, model call, tool execution, permission decision, snapshot write, and index operation — correlated by one `run_id`, stored locally as daily JSONL (14-day retention, 25 MB/day cap), never containing prompts, outputs, or secrets. Adds < 0.5 ms per line (release-gated).
  - New `oh diagnostics status|tail|summary|export|purge` CLI group; headless `diagnostics` request + optional `correlation_id` on every request; MCP `diagnostics_status` tool; redacted support-bundle export with per-rule redaction report.
  - Hang watchdog for long-lived modes: `slow_operation` events per threshold, stack snapshots past hard thresholds, heartbeats, and a daemon-thread probe that detects broken thread handoff without ever touching the asyncio executor.
  - Fixes a v0.1.17 regression where the injected-stream headless stdin fallback could block the event loop. Design: [docs/proposals/observability-metrics.md](docs/proposals/observability-metrics.md).
- **2026-06-11** 🧵 **v0.1.17** — Executor-free search and embedded headless input:
  - Headless and agent session search now read the local conversation index directly instead of through the default asyncio thread executor, fixing the deterministic first-run search hang seen in executor-restricted environments.
  - Programmatic headless input streams now read directly instead of using the thread executor, so injected stream tests and embedded callers do not inherit executor startup failures.
  - Verified with focused headless search, `session_search` tool, MCP serve regressions, the exact two-search first-run subprocess repro, local headless smoke, compile checks, `ruff`, and `pip check`.
- **2026-06-11** 🛠️ **v0.1.16** — Post-release hardening for the v0.1.15 surfaces:
  - Conversation-search surfaces are bounded and fail closed instead of hanging: caller-facing index operations have a hard timeout and diagnostic stack capture on timeout.
  - `conversation_index_enabled=false` is honored consistently by CLI sessions, headless `search_sessions`, MCP session tools, and the in-agent tool.
  - `--mcp-serve` now rejects conflicting modes, provider fallback follows the typed error classifier as the sole policy authority, `ruff check .` is clean, and implemented proposal branches were archived per project policy. Details: [RELEASE_NOTES_v0.1.16.md](RELEASE_NOTES_v0.1.16.md).
- **2026-06-11** 🧠 **v0.1.15** — Conversation search, skill learning & provider resilience:
  - `session_search` searches your past conversations (SQLite FTS5, zero LLM cost) via tool, `oh sessions`, headless, and MCP — secrets redacted before indexing, index rebuildable from snapshots.
  - A skill learning loop: `skill_manage` write tool, post-turn self-review, usage lifecycle + weekly curator, optional staged approval — confined to user skills with write scanning on by default.
  - Typed error recovery with provider fallback chains (`oh fallback`) and API-key rotation, mid-turn, in one resilient wrapper client.
  - New **MCP server** (`oh --mcp-serve`) exposes these features to MCP hosts. Details: [RELEASE_NOTES_v0.1.15.md](RELEASE_NOTES_v0.1.15.md); honest parity report: [docs/reports/learning-search-resilience-parity.md](docs/reports/learning-search-resilience-parity.md).
- **2026-06-10** 🗜️ **v0.1.14** — Config caching & prompt-cache breakpoints:
  - Per-line runtime assembly drops from ~45–60 ms to ~4 ms: settings, plugins, skills, CLAUDE.md, git info, and prompt sections are cached behind stat/identity fingerprints with hot-reload preserved.
  - Anthropic prompt-caching breakpoints (system prefix / tools / history) cut input cost and TTFT on long sessions; `usage` payloads now report `cache_read_input_tokens`. Kill switch: `prompt_caching_enabled`.
  - Details: [RELEASE_NOTES_v0.1.14.md](RELEASE_NOTES_v0.1.14.md).
- **2026-06-10** ⚡ **v0.1.13** — Persistent task workers & per-turn performance:
  - Background agent workers are persistent: one process serves all coordinator follow-ups (idle timeout via `task_worker_idle_timeout_s`), and conversations save/restore under a stable per-task session id — restarts resume with full context instead of paying a multi-second rebuild with an empty conversation.
  - Per-turn overhead cuts across the engine: no more fixed 50 ms compaction poll, cached tool schemas, incremental token estimation, background memory extraction (`memory.extract_model` for a cheaper model), reused Codex connections, and linear streamed-argument accumulation.
  - Full details in [RELEASE_NOTES_v0.1.13.md](RELEASE_NOTES_v0.1.13.md); roadmap in [docs/proposals/performance-hardening-roadmap.md](docs/proposals/performance-hardening-roadmap.md).
- **2026-06-10** 📡 **v0.1.10** — Local headless control API:
  - `oh --headless` runs a local JSONL control protocol over stdin/stdout: `submit`, `resume`, `continue`, `list_sessions`, `status`, `interrupt`, and `shutdown` requests with structured events back, designed for local orchestrators that integrate OpenHarness without a TUI or network service.
  - `oh -p` now resumes sessions headlessly (`--resume <id>` / `--continue`), returns `session_id`, token `usage`, `errors`, and `permission_denials` in `--output-format json`, and exits non-zero on engine errors.
  - `shutdown` is graceful by default; `{"type":"shutdown","force":true}` cancels the active turn. Interrupted turns are persisted so resume keeps the exchange.
  - Full protocol reference: [docs/proposals/headless-local-control-api.md](docs/proposals/headless-local-control-api.md).
- **v0.1.10** 🔍 **Dry-run safe preview**:
  - `oh --dry-run` previews resolved runtime settings, auth state, skills, commands, tools, and configured MCP servers without executing the model, tools, or subagents.
  - Dry-run now reports a `ready` / `warning` / `blocked` readiness verdict with concrete next-step suggestions such as fixing auth, fixing MCP config, or running the prompt directly.
  - Prompt previews include likely matching skills and tools, while slash-command previews show whether the command is mostly read-only or stateful.
- **2026-04-18** ⚙️ **v0.1.7** — Packaging & TUI polish:
  - Install script now links `oh`, `ohmo`, and `openharness` into `~/.local/bin` instead of prepending the virtualenv `bin` directory to `PATH`, which avoids clobbering Conda-managed shells.
  - React TUI now supports `Shift+Enter` to insert a newline while keeping plain `Enter` as submit.
  - Busy-state animation in the React TUI is quieter and less error-prone on Windows terminals, with conservative spinner frames and reduced flashing.
- **2026-04-10** 🧠 **v0.1.6** — Auto-Compaction & Markdown TUI:
  - Auto-Compaction preserves task state and channel logs across context compression — agents can run multi-day sessions without manual compact/clear
  - Subprocess teammates run in headless worker mode; agent team creation stabilized
  - Assistant messages now render full Markdown in the React TUI
  - `ohmo` gains channel slash commands and multimodal attachment support
- **2026-04-08** 🔌 **v0.1.5** — MCP HTTP transport & Swarm polling:
  - MCP protocol adds HTTP transport, auto-reconnect on disconnect, and tool-only server compatibility
  - JSON Schema types inferred for MCP tool inputs — no manual type mapping needed
  - `ohmo` channels support file attachments and multimodal gateway messages
  - Subprocess agents are now pollable in real runs; permission modals serialized to prevent input swallowing
- **2026-04-08** 🌙 **v0.1.4** — Multi-provider auth & Moonshot/Kimi:
  - Native Moonshot/Kimi provider with `reasoning_content` support for thinking models
  - Auth overhaul: fixed provider-switching key mismatch, `OPENAI_BASE_URL` env override, profile-scoped credential priority
  - MCP gracefully handles disconnected servers in `call_tool` / `read_resource`
  - Security: built-in sensitive-path protection in PermissionChecker, hardened `web_fetch` URL validation
  - Stability: EIO crash recovery in Ink TUI, `--debug` logging, Windows cmd flash fix
- **2026-04-06** 🚀 **v0.1.2** — Unified setup flows and `ohmo` personal-agent app:
  - `oh setup` now guides provider selection as workflows instead of exposing raw auth/provider internals
  - Compatible API setup is now profile-scoped, so Anthropic/OpenAI-compatible endpoints can keep separate keys
  - `ohmo` ships as a packaged app with `~/.ohmo` workspace, gateway, bootstrap prompts, and channel config flow
- **2026-04-01** 🎨 **v0.1.0** — Initial **OpenHarness** open-source release featuring complete Harness architecture: 

<p align="center">
  <strong>Start here:</strong>
  <a href="#-quick-start">Quick Start</a> ·
  <a href="#-provider-compatibility">Provider Compatibility</a> ·
  <a href="docs/SHOWCASE.md">Showcase</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="CHANGELOG.md">Changelog</a>
</p>

---

## 🚀 Quick Start

### 1. Install

#### Linux / macOS / WSL

```bash
# One-click install
curl -fsSL https://raw.githubusercontent.com/Real-Bimox/OpenHarness/main/scripts/install.sh | bash

# Or via pip
pip install openharness-ai
```

#### Windows (Native)

```powershell
# One-click install (PowerShell)
iex (Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/Real-Bimox/OpenHarness/main/scripts/install.ps1')

# Or via pip
pip install openharness-ai
```

**Note**: Windows support is now native. In PowerShell, use `openh` instead of `oh` because `oh` can resolve to the built-in `Out-Host` alias.

### 2. Configure

```bash
oh setup    # interactive wizard — pick a provider, authenticate, done
# On Windows PowerShell, use: openh setup
```

Supports **Claude / OpenAI / Copilot / Codex / Moonshot(Kimi) / GLM / MiniMax / NVIDIA NIM** and any compatible endpoint.

### 3. Run

```bash
oh
# On Windows PowerShell, use: openh
```

<p align="center">
  <img src="assets/landing.png" alt="OpenHarness Landing Screen" width="700">
</p>

### 4. Set up ohmo (Personal Agent)

Want an AI agent that works for you from Feishu / Slack / Telegram / Discord?

```bash
ohmo init             # initialize ~/.ohmo workspace
ohmo config           # configure channels and provider
ohmo gateway start    # start the gateway — ohmo is now live in your chat app
```

ohmo runs through the configured **OpenHarness runtime**.

### Non-Interactive Mode (Pipes & Scripts)

```bash
# Single prompt → stdout
oh -p "Explain this codebase"

# JSON output for programmatic use
oh -p "List all functions in main.py" --output-format json

# Stream JSON events in real-time
oh -p "Fix the bug" --output-format stream-json

# Resume a previous session headlessly
oh -p "Continue where we left off" --resume <session_id> --output-format json
oh -p "One more thing" --continue
```

The `json` result includes `session_id`, `is_error`, `errors`, `permission_denials`, `system_messages`, and token `usage`, and `oh -p` exits non-zero when an engine error occurred — so scripts can rely on exit status.

### Headless Control Protocol (Local Orchestrators)

`oh --headless` exposes a session-aware JSONL control loop over stdin/stdout for local integrations that need more than one-shot prompts — session discovery, resume, status snapshots, and active-turn interruption — without a TUI, HTTP server, or new dependencies:

```bash
printf '%s\n' \
  '{"type":"submit","prompt":"inspect this repo","request_id":"s-1"}' \
  '{"type":"shutdown","request_id":"d-1"}' \
  | oh --headless --permission-mode full_auto
```

Key semantics: requests run FIFO; `status`/`list_sessions`/`interrupt`/`diagnostics` are answered immediately even mid-turn; `shutdown` is graceful by default while `{"type":"shutdown","force":true}` cancels the active turn; events carry token `usage`; interrupted turns are persisted for resume. Every request may carry an optional `correlation_id` that external orchestrators get echoed into diagnostics events (never used for routing). Full request/event reference: [docs/proposals/headless-local-control-api.md](docs/proposals/headless-local-control-api.md).

### Diagnostics & Observability (Local-Only)

OpenHarness records bounded, redacted, structured diagnostic events locally — no network sink, no new dependencies. Every turn, model call, tool execution, permission decision, snapshot write, and index operation is correlated by one `run_id` (plus `turn_id`/`request_id`/`tool_use_id`), stored as daily JSONL under the data dir with 14-day retention and a 25 MB/day cap, and never contains prompt text, assistant text, tool output, or secrets:

```bash
oh diagnostics status --json          # canonical health/status document
oh diagnostics tail --component api   # most recent events
oh diagnostics summary --since 24h    # counts, errors, token counters
oh diagnostics export --since 24h     # redacted support bundle (.tar.gz)
oh diagnostics purge --older-than 14d
```

The same data is reachable from integrations: headless `{"type":"diagnostics","scope":"summary"}` returns a `diagnostics_snapshot` event, and the MCP server exposes a read-only `diagnostics_status` tool. Long-lived modes run a watchdog that emits `slow_operation` events (with redacted stack snapshots past hard thresholds) and 5-second heartbeats. Diagnostics add < 0.5 ms per submitted line (gated at release time by `scripts/measure_per_line.py`) and can be disabled with `{"diagnostics": {"enabled": false}}` in settings. Design: [docs/proposals/observability-metrics.md](docs/proposals/observability-metrics.md).

### Optional Health/Status HTTP Server

The health server is opt-in and local-only. It adds FastAPI and plain uvicorn only when installed through the optional extra; the base install has no new runtime dependency:

```bash
pip install "openharness-ai[health-server]"

# From a source checkout:
python -m pip install -e ".[health-server]"
```

Run it as the primary process:

```bash
oh --health-server
oh --health-server --health-server-port 9090
```

Or run it beside long-lived local modes:

```bash
oh --headless --health-server
oh --task-worker --health-server
oh --mcp-serve --health-server
```

The v1 server binds to `127.0.0.1` only and exposes read-only JSON endpoints: `/health`, `/health/detailed`, `/api/status`, `/api/system/stats`, and `/v1/capabilities`. Non-loopback binding, authentication, cron integration, and Prometheus `/metrics` are deferred follow-ups. Design: [docs/proposals/health-status-http-server.md](docs/proposals/health-status-http-server.md).

### Dry Run (Safe Preview)

Use `--dry-run` when you want to inspect what OpenHarness would use before any live execution starts.

```bash
# Preview an interactive session setup
oh --dry-run

# Preview one prompt without executing the model or tools
oh --dry-run -p "Review this bug fix and grep for failing tests"

# Preview a slash command path
oh --dry-run -p "/plugin list"

# Get structured output for scripts or channels
oh --dry-run -p "Explain this repository" --output-format json
```

Dry-run is intentionally static:

- It does **not** call the model
- It does **not** execute tools or spawn subagents
- It does **not** connect to MCP servers
- It **does** resolve settings, auth status, prompt assembly, skills, commands, tools, and obvious MCP config problems

Readiness levels:

- `ready`: configuration looks usable; the next suggested action is usually to run the prompt directly
- `warning`: OpenHarness can resolve the session, but something important still looks wrong, such as broken MCP config or missing auth for later model work
- `blocked`: the requested path will not run successfully as-is, for example an unknown slash command or a prompt that cannot resolve a runtime client

`next actions` in the dry-run output tell you the shortest fix or follow-up step, such as:

- run `oh auth login`
- fix or disable broken MCP configuration
- run the prompt directly with `oh -p "..."` or open the interactive UI with `oh`

## 🔌 Provider Compatibility

OpenHarness treats providers as **workflows** backed by named profiles. In day-to-day use, prefer:

```bash
oh setup
oh provider list
oh provider use <profile>
```

### Built-in Workflows

| Workflow | What it is | Typical backends |
|----------|------------|------------------|
| **Anthropic-Compatible API** | Anthropic-style request format | Claude official, Kimi, GLM, MiniMax, internal Anthropic-compatible gateways |
| **Claude Subscription** | Claude CLI subscription bridge | Local `~/.claude/.credentials.json` |
| **OpenAI-Compatible API** | OpenAI-style request format | OpenAI official, OpenRouter, DashScope, DeepSeek, SiliconFlow, Groq, Ollama, GitHub Models |
| **Codex Subscription** | Codex CLI subscription bridge | Local `~/.codex/auth.json` |
| **GitHub Copilot** | Copilot OAuth workflow | GitHub Copilot device-flow login |

### Compatible API Families

#### Anthropic-Compatible API

Typical examples:

| Backend | Base URL | Example models |
|---------|----------|----------------|
| **Claude official** | `https://api.anthropic.com` | `claude-sonnet-4-6`, `claude-opus-4-6` |
| **Moonshot / Kimi** | `https://api.moonshot.cn/anthropic` | `kimi-k2.5` |
| **Zhipu / GLM** | custom Anthropic-compatible endpoint | `glm-4.5` |
| **MiniMax** | custom Anthropic-compatible endpoint | `minimax-m1` |

#### OpenAI-Compatible API

Any provider implementing the OpenAI `/v1/chat/completions` style API works:

| Backend | Base URL | Example models |
|---------|----------|----------------|
| **OpenAI** | `https://api.openai.com/v1` | `gpt-5.4`, `gpt-4.1` |
| **OpenRouter** | `https://openrouter.ai/api/v1` | provider-specific |
| **Alibaba DashScope** | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen3.5-flash`, `qwen3-max`, `deepseek-r1` |
| **DeepSeek** | `https://api.deepseek.com` | `deepseek-chat`, `deepseek-reasoner` |
| **GitHub Models** | `https://models.inference.ai.azure.com` | `gpt-4o`, `Meta-Llama-3.1-405B-Instruct` |
| **SiliconFlow** | `https://api.siliconflow.cn/v1` | `deepseek-ai/DeepSeek-V3` |
| **NVIDIA NIM** | `https://integrate.api.nvidia.com/v1` | `openai/gpt-oss-120b`, `nvidia/llama-3.3-nemotron-super-49b-v1` |
| **Google Gemini** | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-2.5-flash`, `gemini-2.5-pro` |
| **Groq** | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` |
| **Ollama (local)** | `http://localhost:11434/v1` | any local model |

### Advanced Profile Management

```bash
# List saved workflows
oh provider list

# Switch the active workflow
oh provider use codex

# Add your own compatible endpoint
oh provider add my-endpoint \
  --label "My Endpoint" \
  --provider openai \
  --api-format openai \
  --auth-source openai_api_key \
  --model my-model \
  --base-url https://example.com/v1
```

For custom compatible endpoints, OpenHarness can bind credentials per profile instead of forcing every Anthropic-compatible or OpenAI-compatible backend to share the same API key.

### Ollama (Local Models)

Run local models through Ollama's OpenAI-compatible endpoint:

```bash
# Add an Ollama provider profile
oh provider add ollama \
  --label "Ollama" \
  --provider Ollama \
  --api-format openai \
  --auth-source openai_api_key \
  --model glm-4.7-flash:q8_0 \
  --base-url http://localhost:11434/v1
```
```
Saved provider profile: ollama
```

```bash
# Activate and verify
oh provider use ollama
```
```
Activated provider profile: ollama
```

```bash
oh provider list
```
```
  claude-api: Anthropic-Compatible API [ready]
  ...
  moonshot: Moonshot (Kimi) [missing auth]
    auth=moonshot_api_key model=kimi-k2.5 base_url=https://api.moonshot.cn/v1
* ollama: Ollama [ready]
    auth=openai_api_key model=glm-4.7-flash:q8_0 base_url=http://localhost:11434/v1
```

### GitHub Copilot Format (`--api-format copilot`)

Use your existing GitHub Copilot subscription as the LLM backend. Authentication uses GitHub's OAuth device flow — no API keys needed.

```bash
# One-time login (opens browser for GitHub authorization)
oh auth copilot-login

# Then launch with Copilot as the provider
uv run oh --api-format copilot

# Or via environment variable
export OPENHARNESS_API_FORMAT=copilot
uv run oh

# Check auth status
oh auth status

# Remove stored credentials
oh auth copilot-logout
```

| Feature | Details |
|---------|---------|
| **Auth method** | GitHub OAuth device flow (no API key needed) |
| **Token management** | Automatic refresh of short-lived session tokens |
| **Enterprise** | Supports GitHub Enterprise via `--github-domain` flag |
| **Models** | Uses Copilot's default model selection |
| **API** | OpenAI-compatible chat completions under the hood |

---

## 🏗️ Harness Architecture

OpenHarness implements the core Agent Harness pattern with 10 subsystems:

```
openharness/
  engine/          # 🧠 Agent Loop — query → stream → tool-call → loop
  tools/           # 🔧 43 Tools — file I/O, shell, search, web, MCP
  skills/          # 📚 Knowledge — on-demand skill loading (.md files)
  plugins/         # 🔌 Extensions — commands, hooks, agents, MCP servers
  permissions/     # 🛡️ Safety — multi-level modes, path rules, command deny
  hooks/           # ⚡ Lifecycle — PreToolUse/PostToolUse event hooks
  commands/        # 💬 54 Commands — /help, /commit, /plan, /resume, ...
  mcp/             # 🌐 MCP — Model Context Protocol client
  memory/          # 🧠 Memory — persistent cross-session knowledge
  tasks/           # 📋 Tasks — background task management
  coordinator/     # 🤝 Multi-Agent — subagent spawning, team coordination
  prompts/         # 📝 Context — system prompt assembly, CLAUDE.md, skills
  config/          # ⚙️ Settings — multi-layer config, migrations
  ui/              # 🖥️ React TUI — backend protocol + frontend
```

### The Agent Loop

The heart of the harness. One loop, endlessly composable:

```python
while True:
    response = await api.stream(messages, tools)
    
    if response.stop_reason != "tool_use":
        break  # Model is done
    
    for tool_call in response.tool_uses:
        # Permission check → Hook → Execute → Hook → Result
        result = await harness.execute_tool(tool_call)
    
    messages.append(tool_results)
    # Loop continues — model sees results, decides next action
```

The model decides **what** to do. The harness handles **how** — safely, efficiently, with full observability.

### Harness Flow

```mermaid
flowchart LR
    U[User Prompt] --> C[CLI or React TUI]
    C --> R[RuntimeBundle]
    R --> Q[QueryEngine]
    Q --> A[Anthropic-compatible API Client]
    A -->|tool_use| T[Tool Registry]
    T --> P[Permissions + Hooks]
    P --> X[Files Shell Web MCP Tasks]
    X --> Q
```

---

## ✨ Features

### 🔧 Tools (43+)

| Category | Tools | Description |
|----------|-------|-------------|
| **File I/O** | Bash, Read, Write, Edit, Glob, Grep | Core file operations with permission checks |
| **Search** | WebFetch, WebSearch, ToolSearch, LSP | Web and code search capabilities |
| **Notebook** | NotebookEdit | Jupyter notebook cell editing |
| **Agent** | Agent, SendMessage, TeamCreate/Delete | Subagent spawning and coordination |
| **Task** | TaskCreate/Get/List/Update/Stop/Output | Background task management |
| **MCP** | MCPTool, ListMcpResources, ReadMcpResource | Model Context Protocol integration |
| **Mode** | EnterPlanMode, ExitPlanMode, Worktree | Workflow mode switching |
| **Schedule** | CronCreate/List/Delete, RemoteTrigger | Scheduled and remote execution |
| **Meta** | Skill, Config, Brief, Sleep, AskUser | Knowledge loading, configuration, interaction |

Every tool has:
- **Pydantic input validation** — structured, type-safe inputs
- **Self-describing JSON Schema** — models understand tools automatically
- **Permission integration** — checked before every execution
- **Hook support** — PreToolUse/PostToolUse lifecycle events

### 📚 Skills System

Skills are **on-demand knowledge** — loaded only when the model needs them:

```
Available Skills:
- commit: Create clean, well-structured git commits
- review: Review code for bugs, security issues, and quality
- debug: Diagnose and fix bugs systematically
- plan: Design an implementation plan before coding
- test: Write and run tests for code
- simplify: Refactor code to be simpler and more maintainable
- pdf: PDF processing with pypdf (from anthropics/skills)
- xlsx: Excel operations (from anthropics/skills)
- ... 40+ more
```

Skills can live in bundled, user, ohmo, project, or plugin locations. User-level skills are loaded from:

```text
~/.openharness/skills/<skill>/SKILL.md
~/.claude/skills/<skill>/SKILL.md
~/.agents/skills/<skill>/SKILL.md
```

Project-level skills are enabled by default and are discovered from the current working directory up to the git root:

```text
<project>/.openharness/skills/<skill>/SKILL.md
<project>/.agents/skills/<skill>/SKILL.md
<project>/.claude/skills/<skill>/SKILL.md
```

Disable project skills for untrusted repositories with:

```bash
oh config set allow_project_skills false
```

Use `/skills` to list loaded skills with their source and path. User-invocable skills can be run directly as slash commands, for example `/deploy staging`.

**Compatible with [anthropics/skills](https://github.com/anthropics/skills)** — use the `SKILL.md` directory layout above.

### 🌐 Web search and proxy settings

Built-in `web_search` uses DuckDuckGo HTML search by default. In regions where that endpoint is unreachable, point OpenHarness at a trusted public HTML search endpoint or your own SearXNG instance:

```bash
export OPENHARNESS_WEB_SEARCH_URL="https://your-searxng.example/search"
```

`web_search` and `web_fetch` keep `trust_env=False` for SSRF safety, so they do not automatically inherit `HTTP_PROXY` / `HTTPS_PROXY`. If you need a proxy, opt in with an OpenHarness-specific variable:

```bash
export OPENHARNESS_WEB_PROXY="http://127.0.0.1:7890"
```

The proxy URL must be HTTP/HTTPS and cannot contain embedded credentials.

### 🔌 Plugin System

**Compatible with [claude-code plugins](https://github.com/anthropics/claude-code/tree/main/plugins)**. Tested with 12 official plugins:

| Plugin | Type | What it does |
|--------|------|-------------|
| `commit-commands` | Commands | Git commit, push, PR workflows |
| `security-guidance` | Hooks | Security warnings on file edits |
| `hookify` | Commands + Agents | Create custom behavior hooks |
| `feature-dev` | Commands | Feature development workflow |
| `code-review` | Agents | Multi-agent PR review |
| `pr-review-toolkit` | Agents | Specialized PR review agents |

```bash
# Manage plugins
oh plugin list
oh plugin install <source>
oh plugin enable <name>
```

### 🤝 Ecosystem Workflows

OpenHarness is useful as a lightweight harness layer around Claude-style tooling conventions:

- **OpenClaw-oriented workflows** can reuse Markdown-first knowledge and command-driven collaboration patterns.
- **Claude-style plugins and skills** stay portable because OpenHarness keeps those formats familiar.
- **Multi-agent work** maps well onto the built-in team, task, and background execution primitives.

For concrete usage ideas instead of generic claims, see [`docs/SHOWCASE.md`](docs/SHOWCASE.md).

### 🛡️ Permissions

Multi-level safety with fine-grained control:

| Mode | Behavior | Use Case |
|------|----------|----------|
| **Default** | Ask before write/execute | Daily development |
| **Auto** | Allow everything | Sandboxed environments |
| **Plan Mode** | Block all writes | Large refactors, review first |

**Path-level rules** in `settings.json`:
```json
{
  "permission": {
    "mode": "default",
    "path_rules": [{"pattern": "/etc/*", "allow": false}],
    "denied_commands": ["rm -rf /", "DROP TABLE *"]
  }
}
```

### 🖥️ Terminal UI

React/Ink TUI with full interactive experience:

- **Command picker**: Type `/` → arrow keys to select → Enter
- **Permission dialog**: Interactive y/n with tool details
- **Mode switcher**: `/permissions` → select from list
- **Session resume**: `/resume` → pick from history
- **Animated spinner**: Real-time feedback during tool execution
- **Keyboard shortcuts**: Shown at the bottom, context-aware

### 📡 CLI

```
oh [OPTIONS] COMMAND [ARGS]

Session:     -c/--continue, -r/--resume, -n/--name
Model:       -m/--model, --effort, --max-turns
Output:      -p/--print, --output-format text|json|stream-json, --headless
Permissions: --permission-mode, --dangerously-skip-permissions
Context:     -s/--system-prompt, --append-system-prompt, --settings
Advanced:    -d/--debug, --mcp-config, --bare, --health-server

Subcommands: oh setup | oh provider | oh auth | oh mcp | oh plugin
```

### 🧑‍💼 ohmo Personal Agent

`ohmo` is a personal-agent app built on top of OpenHarness. It is packaged alongside `oh`, with its own workspace and gateway:

```bash
# Initialize personal workspace
ohmo init

# Configure gateway channels and pick a provider profile
ohmo config

# Run the personal agent
ohmo

# Run the gateway in foreground
ohmo gateway run

# Check or restart the gateway
ohmo gateway status
ohmo gateway restart
```

Key concepts:

- `~/.ohmo/`
  - personal workspace root
- `soul.md`
  - long-term agent personality and behavior
- `identity.md`
  - who `ohmo` is
- `user.md`
  - user profile and preferences
- `BOOTSTRAP.md`
  - first-run landing ritual
- `memory/`
  - personal memory
- `gateway.json`
  - selected provider profile and channel configuration

`ohmo config` uses the same workflow language as `oh setup`, so you can point the personal-agent gateway at:

- `Anthropic-Compatible API`
- `Claude Subscription`
- `OpenAI-Compatible API`
- `Codex Subscription`
- `GitHub Copilot`

`ohmo init` creates the home workspace once. After that, use `ohmo config` to update provider and channel settings; if the gateway is already running, the config flow can restart it for you.

Currently `ohmo init` / `ohmo config` can guide channel setup for:

- Telegram
- Slack
- Discord
- Feishu

---

## 📊 Test Results

| Suite | Tests | Status |
|-------|-------|--------|
| Unit + Integration | 114 | ✅ All passing |
| CLI Flags E2E | 6 | ✅ Real model calls |
| Harness Features E2E | 9 | ✅ Retry, skills, parallel, permissions |
| React TUI E2E | 3 | ✅ Welcome, conversation, status |
| TUI Interactions E2E | 4 | ✅ Commands, permissions, shortcuts |
| Real Skills + Plugins | 12 | ✅ anthropics/skills + claude-code/plugins |

```bash
# Run all tests
uv run pytest -q                           # 1260 unit/integration
python scripts/test_harness_features.py     # Harness E2E
python scripts/test_real_skills_plugins.py  # Real plugins E2E
```

---

## 🔧 Extending OpenHarness

### Add a Custom Tool

```python
from pydantic import BaseModel, Field
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

class MyToolInput(BaseModel):
    query: str = Field(description="Search query")

class MyTool(BaseTool):
    name = "my_tool"
    description = "Does something useful"
    input_model = MyToolInput

    async def execute(self, arguments: MyToolInput, context: ToolExecutionContext) -> ToolResult:
        return ToolResult(output=f"Result for: {arguments.query}")
```

### Add a Custom Skill

Create `~/.openharness/skills/my-skill.md`:

```markdown
---
name: my-skill
description: Expert guidance for my specific domain
---

# My Skill

## When to use
Use when the user asks about [your domain].

## Workflow
1. Step one
2. Step two
...
```

### Add a Plugin

Create `.openharness/plugins/my-plugin/.claude-plugin/plugin.json`:

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "My custom plugin"
}
```

Add commands in `commands/*.md`, hooks in `hooks/hooks.json`, agents in `agents/*.md`.

---

## 🌍 Showcase

OpenHarness is most useful when treated as a small, inspectable harness you can adapt to a real workflow:

- **Repo coding assistant** for reading code, patching files, and running checks locally.
- **Headless scripting tool** for `json` and `stream-json` output in automation flows.
- **Plugin and skill testbed** for experimenting with Claude-style extensions.
- **Multi-agent prototype harness** for task delegation and background execution.
- **Provider comparison sandbox** across Anthropic-compatible backends.

See [`docs/SHOWCASE.md`](docs/SHOWCASE.md) for short, reproducible examples.

---

## 🤝 Contributing

OpenHarness is a **community-driven research project**. We welcome contributions in:

| Area | Examples |
|------|---------|
| **Tools** | New tool implementations for specific domains |
| **Skills** | Domain knowledge `.md` files (finance, science, DevOps...) |
| **Plugins** | Workflow plugins with commands, hooks, agents |
| **Providers** | Support for more LLM backends (OpenAI, Ollama, etc.) |
| **Multi-Agent** | Coordination protocols, team patterns |
| **Testing** | E2E scenarios, edge cases, benchmarks |
| **Documentation** | Architecture guides, tutorials, translations |

```bash
# Development setup
git clone https://github.com/Real-Bimox/OpenHarness.git
cd OpenHarness
uv sync --extra dev
uv run pytest -q  # Verify everything works
```

Useful contributor entry points:

- [`CONTRIBUTING.md`](CONTRIBUTING.md) for setup, checks, and PR expectations
- [`CHANGELOG.md`](CHANGELOG.md) for user-visible changes
- [`docs/SHOWCASE.md`](docs/SHOWCASE.md) for real-world usage patterns worth documenting

---

## 🔧 Troubleshooting

### Backspace key in macOS Terminal.app

OpenHarness handles both common terminal delete sequences, including the raw `DEL` byte (`0x7f`) that macOS Terminal.app sends for Backspace. If Backspace inserts spaces or visible control characters instead of deleting text, upgrade OpenHarness first.

For older versions that do not include this fix, use a terminal that sends a standard Backspace sequence or adjust your terminal keyboard profile as a temporary workaround.

---

## 📄 License

MIT — see [LICENSE](LICENSE).

---

<p align="center">
  <img src="assets/logo.png" alt="OpenHarness" width="48">
  <br>
  <strong>Oh my Harness!</strong>
  <br>
  <em>The model is the agent. The code is the harness.</em>
</p>

<div align="center">
  <a href="https://star-history.com/#Real-Bimox/OpenHarness&Date">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=Real-Bimox/OpenHarness&type=Date&theme=dark" />
      <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=Real-Bimox/OpenHarness&type=Date" />
      <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=Real-Bimox/OpenHarness&type=Date" style="border-radius: 15px; box-shadow: 0 0 30px rgba(0, 217, 255, 0.3);" />
    </picture>
  </a>
</div>

<p align="center">
  <em> Thanks for visiting ✨ OpenHarness!</em><br><br>
  <img src="https://visitor-badge.laobi.icu/badge?page_id=Real-Bimox.OpenHarness&style=for-the-badge&color=00d4ff" alt="Views">
</p>
