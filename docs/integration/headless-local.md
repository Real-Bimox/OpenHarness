# OpenHarness Local Headless Integration

This guide is the supported local integration contract for driving OpenHarness without a TUI, browser, HTTP server, or remote control service.

OpenHarness exposes two local headless surfaces:

- `oh -p "..." --output-format json|stream-json` for one-shot prompt execution.
- `oh --headless` for a long-lived JSONL stdin/stdout control process.

Use `--bare` for the smallest local integration surface. Bare mode skips hooks, plugins, MCP, network/image tools, and project-memory auto-discovery.

## Recommended Invocation

For a local orchestrator, start one process per active session:

```bash
oh --headless --bare --cwd /path/to/workspace
```

For a one-shot request:

```bash
oh -p "Summarize this repository" --bare --cwd /path/to/workspace --output-format json
```

`--headless` always emits JSONL and cannot be combined with `-p`, `--continue`, `--resume`, `--task-worker`, `--backend-only`, `--dry-run`, or `--output-format`.

## Headless JSONL Protocol

`oh --headless` reads one JSON object per stdin line and writes one JSON object per stdout line.

The first event is always:

```json
{"type":"process_ready","protocol_version":1}
```

Requests may include `request_id`; responses related to that request echo it back. `id` is accepted as an alias for `request_id`.

### Submit

```json
{"type":"submit","prompt":"Inspect the repo and report the test command","request_id":"submit-1"}
```

Accepted aliases:

- `submit_line` is accepted as an alias for `submit`.
- `line` or `text` are accepted as aliases for `prompt`.

Typical response sequence:

```json
{"type":"ready","protocol_version":1,"session_id":"abc123","request_id":"submit-1"}
{"type":"assistant_delta","session_id":"abc123","request_id":"submit-1","text":"..."}
{"type":"assistant_complete","session_id":"abc123","request_id":"submit-1","text":"...","usage":{}}
{"type":"line_complete","session_id":"abc123","request_id":"submit-1","usage":{}}
```

The first `submit` lazily starts a runtime and emits `ready`. Later `submit` requests reuse the same live session.

### Session Guard

A `submit` may include `session_id` as a guard:

```json
{"type":"submit","session_id":"abc123","prompt":"Continue","request_id":"submit-2"}
```

If the active session differs, OpenHarness emits an `error` event instead of running the prompt.

### Resume

Resume a known session:

```json
{"type":"resume","session_id":"abc123","prompt":"Continue from there","request_id":"resume-1"}
```

`resume` requires a non-empty `session_id`.

Successful resume emits:

```json
{"type":"ready","protocol_version":1,"session_id":"abc123","request_id":"resume-1","resumed":true}
```

If `prompt` is present, the resumed process immediately runs that prompt.

### Continue Latest

Continue the latest saved session for the working directory:

```json
{"type":"continue","prompt":"Continue the last task","request_id":"continue-1"}
```

If no previous session exists, OpenHarness emits a recoverable `error`.

### List Sessions

```json
{"type":"list_sessions","request_id":"list-1"}
```

Response:

```json
{"type":"sessions","request_id":"list-1","sessions":[{"session_id":"abc123","summary":"...","message_count":2,"model":"...","created_at":1710000000.0}]}
```

`list_sessions` is answered immediately, even while another request is active.

### Status

```json
{"type":"status","request_id":"status-1"}
```

Response:

```json
{"type":"state_snapshot","protocol_version":1,"session_id":"abc123","request_id":"status-1","state":{},"busy":false,"usage":{}}
```

Before a runtime starts, `session_id` and `state` are `null`. `status` is answered immediately, even while another request is active.

### Interrupt

```json
{"type":"interrupt","request_id":"interrupt-1"}
```

If a turn is active:

```json
{"type":"interrupting","active":true,"active_request_id":"submit-1","request_id":"interrupt-1"}
{"type":"interrupted","session_id":"abc123","request_id":"submit-1"}
{"type":"line_complete","session_id":"abc123","request_id":"submit-1","usage":{}}
```

If nothing is active:

```json
{"type":"interrupted","active":false,"request_id":"interrupt-1"}
```

Interrupted sessions are persisted before the `interrupted` event is emitted, so a later `resume` can restore the interrupted exchange.

### Shutdown

Graceful shutdown:

```json
{"type":"shutdown","request_id":"shutdown-1"}
```

Response:

```json
{"type":"shutdown","session_id":"abc123","request_id":"shutdown-1"}
```

Graceful shutdown lets the active turn and queued requests ahead of shutdown finish. Requests queued behind shutdown receive an explicit non-recoverable `error`.

Forced shutdown:

```json
{"type":"shutdown","force":true,"request_id":"shutdown-1"}
```

Forced shutdown cancels active work and rejects queued requests.

Closing stdin is equivalent to graceful shutdown.

## Permissions

Headless mode is deterministic. It does not stop to ask an operator for interactive approval.

- `--permission-mode default` and `--permission-mode plan` deny confirmation prompts.
- `--permission-mode full_auto` approves confirmation prompts.
- `--allowed-tools` and `--disallowed-tools` are still enforced.
- Explicit deny wins over broader approval.

Denied tool asks emit:

```json
{"type":"permission_denied","session_id":"abc123","request_id":"submit-1","tool_name":"bash","reason":"..."}
```

`permission_response` is reserved and currently rejected with a recoverable `error`.

## Errors

Recoverable errors do not stop the process:

```json
{"type":"error","request_id":"bad-1","message":"submit requires a non-empty prompt or line","recoverable":true}
```

Common recoverable errors:

- invalid JSON
- unsupported request shape
- missing prompt
- missing or unknown session ID
- `session_id` guard mismatch
- unsupported `permission_response`

Non-recoverable errors are used when the process is shutting down.

## One-Shot Print Mode

JSON output:

```bash
oh -p "Inspect this repo" --bare --cwd /path/to/workspace --output-format json
```

Result shape:

```json
{"type":"result","session_id":"abc123","text":"...","is_error":false,"errors":[],"permission_denials":[],"system_messages":[],"usage":{}}
```

Stream JSON output:

```bash
oh -p "Inspect this repo" --bare --cwd /path/to/workspace --output-format stream-json
```

Each event includes `session_id`; the final event is `line_complete`.

Resume one-shot mode:

```bash
oh -p "Continue" --resume abc123 --bare --cwd /path/to/workspace --output-format json
```

`--resume` without a value is interactive and is not appropriate for local automation.

## Example Client

Run a non-model protocol check:

```bash
python examples/headless_jsonl_client.py --cwd /path/to/workspace
```

Run a prompt:

```bash
python examples/headless_jsonl_client.py --cwd /path/to/workspace --prompt "Summarize this repository"
```

By default the example client uses temporary OpenHarness config/data directories for non-resume examples. Use `--state-dir /path/to/state` when you want sessions to persist across example runs, or `--use-existing-state` when you want to use the normal OpenHarness state locations.

Run the release smoke check:

```bash
python scripts/smoke_headless_local.py
```

The smoke check validates `process_ready`, `status`, `list_sessions`, and `shutdown` without submitting a model prompt.

## Operational Notes

- The process hosts one live session at a time.
- Use one `oh --headless` process per concurrent session.
- Use `request_id` on every request; it makes logs and orchestration much easier to correlate.
- Treat unknown event fields as forward-compatible additions.
- Treat unknown event types as non-fatal unless your workflow requires strict handling.
- Keep stdin/stdout reserved for JSONL. Send process diagnostics to stderr in wrapper code.
