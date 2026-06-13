# Nanobot Value Assessment for OpenHarness

| Field | Value |
|---|---|
| Status | **ASSESSMENT — NO DECISION** |
| Date | 2026-06-13 |
| Purpose | Comprehensively and neutrally evaluate the value [`HKUDS/nanobot`](https://github.com/HKUDS/nanobot) *might* deliver to OpenHarness (`oh`). **No decision has been or will be made until this review is weighed by the owner.** This document presents evidence and options, not a recommendation. |
| Method | Three independent web-research lenses (architecture/engineering, feature depth, maturity/licensing/cost) over the live repo, GitHub API, in-repo docs, release notes, and one independent review — cross-checked against `oh`'s known capabilities. All non-obvious claims are sourced. |

## 0. Disambiguation (read first)

Two unrelated projects are named "nanobot":

- **`HKUDS/nanobot`** — Python personal AI agent by the HKUDS lab (HKU). **This is the subject of this assessment** and the lineage source of `oh`'s channels.
- **`obotai/nanobot`** — a separate TypeScript "MCP-UI / agent-to-agent" gateway by a different author. **Out of scope.** Any "MCP-UI rendering" capability seen in search results belongs to *this* project, not HKUDS. ([glama.ai](https://glama.ai/blog/2025-09-23-nanobot-by-obotai-architecting-real-mcp-agents-with-mcp-ui))

## Owner direction (decision log)

*Records the owner's steer as this assessment is reviewed. It informs — it does not finalise — any adoption.*

- **2026-06-13** — **Microsoft Teams, WeChat, and Signal channels, plus CLI-Anything** are accepted as **future "nice-to-haves"** (explicitly *not* a current need). Direction: implement them **natively in `oh`** — re-implemented to fit `oh`'s message-bus / permission model (adoption mode **(c)**) — **not** as an external nanobot dependency or code port. (Resolves §8 open question 2 for these four items; WeCom / Enterprise WeChat is not in scope.)
- **2026-06-13** — **Nanobot integration PARKED — not proceeding for now.** The four items above remain *future* nice-to-haves (implement natively when prioritised). The **browser WebUI** is the trigger to revisit: reconsider this assessment if/when a browser UI becomes a requirement.

## 1. Identity & lineage

`HKUDS/nanobot` and `HKUDS/OpenHarness` (this repo's `upstream`) are **sibling projects from the same lab.** `oh`'s channel/gateway/message-bus subsystem was **derived from nanobot** — same `InboundMessage`/`OutboundMessage` types, same inbound/outbound `MessageBus`, the same channel line-up, and a residual `NanobotDingTalkHandler` class. nanobot remains a **leaner personal-agent** focus; `oh` has grown a broader production surface (see §4). Crucially, `nanobot` is **not** a dependency of `oh` today — the ~15 `nanobot` strings in `oh`'s code are a vestigial codename (tracked separately for de-branding).

## 2. What nanobot is today (facts)

| Attribute | Value | Source |
|---|---|---|
| Language | Python 78.4%, TypeScript 20.9% (the WebUI) | [GitHub API](https://api.github.com/repos/HKUDS/nanobot) |
| License | **MIT** (© Xubin Ren & contributors) | [LICENSE](https://raw.githubusercontent.com/HKUDS/nanobot/main/LICENSE) |
| Created / latest | **2026-02-01** / **v0.2.1 (2026-06-01)**, last push 2026-06-12 | [GitHub API](https://api.github.com/repos/HKUDS/nanobot), [releases](https://github.com/HKUDS/nanobot/releases) |
| Momentum | ~44.1k stars, 7.8k forks, 396 contributors (heavily concentrated in the lead), 271 open issues, 601 open PRs / 980 merged | [GitHub API](https://api.github.com/repos/HKUDS/nanobot) |
| Distribution | PyPI `nanobot-ai`; Docker/compose; systemd / macOS LaunchAgent; Python ≥3.11 | [README](https://raw.githubusercontent.com/HKUDS/nanobot/main/README.md) |

**Reading:** very high momentum for a ~4.5-month-old project, daily-fresh commits — but **pre-1.0 (0.2.x)**, so the maintainers themselves treat the API/feature set as not yet stable, and there is a large open-PR backlog and a real bus-factor concentration.

## 3. Architecture (how it's built)

- **One small agent loop**, "messages-as-context, not a heavy orchestration layer." A notable **two-tier split**: `AgentLoop` (channel-facing turn: session/workspace/context) vs `AgentRunner` (model-facing inner loop: provider calls, streaming, tools, iteration limits). ([docs/architecture.md](https://raw.githubusercontent.com/HKUDS/nanobot/main/docs/architecture.md))
- **Thin typed message bus** (`bus/`, ~13 KB) with `InboundMessage`/`OutboundMessage` — the same shape `oh` inherited.
- **MCP-as-a-tool**, not a core subsystem — it plugs into the loop through the same contract as filesystem/shell/web. ([docs/architecture.md](https://raw.githubusercontent.com/HKUDS/nanobot/main/docs/architecture.md))
- **File-based persistence:** JSONL sessions + Markdown/JSONL memory, atomic-write+fsync; a background **"Dream"** consolidation pass. ([docs/concepts.md](https://raw.githubusercontent.com/HKUDS/nanobot/main/docs/concepts.md))
- **Clean embedding API:** a `Nanobot` programmatic facade (`from_config()`, async `run()` → `RunResult`). ([nanobot.py](https://raw.githubusercontent.com/HKUDS/nanobot/main/nanobot/nanobot.py))
- **Extensibility asymmetry:** channels are **true out-of-tree plugins** via Python entry points (`nanobot.channels` + a 3-method `BaseChannel`) — a stronger boundary than `oh`'s in-tree registry; providers, by contrast, are in-tree registry edits. ([docs/channel-plugin-guide.md](https://raw.githubusercontent.com/HKUDS/nanobot/main/docs/channel-plugin-guide.md))
- **"Small core" caveat:** the headline is partly a framing artifact — the repo's own `core_agent_lines.sh` hand-picks directories and excludes `tools/`. The two central files are large monoliths (`loop.py` ~1,837 lines, `runner.py` ~1,543), and `agent/` top-level alone is ~6k lines. The *architecture* (thin bus, pluggable context) is genuinely lightweight; the *implementation* is substantial. ([core_agent_lines.sh](https://raw.githubusercontent.com/HKUDS/nanobot/main/core_agent_lines.sh))
- **Engineering signals:** pydantic v2, typer, hatchling; tests mirror the source tree (15 sub-packages). **Gaps:** CI lint runs only `ruff --select F` (PyFlakes) despite a stricter declared rule set, and there is **no static type-checking** (no mypy/pyright). ([ci.yml](https://raw.githubusercontent.com/HKUDS/nanobot/main/.github/workflows/ci.yml))

## 4. Capability comparison vs `oh`

**A. Overlap — `oh` already has these (shared lineage; not differentiators):** chat channels (core set), message bus, MCP **client**, memory + "Dream"/auto-dream consolidation, cron scheduling, provider fallback chains, web search, sandbox/Docker execution, MCP **server** mode (both have).

**B. Where `oh` is ahead / nanobot lacks:** observability/diagnostics subsystem (nanobot offers Langfuse tracing only), headless JSONL control protocol, optional health-status HTTP server, prompt-caching breakpoints, conversation full-text search, persistent task workers, swarm/multi-agent coordination, and **skill auto-discovery** — nanobot's skill system is documented as *"entirely passive"* / auto-generation unconfirmed ([issue #2927](https://github.com/HKUDS/nanobot/issues/2927)), whereas `oh` has a skill-learning loop with discovery + curator.

**C. Where nanobot is ahead / `oh` lacks — the candidate value-adds (see §5).**

## 5. Candidate value-adds to `oh` (neutral — potential, not endorsement)

Ranked by apparent magnitude of the gap they would fill:

1. **Browser WebUI workbench** — the single biggest non-overlapping capability. A React 18 / Vite / Tailwind / shadcn SPA **bundled inside the wheel** (no separate build), served over one WebSocket: live file-edit activity, project workspaces + access controls, thought/response timelines, in-UI model/context/preset controls, image uploads, i18n, settings/keys & MCP-config UI. `oh` is TUI/CLI + headless only. ([v0.2.1 notes](https://github.com/HKUDS/nanobot/releases/tag/v0.2.1), [DeepWiki WebUI](https://deepwiki.com/HKUDS/nanobot/14-webui))
2. **Four channels `oh` lacks** — Microsoft Teams, WeChat (personal), WeCom (Enterprise WeChat), Signal. (`oh` already has the other ~10.) Each is a self-contained `BaseChannel`. ([DeepWiki channels](https://deepwiki.com/HKUDS/nanobot/5.5-other-channels))
3. **CLI Apps / "CLI-Anything"** — wraps arbitrary CLI tools as agent capabilities, unified with MCP. ([v0.2.1 notes](https://github.com/HKUDS/nanobot/releases/tag/v0.2.1))
4. **Pairing-code channel access control** — approve/deny/revoke onboarding flow for chat channels. ([configuration.md](https://github.com/HKUDS/nanobot/blob/main/docs/configuration.md))
5. **Possibly additive (lower confidence):** Langfuse tracing; a richer model-preset onboarding wizard; notably hardened cron durability (corruption quarantine, multi-instance journaling) that *may* exceed `oh`'s — all need a side-by-side with `oh`'s implementations to confirm.

**Architectural patterns worth studying regardless of adoption:** the two-tier `AgentLoop`/`AgentRunner` split; entry-point channel plugins; MCP-as-a-tool; the `Nanobot` embedding facade.

## 6. Costs, risks & constraints (factual)

- **Pre-1.0 volatility.** 0.2.x with weekly releases and 600+ open PRs — any tight coupling tracks a fast-moving, unstable surface.
- **Security posture is self-declared immature.** SECURITY.md enumerates: no rate limiting, **plaintext API-key storage**, limited command filtering, no audit trail; `exec` runs shell ("never run as root"); bubblewrap sandbox is **Linux-only**. A maintainer's own guidance: *"DO NOT OPEN TO INTERNET, USE TAILSCALE."* An independent review cites a Feb-2026 audit that found (reportedly patched) gaps in execution paths and credential handling, and concludes it suits personal/self-hosted use, not customer-facing SaaS without hardening. ([SECURITY.md](https://raw.githubusercontent.com/HKUDS/nanobot/main/SECURITY.md), [andrew.ooo review](https://andrew.ooo/posts/nanobot-hkuds-ultra-lightweight-personal-ai-agent/))
- **Engineering gaps:** no type-checking gate; weaker-than-documented CI lint; large monolithic core files.
- **Bus factor:** contributions concentrated in the lead maintainer despite the headline contributor count.
- **License is permissive (low friction).** MIT allows dependency, porting, or concept-inheritance; the only obligation is retaining the MIT notice on copied "substantial portions." The bundled WebUI additionally carries Tabler Icons (MIT) and KaTeX (MIT / SIL OFL) notices. No copyleft. ([LICENSE](https://raw.githubusercontent.com/HKUDS/nanobot/main/LICENSE), [THIRD_PARTY_NOTICES.md](https://raw.githubusercontent.com/HKUDS/nanobot/main/THIRD_PARTY_NOTICES.md))

## 7. Adoption options (neutral — no option is recommended)

| Mode | Rough effort | Risks | Potential upside |
|---|---|---|---|
| **(a) Runtime dependency** (import/run `nanobot-ai`) | Low to integrate | Tight coupling to a pre-1.0 API; large transitive footprint; inherits nanobot's security caveats wholesale; upstream merge backlog | Immediate broad feature set without building it; fast upstream provider/model support; MIT = no license friction |
| **(b) Port specific components** (e.g., the WebUI, or a channel) | Medium–high, per component | Forked code stops receiving upstream fixes/security patches; must carry MIT + Tabler/KaTeX notices; integration glue to `oh`'s interfaces | A polished, already-shipped capability (esp. the WebUI) without a ground-up build; full control to adapt; same-lab provenance eases it |
| **(c) Inherit concepts, re-implement** | High (full build) | Slowest to value; risk of re-deriving the same issues | Zero coupling/divergence, no license obligation; keeps `oh`'s core clean; cherry-pick only what `oh` needs on its own terms |

## 8. Bottom line (neutral synthesis) & open questions

**Synthesis (not a verdict):** Because `oh`'s core was derived from nanobot and has since advanced further on production surfaces, the **overlap is high** and the **clear net-new value concentrates in a few areas** — chiefly the **browser WebUI**, then the **four extra channels**, the **CLI-Anything** wrapper, and **pairing-code access control**. The principal constraints are nanobot's **pre-1.0 volatility** and **self-declared-immature security**. The decision therefore turns less on "is nanobot good" (it is active and capable) and more on **which specific gap `oh` wants to close and via which mode**.

**Open questions for the decision:**
1. Does `oh` actually want a **browser WebUI**? If yes, it dominates the value case and the choice narrows to *port the WebUI* (b) vs *build `oh`'s own* (c).
2. Are **Teams / WeChat / WeCom / Signal** channels on `oh`'s roadmap? If yes, porting individual `BaseChannel`s is low-risk and high-fit.
3. What is `oh`'s tolerance for **coupling to a pre-1.0 dependency** and for **nanobot's security posture** in `oh`'s threat model?
4. For any port: confirm the WebUI's WebSocket/config contract maps onto `oh`'s runtime, and budget the attribution/notice retention.
5. Side-by-side needed to confirm the "possibly additive" items (§5.5) are genuine gaps, not parity.

## 9. Sources

GitHub: [repo](https://github.com/HKUDS/nanobot) · [API metadata](https://api.github.com/repos/HKUDS/nanobot) · [releases](https://github.com/HKUDS/nanobot/releases) · [v0.2.1](https://github.com/HKUDS/nanobot/releases/tag/v0.2.1) · [LICENSE](https://raw.githubusercontent.com/HKUDS/nanobot/main/LICENSE) · [THIRD_PARTY_NOTICES](https://raw.githubusercontent.com/HKUDS/nanobot/main/THIRD_PARTY_NOTICES.md) · [SECURITY.md](https://raw.githubusercontent.com/HKUDS/nanobot/main/SECURITY.md) · [docs/architecture.md](https://raw.githubusercontent.com/HKUDS/nanobot/main/docs/architecture.md) · [docs/channel-plugin-guide.md](https://raw.githubusercontent.com/HKUDS/nanobot/main/docs/channel-plugin-guide.md) · [issue #2927 (skills)](https://github.com/HKUDS/nanobot/issues/2927). DeepWiki: [WebUI](https://deepwiki.com/HKUDS/nanobot/14-webui) · [Cron](https://deepwiki.com/HKUDS/nanobot/10.1-cron-service) · [channels](https://deepwiki.com/HKUDS/nanobot/5.5-other-channels). Independent: [andrew.ooo review](https://andrew.ooo/posts/nanobot-hkuds-ultra-lightweight-personal-ai-agent/) · [glama.ai (obotai disambiguation)](https://glama.ai/blog/2025-09-23-nanobot-by-obotai-architecting-real-mcp-agents-with-mcp-ui). Internal: `oh` capability set per `docs/REFERENCE.md` and `CHANGELOG.md`.
