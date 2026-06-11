# TODO

## Health Status HTTP Server

- [ ] Open a PR from `proposal/health-status-http-server` to `main` in `Real-Bimox/OpenHarness`.
- [ ] Review commit `14ea306` on the PR, focusing on `src/openharness/api/health_server.py`, `src/openharness/cli.py`, and the optional `health-server` dependency extra.
- [ ] On the review workstation, install the optional test dependencies with `python -m pip install -e ".[dev,health-server]"`.
- [ ] Run `ruff check .`.
- [ ] Run `git diff --check`.
- [ ] Run `.venv/bin/python -m pytest -q` if using the checked-in project venv layout, or `python -m pytest -q` from a venv whose `bin/oh` script exists.
- [ ] Run `python -m pytest tests/test_health_server.py tests/test_health_server_cli.py -q` with `fastapi` and `uvicorn` installed.
- [ ] Smoke-test `oh --health-server --health-server-port 0` or the Python launcher equivalent and verify `/health` returns HTTP 200.
- [ ] If the PR is accepted, merge to `main`.
- [ ] After merge, update `docs/proposals/health-status-http-server.md` status from `DRAFT` to `IMPLEMENTED` on `main`.
- [ ] After the IMPLEMENTED status is on `main`, archive the remote proposal branch with `git push origin <merge-sha>:refs/heads/archive/proposal/health-status-http-server`, then delete `origin/proposal/health-status-http-server`.
- [ ] Decide whether the merged health-server work should ship as the next patch release.

## Deferred Follow-Ups

- [ ] Wire `AppStateStore` into background health-server mode after the runtime startup path is refactored.
- [ ] Add authenticated non-loopback binding in a follow-up proposal before exposing the health server beyond `127.0.0.1`.
- [ ] Add cron integration only after its subprocess lifecycle is designed.
- [ ] Consider a future Prometheus `/metrics` endpoint after the JSON health/status surface is merged.
