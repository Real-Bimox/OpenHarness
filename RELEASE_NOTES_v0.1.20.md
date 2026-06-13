# v0.1.20 — Append-Only Session Persistence (v2)

A performance and durability release for session persistence (roadmap WS4). It
replaces the O(n²) full-history rewrite with an append-only transcript and a
trusted index, behind a `session_storage_format` setting (default `v2`, with a
`v1` revert switch). Every legacy session file remains readable, and the public
loader dict shapes are unchanged.

## Added

- **Append-only v2 session format.** Each session writes an append-only
  `session-<id>.jsonl` transcript (delta appends — only the new messages each
  turn) plus a small `session-<id>.head.json` (model, system-prompt hash, usage,
  tool metadata, counts) and a pointer `latest.json` (`{"session_id": ...}`).
  Per-turn write cost drops from rewriting the whole history to appending the
  delta.
- **Trusted session index + one-time backfill.** The index is trusted whenever
  present; a lazy one-time backfill migrates legacy files, and on-disk sessions
  the index is missing are surfaced and persisted under a store lock.
- **Retention pruning on save.** `session_retention_max_files` (default 50) and
  `session_retention_max_age_days` (default 30) prune old sessions oldest-first.
  The active session, the `latest.json`-pointed session, and any recently-touched
  session are never pruned. `0` disables each limit.
- **`session_storage_format` setting** (default `"v2"`; set `"v1"` to revert new
  writes). Legacy v1 `latest.json` / `session-<id>.json` files load unchanged via
  an on-disk format sniffer — the setting gates writes, never reads.
- Mirrored in `ohmo` session storage (same transcript/head/pointer primitives,
  the `session_key` pointer, and head-less recovery).

## Crash safety & compatibility

- The transcript append is the single per-turn durability point (one fsync per
  turn); the head, pointer, and index are atomic-rename only and are rebuilt on
  the next save. A crash mid-append recovers to the last complete record; a lost
  head recovers the history off the durable transcript.
- No interface break: the public loader dict shapes are unchanged, and every
  legacy v1 file stays readable.
- Forward-only: `session_storage_format=v1` reverts new writes while existing v2
  sessions remain readable.

## Verification

- Full Python suite green (1365 passed, 7 skipped); `ruff check src tests scripts`
  clean. Built test-first across the change, with behavioral tests for the
  cursor/fingerprint, locking, retention, crash-recovery, and format-consumer
  paths. Cleared an independent code review plus an owner review.
- Design and canonical contracts: `docs/proposals/session-persistence-v2-plan.md`
  (sections C.1–C.9).
