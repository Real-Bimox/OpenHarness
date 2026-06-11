# v0.1.16 — Post-Release Hardening for the v0.1.15 Surfaces

A patch release carrying the fixes from the post-release review of v0.1.15.
The v0.1.15 tag predates these commits; consumers should move to this tag.

## Fixed

- **Conversation-search surfaces can no longer hang.** A reported first-run
  hang of headless `search_sessions` could not be reproduced locally in nine
  configurations (all four search shapes, bare and full runtime, isolated and
  real home directories, plus the minimal `asyncio.to_thread` isolation), so
  the fix is structural: every caller-facing index operation is bounded by a
  hard 20-second timeout. On timeout the surface answers with an error that
  includes the blocked worker-thread stack, so any environment-specific block
  (e.g. exotic filesystem locking) self-diagnoses instead of hanging the
  protocol. A regression test proves the protocol completes even when the
  index layer blocks indefinitely; a real-subprocess first-run test pins the
  empty-index path.
- `conversation_index_enabled=false` is now honored by **every** surface
  through one shared gate: `oh sessions list|search|reindex` exits 1, the
  headless `search_sessions` request answers with an error event, and the MCP
  `search_sessions`/`list_sessions` tools return an error payload. Previously
  only the in-agent tool checked it.
- `--mcp-serve` now rejects conflicting flags (`--headless`, `--task-worker`,
  `--backend-only`, `-p/--print`, `--dry-run`, `--continue/--resume`) with
  exit 1 instead of silently starting the MCP server.
- `ResilientApiClient` no longer blanket-fallbacks translated terminal errors
  under a hardcoded `auth` reason. `OpenHarnessApiError` is classified
  (typed auth/rate-limit failures classify by type), and fallback happens only
  when the classifier sets `should_fallback` — restoring the
  classifier-as-sole-policy-authority contract. A non-fallback terminal error
  now raises without consuming the fallback chain.
- Lint debt cleared (`ruff check .` is clean) and a disallowed emoji removed
  from the README release history.
- Proposal hygiene per AGENTS.md: five implemented proposals are now marked
  `IMPLEMENTED`, and the merged proposal branches
  (`learning-search-resilience`, `headless-local-control-api`,
  `config-and-prompt-caching`) are archived under `archive/proposal/*`.

## Verification

- 1260 tests pass (7 new regression tests for the review findings, including
  the never-hang proof and a real-subprocess empty-index headless search).
- `ruff check .` clean.
