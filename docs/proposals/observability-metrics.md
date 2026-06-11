# Proposal: observability-metrics

## Status

| Field | Value |
|---|---|
| Status | IMPLEMENTED |
| Proposal branch | `proposal/observability-metrics` |
| Owner | Bahram Boutorabi |
| Created | 2026-06-11 |
| Related | [headless-local-control-api](headless-local-control-api.md), [mcp-server-mode](mcp-server-mode.md), [error-recovery](error-recovery.md), [performance-hardening-roadmap](performance-hardening-roadmap.md), [release-architecture-hardening](release-architecture-hardening.md) |

## Summary

OpenHarness currently has useful diagnostic fragments: structured headless events, token usage snapshots, retry/fallback stream events, standard Python logging, skill usage telemetry, cron history, gateway state, and startup/per-line measurement scripts. These are not yet enough for reliable consumer support because they are not tied together by one correlation model, one local event schema, one retention policy, or one export path.

This proposal adds a local-first observability layer that records bounded, redacted, structured metrics and diagnostic events across the runtime without introducing any new runtime dependency or external service. The goal is to answer the practical questions that come up during integration:

- What request, turn, tool, provider call, or background job failed?
- How long did each phase take?
- Was the problem model/provider, local tool execution, permissions, indexing, persistence, headless protocol handling, MCP, channels, cron, gateway, or memory/compaction?
- What changed between a passing and failing run?
- Can a consumer send us a diagnostic bundle that contains useful evidence without prompt text, tool output, secrets, or generated attribution?

## Current State

### Existing Signals

OpenHarness already exposes or records the following partial signals:

| Area | Existing coverage |
|---|---|
| Headless JSONL | `process_ready`, `state_snapshot`, `assistant_*`, `tool_*`, `compact_progress`, `line_complete`, `error`, `shutdown` events. |
| Usage | `UsageSnapshot` tracks input tokens, output tokens, cache creation tokens, and cache read tokens. Session totals are available through `QueryEngine.total_usage`. |
| Model recovery | `ApiRetryEvent`, `ProviderFallbackEvent`, and `CredentialRotatedEvent` are emitted during a turn. |
| Tools | Query stream emits `ToolExecutionStarted` and `ToolExecutionCompleted`; debug logs include tool duration and output length. |
| Compaction | `CompactProgressEvent` exposes phase, trigger, attempt, and checkpoint. |
| Session persistence | Session snapshots persist messages, usage, selected tool metadata, and feed the conversation index best-effort. |
| Conversation index | SQLite index has WAL fallback, busy retry logic, FTS probing, and best-effort update isolation. |
| Skill loop | `skill_loop_status` reports usage telemetry, lifecycle states, pending writes, and curator state. |
| Cron/gateway | Cron history and ohmo gateway state files exist. |
| Logging | Standard library logging is used across modules; `OPENHARNESS_LOG_LEVEL` controls application logging. |
| Performance probes | `scripts/measure_startup.py` and `scripts/measure_per_line.py` provide release-time spot checks. |

### Gaps

The missing pieces are structural:

1. No single `run_id` or `turn_id` ties together headless requests, model calls, tool calls, persistence, index updates, gateway activity, and background work.
2. No durable local event log captures request, model, tool, storage, and background lifecycle events in one schema.
3. No standardized latency metrics exist for request duration, time to first token, model duration, tool duration, snapshot write duration, index search/update duration, headless response latency, MCP tool duration, channel media handling, or background jobs.
4. No standardized counters exist for retries, fallbacks, credential rotations, permission denials, tool errors, index busy/rebuild events, protocol parse errors, dropped diagnostic events, queue depth pressure, or channel failures.
5. No health snapshot gives a support engineer a quick view of current mode, version, settings, auth profile state, index state, queue depths, active tasks, recent errors, and resource usage.
6. No diagnostic bundle export exists with a clear redaction policy.
7. No release gate verifies that metrics are emitted, redacted, bounded, and cheap.
8. No built-in watchdog covers hangs such as event-loop stalls, long-running headless requests, stuck tool calls, blocked persistence, or thread-executor startup failures.
9. Logging remains mostly free-form text, so it is useful for humans but weak for automated support and trend analysis.

## Design Goals

- Local only. No network metrics sink, external telemetry service, database server, or new runtime dependency.
- Additive. Do not change the current headless protocol version unless a future release needs a breaking change.
- Redacted by default. Do not store prompt text, assistant text, tool output, secrets, API keys, tokens, or large paths unless explicitly requested for a local debug bundle.
- Correlated. Every important operation should carry `run_id`, and where applicable `session_id`, `request_id`, `turn_id`, `api_call_id`, `tool_use_id`, and `task_id`.
- Cheap. Metrics must not materially affect per-line latency. The hard target for the first release is less than 0.5 ms added per submitted line with diagnostics enabled versus disabled; diagnostics must remain best-effort if the writer falls behind.
- Bounded. Retention and file sizes must be capped.
- Inspectable. A human can read the JSONL, and the CLI/headless/MCP surfaces can summarize it without special tooling.
- Release-useful. A consumer can attach one exported bundle to an issue and give us enough evidence to diagnose most local integration failures.

## Non-Goals

- No Prometheus/OpenTelemetry dependency in the first implementation.
- No remote phone-home telemetry.
- No hosted dashboards.
- No collection of prompt bodies, tool output bodies, raw provider responses, credentials, or private memory content.
- No multi-tenant observability model in this proposal. Multi-tenancy should define tenant/workspace identifiers later and can reuse this schema.

## Proposed Architecture

### 1. Core Diagnostic Event Schema

Add a small local recorder, for example `src/openharness/diagnostics/recorder.py`, based only on the standard library.

The recorder hot path must not depend on asyncio or the default thread executor. The v0.1.17 search hang showed that executor handoff itself can be the failure point in constrained environments. Emitting a diagnostic event should therefore be a plain synchronous append to a bounded `collections.deque`, protected by a small lock. A plain daemon writer thread drains the deque to JSONL files. The recorder must never call `asyncio.to_thread()` or `loop.run_in_executor()` from the event hot path, and the daemon writer must not keep process teardown alive.

Every event is a compact JSON object:

```json
{
  "schema_version": 1,
  "ts": "2026-06-11T03:22:14.123Z",
  "monotonic_ms": 81234.45,
  "run_id": "20260611-032214-4f2a9c",
  "pid": 12345,
  "component": "headless",
  "operation": "submit",
  "event": "completed",
  "level": "info",
  "status": "ok",
  "duration_ms": 1842.6,
  "session_id": "abc123",
  "request_id": "submit-1",
  "turn_id": "turn-0004",
  "api_call_id": null,
  "tool_use_id": null,
  "task_id": null,
  "attrs": {
    "mode": "headless",
    "model": "local-profile-model",
    "message_count": 12
  },
  "counters": {
    "input_tokens": 1024,
    "output_tokens": 220
  },
  "error": null
}
```

Rules:

- `component` is a bounded enum: `startup`, `headless`, `print`, `ui`, `mcp`, `engine`, `api`, `tool`, `permission`, `storage`, `index`, `memory`, `skill`, `cron`, `autopilot`, `task`, `swarm`, `channel`, `gateway`, `diagnostics`.
- `operation` is a stable low-cardinality name such as `submit`, `model_stream`, `tool_execute`, `snapshot_save`, `index_search`, `mcp_tool_call`.
- `event` is one of `started`, `completed`, `failed`, `cancelled`, `timeout`, `retry`, `fallback`, `denied`, `dropped`, `heartbeat`, `snapshot`.
- `attrs` must be allowlisted by component. Free-form raw payloads are not allowed.
- `error` contains only `{ "type": "...", "reason": "...", "message_preview": "...", "status_code": 429 }`; message previews are redacted and capped.

### 2. Local File Layout

Store diagnostics under `get_data_dir()`:

```text
diagnostics/
  current-run.json
  events/
    2026-06-11.jsonl
  summaries/
    2026-06-11.json
  exports/
    openharness-diagnostics-20260611-032214.tar.gz
```

`current-run.json` contains process metadata and active settings safe for support:

- OpenHarness version.
- Python version and platform.
- command mode: `interactive`, `print`, `headless`, `mcp`, `ohmo-gateway`, `task-worker`, `cron`.
- `run_id`, `pid`, start time, cwd hash, data dir path, config dir path.
- selected provider/profile labels and model names, without credentials.
- feature flags relevant to diagnosis: prompt caching, conversation index enabled, memory enabled, MCP enabled, channel names enabled.

### 3. Retention And Bounds

Default policy:

- Metadata-only diagnostics enabled by default.
- Retain event files for 14 days.
- Cap each daily event file at 25 MB by default.
- Cap the in-memory recorder queue at 10,000 events.
- If the queue is full, drop low-priority `started`/`heartbeat` events first and increment `diagnostics.events_dropped`.
- Never fsync every event. Flush periodically and on graceful shutdown.
- If writing fails, disable the recorder for that process and emit one stderr warning.

Settings follow the existing grouped-settings style:

```json
{
  "diagnostics": {
    "enabled": true,
    "event_log_enabled": true,
    "retention_days": 14,
    "max_daily_mb": 25,
    "include_paths": "safe",
    "export_include_logs": true,
    "heartbeat_enabled": true,
    "slow_thresholds": {}
  }
}
```

Path policy:

- `safe`: store project-relative paths where possible and absolute path hashes otherwise.
- `exact`: local debug mode only.
- `hash`: store only path hashes and basenames.

### 4. Required Metrics Inventory

#### Runtime And Startup

| Metric | Type | Labels | Why it matters |
|---|---|---|---|
| `runtime.start` | event | mode, version, python, platform | Confirms what actually ran. |
| `runtime.ready_duration_ms` | duration | mode | Finds slow startup regressions. |
| `runtime.import_probe_ms` | duration | probe | Tracks command/tool registry import drift. |
| `runtime.shutdown_duration_ms` | duration | mode, status | Finds hangs during teardown. |
| `runtime.event_loop_lag_ms` | gauge | mode | Diagnoses event-loop stalls. |
| `runtime.thread_probe_ms` | duration/status | mode | Detects environments where thread handoff is unsafe. |
| `runtime.rss_mb` | gauge | mode | Detects memory growth without adding `psutil`. Use `resource` where available. |
| `runtime.open_file_count` | gauge | mode | Optional Linux-only health signal from `/proc/self/fd`. |

#### Headless And Print Requests

| Metric | Type | Labels | Why it matters |
|---|---|---|---|
| `headless.request_started` | counter/event | request_type | Volume and correlation. |
| `headless.request_duration_ms` | duration | request_type, status | Identifies slow submit, resume, search, status, shutdown. |
| `headless.queue_depth` | gauge | request_type | Shows backpressure and stuck work. |
| `headless.response_latency_ms` | duration | event_type | Measures protocol responsiveness. |
| `headless.parse_error_count` | counter | reason | Diagnoses client/protocol mismatch. |
| `headless.interrupt_count` | counter | active | Shows forced cancellation behavior. |
| `print.result_duration_ms` | duration | status | Supports one-shot integration debugging. |

#### Model/API Calls

| Metric | Type | Labels | Why it matters |
|---|---|---|---|
| `api.call_started` | event | provider, api_format, model | Correlates a turn to upstream work. |
| `api.time_to_first_token_ms` | duration | provider, model | Separates model latency from local work. |
| `api.call_duration_ms` | duration | provider, model, status | Main latency indicator. |
| `api.input_tokens` | counter | provider, model | Cost and prompt growth. |
| `api.output_tokens` | counter | provider, model | Cost and output behavior. |
| `api.cache_creation_input_tokens` | counter | provider, model | Prompt-cache setup cost. |
| `api.cache_read_input_tokens` | counter | provider, model | Confirms prompt-cache effectiveness. |
| `api.retry_count` | counter | reason, status_code | Reliability signal. |
| `api.fallback_count` | counter | from_model, to_model, reason | Recovery behavior. |
| `api.credential_rotation_count` | counter | provider, reason | Credential health. |
| `api.error_count` | counter | reason, status_code, retryable | Failure classification. |
| `api.request_message_count` | gauge | provider, model | Context growth. |
| `api.tool_schema_count` | gauge | provider, model | Tool registry size regression. |
| `api.max_tokens_effective` | gauge | provider, model | Diagnoses max-token clamp/failures. |

#### Engine And Turn Loop

| Metric | Type | Labels | Why it matters |
|---|---|---|---|
| `turn.started` | event | mode | Correlation root for one prompt. |
| `turn.duration_ms` | duration | status | End-to-end local plus model latency. |
| `turn.model_turn_count` | counter/gauge | status | Detects loops and max-turn exhaustion. |
| `turn.assistant_empty_count` | counter | model | Provider behavior. |
| `turn.max_turns_exceeded_count` | counter | model | Agent loop runaway signal. |
| `turn.error_count` | counter | reason | Top-level user-visible failures. |

#### Tools And Permissions

| Metric | Type | Labels | Why it matters |
|---|---|---|---|
| `tool.started` | event | tool_name | Correlation to model tool calls. |
| `tool.duration_ms` | duration | tool_name, status | Finds slow tools. |
| `tool.error_count` | counter | tool_name, reason | Finds brittle tools. |
| `tool.output_chars` | gauge | tool_name, offloaded | Diagnoses truncation/artifact behavior. |
| `tool.artifact_offload_count` | counter | tool_name | Shows large-output paths. |
| `tool.validation_error_count` | counter | tool_name | Detects model/tool schema mismatch. |
| `permission.denied_count` | counter | tool_name, mode, reason | Explains why work did not proceed. |
| `permission.prompt_count` | counter | tool_name, mode | Interactive friction signal. |
| `permission.prompt_duration_ms` | duration | tool_name | Finds operator latency in interactive mode. |

#### Session Storage And Conversation Index

| Metric | Type | Labels | Why it matters |
|---|---|---|---|
| `session.snapshot_write_duration_ms` | duration | app, status | Diagnoses save/resume slowness. |
| `session.snapshot_size_bytes` | gauge | app | Detects runaway sessions. |
| `session.message_count` | gauge | app | Context/session growth. |
| `session.index_update_duration_ms` | duration | status | Index write overhead. |
| `index.search_duration_ms` | duration | mode, status, fts_enabled | Explains headless search behavior. |
| `index.search_hits` | gauge | mode | Search usefulness. |
| `index.busy_retry_count` | counter | operation | SQLite lock pressure. |
| `index.rebuild_count` | counter | reason | Corruption/schema recovery. |
| `index.fts_disabled_count` | counter | reason | Environment capability issue. |
| `index.db_size_bytes` | gauge | fts_enabled | Capacity planning. |

#### Compaction And Memory

| Metric | Type | Labels | Why it matters |
|---|---|---|---|
| `compact.started` | event | trigger | Shows why compaction ran. |
| `compact.duration_ms` | duration | trigger, status | Finds slow memory operations. |
| `compact.before_message_count` | gauge | trigger | Context pressure. |
| `compact.after_message_count` | gauge | trigger | Effectiveness. |
| `compact.retry_count` | counter | trigger | Provider instability. |
| `memory.extract_started` | event | mode | Background memory work correlation. |
| `memory.extract_duration_ms` | duration | status | Background latency/failures. |
| `memory.session_checkpoint_duration_ms` | duration | status | Diagnoses checkpoint writes. |

#### MCP Server

| Metric | Type | Labels | Why it matters |
|---|---|---|---|
| `mcp.server_start_duration_ms` | duration | status | Host integration readiness. |
| `mcp.tool_call_duration_ms` | duration | tool_name, status | Diagnoses MCP consumer calls. |
| `mcp.tool_error_count` | counter | tool_name, reason | Shows MCP-specific issues. |
| `mcp.protocol_error_count` | counter | reason | Host/client compatibility. |

#### Channels And Ohmo Gateway

| Metric | Type | Labels | Why it matters |
|---|---|---|---|
| `channel.start_duration_ms` | duration | channel, status | Startup readiness. |
| `channel.inbound_count` | counter | channel, message_type | Message flow. |
| `channel.outbound_count` | counter | channel, status | Delivery health. |
| `channel.media_download_duration_ms` | duration | channel, media_type, status | Diagnoses attachment handling. |
| `channel.policy_decision_count` | counter | channel, decision | Explains ignored messages. |
| `channel.error_count` | counter | channel, reason | Channel reliability. |
| `gateway.session_count` | gauge | status | Gateway capacity. |
| `gateway.session_duration_ms` | duration | status | Runtime behavior. |
| `gateway.queue_depth` | gauge | queue | Backpressure. |

#### Tasks, Swarm, Cron, Autopilot

| Metric | Type | Labels | Why it matters |
|---|---|---|---|
| `task.spawn_duration_ms` | duration | backend, status | Agent startup regression. |
| `task.write_duration_ms` | duration | backend, status | Follow-up latency. |
| `task.output_tail_duration_ms` | duration | status | Large log behavior. |
| `swarm.agent_count` | gauge | backend, status | Coordination health. |
| `cron.job_duration_ms` | duration | job_name, status | Scheduler reliability. |
| `cron.job_exit_code` | gauge | job_name | Failure details. |
| `autopilot.card_count` | gauge | status | Queue health. |
| `autopilot.run_duration_ms` | duration | status | Background automation behavior. |

#### Diagnostics System

| Metric | Type | Labels | Why it matters |
|---|---|---|---|
| `diagnostics.events_written` | counter | file | Confirms recorder works. |
| `diagnostics.events_dropped` | counter | reason | Shows overload. |
| `diagnostics.flush_duration_ms` | duration | status | Recorder overhead. |
| `diagnostics.export_duration_ms` | duration | status | Bundle creation reliability. |
| `diagnostics.redaction_count` | counter | rule | Verifies sensitive data removal. |

### 5. Instrumentation Points

| File/area | Add instrumentation |
|---|---|
| `src/openharness/cli.py` | process start, mode, debug setting, startup validation, diagnostics commands. |
| `src/openharness/ui/app.py` | headless request lifecycle, queue depth, status/list/search response latency, shutdown, interrupt, parse errors. |
| `src/openharness/engine/query_engine.py` | turn lifecycle, cumulative usage, session memory checkpoint duration. |
| `src/openharness/engine/query.py` | model call span, time to first token, retry/fallback/rotation counters, tool validation/permission/execution spans, compaction spans. |
| `src/openharness/api/client.py` and OpenAI-compatible clients | request metadata, duration, usage extraction, status/error details. |
| `src/openharness/api/resilient_client.py` | retry, credential rotation, fallback, recovery ceiling events. |
| `src/openharness/services/session_storage.py` and `ohmo/session_storage.py` | snapshot size, write duration, index update duration. |
| `src/openharness/services/conversation_index.py` | search/read/browse/around duration, update duration, busy retries, rebuilds, FTS fallback. |
| `src/openharness/mcp/serve.py` | server start, tool call duration, tool errors. |
| `src/openharness/services/cron.py` and `cron_scheduler.py` | job start/finish, exit code, duration. |
| `src/openharness/tasks/manager.py` | spawn, restart, stdin write, output tail, completion. |
| `src/openharness/swarm/*` | agent spawn/send/shutdown and permission sync latency. |
| `src/openharness/channels/impl/*` | channel start/stop, inbound/outbound, media download, policy decisions, errors. |
| `ohmo/gateway/*` | gateway process, session lifecycle, queue depth, outbound notifications. |

### 6. User-Facing Surfaces

#### CLI

Add a diagnostics command group:

```text
oh diagnostics status
oh diagnostics tail --component api --limit 50
oh diagnostics summary --since 1h
oh diagnostics export --since 24h --output /tmp/openharness-diagnostics.tar.gz
oh diagnostics purge --older-than 14d
```

`oh diagnostics status --json` should include:

- current run metadata.
- enabled settings.
- recent error counts by component.
- current queue depths.
- index health.
- auth/profile summary without secrets.
- latest run durations and token counters.
- event recorder health and dropped-event count.

`oh diagnostics status --json` is the canonical v1 diagnostics surface. A separate `doctor` CLI alias can be considered later, but there is no `oh doctor` command today; only the interactive slash command registry has a `/doctor` command.

#### Headless JSONL

Add an additive request:

```json
{"type":"diagnostics","request_id":"diag-1","correlation_id":"consumer-run-42","scope":"summary"}
```

Response:

```json
{
  "type": "diagnostics_snapshot",
  "request_id": "diag-1",
  "run_id": "20260611-032214-4f2a9c",
  "summary": {},
  "recent_errors": [],
  "recorder": {"enabled": true, "events_dropped": 0}
}
```

This keeps the existing protocol version intact because it is an additive request/event pair.

All headless requests may optionally carry `correlation_id`. It is echoed into diagnostics only, not used for protocol routing, so external integrators can attach their own run or job identifier without overloading `request_id`.

#### MCP

Add a read-only `diagnostics_status` tool mirroring the headless summary. Do not expose raw event logs over MCP in the first implementation; exports should stay explicit through the CLI.

#### Diagnostic Bundle

`oh diagnostics export` should produce:

```text
manifest.json
current-run.json
events/*.jsonl
summaries/*.json
status.json
release-info.json
redaction-report.json
```

Do not include session snapshots, memory files, tool artifacts, prompts, assistant text, tool output, API keys, auth tokens, or full environment dumps by default.

## Redaction Policy

Use the existing secret redaction rules where possible and add diagnostics-specific allowlists.

Default stored fields:

- IDs: `run_id`, `session_id`, `request_id`, `turn_id`, `tool_use_id`, `task_id`.
- Low-cardinality names: component, operation, event, status, tool name, provider label, model name, channel name.
- Counts and durations.
- Error type, classifier reason, status code, short redacted message preview.
- Path policy output according to `diagnostics.include_paths`.

Default excluded fields:

- Prompt text.
- Assistant text.
- Tool output.
- Tool input values, except allowlisted shape metadata such as `path_policy`, `command_kind`, `arg_count`, `input_size_chars`, never full command text by default.
- API keys, auth tokens, OAuth session values, headers, cookies.
- Full environment variables.
- Memory file content.
- Session snapshot content.
- Channel message content and attachment bytes.

## Hang And Slow-Operation Diagnostics

Add a lightweight watchdog:

- Track all in-flight operations with start time and correlation IDs.
- Every five seconds emit a `diagnostics.heartbeat` summary with active operation ages in long-lived modes only: headless, MCP, gateway, TUI, and task-worker. Do not run the heartbeat in one-shot `-p`/print mode.
- Run the watchdog as a daemon task/thread that cannot keep teardown alive.
- If a headless request, model call, tool call, index operation, snapshot write, or channel operation exceeds its configured slow threshold, emit a `slow_operation` event.
- If an operation exceeds a hard diagnostic threshold, capture a standard-library stack snapshot with `faulthandler` or `sys._current_frames()` into a redacted local file referenced by the event.
- Add a `runtime.thread_probe` at startup and in `oh diagnostics status`, because recent release work showed that thread/executor handoff can fail in constrained environments. The probe is a raw daemon-thread round-trip bounded at 2 seconds, recording `ok`, `failed`, or `timeout` plus duration. It must never use `asyncio.to_thread()` or `loop.run_in_executor()`: the default executor's workers are non-daemon, so in exactly the broken environments this probe detects, a stuck worker hangs `asyncio.run()` teardown in `shutdown_default_executor()` even after a `wait_for` timeout. The probe is diagnostic only; the recorder must not depend on it, and process teardown must never await it.

Suggested default slow thresholds:

| Operation | Slow threshold |
|---|---:|
| Headless status/list/search | 1 s |
| Headless submit | 30 s without output event |
| Model time to first token | 20 s |
| Tool call | 30 s |
| Session snapshot write | 2 s |
| Index search | 500 ms |
| Index update | 2 s |
| MCP tool call | 5 s |
| Channel media download | 30 s |
| Cron job | configured job timeout or 60 s |

## Rollout Plan

### Phase 1 - Schema And Recorder

- Add `openharness.diagnostics` package with:
  - event dataclass/model.
  - redaction helpers.
  - recorder singleton.
  - context helpers for `run_id`, `request_id`, `turn_id`.
  - synchronous bounded-deque append hot path.
  - daemon-thread JSONL writer and daily rotation.
- Add settings with conservative defaults.
- Add unit tests for schema stability, redaction, retention, queue overflow, and disabled mode.
- Add the bounded daemon-thread probe, but keep it out of the recorder write path.

### Phase 2 - Critical Path Instrumentation

Instrument the release-critical local/headless path:

- CLI process start and mode.
- Headless request lifecycle.
- QueryEngine turn lifecycle.
- Model call duration, time to first token, retry/fallback/rotation.
- Tool execution and permission decisions.
- Session snapshot writes.
- Conversation index search/update.

Acceptance target: a headless smoke run emits a complete diagnostic timeline from process start to shutdown without prompt/tool-output leakage.

Diagnostics for user-facing stream events such as retry, fallback, credential rotation, and compaction must be emitted at the same call sites as the stream events. Do not derive diagnostics later by reading the stream; that creates double-emission drift. Pin this with tests that force a fallback and assert both the user-facing event and diagnostic event are emitted.

### Phase 3 - Diagnostic Surfaces

- Add `oh diagnostics status|tail|summary|export|purge`.
- Add headless `diagnostics` request.
- Add MCP `diagnostics_status`.
- Extend release smoke checks to validate redaction and expected metrics.
- Add the long-lived-mode watchdog and `slow_operation` events.
- Add the cheap Phase 5 gates now: expected event types in smoke, redaction test, overflow test, and diagnostics-on/off latency budget test.

### Phase 4 - Background And Integration Surfaces

- Instrument channels, ohmo gateway, cron, tasks, swarm, memory extraction, compaction, and autopilot.
- Add integration-specific summaries for local consumers: headless, MCP, channel, gateway, cron.
- Add a release report template that includes diagnostics summary from the latest verification run.

Phase 4 should land after the WS4/WS5 performance work that rewrites persistence and per-channel dispatch paths. Instrumenting channel/gateway/cron/task files before those rewrites would double the work and increase merge risk. The rewritten subsystems should include diagnostics as part of their final shape.

### Phase 5 - Full Release Gates

Add release checks:

- `oh diagnostics status --json` succeeds in a clean local environment.
- Headless smoke emits expected diagnostic event types.
- Export bundle contains required manifest files.
- Export bundle does not contain known test secrets, prompt text, tool output, or forbidden attribution strings.
- Per-line latency budget remains within the existing performance target.
- Recorder queue overflow test proves graceful event dropping.

The full Phase 5 gate set follows Phase 4. The Phase 1-3 release carries only the cheap gate subset listed in Phase 3.

## Acceptance Criteria

1. A local headless run with one prompt, one tool call, and shutdown produces correlated `runtime`, `headless`, `turn`, `api`, `tool`, `session`, and `diagnostics` events with one shared `run_id`.
2. `line_complete.usage` still works as today, and the diagnostic summary separately reports cumulative token/cache counters.
3. A forced API retry/fallback test increments retry/fallback counters and records the classifier reason without raw response bodies.
4. A permission denial records tool name, mode, and reason category without tool input content.
5. A conversation search records search mode, duration, hit count, FTS state, and error category if it fails.
6. `oh diagnostics export` produces a bounded archive with manifest, summary, event logs, and redaction report.
7. The export bundle redaction test fails if it contains a fake API key, auth token, prompt body, assistant body, tool output body, or generated-attribution string.
8. Recorder disabled mode performs no writes and does not change user-visible behavior.
9. Queue overflow drops low-priority diagnostics events, increments `diagnostics.events_dropped`, and does not block the runtime.
10. The release smoke suite can run `oh diagnostics status --json` and validate the expected schema.

## Test Plan

- Unit tests for event schema, redaction, path policy, retention, queue overflow, and disabled mode.
- Headless integration test that submits a prompt through a fake API client and verifies correlated metrics.
- Tool execution test that verifies duration/error/output-size fields without output bodies.
- Session/index tests that verify snapshot/index duration metrics and error isolation.
- Recovery tests that verify retry/fallback/credential rotation metrics.
- Forced-fallback double-emission test that verifies the user-facing stream event and diagnostic event are emitted from the same call site.
- Diagnostic export test with seeded fake secrets and fake prompt/tool content.
- Performance test extending `scripts/measure_per_line.py` to compare diagnostics enabled vs disabled, with a hard budget of less than 0.5 ms added per submitted line.

## Acceptance Traceability

| Acceptance criterion | Named test |
|---|---|
| 1. Correlated headless timeline | `test_diagnostics_headless_timeline` |
| 2. Usage parity | `test_diagnostics_usage_matches_line_complete` |
| 3. Retry/fallback/rotation metrics | `test_diagnostics_forced_fallback_double_emission` |
| 4. Permission denial metrics | `test_diagnostics_permission_denial_redacted` |
| 5. Conversation search metrics | `test_diagnostics_session_search_metrics` |
| 6. Export bundle structure | `test_diagnostics_export_bundle_manifest` |
| 7. Export bundle redaction | `test_diagnostics_export_redacts_seeded_secrets` |
| 8. Disabled mode | `test_diagnostics_disabled_mode_no_writes` |
| 9. Queue overflow behavior | `test_diagnostics_overflow_drops_low_priority_events` |
| 10. Status schema | `test_diagnostics_status_json_schema` |

## Risks And Mitigations

| Risk | Mitigation |
|---|---|
| Diagnostics slow down the runtime | Bounded queue, periodic flush, no fsync per event, low-cardinality attrs, overhead budget test. |
| Export leaks private data | Strict allowlists, existing secret redaction, export tests with seeded secrets, no raw prompts/tool outputs. |
| Too many events make support harder | Daily summaries, `diagnostics summary`, component filters, clear event names. |
| Metrics become another unreliable subsystem | Best-effort recorder, self-health counters, disable-on-write-failure behavior. |
| Users expect production observability integrations | Document local-only scope; optional external sinks can be a later proposal. |
| Multi-tenancy needs different dimensions | Keep `tenant_id` out of v1; reserve schema field for future multi-tenancy design. |

## Current Recommendations For Open Questions

1. Use `safe` path handling by default for every user. Exact paths require an explicit local debug opt-in.
2. Enable metadata-only diagnostics by default in the first release for headless, MCP, print, TUI, and task-worker modes. Heartbeats run only in long-lived modes, never in print one-shots.
3. Keep 14 days as the default retention period.
4. Allow optional external `correlation_id` on headless requests, separate from `request_id`, and store it only in diagnostics.
5. Capture redacted stack snapshots on hard thresholds, store them as referenced local files, and include them in exports only when explicitly requested with an include-stacks option.

## Recommended Initial Scope

Implement phases 1 through 3 before the next broad consumer release. That gives the integration team a complete local diagnostic path for the surfaces they are most likely to use now: headless JSONL, MCP status, session search, model calls, tools, permissions, snapshots, and the conversation index.

Channels, gateway, cron, autopilot, and deeper memory metrics should follow immediately after, but they do not need to block local/headless consumer integration if the release notes clearly state the covered surfaces.
