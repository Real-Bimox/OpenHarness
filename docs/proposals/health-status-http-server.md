# Proposal: health-status-http-server

## Status

| Field | Value |
|---|---|
| Status | DRAFT |
| Proposal branch | `proposal/health-status-http-server` |
| Owner | Bahram Boutorabi |
| Created | 2026-06-11 |
| Related | [headless-local-control-api](headless-local-control-api.md), [observability-metrics](observability-metrics.md), [mcp-server-mode](mcp-server-mode.md) |

## Summary

Add an optional HTTP health and status server to OpenHarness so that container orchestrators, load balancers, monitoring systems, and remote operators can probe process liveness, inspect runtime state, and collect resource metrics without requiring SSH access, a TUI session, or the JSONL headless protocol.

The server is an **opt-in install-time extra** (`openharness[health-server]`) that introduces FastAPI and uvicorn only when explicitly requested. The base install is unchanged.

## Motivation

OpenHarness already produces rich diagnostics (`build_status()` in `diagnostics/snapshot.py`) and exposes them through three surfaces: the CLI (`oh diagnostics status --json`), the headless JSONL protocol (`diagnostics` request type), and the MCP server (`diagnostics_status` tool). All three require either a local terminal or a stdin pipe. None of them answer the simplest operational question an external system can ask: *is the process alive?*

Operational scenarios that need an HTTP health endpoint:

- **Container orchestration.** Docker `HEALTHCHECK`, Kubernetes liveness/readiness probes, and systemd watchdogs all expect an HTTP endpoint. They cannot drive a JSONL protocol or parse CLI output.
- **Load balancer health checks.** nginx, HAProxy, and cloud load balancers route traffic away from instances that fail a `GET /health`.
- **Remote monitoring.** Prometheus blackbox exporter, Datadog http_check, and custom dashboards need a URL to scrape — not SSH access.
- **Multi-service coordination.** An orchestrator running multiple OpenHarness workers (task workers, MCP servers, cron schedulers) needs to track which workers are alive, which are idle, and which are overloaded — without logging into each host.
- **Incident response.** Checking `exit_reason`, `recent_errors`, and resource usage over HTTP is faster and safer than SSH in constrained or production environments.

hermes-agent already implements this across two surfaces:

| Endpoint | Surface | Auth |
|---|---|---|
| `GET /health` | Gateway API server | None |
| `GET /health/detailed` | Gateway API server | None |
| `GET /v1/capabilities` | Gateway API server | Bearer token |
| `GET /api/status` | Web dashboard | Session token |
| `GET /api/system/stats` | Web dashboard | Session token |

OpenHarness can deliver equivalent value with a lighter server that reuses the existing `build_status()` pipeline and the `AppStateStore` observable state.

## Scope

In scope:

- An optional FastAPI application (`openharness[health-server]` extra) exposing health, status, and system-metrics endpoints over HTTP.
- A CLI flag `--health-server` to start the server as the primary mode (standalone) or as a background thread alongside another mode.
- A `GET /health` liveness endpoint (no auth, zero I/O, suitable for container probes).
- A `GET /health/detailed` readiness endpoint (no auth, reuses `build_status(probe=True)`).
- A `GET /api/status` operational-status endpoint (no auth in first release; auth gate deferred to a follow-up proposal).
- A `GET /api/system/stats` host/process metrics endpoint (no auth in first release).
- A `GET /v1/capabilities` API-discovery endpoint (no auth).
- Integration with long-running modes: `--headless`, `--task-worker`, and `--mcp-serve` can optionally start the health server as a background thread.

Out of scope:

- WebSocket endpoints.
- Session CRUD or chat endpoints (the JSONL headless protocol already covers this).
- Authentication or authorization on the health endpoints (bind to loopback only; see Security).
- Prometheus/OpenTelemetry native format (a future `/metrics` endpoint can be added without schema changes).
- A web dashboard SPA (this proposal is strictly a JSON API).
- Any change to the base install's dependency set.
- Cron integration (`oh cron start --health-server`) and interactive/REPL integration; these have different lifecycles and are deferred.

## Current State

### Existing Infrastructure

| Component | File | What It Provides |
|---|---|---|
| Canonical status document | `diagnostics/snapshot.py:213` `build_status()` | Version, run_id, run metadata, auth profile, recorder health, index health, event summary, token counters, recent errors, thread probe |
| Process metadata | `diagnostics/runinfo.py` | `current-run.json` with version, Python, platform, mode, PID, started_at, cwd_hash, data_dir, config_dir, active_profile, model, provider, flags |
| Event recorder health | `diagnostics/recorder.py` `health()` | `{enabled, events_written, events_dropped, attrs_redacted, queued}` |
| Watchdog | `diagnostics/watchdog.py` | In-flight operation tracking with per-operation slow thresholds |
| App state | `state/app_state.py` + `state/store.py` | Observable `AppState` dataclass with model, provider, auth_status, MCP status |
| Engine state | `engine/query_engine.py` | `QueryEngine` exposes messages, model, total_usage, has_pending_continuation() |
| Session state | `services/session_storage.py` | Full session persistence with load/list/save by ID |
| Headless status handler | `ui/app.py:899` `_emit_status()` | Already emits full state snapshots as JSONL |
| Headless diagnostics handler | `ui/app.py:915` `_emit_diagnostics_snapshot()` | Already emits diagnostics snapshots as JSONL |

### Gaps

1. **No HTTP server.** Every integration surface requires either a terminal, a stdin pipe, or the MCP stdio protocol. There is no URL an external system can `GET`.
2. **No liveness endpoint.** Container orchestrators cannot probe OpenHarness without a custom health-check script.
3. **No resource metrics endpoint.** Host CPU, memory, disk, and process RSS are not exposed to external consumers.
4. **No API discovery endpoint.** External integrators cannot programmatically determine what OpenHarness supports.

## Proposed Architecture

### 1. New Module: `src/openharness/api/health_server.py`

A self-contained FastAPI application factory that imports no UI modules and touches no global state at import time. All data is read lazily per-request from the existing diagnostics and state subsystems.

```python
def create_health_app() -> FastAPI:
    app = FastAPI(title="OpenHarness Health", lifespan=_lifespan)
    # register routes
    return app
```

### 2. Endpoints

#### `GET /health`

Liveness probe. No auth. Returns immediately.

```json
{
  "status": "ok",
  "platform": "openharness",
  "version": "0.1.17"
}
```

Suitable for: Docker `HEALTHCHECK`, Kubernetes liveness probe, load balancer backend check.

#### `GET /health/detailed`

Readiness probe. No auth. Calls `build_status(probe=True)`.

```json
{
  "status": "ok",
  "platform": "openharness",
  "version": "0.1.17",
  "pid": 42178,
  "uptime_seconds": 3600,
  "status_schema_version": 1,
  "generated_at": "2026-06-11T06:00:00Z",
  "run_id": "20260611-abc123",
  "run": { "mode", "pid", "started_at", "model", "provider", "flags" },
  "auth": { "active_profile", "provider", "model" },
  "recorder": { "enabled", "events_written", "events_dropped", "queued" },
  "index": { "enabled", "fts_enabled", "db_size_bytes" },
  "summary": { "events", "by_component", "counters", "last_turn_duration_ms", "last_api_call_duration_ms" },
  "recent_errors": [ { "ts", "component", "operation", "type", "reason" } ],
  "thread_probe": { "status", "duration_ms" }
}
```

Suitable for: operational dashboards, incident response, readiness probes (non-ok status if the thread probe times out or fails).

#### `GET /api/status`

Operational status. No auth (loopback bind). Combines `build_status(probe=False)` with `AppStateStore` when a store is injected.

```json
{
  "version": "0.1.17",
  "run_id": "20260611-abc123",
  "run": { ... },
  "auth": { "active_profile", "provider", "model" },
  "app_state": {
    "model": "claude-sonnet-4-6",
    "provider": "anthropic",
    "auth_status": "authenticated",
    "mcp_connected": true,
    "mcp_failed": false
  },
  "recorder": { "enabled", "events_written", "events_dropped", "queued" },
  "summary": { "events", "by_component", "counters" },
  "recent_errors": [ ... ]
}
```

Suitable for: admin tooling, orchestrator status checks, integration health monitoring.

#### `GET /api/system/stats`

Host and process resource metrics. No auth. Uses `psutil` when available, degrades gracefully to stdlib.

```json
{
  "os": "Linux",
  "os_release": "6.1.0",
  "arch": "x86_64",
  "hostname": "worker-01",
  "python_version": "3.12.3",
  "openharness_version": "0.1.17",
  "cpu_count": 8,
  "memory": { "total", "available", "percent" },
  "disk": { "total", "used", "free", "percent" },
  "cpu_percent": 23.4,
  "load_avg": [1.2, 0.9, 0.7],
  "uptime_seconds": 86400,
  "process": { "pid", "rss", "create_time", "num_threads" },
  "psutil": true
}
```

Suitable for: capacity planning, memory-leak detection, alerting, performance debugging.

#### `GET /v1/capabilities`

API discovery. No auth. Returns a machine-readable contract of what this OpenHarness instance supports.

```json
{
  "object": "openharness.capabilities",
  "platform": "openharness",
  "version": "0.1.17",
  "features": {
    "headless_protocol": true,
    "mcp_server": true,
    "multi_agent": true,
    "skill_learning_loop": true,
    "conversation_search": true,
    "cron_scheduler": true
  },
  "endpoints": {
    "health": { "method": "GET", "path": "/health" },
    "health_detailed": { "method": "GET", "path": "/health/detailed" },
    "status": { "method": "GET", "path": "/api/status" },
    "system_stats": { "method": "GET", "path": "/api/system/stats" },
    "capabilities": { "method": "GET", "path": "/v1/capabilities" }
  }
}
```

Suitable for: external UIs, custom orchestrators, version compatibility checks.

### 3. CLI Integration

#### Standalone mode

```bash
oh --health-server                # Start on 127.0.0.1:8642
oh --health-server --health-server-port 9090    # Custom port
```

The process runs the health server as its primary activity. Useful for sidecar containers or dedicated monitoring instances.

#### Background thread mode

```bash
oh --headless --health-server
oh --task-worker --health-server
oh --mcp-serve --health-server
oh --headless --health-server --health-server-port 8642
```

The health server starts in a daemon thread alongside the primary mode. External systems can probe the process while it performs its main work.

#### Implementation pattern

```python
import threading
import uvicorn

def start_health_server_background(host: str = "127.0.0.1", port: int = 8642):
    from openharness.api.health_server import create_health_app
    app = create_health_app()
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="health-server")
    thread.start()
    return server
```

### 4. Dependency Declaration

Add to `pyproject.toml` as an optional extra:

```toml
[project.optional-dependencies]
health-server = ["fastapi>=0.100", "uvicorn>=0.20"]
```

The base install is unchanged. Users who want the health server run:

```bash
pip install openharness[health-server]
```

Lazy import pattern: the CLI flag checks for FastAPI availability at invocation time and prints a clear install instruction if missing, matching the pattern used by `mcp/serve.py` for MCP dependencies.

### 5. Configuration

| Setting | Source | Default |
|---|---|---|
| `--health-server-port` | CLI flag (requires `--health-server`) | `8642` |
| `OPENHARNESS_HEALTH_SERVER_PORT` | Environment variable | `8642` |

No entry in `settings.json` for the first release. The health server is an operational concern, not a persistent user preference.

## Security

### Loopback-only default

The default bind address is `127.0.0.1`. The health server is reachable only from the local host. This is the correct default for:

- Local development (no accidental exposure).
- Container sidecar patterns (orchestrator on the same network namespace).
- Single-host deployments.

### Non-loopback binding

Non-loopback binding requires authentication and is deferred to a follow-up proposal. In v1, the server binds to `127.0.0.1` only — there is no `--health-server-host` option.

No authentication is enforced in the first release. Auth gating (session token, bearer token, or mTLS) is deferred to a follow-up proposal because:

1. The endpoints are read-only and expose no secrets (redacted diagnostics, no prompt text, no API keys).
2. Loopback-only binding is the default and is safe without auth.
3. Adding auth later is additive and does not break existing consumers.

### Data redaction

`build_status()` already applies the diagnostics redaction policy (no prompt text, no tool output, no secrets). The health server reuses this pipeline unchanged. `/api/system/stats` exposes host-level metrics (CPU, memory, disk) that are not sensitive.

### No CORS

No CORS headers are set. The health server is not designed for browser consumption. A future web dashboard proposal would add CORS separately.

## Performance

### Target: zero-I/O liveness

`GET /health` responds with three string constants — no I/O, no diagnostics files, no state stores. No timing gate in CI; the zero-I/O property is what matters.

### Target: bounded detailed queries

`GET /health/detailed` calls `build_status(probe=True)` which reads the daily JSONL event file and the `current-run.json` file. The diagnostics recorder already caps daily files (25 MB/day, 10 000-event queue). The thread probe is bounded at 2 seconds. In practice, `build_status()` completes in under 50 ms on a warm system.

### Background thread overhead

The uvicorn daemon thread adds ~10 MB RSS (Python + FastAPI + uvicorn). No measurable CPU impact when idle.

### No impact on base install

Users who do not install `openharness[health-server]` see zero changes: no new imports, no new threads, no new files.

## Acceptance Criteria

1. `pip install openharness[health-server]` installs fastapi and uvicorn without errors.
2. `oh --health-server` starts an HTTP server on `127.0.0.1:8642`.
3. `curl http://127.0.0.1:8642/health` returns `{"status": "ok", "platform": "openharness", "version": "..."}` (zero-I/O response, no timing gate).
4. `curl http://127.0.0.1:8642/health/detailed` returns a valid `build_status()` document with `thread_probe` populated.
5. `curl http://127.0.0.1:8642/api/status` returns a document where `app_state` is included when a store is injected; omitted otherwise.
6. `curl http://127.0.0.1:8642/api/system/stats` returns host metrics (with `psutil` fields when psutil is installed, without errors when it is not).
7. `curl http://127.0.0.1:8642/v1/capabilities` returns a valid capabilities document listing all five endpoints.
8. `oh --headless --health-server` starts the headless JSONL protocol on stdin/stdout and the health server simultaneously.
9. Running `oh --health-server` without `openharness[health-server]` installed prints a clear install instruction and exits with code 1.
10. `GET /health` is reachable while a headless turn is active (the background thread is not blocked by the async event loop).
11. No runtime imports, threads, or state are created when `--health-server` is not passed (base install unchanged).
12. Non-loopback binding is not possible in v1 (no `--health-server-host` option; server binds to `127.0.0.1` only).

## Future Work

- **Authentication gate.** Session token or bearer token on `/api/status` and `/api/system/stats` when bound to non-loopback.
- **Prometheus `/metrics`.** Native Prometheus exposition format derived from `build_status()` and `summarize_events()`.
- **WebSocket `/api/events`.** Real-time streaming of diagnostics events to external consumers (replaces SSE).
- **Session CRUD.** `GET /api/sessions`, `GET /api/sessions/{id}/messages` for remote session inspection.
- **Diagnostic bundle.** `GET /api/diagnostics/bundle` returns a `.tar.gz` generated by `export_bundle()`.
- **Custom health checks.** User-defined readiness conditions (e.g., "MCP connected", "index rebuilt", "no errors in last 5 minutes") expressed as config and evaluated by `/health/detailed`.
