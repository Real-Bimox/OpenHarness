# v0.1.14 — Config Caching and Prompt-Cache Breakpoints

OpenHarness v0.1.14 ships the second pair of performance-hardening workstreams:
per-line configuration caching (WS3) and Anthropic prompt-caching breakpoints
(WS2). Together they remove nearly all fixed per-line overhead and let the
provider cache the unchanged prompt prefix between turns.

## Highlights

- **Per-line assembly: ~45–60 ms → ~4 ms intrinsic**
  - Settings files, inline `--settings` sources, keybindings, plugins, skill
    registries, CLAUDE.md chains, git environment info, the base system
    prompt, and the skills section are cached behind stat/identity
    fingerprints. Hot-reload semantics are preserved: edits are picked up on
    the next line (plugin/skill directory walks revalidate at most once per
    second), and plugin install/uninstall/`/plugins reload` invalidate
    immediately.
  - Hook registries rebuild only when their inputs change, with plugin hooks
    always included from the first turn.
  - Hook/plugin/MCP summaries are computed lazily — plain prompts never pay
    for them.
  - Auth status checks no longer hit the OS keyring (or refresh OAuth tokens
    over the network) on every line; a 30 s TTL cache covers the status chip
    and provider commands refresh it immediately.
  - Per-line state writes keep rename atomicity but skip fsync; the durable
    write policy moves to the persistence workstream (WS4).
  - New `scripts/measure_per_line.py` gates the < 5 ms assembly budget.

- **Anthropic prompt caching**
  - `cache_control` breakpoints on the stable system-prompt prefix, the tool
    array, and the previous turn's last block — the provider now caches
    everything that does not change between requests, cutting input-token
    cost and time-to-first-token on long sessions.
  - The per-line relevant-memories section is kept outside the cached prefix
    via a stable-prefix boundary computed by the prompt builder.
  - Gated by `prompt_caching_enabled` (default on) as a kill switch for
    providers that reject `cache_control`. OpenAI-format and Codex clients
    are unchanged.
  - Cache effectiveness is observable: `usage` payloads everywhere now carry
    `cache_creation_input_tokens` / `cache_read_input_tokens`.

## Verification

- 1201 unit/integration tests pass (8 new for request shapes, cache
  counters, worker persistence interplay, and settings synthesis).
- Headless protocol harness: 34/34; persistent-worker harness: all checks.
- Per-line budget: PASS (~3.2–4.3 ms intrinsic, best-of-rounds minimum).

## Next

The roadmap concludes with append-only session persistence with retention
(WS4) and parallel MCP connect / per-channel dispatchers (WS5), targeted at
v0.1.15.
