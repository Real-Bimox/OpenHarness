# Proposal: headless-local-control-api

## Status

| Field | Value |
|---|---|
| Status | IMPLEMENTED |
| Proposal branch | `proposal/headless-local-control-api` |
| Owner | Bahram Boutorabi |
| Created | 2026-06-09 |
| Doc review | owner-approved for local-only implementation |
| Related | [headless-resume](headless-resume.md), [headless-permission-enforcement](headless-permission-enforcement.md) |

## Summary

Provide a local-only headless control API by expanding the existing noninteractive paths instead of adding an HTTP service. The first implementation exposes session-aware JSONL stdin/stdout control, fixes `--print` resume/continue behavior, emits session IDs in machine-readable output, and makes noninteractive permission decisions explicit.

## Motivation

OpenHarness already has most of the runtime pieces needed for local headless operation:

- `--print` runs a single prompt and can emit `json` or `stream-json`.
- `--task-worker` runs a stdin-driven headless worker for managed background agents.
- `--backend-only` runs a structured JSON-lines protocol for the React terminal UI.
- `build_runtime()` and `handle_line()` already share session, command, tool, hook, and memory behavior across TUI and headless paths.

The missing piece is a stable local integration contract. External local orchestrators should not need a TUI, browser, public network service, or new runtime dependencies.

## Scope

In scope:

- `oh -p "..." --resume <id>` and `oh -p "..." --continue` run in print mode, not the TUI.
- `--output-format json` and `stream-json` include `session_id`.
- A public local JSONL control mode, `oh --headless`, accepts structured requests on stdin and emits structured events on stdout.
- `oh --headless` supports local session discovery, status snapshots, and active-turn interruption.
- Noninteractive permissions are policy-driven:
  - `full_auto` approves confirmation prompts.
  - `default` and `plan` deny confirmation prompts with a machine-readable denial event.
  - explicit allow/deny settings remain enforced by the permission checker.
- No new runtime dependencies.

Out of scope:

- REST/HTTP server.
- WebSocket server.
- Browser CORS/auth concerns.
- MCP server mode.
- Full CRUD APIs for plugins, channels, memory, config, tasks, or cron.
- Remote multi-user daemon operation.

## Local JSONL Protocol

`oh --headless` reads one JSON object per stdin line.

Semantics:

- Requests are processed sequentially in FIFO order. Queued `submit` requests
  run one after another; there is no busy rejection.
- `status`, `list_sessions`, and `interrupt` are answered immediately by the
  stdin reader, even while a turn is active.
- The process hosts at most one live session at a time. `resume`/`continue`
  close the current session and restore the requested one. Orchestrators that
  need concurrent sessions should spawn one `oh --headless` process each.
- A `submit` carrying `session_id` is validated against the active session and
  rejected with an `error` event on mismatch (or when no session is active).
- `shutdown` is graceful: the active turn finishes and requests queued ahead of
  the shutdown complete first. Requests queued behind a shutdown (graceful or
  forced) are rejected with an `error` event each, so every `request_id` gets a
  response. `{"type":"shutdown","force":true}` additionally cancels the active
  turn immediately; a force shutdown arriving while a `resume`/`continue`
  rebuild is in flight prevents (or cancels) the follow-up turn.
- Closing stdin (EOF) is equivalent to a graceful `shutdown`.
- An interrupted turn is persisted to the session snapshot before the
  `interrupted` event is emitted, so `resume` keeps the interrupted exchange.
- When resuming, an explicit CLI `--model` wins over the model stored in the
  snapshot.
- `submit_line` is accepted as an alias of `submit`; `id` is accepted as an
  alias of `request_id`; `line`/`text` are accepted as aliases of `prompt`.

Requests:

```json
{"type":"submit","prompt":"inspect this repo","request_id":"optional","session_id":"optional guard"}
{"type":"resume","session_id":"abc123","prompt":"optional follow-up","request_id":"optional"}
{"type":"continue","session_id":"optional","prompt":"optional follow-up","request_id":"optional"}
{"type":"list_sessions","request_id":"optional"}
{"type":"status","request_id":"optional"}
{"type":"interrupt","request_id":"optional"}
{"type":"shutdown","request_id":"optional","force":false}
```

`permission_response` is intentionally reserved for a later interactive-approval
extension. This implementation keeps headless execution deterministic:
confirmation prompts are denied unless the process was started with
`--permission-mode full_auto`.

Events:

```json
{"type":"process_ready","protocol_version":1}
{"type":"ready","protocol_version":1,"session_id":"abc123","request_id":"optional","resumed":true}
{"type":"sessions","request_id":"optional","sessions":[]}
{"type":"state_snapshot","protocol_version":1,"session_id":"abc123","request_id":"optional","state":{},"busy":false,"usage":{}}
{"type":"system","session_id":"abc123","request_id":"optional","message":"..."}
{"type":"clear_transcript","session_id":"abc123","request_id":"optional"}
{"type":"status","session_id":"abc123","request_id":"optional","message":"..."}
{"type":"compact_progress","session_id":"abc123","request_id":"optional","phase":"...","trigger":"...","attempt":1,"message":"..."}
{"type":"assistant_delta","session_id":"abc123","request_id":"optional","text":"..."}
{"type":"assistant_complete","session_id":"abc123","request_id":"optional","text":"...","usage":{}}
{"type":"tool_started","session_id":"abc123","request_id":"optional","tool_name":"read_file","tool_input":{}}
{"type":"tool_completed","session_id":"abc123","request_id":"optional","tool_name":"read_file","output":"...","is_error":false}
{"type":"permission_denied","session_id":"abc123","request_id":"optional","tool_name":"write_file","reason":"..."}
{"type":"interrupting","request_id":"optional","active":true,"active_request_id":"submit-1"}
{"type":"interrupted","session_id":"abc123","request_id":"optional"}
{"type":"interrupted","active":false,"request_id":"optional"}
{"type":"line_complete","session_id":"abc123","request_id":"optional","usage":{}}
{"type":"error","request_id":"optional","message":"...","recoverable":true}
{"type":"shutdown","session_id":"abc123","request_id":"optional"}
```

Notes:

- `ready.resumed` is present (true) only when a session was restored.
- `interrupted` with `"active": false` is the response to an `interrupt`
  request when no turn is running.
- `usage` objects carry cumulative token usage for the session
  (`assistant_complete.usage` is the per-turn snapshot from the provider).
- `error` with a `question` field is emitted when the model calls `ask_user`,
  which is unavailable in headless mode.

This protocol intentionally mirrors the current `stream-json` and React backend event vocabulary so the implementation stays small.

## Print Mode Result Contract

`oh -p --output-format json` emits a single result object:

```json
{"type":"result","session_id":"abc123","text":"...","is_error":false,"errors":[],"permission_denials":[{"tool_name":"bash","reason":"..."}],"system_messages":[],"usage":{}}
```

`oh -p` exits non-zero when any engine error occurred, in every output format.
`system_messages` carries runtime notices that would otherwise go to stderr,
such as the max-turns truncation notice, so json consumers can detect a
truncated run. `stream-json` includes `session_id` on every event and `usage`
on `line_complete`.

## Acceptance Criteria

1. `oh -p "remember x" --output-format json` returns `session_id`.
2. `oh -p "what did I ask you to remember?" --resume <session_id> --output-format json` runs headlessly and restores context.
3. `oh -p "..." --resume` with no value errors instead of opening a picker.
4. `oh --headless` accepts `submit` requests and emits JSONL events including `process_ready`, `ready`, deltas/completion, and `line_complete`.
5. In noninteractive `default` mode, mutating ask-path permissions are denied with `permission_denied`; `full_auto` still approves.
6. No new runtime dependencies are added.
