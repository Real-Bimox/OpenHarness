# Implementation Plan: health-status-http-server

Target release: **next release** (post v0.1.18).
Proposal: `docs/proposals/health-status-http-server.md` (DRAFT, commit `5360cde`).

---

## 0. Pre-work

### 0.1 Branch

The proposal document `docs/proposals/health-status-http-server.md` already
exists on `main` at commit `5360cde`. Implementation work needs its own branch:

1. Create `proposal/health-status-http-server` from current `main`. No
   cherry-pick needed — the proposal file is already there.
2. Update the proposal status table to confirm the branch name matches.

All implementation work targets `proposal/health-status-http-server`.

### 0.2 Proposal sync checklist

The proposal document currently diverges from this plan in several places.
These edits land as part of Phase 3 (§6) but are listed here for completeness.
Do **not** treat the proposal as aligned until all of these land:

| Proposal location | Current text | Change to |
|---|---|---|
| §4 Dependency Declaration (line 267) | `uvicorn[standard]>=0.20` | `uvicorn>=0.20` |
| §5 Configuration table (line 282) | `--health-server-host` row | Remove row entirely |
| §5 Configuration table (line 283) | `--health-server-port` | Keep, but note it requires `--health-server` |
| § Security → Non-loopback binding (lines 298–304) | Warning text about `0.0.0.0` binding | Remove section; v1 is loopback-only with no host option |
| § Performance → Target: sub-millisecond liveness (line 322–324) | "must respond in under 1 ms" | "zero-I/O; no timing gate in CI" |
| AC #3 (line 342) | "in under 1 ms" | "zero-I/O response (no timing gate)" |
| AC #5 (line 344) | "including `app_state` from `AppStateStore`" | "`app_state` included when a store is injected; omitted otherwise" |
| AC #8 (line 347) | `oh --headless --health-server-port 8642` | `oh --headless --health-server` |
| AC #11 (line 350) | "No new files, imports, or threads are created" | "No runtime imports, threads, or state are created when `--health-server` is not passed" |
| AC #12 (line 351) | "Binding to a non-loopback address prints a warning" | "Non-loopback binding is not possible in v1 (no host option; server binds to 127.0.0.1 only)" |

---

## 1. Dependency Declaration

**File:** `pyproject.toml`

Add an optional extra `health-server` to `[project.optional-dependencies]`
(below the existing `dev` extra at line 38):

```toml
health-server = ["fastapi>=0.100", "uvicorn>=0.20"]
```

Note: `uvicorn` **without** `[standard]`. The `[standard]` extra pulls in
`uvloop` and `httptools` — native C extensions that add compilation weight and
potential wheel issues for a read-only JSON API. (`watchfiles` is already a
base dependency at `pyproject.toml:30`, so it is not a concern here.) Plain
`uvicorn` is sufficient. If performance profiling later shows the need for
`uvloop`/`httptools`, this can be upgraded in a follow-up.

**Verification:** `pip install -e ".[health-server]"` resolves and imports
`fastapi` and `uvicorn` without errors.

---

## 2. New Module: `src/openharness/api/health_server.py`

A self-contained FastAPI application factory. ~270–320 lines.

### 2.1 Module structure

```
src/openharness/api/health_server.py
├── HealthServerHandle                # dataclass: server, thread, host, port
├── create_health_app(store: AppStateStore | None = None) -> FastAPI
├── start_health_server_background(host, port, store) -> HealthServerHandle
├── _lifespan(app)                    # async context manager (startup/shutdown log)
├── GET /health                        # liveness
├── GET /health/detailed               # readiness (build_status with probe)
├── GET /api/status                    # operational (build_status + AppStateStore)
├── GET /api/system/stats              # host/process metrics (psutil optional)
├── GET /v1/capabilities              # API discovery
├── _system_stats() -> dict            # helper: host metrics with graceful psutil fallback
└── _capabilities() -> dict            # helper: static capabilities document
```

### 2.2 AppStateStore access mechanism

**Decision: parameter injection, not a global singleton.**

`AppStateStore` is instantiated inside `RuntimeBundle` assembly
(`src/openharness/ui/runtime.py:131`). It is not a module-level global. The
health server needs it for `/api/status`.

`create_health_app()` accepts an optional `AppStateStore` parameter. When
provided, `/api/status` includes `app_state`. When `None` (both standalone
mode and background mode in v1 — neither assembles a RuntimeBundle before the
health server starts), `/api/status` omits the `app_state` key and returns
`build_status()` only.

This avoids introducing a global registry, avoids circular imports, and keeps
the health server decoupled from UI modules.

```python
def create_health_app(store: AppStateStore | None = None) -> FastAPI:
    app = FastAPI(title="OpenHarness Health", lifespan=_lifespan)
    _register_routes(app, store)
    return app
```

### 2.3 Endpoint implementations

| Endpoint | Data source | Auth | HTTP status | Notes |
|---|---|---|---|---|
| `GET /health` | `__version__` constant | None | Always 200 | Zero I/O. No timing gate in CI. |
| `GET /health/detailed` | `build_status(probe=True)` plus top-level `"status"` | None | 200 if healthy, **503** if `thread_probe.status != "ok"` | Thread probe bounded at 2 s. |
| `GET /api/status` | `build_status(probe=False)` + `store.get()` if store provided | None | Always 200 | `app_state` key present only when store is wired. |
| `GET /api/system/stats` | `os`, `platform` stdlib + `psutil` if available | None | Always 200 | Graceful fallback: `psutil: false` in response when unavailable. |
| `GET /v1/capabilities` | Static dict constructed at import from `__version__` | None | Always 200 | `features` dict derived from module availability checks. |

#### `/health/detailed` response body shape

The `build_status()` function returns a dict with keys like
`status_schema_version`, `generated_at`, `version`, `run`, `auth`,
`recorder`, `index`, `summary`, `recent_errors`, `thread_probe` — but **no**
top-level `"status"` key. The endpoint wraps the `build_status()` result and
adds two top-level keys:

```python
{
    "status": "ok" | "degraded",
    "platform": "openharness",
    **build_status(probe=True),
}
```

The `"status"` field is derived from `thread_probe.status` — `"ok"` when the
probe succeeds, `"degraded"` otherwise. The `"platform"` field is the constant
`"openharness"`, matching `GET /health`.

The response body is always the full document regardless of HTTP status, so
consumers can inspect details even when the probe fails.

#### Readiness status policy

The endpoint returns HTTP 503 when:

- `thread_probe.status` is not `"ok"` (i.e., `"timeout"` or `"failed"`)

This is the **only** 503 trigger. The recorder's `enabled` field is **not** a
health signal — a user who intentionally disables diagnostics via settings has
a perfectly healthy process. The `recorder.enabled` field is present in the
response body as informational data for consumers, but it does not affect the
HTTP status code.

### 2.4 Graceful degradation for psutil

`psutil` is not declared as a dependency — not even a soft one of the
`health-server` extra. The endpoint works without it, returning `"psutil":
false` and only the fields available from stdlib (`os.cpu_count()`,
`platform.node()`, etc.).

Platform portability guards:

- `os.getloadavg()` — Unix-only. Wrapped in `try/except (AttributeError, OSError)`.
  Windows gets `"load_avg": null`.
- `psutil.cpu_percent()` — available everywhere but may return 0.0 on first
  call; call once during module init (or `lifespan`) to seed the internal
  counter.
- `psutil.disk_usage()` — path must exist. Use the cwd or `/` with
  `try/except`.
- Tests use `@pytest.mark.skipif` annotations for platform-specific assertions.

```python
def _system_stats() -> dict:
    import os, platform
    stats = {
        "os": platform.system(),
        "os_release": platform.release(),
        "arch": platform.machine(),
        "hostname": platform.node(),
        "python_version": platform.python_version(),
        "openharness_version": _version(),
        "cpu_count": os.cpu_count(),
        "psutil": False,
    }
    try:
        import psutil
        stats["psutil"] = True
        proc = psutil.Process()
        stats["memory"] = {
            "total": psutil.virtual_memory().total,
            "available": psutil.virtual_memory().available,
            "percent": psutil.virtual_memory().percent,
        }
        stats["disk"] = {
            "total": psutil.disk_usage("/").total,
            "used": psutil.disk_usage("/").used,
            "free": psutil.disk_usage("/").free,
            "percent": psutil.disk_usage("/").percent,
        }
        stats["cpu_percent"] = psutil.cpu_percent(interval=0)
        try:
            stats["load_avg"] = list(os.getloadavg())
        except (AttributeError, OSError):
            stats["load_avg"] = None
        stats["process"] = {
            "pid": os.getpid(),
            "rss": proc.memory_info().rss,
            "create_time": proc.create_time(),
            "num_threads": proc.num_threads(),
        }
    except ImportError:
        pass
    except Exception:
        pass
    return stats
```

### 2.5 Thread safety consideration

`AppStateStore.get()` returns the current `AppState` dataclass reference.
`AppStateStore.set()` uses `dataclasses.replace()` to create a new instance
and rebinds `self._state`. The old reference remains valid for readers. This
is safe for concurrent reads from the health-server daemon thread without
locks — readers see either the old or new state, never a torn object.

`build_status()` reads event files and `current-run.json` synchronously. These
are append-only or atomic-write files. No mutation race.

**Conclusion: no additional synchronization needed.**

### 2.6 Privacy guarantee

`build_status()` does not perform explicit redaction — it trusts the
diagnostics recorder to never write secrets into event files in the first
place. The health server reuses `build_status()` unchanged.

Note: `build_status()` → `_read_current_run()` reads `current-run.json`,
which includes `data_dir` and `config_dir` as absolute paths (see
`diagnostics/runinfo.py:32–33`). These are legitimate operational paths, not
secrets, and will appear in `/api/status` and `/health/detailed` responses.

Privacy tests (§7.4) seed known-sensitive values through **normal
instrumentation paths** (settings with known API key values, auth state) — not
by writing artificial events that bypass the recorder's write discipline.
The health endpoint is not a redaction layer; testing it against synthetic
events that the recorder would never produce would be a false assurance.

---

## 3. Background Thread Launcher

**File:** `src/openharness/api/health_server.py` (same module)

### 3.1 HealthServerHandle dataclass

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import threading

@dataclass
class HealthServerHandle:
    server: Any
    thread: threading.Thread
    host: str
    port: int

    def stop(self, timeout: float = 5.0) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=timeout)
```

`server` is typed as `Any` to avoid importing uvicorn at the module level (it
may not be installed). `stop()` sets the shutdown flag and joins the daemon
thread, ensuring the thread has exited before the caller proceeds. Tests
should call `handle.stop()` in `finally` blocks.

### 3.2 Launcher function

The launcher **always waits** for bind success — not only when `port=0`. A
fixed port can still fail to bind due to conflicts or permission issues. If
uvicorn exits before binding (bind failure), the launcher raises a clear error
instead of letting the primary process continue without a server.

```python
import threading
import time

_READINESS_TIMEOUT = 5.0

class BindError(RuntimeError):
    pass

def start_health_server_background(
    host: str = "127.0.0.1",
    port: int = 8642,
    store: AppStateStore | None = None,
) -> HealthServerHandle:
    import uvicorn
    app = create_health_app(store=store)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="health-server")
    thread.start()
    handle = HealthServerHandle(server=server, thread=thread, host=host, port=port)
    deadline = time.monotonic() + _READINESS_TIMEOUT
    while time.monotonic() < deadline:
        if server.servers:
            sock = server.servers[0].sockets[0]
            actual_port = sock.getsockname()[1]
            handle.port = actual_port
            return handle
        if not thread.is_alive():
            break
        time.sleep(0.05)
    if not thread.is_alive():
        raise BindError(f"Health server failed to bind {host}:{port}")
    raise BindError(f"Health server did not bind within {_READINESS_TIMEOUT}s")
```

Behavior by scenario:

| Scenario | Result |
|---|---|
| Fixed port, binds successfully | Returns `HealthServerHandle` with `handle.port == port` |
| Fixed port, conflict/permission denied | Thread exits → `BindError` raised |
| `port=0`, OS assigns port | Returns `HealthServerHandle` with `handle.port` set to the discovered port |
| `port=0`, bind fails | Thread exits → `BindError` raised |
| Any case, timeout expires | `BindError` raised |

### 3.3 Usage in CLI

The CLI's `_maybe_start_health_server()` catches `BindError` and exits with a
clear message:

```python
def _maybe_start_health_server(port: int) -> HealthServerHandle | None:
    if not _health_server_enabled:
        return None
    _check_health_server_deps()
    try:
        from openharness.api.health_server import start_health_server_background, BindError
        handle = start_health_server_background(host="127.0.0.1", port=port)
    except BindError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(1)
    print(f"Health server running on 127.0.0.1:{handle.port}", file=sys.stderr)
    return handle
```

---

## 4. CLI Integration

**File:** `src/openharness/cli.py`

### 4.1 CLI semantics

A single flag `--health-server` enables the health server everywhere. The
port option `--health-server-port` is configuration only — passing it without
`--health-server` is an error.

`--health-server` is supported in these modes:

| Invocation | Behavior |
|---|---|
| `oh --health-server` | **Standalone.** Health server is the primary activity. No other mode active. |
| `oh --headless --health-server` | **Background.** Health server starts as daemon thread, then headless runs as primary. |
| `oh --task-worker --health-server` | **Background.** Health server + task worker. |
| `oh --mcp-serve --health-server` | **Background.** Health server + MCP server. |
| `oh --health-server --health-server-port 9090` | **Standalone** on port 9090. |
| `oh --headless --health-server --health-server-port 9090` | **Background** on port 9090. |
| `oh --headless` (no `--health-server`) | Unchanged. No health server. |
| `oh --health-server-port 9090` | **Error.** `--health-server-port requires --health-server.` |

The following combinations are **unsupported** and produce a clear error
explaining that `--health-server` is not yet available for interactive/REPL
sessions, one-shot print mode, or dry-run mode:

- `oh --health-server --continue`
- `oh --health-server --resume`
- `oh --health-server --backend-only`
- `oh --health-server -p "prompt"`
- `oh --health-server --dry-run`

The bare case (no other mode flag) enters standalone health-server mode, not
the interactive REPL.

**v1 binding policy: loopback only.** There is no `--health-server-host`
option. The server always binds to `127.0.0.1`. Non-loopback binding is
deferred to a follow-up that includes authentication.

### 4.2 New CLI options in `main()`

Add two new parameters to the `main()` function (after `mcp_serve` at line
~2829):

```python
health_server: bool = typer.Option(
    False,
    "--health-server",
    help="Start the HTTP health/status server as the primary mode, "
         "or as a background thread alongside --headless, --task-worker, "
         "or --mcp-serve (requires openharness[health-server])",
    rich_help_panel="Advanced",
),
health_server_port: int | None = typer.Option(
    None,
    "--health-server-port",
    help="Port for the health server (default: 8642). "
         "Requires --health-server.",
    rich_help_panel="Advanced",
),
```

### 4.3 Early validation

Insert **before** the `mcp_serve` early return (currently at line ~2835). This
must happen first because `--mcp-serve --health-server` needs to start the
background server inside the `mcp_serve` block. Validation and shared state
must be computed before any mode dispatch:

```python
_health_server_enabled = health_server

if health_server_port is not None and not health_server:
    print(
        "Error: --health-server-port requires --health-server.",
        file=sys.stderr,
    )
    raise typer.Exit(1)

if health_server:
    raw_port = health_server_port or os.environ.get("OPENHARNESS_HEALTH_SERVER_PORT", "8642")
    try:
        _health_port = int(raw_port)
    except (ValueError, TypeError):
        print(
            f"Error: Invalid health server port: {raw_port!r} "
            "(must be 0–65535).",
            file=sys.stderr,
        )
        raise typer.Exit(1)
    if not (0 <= _health_port <= 65535):
        print(
            f"Error: Health server port {_health_port} out of range (must be 0–65535).",
            file=sys.stderr,
        )
        raise typer.Exit(1)

_primary_mode = (
    headless or task_worker or mcp_serve
    or print_mode is not None or dry_run
    or continue_session or resume is not None or backend_only
)

_supported_primary = (
    headless or task_worker or mcp_serve
)

if _health_server_enabled and _primary_mode and not _supported_primary:
    print(
        "Error: --health-server is only supported standalone or with "
        "--headless, --task-worker, or --mcp-serve in this release.",
        file=sys.stderr,
    )
    raise typer.Exit(1)
```

This block validates:

1. `--health-server-port` requires `--health-server`.
2. Port is a valid integer in range 0–65535 (handles both CLI flag and
   `OPENHARNESS_HEALTH_SERVER_PORT` env var).
3. `--health-server` is not combined with unsupported modes (`--continue`,
   `--resume`, `--backend-only`, `-p/--print`, or `--dry-run`).

### 4.4 `mcp-serve` integration

The `mcp_serve` block must now optionally start the background server before
entering MCP mode. Insert the background startup inside the existing
`if mcp_serve:` block, before `run_mcp_server()`:

```python
if mcp_serve:
    # ... existing mutual-exclusion checks ...
    if _health_server_enabled:
        _maybe_start_health_server(_health_port)
    from openharness.mcp.serve import run_mcp_server
    run_mcp_server()
    return
```

This works because `_health_server_enabled` and `_health_port` were computed
in the early validation block (§4.3), which now runs **before** this block.

### 4.5 Standalone mode

Standalone fires when `--health-server` is present and no primary mode is
selected.

```python
if _health_server_enabled and not _primary_mode:
    _check_health_server_deps()
    from openharness.api.health_server import create_health_app
    import uvicorn
    app = create_health_app()
    uvicorn.run(app, host="127.0.0.1", port=_health_port)
    return
```

This is inserted after the `mcp_serve` block, before logging setup.

### 4.6 Background thread mode for other primaries

Background fires when `--health-server` is present alongside a supported
primary mode (`--headless`, `--task-worker`, or `--mcp-serve`). The startup
call is inserted immediately before entering each long-lived mode.

The `AppStateStore` is not wired in v1 background mode (see §10). All
endpoints work; `/api/status` omits `app_state`.

```python
def _maybe_start_health_server(port: int) -> HealthServerHandle | None:
    if not _health_server_enabled:
        return None
    _check_health_server_deps()
    try:
        from openharness.api.health_server import start_health_server_background, BindError
        handle = start_health_server_background(host="127.0.0.1", port=port)
    except BindError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(1)
    print(f"Health server running on 127.0.0.1:{handle.port}", file=sys.stderr)
    return handle
```

**Integration points (all in `cli.py`):**

| Mode | Location | Insert before |
|---|---|---|
| `--headless` | ~line 2960 | `asyncio.run(run_headless_control(...))` |
| `--task-worker` | ~line 3122 | `asyncio.run(run_task_worker(...))` |
| `--mcp-serve` | §4.4 above | Inside the `if mcp_serve:` block, before `run_mcp_server()` |

**`oh cron start`:** This is a subcommand (`cron_app` Typer sub-app), not a
flag on `main()`. Deferred to a follow-up.

**Interactive REPL / `--continue` / `--resume` / `--backend-only`:**
Explicitly rejected by the validation block (§4.3). These paths assemble a
`RuntimeBundle` but the health server is not integrated into the REPL startup
path in v1. Rather than silently accepting the flag and not starting the
server, the CLI exits with a clear error message.

### 4.7 Dependency check function

```python
def _check_health_server_deps() -> None:
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        print(
            "Error: --health-server requires the 'health-server' extra.\n"
            "  pip install openharness[health-server]",
            file=sys.stderr,
        )
        raise typer.Exit(1)
```

---

## 5. Files Changed — Summary

| File | Change | Size |
|---|---|---|
| `docs/proposals/health-status-http-server.md` | Sync uvicorn dep; remove `--health-server-host`; update ACs #3, #5, #8, #11, #12; remove non-loopback warning section | ~15 lines changed |
| `pyproject.toml` | Add `health-server` extra | 1 line |
| `src/openharness/api/health_server.py` | **New file.** `HealthServerHandle`, `BindError`, FastAPI app factory, 5 endpoints, background launcher, psutil fallback | ~310 lines |
| `src/openharness/cli.py` | Add 2 CLI options, port validation, mode validation, standalone mode block, background-thread startup for 3 modes | ~70 lines |
| `tests/test_health_server.py` | **New file.** Unit, integration, privacy tests; guarded by `pytest.importorskip` | ~240 lines |
| `tests/test_health_server_cli.py` | **New file.** CLI tests for missing-dep error, flag validation, port validation, unsupported combos; no fastapi import | ~80 lines |

**Total:** ~710 lines of new code, 16 lines of doc/config changes.

**No existing production code is modified** — `pyproject.toml` is additive,
`cli.py` is additive (new options and new code blocks, no changed lines), and
the proposal doc update is a DRAFT status edit.

---

## 6. Implementation Order

The build proceeds in this order so each step can be tested independently:

### Phase 1: Skeleton (can be merged independently)

1. **`pyproject.toml`:** Add `health-server` extra.
2. **`src/openharness/api/health_server.py`:** Create module with
   `create_health_app()`, `HealthServerHandle`, `BindError`, and all 5
   endpoints.
3. **`src/openharness/cli.py`:** Add `--health-server` and
   `--health-server-port` options. Add early validation (port range,
   `--health-server-port` requires `--health-server`, unsupported combos).
   Implement standalone mode. Insert background startup inside `mcp_serve`
   block and before `headless`/`task-worker` calls.
4. **Manual test:** `pip install -e ".[health-server]" && oh --health-server`
   → verify all 5 endpoints via curl.

### Phase 2: Background thread integration

5. **Manual test:** `oh --headless --health-server` → verify `/health`
   responds while headless is idle and during a turn.
6. **Manual test:** `oh --mcp-serve --health-server` → verify `/health`
   responds alongside MCP serve.
7. **Manual test:** `oh --health-server --continue` → verify error message.

### Phase 3: Tests and proposal sync

8. **`tests/test_health_server.py`:** Unit, integration, and privacy tests
   (see §7). Guarded by `pytest.importorskip("fastapi")`.
9. **`tests/test_health_server_cli.py`:** CLI validation tests — no fastapi
   import required (see §7).
10. **`docs/proposals/health-status-http-server.md`:** Apply all changes from
    the sync checklist in §0.2.
11. **Verify acceptance criteria 1–12.**

---

## 7. Testing Strategy

### 7.1 Test suite isolation

The health-server tests must not break the base test suite when the optional
extra is not installed. This requires two separate test files:

- **`tests/test_health_server.py`** — All endpoint tests (unit, integration,
  privacy). Uses `pytest.importorskip("fastapi")` at module level so the
  entire file is skipped when the extra is not installed.
- **`tests/test_health_server_cli.py`** — CLI validation tests (missing-dep
  error message, `--health-server-port` without `--health-server`, port range,
  unsupported combinations including print and dry-run). These do **not** import fastapi; they test the
  CLI's error path by mocking `sys.modules`. This file runs in all
  configurations.

### 7.2 Unit tests (no network, no server startup)

```python
import pytest
pytest.importorskip("fastapi")

import httpx
from openharness.api.health_server import create_health_app

async def _get(path: str, **kwargs):
    transport = httpx.ASGITransport(app=create_health_app(**kwargs))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path)

async def test_health_liveness():
    r = await _get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["platform"] == "openharness"
    assert "version" in body

async def test_health_detailed_ok():
    r = await _get("/health/detailed")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "thread_probe" in body
    assert "status_schema_version" in body

def test_health_detailed_503_on_probe_timeout():
    # Mock build_status to return {"thread_probe": {"status": "timeout", ...}, ...}
    # Assert r.status_code == 503
    # Assert body["status"] == "degraded" (the top-level key added by the endpoint)
    # Assert body still contains full build_status keys (thread_probe, recorder, etc.)

def test_health_detailed_200_with_recorder_disabled():
    # Mock build_status to return {"recorder": {"enabled": False, ...}, "thread_probe": {"status": "ok"}}
    # Assert r.status_code == 200 — recorder disabled is not a health failure

async def test_api_status_without_store():
    r = await _get("/api/status")
    assert r.status_code == 200
    assert "app_state" not in r.json()

async def test_api_status_with_store():
    from openharness.state import AppState, AppStateStore
    store = AppStateStore(AppState(model="test", permission_mode="default", theme="default"))
    r = await _get("/api/status", store=store)
    assert r.status_code == 200
    assert r.json()["app_state"]["model"] == "test"

async def test_system_stats():
    r = await _get("/api/system/stats")
    assert r.status_code == 200
    body = r.json()
    assert "os" in body
    assert "psutil" in body
    assert "cpu_count" in body

async def test_system_stats_cpu_percent_is_number():
    r = await _get("/api/system/stats")
    body = r.json()
    if body["psutil"]:
        assert isinstance(body["cpu_percent"], (int, float))
        assert not isinstance(body["cpu_percent"], tuple)

async def test_capabilities():
    r = await _get("/v1/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "openharness.capabilities"
    assert len(body["endpoints"]) == 5
```

### 7.3 Integration tests (background thread)

```python
def test_background_thread_reachable():
    import urllib.request
    from openharness.api.health_server import start_health_server_background
    handle = start_health_server_background(port=0)
    try:
        assert handle.port > 0, "OS-assigned port not discovered"
        assert handle.thread.is_alive()
        resp = urllib.request.urlopen(f"http://127.0.0.1:{handle.port}/health", timeout=5)
        assert resp.status == 200
    finally:
        handle.stop()

def test_background_bind_failure():
    import socket
    from openharness.api.health_server import start_health_server_background, BindError
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    conflict_port = sock.getsockname()[1]
    try:
        with pytest.raises(BindError):
            start_health_server_background(port=conflict_port)
    finally:
        sock.close()
```

### 7.4 Privacy tests

Verify that no sensitive data leaks through any endpoint. Tests seed sensitive
values through **normal instrumentation paths** (settings, auth state) — not
by writing synthetic events that bypass the recorder's write discipline. The
health endpoint reads `build_status()`; testing against events the recorder
would never produce would give false assurance.

Note: `build_status()` → `_read_current_run()` returns `data_dir` and
`config_dir` as absolute paths (from `current-run.json`). These are
intentional operational paths. Tests check for seeded sensitive strings only,
not blanket absence of all absolute paths.

```python
_SENSITIVE_STRINGS = [
    "sk-ant-api03-test-key-do-not-use",
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_testGitHubToken",
]

def test_no_secrets_in_endpoints():
    for path in ("/health", "/health/detailed", "/api/status", "/api/system/stats", "/v1/capabilities"):
        body = (await _get(path)).text
        for secret in _SENSITIVE_STRINGS:
            assert secret not in body, f"Secret leaked in {path}"

def test_seeded_api_key_absent_from_status():
    # Write a settings file with a known API key value, then call
    # build_status()-dependent endpoints and verify the key value
    # does not appear. (Settings expose provider/model, not the key
    # value itself — build_status() reads auth profile metadata only.)

def test_auth_status_exposes_model_not_key():
    # Configure a profile with a known key, call /api/status,
    # verify the response includes model/provider but not the key.
```

### 7.5 CLI tests

```python
# tests/test_health_server_cli.py
# No fastapi import — runs in all configurations.

def test_missing_deps_error():
    # Patch sys.modules to hide fastapi, run CLI with --health-server,
    # assert stderr contains "pip install openharness[health-server]"
    # and exit code is 1.

def test_port_without_enable_errors():
    # Run CLI with --health-server-port 9090 but no --health-server.
    # Assert stderr contains "--health-server-port requires --health-server"
    # and exit code is 1.

def test_port_range_validation():
    # Run CLI with --health-server --health-server-port 99999.
    # Assert stderr contains "out of range" and exit code is 1.

def test_env_port_validation():
    # Set OPENHARNESS_HEALTH_SERVER_PORT=not-a-number, run with --health-server.
    # Assert stderr contains "Invalid health server port" and exit code is 1.

def test_unsupported_continue_combo():
    # Run CLI with --health-server --continue.
    # Assert stderr contains "only supported standalone"
    # and exit code is 1.

def test_unsupported_resume_combo():
    # Run CLI with --health-server --resume abc123.
    # Assert stderr contains "only supported standalone"
    # and exit code is 1.

def test_unsupported_backend_only_combo():
    # Run CLI with --health-server --backend-only.
    # Assert stderr contains "only supported standalone"
    # and exit code is 1.

def test_unsupported_print_combo():
    # Run CLI with --health-server -p hello.
    # Assert stderr contains "only supported standalone"
    # and exit code is 1.

def test_unsupported_dry_run_combo():
    # Run CLI with --health-server --dry-run.
    # Assert stderr contains "only supported standalone"
    # and exit code is 1.

def test_standalone_mode():
    # Run CLI with --health-server only (no primary mode).
    # Assert server starts on 127.0.0.1:{port}.

def test_background_mode_with_headless():
    # Run CLI with --headless --health-server.
    # Assert both headless JSONL and health server are active.
```

### 7.6 Platform skip annotations

```python
import platform
import pytest

@pytest.mark.skipif(platform.system() == "Windows", reason="load_avg Unix-only")
async def test_system_stats_load_avg():
    r = await _get("/api/system/stats")
    if r.json()["psutil"]:
        assert r.json()["load_avg"] is not None

@pytest.mark.skipif(platform.system() == "Windows", reason="psutil disk_usage root path")
async def test_system_stats_disk():
    r = await _get("/api/system/stats")
    if r.json()["psutil"]:
        assert "disk" in r.json()
```

---

## 8. Acceptance Criteria Mapping

| # | Criterion | Implementation location |
|---|---|---|
| 1 | `pip install openharness[health-server]` installs deps | `pyproject.toml` health-server extra |
| 2 | `oh --health-server` starts on 127.0.0.1:8642 | `cli.py` standalone mode block |
| 3 | `GET /health` returns ok, zero-I/O response (no timing gate in CI) | `health_server.py` — constant handler; test checks content, not latency |
| 4 | `GET /health/detailed` returns build_status plus top-level `"status"` and `"platform"` keys, with thread_probe populated | `health_server.py` — wraps `build_status(probe=True)` |
| 5 | `GET /api/status` includes `app_state` when a store is injected; omits it otherwise | `health_server.py` — conditional `store.get()` |
| 6 | `GET /api/system/stats` works with and without psutil | `health_server.py` — try/except psutil import |
| 7 | `GET /v1/capabilities` lists 5 endpoints | `health_server.py` — static dict |
| 8 | `oh --headless --health-server` runs both simultaneously | `cli.py` — background thread before `asyncio.run(run_headless_control)` |
| 9 | Missing deps prints install instruction, exit 1 | `cli.py` — `_check_health_server_deps()` |
| 10 | `/health` reachable during active turn | Architecture: daemon thread + separate event loop, not blocked by main thread |
| 11 | No runtime imports, threads, or state are created when `--health-server` is not passed | Lazy import in `_check_health_server_deps` and `start_health_server_background` — only triggered by `--health-server` flag. File exists on disk but is never loaded. |
| 12 | Non-loopback binding is not possible in v1 (no `--health-server-host` option) | `cli.py` — host hardcoded to `127.0.0.1` |

---

## 9. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `uvicorn` daemon thread conflicts with main async loop | Low | Medium | Separate event loop in separate thread. Proven pattern used by Prometheus exporters. Test with AC-10. |
| `build_status()` takes > 2 s under load (thread probe timeout) | Low | Low | `/health` is the liveness probe and does zero I/O. `/health/detailed` is readiness and tolerates latency. Bounded by 2 s thread probe. |
| FastAPI/Pydantic v2 compatibility issues with existing deps | Low | High | FastAPI >= 0.100 requires Pydantic v2, which is already a transitive dep of the project. Verify with a clean `pip install -e ".[health-server]"`. |
| Port conflict on 8642 | Medium | Low | User can specify `--health-server-port`. Launcher detects bind failure via `BindError` and exits with clear message. |
| `cron start` not integrated in v1 | Certain | Low | Deferred by design. Cron daemon has a different lifecycle. Container use cases (the primary motivation) are covered by the 3 integrated modes + standalone. |
| Secret leakage through diagnostics events | Low | High | Privacy tests (§7.4) seed known-sensitive strings through normal paths and verify they are absent. Recorder's write-time filtering is trusted but validated by tests. |
| `psutil` metrics differ across platforms | Medium | Low | Platform guards in `_system_stats()`, `@pytest.mark.skipif` in tests, `null` values for unavailable metrics. |
| Tests break base suite without optional extra | Medium | Medium | `pytest.importorskip("fastapi")` in endpoint test file; CLI tests in separate file with no fastapi import. |
| Bind failure not detected, process runs without server | Medium | Medium | Launcher always waits for bind success. Thread exit detected via `thread.is_alive()` check. `BindError` raised on failure. |

---

## 10. Out of Scope for v1 (Explicit Deferments)

- **AppStateStore wiring in background mode** — `/api/status` omits `app_state`
  when running alongside headless/task-worker/mcp-serve. The store is only
  available when `RuntimeBundle` is assembled, which happens inside the
  `asyncio.run()` call. Plucking it out requires refactoring the startup path.
  Trivial to add in a follow-up once the health server module exists. AC #5
  is updated to reflect conditional inclusion.

- **`--health-server` for REPL / `--continue` / `--resume` / `--backend-only`** —
  Explicitly rejected with a clear error. These paths enter the interactive
  REPL which assembles a `RuntimeBundle`. Integration is deferred to a
  follow-up that also wires the store.

- **`--health-server-host` / non-loopback binding** — Removed from v1. The
  server is loopback-only. Non-loopback binding requires authentication and
  is deferred to a follow-up proposal.

- **`oh cron start --health-server`** — Different lifecycle (subprocess
  daemon). Deferred.

- **`uvicorn[standard]`** — Deferred until profiling shows it's needed.

- **Prometheus `/metrics` endpoint** — Future work per proposal.

- **Authentication** — Loopback-only binding is the sole security control for
  v1. Auth gating (session token, bearer token, mTLS) is a follow-up
  proposal.

---

## 11. Estimated Effort

| Phase | Lines | Time |
|---|---|---|
| Phase 1: Skeleton (pyproject + health_server.py + standalone CLI) | ~380 | 3–4 hours |
| Phase 2: Background thread integration (3 modes + validation) | ~40 | 1 hour |
| Phase 3: Tests and proposal sync | ~320 | 3–4 hours |
| **Total** | **~740** | **7–9 hours** |
