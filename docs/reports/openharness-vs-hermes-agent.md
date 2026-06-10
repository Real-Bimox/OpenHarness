# OpenHarness vs hermes-agent — Feature-by-Feature Comparison

Date: 2026-06-10. Method: four parallel code-level studies (core engine/models,
tools/skills/plugins/MCP, orchestration/gateway, memory/sessions/UX) reading
both codebases directly. Baselines: OpenHarness `main` (v0.1.14),
hermes-agent `main` (commit 298bb93d3, same day).

## Scale

| | OpenHarness | hermes-agent |
|---|---|---|
| Python code | ~53,000 lines | ~175,000 lines |
| Commits | 456 | 11,245 |
| Contributors | small team | ~1,400 (Nous Research) |
| Test files | 105 (~1,200 tests) | 110 dirs (~29,000 test functions) |
| Age/maturity | clean young rewrite | 3+ years battle-hardened |

## Scorecard

| Feature area | Winner | Why in one line |
|---|---|---|
| Multi-agent orchestration | **OpenHarness** | persistent teams, mailboxes, permission sync, resumable workers vs a blocking thread pool |
| Agent loop architecture | **OpenHarness** (design) / hermes (robustness) | typed async events vs a 4,200-line battle-tested loop |
| Provider/model support | **hermes** | 35+ providers, fallback chains, credential rotation, live model catalog |
| Error recovery | **hermes** (largest gap) | a 20-category recovery state machine vs retry-3-times-then-die |
| Context compaction | **hermes** (triggers) / OpenHarness (carryover) | real token feedback + aux cheap model vs estimate-only triggers |
| Prompt caching | **OpenHarness** (placement) / hermes (cost reporting) | precise stable-prefix boundary vs dollar-level cost tracking |
| Built-in tools | **hermes** (breadth) / OpenHarness (typing) | browser/computer-use/voice/video vs Pydantic contracts + lazy loading |
| Toolsets/gating | **hermes** | 35 named composable toolsets incl. webhook-safe set; OpenHarness has one boolean |
| Skills | **hermes**, decisively | real self-improving loop + 50k-skill hub; OpenHarness skills are read-only |
| Plugins | **hermes** (power) / OpenHarness (safety+interop) | pip-distributed code plugins vs declarative Claude-Code-compatible manifests |
| MCP client | **hermes** | SSE, OAuth, sampling, injection scanning, per-server timeouts |
| MCP server / ACP | **only hermes** | exposes itself as MCP; full editor (ACP) adapter |
| Permissions | **OpenHarness** (model) / hermes (UX) | default-deny modes vs allow-unless-dangerous with great approval UX |
| Sandboxing | **OpenHarness** (per-command) / hermes (whole-agent) | bwrap/srt per tool call vs Docker the whole agent |
| Messaging gateway | **hermes**, by a mile | ~20 platforms at 10x depth, pairing auth, OpenAI-compatible API server |
| Scheduling (cron) | **hermes** | script injection, human schedules, injection scanning, webhook triggers |
| Headless/embedding | **OpenHarness** | JSONL control protocol, stream-json, dry-run preview — better specified |
| Batch/scale-out | **hermes** | dataset batch runner, Docker/Modal environments, HTTP runs API |
| Memory schema | **OpenHarness** | typed/TTL'd/deduped/usage-tracked records vs untyped text blobs |
| Memory system overall | **hermes** | frozen cache-stable snapshots, USER.md user model, self-review fork |
| Memory hygiene | split | OpenHarness: secrets/TTL/staleness; hermes: injection/poisoning defense |
| Session search | **only hermes** | FTS5 search over all past conversations as an agent tool |
| TUI | parity (arch) / hermes (maturity) | both React+Ink over Python; hermes has newer stack + protocol docs |
| Web/desktop | **only hermes** | web dashboard, Electron app, 341-page docs site |
| Voice | **only hermes** | working STT/TTS vs an OpenHarness stub |
| i18n | **only hermes** | 16 locales |
| Onboarding | **hermes** | setup wizard + doctor + contextual hints vs read-the-README |
| Deployment | **hermes** | Docker/compose/Nix/Homebrew/Termux/desktop vs pip-only |
| Docs/process | hermes (volume) / OpenHarness (discipline) | 341 pages vs proposal/report review culture |

## The one-line summary

**OpenHarness is building the better orchestration kernel; hermes-agent is the
better finished product.** OpenHarness's unique strengths are exactly the things
hermes is weakest at (persistent multi-agent teams, typed clean architecture,
default-deny permissions, per-command sandboxing, embeddable headless control).
hermes's unique strengths are exactly OpenHarness's gaps (self-improving
skills, conversation search, 20-platform gateway, provider resilience, cost
tracking, deployment reach). They are near-perfect complements.

## Detailed findings

### 1. The brain (agent loop + model layer)
Both run the same fundamental loop. OpenHarness's is cleanly engineered:
async, typed stream events, parallel tool calls via asyncio, never leaves a
dangling tool call. hermes's is one enormous function but it survives things
OpenHarness doesn't handle: stalled streams, partial responses, malformed tool
arguments, provider-specific quirks. hermes classifies ~20 failure types and
knows whether to retry, compress context, rotate to another API key, or fail
over to a backup provider — OpenHarness retries three times and gives up.
hermes also reports real dollar costs; OpenHarness only counts tokens.
OpenHarness's brand-new prompt caching places breakpoints more precisely than
hermes's "system + last 3 messages" heuristic. OpenHarness's `effort` setting
only actually reaches the Codex provider; hermes wires reasoning/thinking
controls natively for Anthropic, Kimi, Gemini, OpenRouter, and more.

### 2. Tools, skills, and the "learning loop"
hermes ships roughly twice the tool surface (full browser automation, computer
use, voice, video, Discord, Home Assistant) and gates tools through 35 named
toolsets — including a deliberately restricted set for untrusted webhook
input. OpenHarness's 42 tools are better engineered per-tool (typed inputs,
read-only introspection feeding permissions, lazy loading).

hermes's headline claim is true in code: after every turn a background copy of
the agent reviews the conversation and writes/patches skills and memories; a
weekly "curator" archives stale agent-created skills using usage telemetry;
a `skill_manage` tool gives the agent a guarded write path; and a skills hub
installs from ClawHub (~50k skills), GitHub, and other catalogs through a
quarantine → scan → trust-tier pipeline. OpenHarness can *read* skills
(with clean Claude Code compatibility) but cannot create, improve, or install
them.

hermes also speaks two protocols OpenHarness doesn't: it can act as an MCP
*server* (so Claude Code/Cursor can drive it) and as an ACP agent (so editors
like Zed can embed it).

### 3. Safety
Philosophically opposite. OpenHarness: default-deny — mutating tools need
confirmation unless allowed, plan mode blocks them, a hardcoded sensitive-path
denylist (~/.ssh, cloud creds) sits above all user config, and individual
commands can be wrapped in OS sandboxes (bubblewrap/sandbox-exec/Docker).
hermes: allow-unless-dangerous — a large curated pattern library catches
dangerous commands with excellent approval UX (once/session/always scopes,
LLM risk assessment, approvals deliverable over Telegram), plus injection and
memory-poisoning scanning OpenHarness lacks. OpenHarness's *model* is safer;
hermes's *detection and UX* are richer. The ideal system uses both.

### 4. Multi-agent work
OpenHarness's standout area: YAML agent definitions (tools, model, effort,
permission mode per agent), two execution backends, persistent named teams,
file-based mailboxes between agents, leader-routed permission approval for
workers, git-worktree isolation, and (since v0.1.13) workers that survive
restarts with their conversation intact. hermes delegation is a blocking
thread pool of ephemeral children — simpler, production-proven, but not an
agent society. hermes's mixture-of-agents (multi-model ensemble) tool is a
pattern OpenHarness lacks.

### 5. Reaching the outside world
hermes is gateway-first: ~20 messaging platforms with deep per-platform
features (Telegram forum topics, album batching, voice notes), pairing-code
authentication, HMAC webhooks, an OpenAI-compatible HTTP API so any chat
frontend can drive it, plus cron with human-readable schedules, pre-run script
injection, and prompt-injection scanning of scheduled prompts. OpenHarness's
ohmo gateway has cleaner session routing and ~10 thinner adapters; its cron
has the right skeleton; it has no webhook triggers, no HTTP API, no Docker
image, no Termux path. For "run on a VPS and talk from your phone," hermes is
the proven option today.

### 6. Memory and sessions
OpenHarness has the better memory *records*: typed frontmatter, TTLs,
dedupe signatures, usage-count staleness tracking, team vaults with blocking
secret scans, and a scheduled "dream" consolidation with backup/diff/rollback.
hermes has the better memory *system*: bounded MEMORY.md + USER.md snapshots
frozen per session (so prompt caches stay warm), an explicit user model and
editable persona (SOUL.md), per-turn self-review, eight pluggable external
memory backends, and write-time injection scanning. hermes's killer session
feature: every conversation is FTS5-indexed in SQLite and the agent can search
its own past at zero LLM cost. OpenHarness's snapshots can resume but never
search.

### 7. Surfaces and polish
Both arrived at the same TUI architecture (React+Ink frontend, Python brain);
hermes's is more mature and adds a web dashboard, Electron desktop app, working
voice modes, 16 languages, a 3,400-line setup wizard, a doctor command, and a
341-page docs site. OpenHarness counters with the best scripting surface:
`-p` with json/stream-json, the formally specified headless JSONL protocol,
and the genuinely novel `--dry-run` readiness preview — plus a
proposal/review/report engineering culture hermes doesn't show.

## Priority list: what OpenHarness should adopt (highest value first)

1. **FTS5 conversation search** (SQLite index + agent tool) — biggest single gap.
2. **Skill write path + learning loop** — `skill_manage`-style tool, post-turn
   background review, usage-driven curator. OpenHarness already has the clean
   read side.
3. **Error-recovery classifier + provider fallback chains + credential
   rotation** — the biggest robustness gap in the engine.
4. **Webhook triggers + Docker/compose packaging for ohmo** — completes the
   automation triad and the VPS story cheaply.
5. **Dollar cost reporting** — token counts (incl. cache) already exist;
   pricing tables are the missing 20%.
6. **Named composable toolsets with a webhook-safe set** — pairs naturally
   with the existing permission modes.
7. **Real-usage compaction triggers** (use returned input_tokens, learn
   context windows) instead of chars/4 estimates.
8. **MCP server / ACP exposure of the headless runtime** — interop for free.
9. **USER.md-style user model + persona file; injection scanning on memory
   writes.**
10. **Native Anthropic adaptive-thinking wiring** (effort currently only
    reaches Codex).
11. Setup wizard / doctor; pairing-code auth for channels; voice.

## What OpenHarness already does better (protect these)

- The orchestration kernel (teams/mailboxes/permission-sync/resumable
  workers) — hermes has nothing like it.
- Typed, async, testable architecture throughout.
- Default-deny permission model + per-command sandboxing + sensitive-path
  denylist.
- Precise prompt-cache boundary placement; compaction carryover attachments.
- Memory record schema (types/TTL/dedupe/usage) and team-memory secret gates.
- The headless JSONL control protocol and `--dry-run` (better specified than
  hermes's equivalents).
- Claude Code ecosystem compatibility (skills/plugins/agent definitions).
- Engineering process: proposals, readiness reviews, measured budgets.
