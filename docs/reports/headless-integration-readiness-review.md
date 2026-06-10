# Headless Integration Readiness Review

Date: 2026-06-09

Branch: `proposal/headless-local-control-api`

## Scope

This review checked the local-only headless surfaces before integration work:

- `oh --headless` JSONL stdin/stdout control mode.
- `oh -p/--print` machine-readable output and resume behavior.
- Shared runtime assembly through `build_runtime()`.
- CLI startup controls that a local integration is likely to rely on.

No internet-facing API, HTTP server, WebSocket server, or new runtime dependency is part of this scope.

## Findings And Resolution

### Headless cancellation was missing

Issue: `oh --headless` handled one request at a time by awaiting `handle_line()` inline. A later `interrupt` or `shutdown` request could not be processed until the active model/tool turn completed.

Resolution: the headless loop now uses a persistent reader task and an active request task. `interrupt` cancels active work and emits `interrupted` plus `line_complete`; `shutdown` cancels active work before closing.

### Headless startup emitted throwaway sessions

Issue: `oh --headless` eagerly started a runtime and then closed/reopened it when the first request was `resume` or `continue`, causing duplicate startup side effects and two `ready` events.

Resolution: startup is now lazy. The process emits `process_ready` first, and a runtime emits `ready` only when a session is created or restored.

### Runtime control flags were advertised but not wired

Issue: `--allowed-tools`, `--disallowed-tools`, `--settings`, `--mcp-config`, and `--bare` were defined by the CLI but did not affect the main runtime path.

Resolution:

- `--allowed-tools` and `--disallowed-tools` now update the per-process permission settings.
- `--settings` now loads a process-local settings file or inline JSON object.
- `--mcp-config` now loads process-local MCP config from JSON files or inline JSON.
- `--bare` now skips plugins, MCP config loading, configured hooks, and project-memory auto-discovery.

When `--bare` and `--mcp-config` are both supplied, `--bare` wins and MCP remains disabled for that process.

### Append-system-prompt drifted between dry-run and runtime

Issue: dry-run applied `--append-system-prompt`, but runtime startup ignored it.

Resolution: `append_system_prompt` is now handled by the shared settings merge path and survives runtime refreshes.

### Interactive React resume did not receive restored state

Issue: the parent CLI loaded a snapshot for `oh --resume`, but the spawned React backend process did not receive the restored messages or session id.

Resolution: resumed interactive sessions now forward the session id into the backend command. Backend-only resume also preserves the session id in `build_runtime()`.

### Headless protocol lacked typed request validation and discovery APIs

Issue: request parsing was ad hoc and headless had no session discovery or status request.

Resolution:

- Added typed `HeadlessRequest` validation.
- Added protocol version metadata.
- Added `list_sessions` and `status` requests.

## Current Headless Requests

```json
{"type":"submit","prompt":"inspect this repo","request_id":"optional"}
{"type":"resume","session_id":"abc123","prompt":"optional follow-up","request_id":"optional"}
{"type":"continue","session_id":"optional","prompt":"optional follow-up","request_id":"optional"}
{"type":"list_sessions","request_id":"optional"}
{"type":"status","request_id":"optional"}
{"type":"interrupt","request_id":"optional"}
{"type":"shutdown","request_id":"optional"}
```

`permission_response` remains reserved and unsupported in deterministic headless mode.

## Current Headless Events

```json
{"type":"process_ready","protocol_version":1}
{"type":"ready","protocol_version":1,"session_id":"abc123"}
{"type":"sessions","sessions":[]}
{"type":"state_snapshot","protocol_version":1,"session_id":"abc123","state":{},"busy":false}
{"type":"assistant_delta","session_id":"abc123","text":"..."}
{"type":"assistant_complete","session_id":"abc123","text":"..."}
{"type":"tool_started","session_id":"abc123","tool_name":"read_file","tool_input":{}}
{"type":"tool_completed","session_id":"abc123","tool_name":"read_file","output":"...","is_error":false}
{"type":"permission_denied","session_id":"abc123","tool_name":"write_file","reason":"..."}
{"type":"interrupting","request_id":"optional","active":true,"active_request_id":"submit-1"}
{"type":"interrupted","session_id":"abc123"}
{"type":"line_complete","session_id":"abc123"}
{"type":"error","message":"...","recoverable":true}
{"type":"shutdown","session_id":"abc123"}
```

## Remaining Notes

The local development environment used for this work does not currently include pytest or the declared runtime dependencies, so runtime tests could not be executed here. Static compilation and diff whitespace checks remain available locally.
