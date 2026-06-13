# OpenHarness Reference — CLI, Settings, and Tools

A complete catalogue of the `oh` / `ohmo` command surface, the configuration
settings that gate each feature, and the built-in agent tools.

This document is maintained by hand against the source tree. For the
always-current option lists, run `oh --help` or `oh <group> --help`
(every command group below supports `--help`).

---

## 1. Command-line interface (`oh`)

`oh` (aliases: `openharness`, `openh`) runs in one of several **modes**, and
exposes feature **command groups** as sub-applications.

### 1.1 Run modes

| Invocation | Mode |
|---|---|
| `oh` | Interactive terminal UI (React/Ink TUI) |
| `oh -p "<prompt>"` / `oh --print` | One-shot, non-interactive. `--output-format text\|json\|stream-json`. Supports `--resume <id>` / `--continue` to continue a prior session and emits `session_id` in `json`/`stream-json`. |
| `oh --headless` | Long-running local JSONL control protocol over stdin/stdout (see [`integration/headless-local.md`](integration/headless-local.md)). |
| `oh --mcp-serve` | Run as a stdio MCP server exposing the search / skill / recovery / diagnostics operations. |
| `oh --health-server` | Start the optional health-status HTTP server (see §1.13). Composable with the modes above. |
| `oh setup` | Interactive first-run configuration (provider, auth, model). |

Common session/model flags: `-m/--model`, `--effort`, `--max-turns`,
`-c/--continue`, `-r/--resume`, `-n/--name`, `-s/--system-prompt`,
`--append-system-prompt`, `--settings`, `--mcp-config`, `--bare`, `--dry-run`,
`-k/--api-key`, `--base-url`, `--theme`, `--verbose`, `-d/--debug`.

Permission flags: `--permission-mode default|plan|full_auto`, `--allowed-tools`,
`--disallowed-tools`, `--dangerously-skip-permissions`. See the
[Permissions](../README.md#-permissions) section of the README for mode
semantics; the built-in sensitive-path protection is enforced in every mode.

### 1.2 `oh provider` — provider profiles

| Command | Purpose |
|---|---|
| `oh provider list` | List configured provider profiles. |
| `oh provider use <profile>` | Switch the active profile. |
| `oh provider add <profile> …` | Add a profile. Options include `--provider`, `--api-format`, `--auth-source`, `--model`, `--base-url`, `--allowed-model`, `--context-window-tokens`, `--auto-compact-threshold-tokens`, `--credential-slot`, `--api-key`. |
| `oh provider edit <profile> …` | Edit an existing profile (same options as `add`, including `--api-key` to replace a stored key). |
| `oh provider remove <profile>` | Delete a profile. |

### 1.3 `oh auth` — authentication

| Command | Purpose |
|---|---|
| `oh auth login` | Interactive auth setup for the active/selected profile. |
| `oh auth status` | Show current auth state. |
| `oh auth logout` | Clear stored credentials. |
| `oh auth switch` | Switch between configured auth profiles. |
| `oh auth copilot-login` / `copilot-logout` | GitHub Copilot device-code login / logout. GitHub Enterprise domain is entered interactively during login. |
| `oh auth codex-login` | Bridge an existing Codex CLI subscription credential. |
| `oh auth claude-login` | Bridge an existing local subscription credential (`~/.claude/.credentials.json`). |

### 1.4 `oh config` — settings

| Command | Purpose |
|---|---|
| `oh config show` | Print the resolved settings. |
| `oh config set <key> <value>` | Set a settings value (e.g. `oh config set allow_project_skills false`). |

See §2 for the settings catalogue.

### 1.5 `oh mcp` — MCP servers

| Command | Purpose |
|---|---|
| `oh mcp list` | List configured MCP servers. |
| `oh mcp add` | Register an MCP server. |
| `oh mcp remove` | Remove a configured MCP server. |

### 1.6 `oh plugin` — plugins

| Command | Purpose |
|---|---|
| `oh plugin list` | List installed plugins. |
| `oh plugin install <source>` | Install a plugin from a source. |
| `oh plugin uninstall <name>` | Remove an installed plugin. |

Plugins are enabled/disabled via the `enabled_plugins` setting (there is no
`plugin enable` command).

### 1.7 `oh sessions` — conversation search

A rebuildable SQLite FTS5 index over saved session snapshots (zero LLM cost).
Gated by `conversation_index_enabled` (default on).

| Command | Purpose |
|---|---|
| `oh sessions list` | List recorded sessions. Options: `--project`, `--limit`, `--json`. |
| `oh sessions search "<query>"` | Full-text search across past conversations. Options: `--project`, `--limit`, `--json`. |
| `oh sessions reindex` | Rebuild the search index from snapshots. |

The same capability is also exposed to the agent as the `session_search` tool,
as a headless `search_sessions` request, and through `--mcp-serve`.

### 1.8 `oh fallback` — provider failover

Declarative provider fallback chains with mid-turn switching and per-provider
API-key cooldown pools (see [`proposals/error-recovery.md`](proposals/error-recovery.md)).

| Command | Purpose |
|---|---|
| `oh fallback list` | Show the configured fallback chain. |
| `oh fallback add <provider> …` | Append a provider to the chain. Options include `--base-url`, `--api-format`, `--api-key-env`. |
| `oh fallback remove <provider>` | Remove a provider from the chain. |
| `oh fallback clear` | Clear the fallback chain. |

### 1.9 `oh skills` — skill usage & learning lifecycle

Telemetry and the staged skill-learning loop (see
[`proposals/skill-learning-loop.md`](proposals/skill-learning-loop.md)).
Gated by the `skills.*` settings (§2).

| Command | Purpose |
|---|---|
| `oh skills usage` | Show per-skill usage telemetry (active / stale / archived). |
| `oh skills pin <skill>` / `unpin <skill>` | Pin/unpin a skill so the lifecycle never archives it. |
| `oh skills pending` | List skill changes awaiting approval. |
| `oh skills diff <id>` | Show the faithful diff of a pending change. |
| `oh skills approve <id>` / `discard <id>` | Approve or discard a pending change. |
| `oh skills curator` | Run the periodic LLM curator pass on demand. |

### 1.10 `oh diagnostics` — observability

Opt-in diagnostics recorder and surfaces (see
[`proposals/observability-metrics.md`](proposals/observability-metrics.md)).
Gated by `diagnostics.enabled` (default off).

| Command | Purpose |
|---|---|
| `oh diagnostics status` | Current diagnostics/runtime status. Options include `--component`, `--json`. |
| `oh diagnostics tail` | Tail recent diagnostics events. Options include `--component`, `--since`, `--include-stacks`, `--limit`. |
| `oh diagnostics summary` | Aggregated summary of recorded events. |
| `oh diagnostics export` | Export a diagnostics bundle. Options include `--output`, `--include-stacks`. |
| `oh diagnostics purge` | Purge stored diagnostics. Options include `--older-than`. |

A headless `diagnostics` request exposes the same status snapshot.

### 1.11 `oh cron` — scheduler daemon

A local scheduler daemon plus job management.

| Command | Purpose |
|---|---|
| `oh cron start` / `stop` / `status` | Manage the scheduler daemon lifecycle. |
| `oh cron list` | List scheduled jobs. |
| `oh cron toggle <job>` | Enable/disable a job. |
| `oh cron history` | Show recent run history. |
| `oh cron logs` | Show job logs. |

### 1.12 `oh autopilot` — repo autopilot

Project-level autopilot: intake of work items, execution helpers, and a static
dashboard.

| Command | Purpose |
|---|---|
| `oh autopilot status` | Show autopilot state for the current repo. |
| `oh autopilot list` | List queued/known work items. |
| `oh autopilot add <source>` | Add an intake source/item. |
| `oh autopilot context` | Show the resolved autopilot context. |
| `oh autopilot journal` | Show the autopilot activity journal. |
| `oh autopilot scan <target>` | Scan a configured source for new work. |
| `oh autopilot run-next` | Execute the next queued item. |
| `oh autopilot tick` | Advance the autopilot one step (used by the scheduler). |
| `oh autopilot install-cron` | Register the autopilot tick with the `oh cron` scheduler. |
| `oh autopilot export-dashboard` | Export a static kanban dashboard (e.g. for Pages). |

### 1.13 Optional health-status HTTP server

Ships behind the optional extra: `pip install "openharness-ai[health-server]"`
(FastAPI + uvicorn). Start with `oh --health-server [--health-server-port N]`
(binds loopback `127.0.0.1` by default). Endpoints: `GET /health`,
`/health/detailed`, `/api/status`, `/api/system/stats`, `/v1/capabilities`.
Composable with `--headless`, `--task-worker`, and `--mcp-serve`; rejects
incompatible single-shot flags. See
[`proposals/health-status-http-server.md`](proposals/health-status-http-server.md).

---

## 2. Configuration settings (`settings.json`)

Settings live in `settings.json` (resolved via `oh config show`); most can be
set with `oh config set <key> <value>` or overridden per-process with
`--settings`. The authoritative model and exact defaults are in
`src/openharness/config/settings.py`. Key feature gates and tunables:

### 2.1 Core feature flags

| Key | Purpose |
|---|---|
| `prompt_caching_enabled` | Anthropic prompt-caching breakpoints (default on; no-op for non-Anthropic formats). |
| `conversation_index_enabled` | Conversation-search FTS index + `oh sessions` (default on). |
| `task_worker_idle_timeout_s` | Idle timeout before a persistent background worker exits (default 600). |
| `auto_extract_enabled` | Background durable-memory extraction. |
| `session_memory_enabled` | Per-session memory. |
| `auto_dream_enabled` / `auto_dream_min_hours` / `auto_dream_min_sessions` | Background memory-consolidation ("auto-dream") and its thresholds. |
| `voice_mode` | Voice / speech-to-text input in the TUI (also `/voice`). |
| `vim_mode` | Vim-style editing in the TUI prompt (also `/vim`). |
| `fast_mode` | Faster, lower-latency turn behavior. |
| `effort` / `output_style` / `theme` / `passes` | Reasoning effort, transcript style, UI theme, multi-pass behavior. |
| `allow_project_skills` / `allow_project_plugins` | Trust gates for project-local skills/plugins. |
| `project_skill_dirs` | Project skill search directories (`.openharness` / `.agents` / `.claude`). |

### 2.2 Memory (`memory.*`)

| Key | Purpose |
|---|---|
| `memory.extract_model` | Cheaper model for the durable-memory extraction pass. |
| `memory.max_files` / `memory.max_entrypoint_lines` / `memory.max_entrypoint_bytes` | Memory-store size bounds. |

### 2.3 Skill learning (`skills.*`)

`review_enabled`, `review_interval_turns`, `review_model`, `write_approval`,
`guard_writes`, `curator_enabled`, `curator_interval_hours`, `curator_model`,
`stale_after_days`, `archive_after_days` — gate the post-turn skill review fork,
write scanning/approval, and the weekly curator. Surfaced by `oh skills`.

### 2.4 Diagnostics (`diagnostics.*`)

`enabled` (default off), `event_log_enabled`, `retention_days`, `max_daily_mb`,
`include_paths`, `heartbeat_enabled`, `slow_thresholds`, `export_include_logs` —
control the observability recorder. Surfaced by `oh diagnostics`.

### 2.5 Error recovery & fallback

| Key | Purpose |
|---|---|
| `api_max_retries` | Hard per-attempt budget for the resilient client. |
| `fallback_providers` | Provider fallback chain (managed by `oh fallback`). |
| `credential_pools` | Per-provider API-key pools with cooldowns. |

### 2.6 Sandbox (`sandbox.*`)

`enabled`, `backend` (`subprocess` | `docker`), `fail_if_unavailable`,
`enabled_platforms`; `docker.{auto_build_image, cpu_limit, memory_limit, image,
extra_mounts, extra_env}`; `network.*` and `filesystem.*` allow/deny lists for
OS-level execution isolation. Environment overrides: `OPENHARNESS_SANDBOX_*`.

### 2.7 Web tools (`web.*`)

`proxy`, `resolution_mode`, `synthetic_dns_cidrs` — proxy and DNS-resolution
behavior for `web_fetch` / `web_search`. Environment overrides:
`OPENHARNESS_WEB_RESOLUTION_MODE`, `OPENHARNESS_WEB_SYNTHETIC_DNS_CIDRS`.

### 2.8 Media

| Group | Purpose |
|---|---|
| `image_generation.*` | `image_generation` tool config (`provider`, `model`, `codex_model`, `base_url`, …). |
| `vision.*` | Image-to-text fallback model (`model`, `api_key`, `base_url`). |

### 2.9 Permissions (`permission.*`)

`mode` (`default` / `plan` / `full_auto`), `path_rules` (per-path allow/deny),
`denied_commands`. CLI: `--permission-mode`, `--allowed-tools`,
`--disallowed-tools`. The built-in sensitive-path deny cannot be overridden by
any mode.

---

## 3. Built-in agent tools

Tools are registered in `create_default_tool_registry()`
(`src/openharness/tools/__init__.py`). The headline set (file ops, search,
web, agent/team/task orchestration, MCP, plan mode, worktree, cron, skill,
config) is documented in the [README tool table](../README.md). The full
registry also includes tools that the README table omits:

| Tool | Purpose |
|---|---|
| `session_search` | Search past conversations (the `oh sessions` capability, as a tool). |
| `skill_manage` | Create/edit/patch/delete user skills (the skill-learning loop write path; write-scanned, confined to user skills). |
| `todo_write` | Maintain the in-session todo checklist. |
| `image_generation` | Generate images via the configured provider. |
| `image_to_text` | Describe/extract text from images (vision fallback). |
| `mcp_auth` | Authenticate against MCP servers that require it. |
| `cron_toggle` | Enable/disable a scheduled job from within a session. |

Network-capable tools (`web_fetch`, `web_search`, `image_to_text`,
`image_generation`) are excluded under `--bare`.

---

## 4. `ohmo` personal agent

`ohmo` is the personal-agent app packaged alongside `oh`, with its own
workspace and gateway.

| Command | Purpose |
|---|---|
| `ohmo` | Interactive personal agent. Also supports `-p/--print`, `--model`, `--profile`, `--workspace`, `--max-turns`, `--resume`, `--continue`. |
| `ohmo init` | Initialize the personal workspace. |
| `ohmo config` | Manage ohmo configuration. |
| `ohmo doctor` | Diagnose the ohmo setup. |
| `ohmo memory list\|add\|remove` | Manage personal memory entries. |
| `ohmo soul show\|edit` / `ohmo user show\|edit --set …` | Edit the personality (`soul.md`) and user profile (`user.md`). |
| `ohmo gateway run\|start\|stop\|status\|restart` | Manage the messaging gateway. |

### Chat channels

The gateway supports multiple chat channels. Beyond the commonly-used
Telegram / Slack / Discord / Feishu integrations, the codebase also implements
Matrix, WhatsApp, DingTalk, QQ, email, and MoChat channels. Channels are
configured through the gateway/channel configuration; see `ohmo config` and the
channel settings.
