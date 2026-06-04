# Proposal: headless-resume — session continuity for print mode

## Status

| Field | Value |
|---|---|
| Status | DRAFT |
| Proposal branch | `proposal/headless-resume` |
| Owner | Bahram Boutorabi |
| Created | 2026-06-04 |
| Doc review | pending |
| Related | [headless-permission-enforcement](headless-permission-enforcement.md) |

## Summary

Allow `--resume <session-id>` and `--continue` to combine with `-p/--print`, so a non-interactive invocation can continue a prior conversation instead of always cold-starting. Emit the `session_id` in machine-readable output so orchestrators can chain invocations.

## Motivation

External orchestrators (the immediate driver is AutoDev integration) wake OpenHarness on a polling cadence with a one-shot prompt:

```
oh -p "<wake nudge>" --cwd <worktree> --output-format stream-json
```

Today every wake is a cold start: the agent re-reads its entire working context from scratch each tick. At a ≥2-minute polling cadence this is the dominant token cost of hands-off operation. Headless resume turns each wake into an incremental turn on an existing conversation.

This is an efficiency improvement, not a correctness requirement — orchestration protocols that keep durable state in git remain the source of truth. Resume is an optimization layered on top.

## Current behavior (evidence)

1. **The resume branch wins and forces the interactive REPL.** In the main CLI dispatch, the `--continue`/`--resume` branch loads the snapshot and dispatches to `run_repl(...)`, then `return`s — before the `print_mode` branch is ever reached (`src/openharness/cli.py:2437-2496`). `oh -p "..." --resume <id>` therefore silently ignores `-p` and opens the TUI.
2. **`--resume` with no value is interactive by design.** It lists snapshots and calls `typer.prompt(...)` for a picker (`src/openharness/cli.py:2453-2460`) — unusable headless.
3. **`run_print_mode` cannot accept restored state.** Its signature has no restore parameters (`src/openharness/ui/app.py:177-192`).
4. **The plumbing below already exists.** `build_runtime` accepts `restore_messages` (`src/openharness/ui/runtime.py:290`) and rehydrates them into the engine (`src/openharness/ui/runtime.py:433-435`); `run_repl` already passes them through (`src/openharness/ui/app.py:53-70`).
5. **Print-mode runs already persist sessions.** Print mode executes via `handle_line` (`src/openharness/ui/app.py:300-306`), which saves a snapshot through `bundle.session_backend.save_snapshot` (`src/openharness/ui/runtime.py:764`, plus the max-turns and continue paths at `:689` and `:720`). So the *write* half of headless continuity already works; only the *read* half (resume into print mode) is missing.
6. **Orchestrators cannot learn the session id.** The `--output-format json` result is `{"type": "result", "text": ...}` with no `session_id` (`src/openharness/ui/app.py:325-327`), so even though snapshots are saved, a caller has nothing to pass to `--resume`.

## Proposed change

### CLI surface

| Invocation | New behavior |
|---|---|
| `oh -p "..." --resume <id>` | Load snapshot by id; run print mode with restored messages; exit as today |
| `oh -p "..." --continue` | Load latest snapshot for `--cwd`; run print mode with restored messages |
| `oh -p "..." --resume` (no value) | **Error** to stderr, non-zero exit — never an interactive picker in print mode |
| `oh -p "..."` (no resume) | Unchanged |
| `--resume`/`--continue` without `-p` | Unchanged (interactive REPL, picker allowed) |

### Output contract

- `--output-format json`: the final result object gains `"session_id": "<id>"`.
- `--output-format stream-json`: emit a first event `{"type": "session_started", "session_id": "<id>", "resumed": <bool>}`.
- `--output-format text`: print `session id: <id>` to stderr at completion (stdout stays clean).

A caller can then loop: invoke → capture `session_id` → next tick `--resume <session_id>`.

### Semantics

- Resuming continues the **same** `session_id`; the post-run snapshot overwrites/extends that session's chain, matching what interactive `--resume` users expect.
- Snapshot-not-found remains a hard error (exit 1), as it is today (`src/openharness/cli.py:2474-2477`).
- `--dry-run` continues to reject `--continue`/`--resume` (`src/openharness/cli.py:2399-2401`); no change.

## Implementation sketch

1. **`src/openharness/cli.py`** — restructure the dispatch: when `print_mode is not None`, perform snapshot loading inline (id and `--continue` paths only; picker path errors), then fall through to the print-mode branch with `session_data`.
2. **`src/openharness/ui/app.py`** — extend `run_print_mode` with `restore_messages`, `restore_tool_metadata`, `session_id` keyword parameters; forward to `build_runtime` exactly as `run_repl` does (`src/openharness/ui/app.py:53-70`).
3. **`src/openharness/ui/app.py`** — emit `session_id` per the output contract above (the bundle exposes it; see `bundle.session_id` usage at `src/openharness/ui/runtime.py:695`).
4. **Tests** — `tests/`: dispatch matrix (the five rows above), snapshot round-trip (`-p` run → `-p --resume` run sees prior messages), no-picker guarantee (print mode with bare `--resume` exits non-zero without reading stdin).

No new dependencies; no changes outside the Python package. Conforms to AGENTS.md §3 (runtime baseline).

## Compatibility and risks

- **Backward compatible.** Every currently-working invocation behaves identically. The only behavior change is to invocations that are broken today (`-p` + resume flags ignoring `-p`).
- **Risk: model/profile drift across resumes.** A snapshot stores the model it ran with; the interactive path prefers the snapshot's model (`src/openharness/cli.py:2484`). Print mode should do the same: snapshot model unless `--model` is explicitly given. Note the upstream-inherited fix `9b2efd7` ("preserve profile auth when overriding model") touches this area — keep its behavior intact.
- **Risk: context growth across many resumed ticks.** Long-running loops will eventually trigger compaction; `CompactProgressEvent` is already surfaced in stream-json (`src/openharness/ui/app.py:276-287`), so orchestrators can observe it. No additional work in this proposal.

## Acceptance criteria

1. `oh -p "remember the word kestrel" --output-format json` returns a `session_id`.
2. `oh -p "what word did I ask you to remember?" --resume <that-id> --output-format json` answers from restored context and exits 0.
3. `oh -p "..." --resume` (no value) exits non-zero with a clear stderr message and never blocks on input.
4. `oh -p "..." --continue` in a directory with no sessions exits 1 with the existing "No previous session found" message.
5. Interactive `--resume`/`--continue` behavior is byte-for-byte unchanged.
6. New tests cover 1-5 and pass in CI.

## Out of scope

- Permission behavior in headless mode — see [headless-permission-enforcement](headless-permission-enforcement.md).
- Resume support for `--task-worker` and `--backend-only` (both have their own session semantics; revisit after this lands).
- Cross-machine session portability.

## Open questions

1. Should `--continue` in print mode pick the latest session across *all* of `--cwd`'s snapshots, or only sessions previously created by print mode? (Proposed: all — same as interactive.)
2. Should `stream-json` also emit `session_started` for non-resumed runs? (Proposed: yes — uniform contract is simpler for orchestrators.)
