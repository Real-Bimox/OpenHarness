# Proposal: robustness-performance-hardening

## Status

| Field | Value |
|---|---|
| Status | DRAFT |
| Proposal branch | `proposal/robustness-performance-hardening` |
| Owner | Bahram Boutorabi |
| Created | 2026-06-09 |
| Doc review | pending |

## Summary

Implement the high and medium robustness/performance findings that can be addressed with scoped guardrails while preserving a low-conflict path for future upstream updates. The changes focus on bounded waits, bounded memory/log growth, safer file writes and path handling, frontend protocol resilience, and compatibility defaults for channel configuration.

## Motivation

The review reports identify several classes of failure that can stall or degrade long-running sessions:

- external calls without deadlines can hang a turn indefinitely;
- logs, queues, transcripts, and file reads can grow without bound;
- concurrent file mutations can lose updates or leave partial files;
- malformed configuration or backend protocol lines can crash startup/UI;
- channel adapters can dereference fields that are absent from the declared config schema.

These are operational guardrails rather than feature rewrites, so the implementation should stay close to existing modules and avoid broad architectural churn.

## Scope

In scope:

- Channel config schema compatibility defaults for existing adapters.
- Tool, MCP, and frontend-question timeouts with structured failures.
- Bounded channel queues and task/bridge output storage.
- True tail reads for task and bridge logs.
- Safer file and image input limits; atomic writes for write/edit tools.
- Per-file serialization for mutating file tools.
- Plugin traversal limits that avoid following symlink cycles.
- Terminal frontend caps and malformed protocol handling.
- Settings/env parse recovery diagnostics.
- OAuth client cleanup on refresh.
- Workspace-contained `todo_write` paths.
- Process-wide locking around mode resolution in atomic write helper.

Out of scope:

- Large module splits, dependency changes, or frontend framework upgrades.
- Changes that require new runtime services or toolchains.
- Unsupported findings from the audit comparison.

## Compatibility

The implementation should remain backward compatible for valid inputs. Overload cases may now return bounded error messages, reject unsafe paths, cap retained history, or drop queue items instead of consuming unbounded memory. Defaults are intentionally conservative and local to the affected subsystem.

## Acceptance Criteria

1. The repository compiles with the supported Python baseline.
2. Focused tests or direct checks cover the modified guardrails where feasible.
3. No new runtime dependencies are introduced.
4. The implementation branch can be pushed to `origin` without touching upstream.
