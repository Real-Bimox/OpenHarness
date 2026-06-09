# Proposal: headless-local-control-api

## Status

| Field | Value |
|---|---|
| Status | APPROVED |
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

Requests:

```json
{"type":"submit","prompt":"inspect this repo","request_id":"optional"}
{"type":"resume","session_id":"abc123","prompt":"optional follow-up","request_id":"optional"}
{"type":"continue","session_id":"optional","prompt":"optional follow-up","request_id":"optional"}
{"type":"shutdown","request_id":"optional"}
```

`permission_response` is intentionally reserved for a later interactive-approval
extension. This implementation keeps headless execution deterministic:
confirmation prompts are denied unless the process was started with
`--permission-mode full_auto`.

Events:

```json
{"type":"ready","session_id":"abc123","request_id":"optional"}
{"type":"assistant_delta","session_id":"abc123","request_id":"optional","text":"..."}
{"type":"assistant_complete","session_id":"abc123","request_id":"optional","text":"..."}
{"type":"tool_started","session_id":"abc123","request_id":"optional","tool_name":"read_file","tool_input":{}}
{"type":"tool_completed","session_id":"abc123","request_id":"optional","tool_name":"read_file","output":"...","is_error":false}
{"type":"permission_denied","session_id":"abc123","request_id":"optional","tool_name":"write_file","reason":"..."}
{"type":"line_complete","session_id":"abc123","request_id":"optional"}
{"type":"error","request_id":"optional","message":"...","recoverable":true}
{"type":"shutdown","session_id":"abc123","request_id":"optional"}
```

This protocol intentionally mirrors the current `stream-json` and React backend event vocabulary so the implementation stays small.

## Acceptance Criteria

1. `oh -p "remember x" --output-format json` returns `session_id`.
2. `oh -p "what did I ask you to remember?" --resume <session_id> --output-format json` runs headlessly and restores context.
3. `oh -p "..." --resume` with no value errors instead of opening a picker.
4. `oh --headless` accepts `submit` requests and emits JSONL events including `ready`, deltas/completion, and `line_complete`.
5. In noninteractive `default` mode, mutating ask-path permissions are denied with `permission_denied`; `full_auto` still approves.
6. No new runtime dependencies are added.
