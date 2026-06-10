# Proposal: release-architecture-hardening

## Status

| Field | Value |
|---|---|
| Status | DRAFT |
| Proposal branch | `proposal/release-architecture-hardening` |
| Owner | Bahram Boutorabi |
| Created | 2026-06-10 |
| Related | [headless-local-control-api](headless-local-control-api.md), [headless-permission-enforcement](headless-permission-enforcement.md) |

## Summary

OpenHarness is ready for the integration team to start on the current local/headless surface, but the review identified several architectural areas that should be hardened after the release: command startup boundaries, staged typing, provider/local-mode hygiene, performance guardrails, and eventual multi-tenancy.

This proposal keeps the integration release small and stable while creating a review path for deeper changes that should not be rushed into the release branch.

## Current Release Position

The current release improves startup and runtime robustness without changing the main public integration contract:

- Tool registration no longer imports every built-in tool module during startup.
- `ohmo` CLI command handling no longer imports heavy runtime modules for simple startup paths.
- Gateway runtime sessions are bounded by count and idle age, and cached sessions are closed on shutdown.
- Session listings use a lightweight index while preserving legacy session-file visibility.
- Background task and bridge output reads tail files instead of loading whole logs.
- Mailbox reads can avoid scanning already-read messages.
- Frontend launch fails clearly when local dependencies are missing instead of performing implicit installs.
- Autopilot blocking work is moved off the async event loop at orchestration boundaries.
- Startup measurement is available through `scripts/measure_startup.py`.

## Problems To Address Later

### 1. Monolithic Command Registry

`openharness.commands.registry` still costs roughly 1.3 seconds to import. It is a large module that combines command metadata, handlers, and subsystem imports for unrelated domains.

Target outcome:

- Keep `create_default_command_registry()` and public command lookup APIs stable.
- Split command domains into focused modules:
  - `commands/core.py`
  - `commands/session.py`
  - `commands/memory.py`
  - `commands/tasks.py`
  - `commands/plugins.py`
  - `commands/autopilot.py`
  - `commands/config.py`
- Register lightweight command metadata eagerly.
- Import heavy handler implementations only when the matching command runs.
- Add a startup budget in CI or release checks for command-registry import time.

Review criteria:

- `import openharness.commands.registry` should be below 300 ms on the release test environment.
- `oh --help` and `ohmo --help` should remain below 300 ms.
- Existing command tests should pass without changing command names, aliases, or output contracts.

### 2. Staged Typing And Mypy Gate

Full-project `mypy` currently reports baseline typing debt across legacy modules, so it is too noisy to use as a release gate.

Target outcome:

- Fix package/source-layout configuration so local modules are analyzed as source, not mistaken for untyped installed packages.
- Create a typed-module allowlist for actively maintained surfaces:
  - headless control
  - session storage
  - gateway runtime
  - tool registry
  - mailbox/task persistence
  - command registry facade
- Gate the allowlist in CI or release checks.
- Expand the allowlist as modules are cleaned.

Review criteria:

- Mypy passes for the allowlisted modules.
- New code in release-critical paths must either pass mypy or explicitly document why it is excluded.
- Legacy exclusions are tracked in the proposal or a baseline file, not hidden in broad global ignores.

### 3. Provider And Local-Mode Hygiene

Provider/product references are currently functional references for supported API/auth modes. They are not generated attribution leakage, but the local/headless integration path should be clear and minimal.

Target outcome:

- Keep provider-specific code where it is required for auth, model routing, and compatibility.
- Add a local/headless integration profile that hides unrelated provider setup paths unless configured.
- Separate user-facing provider labels from low-level provider implementation constants.
- Keep release scans for generated attribution and accidental third-party leakage.

Review criteria:

- Local-only operation can be configured without presenting remote-provider setup as mandatory.
- Provider references are either functional, test-only, or documented compatibility labels.
- No generated attribution strings appear in release artifacts.

### 4. Performance Guardrails

The release now has a startup measurement script, but the measurements are not yet enforced.

Target outcome:

- Define release budgets for startup probes:
  - `import openharness.tools`
  - `create_default_tool_registry`
  - `import openharness.commands.registry`
  - `create_default_command_registry`
  - `oh --help`
  - `ohmo --help`
- Store measured results in release notes or reports.
- Fail release checks only after budgets are stable enough to avoid false failures.

Review criteria:

- Performance budgets are explicit.
- Regressions are visible before release.
- Slow paths have owner-visible exceptions, not silent drift.

### 5. Multi-Tenancy

Multi-tenancy should be handled as a separate design effort. It changes isolation, identity, storage, permissions, and operational boundaries, so it should not be folded into the current integration release.

Target outcome:

- Define tenant identity and scope:
  - local user
  - workspace
  - channel
  - team
  - external orchestrator session
- Define tenant-isolated storage roots for sessions, memory, task logs, mailbox state, credentials, and gateway runtime state.
- Define permission policy inheritance and override rules.
- Define migration behavior for existing single-tenant data.
- Define observability and audit records without leaking tenant-private content.

Review criteria:

- No session, memory, task, or mailbox state can cross tenant boundaries by accident.
- Credentials and provider bindings are tenant-scoped.
- Gateway runtime pools enforce tenant-aware limits.
- Existing single-user local workflows remain simple.

## Suggested Review Order

1. Command registry split and startup budget enforcement.
2. Staged mypy gate for release-critical modules.
3. Local/headless profile cleanup and provider-label hygiene.
4. Session/runtime performance budgets beyond startup.
5. Multi-tenancy proposal and threat model.

## Non-Goals For The Current Release

- No new runtime dependencies.
- No REST, WebSocket, or over-the-network control service.
- No broad command behavior redesign.
- No full-project typing cleanup in one pass.
- No multi-tenant storage migration.
