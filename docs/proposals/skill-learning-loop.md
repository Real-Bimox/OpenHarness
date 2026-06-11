# Proposal: skill-learning-loop

## Status

| Field | Value |
|---|---|
| Status | IN PROGRESS |
| Proposal branch | `proposal/learning-search-resilience` |
| Owner | Bahram Boutorabi |
| Created | 2026-06-11 |
| Source study | hermes-agent @ `298bb93d3` (`tools/skill_manager_tool.py`, `agent/background_review.py`, `agent/curator.py`, `tools/skill_usage.py`, `tools/write_approval.py`) |
| Related | [conversation-search](conversation-search.md), report `docs/reports/openharness-vs-hermes-agent.md` |

## What hermes-agent does (the source capability)

Four cooperating subsystems make hermes "self-improving":

1. **`skill_manage` write tool** — the only sanctioned mutation path for the
   skill library: create/edit/patch/delete/write_file/remove_file with name
   regex + 64-char cap, frontmatter validation (name, description ≤1024,
   non-empty body), 100k-char SKILL.md cap, 1 MiB support-file cap, a
   `{references,templates,scripts,assets}` subdir allow-list, traversal +
   symlink-resolve containment, same-directory atomic writes, a pinned-skill
   delete guard, and `absorbed_into` bookkeeping on deletes. An optional
   security scan (off by default) can block and roll back writes.
2. **Post-turn background review** — every ~10 user turns (memory) or ~10
   tool iterations (skills), after the response is delivered, a second agent
   is forked in a daemon thread with the parent's provider, model, byte-equal
   system prompt and tools array (for prompt-cache reuse, measured ~26%
   cost reduction), a runtime tool whitelist (memory + skill tools only), an
   auto-deny approval callback, and the full conversation replayed. Its
   prompt instructs: prefer patching the loaded skill, maintain class-level
   umbrella skills (never session-named one-offs), treat user corrections
   and frustration as first-class learning signals, never capture
   environment-dependent failures or "tool X doesn't work" claims, and
   "Nothing to save." is allowed but discouraged ("be ACTIVE").
3. **Usage telemetry + weekly curator** — a `.usage.json` sidecar (file lock,
   atomic writes, best-effort bumps) counts uses/views/patches; lifecycle
   active → stale (30d) → archived (90d) with first-sight clock seeding so
   nothing is mass-pruned on upgrade; archive = move to `.archive/`
   (recoverable), **never delete**. A weekly LLM pass (routable to a cheap
   model) consolidates agent-created skills into umbrellas, with structured
   YAML output, rename reconciliation, cron-reference rewriting, and
   per-run reports.
4. **Write approval** — optional staging: skill writes become pending records
   (exact replayable kwargs) the user can list/diff/approve/discard;
   memory writes can prompt inline.

Weaknesses found in their implementation (from the source study, addressed
here): bundled/hub skill protection is prompt-only — the tool will happily
edit shipped skills; the curator fork has full `terminal` access plus an
explicit "fewer than 10 archives means you stopped too early" quota that
incentivizes destructive action; the "be ACTIVE" doctrine biases toward
accreting mediocre skills with no human in the loop by default; the security
scan is off by default while the review fork replays untrusted tool output
(prompt-injection persistence risk); the approval diff uses a different
patch algorithm than the real apply; several guards fail open.

## OpenHarness design

Same four subsystems, with these deliberate differences (each one exists to
fix a documented hermes weakness or to fit OpenHarness's default-deny
philosophy — capability is otherwise matched):

1. **Tool-level protection, not prompt-level**: `skill_manage` refuses to
   modify bundled and plugin-provided skills structurally (it only operates
   inside the user skills directory). hermes's biggest footgun is closed.
2. **Write scanning is ON by default** (`skills.guard_writes`): secret
   patterns (shared with team memory) plus prompt-injection markers; a
   blocked write rolls back. hermes leaves this off; given the review fork
   replays untrusted tool output, on-by-default is the safer posture and
   costs microseconds.
3. **The review fork is skills-only.** OpenHarness already runs background
   memory extraction (`services/memory_extract`) after turns; duplicating
   memory capture in the review fork would double-write. Honest divergence:
   hermes combines both in one fork; we keep two specialized background
   passes.
4. **No activeness quota.** The review prompt keeps hermes's class-level
   umbrella doctrine, the user-corrections-are-signals rule, and the full
   anti-capture list verbatim in spirit, but drops "a pass that does nothing
   is a missed opportunity" and the curator's 10-archive quota. The fork is
   told a small high-confidence update beats none — and that no update is a
   legitimate outcome.
5. **The curator has no shell.** Archival is an internal move operation;
   the curator fork's registry contains only skill read/manage tools.
6. **Approval diffs use the real patch engine** — the preview is generated
   by applying the identical operation to a copy, so what the user approves
   is what runs.
7. Same-model fork with shared client: the fork reuses the live
   `api_client` instance (connection pool + provider prompt cache) and the
   session's model; `skills.review_model` can route it to a cheaper model
   (hermes only offers that for the curator).

### Components

- `src/openharness/skills/usage.py` — sidecar telemetry + lifecycle
  (`.usage.json` in the user skills dir; lock + atomic write; states
  active/stale/archived; pinned; first-sight seeding; archive/restore moves).
- `src/openharness/tools/skill_manage_tool.py` — the write tool (tool #44),
  provenance-aware via a ContextVar so only background-review creations are
  curator-eligible.
- `src/openharness/services/skill_review.py` — trigger counters (every
  `skills.review_interval_turns` user turns, default 10, 0 disables;
  one in flight; skipped when conversation hasn't grown), fork construction
  (restricted registry: `skill`, `skill_manage`; max 8 turns; auto-deny
  permissions; provenance set), review prompt, summary surfaced as a
  status event.
- `src/openharness/services/skill_curator.py` — auto-transitions + weekly
  gated LLM consolidation pass (routable via `skills.curator_model`),
  archive-only invariant, run reports under
  `<data_dir>/reports/skill-curator/`.
- `src/openharness/services/skill_approval.py` — staging store at
  `<data_dir>/pending/skills/`, replay with gate bypass, real-engine diffs.
- Settings: `skills.review_enabled` (default **true** — capability parity
  with hermes; the cost profile matches theirs and benefits from our prompt
  caching), `review_interval_turns=10`, `review_model=""`,
  `write_approval=false`, `guard_writes=true`, `curator_enabled=true`,
  `curator_interval_hours=168`, `stale_after_days=30`,
  `archive_after_days=90`.

### Exposure surfaces

- **Agent tools**: `skill_manage` (write), existing `skill` (read).
- **CLI**: `oh skills usage|pin|unpin|pending|approve|discard|curator ...`.
- **Headless JSONL**: additive `skill_loop_status` request (telemetry,
  pending count, last review/curator runs); pending approval stays a
  human/CLI surface by design.
- **MCP** (F4): `skill_loop_status`, `list_pending_skill_writes`,
  `approve_skill_write`.

## Capability parity statement (honest)

- Equal: full write-tool action set and validation; post-turn self-review
  with conversation replay, cache-reusing fork, tool restriction, auto-deny;
  usage telemetry; stale/archive lifecycle with first-sight seeding and
  pinning; weekly LLM consolidation with reports; staged write approval
  with diff/approve/discard; provenance gating (only agent-created skills
  are curator-eligible).
- Better: structural protection of shipped skills; scanning on by default;
  no shell in the curator; no destructive quotas; approval diff fidelity.
- Not carried over, with reasons: hermes's fuzzy patch matching (we use
  exact-match with a no-match preview, consistent with our `edit_file`;
  the fork can re-read and retry); combined memory+skill review in one fork
  (we already background memory extraction separately); cron-reference
  rewriting on consolidation (OpenHarness cron jobs reference prompts, not
  skill names — nothing to rewrite today; revisit if that changes);
  cross-profile skill lookup (no profile homes in OpenHarness).
- Known cost (stated plainly): an enabled review pass is a real model call
  every ~10 turns, 1–8 iterations on the session model (or
  `skills.review_model`). hermes pays the same; both rely on provider
  prompt caching to discount it.
