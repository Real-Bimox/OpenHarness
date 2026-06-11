# Proposal: conversation-search

## Status

| Field | Value |
|---|---|
| Status | IN PROGRESS |
| Proposal branch | `proposal/learning-search-resilience` |
| Owner | Bahram Boutorabi |
| Created | 2026-06-10 |
| Source study | hermes-agent @ `298bb93d3` (`hermes_state.py`, `tools/session_search_tool.py`) |
| Related | [performance-hardening-roadmap](performance-hardening-roadmap.md), report `docs/reports/openharness-vs-hermes-agent.md` |

## What hermes-agent does (the source capability)

hermes stores every message of every session in one SQLite database
(`~/.hermes/state.db`, WAL, schema v15). Two FTS5 virtual tables (unicode61 +
trigram for CJK) are maintained synchronously by six SQL triggers that index
`content || tool_name || tool_calls` per message. A `session_search` agent
tool exposes four shapes, inferred from the arguments (no mode parameter):

- **discover** — FTS5 query (bm25 rank, `snippet()` with markers), up to 50
  raw hits deduplicated per session-lineage root, each returned with a ±5
  anchored message window plus 3-message session "bookends" at both ends;
- **scroll** — re-anchor on any previously seen message id, window 1–20;
- **read** — whole session, head 20 + tail 10 when longer than 30;
- **browse** — recent sessions with previews when no query is given.

Lineage chains (sessions split by compaction) are walked to a root so the
same conversation never appears twice and the *active* conversation is never
returned. Query text from the model is sanitized by regex surgery before
reaching FTS5; CJK queries route to the trigram table or a LIKE fallback.
Robustness machinery worth copying: WAL with `journal_mode=DELETE` fallback
on network filesystems, `BEGIN IMMEDIATE` + jittered busy retries, FTS5
module probing with graceful degradation, segment `optimize`/`VACUUM`
maintenance, 90-day auto-prune. The tool costs zero LLM calls.

Weaknesses found in their implementation (documented in the source study and
deliberately fixed here): no output budget (a 200 KB message inside a window
goes to the model untruncated); no redaction (secrets in tool output are
permanently indexed); window counters are slice-local but documented as
global; duplicated dispatch paths drifted (their second path drops the
`profile` parameter — a live bug); the regex query sanitizer has silent
empty-result edge cases; two full FTS indexes double the write/storage cost;
sqlite_master surgery is needed for corruption because the DB is the only
copy of the data.

## OpenHarness design

### The structural advantage we exploit

OpenHarness's source of truth is the JSON session snapshots
(`services/session_storage.py`). The search index is therefore a **derived
cache**, not primary storage. This removes hermes's hardest problems wholesale:
corruption recovery is "delete the file and rebuild from snapshots" (no
`sqlite_master` surgery), schema migration is "bump the version and rebuild",
and the index can be regenerated at any time with `oh sessions reindex`.

### Index

`services/conversation_index.py`, DB at `<data_dir>/conversation_index.db`:

- `sessions(session_id PK, project, source, model, title, started_at,
  last_active, message_count, indexed_count)` and
  `messages(id INTEGER PK AUTOINCREMENT, session_id, project, snapshot_idx,
  role, ts, body, tool_name)`.
- One FTS5 table, **external content** (`content='messages'`), columns
  `body` and `tool_name` weighted 10:2 at query time via `bm25()`, kept in
  sync by insert/delete/update triggers. Tokenizer `trigram` — one index
  serves ASCII, CJK, and substring matching, replacing hermes's
  dual-table + CJK-routing maze. Queries shorter than 3 characters use a
  LIKE fallback (trigram minimum). Honest cost: trigram indexes are larger
  than unicode61; acceptable at local scale and it is the simpler correct
  design.
- **Redaction before indexing**: the team-memory secret patterns
  (`memory/team.py SECRET_RULES`) are applied as substitutions to every
  indexed body. Indexed bodies are also capped (8 KB per message) — the
  index serves discovery; full text stays in the snapshots.
- Indexing is incremental at snapshot-save time (both `services/` and
  `ohmo/` storage call it, best-effort, already off the event loop): only
  messages beyond `indexed_count` are added; if a snapshot shrinks
  (compaction rewrote history) the session is reindexed from scratch.
- Ported from hermes because they are right: WAL with DELETE fallback on
  `locking protocol` errors, `BEGIN IMMEDIATE` + jittered busy retry, FTS5
  probe with graceful no-FTS degradation, id-ordered (not timestamp-ordered)
  windows.

### Tool — `session_search` (tool #43)

Same four inferred shapes and parameter names as hermes (`query`, `limit`
1–10, `sort`, `session_id`, `around_message_id`, `window` 1–20,
`role_filter`), with these deliberate differences:

1. **Output budget** — every returned message body is truncated to 2,000
   chars with `truncated: true` and the message id for scroll-in; total
   response capped. (Fixes hermes weakness #1.)
2. **Honest counters** — `messages_before/after` are real `COUNT(*)` values,
   and the misleading `sessions_searched` field is dropped.
3. **Parser-based query sanitizer** — model input is tokenized and re-emitted
   as a guaranteed-valid FTS5 expression (every term quoted; only
   `AND/OR/NOT` and trailing `*` honored); a query that sanitizes to nothing
   returns an explanatory error, not fake "no matches".
4. **`project` scope** — defaults to the current project; `project: "all"`
   searches every project (OpenHarness's analog of hermes's cross-profile
   read, which their second dispatch path silently broke).
5. Single dispatch path through the standard registry (their dual-path drift
   bug is structurally impossible here).
6. Current-session exclusion via the engine's `session_id` tool metadata.

### Exposure surfaces

- **Agent tool**: `session_search` (read-only classification).
- **Headless JSONL**: `{"type":"search_sessions", "query":..., ...}` handled
  inline by the reader (like `list_sessions`), response
  `{"type":"session_search_results", ...}`. Additive; protocol stays v1.
- **CLI**: `oh sessions list|search|reindex`.
- **MCP**: exposed by the F4 `oh --mcp-serve` server as `search_sessions`.

### Settings

`conversation_index_enabled: bool = True` (kill switch; when off, the tool
and surfaces report the index is disabled rather than erroring).

## Capability parity statement (honest)

- Equal: all four search shapes, FTS5 ranking + snippets, anchored windows +
  bookends, current-conversation exclusion, zero LLM cost, CJK support,
  graceful no-FTS degradation, WAL/locking robustness.
- Better: derived-cache rebuildability, redaction, output budgets, honest
  counts, parser-based sanitization, per-project scoping, single dispatch.
- Not carried over, with reasons: session *lineage* walking — OpenHarness
  compaction rewrites history inside one session id rather than splitting
  sessions, so lineage dedup is unnecessary by construction; hermes's
  90-day auto-prune (the index is small and rebuildable; revisit with WS4
  retention); cross-*profile* search (OpenHarness has no profile homes —
  per-project scoping covers the real use case).
