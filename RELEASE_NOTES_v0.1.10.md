# v0.1.10 — Local Headless Control API

OpenHarness v0.1.10 delivers a local-only headless integration surface so external orchestrators can drive `oh` without a TUI, browser, or network service — plus the reliability and contract fixes needed to depend on it.

## Highlights

- **`oh --headless` JSONL control protocol**
  - One JSON request per stdin line, structured events on stdout: `submit`, `resume`, `continue`, `list_sessions`, `status`, `interrupt`, and `shutdown`.
  - Requests are processed FIFO; `status`, `list_sessions`, and `interrupt` are answered immediately even while a turn is active.
  - `shutdown` is graceful by default — the active turn and requests queued ahead of it complete first, and anything queued behind a shutdown is rejected with an explicit `error` event so every `request_id` gets a response. `{"type":"shutdown","force":true}` cancels the active turn immediately. Closing stdin is equivalent to a graceful shutdown.
  - Interrupted turns are persisted to the session snapshot before `interrupted` is emitted, so `resume` keeps the interrupted exchange.
  - A `submit` carrying a `session_id` is validated against the active session and rejected on mismatch.

- **Machine-readable usage and results**
  - Headless events carry token usage: `assistant_complete.usage` (per-turn), `line_complete.usage` and `state_snapshot.usage` (cumulative).
  - `oh -p --output-format json` results include `is_error`, `errors`, `permission_denials`, `system_messages` (e.g. the max-turns truncation notice), and `usage`.
  - `oh -p` exits non-zero when an engine error occurred, in every output format.

- **Print-mode resume**
  - `oh -p "..." --resume <id>` and `oh -p "..." --continue` run headlessly and restore conversation context across processes.
  - `oh -p --resume` without a session ID errors instead of opening a picker.

- **Hook priorities and edit previews**
  - Hooks support a `priority` field; within an event, hooks run highest-priority first.
  - `edit_file` and `write_file` in the React TUI preview a unified diff before applying changes, with once/session approval and automatic skip in `full_auto` mode.

## Fixes

- `oh --headless` no longer cancels an in-flight turn when a `shutdown` request arrives on stdin, so piping a `submit` + `shutdown` batch returns the full response.
- Headless `resume`/`continue` failures (missing or corrupt snapshot, runtime build errors) emit a recoverable `error` event instead of crashing the control process; the stdin reader survives request-handling exceptions.
- Explicit CLI `--model` wins over the model stored in a session snapshot for `-p --resume/--continue`, headless `resume`/`continue`, and interactive resume.
- Conflicting mode flags error instead of resolving silently: `--headless` with `--task-worker`, `--backend-only`, or `--output-format`, and `--dry-run` with `--headless`.
- `--bare` combined with `--mcp-config` prints a warning that MCP stays disabled; `--bare` help text lists everything it disables.
- `--max-turns` is honored when resuming an interactive session with `--continue`/`--resume`.
- Inline/file `--settings` sources no longer overwrite a user-supplied `profiles` entry or an explicit `active_profile` when synthesizing a profile from flat fields.
- The team-memory secret scanner now catches `ENV_VAR_API_KEY=value` style assignments.
- Codex subscription requests pass reasoning effort separately, enabling `gpt-5.5` with `xhigh` effort.
- Telegram channel delivers replies again under `ohmo init --no-interactive`.

## Protocol Reference

The full request/event vocabulary and semantics are documented in
[docs/proposals/headless-local-control-api.md](docs/proposals/headless-local-control-api.md).

## Testing

- 1185 unit/integration tests pass (24 new tests covering the headless protocol, permission policy, exit codes, and CLI flag handling).
- A 34-check end-to-end exercise drives real `oh` subprocesses over stdin/stdout pipes, including cross-process session resume, graceful vs. forced shutdown, error recovery, and exit-code propagation.
