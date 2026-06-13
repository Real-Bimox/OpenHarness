# Proposal: headless-permission-enforcement — honor permission policy in print mode

## Status

| Field | Value |
|---|---|
| Status | IMPLEMENTED |
| Proposal branch | `proposal/headless-permission-enforcement` |
| Owner | Bahram Boutorabi |
| Created | 2026-06-04 |
| Doc review | pending |
| Related | [headless-resume](headless-resume.md) |

> **Implemented.** The headless ask-path is deny-by-default outside `full_auto` / `--dangerously-skip-permissions`; `--allowed-tools` / `--disallowed-tools` are wired through interactive, print, task-worker, and headless modes (no longer dead flags); denials surface as `permission_denied` events and `denied_tools` / `permission_denials` in machine-readable output. See `_noninteractive_permission` in `src/openharness/ui/app.py` and the flag wiring in `src/openharness/cli.py`. Folded into [headless-local-control-api](headless-local-control-api.md).

## Summary

Two defects make headless runs effectively unrestricted today: (a) print mode auto-approves every permission ask regardless of `--permission-mode`, and (b) the `--allowed-tools` / `--disallowed-tools` flags are parsed but never consumed — they are dead flags in every mode. This proposal makes headless permission behavior deny-by-default on the ask path, wires the allow/deny flags into the permission engine, and emits machine-readable `permission_denied` events so orchestrators can observe and react.

## Motivation

Hands-off orchestration (the immediate driver is AutoDev integration) runs OpenHarness unattended inside a worktree:

```
oh -p "<wake nudge>" --cwd <worktree> --permission-mode full_auto --output-format stream-json
```

An orchestrator's safety model should be able to rely on layered guardrails: its own gates (review gates, branch protections, merge authority) *plus* agent-side enforcement. Today the agent-side layer silently evaporates in headless mode — any tool the model asks for is approved, and the operator's `--disallowed-tools` intent is discarded at the CLI boundary. Unattended operation should be a deliberate grant (`full_auto`, explicit allowlists), never an accident of a no-op callback.

## Current behavior (evidence)

1. **Print mode auto-approves the ask path.** `run_print_mode` wires `_noop_permission`, which returns `True` unconditionally, and `_noop_ask`, which returns `""` (`src/openharness/ui/app.py:204-208`). `--permission-mode` is forwarded to `build_runtime`, but in `default` mode every non-read-only tool that would prompt a human is instead silently approved — `default` behaves as `full_auto`.
2. **`--allowed-tools` / `--disallowed-tools` are dead flags.** They are declared (`src/openharness/cli.py:2272-2280`) and never referenced again anywhere in the dispatch — not passed to `run_repl`, `run_print_mode`, `run_task_worker`, or `build_runtime`. This affects **all** modes, not just print.
3. **The enforcement engine already exists and is sound.** `PermissionChecker.evaluate` (`src/openharness/permissions/checker.py:75-157`) implements, in order: built-in sensitive-path deny that no mode can override (`:86-99`), explicit `denied_tools` (`:101-102`), explicit `allowed_tools` (`:104-106`), path rules, `denied_commands`, `FULL_AUTO` allow-all (`:129-130`), read-only allow (`:132-134`), and otherwise falls through to the interactive ask. `PermissionSettings` already carries `allowed_tools` (`src/openharness/config/settings.py:54`). Nothing reaches it from the CLI.
4. **An explicit bypass flag already exists.** `--dangerously-skip-permissions` (`src/openharness/cli.py:2266-2268`, consumed at `:2386`) is the sanctioned "I accept the risk" switch.

The gap is therefore plumbing and one policy decision — not new machinery.

## Proposed change

### 1. Wire the flags (all modes)

Parse `--allowed-tools` / `--disallowed-tools` (comma- or space-separated, per their help text) and merge them into the effective `PermissionSettings`:

- CLI values are **additive** to `settings.json` values, with CLI taking precedence on conflict.
- Deny stays stronger than allow (checker already evaluates `denied_tools` first, `src/openharness/permissions/checker.py:101-106`).
- The built-in sensitive-path protection remains non-overridable (unchanged).

Applies uniformly to interactive, `-p`, `--task-worker`, and `--backend-only` modes.

### 2. Mode-aware headless ask policy (print mode and task worker)

Replace `_noop_permission` with a policy handler:

| Effective mode | Ask-path outcome |
|---|---|
| `full_auto` or `--dangerously-skip-permissions` | Approve (today's behavior, now opt-in) |
| `default` | **Deny.** The tool call fails with an explicit error the model sees: "permission denied in non-interactive mode; run with `--permission-mode full_auto` or add the tool to `--allowed-tools`" — the turn continues and the model may adapt or finish |
| `plan` | Deny (same message); plan mode's read-only posture is preserved |

`_noop_ask` (the free-text question path) keeps returning `""` — an unattended run has no one to ask; the model is told no answer is available.

### 3. Machine-readable denial events

- `stream-json`: emit `{"type": "permission_denied", "tool_name": ..., "reason": ...}` for each denial.
- `json`: the final result object gains `"denied_tools": [...]` (empty list when nothing was denied).
- `text`: denial reasons go to stderr (stdout stays clean).

Orchestrators can then distinguish "completed unrestricted" from "completed but was refused X" and gate accordingly.

### 4. Exit-code contract (unchanged)

Denials do **not** change the exit code — a denied tool is a normal in-conversation event, and the model's final answer is still the result. Orchestrators that need hard failure on denial can detect `permission_denied` events. (Revisit only if pilot usage shows this is insufficient.)

## Implementation sketch

1. **`src/openharness/cli.py`** — normalize the two flag lists (split on comma/whitespace) and thread them through the dispatch into runtime construction for all four modes.
2. **`src/openharness/ui/runtime.py`** — accept `allowed_tools` / `disallowed_tools` overrides in `build_runtime` and fold them into the `PermissionSettings` handed to `PermissionChecker`.
3. **`src/openharness/ui/app.py`** — replace `_noop_permission` (`:204-205`) with the mode-aware handler; mirror in `run_task_worker` (`:92-174`); emit the new events alongside the existing event rendering (`:240-296`).
4. **Docs** — README headless section + `CHANGELOG.md` migration note.
5. **Tests** — `tests/`: flag parsing/merge precedence; ask-path matrix (3 modes × approve/deny); sensitive-path deny unaffected by `full_auto`; event emission in both JSON formats; task-worker parity.

No new dependencies. Conforms to AGENTS.md §3 (runtime baseline).

## Compatibility and risks

- **This is a deliberate breaking change** for headless users who relied on implicit auto-approval in `default` mode. Migration is one flag: `--permission-mode full_auto`. Must be called out prominently in `CHANGELOG.md` and release notes; warrants a minor version bump.
- **Risk: silently degraded runs.** A run that used to "work" may now finish with the task incomplete because a tool was denied. Mitigation: the denial events (and stderr messages) make this observable; the denial message tells the operator the exact remediation.
- **Risk: divergence between print mode and task worker.** Both must get the same handler; the test suite pins parity.
- **Interaction with [headless-resume](headless-resume.md):** none structural — both touch `run_print_mode`'s signature, so land sequentially and rebase the second; either order works.

## Acceptance criteria

1. `oh -p "delete x" --disallowed-tools Bash --permission-mode full_auto` — Bash calls are denied (explicit deny beats full_auto), denial visible in `stream-json`.
2. `oh -p "write a file" ` (default mode, non-read-only tool, no allowlist) — tool denied, run exits 0, result text present, `denied_tools` non-empty.
3. `oh -p "write a file" --permission-mode full_auto` — tool approved; behavior identical to today.
4. `oh -p "read that credential" --permission-mode full_auto` targeting a sensitive path — still denied (built-in protection unaffected).
5. `--allowed-tools`/`--disallowed-tools` demonstrably affect interactive mode too (no longer dead flags).
6. Interactive prompting behavior (TUI ask dialogs) is unchanged.
7. New tests cover 1-6 and pass in CI.

## Out of scope

- Per-path or per-command allow *rules* from the CLI (settings.json already supports path rules; CLI surface for them is a separate proposal if needed).
- Permission semantics of `--backend-only` modal flow (interactive by design; only flag-wiring from §1 applies to it).
- Sandboxing/jailing of tool execution — orthogonal defence layer.

## Open questions

1. Should `plan` mode in headless emit a distinct event type (e.g. `plan_blocked`) instead of `permission_denied`? (Proposed: no — one event type, `reason` carries the mode.)
2. Should `denied_tools` in the `json` result deduplicate by tool name or list every denial occurrence? (Proposed: every occurrence, with `tool_name` + `reason`; callers can dedupe.)
