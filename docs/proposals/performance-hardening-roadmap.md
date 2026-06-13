# Proposal: performance-hardening-roadmap

## Status

| Field | Value |
|---|---|
| Status | PARTIALLY IMPLEMENTED |
| Proposal branch | `proposal/performance-hardening-roadmap` |
| Owner | Bahram Boutorabi |
| Created | 2026-06-10 |
| Baseline | `v0.1.12` (`841e3e5`) |
| Related | [release-architecture-hardening](release-architecture-hardening.md), [headless-local-control-api](headless-local-control-api.md) |

> **Partially implemented.** WS1 (persistent task workers) and WS6 (quick wins) shipped in v0.1.13; WS3 (config/prompt-stability caching) and WS2 (prompt-caching breakpoints) shipped in v0.1.14. **WS4 (append-only session persistence + retention) and WS5 (parallel MCP connect + per-channel dispatchers) are not yet implemented** — verified absent from `services/session_storage.py`, `config/settings.py`, `mcp/client.py`, and `channels/impl/manager.py`. The Sequencing table below is the original plan; the `0.1.15` row did not happen (0.1.15 shipped unrelated features), so WS4/WS5 remain unscheduled.

## Summary

The v0.1.12 architectural review identified four structural bottlenecks (one-shot
task workers, no prompt caching, O(n²) session persistence, per-line config
re-reads) plus a set of per-turn latency and throughput issues. This roadmap
addresses every finding in six workstreams, ordered by leverage. Each workstream
is independently shippable, has explicit acceptance criteria and budgets, and
none changes the public headless protocol contract (`protocol_version` stays 1).

## Measured Baseline (v0.1.12, NVMe, warm cache)

| Probe | Baseline | Target |
|---|---|---|
| Follow-up message to a subprocess teammate | ~1.8–5 s process rebuild + full context loss | < 5 ms (stdin write), context preserved |
| Input-token cost, 100-turn / 150K-token session | ~100% uncached every turn | ≥ 80% cached prefix tokens after turn 2 |
| Per-line runtime assembly before API call | ~45–60 ms (empty config; grows with plugins/skills/memory) | < 5 ms when nothing on disk changed |
| Disk writes per user line (200-msg session) | ~2–4 MB × 2 files + index, ≥ 3 fsyncs | O(turn delta), 1 fsync |
| Per-turn fixed dead time (compaction poll) | ~50 ms | ~0 ms |
| MCP cold connect (4 servers) | sum of handshakes, unbounded on hang | max(handshakes), hard per-server timeout |

---

## Workstream 1 — Persistent Task Workers

**Problem.** `run_task_worker` reads one stdin line and exits by design
(`src/openharness/ui/app.py:286-305`). Every coordinator follow-up triggers
`_ensure_writable_process` → `_restart_agent_task`
(`src/openharness/tasks/manager.py:333-361`): a full `oh` cold start (~1.8 s
floor measured, 2–5 s realistic with MCP) and — worse — an empty conversation,
so the model re-derives all context. Multi-agent token costs are dominated by
this re-derivation.

**Design.**

1. Make the worker loop persistent: remove the one-shot `break`; keep reading
   stdin lines until EOF or idle timeout. The loop structure and EOF handling
   already exist.
2. Add an idle timeout (default 10 minutes, env/setting
   `task_worker_idle_timeout_s`) using `asyncio.wait_for` around the stdin
   read; on timeout, save a session snapshot and exit 0. The existing restart
   path in the task manager remains as the crash/idle-resume fallback —
   `_ensure_writable_process` already restarts dead processes transparently.
3. Context preservation across restarts (second phase): on worker start, if the
   task metadata carries a `session_id`, restore the snapshot via the existing
   `restore_messages` path in `build_runtime`; on exit/idle, save it. This
   turns the restart fallback from "context lost" into "context restored",
   and the `status_note` "prior interactive context was not preserved"
   (`tasks/manager.py:354`) can be retired.
4. Event-driven coordinator waits: `drain_coordinator_async_agents` polls every
   100 ms (`src/openharness/ui/coordinator_drain.py:62-86`); switch it to the
   existing `BackgroundTaskManager.register_completion_listener`
   (`tasks/manager.py:237-245`) with the poll as a fallback heartbeat.
5. Swarm permission sync polls the mailbox directory at 0.5 s
   (`src/openharness/swarm/permission_sync.py:1056-1072`); piggyback on the
   persistent-worker change by checking the in-memory queue first and lowering
   the directory-scan frequency (scan only as fallback, 2 s).

**Files.** `src/openharness/ui/app.py` (worker loop),
`src/openharness/tasks/manager.py` (restore metadata, retire notice),
`src/openharness/ui/coordinator_drain.py`, `src/openharness/swarm/permission_sync.py`,
`src/openharness/config/settings.py` (idle-timeout setting).

**Acceptance criteria.**
- A coordinator sending 3 follow-ups to one worker spawns exactly 1 process;
  each follow-up answer reflects prior context.
- Worker exits cleanly after idle timeout; the next follow-up transparently
  restarts it with restored conversation history.
- Coordinator drain wakes within 50 ms of worker completion without polling.

**Tests.** Extend `tests/test_merged_prs_on_autoagent.py` /
`tests/test_tasks/`: multi-message worker lifecycle (mock api client), idle
timeout exit + snapshot, restart-with-restore, drain listener wiring.

**Risks.** Long-lived workers hold MCP connections and memory — bounded by the
idle timeout; restart fallback preserves today's behavior on crash. The
`BackgroundTaskManager` keeps per-task stdin locks, so interleaved writes are
already serialized.

**Effort.** ~2–3 days including tests. No protocol changes.

---

## Workstream 2 — Prompt Caching Breakpoints

**Problem.** No `cache_control` anywhere. `_stream_once`
(`src/openharness/api/client.py:203-240`) sends `system` as a plain string,
tools as a list, full history as messages — the provider reprocesses the whole
prefix every turn. Dominant cost/TTFT lever (~10× input cost on long sessions).

**Design.**

1. Convert `params["system"]` to block-array form
   `[{"type":"text","text":..., "cache_control":{"type":"ephemeral"}}]` with
   the breakpoint on the last system block. The OAuth attribution header
   (`client.py:213-218`) becomes the first block so the cacheable suffix stays
   stable.
2. Add `cache_control` to the **last tool** in `params["tools"]` (caches the
   whole tool array). Requires tool-schema stability per session — delivered by
   the WS6 schema cache (do schema cache first or together).
3. Add a history breakpoint: `cache_control` on the last content block of the
   most recent *previous-turn* message (the prefix that will not change), set
   in `to_api_param` plumbing via a `cache_marker_index` computed in
   `ApiMessageRequest` assembly (`engine/query.py` request build). Anthropic
   allows 4 breakpoints total: attribution/system, tools, history prefix —
   3 used, 1 spare.
4. Gate per provider: Anthropic-format clients only (`AnthropicApiClient`,
   both API-key and OAuth paths; verify the betas list for the OAuth path
   accepts cache_control — fall back to no-op if the provider rejects it).
   OpenAI-compatible providers do their own implicit caching — no change.
5. Mind invalidation: the system prompt is currently rebuilt per line with a
   changing `latest_user_prompt` section (`prompts/context.py`) — WS3 makes the
   prefix stable; until then, place the per-line dynamic content *after* the
   cached system block or in the user message. WS2 therefore lands after or
   with WS3's prompt-stability slice.
6. Surface cache metrics: `UsageSnapshot` gains
   `cache_creation_input_tokens` / `cache_read_input_tokens` (already in API
   responses), reported through the existing `usage` fields in headless events
   and `oh -p` json results (additive, no contract break).

**Files.** `src/openharness/api/client.py`, `src/openharness/api/usage.py`,
`src/openharness/engine/query.py` (request assembly),
`src/openharness/engine/messages.py` (`to_api_param` cache marker).

**Acceptance criteria.**
- Turn 3+ of a steady session reports `cache_read_input_tokens ≥ 80%` of input
  tokens (integration test against the real API via the harness-eval skill, or
  recorded fixtures asserting request shape).
- Request-shape unit tests: breakpoints present exactly where specified;
  absent for OpenAI-format clients; OAuth path unaffected when rejected.

**Risks.** Cache invalidation from per-turn system-prompt churn (mitigated by
ordering after WS3); OAuth/beta path behavior differences (feature-flag
`prompt_caching_enabled`, default on for anthropic api_format, with kill
switch).

**Effort.** ~2–3 days + a real-API validation pass.

---

## Workstream 3 — Config/Prompt Caching and HookReloader Wiring

**Problem.** Every submitted line: settings.json parsed ~7×, plugin dirs walked
4× (full manifest/SKILL.md/YAML re-parse), hook registry rebuilt 2×, system
prompt rebuilt from disk including two synchronous `git rev-parse` subprocesses
(~34 ms measured), memory dir fully re-read with `usage_index.json` parsed once
per memory file then rewritten under a locked fsync, and `sync_app_state`
re-resolving auth — potentially a keyring D-Bus roundtrip or a synchronous
network OAuth refresh on the event loop (`runtime.py:620-643`). The existing
mtime-guarded `HookReloader` is instantiated and never used after startup
(`runtime.py:383`). Total: 45–60 ms+ blocking per line, all avoidable when
nothing changed.

**Design.**

1. **Settings cache**: module-level cache in `load_settings` keyed on
   `(path, stat.st_mtime_ns, st_size)` returning a deep-frozen `Settings`;
   `merge_cli_overrides` already copies. Also cache `merged_profiles()` on the
   instance (it deep-copies the 11-profile catalog 3–5× per load,
   `settings.py:614-627`). Env overrides re-applied per load (cheap) so
   env-var changes still take effect.
2. **Plugin/skill/CLAUDE.md caches**: cache `load_plugins` and
   `load_skill_registry` results keyed on a cheap fingerprint — tuple of
   `(dir, mtime_ns)` for each root directory (one `scandir` pass instead of
   full re-parse). Same pattern for the CLAUDE.md ancestor chain and
   MEMORY.md reads in `prompts/context.py`.
3. **Environment info cache**: `get_environment_info` (git subprocesses) cached
   per `(cwd, HEAD mtime)`; refresh at most every N seconds or on cwd change.
4. **Wire `HookReloader` into the bundle**: store it on `RuntimeBundle`; in
   `handle_line`, call `reloader.current_registry()` (stat-guarded) instead of
   the unconditional `load_hook_registry(current_settings(), current_plugins())`
   (`runtime.py:686-688`).
5. **Lazy `CommandContext` summaries**: change `hooks_summary`/`mcp_summary`/
   `plugin_summary` fields to zero-arg callables (or lazy properties) so plain
   prompts never compute them (`runtime.py:690-704`); only the `/hooks`-style
   commands evaluate them.
6. **`sync_app_state` slimming**: cache `auth_status` per settings fingerprint;
   never perform OAuth refresh inline — move `resolve_auth(refresh_if_needed=…)`
   to a background task that updates `app_state` when done; cache
   `load_keybindings` on file mtime.
7. **Memory relevance**: parse `usage_index.json` once per call (pass it down
   instead of per-file `get_memory_usage`, `memory/search.py:39`), move
   `mark_memory_used`'s locked fsync write to `asyncio.to_thread`, and cache
   memory-file scans on directory mtime.
8. Remove redundant `mkdir(exist_ok=True)` from hot getters (`config/paths.py:28`,
   `plugins/loader.py:43-47`, `skills/loader.py:170-171`) — create once at
   startup.

**Files.** `src/openharness/config/settings.py`, `src/openharness/config/paths.py`,
`src/openharness/ui/runtime.py`, `src/openharness/plugins/loader.py`,
`src/openharness/skills/loader.py`, `src/openharness/prompts/{context,environment,claudemd}.py`,
`src/openharness/memory/{search,usage}.py`, `src/openharness/keybindings/loader.py`.

**Acceptance criteria.**
- A submitted line with no on-disk changes performs ≤ 1 settings parse, 0 plugin
  re-parses, 0 git subprocesses, 0 network/keyring calls; measured per-line
  assembly < 5 ms (new benchmark, see Verification).
- Editing settings.json / a plugin file / CLAUDE.md between lines is picked up
  on the next line (mtime tests).
- Hook hot-reload semantics preserved: `tests/test_hooks_skills_plugins_real.py`
  passes unmodified.

**Risks.** Staleness bugs — mitigated by keying every cache on mtime_ns and
keeping the per-line check (a stat is ~1 µs); sub-second mtime granularity on
exotic filesystems (include size + inode in the key).

**Effort.** ~4–5 days; the largest workstream by surface, but each cache is
independent and individually revertable.

---

## Workstream 4 — Session Persistence: Append Transcript, Retention, Index Trust

**Problem.** Every line re-serializes the entire history (+ full system prompt)
and writes identical bytes to `latest.json` and `session-<id>.json` plus a full
index rewrite — ≥ 3 fsyncs, ~2–4 MB/line at 200 messages, O(n²) cumulative
(`services/session_storage.py:113-158`). No retention ever. The v0.1.11 index
fast path only applies when it already holds ≥ `limit` entries
(`session_storage.py:204-206`), so most projects still full-parse every session
file per listing. Resume does a double pydantic round-trip
(`_sanitize_snapshot_payload`).

**Design.**

1. **Append-only transcript**: per session, `session-<id>.jsonl` — one JSON
   line per appended message (or per turn delta), plus a small
   `session-<id>.head.json` (model, system-prompt hash, usage, tool_metadata,
   message_count) rewritten per turn. Save path computes the delta from the
   last persisted index (engine messages are append-only between compactions;
   on compaction, rewrite the file once and note a `compacted_at` marker).
2. **`latest.json` becomes a pointer** `{"session_id": ...}`; loaders resolve
   via `load_session_by_id`. Legacy full-format `latest.json`/`session-*.json`
   remain readable (loader sniffs format); new saves write only the new format.
3. **Trust the index**: `list_session_snapshots` returns index entries whenever
   the index file exists, regardless of count; one-time backfill migrates
   legacy files into the index on first listing. Compact stale entries on
   write (currently filtered on read, never removed,
   `session_storage.py:190`).
4. **Retention policy**: settings `session_retention_max_files` (default 50/project)
   and `session_retention_max_age_days` (default 30); pruning runs on save,
   oldest-first, never pruning the active or `latest`-pointed session. Headless
   `list_sessions` output unchanged in shape.
5. **fsync policy**: one fsync per turn on the transcript append; head/index
   writes use atomic-rename without per-write fsync (crash loses at most
   cosmetic metadata, transcript stays durable). Fix the missing parent-dir
   fsync in `atomic_write_text` or document the tradeoff (`utils/fs.py:57-62`).
6. **Resume cost**: single-pass load — validate once, drop the
   validate→dump→re-validate round-trip (`session_storage.py:161-171`).
7. Apply the same head+append pattern to `ohmo/session_storage.py` (same
   amplification, `_update_session_index` full rewrite per turn).
8. Stop persisting the full built system prompt per snapshot (store its hash +
   rebuild inputs; it's reconstructed on resume anyway by `build_runtime`).

**Files.** `src/openharness/services/session_storage.py`,
`src/openharness/services/session_backend.py`, `src/openharness/utils/fs.py`,
`ohmo/session_storage.py`, `src/openharness/config/settings.py` (retention),
loaders in `src/openharness/cli.py` / `src/openharness/ui/app.py` (no interface
change — same dict shape returned).

**Acceptance criteria.**
- Bytes written per line on a 200-message session drop from ~4–8 MB to
  O(new-turn size) (< 50 KB typical) — asserted by a benchmark counting bytes
  via a tmpdir.
- `-p --resume`, headless `resume`/`continue`/`list_sessions`, TUI picker, and
  `/session` commands behave identically (existing tests pass; add
  legacy-format fixture tests).
- A project with 500 legacy sessions lists in < 50 ms after first backfill.
- Retention prunes deterministically and never the active session.

**Risks.** Migration/compat — highest-risk workstream. Mitigations: loaders
keep reading the legacy format forever; new format behind
`session_storage_format=v2` setting for one release (default on, revert
switch); crash-consistency tests (truncate mid-append, loader recovers to last
complete line).

**Effort.** ~4–6 days including migration tests and the ohmo twin.

---

## Workstream 5 — Parallel MCP Connect; Per-Channel Dispatchers

**Problem A.** `McpClientManager.connect_all` connects servers strictly
serially with no timeout (`src/openharness/mcp/client.py:45-59`): cold start is
the *sum* of handshakes and one hung server blocks startup (and every headless
`resume`, which rebuilds the bundle) forever.

**Design A.** `asyncio.gather` over servers, each wrapped in
`asyncio.wait_for(timeout=settings.mcp_connect_timeout_s, default 15)`;
timeout/failure marks the server `failed` with detail (status surface already
exists, `list_statuses`) and never blocks the session. Re-resolve lazily on
first use of that server's tools (retry hook already exists in the manager).

**Problem B.** One global outbound dispatcher serializes all channel traffic
(`src/openharness/channels/impl/manager.py:209-238`): a 4 s Telegram
flood-control retry or 30 s SMTP timeout head-of-line-blocks every other
channel and all gateway progress hints.

**Design B.** Per-channel outbound queues + one dispatcher task per channel
(spawned on channel start, joined on stop). The `MessageBus` keeps its inbound
single queue; outbound `publish` routes to the channel's queue. Order is
preserved per channel (the only ordering that matters). Bounded queue
(e.g. 1000) with drop-oldest-progress-hint policy under backpressure so chat
replies are never dropped in favor of hints.

**Plus (channel hygiene, same workstream):**
- Email: persistent IMAP connection across polls with reconnect-on-error;
  reuse one SMTP connection per dispatch burst (`channels/impl/email.py:175-246`).
- Matrix: resync without `full_state=True` after transient errors
  (`channels/impl/matrix.py:448-455`).

**Files.** `src/openharness/mcp/client.py`,
`src/openharness/channels/impl/manager.py`, `src/openharness/channels/bus/queue.py`,
`channels/impl/{email,matrix}.py`, `src/openharness/config/settings.py`.

**Acceptance criteria.**
- 4 mock MCP servers with 1 s handshakes connect in ~1 s total; a never-answering
  server yields `failed` status after the timeout and the session starts.
- A blocked send on channel A does not delay channel B (test with two fake
  channels, one sleeping).
- Per-channel message order preserved under concurrency.

**Effort.** ~2–3 days.

---

## Workstream 6 — Quick Wins (one batch, each independently small)

| # | Fix | Where | Acceptance |
|---|---|---|---|
| 6.1 | Remove ~50 ms/turn compaction-poll floor: wait on the task and queue together instead of `wait_for(get(), 0.05)` then `task.done()` | `engine/query.py:686-693` | No fixed 50 ms delay on the no-compaction path (timed test) |
| 6.2 | Codex client: one shared `httpx.AsyncClient`, JWT/account-id decoded once per token | `api/codex_client.py:276,61` | Single connection reused across turns (transport mock counts connects) |
| 6.3 | Memory auto-extract: fire-and-forget background task (pattern of `_schedule_auto_dream`), `memory.extract_model` setting for a cheap model, skip when no new messages since last extraction | `engine/query_engine.py:275-278`, `services/memory_extract/` | Turn completion not delayed by extraction; extraction uses configured model |
| 6.4 | Offload artifact write of oversized tool output to `asyncio.to_thread` | `engine/query.py:538,515` | 5 MB output does not stall concurrent tool siblings (timed test) |
| 6.5 | Cache `tool_registry.to_api_schema()`; invalidate on `register()` | `tools/__init__.py`, `engine/query.py:734` | One schema generation per registry mutation, not per turn; also stops the first call defeating the lazy registry |
| 6.6 | Incremental token estimation: per-message count cache (append-only between compactions) | `services/compact/__init__.py:1106-1140` | O(new messages) per turn, full recount only after compaction |
| 6.7 | OpenAI client: list-append + `"".join` for streamed tool args / reasoning (match Codex client) | `api/openai_client.py:364,391` | No O(n²) accumulation (unit test with 2K chunks) |
| 6.8 | Cap `compact_checkpoints` (keep last 10) and stop spreading carryover metadata into hook env payloads | `services/compact/__init__.py:208-211,1186,1396`, `hooks/executor.py:93-97` | Hook env size bounded across a session |
| 6.9 | Mailbox `mark_read`: direct `glob(f"*_{message_id}.json")` instead of parse-all; prune `read/` past N files | `swarm/mailbox.py:196-226` | O(1) mark_read (test with 500 archived messages) |
| 6.10 | Close the previous Anthropic client on auth refresh | `api/client.py:157-163` | No FD growth across rotations |
| 6.11 | Backend host: plain-dict `json.dumps` for stream deltas; emit tasks/status snapshots only on change | `ui/backend_host.py:858-868,346-347` | ≤ 1 snapshot frame per actual state change |
| 6.12 | Auto-dream: read session_id from filename instead of parsing files; retain task reference; deregister completion listeners | `autodream lock.py:105-138`, `service.py:244,313` | 10-min scan does zero JSON parses; no listener growth |

**Effort.** ~3–4 days for the batch; each item is independently committable and
revertable.

**Deliberately deferred** (documented, not planned here): mid-stream retry
resume (`client.py:169-196` — needs provider-side support to do properly; the
current full-replay is correct, just wasteful), per-delta UI buffering, and the
command-registry/vendor-SDK import split — the latter belongs to
[release-architecture-hardening](release-architecture-hardening.md), with one
correction from this review: **the 1.4 s import is ~0.8 s eager `anthropic` +
`openai` SDK imports via `openharness.api.client`; defer those into client
constructors or the registry split will not reach the < 300 ms budget.**

---

## Sequencing and Release Mapping

```
0.1.13  WS6 (quick wins) + WS1 (persistent workers)        ~1 week
0.1.14  WS3 (config caching) + WS2 (prompt caching)        ~1.5 weeks   WS2 depends on WS3 prompt stability + 6.5
0.1.15  WS4 (persistence v2) + WS5 (MCP/channels)          ~1.5 weeks   WS4 isolated behind format flag
```

Dependencies: WS2 → (WS3 system-prompt stability, 6.5 schema cache). WS1 ↔ WS4
interact only at worker snapshot save/restore (works with either storage
format). Everything else is independent.

## Verification Strategy

1. **Extend `scripts/measure_startup.py`** with runtime probes:
   per-line assembly time (mock client, 10 lines, report p50), bytes written
   per line (tmpdir), MCP connect wall-clock (mock servers), worker follow-up
   latency. Record results in release notes per the existing
   performance-guardrails plan.
2. **Budgets** (enforced once stable, same policy as
   release-architecture-hardening): per-line assembly < 5 ms; bytes/line
   < 100 KB at 200 messages; worker follow-up < 50 ms; no fixed per-turn sleeps.
3. **Real-API validation** for WS2 via the harness-eval skill: assert
   `cache_read_input_tokens` ratio on a 10-turn session.
4. **Regression gate**: full unit suite + the 34-check headless E2E harness
   (from the v0.1.10 release work) must pass at every workstream merge.

## Non-Goals

- No public protocol changes (headless `protocol_version` stays 1; only
  additive `usage` fields).
- No new runtime dependencies.
- No multi-tenancy work (tracked separately in release-architecture-hardening).
- No provider-side streaming-resume support.
