# Proposal: error-recovery

## Status

| Field | Value |
|---|---|
| Status | IN PROGRESS |
| Proposal branch | `proposal/learning-search-resilience` |
| Owner | Bahram Boutorabi |
| Created | 2026-06-11 |
| Source study | hermes-agent @ `298bb93d3` (`agent/error_classifier.py`, `agent/conversation_loop.py`, `agent/credential_pool.py`, `hermes_cli/fallback_cmd.py`) |
| Related | report `docs/reports/openharness-vs-hermes-agent.md` |

## What hermes-agent does (the source capability)

hermes turns provider failures into a recovery state machine instead of
"retry 3 times then die":

- **`error_classifier.py`** maps any exception to a `ClassifiedError` with a
  `reason` and recovery-hint flags (`retryable`, `should_compress`,
  `should_rotate_credential`, `should_fallback`). Classification walks
  status codes, structured error codes, and ~15 lists of lowercased message
  substrings, with careful provider-specific ordering (safety blocks before
  status, `max_tokens` validation before context-overflow, SSL before
  disconnect, etc.).
- **The retry loop** acts on the classification in a fixed order: credential
  rotation within a provider, one-shot format recoveries (image shrink,
  thinking-signature strip, …), eager fallback for rate-limit/billing,
  compress-then-retry for context overflow, transport rebuild on timeout,
  jittered backoff (Retry-After honored), and a per-turn primary-provider
  restoration so a temporary fallback doesn't become permanent.
- **`credential_pool.py`** holds multiple credentials per provider with
  selection strategies (fill-first/round-robin/least-used), failure cooldowns
  (401→5min, 429→1h, or a provider-reported reset), DEAD vs EXHAUSTED states,
  and per-provider OAuth refresh with single-use-token bracketing.
- **Fallback chains** (`fallback_providers` in config, `hermes fallback` CLI)
  switch provider/model mid-turn; conversation state needs no migration
  because the canonical message list is converted to each provider's wire
  format per request.

Weaknesses found in their implementation (from the source study, addressed
here): every detection is an English substring match — localized or reworded
errors silently change behavior; the classifier's flags are only partially
honored (the loop re-derives behavior from reason-membership sets); recovery
state is scattered across `TurnRetryState` plus a dozen `agent._*` counters
with different lifetimes, so the total attempt count per turn is effectively
unbounded under adversarial chains; backoff has two divergent parameter sets;
credential-pool multi-process races are handled by best-effort disk resyncs.

## OpenHarness design

OpenHarness's architecture lets recovery compose cleanly instead of living
inside a 4,200-line loop:

### 1. Typed declarative classifier — `api/error_classifier.py`

`RecoveryReason` enum + frozen `ClassifiedError(reason, status_code,
retryable, should_rotate_credential, should_fallback, should_compress,
message)`. Detection is a **declarative rule table** (the cleaner design
hermes's own author recommended): ordered rows of
`(status, marker_substrings, code, reason)` evaluated by specificity, each
row independently unit-testable. `classify_error(exc)` extracts status/body/
code/message once (walking `__cause__`/`__context__`), then returns the first
matching row's `ClassifiedError`. This replaces the existing
`_is_retryable`/`_get_retry_delay` string checks in `api/client.py`.

The `ClassifiedError` flags are the **single policy authority** — the
recovery layer reads the flags, never re-derives behavior from the reason.

### 2. Credential pool — `api/credentials.py`

API-key rotation: a per-provider pool loaded from settings
(`credential_pools: {provider: [keys]}`) and env, with failure cooldowns
(401→5min, 429/billing→1h, provider `retry-after` honored), `exhausted` vs
`dead` states, and fill-first selection. Honest deviation: this rotates
**API keys**, the common multi-key case. It does not maintain per-provider
*OAuth* token pools with single-use-refresh bracketing (hermes's deepest,
2,184-line feature); OpenHarness's existing singleton OAuth refresh
(`AnthropicApiClient.auth_token_resolver`) is invoked on auth failures, but
multi-account OAuth pools are out of scope and documented as such.

### 3. Resilient wrapper client — `api/resilient_client.py`

A `ResilientApiClient` implements `SupportsStreamingMessages` and wraps a
primary client plus an ordered list of fallback client factories. It runs the
recovery state machine in **one place** with one `AttemptBudget` (total
attempts capped, replacing hermes's scattered counters):

1. classify the error;
2. `should_rotate_credential` → rotate the pool, rebuild the client, retry;
3. `should_compress` → surface a `CompactionRequiredEvent` the engine's
   existing reactive-compaction path consumes (OpenHarness already compacts
   on "prompt too long"; this drives it from typed classification);
4. `should_fallback` → advance to the next chain entry, rebuild, retry;
5. otherwise `retryable` → jittered backoff (single parameter set,
   Retry-After honored), retry;
6. else translate and raise.

Primary restoration happens at the start of each turn (the wrapper resets to
the primary client), matching hermes's per-turn semantics. Mid-turn provider
switching needs no message migration: each underlying client already converts
`request.messages` to its own wire format in `_stream_once`.

Recovery steps emit existing/new stream events (`ApiRetryEvent`, new
`ProviderFallbackEvent`, `CredentialRotatedEvent`) so they appear in
`stream-json`, headless events, and the TUI.

### 4. Settings + CLI

- Settings: `fallback_providers: list[FallbackProvider]`
  (`{provider, model, base_url?, api_format?, api_key_env?}`),
  `credential_pools: dict[str, list[str]]`, `api_max_retries: int = 3`.
- CLI: `oh fallback add|list|remove|clear` (add reuses provider/model
  resolution and never changes the active provider, like hermes).
- The resilient wrapper is installed by `_resolve_api_client_from_settings`
  only when a chain or pool is configured — zero overhead otherwise.

### 5. Exposure surfaces

- **Headless JSONL**: additive `recovery_status` request → configured chain,
  pool sizes, last recovery actions; recovery events already flow as stream
  events.
- **CLI**: `oh fallback ...`.
- **MCP** (F4): `recovery_status`.

## Capability parity statement (honest)

- Equal: typed error classification across status/code/message with
  provider-specific ordering; retry with Retry-After-aware jittered backoff;
  credential rotation on rate-limit/auth/billing with cooldowns; provider
  fallback chains with mid-turn switching and no message migration;
  compress-and-retry on context overflow; per-turn primary restoration;
  recovery actions surfaced to the user.
- Better: classification is a declarative, unit-testable rule table, not
  15 ad-hoc substring lists; `ClassifiedError` flags are the sole policy
  authority (no duplicated reason sets); one `AttemptBudget` with a hard
  per-turn ceiling instead of scattered counters; one backoff parameter set;
  recovery lives in a composable wrapper, not the engine.
- Not carried over, with reasons (stated plainly, no hand-waving):
  - **Multi-account OAuth credential pools** with single-use-refresh
    bracketing — hermes's 2,184-line pool. OpenHarness rotates API keys and
    refreshes the singleton OAuth token on auth failure, but does not pool
    multiple OAuth accounts per provider. This is the one real capability
    gap; it is documented, not hidden.
  - The long tail of provider-specific one-shot *format* recoveries (image
    shrink for 413, thinking-signature strip, llama.cpp grammar strip,
    encrypted-content replay disable). The classifier defines the reasons;
    OpenHarness wires the high-value ones (context-overflow compress,
    auth refresh, rate-limit rotate, fallback) and treats the format-repair
    reasons as retryable/fallback for now. Adding each repair is incremental
    and does not change the architecture.
  - Multi-process credential-pool coordination (hermes's disk-resync races):
    OpenHarness's pool is per-process; concurrent gateway processes are a
    later concern tracked with the multi-tenancy work.
