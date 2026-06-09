# OpenHarness Comprehensive Review — Robustness & Performance Issues

**Date:** 2026-06-09
**Scope:** `src/`, `ohmo/`, `tests/`, `scripts/`, `frontend/`, `autopilot-dashboard/`, `.github/workflows/`

---

## Executive Summary

This review examined ~290 Python files, 40+ TypeScript files, and 10+ shell scripts across the OpenHarness codebase. Three parallel agents investigated **robustness**, **performance (speed)**, and **architectural/design** concerns. The project shows signs of rapid growth with significant architectural debt. The most critical issues are **bidirectional cross-package dependencies** (`ohmo` ↔ `openharness`), **massive God modules** (`cli.py` at 2,551 lines, `ui/runtime.py` at 799 lines), **hardcoded secrets in tests**, **plaintext credential fallback**, **fire-and-forget asyncio tasks**, and **synchronous subprocess calls blocking the async event loop**.

---

## Critical Issues (Immediate Action Required)

### Security & Secrets

| # | File | Issue |
|---|------|-------|
| C1 | `tests/test_untested_features.py:28`, `test_real_large_tasks.py:24`, `test_hooks_skills_plugins_real.py:25`, `test_merged_prs_on_autoagent.py:22` | **Hardcoded API keys** in test files with fallback values — committed to repo and used if env vars are missing |
| C2 | `src/openharness/auth/storage.py` | **Plaintext credential fallback** — stores credentials in `~/.openharness/credentials.json` when keyring unavailable |
| C3 | `scripts/install.sh:186` | Installer points to wrong repo (`HKUDS/OpenHarness` instead of `Real-Bimox/OpenHarness`) |

### Architecture

| # | File | Issue |
|---|------|-------|
| C4 | `ohmo/gateway/runtime.py:34` ↔ `src/openharness/channels/impl/base.py:24` | **Bidirectional circular dependency** — `ohmo` imports `openharness.ui.runtime`, `openharness` channels import `ohmo.workspace` |
| C5 | `src/openharness/cli.py` | **God module** — 2,551 lines, violates SRP, 30+ inline imports to avoid circular imports |
| C6 | `src/openharness/ui/runtime.py` | **God module** — 799 lines imports 20+ subsystems directly |

### Robustness — Async/Concurrency

| # | File | Issue |
|---|------|-------|
| C7 | `src/openharness/utils/shell.py:104` | **Fire-and-forget asyncio task** — `create_task()` with no reference, may be garbage-collected before cleanup runs |
| C8 | `src/openharness/ui/backend_host.py:449-452` | Sync method schedules async task without storing reference; exceptions silently lost |
| C9 | `ohmo/gateway/service.py:279-296` | **File descriptor race** — log file opened in `with` block and passed to `subprocess.Popen`; closed before child finishes writing |
| C10 | `src/openharness/tasks/manager.py:422-435` | **Singleton race condition** — `get_task_manager()` not thread/async-safe, multiple managers can be created concurrently |

### Robustness — Resource Exhaustion

| # | File | Issue |
|---|------|-------|
| C11 | `src/openharness/tasks/manager.py:273-282` | **Unbounded file growth** — task output appended with no size limit or rotation |
| C12 | `src/openharness/memory/manager.py:167-171` | **Infinite loop** — `while True` for file naming; can DoS if files pre-created |
| C13 | `src/openharness/channels/impl/telegram.py` | Downloads arbitrary media without size limits or MIME validation |

---

## High Severity Issues

### Performance — Event Loop Blocking

| # | File | Issue |
|---|------|-------|
| H1 | `src/openharness/autopilot/service.py:1236-1249` | `subprocess.run()` called synchronously inside async methods — **blocks entire asyncio event loop** during git/gh commands |
| H2 | `src/openharness/autopilot/service.py:1982-2000` | Same issue — `_run_gh_json` uses sync `subprocess.run` in async context |

### Robustness — Exception Handling

| # | File | Issue |
|---|------|-------|
| H3 | `src/openharness/api/openai_client.py:294`, `codex_client.py:236` | `except Exception` in retry loops catches `NameError`/`AttributeError` and retries them, masking bugs |
| H4 | `src/openharness/auth/storage.py:177-181` | `except (PasswordDeleteError, Exception): pass` — catches literally everything, silently ignores credential deletion failures |
| H5 | `src/openharness/swarm/in_process.py:273-274` | Agent loop silently dies on errors; leader never notified of failure |
| H6 | `src/openharness/channels/impl/manager.py:163-169` | Channel startup failures swallowed; channel left in partially initialized state |
| H7 | `src/openharness/channels/adapter.py:91-92`, `ohmo/gateway/bridge.py:296-305` | Message processing errors silently drop messages |

### Robustness — Subprocess & I/O

| # | File | Issue |
|---|------|-------|
| H8 | `src/openharness/auth/external.py:521-536` | `subprocess.run()` for macOS `security` command has **no timeout** — hangs indefinitely in headless/CI |
| H9 | `src/openharness/tasks/manager.py:202-207` | `CancelledError` not caught in process cleanup — leaves orphaned subprocesses |
| H10 | `src/openharness/config/settings.py:1062` | JSON config read without `JSONDecodeError` handling — crashes on corrupted config |
| H11 | `src/openharness/tools/file_write_tool.py:63-67`, `file_edit_tool.py`, `grep_tool.py` | `Path.resolve()` follows symlinks before sandbox validation — **path traversal via symlinks** |

### Performance — Memory

| # | File | Issue |
|---|------|-------|
| H12 | `src/openharness/ui/textual_app.py:229` | Transcript lines grow **unbounded** in long-running TUI sessions |
| H13 | `frontend/terminal/src/hooks/useBackendSession.ts:25` | React transcript state accumulates forever — memory bloat + slower reconciliation |
| H14 | `src/openharness/channels/impl/feishu.py:860,892` | Downloads entire media files into memory without size cap |
| H15 | `src/openharness/tools/file_read_tool.py:52` | `path.read_bytes()` loads entire multi-GB file into memory |

### Architecture — Global State

| # | File | Issue |
|---|------|-------|
| H16 | `src/openharness/bridge/manager.py:97-105` | Module-level singleton with no thread/async safety |
| H17 | `src/openharness/coordinator/coordinator_mode.py:63-71` | Module-level singleton for team registry |
| H18 | `src/openharness/auth/external.py:48-49` | Global mutable cache variables without synchronization |

### Security

| # | File | Issue |
|---|------|-------|
| H19 | `src/openharness/sandbox/docker_backend.py` | Arbitrary `env` dict passed directly to `docker exec -e` — no secret scrubbing |
| H20 | `src/openharness/channels/impl/manager.py` | No rate limiting or backpressure on outbound message dispatch |

---

## Medium Severity Issues

### Performance — I/O & Polling

| # | File | Issue |
|---|------|-------|
| M1 | `src/openharness/autopilot/service.py:1557-1572` | CI polling uses fixed 20s sleep — no exponential backoff or jitter |
| M2 | `src/openharness/channels/impl/email.py:79-101` | IMAP polling reconnects every cycle — no IDLE support or connection reuse |
| M3 | `src/openharness/channels/impl/mochat.py:592-631` | Fixed-interval polling with no error backoff |
| M4 | `src/openharness/channels/impl/discord.py:48-60` | Gateway reconnect uses fixed 5s sleep — no backoff |

### Performance — Algorithmic

| # | File | Issue |
|---|------|-------|
| M5 | `src/openharness/commands/registry.py:937,950` | `/files` uses `rglob("*")` with no depth limit, materializing entire tree |
| M6 | `src/openharness/swarm/mailbox.py:190` | `mark_read()` does O(n) linear scan over all inbox JSON files |
| M7 | `src/openharness/channels/impl/telegram.py:45-78` | ~10 sequential regex substitutions for Markdown→HTML conversion |
| M8 | `src/openharness/channels/impl/email.py:400-402` | HTML-to-text uses regex instead of parser |

### Performance — Async/Await

| # | File | Issue |
|---|------|-------|
| M9 | `src/openharness/engine/query.py:686-695` | `_stream_compaction` busy-waits with 0.05s timeout — wakes 20×/second doing nothing |
| M10 | `src/openharness/ui/backend_host.py:191` | `_read_requests` queue has no size limit — unbounded growth if frontend floods stdin |

### Robustness

| # | File | Issue |
|---|------|-------|
| M11 | `src/openharness/channels/impl/feishu.py:512` | WebSocket thread is daemon with no graceful shutdown |
| M12 | `src/openharness/tools/grep_tool.py:116` | **ReDoS risk** — arbitrary regex from LLM with no timeout/complexity check |
| M13 | `src/openharness/swarm/mailbox.py:150,180,208,228` | Deprecated `asyncio.get_event_loop()` usage (Python 3.10+) |
| M14 | `src/openharness/sandbox/docker_backend.py:152` | `process.communicate()` buffers all output in memory — OOM risk |
| M15 | `src/openharness/ui/backend_host.py:257` | `assert` used for runtime check — stripped in `python -O` |
| M16 | `tests/test_untested_features.py:574`, `test_real_large_tasks.py:427,461` | `os.system` with string interpolation — shell injection risk in tests |
| M17 | `src/openharness/api/copilot_auth.py` | Busy-loop polling GitHub OAuth with `time.sleep()` — no jitter |

### Architecture — Dependencies & CI

| # | File | Issue |
|---|------|-------|
| M18 | `frontend/terminal/package.json` vs `autopilot-dashboard/package.json` | **React version conflict** — terminal uses React 18, dashboard uses React 19 |
| M19 | `pyproject.toml` | Very loose dependency pins (`>=` with no upper bounds) — risk of breaking on upstream majors |
| M20 | `.github/workflows/ci.yml` | Autopilot-dashboard **not typechecked** in CI |
| M21 | `.github/workflows/autopilot-run-next.yml`, `autopilot-scan.yml` | Uses **self-hosted runners** with no timeout/resource safeguards; runs every 30 min |
| M22 | `.github/workflows/ci.yml` | No security scanning (`bandit`, `pip-audit`) |

### Architecture — Testing

| # | File | Issue |
|---|------|-------|
| M23 | `tests/` | 1,554 mock/patch occurrences vs ~101 test files — extremely heavy mocking |
| M24 | `src/openharness/ui/runtime.py` | 799-line core runtime has **no dedicated unit tests** |
| M25 | `src/openharness/channels/impl/` | 10 channel implementations, minimal test coverage |

---

## Low Severity Issues

### Performance

| # | File | Issue |
|---|------|-------|
| L1 | `src/openharness/commands/registry.py:1383-1386` | Repeated `+=` string concatenation in PR comments builder |
| L2 | `src/openharness/config/settings.py:446-450` | `_slugify_profile_name` uses `while "--" in cleaned` — O(n²) worst case |
| L3 | `src/openharness/services/session_storage.py:138` | Duplicate `stat()` calls during session sorting |
| L4 | `src/openharness/channels/impl/email.py:312-314` | `_processed_uids` eviction rebuilds entire set via `list()` conversion |
| L5 | `src/openharness/sandbox/docker_backend.py:44` | `docker info` subprocess at module import — adds startup latency |
| L6 | `frontend/terminal/src/App.tsx:66` | Command history grows without bound |
| L7 | `autopilot-dashboard/src/App.tsx:145` | Fetches `snapshot.json` on every mount with no debounce |

### Robustness

| # | File | Issue |
|---|------|-------|
| L8 | `src/openharness/skills/loader.py:144-150` | Git root search could loop on cyclic mounts |
| L9 | `src/openharness/tools/web_fetch_tool.py:78-84` | HTMLParser `close()` skipped if `feed()` raises |
| L10 | `src/openharness/tools/image_generation_tool.py:223-225` | If first `close()` raises, second never called |
| L11 | Multiple files | `assert` used for runtime checks throughout codebase |
| L12 | Various modules | Missing `__all__` — exports all non-underscore names |

---

## Top 10 Recommended Fixes (Priority Order)

1. **Remove hardcoded API keys** from all test files immediately; use `pytest.skip` when env vars are missing
2. **Fix circular dependency** between `ohmo` ↔ `openharness` — extract shared core/protocol layer
3. **Replace sync `subprocess.run`** in `autopilot/service.py` with `asyncio.create_subprocess_exec` or `asyncio.to_thread()`
4. **Store references** to all `asyncio.create_task()` calls to prevent garbage collection
5. **Add output size limits/rotation** to task manager log files
6. **Fix file descriptor race** in `ohmo/gateway/service.py` — keep log file open for child lifetime
7. **Resolve React version conflict** between terminal (18) and dashboard (19)
8. **Add upper bounds** to Python dependencies in `pyproject.toml`
9. **Replace `except Exception: pass`** with specific exception types everywhere
10. **Add ReDoS protection** to grep tool and size limits to Telegram/Feishu media downloads

---

## Appendix A: Issue Count by Category

| Category | Critical | High | Medium | Low | Total |
|----------|----------|------|--------|-----|-------|
| Security & Secrets | 3 | 0 | 0 | 0 | 3 |
| Architecture | 3 | 0 | 7 | 0 | 10 |
| Async/Concurrency | 4 | 2 | 2 | 0 | 8 |
| Resource Exhaustion | 3 | 4 | 1 | 0 | 8 |
| Exception Handling | 0 | 5 | 0 | 0 | 5 |
| Subprocess & I/O | 0 | 4 | 0 | 0 | 4 |
| Memory | 0 | 4 | 2 | 2 | 8 |
| Global State | 0 | 3 | 0 | 0 | 3 |
| Polling & Network | 0 | 0 | 4 | 0 | 4 |
| Algorithmic | 0 | 0 | 4 | 3 | 7 |
| CI/CD & Dependencies | 0 | 0 | 5 | 0 | 5 |
| Testing | 0 | 0 | 3 | 0 | 3 |
| **TOTAL** | **13** | **22** | **29** | **7** | **71** |

---
