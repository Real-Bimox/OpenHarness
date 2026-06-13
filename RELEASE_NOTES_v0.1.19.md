# v0.1.19 — Optional Health/Status HTTP Server

A minor release adding an opt-in, local-only health/status HTTP API. The base
install and default behaviour are unchanged; the server is off unless
explicitly enabled, and it pulls in no new dependencies for the base install.

## Added

- **Optional health-status HTTP server.** `oh --health-server` (with
  `--health-server-port`, default loopback `127.0.0.1`) starts a daemon-thread
  HTTP server exposing `GET /health`, `GET /health/detailed`, `GET /api/status`,
  `GET /api/system/stats`, and `GET /v1/capabilities`, reporting the running
  version and runtime status as JSON.
- Shipped behind a new optional extra: `pip install "openharness-ai[health-server]"`
  (FastAPI + uvicorn).
- Composable with the long-lived local modes — `oh --headless --health-server`,
  `oh --task-worker --health-server`, and `oh --mcp-serve --health-server` — and
  rejects incompatible single-shot flags (`-p/--print`, `--dry-run`,
  `--continue`, `--resume`, `--backend-only`) with a clear error.
- Design: [docs/proposals/health-status-http-server.md](docs/proposals/health-status-http-server.md).

## Verification

- The `[health-server]` optional extra is declared in `pyproject.toml`
  (`fastapi>=0.100`, `uvicorn>=0.20`); the base install is unaffected.
- Endpoint and CLI behaviour are covered by `tests/test_health_server.py` and
  `tests/test_health_server_cli.py` (run with the `[health-server]` extra
  installed).
- Post-publish check: confirm `pip install "openharness-ai[health-server]"`
  resolves from the published package.
