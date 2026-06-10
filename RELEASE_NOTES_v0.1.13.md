# v0.1.13 — Persistent Task Workers and Per-Turn Performance

OpenHarness v0.1.13 ships the first two workstreams of the
[performance-hardening roadmap](docs/proposals/performance-hardening-roadmap.md):
persistent background agent workers (WS1) and a twelve-item batch of per-turn
latency and efficiency fixes (WS6) identified by the v0.1.12 architectural
review.

## Highlights

- **Persistent task workers**
  - `--task-worker` processes serve every coordinator follow-up over one
    stdin loop until EOF, a terminating command, or the new
    `task_worker_idle_timeout_s` setting (default 600 s) — follow-up messages
    no longer pay a multi-second process rebuild.
  - The task manager injects a stable `OPENHARNESS_TASK_SESSION_ID` per agent
    task; workers save and restore their conversation under it, so crash or
    idle restarts resume with full context instead of an empty conversation.
  - Coordinator drain wakes on task completion listeners instead of 100 ms
    polling; swarm permission polling backs off 0.2 s → 2 s.

- **Per-turn latency**
  - Removed a fixed ~50 ms per-turn floor from the compaction progress poll.
  - Tool API schemas are cached and regenerate only when a tool is
    registered, instead of pydantic schema generation for ~40 tools on every
    model turn.
  - Autocompact token estimation is incremental: only newly appended messages
    are counted each turn.
  - Oversized tool-output artifact writes moved off the event loop so
    concurrent sibling tools and streaming never stall on disk.

- **Background memory work**
  - Durable memory extraction runs as a background task (one in flight,
    skipped when the conversation has not grown) instead of delaying every
    turn's completion by a full model call, and can target a cheaper model
    via the new `memory.extract_model` setting.
  - Session-memory checkpoint writes moved off the event loop; auto-dream
    periodic scans no longer parse every session snapshot.

- **API client efficiency**
  - `CodexApiClient` reuses one connection pool and one decoded-JWT header
    set across turns (was a TLS handshake + JWT decode per request).
  - The OpenAI-compatible client accumulates streamed text/reasoning/tool
    arguments linearly instead of quadratically.
  - The Anthropic client closes the replaced connection pool on auth refresh.

- **Bounded session state**
  - `compact_checkpoints` is capped and excluded from hook payloads, keeping
    hook subprocess environment size bounded across long sessions.
  - Swarm mailbox `mark_read` targets messages by filename id instead of
    parsing the whole archive under the write lock; the React backend host
    only ships tasks/status snapshot frames whose content changed.

## Verification

- 1197 unit/integration tests pass (12 new for worker persistence, restore,
  idle timeout, and listener-driven drain).
- Headless protocol harness: 34/34 checks against real `oh --headless`
  subprocesses.
- Worker harness: one real `oh --task-worker` process answered two messages
  with shared context in 2.2 s, and a restarted worker restored its
  conversation from the session snapshot.

## Next

The roadmap continues with config/prompt caching plus prompt-caching
breakpoints (0.1.14) and append-only session persistence plus parallel MCP
connect / per-channel dispatchers (0.1.15).
