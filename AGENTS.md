# OpenHarness — project guidelines (agent host project memory)

> Standing rules for any AI agent working in this repository.
> Owner: Bahram Boutorabi. These rules override any conflicting default behavior.

## 1. Repository authority

This repository (`Real-Bimox/OpenHarness`) is an **independent fork** of `HKUDS/OpenHarness` and is the sole authority for this project going forward.

- **This is a private repository — keep it private.** Treat the repository and its entire contents (code, docs, history, issues) as confidential: never publish, mirror, paste, or otherwise expose them outside this repo, and never change its GitHub visibility to public. Changing visibility is an owner-only decision.
- **Push only to the owner's own repository.** All pushes, branches, tags, and PRs target `origin` (`Real-Bimox/OpenHarness`) — the owner's repo — and nowhere else.
- **Never push to a repository the owner does not own — ever.** `upstream` (`HKUDS/OpenHarness`) and any other third-party or external remote are **fetch-only**: no pushes, no PRs, no issues filed on the project's behalf. Judge by *who owns the repository*, not the remote's name — if a remote points anywhere other than the owner's own repo, it is push-forbidden.
- Merging or cherry-picking upstream changes into this fork is an owner decision, never a default.

## 2. No third-party attribution — ever

Do **not** add any of the following to commits, tags, PR descriptions, PR titles, branch names, file content, comments, or any other repo artifact:

- `Co-Authored-By: Claude` (or any model name)
- `Co-Developed by ...`
- `Generated with [Claude Code]` (or any tooling attribution)
- 🤖 or any equivalent emoji/icon flag
- "Created by Claude", "Authored by Claude", "Drafted with AI", and similar phrasings

This is a hard owner rule and the foremost standing instruction for any AI agent session in this repository.

When committing on behalf of the owner, the commit message body ends at the last sentence of substantive content — no trailers, no footers, no signatures, no machine-generated attribution of any kind.

This document may **reference** these forbidden strings inline when stating the rule itself. Quoting the rule is not a violation. Emitting the string as actual attribution is.

## 3. Runtime baseline

OpenHarness is a Python application with two TypeScript frontend packages. Its **established runtime baseline** — the only toolchains the project may depend on — is:

- **Python ≥ 3.10** with the dependencies declared in `pyproject.toml` (core application, `src/openharness`)
- **Node.js / npm** with the dependencies declared in `frontend/terminal/package.json` and `autopilot-dashboard/package.json` (frontend packages)
- **`bash` and `git`** — development and CI plumbing

**Do not introduce any new runtime dependency, toolchain, or external service without explicit owner approval.** This includes — but is not limited to:

- New top-level Python or npm packages
- New language runtimes (Ruby, Java, Go, compiled binaries)
- Docker images or containers as operational requirements
- External services (HTTP APIs, databases, message queues)
- CI runners that require toolchains beyond the baseline above

When a dependency addition is approved:

- **Declare it in the canonical manifest** (`pyproject.toml` or the relevant `package.json`) — never an out-of-band install.
- **Justify it.** State in the proposing commit or PR why the project operates better with the dependency than without, and why nothing in the existing baseline covers the need.

Inert files — Markdown docs, plain-text references, YAML/TOML configuration, fixtures — are not runtime dependencies and are permitted when their purpose is identified and they impose no new executable requirement.

## 4. Branch archive policy

Substantial changes are developed as **proposals**: a Markdown design document under `docs/proposals/<name>.md` with a status block (`DRAFT` / `APPROVED` / `IMPLEMENTED` / `WITHDRAWN`), implemented on a branch named `proposal/<name>`.

Remote proposal branches are managed by status:

- **IMPLEMENTED proposals** — implementation branch is renamed from `proposal/<name>` to `archive/proposal/<name>` once the proposal merges to `main` (or once it is otherwise marked IMPLEMENTED on `main` for non-PR landings). This signals the work is complete and the branch is preserved for historical reference only.
- **DRAFT / APPROVED / under-review proposals** — branch keeps its `proposal/<name>` name. It is still live working state.
- **WITHDRAWN proposals** — branch keeps its `proposal/<name>` name (not archived). The withdrawal is recorded in the proposal file's status block on `main`; keeping the branch live means the design can be revisited if circumstances change.

Mechanically, archiving a branch is `git push origin <sha>:refs/heads/archive/proposal/<name>` followed by `git push origin --delete proposal/<name>`. Use the SHA-then-delete order; never delete first.

Local branches are never auto-archived or deleted. Owner manages local branch hygiene at their own discretion.

## 5. Operating memory — the repository is the record

**Machine-local agent memory** (for example a harness's per-user state directory) does not travel between machines and must never be relied upon. This git repository, kept in sync with `origin`, is the only durable memory.

- **Commit-and-push or it didn't happen.** Anything that matters — a decision, a finding, a question parked for later — must be captured in a committed file and pushed to `origin`. Content living only in a chat transcript or an unpushed local copy is not part of the record.
- **Persist durable findings before seeking approval.** When you produce findings, analysis, a recommendation, or a proposed decision of record, first capture it in the appropriate committed document (`docs/proposals/` for a design in flight, `docs/reports/` for an analysis, the relevant status doc otherwise), commit and push it, and only then walk the owner through it. Do not ask the owner to approve a durable decision that exists only in chat.
- **Live-answer exception.** Simple factual questions, status checks, and tactical advice are answered directly first; afterward, offer to preserve the exchange (`Document this? Y/N`) and persist it only if the owner agrees.
- **Sync at session start, and before touching shared state.** Begin every session by syncing (`git pull`) so local `main` matches `origin/main`; reconcile any divergence before starting work. Re-fetch and re-read the current state from `origin` before editing a shared file or integrating work — never act on a stale local copy.

## 6. Delegation — subagents and external agents

**Adherence to these rules is not delegable.** Whenever you dispatch a subagent, spawn a background or external agent, or author a brief for any agent that will work on or assess this repository, you **must** instruct it to read and follow this `AGENTS.md` as its binding operating rules — not merely as background context. At minimum bind it to **§2** (no third-party attribution) and **§12** (halt-to-human conditions), and default it to **read-only**: no commits, pushes, branches, or file changes unless the owner has explicitly authorized them.

## 7. Engineering tenets and the working loop

Standing tenets every change must respect:

- **Quality is invariant; throughput is variable.** Never weaken or skip a verification to go faster.
- **Trust only what can be observed or computed.** What an agent reports is a hint; ground truth is what a check, test, or build independently confirms. Evidence before assertions, always.
- **Author ≠ approver.** An agent does not approve or merge its own work; changes are independently reviewed (by the owner or a separate reviewer) before they land on `main`.

The working loop:

- **Substantial changes start as a proposal.** Brainstorm first, then a design document under `docs/proposals/` (see §4 for the proposal/branch lifecycle), then implementation. Each shipped behaviour change traces back to a proposal.
- **Design-quality-gate review (proposals & specs).** Draft and review every proposal/spec with the [Design Quality Gate](skills/design-quality-gate/SKILL.md) skill — triage to its tier, embed the gate section (copy [`skills/design-quality-gate/assets/quality-gate-template.md`](skills/design-quality-gate/assets/quality-gate-template.md)), and clear it (no open **P1**; no unaccepted **P2**) before a proposal moves DRAFT → APPROVED (§4). The optional, dependency-free [`gate-lint.py`](skills/design-quality-gate/scripts/gate-lint.py) clears the mechanical checks. Skill catalogue: [`skills/README.md`](skills/README.md).
- **Test-driven and verification-before-completion.** Run the project's real checks — its test suites, linters, and builds — and show the evidence before claiming anything works, is fixed, or is done.
- **Keep documentation current.** Update affected docs, status, and version references as part of the change, not as a follow-up.
- **Keep changes small, focused, and traceable.**

## 8. Delivery drive

**Carry the work as close as possible to a delivery-ready, fully tested solution before handing back.** Take the initiative to drive a task through to completion rather than stopping at the first open question. In practice: refine requirements iteratively as understanding improves; use parallel worktree lanes where they speed independent work (§9); integrate through review, never by self-approval (§7, §9); keep documentation, status, and version references current as you go (§7); and commit and push at each meaningful step so progress is durable (§5, §10).

**Stop only for a true owner-gated blocker** — one of the halt-to-human conditions in §12 (secrets, security, legal/attribution, scope expansion, novel or irreversible decisions, no-ground-truth judgment, destructive operations) or a release/tag decision (§13). Exhaust autonomous recovery first; do not pause for choices you are competent to make and that only tighten a safety guarantee (decision altitude, §12).

## 9. Concurrency and writer authority

- **`main` is the integration target and source of truth**, always kept in sync with `origin`. A single agent working alone may commit to `main` directly; whenever more than one agent works concurrently, each works on its own lane branch and a single integrator merges into `main`.
- **One writer per shared file.** Shared, owner-controlled files (this `AGENTS.md`, design-of-record docs, shared status files) have a single writing lane at a time; a file is never edited by two lanes at once.
- **Concurrent agents own a branch *and* a worktree.** A branch isolates history; a separate `git worktree` (or clone) isolates the checkout and index. Both are required for concurrent work.

## 10. Sync and safety

Complements the archive mechanics in §4.

- **Push completed work promptly** to its remote — your lane branch on `origin` (concurrent work) or `main` (working alone). Nothing of value stays local-only; only what is pushed to `origin` survives a fresh clone.
- **The most destructive permitted action on an existing file is archiving it, and only after confirming with the owner.** No force-push, no history rewrite, and no deletion of existing files or branches without explicit owner confirmation.
- **`origin` is the source of truth, always.** If any circumstance contradicts these rules, ask the owner before proceeding.

## 11. End-of-day checkpoint

At the end of a working day — or when winding down a working session — close out cleanly. Beyond the ongoing commit-and-push discipline (§5, §8, §10), the wind-down must:

- **Record the day's work.** Write a brief end-of-day note — what was done, the decisions taken, and what remains open — as a session/handoff note under `docs/reports/` (following the existing `session-handoff-*.md` convention).
- **Update the forward view.** Refresh `TODO.md` with the current open items and the next scheduled tasks, and update the roadmap and any status doc the project maintains.
- **Commit and push.** Commit the closure artifacts locally and push them to `origin` — the owner's GitHub remote (`origin` only, per §1). If the repository has no remote, the local commit stands, but flag the missing remote.
- **Verify full sync.** End with the working tree clean and local in sync with `origin` — nothing uncommitted, nothing unpushed — and every artifact (the end-of-day note, `TODO.md`, roadmap, status, version references) mutually consistent. `origin` is the source of truth (§10).

This is a checkpoint, not a release: tagging or publishing a version remains owner-gated (§13).

## 12. Halt-to-human conditions

Stop and ask the owner — never guess — before:

- Handling secrets or credentials, or pushing to a protected branch, without explicit authorization.
- Acting on security findings, or making any license / legal / attribution decision.
- Expanding scope beyond the agreed mandate or brief.
- Making a novel or irreversible architecture decision, or any decision with **no ground truth** ("is this design sound?") — escalate rather than guess.
- Any destructive operation (force-push, history rewrite, branch or file deletion).

**Decision altitude — don't over-escalate.** Escalate the decisions that are genuinely the owner's: risk acceptance, business or legal calls, irreversible or novel architecture, no-ground-truth judgment. Do not make the owner adjudicate implementation detail you are competent to decide and that only *tightens* a safety guarantee — decide it, own the outcome, and surface it for review.

**Phrase owner questions in plain terms.** The owner decides on the basis of product features and their functional or user impact. Frame every question and decision as that plain-language trade-off; translate any underlying technical choice into it rather than asking the owner to adjudicate jargon.

## 13. Release policy

A version is not released until **both** are true:

- a **GitHub Release** exists for the tag (not merely a git tag), with release notes; and
- **all relevant documentation is in sync** — the `README.md` version reference, `CHANGELOG.md`, the per-version release notes, and any other version references.

Tag and release decisions are owner-only (§12).

## 14. Context-usage reporting

Once a session's context reaches **60% utilisation**, every subsequent message to the owner must end with a final line stating total context usage:

- Format (last line of the message): `Context: NN% used`
- Applies from the moment 60% is reached, for every message thereafter in that session.
- **Source.** If your agent harness exposes session token usage, read the latest usage and divide by the context window. If no such source is available, say so and give a best estimate (e.g. `Context: ~65% used (estimate)`) rather than omit the line.

## 15. Owner-facing progress reporting

Progress reports to the owner must be feature/functionality focused, not broad
subsystem or file/checker focused. Name the concrete capability or operating
function, for example `Web UI - Agent report output`, `Web UI - Owner decision
panel`, `Advisory router - Agent recommendation`, or `Active routing -
Controlled pilot envelope`.

Each row should include:

- `Feature / function`
- `Global progress`: `done/total - NN%` when the task set is known; otherwise
  `unknown` plus the missing evidence.
- `Lifecycle status`: one of `Planned`, `Proposal drafted`,
  `Requires approval`, `Approved`, `Spec done`, `Dev started`, `In review`,
  `Implemented`, `Verified`, `Shipped`, or `Parked`.
- `Owner task`: one of `No action`, `Requires approval`, `Approved`,
  `Deferred`, or `Blocked`.
- `Owner meaning`: one short plain-language sentence explaining why it matters.

Avoid command-by-command, file-by-file, or checker-by-checker progress reports
unless a failure changes the owner's decision. Routine closeouts should report
feature impact, verification summary, commit/SHA, pushed branch, and what
remains.
