# Proposal: mcp-server-mode

## Status

| Field | Value |
|---|---|
| Status | IMPLEMENTED |
| Proposal branch | `proposal/learning-search-resilience` |
| Owner | Bahram Boutorabi |
| Created | 2026-06-11 |
| Source study | hermes-agent @ `298bb93d3` (`mcp_serve.py`) |
| Related | [conversation-search](conversation-search.md), [skill-learning-loop](skill-learning-loop.md), [error-recovery](error-recovery.md) |

## What hermes-agent does

hermes ships `mcp_serve.py`: a FastMCP stdio server exposing ~10 tools
(conversation listing/reading, event polling, message sending, permission
listing/responding, channel listing) so an MCP host — Claude Code, Cursor,
Zed — can drive hermes's messaging sessions and even answer its approval
prompts. OpenHarness had no MCP server at all (it was MCP client only); this
was a named gap in the comparison report.

## OpenHarness design

`oh --mcp-serve` runs a FastMCP stdio server (`src/openharness/mcp/serve.py`)
built on the official `mcp` SDK already vendored for the client — no new
runtime dependency. It wraps the **same internal operations** as the headless
JSONL protocol, so the two machine surfaces cannot drift:

- `search_sessions` — discover/read/scroll/browse over the conversation index.
- `list_sessions` — recent sessions with previews.
- `skill_loop_status` — skill telemetry, lifecycle states, pending writes,
  last curator run.
- `run_skill_curator` — lifecycle pass (and LLM consolidation unless dry-run).
- `recovery_status` — configured fallback chain and credential-pool sizes.

## Capability parity statement (honest)

- Equal in kind: OpenHarness now exposes itself as an MCP server, the surface
  hermes has and OpenHarness lacked.
- Scope difference, stated plainly: this first server exposes the three new
  features plus session listing — all **read/maintenance** operations. It
  does **not** yet expose turn submission, streaming output, or permission
  answering over MCP. hermes's server does expose message-send and
  permission-respond. Submit/stream/approve over MCP is a larger design
  question (it overlaps the headless JSONL protocol's stateful turn
  execution) and is deliberate follow-up scope, not a silent omission. The
  headless JSONL protocol remains the full stateful surface today.
