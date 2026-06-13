# Session Persistence v2 (WS4) Implementation Plan

> **Status: APPROVED** (owner, 2026-06-13). design-quality-gate (Tier T2) **CLEARED & implementation-ready** — 14 original + 4 re-review findings (R-001..R-004) + 4 post-merge format-consumer findings (PMR-001..004) resolved across **three** independent adversarial re-gates; the format-change blast radius is enumerated in **C.9** and covered by **Phase 7**. The build has not been started.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the O(n²) full-history rewrite in OpenHarness session persistence with an append-only per-session transcript plus a trusted index and a retention policy, behind a `session_storage_format=v2` setting, while keeping every legacy file readable forever and every loader's public dict shape unchanged.

**Architecture:** Each session writes an append-only `session-<id>.jsonl` transcript (one JSON line per message, deltas-from-last-persisted-index appends) plus a small `session-<id>.head.json` (model, system-prompt hash + rebuild inputs, usage, tool_metadata, message_count) rewritten per turn; `latest.json` becomes a `{"session_id": ...}` pointer. The index is trusted whenever it exists (one-time backfill migrates legacy files, stale entries compacted on write). All new writes are gated behind `session_storage_format=v2` (default on, revert switch to `v1`), and a format sniffer keeps legacy `latest.json` / `session-*.json` readable indefinitely. The transcript append gets one fsync/turn; head/index use atomic-rename without per-write fsync.

**Tech Stack:** Python, pytest, pydantic; existing OpenHarness persistence modules.

---

## File Structure

| File | Create / Modify | Responsibility |
|---|---|---|
| `src/openharness/config/settings.py` | Modify (add fields to `Settings`, `settings.py:614-677` block) | New `session_storage_format: str = "v2"`, `session_retention_max_files: int = 50`, `session_retention_max_age_days: int = 30` fields. |
| `src/openharness/utils/fs.py` | Modify (`atomic_write_bytes`, `fs.py:39-78`; new `append_jsonl_line`, `read_jsonl_complete_lines`) | Add a parent-dir fsync to `atomic_write_bytes` (close the documented gap at `fs.py:57-62`); add an append-with-fsync helper and a crash-safe JSONL reader that stops at the last complete line. |
| `src/openharness/services/session_format.py` | **Create** | Format sniffer (`detect_session_format`), v2 transcript read/write primitives (`append_messages_to_transcript`, `write_head`, `read_head`, `load_v2_snapshot`, `rewrite_transcript`), the system-prompt hash helper (`system_prompt_fingerprint`), and the message-history content fingerprint (`fingerprint_messages`) used to detect in-place compaction (R-001). Pure functions, no settings access. |
| `src/openharness/services/session_storage.py` | Modify (`session_storage.py:114-310`) | Route `save_session_snapshot` through v1 or v2 by setting; make `load_session_snapshot` / `load_session_by_id` resolve the `latest.json` pointer and sniff format; make `list_session_snapshots` **surface on-disk sessions the index is missing via the sniffer (v1 `.json` AND v2 head/transcript), not only when the index is empty (PMR-002)**; add retention pruning; v2 pointer resolution + format-sniffing load. |
| `src/openharness/services/session_backend.py` | Modify | Add `export_snapshot_json(cwd, dest)` to the protocol + impls (OpenHarness + ohmo): writes a **full v1-shaped** snapshot via the v2-aware loader, so `/session tag` exports a real snapshot, not a pointer (PMR-001). Resume shape otherwise unchanged (Task 14 no-op shape test still holds). |
| `ohmo/session_storage.py` | Modify (`ohmo/session_storage.py:92-209`) | Apply the same head+append pattern via the shared `session_format` primitives; keep `session_key` plumbing and the `latest-<token>.json` pointer. **Mirror the PMR-002 missing-entry/sniffer listing fix in ohmo's `list_snapshots` (`:179`)** — it has the same legacy-`.json`-only glob. |
| `src/openharness/services/conversation_index.py` | Modify (`rebuild`, `conversation_index.py:343-361`) | `rebuild()` globs only `*/session-*.json`, so it indexes **zero** v2 sessions and the wipe-then-rebuild empties search (PMR-003). Make it enumerate v1 **and** v2 sessions per project and reassemble each full payload through the v2-aware storage loader before `index_snapshot`. |
| `src/openharness/services/autodream/lock.py` | Modify (`list_sessions_touched_since`, `lock.py:104-134`) | Touched-since scan globs only `session-*.json`, so under v2 it returns `[]` and auto-dream silently never triggers (PMR-004a). Scan both `session-*.json` (v1) and `session-*.jsonl` (v2 transcript — the per-turn fsync'd artifact), by mtime, deduped by id. |
| `src/openharness/services/autodream/prompt.py` | Modify (`prompt.py:80`) | The consolidation prompt tells dream workers to inspect `session-*.json`, which do not exist under v2 (PMR-004b). Reference both shapes (v2 `session-*.jsonl` transcripts + legacy `session-*.json`). |
| `src/openharness/commands/registry.py` | Modify (`/session tag`, `registry.py:914`; `/session ls`, `:892`) | `/session tag` does `shutil.copy2(save_snapshot()→latest.json, <tag>.json)` — under v2 that copies a pointer (PMR-001); switch it to `export_snapshot_json`. `/session ls` (`iterdir`) is cosmetic-only under v2 (P3); optionally label artifact types. |
| `tests/test_utils/test_fs.py` | Modify | Tests for parent-dir fsync, `append_jsonl_line`, and crash-safe `read_jsonl_complete_lines`. |
| `tests/test_services/test_session_format.py` | **Create** | Unit tests for the sniffer, transcript primitives, hash helper, and crash-consistency (truncated mid-line). |
| `tests/test_services/test_session_storage.py` | Modify | v2 save/load round-trip, pointer `latest.json`, index-trust + backfill, retention, byte-budget, legacy-format fixtures, format-flag revert. |
| `tests/test_ohmo/test_ohmo_session_storage.py` | Modify | v2 ohmo save/load round-trip, legacy fixture, `session_key` pointer under v2. |

---

## v2 Storage Contracts (canonical)

> These are the canonical invariants for v2. Every task body below implements them and cites this section by name; no rule is restated as an independent source. Added in the gate-revision pass to resolve **P1-001, P1-003, P1-004, P1-005, P2-002, P2-003, P2-004, P2-005** (see the Quality Gate section for the finding-by-finding mapping).

### C.1 Durability / fsync policy — canonical [P2-002]

The transcript `session-<id>.jsonl` is the **only durable artifact and the commit point** for a turn. Everything else is derived from it and reconstructible.

- **One fsync per turn.** The append path fsyncs the *final* appended line; the compaction path fsyncs the single full rewrite. Parent-directory fsync (Task 2) makes the create/rename itself durable.
- **Derived artifacts are rename-only.** `session-<id>.head.json`, the `latest.json` pointer, and `sessions-index.json` are written with `atomic_write_text(..., fsync=False)` — atomic rename, no per-write fsync. A crash loses at most derived metadata, which is rebuilt on the next save (C.4) or tolerated on load (C.6).
- **ohmo mirrors this policy verbatim.** ohmo's per-session transcript is its durable artifact (one fsync/turn); its head / `latest-<token>.json` pointer / index are rename-only. This single statement is canonical for both apps — Task 15 references it rather than restating it (resolves P2-002: the fsync policy is no longer stated only for openharness).

### C.2 Writer authority & concurrency — canonical [P1-003]

v1 needed no lock because every save was a full **idempotent rewrite**. v2 introduces (a) an *append* (not idempotent without a correct cursor — see C.4) and (b) a *read-modify-write* of the shared index plus a retention *delete*. Per-session artifacts stay single-writer by design; the store-wide artifacts are serialised with the same `exclusive_file_lock` (`utils/file_lock.py`) the rest of the codebase already uses for shared JSON registries (settings, auth, memory, cron, swarm mailbox).

| Artifact | Scope | Writers | Concurrency | Guard |
|---|---|---|---|---|
| `session-<id>.jsonl` + `session-<id>.head.json` | one session id | the single process that owns `<id>` | **single-writer by design** — WS1 workers each get a distinct per-task session id; the foreground/interactive process owns its own id | none (per-id ownership). Same-id concurrent writers are **out of contract** — see the WS1↔WS4 note. |
| `latest.json` pointer | project dir | any saver | multi-writer, last-writer-wins | atomic rename only (the race is benign — "latest" is by definition whoever saved last) |
| `sessions-index.json` | project dir | every saver (v1 **and** v2), backfill, retention | **multi-writer read-modify-write** | **required:** `exclusive_file_lock(session_dir / ".sessions.lock")` |
| retention prune (unlinks whole sessions + rewrites the index) | project dir | any saver | multi-writer | the **same** `.sessions.lock` — prune and the index update share one critical section |

**Critical-section rule.** The transcript append + head write + pointer write happen **outside** the lock (per-id, atomic, and the slow fsync must never hold a store-wide lock). The index read-modify-write **and** the retention prune happen **inside one** `with exclusive_file_lock(session_dir / ".sessions.lock"):` block, acquired **once** per save. The index/retention helper *cores* assume the lock is held and never re-acquire it — `flock` is per-open-description, so a second acquisition in the same process would self-deadlock. (Concretely: a lock-free `_update_session_index_unlocked` / `_prune_retention_unlocked` core, with the public entry points or the save path acquiring the lock once around both.) This serialises concurrent savers — e.g. two WS1 workers plus a foreground save — on the only genuinely shared mutable state. Putting the lock on the index resource also closes a latent v1 same-file race for free.

**Schema versioning (hot-reload / mixed-format safety).** `sessions-index.json` already carries `"version": 1`; the v2 head carries the v2 shape (presence of `.head.json` + `.jsonl` *is* the v2 on-disk signal — see C.3). The active format is decided **per save** from `session_storage_format`; a process never changes an id's format mid-write. A session started under v1 and continued under v2 produces the **CONFLICT** state (C.3), resolved by v2-precedence — it is not an undefined concurrent-writer state. Two processes writing *different* ids under different formats is fine (each owns its own files); two processes writing the *same* id is the out-of-contract case above.

**WS1↔WS4 note** (the roadmap's `WS1 ↔ WS4` dependency). The contract holds because a worker and the foreground never share a session id, so their transcripts never collide and only the store-wide index/pointer are contended — which the lock covers. If a future change lets two writers target one session id, that violates this contract and would require a per-session transcript lock; that is explicitly **out of scope** and flagged here so the dependency is visible at merge time.

### C.3 Format & lifecycle state machine — canonical [P1-005]

**(A) On-disk format detection, per id.** `detect_session_format` reads on-disk *shape only* — never the setting (the setting gates writes; Design decision 2). It returns one of:

| State | On disk | Load behavior |
|---|---|---|
| `ABSENT` | no session files | new session |
| `V1` | `session-<id>.json` only | load the full v1 file |
| `V2` | `.head.json` + `.jsonl`, no `.json` | load head + transcript |
| `V2_HEADLESS` *(named halt)* | `.jsonl` present, `.head.json` missing/corrupt — the P1-001 crash state | load the transcript directly; head rebuilt on next save. Safe because the cursor comes from the transcript (C.4) |
| `TRUNCATED_TAIL` *(named halt)* | final transcript line incomplete (crash mid-append) | `read_jsonl_complete_lines` drops the partial line; recover to the last complete record |
| `CONFLICT` *(named branch — v1+v2 same id)* | both `session-<id>.json` **and** (`.head.json`/`.jsonl`) exist | **precedence: v2 wins** (the `.json` is a pre-migration leftover). A v2 save for an id that has a legacy `.json` removes the `.json` after the v2 write succeeds (supersede); read always prefers v2. Resolved by the migration contract (C.7) |

**(B) Transcript lifecycle, per session.**

```
EMPTY ──first append──▶ APPENDING(N)
APPENDING(N) ──turn (append-only)──▶ APPENDING(N+Δ)        cursor = live-record count read from the transcript
APPENDING(N) ──compaction (len(messages) < N, or any non-append edit)──▶ REWRITING ──▶ APPENDING(M)
                                                            marker line + post-compaction history; cursor = post-marker count
APPENDING/any ──retention prune──▶ REMOVED (whole id)       unless protected: active id, latest-pointed id, or within the recency window (C.8)
```

**Cursor invariant (the spine of correctness).** The append cursor is *always* the transcript's post-last-marker live-record count — **never `head.message_count`**. This one rule makes `APPENDING` crash-safe (`V2_HEADLESS` recovers with no duplication) and removes the head from the correctness path entirely. See C.4 and the Task 8 / P1-001 edit.

### C.4 Save partial-failure matrix — canonical [P1-001]

The v2 save runs these steps. Step 1 (the transcript fsync) is the **commit point**; steps 2–6 write only derived state, so a crash after any of them is recoverable with **no duplication and no loss**:

| # | Step | Durable? | Crash-after state | Recovery |
|---|---|---|---|---|
| 1 | Append delta to `.jsonl` (or full rewrite on compaction) | **yes (fsync)** | transcript has the new messages; head/pointer/index not yet updated | next save: cursor read from transcript → correct delta; load: `V2_HEADLESS` reads the transcript |
| 2 | Rewrite `head.json` | no (rename) | head missing or stale | rebuilt next save (count from transcript); load tolerates a missing head |
| 3 | Write `latest.json` pointer | no (rename) | pointer stale (points at the prior session) | benign; corrected next save; load fallback (C.6) |
| 4 | *(under `.sessions.lock`)* update `sessions-index.json` | no (rename) | index missing this id | session still loads by id from the transcript; backfill / next save re-adds it |
| 5 | *(under `.sessions.lock`)* retention prune | unlink + rename | partial delete | prune is idempotent; re-runs next save |
| 6 | conversation index (best-effort) | swallowed | — | independent; never blocks a save |

**The cursor fix.** `last_persisted` MUST be derived from the transcript — the count of live records returned by reading the transcript post-last-marker — **not** from `head.message_count`. This is what makes the matrix hold; without it, a lost head (step 2 crash) re-appends already-durable messages → duplicate history on resume. Implemented in Task 8 (P1-001).

### C.5 Compaction-marker record schema — canonical [P2-003]

The compaction marker shares the `.jsonl` line namespace with message records, so it must be **unambiguously typed**. A record is a marker **iff** it has the `__compacted_at__` key **and no `role` key** (message records always carry `role`). `load_v2_snapshot` treats only such records as markers; a message whose *content* merely contains the string `__compacted_at__` (nested inside content blocks) is never mistaken for a marker. Code edit + collision test in Task 7 (P2-003).

**Rewrite trigger — canonical [R-001].** A save must rewrite the transcript (marker + full history) **whenever the durable history is stale relative to the in-memory list**, and append only otherwise. Staleness is decided by a **content fingerprint of the durable prefix**, not the message count: the engine compacts *in place* (`microcompact_messages` clears old tool-result bodies, `try_context_collapse` collapses text — both keep the same count while changing content, verified at `compact/__init__.py:854` / `:348`), so `last_persisted > len(messages)` (count shrink) misses them and leaves stale bytes on disk. The canonical trigger is: `compacted = len(messages) < last_persisted OR fingerprint_messages(messages[:last_persisted]) != persisted_prefix_fingerprint`. The fingerprint (`session_format.fingerprint_messages`) and the cursor are seeded together from the transcript on first use and maintained in-process by the single writer (C.2/C.4). This rule is owned identically by `_save_session_snapshot_v2` and the ohmo twin (Task 15). A false positive (spurious rewrite) is merely slower, never lossy; the strong content hash makes false negatives (missed staleness) negligible — closing R-001 at the root.

### C.6 Read fallback & pointer precedence — canonical [P2-005]

`load_session_snapshot` resolves `latest.json` as:

1. v2 pointer (`{"session_id": x}`) → load session `x` through the C.3 state machine.
2. legacy full payload (has a `messages` key) → load it directly (legacy path).
3. pointer present but the target head is missing/corrupt → fall through to the transcript (`V2_HEADLESS`); if the transcript is also absent → return `None`.
4. pointer target absent **and** a legacy `session-<id>.json` exists for that id → `CONFLICT` precedence (v2 first, then the `.json`).

A missing/corrupt index never blocks a load: `load_session_by_id` sniffs on-disk shape directly, and `list_session_snapshots` falls back to globbing (existing behavior). Tests cover the v2-pointer, legacy-full, and missing-head cases.

**Head-less degradation contract — canonical [R-002].** In the `V2_HEADLESS` recovery (head lost in the crash window, transcript durable), the **message history is recovered in full** from the transcript; the **head-only fields degrade deterministically** because they were never fsync'd:

- `model` is **omitted** from the recovered payload. Resume resolves the model as `explicit --model` → else the runtime's configured default (`build_runtime` passes `model=None` to `merge_cli_overrides`, which **drops `None`** so `settings.model` stands — `settings.py:935`). It is **not** null and does **not** crash; the only effect is that a head-less resume taken with no `--model` continues on the *current configured* model rather than the model the head recorded (a benign drift, since explicit `--model` always wins).
- `usage` resets to zero — cumulative token accounting under-reports until the next save rewrites the head.
- `tool_metadata` is empty — carryover/session-memory context for the session is not restored.
- `created_at` resets to *now* on the next save after a head-less recovery (the prior head carried it). This only shifts retention's sort/display and makes the session marginally *safer* from age-pruning — never less safe.

This degradation is the **defined contract** for the rare lost-head window, not an error path: history fidelity is preserved; head-derived session config falls back to runtime defaults. **ohmo mirrors it** (R-002a) — `_load_ohmo_v2_payload` recovers head-less off the transcript, injecting the constant `app: "ohmo"` and (on the session-key lookup) re-injecting `session_key`, which the caller knows. Restoring `model`/`usage`/`tool_metadata` from the surviving index entry is a possible future strengthening but is **deferred** (right-sized: the window is narrow and the degradation is non-fatal).

### C.7 Index backfill migration contract — canonical [P1-004]

- **Trigger:** lazy and one-time — when the index is absent or missing entries for on-disk sessions (driven from `list_session_snapshots` / load).
- **Idempotent:** re-running yields the same index. Entries are keyed by `session_id` and re-derived from each session's head (v2) or full file (v1); existing entries are never duplicated.
- **Partial-state safe:** the whole index is written once, under `.sessions.lock`, via atomic rename — a reader sees either the old or the new index, never a torn one. A crash mid-backfill leaves the old index; the next trigger re-runs.
- **Dual-format-same-id:** if both a v1 `.json` and a v2 head exist for one id, the entry is derived from the v2 head (v2-wins, per C.3); the stale `.json` is left in place (read prefers v2) and removed on the next v2 save (supersede).
- **Forward-only:** there is no down-migration. The revert switch (`session_storage_format=v1`) stops new v2 *writes*; existing `.jsonl`/`.head` sessions stay readable because the sniffer is format-agnostic. Backfill never deletes data — retention (C.8) is the only deleter, and it runs separately under the same lock.

### C.8 Retention safety — canonical [P2-004]

Retention runs on save, **inside the `.sessions.lock` critical section** it shares with the index update, oldest-first by `created_at`. It never deletes: the session being saved (active id), the `latest.json`-pointed id, or any session whose transcript `mtime` is within a **recency window** sized to the concurrent-writer horizon — `max(2 × task_worker_idle_timeout_s, 60s)`, so a worker actively appending another id is never pruned out from under itself (R-003: the window tracks the idle timeout, not a fixed hour). `0` disables each limit. Because the protected window defers pruning of recent sessions, **`session_retention_max_files` is a soft hint, not a hard cap**: the live count may exceed it during a burst of recent sessions and converges to it as they age past the window; `session_retention_max_age_days` bounds the long tail. Because prune holds the lock, it cannot race a concurrent saver's index update or delete a session another saver is mid-append on.

### C.9 Format-consumer inventory — canonical [PMR-001..004]

Changing the on-disk session shape (v1 full `session-<id>.json` / full `latest.json` → v2 `session-<id>.jsonl` + `.head.json` / pointer `latest.json`) breaks **every** code path that reads sessions *by file shape* rather than through the storage loader. The first gate verified the storage layer's internal correctness but **did not enumerate these consumers** — the post-merge review (PMR-001..004) found four live breaks. This contract is the **authoritative inventory** of every session-shape consumer and how each behaves under v2. Any new consumer MUST be added here and routed through the loader or made format-aware. Two categories:

**(A) Through the storage loader — format-agnostic by construction (no change needed).** Callers that obtain a session as a *dict* from `load_session_snapshot` / `load_session_by_id` / `load_latest` / `load_latest_for_session_key` / `list_session_snapshots` and read keys off it. The loader is made v2-aware (Tasks 9, 15), so these are correct automatically. Verified clean: CLI resume (`cli.py:3093-3187`), `/resume` (`registry.py:813-858`), runtime save (return discarded, `runtime.py:629`), UI app (`app.py`), ohmo gateway/CLI, swarm/coordinator (no session-file I/O), diagnostics/session-memory (separate stores). **Rule: prefer this path for any new consumer.**

**(B) Bypassing the loader — must be made format-aware (the blast radius).** Each below is fixed by a task in Phase 7:

| Consumer | v1 assumption | v2 break | Fix | Finding |
|---|---|---|---|---|
| `commands/registry.py` `/session tag` (`:914`) | `copy2(save_snapshot()→latest.json, <tag>.json)` is a full snapshot | copies a pointer | export via `export_snapshot_json` (full v1-shaped, loader-built) | PMR-001 / Task 19 |
| `session_storage.list_session_snapshots` (`:212-294`) + ohmo `list_snapshots` (`:179`) | legacy `session-*.json` glob surfaces un-indexed sessions | v2 sessions (`.jsonl`/`.head`) never surfaced when the index is incomplete | sniffer-based missing-entry merge (v1 + v2) | PMR-002 / Task 20 |
| `conversation_index.rebuild` (`:354`) | `glob("*/session-*.json")` + `json.loads` | indexes zero v2 sessions; wipe-then-rebuild empties search | enumerate v1+v2 per project, reassemble via the loader | PMR-003 / Task 21 |
| `autodream/lock.list_sessions_touched_since` (`:118`) + `autodream/prompt.py` (`:80`) | `glob("session-*.json")` by mtime; prompt says inspect `session-*.json` | empty list → auto-dream never triggers; prompt points at absent files | scan `.json` + `.jsonl`; prompt references both shapes | PMR-004 / Task 22 |

This inventory is the gate's blast-radius checklist for contract/format changes (see the Quality Gate "consumer enumeration" check).

---

## Phase 0 — Pre-work

### Task 0: Branch and baseline

**Files:** none (git only).

1. - [ ] Create the implementation branch from current `main`:
   ```bash
   git checkout -b proposal/session-persistence-v2 main
   ```
2. - [ ] Run the two existing persistence suites to confirm a green baseline before any change:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py tests/test_ohmo/test_ohmo_session_storage.py tests/test_utils/test_fs.py -q
   ```
   Expected: all pass (this is the regression set every later task must keep green).
3. - [ ] Commit nothing yet; proceed to Phase 1.

---

## Phase 1 — Settings and fs primitives

### Task 1: Add the `session_storage_format` and retention settings

**Files:**
- Modify: `src/openharness/config/settings.py` (the `Settings` class field block, after `conversation_index_enabled` at `settings.py:664-666`)
- Test: `tests/test_services/test_session_storage.py`

1. - [ ] Write the failing test. Add to `tests/test_services/test_session_storage.py`:
   ```python
   def test_settings_session_storage_defaults():
       from openharness.config.settings import Settings

       settings = Settings()
       assert settings.session_storage_format == "v2"
       assert settings.session_retention_max_files == 50
       assert settings.session_retention_max_age_days == 30
   ```
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py::test_settings_session_storage_defaults -q
   ```
   Expected: `AttributeError: 'Settings' object has no attribute 'session_storage_format'`.
3. - [ ] Write minimal implementation. In `src/openharness/config/settings.py`, immediately after the `conversation_index_enabled` field (`settings.py:666`), add:
   ```python
       # Session persistence on-disk format. "v2" = append-only transcript +
       # head file + pointer latest.json (default). "v1" = legacy full-history
       # rewrite. Revert switch only; v1 files are always readable regardless.
       session_storage_format: str = "v2"
       # Retention: prune oldest saved sessions on save. Never prunes the active
       # session or the one latest.json points at. 0 disables the limit.
       session_retention_max_files: int = 50
       session_retention_max_age_days: int = 30
   ```
4. - [ ] Run, verify pass:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py::test_settings_session_storage_defaults -q
   ```
   Expected: 1 passed.
5. - [ ] Commit:
   ```bash
   git add src/openharness/config/settings.py tests/test_services/test_session_storage.py && git commit -m "Add session_storage_format and retention settings"
   ```

### Task 2: Parent-directory fsync in `atomic_write_bytes`

**Files:**
- Modify: `src/openharness/utils/fs.py` (`atomic_write_bytes`, `fs.py:39-78`)
- Test: `tests/test_utils/test_fs.py`

**Design decision (proposal left this open — "fix or document"):** We *fix* it. After `os.replace`, when `fsync=True`, fsync the parent directory so the rename itself reaches stable storage (a rename is only durable once the directory entry is flushed). Best-effort: wrapped in `try/except OSError` because some platforms (Windows, certain network mounts) cannot open a directory fd. When `fsync=False` (the per-line state-cache path) the dir fsync is skipped — consistent with the existing "crash may lose the newest version" contract.

1. - [ ] Write the failing test. Add to `tests/test_utils/test_fs.py`:
   ```python
   def test_atomic_write_fsyncs_parent_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
       """With fsync=True the parent directory is fsynced so the rename is durable."""
       synced_fds: list[int] = []
       real_fsync = os.fsync

       def _record(fd: int) -> None:
           synced_fds.append(fd)
           real_fsync(fd)

       monkeypatch.setattr("openharness.utils.fs.os.fsync", _record)
       path = tmp_path / "out.txt"
       atomic_write_text(path, "payload", fsync=True)
       # One fsync for the file, one for the parent directory.
       assert len(synced_fds) == 2
       assert path.read_text() == "payload"


   def test_atomic_write_no_dir_fsync_when_fsync_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
       synced_fds: list[int] = []
       monkeypatch.setattr("openharness.utils.fs.os.fsync", lambda fd: synced_fds.append(fd))
       atomic_write_text(tmp_path / "out.txt", "payload", fsync=False)
       assert synced_fds == []
   ```
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_utils/test_fs.py::test_atomic_write_fsyncs_parent_dir -q
   ```
   Expected: `AssertionError: assert 1 == 2` (only the file is fsynced today).
3. - [ ] Write minimal implementation. In `src/openharness/utils/fs.py`, replace the body of the `try:` block in `atomic_write_bytes` (`fs.py:67-78`) with:
   ```python
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            tmp_file.write(data)
            tmp_file.flush()
            if fsync:
                os.fsync(tmp_file.fileno())
        _apply_mode(tmp_path, target_mode)
        os.replace(tmp_path, dst)
        if fsync:
            _fsync_dir(dst.parent)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise
   ```
   Then add this helper at the end of the module (after `_apply_mode`, `fs.py:122`):
   ```python
   def _fsync_dir(directory: Path) -> None:
       """Fsync a directory so a contained rename reaches stable storage.

       A rename is only durable once the directory entry is flushed. Best
       effort: opening a directory fd is not possible on every platform
       (Windows, some network mounts), so failures are swallowed — the
       payload file itself was already fsynced before the rename.
       """
       try:
           dir_fd = os.open(str(directory), os.O_RDONLY)
       except OSError:
           return
       try:
           os.fsync(dir_fd)
       except OSError:
           pass
       finally:
           os.close(dir_fd)
   ```
4. - [ ] Run, verify pass (and the whole fs suite stays green):
   ```bash
   python -m pytest tests/test_utils/test_fs.py -q
   ```
   Expected: all passed (the two new tests plus the existing ones).
5. - [ ] Commit:
   ```bash
   git add src/openharness/utils/fs.py tests/test_utils/test_fs.py && git commit -m "Fsync parent dir on durable atomic writes"
   ```

### Task 3: Append-only JSONL write + crash-safe read helpers

**Files:**
- Modify: `src/openharness/utils/fs.py` (`__all__` at `fs.py:36`; new `append_jsonl_line`, `read_jsonl_complete_lines`)
- Test: `tests/test_utils/test_fs.py`

**Design decision:** A crash mid-append can leave a partial final line. The reader returns only *complete* lines (a complete line is one terminated by `\n`); a trailing partial line is silently dropped. This is the crash-recovery contract relied on by the loader.

1. - [ ] Write the failing test. Add to `tests/test_utils/test_fs.py`:
   ```python
   def test_append_jsonl_line_appends_and_fsyncs(tmp_path: Path) -> None:
       from openharness.utils.fs import append_jsonl_line, read_jsonl_complete_lines

       path = tmp_path / "t.jsonl"
       append_jsonl_line(path, '{"a": 1}')
       append_jsonl_line(path, '{"a": 2}')
       assert path.read_text() == '{"a": 1}\n{"a": 2}\n'
       assert read_jsonl_complete_lines(path) == ['{"a": 1}', '{"a": 2}']


   def test_read_jsonl_drops_trailing_partial_line(tmp_path: Path) -> None:
       from openharness.utils.fs import read_jsonl_complete_lines

       path = tmp_path / "t.jsonl"
       # Simulate a crash mid-append: last line has no terminating newline.
       path.write_bytes(b'{"a": 1}\n{"a": 2}\n{"a": 3')
       assert read_jsonl_complete_lines(path) == ['{"a": 1}', '{"a": 2}']


   def test_read_jsonl_missing_file_is_empty(tmp_path: Path) -> None:
       from openharness.utils.fs import read_jsonl_complete_lines

       assert read_jsonl_complete_lines(tmp_path / "nope.jsonl") == []
   ```
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_utils/test_fs.py::test_append_jsonl_line_appends_and_fsyncs -q
   ```
   Expected: `ImportError: cannot import name 'append_jsonl_line'`.
3. - [ ] Write minimal implementation. In `src/openharness/utils/fs.py`, extend `__all__` (`fs.py:36`):
   ```python
   __all__ = [
       "atomic_write_bytes",
       "atomic_write_text",
       "read_text_tail",
       "append_jsonl_line",
       "read_jsonl_complete_lines",
   ]
   ```
   Then add after `read_text_tail` (`fs.py:108`):
   ```python
   def append_jsonl_line(
       path: str | os.PathLike[str],
       line: str,
       *,
       encoding: str = "utf-8",
       fsync: bool = True,
   ) -> None:
       """Append one newline-terminated line to a JSONL file durably.

       ``line`` must not already contain a trailing newline; exactly one is
       added. With ``fsync=True`` (default) the file is flushed to stable
       storage after the write — this is the single per-turn durability point
       for the v2 transcript. The parent directory is created on first write.
       """
       dst = Path(path)
       dst.parent.mkdir(parents=True, exist_ok=True)
       payload = (line + "\n").encode(encoding)
       with open(dst, "ab") as handle:
           handle.write(payload)
           handle.flush()
           if fsync:
               os.fsync(handle.fileno())


   def read_jsonl_complete_lines(
       path: str | os.PathLike[str],
       *,
       encoding: str = "utf-8",
   ) -> list[str]:
       """Return every complete (newline-terminated) line of a JSONL file.

       A line is "complete" only when it ends in ``\\n``. A trailing partial
       line — the signature of a crash mid-append — is dropped, so callers
       recover to the last fully-written record. A missing file yields ``[]``.
       """
       src = Path(path)
       try:
           raw = src.read_bytes()
       except FileNotFoundError:
           return []
       text = raw.decode(encoding, errors="replace")
       if not text:
           return []
       lines = text.split("\n")
       # split() always leaves a final element after the last "\n": "" when the
       # file ended in "\n" (all records complete), or the unterminated trailing
       # line (the signature of a crash mid-append). Either way it is not a
       # complete record, so drop it unconditionally.
       lines.pop()
       return [line for line in lines if line]
   ```
4. - [ ] Run, verify pass:
   ```bash
   python -m pytest tests/test_utils/test_fs.py -q
   ```
   Expected: all passed.
5. - [ ] Commit:
   ```bash
   git add src/openharness/utils/fs.py tests/test_utils/test_fs.py && git commit -m "Add append-only JSONL write and crash-safe read helpers"
   ```

---

## Phase 2 — The format module (sniffer, hash, v2 primitives)

### Task 4: Format sniffer

**Files:**
- Create: `src/openharness/services/session_format.py`
- Test: `tests/test_services/test_session_format.py`

**Design decision:** Format is detected from on-disk shape, not a settings flag (so a v1 file is still read correctly even when the setting is `v2`, and vice versa — Design decision 2). Per **C.3**: a `latest.json` whose only meaningful key is `session_id` (no `messages`, no `model`) is a **v2 pointer**; for a session id, a `session-<id>.head.json` *or* a `session-<id>.jsonl` transcript marks it **v2** — this covers **V2_HEADLESS** (transcript present, head lost in a crash) and makes **v2 win** a v1+v2 **CONFLICT** (both a legacy `.json` and v2 files exist); only a lone `session-<id>.json` is **v1**. The function operates on a parsed dict for `latest.json` and on a session dir + id for per-session detection.

1. - [ ] Write the failing test. Create `tests/test_services/test_session_format.py`:
   ```python
   """Tests for the v1/v2 session format primitives."""

   from __future__ import annotations

   from pathlib import Path

   from openharness.services.session_format import detect_latest_format, detect_session_format


   def test_detect_latest_pointer_is_v2():
       assert detect_latest_format({"session_id": "abc123"}) == "v2"


   def test_detect_latest_full_payload_is_v1():
       assert detect_latest_format({"session_id": "abc", "model": "m", "messages": []}) == "v1"


   def test_detect_latest_empty_is_v1():
       assert detect_latest_format({}) == "v1"


   def test_detect_session_format_head_present_is_v2(tmp_path: Path):
       (tmp_path / "session-abc.head.json").write_text("{}", encoding="utf-8")
       assert detect_session_format(tmp_path, "abc") == "v2"


   def test_detect_session_format_only_json_is_v1(tmp_path: Path):
       (tmp_path / "session-abc.json").write_text("{}", encoding="utf-8")
       assert detect_session_format(tmp_path, "abc") == "v1"


   def test_detect_session_format_missing_is_none(tmp_path: Path):
       assert detect_session_format(tmp_path, "ghost") is None


   def test_detect_session_format_headless_transcript_is_v2(tmp_path: Path):
       # V2_HEADLESS (C.3): transcript present, head lost in a crash -> still v2.
       (tmp_path / "session-abc.jsonl").write_text("", encoding="utf-8")
       assert detect_session_format(tmp_path, "abc") == "v2"


   def test_detect_session_format_v1_v2_conflict_prefers_v2(tmp_path: Path):
       # CONFLICT (C.3): a legacy .json and v2 files coexist -> v2 wins.
       (tmp_path / "session-abc.json").write_text("{}", encoding="utf-8")
       (tmp_path / "session-abc.head.json").write_text("{}", encoding="utf-8")
       assert detect_session_format(tmp_path, "abc") == "v2"
   ```
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_services/test_session_format.py -q
   ```
   Expected: `ModuleNotFoundError: No module named 'openharness.services.session_format'`.
3. - [ ] Write minimal implementation. Create `src/openharness/services/session_format.py`:
   ```python
   """On-disk session format primitives shared by openharness and ohmo.

   Two formats coexist:

   * **v1** (legacy): a single ``session-<id>.json`` (and a full ``latest.json``)
     holding the entire history, system prompt, usage, and metadata. Rewritten
     in full on every save.
   * **v2**: an append-only ``session-<id>.jsonl`` transcript (one message per
     line) plus a small ``session-<id>.head.json`` (model, system-prompt hash +
     rebuild inputs, usage, tool_metadata, message_count, summary, created_at),
     and a pointer ``latest.json`` of the form ``{"session_id": ...}``.

   Loaders always sniff the on-disk shape, so a v1 file is read as v1 even when
   the active ``session_storage_format`` is ``v2`` and vice versa. These are
   pure functions with no settings access.
   """

   from __future__ import annotations

   from pathlib import Path
   from typing import Any


   def detect_latest_format(payload: dict[str, Any]) -> str:
       """Classify a parsed ``latest.json`` payload as ``"v1"`` or ``"v2"``.

       A v2 pointer carries ``session_id`` and nothing load-bearing else (no
       ``messages``, no ``model``). Anything richer is a legacy full payload.
       """
       if "messages" in payload or "model" in payload:
           return "v1"
       if "session_id" in payload:
           return "v2"
       return "v1"


   def detect_session_format(session_dir: Path, session_id: str) -> str | None:
       """Classify a stored session by id, or ``None`` when no files exist.

       A ``session-<id>.head.json`` OR a ``session-<id>.jsonl`` transcript marks
       v2 — this covers V2_HEADLESS (transcript present, head lost) and makes v2
       win a v1+v2 CONFLICT (C.3). Only a lone ``session-<id>.json`` is v1.
       """
       head = (session_dir / f"session-{session_id}.head.json").exists()
       transcript = (session_dir / f"session-{session_id}.jsonl").exists()
       if head or transcript:
           return "v2"
       if (session_dir / f"session-{session_id}.json").exists():
           return "v1"
       return None
   ```
4. - [ ] Run, verify pass:
   ```bash
   python -m pytest tests/test_services/test_session_format.py -q
   ```
   Expected: 8 passed.
5. - [ ] Commit:
   ```bash
   git add src/openharness/services/session_format.py tests/test_services/test_session_format.py && git commit -m "Add session format sniffer"
   ```

### Task 5: System-prompt fingerprint helper

**Files:**
- Modify: `src/openharness/services/session_format.py`
- Test: `tests/test_services/test_session_format.py`

**Design decision (sub-item i):** v2 stores `system_prompt_sha256` (a hex digest of the built prompt) plus the *rebuild inputs* already available at save time — `model` and the persistable `tool_metadata` are already in the head; we add nothing the runtime can't already reconstruct. The full prompt text is **not** stored. This is safe because no loader ever reads `system_prompt` back into a runtime (verified: `build_runtime` always rebuilds it via `build_runtime_system_prompt_with_cache_boundary`, `runtime.py:491`); the only readers of the stored `system_prompt` were the writers and tests. The hash is retained purely for diagnostics/debugging ("did the prompt change between turns?").

1. - [ ] Write the failing test. Add to `tests/test_services/test_session_format.py`:
   ```python
   def test_system_prompt_fingerprint_is_stable_sha256():
       from openharness.services.session_format import system_prompt_fingerprint

       fp = system_prompt_fingerprint("You are a helpful assistant.")
       assert fp == system_prompt_fingerprint("You are a helpful assistant.")
       assert len(fp) == 64  # sha256 hex digest
       assert fp != system_prompt_fingerprint("different")


   def test_system_prompt_fingerprint_empty():
       from openharness.services.session_format import system_prompt_fingerprint

       assert len(system_prompt_fingerprint("")) == 64
   ```
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_services/test_session_format.py::test_system_prompt_fingerprint_is_stable_sha256 -q
   ```
   Expected: `ImportError: cannot import name 'system_prompt_fingerprint'`.
3. - [ ] Write minimal implementation. Add to the top imports of `src/openharness/services/session_format.py`:
   ```python
   from hashlib import sha256
   ```
   Then add the function after `detect_session_format`:
   ```python
   def system_prompt_fingerprint(system_prompt: str) -> str:
       """Return the sha256 hex digest of a built system prompt.

       v2 persists this digest instead of the full prompt text. The prompt is
       always rebuilt on resume from ``model`` + ``tool_metadata`` (the rebuild
       inputs already in the head), so the text itself is never needed on disk;
       the digest is kept only as a debugging signal for prompt drift.
       """
       return sha256(system_prompt.encode("utf-8")).hexdigest()
   ```
4. - [ ] Run, verify pass:
   ```bash
   python -m pytest tests/test_services/test_session_format.py -q
   ```
   Expected: 10 passed.
5. - [ ] Commit:
   ```bash
   git add src/openharness/services/session_format.py tests/test_services/test_session_format.py && git commit -m "Add system_prompt_fingerprint helper for v2 heads"
   ```

### Task 6: v2 head read/write

**Files:**
- Modify: `src/openharness/services/session_format.py`
- Test: `tests/test_services/test_session_format.py`

1. - [ ] Write the failing test. Add to `tests/test_services/test_session_format.py`:
   ```python
   def test_write_and_read_head_round_trip(tmp_path: Path):
       from openharness.services.session_format import read_head, write_head

       head = {
           "session_id": "abc123",
           "model": "claude-test",
           "system_prompt_sha256": "deadbeef" * 8,
           "usage": {"input_tokens": 1, "output_tokens": 2},
           "tool_metadata": {"permission_mode": "default"},
           "message_count": 3,
           "summary": "hello",
           "created_at": 1.0,
       }
       write_head(tmp_path, "abc123", head)
       assert (tmp_path / "session-abc123.head.json").exists()
       loaded = read_head(tmp_path, "abc123")
       assert loaded == head


   def test_read_head_missing_returns_none(tmp_path: Path):
       from openharness.services.session_format import read_head

       assert read_head(tmp_path, "ghost") is None
   ```
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_services/test_session_format.py::test_write_and_read_head_round_trip -q
   ```
   Expected: `ImportError: cannot import name 'write_head'`.
3. - [ ] Write minimal implementation. Add to the imports of `src/openharness/services/session_format.py`:
   ```python
   import json

   from openharness.utils.fs import atomic_write_text
   ```
   Then add:
   ```python
   def head_path(session_dir: Path, session_id: str) -> Path:
       return session_dir / f"session-{session_id}.head.json"


   def transcript_path(session_dir: Path, session_id: str) -> Path:
       return session_dir / f"session-{session_id}.jsonl"


   def write_head(session_dir: Path, session_id: str, head: dict[str, Any]) -> None:
       """Atomically rewrite the per-session head file.

       Atomic-rename without per-write fsync: a crash loses at most cosmetic
       head metadata (the transcript stays durable), so the durability cost of
       an fsync per turn is not paid here.
       """
       atomic_write_text(
           head_path(session_dir, session_id),
           json.dumps(head, indent=2) + "\n",
           fsync=False,
       )


   def read_head(session_dir: Path, session_id: str) -> dict[str, Any] | None:
       path = head_path(session_dir, session_id)
       if not path.exists():
           return None
       try:
           payload = json.loads(path.read_text(encoding="utf-8"))
       except (json.JSONDecodeError, OSError):
           return None
       return payload if isinstance(payload, dict) else None
   ```
4. - [ ] Run, verify pass:
   ```bash
   python -m pytest tests/test_services/test_session_format.py -q
   ```
   Expected: 12 passed.
5. - [ ] Commit:
   ```bash
   git add src/openharness/services/session_format.py tests/test_services/test_session_format.py && git commit -m "Add v2 head read/write primitives"
   ```

### Task 7: v2 transcript append, full-load, and compaction rewrite

**Files:**
- Modify: `src/openharness/services/session_format.py`
- Test: `tests/test_services/test_session_format.py`

**Design decision (delta append + compaction):** Between compactions the save path appends only the messages past `last_persisted_count`, where that cursor is the transcript's own live-record count (C.4 cursor invariant) — **never** `head.message_count`. Compaction is detected by a **content fingerprint of the durable prefix**, not a count comparison (Design decision 3 / C.5-trigger): the engine compacts *in place* — `microcompact_messages` clears old tool-result bodies and `try_context_collapse` shrinks text, both rewriting message **content while leaving the count unchanged** (verified against `compact/__init__.py:854` / `:348`). A count test (`last_persisted > len(messages)`) misses these and would leave stale content on disk (R-001). Instead the save fingerprints the durable prefix (`fingerprint_messages(messages[:last_persisted])`) and rewrites the transcript in full whenever that fingerprint diverges from what was persisted, **or** the history shrank. The rewrite is preceded by a typed compaction-marker line. Per **C.5** the marker is `{"__compacted_at__": <ts>}` and a record dispatches as a marker **iff** it carries `__compacted_at__` *and has no* `role` key (message records always carry `role`) — so a message can never be mistaken for a marker. `load_v2_snapshot` reads every complete transcript line, drops everything up to and including the last marker, and returns the live history; `transcript_live_count` returns its length, the crash-correct seed for the cursor, and the same `load_v2_snapshot` read seeds the prefix fingerprint.

1. - [ ] Write the failing test. Add to `tests/test_services/test_session_format.py`:
   ```python
   def _msgs(*texts):
       from openharness.engine.messages import ConversationMessage, TextBlock

       return [ConversationMessage(role="user", content=[TextBlock(text=t)]) for t in texts]


   def test_append_messages_delta_only(tmp_path: Path):
       from openharness.services.session_format import (
           append_messages_to_transcript,
           load_v2_snapshot,
       )

       append_messages_to_transcript(tmp_path, "s1", _msgs("a", "b"), last_persisted_count=0)
       append_messages_to_transcript(tmp_path, "s1", _msgs("a", "b", "c"), last_persisted_count=2)
       snap = load_v2_snapshot(tmp_path, "s1")
       assert [m["content"][0]["text"] for m in snap] == ["a", "b", "c"]


   def test_compaction_rewrites_and_load_keeps_post_marker(tmp_path: Path):
       from openharness.services.session_format import (
           append_messages_to_transcript,
           load_v2_snapshot,
           rewrite_transcript,
       )

       append_messages_to_transcript(tmp_path, "s1", _msgs("a", "b", "c"), last_persisted_count=0)
       # Compaction collapses history to a single summary message.
       rewrite_transcript(tmp_path, "s1", _msgs("summary"))
       snap = load_v2_snapshot(tmp_path, "s1")
       assert [m["content"][0]["text"] for m in snap] == ["summary"]


   def test_load_v2_recovers_from_truncated_final_line(tmp_path: Path):
       from openharness.services.session_format import load_v2_snapshot, transcript_path

       # Two complete records then a crash mid-third (no newline).
       transcript_path(tmp_path, "s1").write_bytes(
           b'{"role": "user", "content": [{"type": "text", "text": "a"}]}\n'
           b'{"role": "user", "content": [{"type": "text", "text": "b"}]}\n'
           b'{"role": "user", "content": [{"type": "text", "text": "c"'
       )
       snap = load_v2_snapshot(tmp_path, "s1")
       assert [m["content"][0]["text"] for m in snap] == ["a", "b"]


   def test_record_with_marker_key_but_role_is_a_message_not_a_marker(tmp_path: Path):
       # Typed dispatch (C.5, P2-003): a record carrying both the marker key and a
       # "role" is a message, not a marker, so it must NOT wipe history. Written as
       # a raw line to bypass the message schema and exercise the discriminator.
       from openharness.services.session_format import load_v2_snapshot, transcript_path

       transcript_path(tmp_path, "s1").write_bytes(
           b'{"role": "user", "content": [{"type": "text", "text": "a"}]}\n'
           b'{"__compacted_at__": 123, "role": "user", "content": [{"type": "text", "text": "b"}]}\n'
       )
       snap = load_v2_snapshot(tmp_path, "s1")
       assert [m["content"][0]["text"] for m in snap] == ["a", "b"]


   def test_transcript_live_count_counts_post_marker_records(tmp_path: Path):
       from openharness.services.session_format import (
           append_messages_to_transcript,
           rewrite_transcript,
           transcript_live_count,
       )

       assert transcript_live_count(tmp_path, "s1") == 0  # absent transcript
       append_messages_to_transcript(tmp_path, "s1", _msgs("a", "b"), last_persisted_count=0)
       assert transcript_live_count(tmp_path, "s1") == 2
       rewrite_transcript(tmp_path, "s1", _msgs("summary"))  # compaction
       assert transcript_live_count(tmp_path, "s1") == 1  # only the post-marker record


   def test_fingerprint_messages_detects_in_place_content_change(tmp_path: Path):
       # R-001: the signal a count test misses. Same message COUNT, changed content
       # (an in-place compaction) MUST yield a different fingerprint; and a message
       # object must fingerprint equal to the dict it was persisted as (the seed path
       # reads dicts via load_v2_snapshot, the compare path uses live objects).
       from openharness.services.session_format import (
           append_messages_to_transcript,
           fingerprint_messages,
           load_v2_snapshot,
       )

       original = _msgs("tool-output-aaaa", "b")
       cleared_same_count = _msgs("cleared", "b")  # in place: count unchanged, content changed
       assert len(original) == len(cleared_same_count)
       assert fingerprint_messages(original) != fingerprint_messages(cleared_same_count)
       assert fingerprint_messages(original) == fingerprint_messages(original)  # stable
       append_messages_to_transcript(tmp_path, "s1", original, last_persisted_count=0)
       assert fingerprint_messages(load_v2_snapshot(tmp_path, "s1")) == fingerprint_messages(original)
   ```
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_services/test_session_format.py::test_append_messages_delta_only -q
   ```
   Expected: `ImportError: cannot import name 'append_messages_to_transcript'`.
3. - [ ] Write minimal implementation. Add to the imports of `src/openharness/services/session_format.py`:
   ```python
   from openharness.engine.messages import ConversationMessage
   from openharness.utils.fs import (
       append_jsonl_line,
       atomic_write_text,
       read_jsonl_complete_lines,
   )
   ```
   (replace the earlier `from openharness.utils.fs import atomic_write_text` line with the grouped import above). Then add:
   ```python
   _COMPACTION_MARKER = "__compacted_at__"


   def append_messages_to_transcript(
       session_dir: Path,
       session_id: str,
       messages: list[ConversationMessage],
       *,
       last_persisted_count: int,
   ) -> None:
       """Append only the messages past ``last_persisted_count`` (one fsync).

       The whole batch is written as individual lines and the file is fsynced
       once at the end via the final ``append_jsonl_line`` call — the single
       per-turn durability point.
       """
       session_dir.mkdir(parents=True, exist_ok=True)
       new_messages = messages[last_persisted_count:]
       if not new_messages:
           return
       path = transcript_path(session_dir, session_id)
       last = len(new_messages) - 1
       for index, message in enumerate(new_messages):
           line = json.dumps(message.model_dump(mode="json"), separators=(",", ":"))
           append_jsonl_line(path, line, fsync=(index == last))


   def rewrite_transcript(
       session_dir: Path,
       session_id: str,
       messages: list[ConversationMessage],
   ) -> None:
       """Rewrite the transcript after a compaction.

       Writes a compaction marker line followed by the post-compaction history,
       atomically replacing the file. Readers keep only records after the last
       marker, so the loaded history always matches the compacted state.
       """
       import time

       lines = [json.dumps({_COMPACTION_MARKER: time.time()}, separators=(",", ":"))]
       lines.extend(
           json.dumps(message.model_dump(mode="json"), separators=(",", ":"))
           for message in messages
       )
       atomic_write_text(
           transcript_path(session_dir, session_id),
           "\n".join(lines) + "\n",
           fsync=True,
       )


   def load_v2_snapshot(session_dir: Path, session_id: str) -> list[dict[str, Any]]:
       """Return raw message dicts from a v2 transcript, post-last-compaction.

       Skips marker lines, discards everything up to and including the last
       marker, and ignores any malformed line. The result feeds the same
       sanitize/validate path as v1 messages.
       """
       records: list[dict[str, Any]] = []
       for raw in read_jsonl_complete_lines(transcript_path(session_dir, session_id)):
           try:
               obj = json.loads(raw)
           except json.JSONDecodeError:
               continue
           if not isinstance(obj, dict):
               continue
           # Typed dispatch (C.5, P2-003): a record is a compaction marker iff it
           # carries the marker key AND lacks "role". Message records always have
           # "role", so a message is never mistaken for a marker.
           if _COMPACTION_MARKER in obj and "role" not in obj:
               records.clear()  # drop pre-compaction history
               continue
           records.append(obj)
       return records


   def transcript_live_count(session_dir: Path, session_id: str) -> int:
       """Count the live records durable in the transcript (post-last-marker).

       The crash-correct seed for the append cursor (C.4): it reflects what is
       actually fsync'd in the transcript, independent of the non-durable head.
       Called once per process per session — the writer then maintains the count
       in-process (Task 8), so it is not an O(n) per-save read.
       """
       return len(load_v2_snapshot(session_dir, session_id))


   def fingerprint_messages(messages: list[ConversationMessage] | list[dict[str, Any]]) -> str:
       """Stable content fingerprint of an ordered message history (R-001 / C.5-trigger).

       Detects an in-place compaction that rewrites message *content* without
       changing the count: two histories fingerprint equal iff their ordered,
       canonicalized JSON content is identical. Accepts either ConversationMessage
       objects (the in-memory list) or already-serialized dicts (``load_v2_snapshot``'s
       output) and canonicalizes both the same way — a message and the dict it was
       persisted as (``json.loads`` of ``json.dumps(model_dump(mode="json"))``) round-trip
       to equal ``sort_keys`` JSON, so an in-memory prefix compares equal to the durable
       prefix it became. Pure; no I/O, no settings.
       """
       digest = hashlib.blake2b(digest_size=16)
       for message in messages:
           payload = message.model_dump(mode="json") if hasattr(message, "model_dump") else message
           digest.update(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
           digest.update(b"\x1e")  # record separator: makes the hash order- and boundary-sensitive
       return digest.hexdigest()
   ```
   `fingerprint_messages` needs `import hashlib` at the top of `session_format.py` (alongside the existing `import json`).
4. - [ ] Run, verify pass:
   ```bash
   python -m pytest tests/test_services/test_session_format.py -q
   ```
   Expected: 18 passed (Task 4 adds the V2_HEADLESS + CONFLICT detection tests; Task 7 adds the P2-003 dispatch, `transcript_live_count`, and `fingerprint_messages` in-place-change tests).
5. - [ ] Commit:
   ```bash
   git add src/openharness/services/session_format.py tests/test_services/test_session_format.py && git commit -m "Add v2 transcript append, load, and compaction rewrite"
   ```

---

## Phase 3 — Wire v2 into openharness session_storage

### Task 8: v2 save path behind the format flag

**Files:**
- Modify: `src/openharness/services/session_storage.py` (`save_session_snapshot`, `session_storage.py:114-174`; new `_save_session_snapshot_v2`)
- Test: `tests/test_services/test_session_storage.py`

**Design decision:** `save_session_snapshot` reads `load_settings().session_storage_format`; on `"v2"` it routes to `_save_session_snapshot_v2`, otherwise it keeps the existing v1 body verbatim (the revert switch). v2 computes the delta from the head's prior `message_count`, appends, rewrites the head (with `system_prompt_sha256`, not the prompt), writes the `latest.json` pointer, updates the index, and feeds the conversation index with the *same* payload shape v1 produced (so `_update_conversation_index` is unchanged). Return value stays `latest_path` (the pointer file) — callers only use it as a truthy Path.

1. - [ ] Write the failing test. Add to `tests/test_services/test_session_storage.py`:
   ```python
   def test_v2_save_creates_transcript_head_and_pointer(tmp_path: Path, monkeypatch):
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
       project = tmp_path / "repo"
       project.mkdir()

       save_session_snapshot(
           cwd=project,
           model="claude-test",
           system_prompt="SYSTEM PROMPT TEXT",
           session_id="v2sess",
           messages=[ConversationMessage(role="user", content=[TextBlock(text="hi")])],
           usage=UsageSnapshot(input_tokens=1, output_tokens=2),
           tool_metadata={"permission_mode": "default"},
       )

       session_dir = get_project_session_dir(project)
       assert (session_dir / "session-v2sess.jsonl").exists()
       assert (session_dir / "session-v2sess.head.json").exists()
       # latest.json is a pointer, not a full payload.
       latest = json.loads((session_dir / "latest.json").read_text(encoding="utf-8"))
       assert latest == {"session_id": "v2sess"}
       # The full system prompt text is not persisted; only its hash.
       head = json.loads((session_dir / "session-v2sess.head.json").read_text(encoding="utf-8"))
       assert "SYSTEM PROMPT TEXT" not in json.dumps(head)
       assert len(head["system_prompt_sha256"]) == 64


   def test_v1_revert_switch_writes_full_session_file(tmp_path: Path, monkeypatch):
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
       project = tmp_path / "repo"
       project.mkdir()
       config_dir = tmp_path / "cfg"
       config_dir.mkdir()
       (config_dir / "settings.json").write_text('{"session_storage_format": "v1"}', encoding="utf-8")
       monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(config_dir))

       save_session_snapshot(
           cwd=project,
           model="claude-test",
           system_prompt="system",
           session_id="v1sess",
           messages=[ConversationMessage(role="user", content=[TextBlock(text="hi")])],
           usage=UsageSnapshot(),
       )
       session_dir = get_project_session_dir(project)
       assert (session_dir / "session-v1sess.json").exists()
       assert not (session_dir / "session-v1sess.jsonl").exists()


   def test_v2_lost_head_does_not_duplicate_on_next_save(tmp_path: Path, monkeypatch):
       # P1-001 (behavioral). A crash that loses the non-fsync'd head between two
       # saves must NOT make the next save re-append already-durable messages.
       # Fails with the old head-derived cursor (re-appends -> a,b,a,b,c);
       # passes with the transcript-derived cursor (C.4).
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
       import openharness.services.session_storage as ss
       from openharness.services.session_format import head_path, load_v2_snapshot

       project = tmp_path / "repo"
       project.mkdir()

       def save(texts):
           save_session_snapshot(
               cwd=project, model="claude-test", system_prompt="s", session_id="s1",
               messages=[ConversationMessage(role="user", content=[TextBlock(text=t)]) for t in texts],
               usage=UsageSnapshot(),
           )

       save(["a", "b"])  # transcript durable with 2 records; head + cache reflect 2
       session_dir = get_project_session_dir(project)
       # Simulate the crash window: the head write (no fsync) is lost AND the
       # in-process cursor cache is gone (a fresh process would have neither).
       head_path(session_dir, "s1").unlink()
       ss._v2_persisted_count.clear()
       ss._v2_persisted_prefix_fp.clear()  # both halves of the in-process cursor vanish on crash

       save(["a", "b", "c"])  # cursor re-seeds from the transcript (=2); appends only "c"

       assert [m["content"][0]["text"] for m in load_v2_snapshot(session_dir, "s1")] == ["a", "b", "c"]


   def test_concurrent_v2_saves_preserve_all_index_entries(tmp_path: Path, monkeypatch):
       # P1-003 (behavioral). Many concurrent savers each add a distinct session id;
       # the store lock must serialise the index read-modify-write so no entry is
       # lost. Without the lock, concurrent RMW drops updates and the set is short.
       import threading
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
       project = tmp_path / "repo"
       project.mkdir()

       def worker(n: int):
           save_session_snapshot(
               cwd=project, model="m", system_prompt="s", session_id=f"c{n}",
               messages=[ConversationMessage(role="user", content=[TextBlock(text=str(n))])],
               usage=UsageSnapshot(),
           )

       threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
       for t in threads:
           t.start()
       for t in threads:
           t.join()

       from openharness.services.session_storage import _load_session_index
       ids = {e["session_id"] for e in _load_session_index(get_project_session_dir(project))}
       assert ids == {f"c{i}" for i in range(12)}
   ```
   > **Verified mechanism:** `get_config_file_path()` (`config/paths.py:44`) returns `get_config_dir() / "settings.json"`, and `get_config_dir()` (`config/paths.py:28-41`) honors `OPENHARNESS_CONFIG_DIR`. Setting that env var to a temp dir and writing `settings.json` inside it makes `load_settings()` pick up the format flag. (`OPENHARNESS_DATA_DIR` controls only the *sessions* dir, not the settings file, so the two env vars point at different temp dirs above.)
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py::test_v2_save_creates_transcript_head_and_pointer -q
   ```
   Expected: fails — `session-v2sess.jsonl` does not exist (v1 path still runs).
3. - [ ] Write minimal implementation. In `src/openharness/services/session_storage.py`, add to the imports (after `session_storage.py:15`):
   ```python
   from openharness.services import session_format
   from openharness.utils.file_lock import exclusive_file_lock
   ```
   Add module-level in-process persistence state (C.4) near the other module state — the crash-correct cursor source that replaces `head.message_count`, **plus** the content fingerprint that lets the save detect in-place compaction the count cannot see (C.5-trigger / R-001):
   ```python
   # In-process per-session persistence state, keyed by (session_dir, session_id):
   #   _v2_persisted_count     -> count of live records the owning writer has
   #                              persisted (the crash-correct append cursor —
   #                              C.4 / P1-001; seeded from the durable transcript,
   #                              NEVER from the non-durable head).
   #   _v2_persisted_prefix_fp -> content fingerprint of those same persisted live
   #                              records, used to detect an *in-place* compaction
   #                              that rewrites content WITHOUT changing the count
   #                              (C.5-trigger / R-001). Always written together
   #                              with the count, via _v2_remember_persisted.
   # Process-local: a crash discards both and the next process re-seeds from the
   # transcript. Bounded (R-004): a long-lived foreground process that resumes many
   # sessions evicts the oldest entry past _V2_CURSOR_CACHE_MAX; an evicted session
   # simply re-seeds (count + fp) from its transcript on its next save — correct,
   # one extra read.
   _V2_CURSOR_CACHE_MAX = 1024
   _v2_persisted_count: dict[tuple[str, str], int] = {}
   _v2_persisted_prefix_fp: dict[tuple[str, str], str] = {}


   def _v2_remember_persisted(key: tuple[str, str], count: int, prefix_fp: str) -> None:
       """Record the durable (count, fingerprint) for a session, bounding the cache.

       Re-inserts the key at the most-recent position (write-LRU) and evicts the
       oldest entries past the cap; eviction is safe because the next save for an
       evicted id re-seeds from the transcript (the cold-seed path).
       """
       _v2_persisted_count.pop(key, None)
       _v2_persisted_prefix_fp.pop(key, None)
       _v2_persisted_count[key] = count
       _v2_persisted_prefix_fp[key] = prefix_fp
       while len(_v2_persisted_count) > _V2_CURSOR_CACHE_MAX:
           oldest = next(iter(_v2_persisted_count))
           _v2_persisted_count.pop(oldest, None)
           _v2_persisted_prefix_fp.pop(oldest, None)
   ```
   Split the existing `_update_session_index` (`session_storage.py:101`) into a lock-free core plus a locking wrapper, so the store-wide index read-modify-write is serialised by the same `exclusive_file_lock` the rest of the codebase already uses for shared JSON registries (C.2). v1 callers keep calling the wrapper (one acquisition, no nesting); the v2 save acquires the lock once itself and calls the *core*:
   ```python
   def _update_session_index_unlocked(session_dir: Path, entry: dict[str, Any]) -> None:
       # The current _update_session_index body (read-modify-write) — NO lock; the
       # caller must hold session_dir / ".sessions.lock".
       session_id = str(entry.get("session_id") or "")
       if not session_id:
           return
       entries = [
           existing
           for existing in _load_session_index(session_dir)
           if str(existing.get("session_id") or "") != session_id
       ]
       entries.append(entry)
       _write_session_index(session_dir, entries)


   def _update_session_index(session_dir: Path, entry: dict[str, Any]) -> None:
       # Locking wrapper for callers not already under the store lock (e.g. v1).
       with exclusive_file_lock(session_dir / ".sessions.lock"):
           _update_session_index_unlocked(session_dir, entry)
   ```
   Replace the body of `save_session_snapshot` from `session_dir = get_project_session_dir(cwd)` (`session_storage.py:125`) down to `return latest_path` (`session_storage.py:174`) with a router that keeps the existing v1 body in a helper:
   ```python
       from openharness.config import load_settings

       session_dir = get_project_session_dir(cwd)
       sid = session_id or uuid4().hex[:12]
       now = time.time()
       messages = sanitize_conversation_messages(messages)
       summary = ""
       for msg in messages:
           if msg.role == "user" and msg.text.strip():
               summary = msg.text.strip()[:80]
               break

       fmt = load_settings().session_storage_format
       if fmt == "v2":
           return _save_session_snapshot_v2(
               session_dir=session_dir,
               sid=sid,
               cwd=cwd,
               model=model,
               system_prompt=system_prompt,
               messages=messages,
               usage=usage,
               tool_metadata=tool_metadata,
               summary=summary,
               now=now,
           )
       return _save_session_snapshot_v1(
           session_dir=session_dir,
           sid=sid,
           cwd=cwd,
           model=model,
           system_prompt=system_prompt,
           messages=messages,
           usage=usage,
           tool_metadata=tool_metadata,
           summary=summary,
           now=now,
       )
   ```
   Add the two helpers immediately after `save_session_snapshot`. `_save_session_snapshot_v1` is the *old body* lifted verbatim (payload build + watchdog + dual atomic_write + index + conversation index + record + `return latest_path`):
   ```python
   def _save_session_snapshot_v1(
       *,
       session_dir: Path,
       sid: str,
       cwd: str | Path,
       model: str,
       system_prompt: str,
       messages: list[ConversationMessage],
       usage: UsageSnapshot,
       tool_metadata: dict[str, object] | None,
       summary: str,
       now: float,
   ) -> Path:
       payload = {
           "session_id": sid,
           "cwd": str(Path(cwd).resolve()),
           "model": model,
           "system_prompt": system_prompt,
           "messages": [message.model_dump(mode="json") for message in messages],
           "usage": usage.model_dump(),
           "tool_metadata": _persistable_tool_metadata(tool_metadata),
           "created_at": now,
           "summary": summary,
           "message_count": len(messages),
       }
       data = json.dumps(payload, indent=2) + "\n"

       from openharness.diagnostics import watchdog

       with watchdog.track("snapshot_write", session_id=sid):
           latest_path = session_dir / "latest.json"
           atomic_write_text(latest_path, data, fsync=False)
           session_path = session_dir / f"session-{sid}.json"
           atomic_write_text(session_path, data, fsync=False)
           _update_session_index(session_dir, _session_index_entry(payload, session_path))
           _update_conversation_index(payload)

       from openharness.diagnostics import record

       record(
           "storage",
           "snapshot_save",
           "completed",
           duration_ms=(time.time() - now) * 1000.0,
           session_id=sid,
           attrs={"app": "openharness", "size_bytes": len(data), "message_count": len(messages)},
       )
       return latest_path


   def _save_session_snapshot_v2(
       *,
       session_dir: Path,
       sid: str,
       cwd: str | Path,
       model: str,
       system_prompt: str,
       messages: list[ConversationMessage],
       usage: UsageSnapshot,
       tool_metadata: dict[str, object] | None,
       summary: str,
       now: float,
   ) -> Path:
       from openharness.diagnostics import watchdog

       prior_head = session_format.read_head(session_dir, sid)
       created_at = prior_head.get("created_at", now) if prior_head else now
       # Cursor + fingerprint invariant (C.4 / C.5-trigger): the append cursor is
       # the count of live records already durable in the transcript — NOT
       # head.message_count, which is rename-written (no fsync) after the fsync'd
       # transcript and so can be lost in a crash (P1-001). Alongside it we keep a
       # content fingerprint of that durable prefix. Both are seeded once from the
       # transcript (a single load_v2_snapshot read — same I/O the count seed used)
       # and then maintained in-process (single writer per C.2).
       key = (str(session_dir), sid)
       last_persisted = _v2_persisted_count.get(key)
       persisted_fp = _v2_persisted_prefix_fp.get(key)
       if last_persisted is None or persisted_fp is None:
           durable = session_format.load_v2_snapshot(session_dir, sid)
           last_persisted = len(durable)
           persisted_fp = session_format.fingerprint_messages(durable)
       # R-001: count alone is a LOSSY proxy for "was the history compacted". The
       # engine compacts IN PLACE — microcompact clears old tool-result bodies and
       # context-collapse shrinks text, both rewriting message *content* while the
       # count stays the same (verified: compact/__init__.py:854 / :348). A count
       # test would take the append path and leave stale content on disk. Compare
       # the durable prefix's content fingerprint instead: the prefix is stale iff
       # the in-memory prefix no longer matches what we persisted, OR the history
       # shrank. Either way rewrite the transcript in full (C.5 marker + full
       # history); otherwise append only the delta (C.3).
       compacted = (
           len(messages) < last_persisted
           or session_format.fingerprint_messages(messages[:last_persisted]) != persisted_fp
       )

       with watchdog.track("snapshot_write", session_id=sid):
           if compacted:
               session_format.rewrite_transcript(session_dir, sid, messages)
           else:
               session_format.append_messages_to_transcript(
                   session_dir, sid, messages, last_persisted_count=last_persisted
               )
           # Transcript is now durable; record the new live count AND the fingerprint
           # of the now-persisted history (the whole list, after either path) before
           # the derived head/pointer/index writes (C.4 matrix). Bounded cache (R-004).
           _v2_remember_persisted(
               key, len(messages), session_format.fingerprint_messages(messages)
           )

           head = {
               "session_id": sid,
               "cwd": str(Path(cwd).resolve()),
               "model": model,
               "system_prompt_sha256": session_format.system_prompt_fingerprint(system_prompt),
               "usage": usage.model_dump(),
               "tool_metadata": _persistable_tool_metadata(tool_metadata),
               "created_at": created_at,
               "summary": summary,
               "message_count": len(messages),
           }
           session_format.write_head(session_dir, sid, head)

           latest_path = session_dir / "latest.json"
           atomic_write_text(
               latest_path, json.dumps({"session_id": sid}) + "\n", fsync=False
           )

           index_payload = {**head, "messages": []}
           # Store-wide critical section (C.2): the index read-modify-write — and,
           # added in Task 11, the retention prune — run under ONE acquisition of
           # the store lock. Call the *_unlocked core, never the locking
           # _update_session_index (flock is per-open-description → a second
           # acquire in this process self-deadlocks).
           with exclusive_file_lock(session_dir / ".sessions.lock"):
               _update_session_index_unlocked(
                   session_dir,
                   _session_index_entry(index_payload, session_dir / f"session-{sid}.head.json"),
               )
               # Task 11 inserts the retention prune here, inside this same lock.
           _update_conversation_index({**head, "messages": [m.model_dump(mode="json") for m in messages]})

       from openharness.diagnostics import record

       record(
           "storage",
           "snapshot_save",
           "completed",
           duration_ms=(time.time() - now) * 1000.0,
           session_id=sid,
           attrs={"app": "openharness", "message_count": len(messages), "format": "v2"},
       )
       return latest_path
   ```
   > **Crash safety (C.4).** Step order is: (1) append/rewrite the transcript — the single fsync and the commit point; (2) record the in-process cursor; (3) write the head; (4) write the pointer; (5) update the index under the store lock. A crash after any of (2)–(5) loses only derived state: the next save re-seeds the cursor from the transcript and rewrites the head/pointer/index, so no message is duplicated or lost. The full step-by-step matrix is in C.4.
4. - [ ] Run, verify pass (both new tests + the existing v1 round-trip):
   ```bash
   python -m pytest tests/test_services/test_session_storage.py::test_v2_save_creates_transcript_head_and_pointer tests/test_services/test_session_storage.py::test_v1_revert_switch_writes_full_session_file tests/test_services/test_session_storage.py::test_v2_lost_head_does_not_duplicate_on_next_save tests/test_services/test_session_storage.py::test_concurrent_v2_saves_preserve_all_index_entries -q
   ```
   Expected: 4 passed (lost-head proves P1-001; the concurrency test proves the P1-003 store lock).
5. - [ ] Commit:
   ```bash
   git add src/openharness/services/session_storage.py tests/test_services/test_session_storage.py && git commit -m "Route session save through v2 format behind the flag"
   ```

### Task 9: v2 load path — pointer resolution + sniff + reassembly

**Files:**
- Modify: `src/openharness/services/session_storage.py` (`_sanitize_snapshot_payload` `session_storage.py:191-201`, `load_session_snapshot` `session_storage.py:204-209`, `load_session_by_id` `session_storage.py:297-310`; new `_load_v2_payload`)
- Test: `tests/test_services/test_session_storage.py`

**Design decision (resume load — sub-item g) [P2-001]:** This is a **documents-only / readability** change, not a speed-up. `_sanitize_snapshot_payload` stays a single validate→sanitize→dump pass and gains a clean v2 reassembly path. The earlier draft's "drop the wasteful second dump / half the pydantic work" claim was **inaccurate** — the current code (`session_storage.py:191-201`) already does exactly one `model_validate` and one `model_dump`, so the proposed restructure is operationally identical (verified against the source). The genuine resume redundancy is at the **storage↔runtime boundary**: storage dumps messages to dicts to preserve the public shape, then `build_runtime` re-validates those dicts into `ConversationMessage` objects on resume. Removing that round-trip would change the public loader shape (objects, not dicts) and is **explicitly deferred** — it is a one-time cost paid once at session start, and the refactor's regression risk is disproportionate to the gain (right-size / YAGNI). Revisit only if resume latency is ever a *measured* problem.

1. - [ ] Write the failing test. Add to `tests/test_services/test_session_storage.py`:
   ```python
   def test_v2_load_latest_via_pointer_round_trip(tmp_path: Path, monkeypatch):
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
       project = tmp_path / "repo"
       project.mkdir()

       save_session_snapshot(
           cwd=project,
           model="claude-test",
           system_prompt="system",
           session_id="v2sess",
           messages=[
               ConversationMessage(role="user", content=[TextBlock(text="hello")]),
               ConversationMessage(role="assistant", content=[TextBlock(text="world")]),
           ],
           usage=UsageSnapshot(input_tokens=3, output_tokens=4),
           tool_metadata={"recent_verified_work": ["did a thing"]},
       )

       snap = load_session_snapshot(project)
       assert snap is not None
       assert snap["session_id"] == "v2sess"
       assert snap["model"] == "claude-test"
       assert snap["message_count"] == 2
       assert [m["role"] for m in snap["messages"]] == ["user", "assistant"]
       assert snap["usage"]["output_tokens"] == 4
       assert snap["tool_metadata"]["recent_verified_work"] == ["did a thing"]


   def test_v2_load_by_id_round_trip(tmp_path: Path, monkeypatch):
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
       project = tmp_path / "repo"
       project.mkdir()
       save_session_snapshot(
           cwd=project,
           model="claude-test",
           system_prompt="system",
           session_id="byid",
           messages=[ConversationMessage(role="user", content=[TextBlock(text="x")])],
           usage=UsageSnapshot(),
       )
       from openharness.services.session_storage import load_session_by_id

       snap = load_session_by_id(project, "byid")
       assert snap is not None and snap["session_id"] == "byid"
       assert snap["messages"][0]["content"][0]["text"] == "x"


   def test_v2_load_via_pointer_recovers_when_head_missing(tmp_path: Path, monkeypatch):
       # P2-005 / V2_HEADLESS (C.6): the head was lost in a crash but the transcript
       # is durable — resume must still recover the history off the transcript.
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
       import openharness.services.session_storage as ss
       from openharness.services.session_format import head_path

       project = tmp_path / "repo"
       project.mkdir()
       save_session_snapshot(
           cwd=project, model="claude-test", system_prompt="s", session_id="hl",
           messages=[ConversationMessage(role="user", content=[TextBlock(text="kept")])],
           usage=UsageSnapshot(),
       )
       session_dir = get_project_session_dir(project)
       head_path(session_dir, "hl").unlink()  # simulate the lost-head crash window
       ss._v2_persisted_count.clear()
       ss._v2_persisted_prefix_fp.clear()  # both halves of the in-process cursor vanish on crash

       snap = load_session_snapshot(project)  # resolves the latest.json pointer
       assert snap is not None
       assert [m["content"][0]["text"] for m in snap["messages"]] == ["kept"]
   ```
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py::test_v2_load_latest_via_pointer_round_trip -q
   ```
   Expected: fails — `load_session_snapshot` reads the pointer's `{"session_id": ...}` as the payload, so `model`/`messages` are missing.
3. - [ ] Write minimal implementation. In `src/openharness/services/session_storage.py`:
   First, make `_sanitize_snapshot_payload` single-pass — replace its body (`session_storage.py:191-201`):
   ```python
   def _sanitize_snapshot_payload(payload: dict[str, Any]) -> dict[str, Any]:
       """Normalize persisted messages for forward compatibility (single pass)."""
       raw_messages = payload.get("messages", [])
       if not isinstance(raw_messages, list):
           return payload
       sanitized = sanitize_conversation_messages(
           [ConversationMessage.model_validate(item) for item in raw_messages]
       )
       payload = dict(payload)
       payload["messages"] = [message.model_dump(mode="json") for message in sanitized]
       payload["message_count"] = len(sanitized)
       return payload
   ```
   Add a v2 payload assembler after `_sanitize_snapshot_payload`:
   ```python
   def _load_v2_payload(session_dir: Path, session_id: str) -> dict[str, Any] | None:
       """Reassemble a v1-shaped snapshot dict from a v2 head + transcript.

       Handles V2_HEADLESS (C.3 / C.6): if the head was lost in a crash but the
       transcript is durable, resume still works off the transcript and the head
       is rebuilt on the next save. Returns None only when BOTH are absent.
       """
       head = session_format.read_head(session_dir, session_id)
       raw_messages = session_format.load_v2_snapshot(session_dir, session_id)
       if head is None and not raw_messages:
           return None
       # Head-less branch: history is preserved; head-only fields (model, usage,
       # tool_metadata) are deliberately omitted and degrade per the C.6 contract
       # (R-002) — model falls back to the runtime default, NOT null.
       payload = dict(head) if head is not None else {
           "session_id": session_id,
           "message_count": len(raw_messages),
       }
       payload["messages"] = raw_messages
       # system_prompt is rebuilt by build_runtime; loaders never read it back.
       payload.setdefault("system_prompt", "")
       return _sanitize_snapshot_payload(payload)
   ```
   Replace `load_session_snapshot` (`session_storage.py:204-209`):
   ```python
   def load_session_snapshot(cwd: str | Path) -> dict[str, Any] | None:
       """Load the most recent session snapshot for the project."""
       session_dir = get_project_session_dir(cwd)
       path = session_dir / "latest.json"
       if not path.exists():
           return None
       try:
           raw = json.loads(path.read_text(encoding="utf-8"))
       except (json.JSONDecodeError, OSError):
           return None
       if session_format.detect_latest_format(raw) == "v2":
           sid = str(raw.get("session_id") or "")
           return _load_v2_payload(session_dir, sid) if sid else None
       return _sanitize_snapshot_payload(raw)
   ```
   Replace `load_session_by_id` (`session_storage.py:297-310`):
   ```python
   def load_session_by_id(cwd: str | Path, session_id: str) -> dict[str, Any] | None:
       """Load a specific session by ID."""
       session_dir = get_project_session_dir(cwd)
       fmt = session_format.detect_session_format(session_dir, session_id)
       if fmt == "v2":
           return _load_v2_payload(session_dir, session_id)
       if fmt == "v1":
           path = session_dir / f"session-{session_id}.json"
           return _sanitize_snapshot_payload(json.loads(path.read_text(encoding="utf-8")))
       # Fallback to latest.json if it resolves to this id.
       snap = load_session_snapshot(cwd)
       if snap is not None and (snap.get("session_id") == session_id or session_id == "latest"):
           return snap
       return None
   ```
4. - [ ] Run, verify pass (new tests + the legacy-sanitize test must still pass):
   ```bash
   python -m pytest tests/test_services/test_session_storage.py -q
   ```
   Expected: all passed.
5. - [ ] Commit:
   ```bash
   git add src/openharness/services/session_storage.py tests/test_services/test_session_storage.py && git commit -m "Resolve latest.json pointer and load v2 snapshots single-pass"
   ```

### Task 10: Trust the index unconditionally + one-time backfill + compact stale entries

**Files:**
- Modify: `src/openharness/services/session_storage.py` (`list_session_snapshots` `session_storage.py:212-294`; `_write_session_index` `session_storage.py:92-98` for stale compaction; new `_backfill_index`)
- Test: `tests/test_services/test_session_storage.py`

**Design decision (sub-item d):** when the index file exists, `list_session_snapshots` returns its entries (filtered to those whose backing file still exists) regardless of count — dropping the `len(sessions) >= limit` gate at `session_storage.py:234`. When the index does *not* exist, a one-time backfill scans both `session-*.json` (v1) and `session-*.head.json` (v2) files, builds the index, and writes it once; subsequent lists are index-only. Stale entries (backing file gone) are compacted out at the *next save's* `_write_session_index` (they are currently filtered on read but never removed, `session_storage.py:220`). `latest.json` is no longer scanned as a pseudo-session under v2 (it is a pointer); the legacy `latest.json` fallback is kept only when the index is empty AND it is a v1 full payload.

**Migration contract (C.7) [P1-004].** The backfill is **idempotent** (id-keyed; re-running yields the same index) and **partial-state safe** (the whole index is written once, under `.sessions.lock`, via atomic rename — never torn; a crash mid-backfill leaves the prior/absent index and the next trigger re-runs). For a **dual-format same id** (a legacy `.json` *and* a v2 head both exist), **v2 wins**: the backfill scans `.head.json` first and the `seen` set blocks the later `.json` (C.3 precedence). The migration is **forward-only** — no down-migration; the `v1` revert switch stops new v2 writes but the sniffer keeps existing v2 sessions readable. Backfill never deletes data; retention (C.8) is the only deleter. Stale-compaction and the list filter use the format **sniffer** (not the head-file path), so a `V2_HEADLESS` session (live transcript, lost head) is kept, not dropped.

1. - [ ] Write the failing test. Add to `tests/test_services/test_session_storage.py`:
   ```python
   def test_index_trusted_below_limit(tmp_path: Path, monkeypatch):
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
       project = tmp_path / "repo"
       project.mkdir()
       for i in range(3):
           save_session_snapshot(
               cwd=project, model="m", system_prompt="s", session_id=f"s{i}",
               messages=[ConversationMessage(role="user", content=[TextBlock(text=f"m{i}")])],
               usage=UsageSnapshot(),
           )
       # limit far above count: index path must still return all three without
       # falling through to a file scan.
       got = list_session_snapshots(project, limit=50)
       assert {s["session_id"] for s in got} == {"s0", "s1", "s2"}


   def test_backfill_builds_index_from_legacy_files(tmp_path: Path, monkeypatch):
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
       project = tmp_path / "repo"
       project.mkdir()
       session_dir = get_project_session_dir(project)
       for sid in ("leg1", "leg2"):
           (session_dir / f"session-{sid}.json").write_text(
               json.dumps({"session_id": sid, "summary": sid, "message_count": 1,
                           "model": "m", "created_at": 1.0, "messages": []}),
               encoding="utf-8",
           )
       assert not (session_dir / "sessions-index.json").exists()
       got = list_session_snapshots(project, limit=10)
       assert {s["session_id"] for s in got} == {"leg1", "leg2"}
       # Backfill persisted the index.
       assert (session_dir / "sessions-index.json").exists()


   def test_stale_index_entry_compacted_on_next_write(tmp_path: Path, monkeypatch):
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
       project = tmp_path / "repo"
       project.mkdir()
       save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="keep",
                             messages=[ConversationMessage(role="user", content=[TextBlock(text="a")])],
                             usage=UsageSnapshot())
       session_dir = get_project_session_dir(project)
       # Inject a stale entry pointing at a now-missing file.
       from openharness.services.session_storage import _load_session_index, _write_session_index
       entries = _load_session_index(session_dir)
       entries.append({"session_id": "gone", "path": "session-gone.head.json",
                       "model": "m", "summary": "", "message_count": 0, "created_at": 1.0})
       _write_session_index(session_dir, entries)
       # Next save must compact the stale entry out.
       save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="keep2",
                             messages=[ConversationMessage(role="user", content=[TextBlock(text="b")])],
                             usage=UsageSnapshot())
       ids = {e["session_id"] for e in _load_session_index(session_dir)}
       assert "gone" not in ids
       assert {"keep", "keep2"} <= ids


   def test_backfill_dual_format_same_id_prefers_v2_and_is_idempotent(tmp_path: Path, monkeypatch):
       # P1-004 / C.7: a legacy .json and a v2 head for the same id -> v2 wins;
       # re-running the backfill yields the same single entry (idempotent).
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
       project = tmp_path / "repo"
       project.mkdir()
       session_dir = get_project_session_dir(project)
       (session_dir / "session-dup.json").write_text(
           json.dumps({"session_id": "dup", "summary": "v1", "message_count": 9,
                       "model": "v1model", "created_at": 1.0, "messages": []}),
           encoding="utf-8",
       )
       from openharness.services.session_format import write_head
       write_head(session_dir, "dup", {"session_id": "dup", "summary": "v2",
                  "message_count": 2, "model": "v2model", "created_at": 2.0})

       from openharness.services.session_storage import _backfill_index, _load_session_index
       first = _backfill_index(session_dir)
       dup = [e for e in first if e["session_id"] == "dup"]
       assert len(dup) == 1 and dup[0]["model"] == "v2model"  # v2 won the conflict
       second = _backfill_index(session_dir)  # idempotent
       assert {e["session_id"] for e in second} == {e["session_id"] for e in first}
       assert len(_load_session_index(session_dir)) == len(first)
   ```
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py::test_backfill_builds_index_from_legacy_files -q
   ```
   Expected: passes the listing assert but fails `assert (session_dir / "sessions-index.json").exists()` (no backfill is persisted today).
3. - [ ] Write minimal implementation. In `src/openharness/services/session_storage.py`:
   Make `_write_session_index` drop stale entries — replace its body (`session_storage.py:92-98`):
   ```python
   def _write_session_index(session_dir: Path, entries: list[dict[str, Any]]) -> None:
       # Stale-compaction: keep an entry only if its session still exists. Use the
       # sniffer, not the head-file path, so a V2_HEADLESS session (head lost in a
       # crash, transcript still present — C.3) is NOT dropped. Caller holds the
       # store lock (this is part of a read-modify-write).
       live = [
           entry
           for entry in entries
           if session_format.detect_session_format(session_dir, str(entry.get("session_id") or "")) is not None
       ]
       live = sorted(live, key=lambda item: item.get("created_at", 0), reverse=True)
       atomic_write_text(
           _session_index_path(session_dir),
           json.dumps({"version": 1, "sessions": live}, indent=2) + "\n",
           fsync=False,
       )
   ```
   Add a backfill builder after `_update_session_index` (`session_storage.py:111`):
   ```python
   def _backfill_index(session_dir: Path) -> list[dict[str, Any]]:
       """Build the index once from legacy v1 and v2 files, then persist it."""
       entries: list[dict[str, Any]] = []
       seen: set[str] = set()
       for head_file in session_dir.glob("session-*.head.json"):
           sid = head_file.stem[len("session-"):-len(".head")]
           head = session_format.read_head(session_dir, sid)
           if head is None or sid in seen:
               continue
           seen.add(sid)
           entries.append(_session_index_entry({**head, "messages": []}, head_file))
       for json_file in session_dir.glob("session-*.json"):
           if json_file.name.endswith(".head.json"):
               continue
           sid = json_file.stem.replace("session-", "")
           if sid in seen:  # a v2 head already claimed this id — v2 wins (C.3 / C.7)
               continue
           try:
               data = json.loads(json_file.read_text(encoding="utf-8"))
           except (json.JSONDecodeError, OSError):
               continue
           seen.add(sid)
           entries.append(_session_index_entry(data, json_file))
       if entries:
           # Write the whole index once under the store lock (C.7): atomic rename
           # means a reader sees the old or the new index, never a torn one; a
           # crash mid-backfill leaves the prior state and the next trigger re-runs.
           with exclusive_file_lock(session_dir / ".sessions.lock"):
               _write_session_index(session_dir, entries)
       return entries
   ```
   Replace `list_session_snapshots` (`session_storage.py:212-294`) with the index-trusting version:
   ```python
   def list_session_snapshots(cwd: str | Path, limit: int = 20) -> list[dict[str, Any]]:
       """List saved sessions for the project, newest first.

       Trusts the index whenever it exists (any count). Builds it once via a
       backfill when absent, then lists from the index forever after.
       """
       session_dir = get_project_session_dir(cwd)
       indexed = _load_session_index(session_dir)
       if not indexed:
           indexed = _backfill_index(session_dir)
       sessions: list[dict[str, Any]] = []
       for item in indexed:
           if session_format.detect_session_format(session_dir, str(item.get("session_id") or "")) is None:
               continue  # session truly gone (V2_HEADLESS counts as live — C.3)
           sessions.append(
               {
                   "session_id": item.get("session_id", ""),
                   "summary": item.get("summary", ""),
                   "message_count": item.get("message_count", 0),
                   "model": item.get("model", ""),
                   "created_at": item.get("created_at", 0),
               }
           )
       sessions.sort(key=lambda item: item.get("created_at", 0), reverse=True)
       return sessions[:limit]
   ```
4. - [ ] Run, verify pass (new tests + the existing `test_list_session_snapshots_merges_index_with_legacy_files`):
   ```bash
   python -m pytest tests/test_services/test_session_storage.py -q
   ```
   Expected: all passed.
5. - [ ] Commit:
   ```bash
   git add src/openharness/services/session_storage.py tests/test_services/test_session_storage.py && git commit -m "Trust the session index, backfill legacy files, compact stale entries"
   ```

### Task 11: Retention pruning on save

**Files:**
- Modify: `src/openharness/services/session_storage.py` (new `_prune_sessions`; call it from both save helpers)
- Test: `tests/test_services/test_session_storage.py`

**Design decision (sub-item e) [P2-004]:** after a successful save, prune oldest-first by `created_at` from the index down to `session_retention_max_files`, and drop anything older than `session_retention_max_age_days`. Per **C.8**, the prune runs **inside the same `.sessions.lock` critical section as the index update** (a lock-free `_prune_sessions_unlocked` core, single acquisition — C.2), so it can never race a concurrent saver's index write. It **never** deletes: the session just saved (active id), the id `latest.json` points at, **or any session whose files were modified within the recency window** (`mtime` newer than `max(2 × task_worker_idle_timeout_s, 60s)`) — so a *concurrent* worker actively appending another id (its transcript append happens outside the lock) is never pruned out from under itself. The window is sized to the concurrent-writer horizon (the idle timeout, with margin), **not** a fixed hour (R-003). Consequence: count/age limits only reclaim sessions *older* than the recency window — so **`session_retention_max_files` is a soft hint, not a hard cap**: the live session count can exceed `max_files` while many sessions are within the window (e.g. a burst of short sessions in a few minutes), and converges to `max_files` as they age past it. Age-pruning (`max_age_days`) bounds the long tail regardless. This is the safe semantics (never prune an in-use session); the cap is best-effort by design. Pruning deletes the backing files (v2: `.jsonl` + `.head.json`; v1: `.json`) and rewrites the index. `0` for either limit disables that rule. Pruning is wrapped so a failure never breaks the save (best-effort).

1. - [ ] Write the failing test. Add to `tests/test_services/test_session_storage.py`:
   ```python
   import time as _time


   def test_retention_prunes_oldest_keeps_active_and_latest(tmp_path: Path, monkeypatch):
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
       project = tmp_path / "repo"
       project.mkdir()
       config_dir = tmp_path / "cfg"
       config_dir.mkdir()
       (config_dir / "settings.json").write_text('{"session_retention_max_files": 2, "session_retention_max_age_days": 0}', encoding="utf-8")
       monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(config_dir))

       import os
       for i in range(3):  # s0, s1, s2 — the older sessions
           save_session_snapshot(
               cwd=project, model="m", system_prompt="s", session_id=f"s{i}",
               messages=[ConversationMessage(role="user", content=[TextBlock(text=f"m{i}")])],
               usage=UsageSnapshot(),
           )
           _time.sleep(0.01)  # distinct created_at ordering
       # Age them past the recency window so count-pruning can reclaim them (C.8);
       # without this they would be recency-protected as possibly-active.
       session_dir = get_project_session_dir(project)
       old = _time.time() - 7 * 86400
       for i in range(3):
           for suffix in (".jsonl", ".head.json"):
               os.utime(session_dir / f"session-s{i}{suffix}", (old, old))
       # The active save (s3) triggers the prune; max_files=2 keeps s3 + the newest aged.
       save_session_snapshot(
           cwd=project, model="m", system_prompt="s", session_id="s3",
           messages=[ConversationMessage(role="user", content=[TextBlock(text="m3")])],
           usage=UsageSnapshot(),
       )

       ids = {s["session_id"] for s in list_session_snapshots(project, limit=50)}
       # max_files=2 keeps the two newest; the active save (s3) is always kept.
       assert "s3" in ids
       assert len(ids) == 2
       assert "s0" not in ids


   def test_retention_age_prunes_old_sessions(tmp_path: Path, monkeypatch):
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
       project = tmp_path / "repo"
       project.mkdir()
       config_dir = tmp_path / "cfg"
       config_dir.mkdir()
       (config_dir / "settings.json").write_text('{"session_retention_max_files": 0, "session_retention_max_age_days": 1}', encoding="utf-8")
       monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(config_dir))

       import os
       session_dir = get_project_session_dir(project)
       # Inject an ancient v1 session directly into the index.
       (session_dir / "session-ancient.json").write_text(
           json.dumps({"session_id": "ancient", "summary": "old", "message_count": 1,
                       "model": "m", "created_at": 1.0, "messages": []}),
           encoding="utf-8",
       )
       # Age its mtime too, so it falls outside the recency window (C.8) and the
       # age limit can reclaim it (a fresh file would be recency-protected).
       old = _time.time() - 5 * 86400
       os.utime(session_dir / "session-ancient.json", (old, old))
       from openharness.services.session_storage import _update_session_index, _session_index_entry
       _update_session_index(session_dir, _session_index_entry(
           {"session_id": "ancient", "summary": "old", "message_count": 1, "model": "m", "created_at": 1.0},
           session_dir / "session-ancient.json"))

       save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="fresh",
                             messages=[ConversationMessage(role="user", content=[TextBlock(text="new")])],
                             usage=UsageSnapshot())
       ids = {s["session_id"] for s in list_session_snapshots(project, limit=50)}
       assert "ancient" not in ids
       assert "fresh" in ids
   ```
   > Uses the same verified `OPENHARNESS_CONFIG_DIR` mechanism as Task 8.
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py::test_retention_prunes_oldest_keeps_active_and_latest -q
   ```
   Expected: fails — all 4 sessions are retained (no pruning yet).
3. - [ ] Write minimal implementation. In `src/openharness/services/session_storage.py`, add after `_backfill_index`:
   ```python
   def _delete_session_files(session_dir: Path, session_id: str) -> None:
       for suffix in (".jsonl", ".head.json", ".json"):
           candidate = session_dir / f"session-{session_id}{suffix}"
           try:
               candidate.unlink()
           except FileNotFoundError:
               pass
           except OSError:
               pass


   def _transcript_mtime(session_dir: Path, session_id: str) -> float:
       """Newest mtime across a session's files (0.0 if none exist)."""
       newest = 0.0
       for suffix in (".jsonl", ".head.json", ".json"):
           try:
               newest = max(newest, (session_dir / f"session-{session_id}{suffix}").stat().st_mtime)
           except OSError:
               pass
       return newest


   def _prune_sessions_unlocked(session_dir: Path, *, active_id: str, settings: Any) -> None:
       """Prune oldest/aged-out sessions. Lock-free core — the caller holds
       ``session_dir / ".sessions.lock"`` (C.2). Never deletes the active id, the
       latest-pointed id, or a session modified within the recency window (C.8)."""
       max_files = int(getattr(settings, "session_retention_max_files", 0) or 0)
       max_age_days = int(getattr(settings, "session_retention_max_age_days", 0) or 0)
       if max_files <= 0 and max_age_days <= 0:
           return

       protected = {active_id}
       latest_path = session_dir / "latest.json"
       if latest_path.exists():
           try:
               raw = json.loads(latest_path.read_text(encoding="utf-8"))
               pointed = str(raw.get("session_id") or "")
               if pointed:
                   protected.add(pointed)
           except (json.JSONDecodeError, OSError):
               pass

       entries = sorted(
           _load_session_index(session_dir),
           key=lambda item: item.get("created_at", 0),
           reverse=True,
       )
       to_delete: list[str] = []
       cutoff = time.time() - max_age_days * 86400 if max_age_days > 0 else None
       idle = float(getattr(settings, "task_worker_idle_timeout_s", 600) or 600)
       # R-003: the window only needs to cover the *concurrent-writer* horizon — a
       # worker idle longer than its timeout is reaped and won't append again — so
       # base it on the idle timeout (2x margin for an append near the boundary, 60s
       # absolute minimum), NOT a fixed hour. The old `max(3600s, idle)` floor meant
       # max_files could not bound count for any session touched in the last hour.
       recency_window = max(idle * 2.0, 60.0)
       recency_floor = time.time() - recency_window
       kept = 0
       for entry in entries:
           sid = str(entry.get("session_id") or "")
           # C.8: never prune the active/latest id, nor a session whose files were
           # touched within the recency window (a concurrent worker may be appending).
           if sid in protected or _transcript_mtime(session_dir, sid) > recency_floor:
               kept += 1
               continue
           created = float(entry.get("created_at", 0) or 0)
           too_old = cutoff is not None and created < cutoff
           over_count = max_files > 0 and kept >= max_files
           if too_old or over_count:
               to_delete.append(sid)
           else:
               kept += 1
       if not to_delete:
           return
       for sid in to_delete:
           _delete_session_files(session_dir, sid)
       remaining = [
           entry
           for entry in entries
           if str(entry.get("session_id") or "") not in set(to_delete)
       ]
       _write_session_index(session_dir, remaining)
   ```
   Wire it **inside** each save's store-lock critical section (C.2 — the index update and the prune share one acquisition), calling the lock-free core. In `_save_session_snapshot_v2`, replace the `# Task 11 inserts the retention prune here` placeholder so the lock block reads:
   ```python
           with exclusive_file_lock(session_dir / ".sessions.lock"):
               _update_session_index_unlocked(
                   session_dir,
                   _session_index_entry(index_payload, session_dir / f"session-{sid}.head.json"),
               )
               try:  # retention is best-effort and must never break a save
                   from openharness.config import load_settings

                   _prune_sessions_unlocked(session_dir, active_id=sid, settings=load_settings())
               except Exception:
                   pass
   ```
   In `_save_session_snapshot_v1`, replace the lone `_update_session_index(session_dir, _session_index_entry(payload, session_path))` line with the same locked block, so v1 also serialises the index update + prune under one acquisition:
   ```python
           with exclusive_file_lock(session_dir / ".sessions.lock"):
               _update_session_index_unlocked(session_dir, _session_index_entry(payload, session_path))
               try:
                   from openharness.config import load_settings

                   _prune_sessions_unlocked(session_dir, active_id=sid, settings=load_settings())
               except Exception:
                   pass
       return latest_path
   ```
4. - [ ] Run, verify pass:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py -q
   ```
   Expected: all passed.
5. - [ ] Commit:
   ```bash
   git add src/openharness/services/session_storage.py tests/test_services/test_session_storage.py && git commit -m "Add session retention pruning on save"
   ```

### Task 12: Byte-budget benchmark + end-to-end compaction round-trip

**Files:**
- Test only: `tests/test_services/test_session_storage.py`

**Design decision:** the acceptance criterion "bytes/line drops to O(new-turn size)" is asserted by counting bytes written across two consecutive saves of a large history. We measure the *delta* written on the second save (one extra short message) by intercepting `append_jsonl_line` and the head/index writes via a tmpdir size diff. We assert the transcript-append delta is bounded (< 50 KB for a 200-message session whose append is a single short message).

1. - [ ] Write the failing test (it will pass once v2 is wired — this is the guardrail). Add to `tests/test_services/test_session_storage.py`:
   ```python
   def test_v2_append_delta_is_bounded(tmp_path: Path, monkeypatch):
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
       project = tmp_path / "repo"
       project.mkdir()
       session_dir = get_project_session_dir(project)

       big = [
           ConversationMessage(role="user", content=[TextBlock(text="x" * 1000)])
           for _ in range(200)
       ]
       save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="big",
                             messages=big, usage=UsageSnapshot())
       transcript = session_dir / "session-big.jsonl"
       size_before = transcript.stat().st_size

       big.append(ConversationMessage(role="assistant", content=[TextBlock(text="ok")]))
       save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="big",
                             messages=big, usage=UsageSnapshot())
       size_after = transcript.stat().st_size

       # Second save appended only the one new short message, not the whole history.
       assert size_after - size_before < 50_000
       assert size_after - size_before > 0


   def test_v2_compaction_shrink_round_trip(tmp_path: Path, monkeypatch):
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
       project = tmp_path / "repo"
       project.mkdir()
       from openharness.services.session_storage import load_session_by_id

       msgs = [ConversationMessage(role="user", content=[TextBlock(text=f"m{i}")]) for i in range(5)]
       save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="c",
                             messages=msgs, usage=UsageSnapshot())
       # Simulate compaction: history collapses to one summary message.
       compacted = [ConversationMessage(role="user", content=[TextBlock(text="summary")])]
       save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="c",
                             messages=compacted, usage=UsageSnapshot())

       snap = load_session_by_id(project, "c")
       assert snap is not None
       assert [m["content"][0]["text"] for m in snap["messages"]] == ["summary"]


   def test_v2_in_place_compaction_same_count_rewrites_not_stale(tmp_path: Path, monkeypatch):
       # R-001 regression: the engine compacts IN PLACE — message *content* is
       # rewritten while the message COUNT stays the same (microcompact clears old
       # tool-result bodies). A count-shrink trigger (`last_persisted > len(messages)`)
       # would take the append path, write nothing, and leave the stale bloated
       # content on disk. The fingerprint trigger must detect the divergence and
       # rewrite the transcript.
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
       project = tmp_path / "repo"
       project.mkdir()
       from openharness.services.session_storage import load_session_by_id
       from openharness.services.session_format import transcript_path

       bloated = [
           ConversationMessage(role="user", content=[TextBlock(text="BIG-OUTPUT-" + "x" * 4000)]),
           ConversationMessage(role="assistant", content=[TextBlock(text="b")]),
       ]
       save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="ip",
                             messages=bloated, usage=UsageSnapshot())
       transcript = transcript_path(get_project_session_dir(project), "ip")
       size_before = transcript.stat().st_size

       # In-place compaction: SAME count, the first message's content cleared.
       compacted = [
           ConversationMessage(role="user", content=[TextBlock(text="[cleared]")]),
           ConversationMessage(role="assistant", content=[TextBlock(text="b")]),
       ]
       assert len(compacted) == len(bloated)  # count did NOT shrink — the R-001 trap
       save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="ip",
                             messages=compacted, usage=UsageSnapshot())

       snap = load_session_by_id(project, "ip")
       assert snap is not None
       # Durable history is the COMPACTED content, not the stale bloated text...
       assert [m["content"][0]["text"] for m in snap["messages"]] == ["[cleared]", "b"]
       # ...and the transcript was actually rewritten smaller (a buggy no-op append
       # would leave it unchanged at size_before).
       assert transcript.stat().st_size < size_before
   ```
2. - [ ] Run it, verify it passes (v2 is already wired from Tasks 8–9; these lock the behavior). If `test_v2_append_delta_is_bounded` fails because the whole history was rewritten, the `compacted` logic in `_save_session_snapshot_v2` is wrong — fix it so a pure append (no content divergence) takes the append branch:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py::test_v2_append_delta_is_bounded tests/test_services/test_session_storage.py::test_v2_compaction_shrink_round_trip tests/test_services/test_session_storage.py::test_v2_in_place_compaction_same_count_rewrites_not_stale -q
   ```
   Expected: 3 passed.
3. - [ ] (If green on first run, no implementation change needed.) Simplify the redundant `if/elif` left in `_save_session_snapshot_v2` from Task 8 down to:
   ```python
           if compacted:
               session_format.rewrite_transcript(session_dir, sid, messages)
           else:
               session_format.append_messages_to_transcript(
                   session_dir, sid, messages, last_persisted_count=last_persisted
               )
   ```
4. - [ ] Run the full file again, verify pass:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py -q
   ```
   Expected: all passed.
5. - [ ] Commit:
   ```bash
   git add src/openharness/services/session_storage.py tests/test_services/test_session_storage.py && git commit -m "Assert v2 append byte-budget and compaction round-trip"
   ```

---

## Phase 4 — Crash-consistency and legacy fixtures

### Task 13: Crash-consistency at the storage layer (truncated transcript)

**Files:**
- Test only: `tests/test_services/test_session_storage.py`

**Design decision:** Task 7 proved the *primitive* recovers from a truncated line; this proves the full `load_session_by_id` path recovers a usable snapshot when the live transcript is truncated mid-append. The loader must return the last complete history and a valid dict shape.

1. - [ ] Write the test. Add to `tests/test_services/test_session_storage.py`:
   ```python
   def test_crash_truncated_transcript_loads_last_complete_history(tmp_path: Path, monkeypatch):
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
       project = tmp_path / "repo"
       project.mkdir()
       from openharness.services.session_storage import load_session_by_id

       msgs = [
           ConversationMessage(role="user", content=[TextBlock(text="a")]),
           ConversationMessage(role="assistant", content=[TextBlock(text="b")]),
       ]
       save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="crash",
                             messages=msgs, usage=UsageSnapshot())

       session_dir = get_project_session_dir(project)
       transcript = session_dir / "session-crash.jsonl"
       # Simulate a crash mid-append: tack on a partial third record.
       with open(transcript, "ab") as fh:
           fh.write(b'{"role": "user", "content": [{"type": "text", "text": "c"')

       snap = load_session_by_id(project, "crash")
       assert snap is not None
       # The partial line is dropped; the two complete messages survive.
       assert [m["content"][0]["text"] for m in snap["messages"]] == ["a", "b"]
       assert snap["message_count"] == 2
   ```
2. - [ ] Run it, verify it passes (the crash-safe reader from Task 3 + the v2 loader from Task 9 already deliver this):
   ```bash
   python -m pytest tests/test_services/test_session_storage.py::test_crash_truncated_transcript_loads_last_complete_history -q
   ```
   Expected: 1 passed. If it fails, the regression is in `read_jsonl_complete_lines` (Task 3) or `load_v2_snapshot` (Task 7) — fix there, not here.
3. - [ ] Commit:
   ```bash
   git add tests/test_services/test_session_storage.py && git commit -m "Assert v2 loader recovers from a truncated transcript"
   ```

### Task 14: Legacy v1 fixtures still load + backend shape unchanged

**Files:**
- Test only: `tests/test_services/test_session_storage.py`

**Design decision:** the strongest compat guarantee — a *full* v1 `latest.json` AND a v1 `session-<id>.json` (the exact shapes written before this change) load identically through the public functions, and the `OpenHarnessSessionBackend` returns the same dict keys it always did. This is the no-interface-break test.

1. - [ ] Write the test. Add to `tests/test_services/test_session_storage.py`:
   ```python
   def test_legacy_v1_full_latest_still_loads(tmp_path: Path, monkeypatch):
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
       project = tmp_path / "repo"
       project.mkdir()
       session_dir = get_project_session_dir(project)
       (session_dir / "latest.json").write_text(
           json.dumps({
               "session_id": "legfull", "cwd": str(project), "model": "claude-legacy",
               "system_prompt": "old system prompt", "summary": "hi", "created_at": 5.0,
               "message_count": 1, "usage": {"input_tokens": 7, "output_tokens": 8},
               "tool_metadata": {"permission_mode": "default"},
               "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
           }),
           encoding="utf-8",
       )
       snap = load_session_snapshot(project)
       assert snap is not None
       assert snap["session_id"] == "legfull"
       assert snap["model"] == "claude-legacy"
       assert snap["usage"]["output_tokens"] == 8
       assert snap["messages"][0]["content"][0]["text"] == "hi"


   def test_legacy_v1_session_file_loads_by_id(tmp_path: Path, monkeypatch):
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
       project = tmp_path / "repo"
       project.mkdir()
       from openharness.services.session_storage import load_session_by_id

       session_dir = get_project_session_dir(project)
       (session_dir / "session-legid.json").write_text(
           json.dumps({"session_id": "legid", "model": "m", "summary": "s", "created_at": 1.0,
                       "message_count": 1,
                       "messages": [{"role": "user", "content": [{"type": "text", "text": "z"}]}]}),
           encoding="utf-8",
       )
       snap = load_session_by_id(project, "legid")
       assert snap is not None and snap["session_id"] == "legid"
       assert snap["messages"][0]["content"][0]["text"] == "z"


   def test_backend_load_shape_unchanged(tmp_path: Path, monkeypatch):
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
       project = tmp_path / "repo"
       project.mkdir()
       from openharness.services.session_backend import DEFAULT_SESSION_BACKEND

       DEFAULT_SESSION_BACKEND.save_snapshot(
           cwd=project, model="m", system_prompt="s", session_id="shape",
           messages=[ConversationMessage(role="user", content=[TextBlock(text="q")])],
           usage=UsageSnapshot(input_tokens=1, output_tokens=1),
           tool_metadata={"permission_mode": "default"},
       )
       loaded = DEFAULT_SESSION_BACKEND.load_latest(project)
       assert loaded is not None
       # The public dict shape relied on by build_runtime / cli / app.
       for key in ("session_id", "model", "messages", "usage", "tool_metadata", "message_count"):
           assert key in loaded, f"missing key {key}"
       listed = DEFAULT_SESSION_BACKEND.list_snapshots(project, limit=5)
       for key in ("session_id", "summary", "message_count", "model", "created_at"):
           assert key in listed[0], f"missing list key {key}"
   ```
2. - [ ] Run it, verify it passes:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py::test_legacy_v1_full_latest_still_loads tests/test_services/test_session_storage.py::test_legacy_v1_session_file_loads_by_id tests/test_services/test_session_storage.py::test_backend_load_shape_unchanged -q
   ```
   Expected: 3 passed.
3. - [ ] Run the **whole** openharness storage + fs + format suite to confirm no regression:
   ```bash
   python -m pytest tests/test_services/test_session_storage.py tests/test_services/test_session_format.py tests/test_utils/test_fs.py -q
   ```
   Expected: all passed.
4. - [ ] Commit:
   ```bash
   git add tests/test_services/test_session_storage.py && git commit -m "Assert legacy v1 fixtures load and backend shape unchanged"
   ```

---

## Phase 5 — ohmo twin (sub-item h)

### Task 15: v2 ohmo save + load via shared primitives

**Files:**
- Modify: `ohmo/session_storage.py` (`save_session_snapshot` `ohmo/session_storage.py:92-137`, `load_latest`/`load_latest_for_session_key`/`load_by_id` `ohmo/session_storage.py:140-209`)
- Test: `tests/test_ohmo/test_ohmo_session_storage.py`

**Design decision:** ohmo reuses `session_format` for the transcript/head/pointer primitives but keeps its extra surface: the `app: "ohmo"` and `session_key` fields go into the head; `latest.json` AND `latest-<token>.json` become pointers `{"session_id": ...}`; `load_latest_for_session_key` resolves the token pointer then loads the v2 payload. Gated by the same `session_storage_format` setting (ohmo reads it via `openharness.config.load_settings`, the same source). Legacy ohmo files remain readable via the sniffer. We add an `_load_ohmo_v2_payload` mirroring `_load_v2_payload` but injecting `app`/`session_key` from the head.

**fsync + cursor (mirrors openharness) [P2-002, P1-001]:** ohmo follows **C.1** verbatim — the `.jsonl` transcript is the only durable artifact (one fsync/turn, via the shared `append_messages_to_transcript` / `rewrite_transcript` primitives); the head, the `latest.json` / `latest-<token>.json` pointers, and the index are rename-only (`fsync=False`). This is what resolves P2-002 (the policy is C.1, not restated or silently dropped here). The append cursor comes from the transcript (C.4), **not** `head.message_count`. ohmo's index read-modify-write is serialised by `exclusive_file_lock` like openharness (C.2); ohmo runs no retention sweep, so the index is its only locked store-wide write.

1. - [ ] Write the failing test. Add to `tests/test_ohmo/test_ohmo_session_storage.py`:
   ```python
   def test_ohmo_v2_save_and_load_round_trip(tmp_path: Path):
       from ohmo.session_storage import load_by_id, load_latest, save_session_snapshot
       from ohmo.workspace import initialize_workspace
       from openharness.engine.messages import ConversationMessage, TextBlock
       from openharness.api.usage import UsageSnapshot

       workspace = tmp_path / ".ohmo-home"
       initialize_workspace(workspace)
       save_session_snapshot(
           cwd=tmp_path, workspace=workspace, model="gpt-5.4", system_prompt="SYS",
           session_id="o2", session_key="feishu:chat-9",
           messages=[ConversationMessage(role="user", content=[TextBlock(text="hi")])],
           usage=UsageSnapshot(input_tokens=2, output_tokens=3),
           tool_metadata={"permission_mode": "default"},
       )
       from ohmo.session_storage import get_session_dir

       session_dir = get_session_dir(workspace)
       assert (session_dir / "session-o2.jsonl").exists()
       assert (session_dir / "session-o2.head.json").exists()
       import json
       assert json.loads((session_dir / "latest.json").read_text())["session_id"] == "o2"

       latest = load_latest(workspace)
       assert latest is not None and latest["session_id"] == "o2"
       assert latest["messages"][0]["content"][0]["text"] == "hi"
       assert latest["usage"]["output_tokens"] == 3
       byid = load_by_id(workspace, "o2")
       assert byid is not None and byid["session_id"] == "o2"


   def test_ohmo_v2_session_key_pointer_round_trip(tmp_path: Path):
       from ohmo.session_storage import load_latest_for_session_key, save_session_snapshot
       from ohmo.workspace import initialize_workspace
       from openharness.engine.messages import ConversationMessage, TextBlock
       from openharness.api.usage import UsageSnapshot

       workspace = tmp_path / ".ohmo-home"
       initialize_workspace(workspace)
       save_session_snapshot(
           cwd=tmp_path, workspace=workspace, model="gpt-5.4", system_prompt="SYS",
           session_id="o3", session_key="feishu:chat-7",
           messages=[ConversationMessage(role="user", content=[TextBlock(text="yo")])],
           usage=UsageSnapshot(),
       )
       loaded = load_latest_for_session_key(workspace, "feishu:chat-7")
       assert loaded is not None
       assert loaded["session_id"] == "o3"
       assert loaded["session_key"] == "feishu:chat-7"
       assert loaded["messages"][0]["content"][0]["text"] == "yo"
   ```
2. - [ ] Run it, verify it fails:
   ```bash
   python -m pytest tests/test_ohmo/test_ohmo_session_storage.py::test_ohmo_v2_save_and_load_round_trip -q
   ```
   Expected: fails — `session-o2.jsonl` does not exist (ohmo still writes full v1).
3. - [ ] Write minimal implementation. In `ohmo/session_storage.py`, add to the imports (after `ohmo/session_storage.py:19`):
   ```python
   from openharness.services import session_format
   from openharness.utils.file_lock import exclusive_file_lock
   ```
   Add module-level in-process persistence state (C.4 / C.5-trigger), mirroring openharness — the crash-correct cursor source that replaces `head.message_count`, plus the durable-prefix content fingerprint that detects in-place compaction (R-001):
   ```python
   _V2_CURSOR_CACHE_MAX = 1024
   _v2_persisted_count: dict[tuple[str, str], int] = {}
   _v2_persisted_prefix_fp: dict[tuple[str, str], str] = {}


   def _v2_remember_persisted(key: tuple[str, str], count: int, prefix_fp: str) -> None:
       """Record the durable (count, fingerprint) for a session, bounding the cache (R-004)."""
       _v2_persisted_count.pop(key, None)
       _v2_persisted_prefix_fp.pop(key, None)
       _v2_persisted_count[key] = count
       _v2_persisted_prefix_fp[key] = prefix_fp
       while len(_v2_persisted_count) > _V2_CURSOR_CACHE_MAX:
           oldest = next(iter(_v2_persisted_count))
           _v2_persisted_count.pop(oldest, None)
           _v2_persisted_prefix_fp.pop(oldest, None)
   ```
   Replace the body of `save_session_snapshot` from `payload = {` (`ohmo/session_storage.py:115`) through `return latest_path` (`ohmo/session_storage.py:137`) with a v1/v2 router:
   ```python
       from openharness.config import load_settings

       fmt = load_settings().session_storage_format
       if fmt == "v2":
           # Cursor + fingerprint from the durable transcript, not the non-fsync'd
           # head (C.4 / C.5-trigger / P1-001); seeded once, maintained in-process
           # (single writer per id — C.2). Mirrors openharness exactly (R-001).
           prior_head = session_format.read_head(session_dir, sid)
           created_at = prior_head.get("created_at", now) if prior_head else now
           key = (str(session_dir), sid)
           last_persisted = _v2_persisted_count.get(key)
           persisted_fp = _v2_persisted_prefix_fp.get(key)
           if last_persisted is None or persisted_fp is None:
               durable = session_format.load_v2_snapshot(session_dir, sid)
               last_persisted = len(durable)
               persisted_fp = session_format.fingerprint_messages(durable)
           # In-place compaction keeps the count but rewrites content (R-001) — detect
           # via the durable-prefix content fingerprint, not a count shrink.
           compacted = (
               len(messages) < last_persisted
               or session_format.fingerprint_messages(messages[:last_persisted]) != persisted_fp
           )
           if compacted:
               session_format.rewrite_transcript(session_dir, sid, messages)
           else:
               session_format.append_messages_to_transcript(
                   session_dir, sid, messages, last_persisted_count=last_persisted
               )
           # transcript durable; maintain cursor + fingerprint (C.4), bounded (R-004)
           _v2_remember_persisted(
               key, len(messages), session_format.fingerprint_messages(messages)
           )
           head = {
               "app": "ohmo",
               "session_id": sid,
               "session_key": session_key,
               "cwd": str(Path(cwd).resolve()),
               "model": model,
               "system_prompt_sha256": session_format.system_prompt_fingerprint(system_prompt),
               "usage": usage.model_dump(),
               "tool_metadata": _persistable_tool_metadata(tool_metadata),
               "created_at": created_at,
               "summary": summary,
               "message_count": len(messages),
           }
           session_format.write_head(session_dir, sid, head)
           # Pointer is derived/rename-only per C.1 (the transcript, fsynced once by
           # the append/rewrite primitives above, is the durable artifact).
           pointer = json.dumps({"session_id": sid}) + "\n"
           latest_path = session_dir / "latest.json"
           atomic_write_text(latest_path, pointer, fsync=False)
           if session_key:
               atomic_write_text(_session_key_latest_path(workspace, session_key), pointer, fsync=False)
           # Store-wide index read-modify-write under the same lock as openharness
           # (C.2); ohmo has no retention, so the index is its only locked store write.
           with exclusive_file_lock(session_dir / ".sessions.lock"):
               _update_session_index(
                   session_dir,
                   _session_index_entry({**head, "messages": []}, session_dir / f"session-{sid}.head.json"),
               )
           return latest_path

       payload = {
           "app": "ohmo",
           "session_id": sid,
           "session_key": session_key,
           "cwd": str(Path(cwd).resolve()),
           "model": model,
           "system_prompt": system_prompt,
           "messages": [message.model_dump(mode="json") for message in messages],
           "usage": usage.model_dump(),
           "tool_metadata": _persistable_tool_metadata(tool_metadata),
           "created_at": now,
           "summary": summary,
           "message_count": len(messages),
       }
       data = json.dumps(payload, indent=2) + "\n"
       latest_path = session_dir / "latest.json"
       atomic_write_text(latest_path, data)
       if session_key:
           atomic_write_text(_session_key_latest_path(workspace, session_key), data)
       session_path = session_dir / f"session-{sid}.json"
       atomic_write_text(session_path, data)
       # ohmo's index is multi-writer too (many chat channels); serialise its RMW
       # under the same store lock (C.2). ohmo has no retention, so a direct
       # call-site lock is sufficient (no core/wrapper split needed).
       with exclusive_file_lock(session_dir / ".sessions.lock"):
           _update_session_index(session_dir, _session_index_entry(payload, session_path))
       return latest_path
   ```
   Add the v2 payload assembler after `_update_session_index` (`ohmo/session_storage.py:89`):
   ```python
   def _load_ohmo_v2_payload(
       session_dir: Path, session_id: str, *, session_key: str | None = None
   ) -> dict[str, Any] | None:
       # Mirror openharness V2_HEADLESS recovery (C.6 / R-002a): if the head was lost
       # in a crash but the transcript is durable, resume STILL recovers history off
       # the transcript (the original code returned None here, losing the whole ohmo
       # session — the "twin in lockstep" gap). Returns None only when BOTH are absent.
       head = session_format.read_head(session_dir, session_id)
       raw_messages = session_format.load_v2_snapshot(session_dir, session_id)
       if head is None and not raw_messages:
           return None
       if head is not None:
           payload = dict(head)
       else:
           # Head-less degradation contract (C.6): history is preserved; head-only
           # fields fall back to runtime defaults. `app` is the constant "ohmo";
           # `session_key` was head-only, so it is re-injected by the session-key
           # lookup path (it knows the key) and is otherwise absent.
           payload = {"app": "ohmo", "session_id": session_id, "message_count": len(raw_messages)}
       payload["messages"] = raw_messages
       payload.setdefault("system_prompt", "")
       if session_key and not payload.get("session_key"):
           payload["session_key"] = session_key
       return _sanitize_snapshot_payload(payload)
   ```
   Replace `load_latest` (`ohmo/session_storage.py:140-144`):
   ```python
   def load_latest(workspace: str | Path | None = None) -> dict[str, Any] | None:
       session_dir = get_session_dir(workspace)
       path = session_dir / "latest.json"
       if not path.exists():
           return None
       try:
           raw = json.loads(path.read_text(encoding="utf-8"))
       except (json.JSONDecodeError, OSError):
           return None
       if session_format.detect_latest_format(raw) == "v2":
           sid = str(raw.get("session_id") or "")
           return _load_ohmo_v2_payload(session_dir, sid) if sid else None
       return _sanitize_snapshot_payload(raw)
   ```
   Replace `load_latest_for_session_key` (`ohmo/session_storage.py:147-151`):
   ```python
   def load_latest_for_session_key(workspace: str | Path | None, session_key: str) -> dict[str, Any] | None:
       path = _session_key_latest_path(workspace, session_key)
       if not path.exists():
           return None
       try:
           raw = json.loads(path.read_text(encoding="utf-8"))
       except (json.JSONDecodeError, OSError):
           return None
       if session_format.detect_latest_format(raw) == "v2":
           sid = str(raw.get("session_id") or "")
           # Pass session_key so a head-less recovery (R-002a) still carries it — the
           # lookup knows the key even when the crashed-away head did not survive.
           return _load_ohmo_v2_payload(get_session_dir(workspace), sid, session_key=session_key) if sid else None
       return _sanitize_snapshot_payload(raw)
   ```
   Replace `load_by_id` (`ohmo/session_storage.py:202-209`):
   ```python
   def load_by_id(workspace: str | Path | None, session_id: str) -> dict[str, Any] | None:
       session_dir = get_session_dir(workspace)
       fmt = session_format.detect_session_format(session_dir, session_id)
       if fmt == "v2":
           return _load_ohmo_v2_payload(session_dir, session_id)
       if fmt == "v1":
           path = session_dir / f"session-{session_id}.json"
           return _sanitize_snapshot_payload(json.loads(path.read_text(encoding="utf-8")))
       latest = load_latest(workspace)
       if latest and (latest.get("session_id") == session_id or session_id == "latest"):
           return latest
       return None
   ```
4. - [ ] Run, verify pass (new tests + the existing ohmo legacy/sanitize tests):
   ```bash
   python -m pytest tests/test_ohmo/test_ohmo_session_storage.py -q
   ```
   Expected: all passed.
5. - [ ] Commit:
   ```bash
   git add ohmo/session_storage.py tests/test_ohmo/test_ohmo_session_storage.py && git commit -m "Apply v2 head+append pattern to ohmo session storage"
   ```

### Task 16: ohmo crash-consistency + legacy fixture

**Files:**
- Test only: `tests/test_ohmo/test_ohmo_session_storage.py`

1. - [ ] Write the test. Add to `tests/test_ohmo/test_ohmo_session_storage.py`:
   ```python
   def test_ohmo_v2_recovers_from_truncated_transcript(tmp_path: Path):
       from ohmo.session_storage import get_session_dir, load_by_id, save_session_snapshot
       from ohmo.workspace import initialize_workspace
       from openharness.engine.messages import ConversationMessage, TextBlock
       from openharness.api.usage import UsageSnapshot

       workspace = tmp_path / ".ohmo-home"
       initialize_workspace(workspace)
       save_session_snapshot(
           cwd=tmp_path, workspace=workspace, model="gpt-5.4", system_prompt="s",
           session_id="oc",
           messages=[
               ConversationMessage(role="user", content=[TextBlock(text="a")]),
               ConversationMessage(role="assistant", content=[TextBlock(text="b")]),
           ],
           usage=UsageSnapshot(),
       )
       transcript = get_session_dir(workspace) / "session-oc.jsonl"
       with open(transcript, "ab") as fh:
           fh.write(b'{"role": "user", "content": [{"type": "text", "text": "c"')
       snap = load_by_id(workspace, "oc")
       assert snap is not None
       assert [m["content"][0]["text"] for m in snap["messages"]] == ["a", "b"]


   def test_ohmo_v2_recovers_when_head_lost(tmp_path: Path):
       # R-002a: a lost-head crash must NOT lose the whole ohmo session — the
       # V2_HEADLESS recovery is now mirrored from openharness. History recovers off
       # the durable transcript; the session-key lookup re-injects the key.
       from ohmo.session_storage import (
           get_session_dir, load_by_id, load_latest_for_session_key, save_session_snapshot,
       )
       from ohmo.workspace import initialize_workspace
       from openharness.engine.messages import ConversationMessage, TextBlock
       from openharness.api.usage import UsageSnapshot
       from openharness.services.session_format import head_path

       workspace = tmp_path / ".ohmo-home"
       initialize_workspace(workspace)
       save_session_snapshot(
           cwd=tmp_path, workspace=workspace, model="gpt-5.4", system_prompt="s",
           session_id="oh", session_key="feishu:chat-3",
           messages=[ConversationMessage(role="user", content=[TextBlock(text="kept")])],
           usage=UsageSnapshot(),
       )
       # Lost-head crash window: the head (rename, no fsync) is gone; transcript durable.
       head_path(get_session_dir(workspace), "oh").unlink()

       by_id = load_by_id(workspace, "oh")
       assert by_id is not None  # was None before R-002a — the whole session was lost
       assert by_id["app"] == "ohmo"
       assert [m["content"][0]["text"] for m in by_id["messages"]] == ["kept"]

       by_key = load_latest_for_session_key(workspace, "feishu:chat-3")
       assert by_key is not None
       assert by_key["session_key"] == "feishu:chat-3"  # re-injected on head-less recovery
       assert [m["content"][0]["text"] for m in by_key["messages"]] == ["kept"]


   def test_ohmo_legacy_v1_latest_still_loads(tmp_path: Path):
       import json
       from ohmo.session_storage import get_session_dir, load_latest
       from ohmo.workspace import initialize_workspace

       workspace = tmp_path / ".ohmo-home"
       initialize_workspace(workspace)
       (get_session_dir(workspace) / "latest.json").write_text(
           json.dumps({
               "app": "ohmo", "session_id": "oleg", "session_key": "feishu:chat-1",
               "cwd": str(tmp_path), "model": "gpt-legacy", "system_prompt": "old",
               "summary": "hi", "created_at": 1.0, "message_count": 1,
               "usage": {"input_tokens": 1, "output_tokens": 1}, "tool_metadata": {},
               "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
           }),
           encoding="utf-8",
       )
       snap = load_latest(workspace)
       assert snap is not None
       assert snap["session_id"] == "oleg"
       assert snap["messages"][0]["content"][0]["text"] == "hi"
   ```
2. - [ ] Run it, verify it passes:
   ```bash
   python -m pytest tests/test_ohmo/test_ohmo_session_storage.py -q
   ```
   Expected: all passed.
3. - [ ] Commit:
   ```bash
   git add tests/test_ohmo/test_ohmo_session_storage.py && git commit -m "Assert ohmo v2 crash recovery and legacy fixture load"
   ```

---

## Phase 6 — Full regression and proposal status sync

### Task 17: Full regression gate

**Files:** none (verification only).

1. - [ ] Run the complete persistence-touching surface plus the headless and CLI session paths (the loaders in `cli.py` / `ui/app.py` are unchanged, so these must pass without edits):
   ```bash
   python -m pytest tests/test_services/test_session_storage.py tests/test_services/test_session_format.py tests/test_services/test_conversation_index.py tests/test_ohmo/test_ohmo_session_storage.py tests/test_utils/test_fs.py tests/test_ui/test_headless_control.py tests/test_commands/test_cli.py -q
   ```
   Expected: all passed.
2. - [ ] Run the broader suite to catch any unexpected coupling:
   ```bash
   python -m pytest tests/test_services tests/test_ohmo tests/test_utils -q
   ```
   Expected: all passed.
3. - [ ] Manual smoke (optional, requires a real model — use the `harness-eval` skill if running it): start a session, send 3 messages, `oh --resume`, confirm context is intact and `~/.openharness/data/sessions/<project>/` contains a `.jsonl` + `.head.json` + pointer `latest.json`.

### Task 18: Update the proposal status

**Files:**
- Modify: `docs/proposals/performance-hardening-roadmap.md` (status block `performance-hardening-roadmap.md:7-14`)

1. - [ ] Update the status table row and the partial-implementation note to record WS4 as implemented behind the `session_storage_format=v2` flag, citing this plan. Change the line at `performance-hardening-roadmap.md:14` to note "WS4 (append-only session persistence + retention) shipped behind `session_storage_format=v2`; WS5 remains unscheduled," and adjust `performance-hardening-roadmap.md:7` Status if appropriate.
2. - [ ] Commit:
   ```bash
   git add docs/proposals/performance-hardening-roadmap.md && git commit -m "Mark WS4 session persistence v2 as implemented"
   ```

---

## Design decisions made here that the proposal left open

1. **Parent-dir fsync: fixed, not just documented.** Proposal said "fix or document" (`performance-hardening-roadmap.md:260`); this plan adds a best-effort directory fsync on durable writes (Task 2).
2. **Format detection from on-disk shape, not the setting.** Loaders sniff (Task 4) so a v1 file loads correctly even when the active format is `v2`; the setting gates *writes* only.
3. **Compaction detected by a durable-prefix content fingerprint, not a count shrink (R-001).** The proposal described a `compacted_at` marker but not the trigger. The first revision used a count shrink (`last_persisted > len(messages)`); the independent re-review (R-001) showed that misses the engine's **in-place** compactions — `microcompact_messages` (`compact/__init__.py:854`) clears old tool-result bodies and `try_context_collapse` (`:348`) shrinks text, both rewriting content at the **same count** — leaving stale content on disk. v2 now keeps a content fingerprint of the durable prefix (`fingerprint_messages`, seeded once from the transcript alongside the cursor) and rewrites (with the marker line) whenever the in-memory prefix's fingerprint diverges, or the count shrank. **Alternative considered and rejected — an explicit `compacted` signal threaded from the conversation runtime** (the reviewer's suggestion: `auto_compact_if_needed` already returns a correct `was_compacted` bool — note `AutoCompactState.compacted` does *not* flip on the in-place paths, so it would be a trap). Rejected because (a) it is a *distributed* invariant: the flag must be plumbed correctly through every save call site — `save_runtime_snapshot` and the manual `/session tag` save (`registry.py:900`) — and any future one, the exact "trust an unverified assumption across components" failure that produced R-001; (b) the storage-owned fingerprint is a *local* invariant verifiable in `_save_session_snapshot_v2` alone, robust to all callers, and keeps the fix inside storage (no runtime reach); (c) it costs nothing extra in practice — the save is already O(n) per call (the conversation-index `model_dump`), so an O(prefix) hash is dwarfed by it and by turn latency. If save-path CPU is ever a *measured* concern, `fingerprint_messages` can be made incremental (rolling hash, fold the appended delta) — deferred (YAGNI). Tasks 7–8, mirrored to ohmo in Task 15.
4. **System prompt: store sha256 + rely on rebuild inputs already in the head.** No new "rebuild inputs" field is added because `model` + `tool_metadata` (already persisted) are the inputs `build_runtime` uses; the full text is dropped (Task 5). Verified safe: no loader reads `system_prompt` back into a runtime.
5. **Retention runs on save, best-effort, oldest-first by `created_at`, protecting the active id and the `latest.json`-pointed id.** `0` disables each limit (Task 11).
6. **`latest.json` fallback narrowed.** The old `list_session_snapshots` treated `latest.json` as a pseudo-session; under v2 it is a pointer, so it is no longer listed separately (the pointed session is already in the index). The legacy full `latest.json` is still loaded by `load_session_snapshot` (Task 9).
7. **Index entry `path` points at the head file for v2** (`session-<id>.head.json`), at the `.json` for v1 — so the existing existence check (`session_storage.py:220`) and the new stale-compaction both work uniformly.

## Spec items and how each maps to a task

| WS4 sub-item (proposal) | Task(s) |
|---|---|
| (a) `session_storage_format` setting + format sniffer keeping legacy readable | 1, 4 |
| (b) append-only `.jsonl` + `.head.json`, delta append, compaction rewrite | 3, 6, 7, 8, 12 |
| (c) `latest.json` becomes a pointer | 8, 9 |
| (d) trust the index + one-time backfill + compact stale on write | 10 |
| (e) retention policy (max_files=50, max_age_days=30), oldest-first, never active/latest | 1, 11 |
| (f) fsync policy: 1 fsync/turn on transcript append; atomic-rename no per-write fsync for head/index; fix parent-dir fsync | 2, 3, 6, 8 |
| (g) single-pass resume load (drop validate→dump→re-validate) | 9 |
| (h) head+append pattern in `ohmo/session_storage.py` | 15, 16 |
| (i) stop persisting full built system prompt (hash + rebuild inputs) | 5, 8 |
| crash-consistency tests (truncate mid-append → recover to last complete line) | 3, 7, 13, 16 |
| legacy-format fixture tests (v1 files still load) | 10, 14, 16 |
| public dict shapes unchanged (no interface break) | 9, 14 |

## Assumptions left in the plan (clearly marked)

- **The `prior_head is not None and last_persisted == 0` edge clause** left in Task 8's `_save_session_snapshot_v2` is explicitly flagged inline as a no-op to be simplified in Task 12 step 3. It is the only deliberately-temporary code in the plan.
- **`watchdog.track` / `record` diagnostics calls** in the v2 save helper mirror the v1 ones verbatim; if the `watchdog` module's `track` signature has changed since `session_storage.py:152`, copy the current call exactly (it is lifted unmodified from the existing body, so it cannot drift unless the existing code does).

No other open assumptions: the settings-override mechanism (`OPENHARNESS_CONFIG_DIR`), the no-read-back of `system_prompt` on resume, and the public loader dict shapes were all verified against the current code while writing this plan.

---

## Phase 7 — Format-consumer fixes (post-merge review, PMR-001..004)

These close the C.9 blast-radius gap: code that reads sessions *by file shape* instead of through the loader, which v2-as-default would silently break. All four verified against the real code (file:line in C.9). Each is a small, explicit, test-first change.

### Task 19: `/session tag` exports a full snapshot under v2 (PMR-001)

**Files:**
- Modify: `src/openharness/services/session_backend.py` (protocol + `OpenHarnessSessionBackend`), `ohmo/session_storage.py` (ohmo backend), `src/openharness/commands/registry.py` (`registry.py:914`)
- Test: `tests/test_services/test_session_storage.py` (backend unit) **and `tests/test_commands/test_registry.py` (command-level — the PMR-001 regression that exercises the actual `/session tag` handler)**

**Design decision:** `/session tag NAME` does `shutil.copy2(save_snapshot()→latest_path, <tag>.json)`. Under v2 `latest_path` is the pointer, so the export becomes `{"session_id": ...}`. Add an explicit `export_snapshot_json(*, cwd, dest)` to the backend (parallel to the existing `export_markdown`) that obtains the **full v1-shaped payload from the v2-aware loader** and writes it — correct for both formats (v1: loader reads the full `latest.json`; v2: loader reassembles head+transcript). `/session tag` calls it instead of copying the pointer. The just-saved session is the project's latest, so `load_session_snapshot(cwd)` returns it. **PMR-001 is specifically about the command**, so the binding regression runs the `/session tag` handler end-to-end and asserts the tagged file is a full snapshot — proving the raw `.json` copy is gone (a backend-only test would not exercise `registry.py`).

1. - [ ] Failing tests. **(a) Backend unit** (`tests/test_services/test_session_storage.py`):
   ```python
   def test_session_tag_export_is_full_snapshot_under_v2(tmp_path: Path, monkeypatch):
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
       from openharness.services.session_backend import OpenHarnessSessionBackend
       project = tmp_path / "repo"; project.mkdir()
       backend = OpenHarnessSessionBackend()
       backend.save_snapshot(cwd=project, model="m", system_prompt="s",
                             messages=[ConversationMessage(role="user", content=[TextBlock(text="hello")])],
                             usage=UsageSnapshot(), session_id="t1")
       dest = get_project_session_dir(project) / "mytag.json"
       backend.export_snapshot_json(cwd=project, dest=dest)
       payload = json.loads(dest.read_text())
       assert payload["session_id"] == "t1"
       assert payload["messages"][0]["content"][0]["text"] == "hello"  # full snapshot, not a pointer
   ```
   **(b) Command-level regression** (`tests/test_commands/test_registry.py` — the PMR-001 binding proof; mirrors that file's `_make_context`/`registry.lookup` pattern):
   ```python
   @pytest.mark.asyncio
   async def test_session_tag_command_exports_full_snapshot_under_v2(tmp_path: Path, monkeypatch):
       # PMR-001 at the COMMAND level: exercises registry.py's /session tag handler and
       # proves it no longer does a raw .json copy (which under v2 = the pointer).
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))  # v2 is the default format
       from openharness.services.session_backend import OpenHarnessSessionBackend
       from openharness.services.session_storage import get_project_session_dir
       ctx = _make_context(tmp_path)              # existing helper (engine, cwd, tool_registry, app_state)
       ctx.session_backend = OpenHarnessSessionBackend()   # the handler reads context.session_backend
       command, args = registry.lookup("/session tag mytag")
       await command.handler(args, ctx)
       tagged = get_project_session_dir(tmp_path) / "mytag.json"
       payload = json.loads(tagged.read_text())
       # The discriminator: a v2 pointer is exactly {"session_id": ...}; a full snapshot has "messages".
       assert "messages" in payload and payload.get("session_id")
       assert set(payload) != {"session_id"}   # NOT a raw copy of the pointer latest.json
   ```
   (If `_make_context` doesn't expose `session_backend`, set it on the context or extend the helper — the point is the real handler runs against a v2 store.)
2. - [ ] Implement. In `session_backend.py` add to the `SessionBackend` protocol and `OpenHarnessSessionBackend`:
   ```python
   def export_snapshot_json(self, *, cwd: str | Path, dest: Path) -> Path:
       """Write a full v1-shaped snapshot (loader-built, v2-aware) to ``dest``."""
       payload = session_storage.load_session_snapshot(cwd)
       if payload is None:
           raise FileNotFoundError("no session to export")
       dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
       return dest
   ```
   (ohmo backend mirrors it via `ohmo.session_storage.load_latest`.) In `registry.py` replace `shutil.copy2(snapshot_path, tagged_json)` with `context.session_backend.export_snapshot_json(cwd=context.cwd, dest=tagged_json)`.
3. - [ ] Run + commit (`Export full snapshot for /session tag under v2 (PMR-001)`).

### Task 20: `list_session_snapshots` surfaces index-missing sessions via the sniffer (PMR-002)

**Files:**
- Modify: `src/openharness/services/session_storage.py` (`list_session_snapshots`, amends Task 10), `ohmo/session_storage.py` (`list_snapshots`, `:179`)
- Test: `tests/test_services/test_session_storage.py`, `tests/test_ohmo/test_ohmo_session_storage.py`

**Design decision:** **Amends Task 10.** C.7 says backfill triggers when the index is absent **or missing entries**, but the copyable `list_session_snapshots` only scans disk when `_load_session_index()` is empty, and it scans only `session-*.json` — so a present-but-incomplete index hides on-disk sessions, and under v2 the `.json` glob finds nothing. Replace the empty-only trigger with a **sniffer-based missing-entry merge**: after adding indexed entries, enumerate on-disk session ids via the shared `session_ids_on_disk` (Task 21 — v1 `session-*.json` **and** v2 `session-*.head.json`/`session-*.jsonl`, deduped v2-wins, with the `.head.json` skip), and for each id not already present, build an entry **from the v2-aware loader core `_load_snapshot_in_dir` (Task 21)** — `_session_index_entry(loaded_payload, ...)` — **not** `read_head`. This matters for **head-less v2** (transcript present, head lost in a crash — C.6): `read_head` returns `None` for it, which would silently drop a recoverable session from listing; the loader reassembles it (degraded `model`/`summary` per C.6) so it still appears. Drop the `len(sessions) >= limit` early-return that currently short-circuits the legacy scan. Mirror the same in ohmo's `list_snapshots`.

1. - [ ] Failing test (v2 session missing from a non-empty index still lists — **including a head-less one**):
   ```python
   def test_list_surfaces_v2_session_absent_from_index(tmp_path: Path, monkeypatch):
       monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
       project = tmp_path / "repo"; project.mkdir()
       # A: indexed via save. B: v2 head+transcript on disk, NOT in index. C: HEAD-LESS v2 (transcript only).
       save_session_snapshot(cwd=project, model="m", system_prompt="s", session_id="A",
                             messages=[ConversationMessage(role="user", content=[TextBlock(text="a")])], usage=UsageSnapshot())
       sdir = get_project_session_dir(project)
       session_format.append_messages_to_transcript(sdir, "B", [ConversationMessage(role="user", content=[TextBlock(text="b")])], last_persisted_count=0)
       session_format.write_head(sdir, "B", {"session_id": "B", "message_count": 1, "created_at": 1.0, "model": "m", "summary": ""})
       session_format.append_messages_to_transcript(sdir, "C", [ConversationMessage(role="user", content=[TextBlock(text="c")])], last_persisted_count=0)
       # C has NO head.json — the V2_HEADLESS case; it must still surface via the loader.
       ids = {s["session_id"] for s in list_session_snapshots(project, limit=50)}
       assert {"A", "B", "C"} <= ids   # head-less C surfaces too (loader-based derivation, not read_head)
       # .head.json trap: `glob("session-*.json")` ALSO matches `session-<id>.head.json`, yielding phantom
       # ids "B.head" etc. (a lone `.head.json` sniffs as v1). The shared enumerator skips them — assert so.
       assert not any(i.endswith(".head") for i in ids)
   ```
2. - [ ] Implement the missing-entry merge via `session_ids_on_disk` + `_load_snapshot_in_dir` (Task 21 — the shared enumerator/loader that strip/skip `.head.json` and reassemble head-less v2); mirror in ohmo. Task 10's body backfills only when the index is **absent**; this extends it to **missing entries** (C.7). The existing legacy-merge test must stay green.
3. - [ ] Run + commit (`Surface index-missing v1+v2 (incl. head-less) sessions in listing (PMR-002)`).

### Task 21: `conversation_index.rebuild` is v2-aware (PMR-003)

**Files:**
- Modify: `src/openharness/services/conversation_index.py` (`rebuild`, `:343-361`), `src/openharness/services/session_storage.py` (expose a dir-based loader core)
- Test: `tests/test_services/test_conversation_index.py`

**Design decision:** `rebuild()` wipes the index then reindexes `glob("*/session-*.json")` — zero v2 sessions, so on a v2 store rebuild empties search. Factor `load_session_by_id`'s body into a `_load_snapshot_in_dir(session_dir, session_id)` core (sniffer → `_load_v2_payload` for v2, `.json` read for v1) and reuse it. `rebuild()` enumerates each project dir under `get_sessions_dir()`, collects v1+v2 session ids (deduped, v2-wins), loads each full payload via that core, and `index_snapshot`s it. The `session_storage` import in `rebuild` is **local/lazy** — `session_storage` only imports `conversation_index` lazily today (inside `_update_conversation_index`), so there is no module-load cycle now; the local import keeps it that way regardless of future top-level imports. A head-less v2 session reassembles to `{session_id, message_count, messages, system_prompt:""}`; `index_snapshot` indexes it fine (it needs only `session_id`+`messages`; `cwd`/`model`/`summary` default), so rebuild degrades gracefully per C.6.

1. - [ ] Failing test: extend `test_rebuild_from_snapshots` (or add `test_rebuild_indexes_v2_sessions`) — save a session under v2 default, `rebuild()`, assert `count >= 1` and a search hit.
2. - [ ] Implement:
   ```python
   def rebuild(self) -> int:
       from openharness.config.paths import get_sessions_dir
       from openharness.services import session_storage  # lazy: avoid import cycle
       self._write(_clear)
       count = 0
       for project_dir in sorted(p for p in get_sessions_dir().iterdir() if p.is_dir()):
           for sid in session_storage.session_ids_on_disk(project_dir):  # v1 + v2, deduped
               payload = session_storage._load_snapshot_in_dir(project_dir, sid)
               if payload is None:
                   continue
               self.index_snapshot(payload); count += 1
       return count
   ```
   Add `session_ids_on_disk` + `_load_snapshot_in_dir` to `session_storage` (`load_session_by_id` delegates to the latter). **`session_ids_on_disk` must handle the `.head.json` glob collision** — `glob("session-*.json")` matches `session-<id>.head.json` too, so derive ids precisely:
   ```python
   def session_ids_on_disk(session_dir: Path) -> list[str]:
       """v1 + v2 session ids present on disk (deduped; v2 and v1 share the id space)."""
       ids: dict[str, None] = {}  # insertion-ordered set
       for p in session_dir.glob("session-*.head.json"):      # v2 head
           ids.setdefault(p.name[len("session-"):-len(".head.json")], None)
       for p in session_dir.glob("session-*.jsonl"):           # v2 transcript (headless too)
           ids.setdefault(p.stem[len("session-"):], None)
       for p in session_dir.glob("session-*.json"):            # v1 — but this ALSO matches *.head.json
           if p.name.endswith(".head.json"):
               continue
           ids.setdefault(p.stem[len("session-"):], None)
       return list(ids)
   ```
   This is the same `.head`-stripping/skip pattern Task 10's `_backfill_index` already uses; `list_session_snapshots` (Task 20) and `rebuild` (here) share this one enumerator so the trap is fixed in a single place.
3. - [ ] Run + commit (`Make conversation-index rebuild v2-aware (PMR-003)`).

### Task 22: auto-dream discovers and prompts for v1+v2 sessions (PMR-004)

**Files:**
- Modify: `src/openharness/services/autodream/lock.py` (`list_sessions_touched_since`, `:118`), `src/openharness/services/autodream/prompt.py` (`:80`)
- Test: `tests/test_services/test_autodream*.py` (the lock/discovery test)

**Design decision:** `list_sessions_touched_since` globs only `session-*.json` by mtime, so under v2 it returns `[]` and auto-dream silently stops triggering. Scan **both** `session-*.json` (v1) and `session-*.jsonl` (v2 transcript — the per-turn fsync'd artifact whose mtime tracks activity), merge by mtime, dedupe by id. **Trap:** `glob("session-*.json")` *also matches* `session-<id>.head.json` (its stem is `session-<id>.head`), so the v1 branch must **`continue` on `name.endswith(".head.json")`** — otherwise auto-dream feeds a phantom id `<id>.head` to the dream worker. (The `.jsonl` glob is clean.) Update the consolidation prompt to reference both shapes.

1. - [ ] Failing test:
   ```python
   def test_list_sessions_touched_since_finds_v2_transcripts(tmp_path: Path):
       from openharness.services.autodream.lock import list_sessions_touched_since
       from openharness.services import session_format
       session_format.append_messages_to_transcript(tmp_path, "v2sess",
           [ConversationMessage(role="user", content=[TextBlock(text="x")])], last_persisted_count=0)
       session_format.write_head(tmp_path, "v2sess", {"session_id": "v2sess", "message_count": 1, "created_at": 1.0})
       ids = list_sessions_touched_since(tmp_path, since_ts=0.0, session_dir=tmp_path)
       assert "v2sess" in ids                 # v2 transcript discovered
       assert "v2sess.head" not in ids        # .head.json glob-collision phantom must NOT appear
   ```
2. - [ ] Implement: glob `session-*.jsonl` (v2) + `session-*.json` (v1, **skipping `*.head.json`**), sort merged by mtime, dedupe by id. In `prompt.py:80` change to: ``2. Recent session transcripts (`session-*.jsonl`, or legacy `session-*.json`) when you need concrete context.``
3. - [ ] Run + commit (`Auto-dream discovers v1+v2 sessions and prompts for both (PMR-004)`).

### Task 23: full regression re-run (post-PMR)

1. - [ ] `python -m pytest tests/test_services/ tests/test_ohmo/ -q` — all green, including the previously-failing `test_rebuild_from_snapshots`, the legacy-merge listing test, and the new PMR regression tests.

---

## Quality Gate (design-quality-gate v3 — Tier T2)

> **Tier T2** (storage-format migration · persisted-state shape change · multi-step save lifecycle · one-time backfill · retention deletion · format flag).
> First run 2026-06-13 — NOT CLEARED (5 P1, 6 P2). Revision pass + author self-re-run resolved all 14 original findings (C.1–C.8 contracts + task-body fixes). **1st independent adversarial re-review 2026-06-13 (fresh agent): NOT CLEARED** — the original 14 were genuinely resolved, but it found a load-bearing **P1 (R-001)** the count-shrink compaction trigger misses, an ohmo crash-recovery **P2 (R-002)**, and two **P3s (R-003/R-004)** — see Q.4.1. **Fix pass 2026-06-13 (fresh session): all four resolved** — R-001 by a storage-local durable-prefix content fingerprint (explicit-signal alternative considered and rejected; mirrored to ohmo), R-002 by mirroring V2_HEADLESS recovery to ohmo + a defined head-less degradation contract, R-003/R-004 as recorded. **2nd independent adversarial re-review 2026-06-13: CLEARED** — verified every fix against the real source (incl. empirically confirming the fingerprint-equality assumption) and could not break them. **Owner promoted DRAFT → APPROVED 2026-06-13** (gate cleared was the prerequisite); merged to `main`. **Post-merge review 2026-06-13 (owner): four P1 format-consumer breaks (PMR-001..004, Q.4.2)** — code reading sessions by old file shape (`/session tag`, `list_session_snapshots`, `conversation_index.rebuild`, auto-dream) that v2-default would silently break; the first three gate runs missed the whole class because none enumerated the blast radius. **PMR fix pass 2026-06-13 (fresh session): all four resolved** — added the **C.9 format-consumer inventory** (root-cause fix) + Phase 7 (Tasks 19–23) + ohmo mirror. **3rd independent adversarial re-gate: CLEARED** — independently confirmed C.9 is exhaustive (no other consumer) and verified the fixes; one P2 it raised (the `.head.json` glob collision) is closed. This is a *design* gate: the tests below are *specified* as TDD steps and are executed when the plan is implemented, not as part of the gate.

### Q.1 Canonical contract surfaces

| Surface | Canonical? | Resolved by | Checked |
|---|---|---|---|
| `services/session_format.py` (sniffer, transcript primitives, head r/w, hash, `transcript_live_count`) | yes | the v2 format authority; sniffer now covers V2_HEADLESS / CONFLICT (C.3) | [x] |
| `services/session_storage.py` (save / load / list / retention) | yes | writer authority C.2; states C.3; cursor C.4 | [x] |
| `utils/fs.py` (atomic write + append + crash-safe read) | yes | C.1 | [x] |
| `ohmo/session_storage.py` (v2 twin) | mirrors `session_format` + C.1 | P2-002 resolved (cites C.1); cursor + index lock mirrored | [x] |
| `config/settings.py` (format + retention settings) | yes | — | [x] |
| `latest.json` / `latest-<token>.json` (pointer) | yes | P2-005 — precedence + missing-head fallback (C.6) | [x] |
| `sessions-index.json` (trusted + backfill + lock) | yes | P1-004 migration contract (C.7); store lock (C.2) | [x] |
| **Format-consumer inventory (C.9)** — every code path reading sessions by shape | yes | C.9 enumerates them; first three gate runs lacked this (PMR-001..004) | [x] |
| `commands/registry.py` (`/session tag` export; `/session ls`) | consumer (C.9-B) | PMR-001 / Task 19 — `export_snapshot_json`, command-level test; `/session ls` P3 cosmetic | [x] |
| `services/session_backend.py` (`export_snapshot_json`) | consumer (C.9-B) | PMR-001 / Task 19 — full v1-shaped export via the loader (OpenHarness + ohmo) | [x] |
| `services/conversation_index.py` (`rebuild`) | consumer (C.9-B) | PMR-003 / Task 21 — v2-aware rebuild via `session_ids_on_disk` + `_load_snapshot_in_dir` | [x] |
| `services/autodream/lock.py` + `autodream/prompt.py` | consumer (C.9-B) | PMR-004 / Task 22 — v1+v2 discovery scan + prompt | [x] |
| `ohmo/session_storage.py` `list_snapshots` (PMR-002 mirror) | mirrors C.9-B | PMR-002 / Task 20 — same missing-entry merge as openharness | [x] |
| Tests (`test_session_format` / `test_session_storage` / `test_ohmo` / `test_fs` / `test_commands` / `test_conversation_index` / `test_autodream`) | mirror | P2-006 — proof types classified (Q.3.1); P1-001/P1-003 + R-fix + PMR regression tests | [x] |

### Q.2 State / Handoff Invariants

- [x] Every entry shape has one parser + one dispatch behavior — **P2-003 resolved** (typed marker dispatch: marker iff `__compacted_at__` and no `role`, C.5; collision test).
- [x] Every writer has an explicit allowlist — **P1-003 resolved** (writer-authority table C.2; store-wide writes under `exclusive_file_lock`; WS1↔WS4 noted).
- [x] Every multi-step handoff defines partial-failure behavior — **P1-001 resolved** (save partial-failure matrix C.4; cursor from transcript; lost-head test).
- [x] Every durable artifact has ownership, collision, recovery, deletion/supersede semantics — **P2-004 resolved** (retention under lock + recency protection C.8; C.2 ownership; C.3 supersede).
- [x] Every repeated rule names its canonical source — **F-001 resolved** (the v2 save / fsync / cursor / compaction rules live once in C.1–C.8; Task 8 and Task 15 cite them; the two save *bodies* differ only by module-specific head fields).

### Q.3 Quality Checklist

- [x] Touched surfaces listed (File Structure table)
- [x] Canonical source for each rule declared — C.1–C.8; tasks cite by section
- [x] Change type classified — additive + **migration** (C.7) + format flag; forward-only
- [x] Copyable prescriptions contain no known-bad text — **P3-002 resolved** (clean `if compacted: rewrite else: append`)
- [x] Claim-to-evidence traceability — Q.3.1 (required-behavior / proof-type / coverage)
- [x] Proof type classified for every test — **P2-006** (Q.3.1)
- [x] Writer authority table — **P1-003** (C.2)
- [x] Read/write fallback defined — **P2-005** (C.6)
- [x] Tests map to all P1/P2 risks — **P2-006** (Q.3.1; P1-001 lost-head + P1-003 concurrency added)
- [N/A — plan adds no CI job, workflow, or merge gate] CI classified signal-only vs merge-blocking
- [x] Hot-reload safety / schema bump declared — **P1-003** (format decided per-save; CONFLICT branch C.3; index `version`; head shape is the v2 signal — C.2)
- [x] State machine included — **P1-005** (C.3: format-detection states + named halt/conflict branches + lifecycle)
- [x] Partial-failure matrix included — **P1-001** (C.4)
- [x] Migration contract included — **P1-004** (C.7: idempotency / partial-state / dual-format precedence / forward-only)

### Q.3.1 Proof-type classification & coverage (P2-006)

| Risk / behavior | Proof type | Test(s) (specified — run at implementation) |
|---|---|---|
| P1-001 lost-head → no duplicate history | behavioral | `test_v2_lost_head_does_not_duplicate_on_next_save` |
| P1-003 concurrent index writes → no lost entry | behavioral | `test_concurrent_v2_saves_preserve_all_index_entries` |
| P2-003 marker vs message dispatch | behavioral | `test_record_with_marker_key_but_role_is_a_message_not_a_marker` |
| P2-005 missing-head recovery (V2_HEADLESS) | behavioral | `test_v2_load_via_pointer_recovers_when_head_missing` |
| R-001 in-place compaction → fingerprint differs | behavioral | `test_fingerprint_messages_detects_in_place_content_change` |
| R-001 same-count in-place compaction → rewrite, not stale-append | behavioral | `test_v2_in_place_compaction_same_count_rewrites_not_stale` |
| R-002a ohmo lost-head → recovers off transcript | behavioral | `test_ohmo_v2_recovers_when_head_lost` |
| PMR-001 `/session tag` exports a full snapshot (command-level + backend) | behavioral | `test_session_tag_command_exports_full_snapshot_under_v2` (command), `test_session_tag_export_is_full_snapshot_under_v2` (backend) |
| PMR-002 listing surfaces index-missing v2 sessions, incl. **head-less**, no `.head` phantom | behavioral | `test_list_surfaces_v2_session_absent_from_index` (+ ohmo mirror) |
| PMR-003 conversation-index rebuild indexes v2 sessions | behavioral | `test_rebuild_indexes_v2_sessions` (extends `test_rebuild_from_snapshots`) |
| PMR-004 auto-dream discovers v2 transcripts, no `.head` phantom | behavioral | `test_list_sessions_touched_since_finds_v2_transcripts` |
| P1-004 dual-format precedence + idempotency | behavioral | `test_backfill_dual_format_same_id_prefers_v2_and_is_idempotent` |
| P1-005 format detection (V2_HEADLESS / CONFLICT) | behavioral | `test_detect_session_format_headless_transcript_is_v2`, `…_v1_v2_conflict_prefers_v2` |
| P2-004 retention (count + age, protect active/latest/recent) | behavioral | `test_retention_prunes_oldest_keeps_active_and_latest`, `test_retention_age_prunes_old_sessions` |
| cursor seed / live-count | behavioral | `test_transcript_live_count_counts_post_marker_records` |
| crash-truncated transcript recovery | behavioral | `test_load_v2_recovers_from_truncated_final_line` |
| C.1 parent-dir fsync policy | behavioral | `test_atomic_write_fsyncs_parent_dir`, `test_atomic_write_no_dir_fsync_when_fsync_false` |
| public shapes unchanged / legacy readable | structural + behavioral | v2 round-trips, legacy fixtures (Task 14), no-interface-break test |

### Q.4 Review Findings

| ID | Severity | Status | Resolution / Evidence |
|---|---|---|---|
| P1-001 | P1 | **resolved** | Cursor from the durable transcript (in-process count seeded by `transcript_live_count`), never `head.message_count`; partial-failure matrix C.4; clean branch; lost-head behavioral test; mirrored in ohmo (Task 15). |
| P1-002 | P1 | **resolved** | This gate section (re-run). |
| P1-003 | P1 | **resolved** | Writer-authority table C.2; store-wide index/pointer/retention under `exclusive_file_lock` (single acquisition, lock-free cores, no nesting); v1, v2 and ohmo covered; concurrency behavioral test; WS1↔WS4 noted. |
| P1-004 | P1 | **resolved** | Migration contract C.7 + Task 10: idempotent, partial-state-safe under lock, dual-format v2-wins (code + test), forward-only; sniffer-based stale-compaction keeps V2_HEADLESS. |
| P1-005 | P1 | **resolved** | State machine C.3: format-detection states (incl. V2_HEADLESS, TRUNCATED_TAIL, CONFLICT/v2-wins) + append→compaction→prune lifecycle; sniffer implements them (Task 4). |
| P2-001 | P2 | **resolved** | Reworded to documents-only (the claim was a no-op vs the code); the real storage↔runtime round-trip removal is explicitly deferred (YAGNI / one-time cost). |
| P2-002 | P2 | **resolved** | fsync policy stated once canonically (C.1); ohmo cites it and writes pointers rename-only (`fsync=False`). |
| P2-003 | P2 | **resolved** | Typed dispatch C.5 (marker iff key present and no `role`); raw-line collision test. |
| P2-004 | P2 | **resolved** | Retention runs inside the store lock (C.2) and protects active/latest/recently-modified sessions (recency window, C.8); count + age tests age fixtures past the window. |
| P2-005 | P2 | **resolved** | C.6 precedence + missing-head fallback; `_load_v2_payload` recovers from the transcript when the head is gone; sniffer keeps V2_HEADLESS; pointer-recovery test. |
| P2-006 | P2 | **resolved** | Proof-type classification (Q.3.1); behavioral tests for P1-001 and P1-003 added. |
| P3-001 | P3 | **resolved** | Citation corrected to `session_storage.py:220`. |
| P3-002 | P3 | **resolved** | Ships the clean `if compacted: rewrite else: append`; rationale moved to C.4 prose. |
| P3-003 | P3 | **resolved** | Single unconditional `lines.pop()`. |
| P3-004 | P3 | **resolved** | The in-process persistence caches (`_v2_persisted_count` + `_v2_persisted_prefix_fp`) are now **bounded** (R-004): `_v2_remember_persisted` enforces an LRU cap (`_V2_CURSOR_CACHE_MAX`) on both, evicting the oldest entry; an evicted session re-seeds from its transcript on its next save. A long-lived foreground process that resumes many sessions can no longer grow them without limit. (Originally accepted as negligible; promoted to fixed when the fingerprint cache was added by R-001, which made the bound worth enforcing.) |

### Q.4.1 Independent re-review findings (2026-06-13, fresh agent)

| ID | Sev | Location | Finding | Status |
|---|---|---|---|---|
| R-001 | **P1** | C.5 / Design decision 3 / `_save_session_snapshot_v2` (+ ohmo) | The compaction trigger `compacted = last_persisted > len(messages)` only detects a count **shrink**. The engine's in-place compactions change message *content* without shrinking the count — `microcompact_messages` clears tool-result bodies in place (same count, `compact/__init__.py:854`) and `try_context_collapse` collapses content (same count). So the save takes the **append** path, `messages[last_persisted:]` is empty, nothing is written, and the durable transcript keeps **stale pre-compaction content** → resume shows stale history and the byte-budget is defeated. The plan's "engine messages are append-only otherwise" assumption is false (verified against the compaction code). **Must fix before APPROVED** — e.g. an explicit `compacted` signal from the runtime (the single writer knows when it compacted), or content-divergence detection; count-shrink alone is insufficient. | **resolved** — content-divergence chosen: durable-prefix fingerprint (`fingerprint_messages`, C.5-trigger) replaces the count test in `_save_session_snapshot_v2` and ohmo (Design decision 3 records the rejected explicit-signal alternative). Unit + integration regression tests (same-count in-place compaction must rewrite). 2nd independent re-review verified the fix and the fingerprint-equality assumption against the real `ConversationMessage` model. |
| R-002 | P2 | `_load_ohmo_v2_payload`; `_load_v2_payload` V2_HEADLESS payload | (a) ohmo's load returns `None` when the head is missing even if the transcript is durable, so a lost-head crash loses the whole ohmo session — the openharness V2_HEADLESS recovery (C.6) was **not mirrored** to ohmo (contradicts the "twin in lockstep" claim). (b) Even in openharness, the V2_HEADLESS payload reconstructs only `{session_id, message_count, messages}` — no `model`/`usage`/`tool_metadata`, so resume hands `build_runtime` a null model: history recovers, runtime config does not. Mirror the recovery to ohmo and define the head-less config fallback. | **resolved** — (a) `_load_ohmo_v2_payload` now mirrors the V2_HEADLESS recovery (recovers off the transcript, injects `app`, re-injects `session_key` on the key path); test `test_ohmo_v2_recovers_when_head_lost`. (b) C.6 now defines the head-less degradation contract; the "null model" wording was **corrected** — `merge_cli_overrides` drops `None` (`settings.py:935`) so resume falls back to the configured model, not null (re-review confirmed). |
| R-003 | P3 | C.8 / `_prune_sessions_unlocked` | The recency window `max(3600s, idle)` = 1h protects every session touched in the last hour from **both** count and age pruning, so `session_retention_max_files` does not bound count in the common "many short sessions within an hour" case (age-pruning still bounds long-term). Disclose that `max_files` is a soft hint sub-hour, and/or lower the floor toward the worker idle timeout. | **resolved** — both: recency window is now `max(2 × idle_timeout, 60s)` (the concurrent-writer horizon, ~20 min default) not a fixed hour, and C.8 / Task 11 disclose `max_files` as a soft hint (age-pruning bounds the tail). Re-review confirmed no P2-004 regression. |
| R-004 | P3 | gate wording / P3-004 | The "0 unaccepted findings / none merely accepted" phrasing over-reads next to the accepted P3-004; and P3-004's "bounded per process" omits a long-lived foreground process that resumes many sessions (the cache never evicts). Tighten wording + add cache eviction or a bounded note. | **resolved** — both in-process caches are now LRU-bounded (`_v2_remember_persisted`, `_V2_CURSOR_CACHE_MAX`), so P3-004 is promoted from accepted to **fixed** (no merely-accepted finding remains); evicted sessions re-seed from the transcript. |

### Q.4.2 Post-merge review findings (2026-06-13)

| ID | Sev | Location | Finding | Status |
|---|---|---|---|---|
| PMR-001 | **P1** | Task 8 return-path claim; `commands/registry.py` `/session tag` | The plan says the v2 save return value can stay `latest_path` because callers only use it as a truthy path, but `/session tag NAME` calls `save_snapshot()`, then `shutil.copy2(snapshot_path, tagged_json)`. Under v2 that copies the pointer-only `latest.json` into `<tag>.json`, silently replacing a full tagged snapshot export with `{"session_id": ...}`. Add a concrete prescription and regression test for `/session tag` under v2, either by making the command write a v1-shaped export from the loader or by adding an explicit snapshot-export API. | **resolved** — Task 19: explicit `export_snapshot_json` backend API (loader-built full v1-shaped payload, v2-aware) replaces the pointer copy in `/session tag`; a **command-level** regression (`tests/test_commands/test_registry.py`) runs the handler and asserts the tag file is a full snapshot, not a pointer. Listed in C.9. |
| PMR-002 | **P1** | C.7 trigger; Task 10 `_backfill_index` / `list_session_snapshots`; existing `test_list_session_snapshots_merges_index_with_legacy_files` | C.7 says backfill triggers when the index is absent **or missing entries**, and Task 10 says the existing legacy-merge test must still pass. The copyable `list_session_snapshots` only calls `_backfill_index()` when `_load_session_index()` returns empty, so an existing/incomplete index hides legacy files that are present on disk. With v2 as default, the existing test path (legacy file first, then a save that writes an index) drops the legacy session. Add missing-entry detection/merge semantics, or explicitly change and approve the user-visible listing contract. | **resolved** — Task 20 (amends Task 10): sniffer-based missing-entry merge — after indexed entries, enumerate on-disk ids (v1 `.json` + v2 `.head`/`.jsonl`, deduped v2-wins) and surface any not in the index, building each entry from the v2-aware **loader** (so head-less v2 sessions surface too, not just headed ones); drop the `>= limit` early-return that hid the legacy scan. Mirrored in ohmo `list_snapshots`. Keeps the legacy-merge test green + new v2 (incl. head-less) test. |
| PMR-003 | **P1** | Task 17 includes `tests/test_services/test_conversation_index.py`; `services/conversation_index.py::rebuild` | The plan keeps feeding the conversation index on live saves, but the rebuild path still scans only `*/session-*.json`. After Task 1 makes v2 the default, `test_rebuild_from_snapshots` saves a v2 transcript/head with no `.json`, then `rebuild()` indexes zero sessions. Add `conversation_index.py` as a touched surface and make rebuild v2-aware by reassembling snapshots through session storage/session format. | **resolved** — Task 21: `conversation_index.py` added as a touched surface; `rebuild()` enumerates v1+v2 sessions per project (shared `session_ids_on_disk`) and reassembles each via a new `session_storage._load_snapshot_in_dir` core (local/lazy import); `test_rebuild_from_snapshots` extended for v2. |
| PMR-004 | **P1** | Missing touched surface: `services/autodream/lock.py`; `services/autodream/prompt.py` | Auto-dream still treats `session-*.json` as the saved-session contract: `list_sessions_touched_since()` only scans full JSON snapshots, and the consolidation prompt tells workers to inspect `session-*.json`. With v2 default writes, recent sessions no longer create those files, so auto-dream can stop triggering from session activity and can point workers at the wrong artifact shape. Add autodream as a touched surface, update the scan/prompt for v1+v2, and cover it with a v2 regression test. | **resolved** — Task 22: `autodream/lock.py` + `autodream/prompt.py` added as touched surfaces; `list_sessions_touched_since` scans `session-*.json` (skipping `.head.json`) **and** `session-*.jsonl` (by mtime, deduped); prompt references both shapes; new test asserts a v2 transcript is discovered and no `.head` phantom. |

### Q.5 Approval Criteria

- [x] No open P1 findings. — original 5 + R-001 resolved; R-001's fix verified against the real compaction code and by a 2nd independent re-review.
- [x] No open P2 findings unless explicitly accepted by owner. — original 6 + R-002 resolved; no merely-accepted finding remains (P3-004 promoted to fixed).
- [x] Tests / verification cover every P1/P2 class found. — original 14 (Q.3.1) plus the three R-fix regression tests (`fingerprint_messages` in-place change, same-count in-place rewrite, ohmo lost-head recovery) now registered in Q.3.1.
- [x] Open questions resolved or explicitly deferred. — the R-001 trigger design choice is **decided** (content-divergence fingerprint; explicit-signal alternative recorded as rejected in Design decision 3); incremental-fingerprint and index-sourced head-less recovery explicitly deferred (YAGNI).
- [x] All "fully resolves / closes" claims have full source coverage. — the premature first "CLEARED" was corrected by the 1st re-review (R-001); this revision's claims were re-verified against source by a 2nd independent re-review.
- [x] Conditional touched surfaces and merge-order deps resolved / deferred. — WS1↔WS4 documented (C.2); R-001's fix is **storage-local**. **Format-consumer blast radius now enumerated in C.9** (the gap that produced PMR-001..004): every code path that reads sessions by file shape is listed and routed through the loader or made format-aware (Phase 7).
- [x] **Contract/format-change consumer enumeration (C.9).** For a shape change, every consumer of the old shape is inventoried and shown handling v1+v2 — not just the changing module's internal correctness. This check was **absent** in the first three gate runs and is the root-cause fix for the PMR class.

**Gate verdict (post-PMR): CLEARED — implementation-ready.** The prior "CLEARED" (2026-06-13, pre-PMR) was **superseded**: the post-merge review found four P1 format-consumer breaks (PMR-001..004, Q.4.2) the first three gate runs missed because none enumerated the blast radius. This revision adds the **C.9 consumer inventory** (the root-cause fix for the class) and Phase 7 (Tasks 19–23) resolving all four + the ohmo `list_snapshots` mirror. A **third independent adversarial re-gate** (fresh agent, blind) then (a) **independently confirmed C.9's inventory is exhaustive** — its own codebase sweep found no session-shape consumer outside the four, so no new P1 — and (b) verified each fix against the real source. It surfaced one **P2** — the `glob("session-*.json")` / `session-<id>.head.json` collision (a phantom `<id>.head` id the sniffer doesn't filter) — which is now **closed**: a single shared `session_ids_on_disk` enumerator with the `.head.json` skip (Task 21), reused by listing (Task 20) and rebuild, plus negative assertions in the Task 20/22 tests and corrected prose. A **fourth review (owner, 2026-06-13)** added: a command-level `/session tag` regression (PMR-001 is a command, not just a backend API — Task 19), loader-based listing so **head-less** v2 sessions surface (Task 20), Q.1/Q.3.1 updated for the Phase 7 surfaces/tests, the stale "blocked" note removed, and Phase 7 renumbered (18→19..) to clear the duplicate-Task-18 collision. No open P1, no unaccepted P2; the format-change blast radius is fully covered and enumerated.

This remains a **design** gate: the tests above are *specified* as TDD steps to run at implementation, not executed as part of the gate. **Owner promoted DRAFT → APPROVED on 2026-06-13** and this plan is on `main`. The plan is now **implementation-ready** (the PMR blockers that made it not-ready are resolved); implementation is a separate, not-yet-started effort (execute the tasks via TDD, Phase 7 last).

> **Post-merge review note (2026-06-13, RESOLVED):** PMR-001..PMR-004 were found after the prior merge; they are now **resolved** (Phase 7, Tasks 19–23) and re-gated **CLEARED** — Q.4.2, Q.5, and the gate verdict reflect the resolved state. A fourth owner review then added the command-level `/session tag` regression (Task 19), head-less listing coverage (Task 20), the Q.1/Q.3.1 updates, the Phase 7 renumber, and this correction. No implementation block remains.
