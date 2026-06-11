# TODO

## Health Status HTTP Server

- [ ] Decide whether the merged health-server work should ship as the next patch release.
- [ ] If releasing, update the README `Unreleased` health-server entry to the target release version.
- [ ] If releasing, add or update release notes for the health-server feature.
- [ ] If releasing, verify the published package installs with `pip install "openharness-ai[health-server]"`.

## Deferred Follow-Ups

- [ ] Wire `AppStateStore` into background health-server mode after the runtime startup path is refactored.
- [ ] Add authenticated non-loopback binding in a follow-up proposal before exposing the health server beyond `127.0.0.1`.
- [ ] Add cron integration only after its subprocess lifecycle is designed.
- [ ] Consider a future Prometheus `/metrics` endpoint after the JSON health/status surface is merged.
