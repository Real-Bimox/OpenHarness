# v0.1.15 — Conversation Search, Skill Learning, and Provider Resilience

OpenHarness v0.1.15 adds three capabilities studied from
[hermes-agent](https://github.com/NousResearch/hermes-agent) and reimplemented
to a higher engineering standard, each exposed via agent tool, headless JSONL
protocol, `oh` CLI, and a new MCP server. The full, honest parity accounting —
including the one documented capability gap — is in
[docs/reports/learning-search-resilience-parity.md](docs/reports/learning-search-resilience-parity.md).

## Highlights

- **Conversation search.** A rebuildable SQLite FTS5 index over saved session
  snapshots powers a `session_search` tool (discover / read / scroll / browse,
  zero LLM cost), `oh sessions list|search|reindex`, a headless
  `search_sessions` request, and an MCP tool. Because the index is a derived
  cache over the JSON snapshots, recovery is "rebuild," not database surgery.
  Secrets are redacted before indexing, message bodies are budgeted, and
  model-supplied queries are parsed into valid FTS5. Gated by
  `conversation_index_enabled` (default on).

- **Skill learning loop.** A `skill_manage` tool
  (create/edit/patch/delete/write_file/remove_file) lets the agent grow its
  own skill library; a post-turn background review fork may create or improve
  skills; usage telemetry drives an active → stale → archived lifecycle with
  pinning; a weekly curator consolidates agent-created skills into umbrellas;
  and an optional approval mode stages writes for review. Hardened beyond the
  source: writes are structurally confined to user skills (bundled and plugin
  skills cannot be edited), write scanning for secrets and injection is on by
  default, the curator has no shell and no archive quota, and approval diffs
  apply the same operation they preview. New `SkillSettings`; `oh skills
  usage|pin|unpin|pending|diff|approve|discard|curator`.

- **Error recovery, fallback chains, and credential rotation.** A typed,
  declarative error classifier feeds a resilient wrapper client that runs the
  recovery state machine — credential rotation, provider fallback, backoff,
  per-turn primary restoration — in one place with a single hard attempt
  budget. Provider fallback chains (`oh fallback list|add|remove|clear`)
  switch mid-turn with no message migration. Per-provider API-key pools rotate
  on rate-limit/auth/billing with cooldowns. Recovery actions surface as
  `ProviderFallbackEvent` / `CredentialRotatedEvent` in stream-json and
  headless output.

- **MCP server mode** (`oh --mcp-serve`). OpenHarness can now act as an MCP
  server (it was previously client-only), exposing `search_sessions`,
  `list_sessions`, `skill_loop_status`, `run_skill_curator`, and
  `recovery_status` over stdio on the official SDK — no new dependency. It
  wraps the same internal operations as the headless protocol, so the two
  surfaces cannot drift.

## Honest capability gap

F3 rotates API keys and refreshes the singleton OAuth token on auth failure,
but does **not** pool multiple OAuth *accounts* per provider (hermes-agent's
largest module). This is the one real deviation and is documented in
[docs/proposals/error-recovery.md](docs/proposals/error-recovery.md) and the
parity report rather than hidden. The MCP server exposes read/maintenance
operations; turn submission and streaming over MCP remain follow-up scope (the
headless JSONL protocol is the stateful turn surface).

## Verification

- 1253 unit/integration tests pass (62 new across the four features,
  including an end-to-end review fork that creates a skill and a real
  `oh --mcp-serve` stdio handshake).
- Headless protocol harness 34/34; persistent-worker harness all checks;
  per-line assembly budget PASS.

## Reference

- Comparative study: [docs/reports/openharness-vs-hermes-agent.md](docs/reports/openharness-vs-hermes-agent.md)
- Parity accounting: [docs/reports/learning-search-resilience-parity.md](docs/reports/learning-search-resilience-parity.md)
- Proposals: `docs/proposals/{conversation-search,skill-learning-loop,error-recovery,mcp-server-mode}.md`
