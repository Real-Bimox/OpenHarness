---
name: design-quality-gate
description: Pre-implementation design review discipline for proposals, specs, and RFCs. Use this skill whenever drafting or reviewing any design that changes protocol or contract behavior, schema, queues, handoffs, writer authority, migrations, tests, CI gates, agent behavior, or governance — even when the change seems small or nobody calls it a "design review". It scales rigor to risk with a fast triage step, then catches cross-document drift, missing failure modes, overclaimed coverage, and incomplete contracts before implementation begins.
---

# Design Quality Gate v3

A pre-implementation review discipline for design changes that touch shared contracts: protocol text, boot prompts, schemas, queues, handoffs, test plans, CI, documentation, or agent behavior.

Its job is to catch cross-document drift, missing failure modes, overclaimed test coverage, and incomplete contracts **before** implementation begins — when fixing them costs a sentence instead of a migration.

## What changed in v3, and why

v3 keeps every check from v2 but reorganizes around one idea: **most changes are not risky, and forcing them through a heavyweight gate is how a quality discipline turns into theatre that people route around.** So v3:

- **Triages first.** A 30-second classification routes each change to a tier. A local additive change answers four questions; a breaking schema migration gets the full treatment. Rigor follows risk instead of being uniform.
- **States each check once.** v2 described the same concern twice — once as an author "criterion" and again as a reviewer "pass". v3 has **one canonical list of checks**; authoring and reviewing are two lenses on the same check. (v2 violated its own Criterion 1 here.)
- **Loads progressively.** This file is the whole discipline for everyday use. The exhaustive reviewer procedure, recovery tables, and write protocol live in `references/` and load only when you need them.
- **Mechanizes the boring parts.** The checks that are pure pattern-matching (bare `[N/A]`, revision/schema header agreement, banned force-restore text, closure-claims-without-a-trace-table, duplication drift) are handled by an optional `scripts/gate-lint.py`, freeing human and agent judgment for the checks that actually need it.

## When to load

- Authoring any design proposal, spec, or RFC that changes contract behavior, boot logic, schema, handoff contracts, CI, test strategy, or agent roles.
- Reviewing a draft before it is approved for implementation.
- Auditing a previously-approved design during post-merge verification.

Read end-to-end the first time; keep it open during the conversation or agent run.

## Core principle: single canonical definition

Every rule has exactly one canonical home. Other sections or documents may summarize it, but must point back — e.g. "mirrors §3.2". Never restate a complex rule in two places as if each were authoritative: duplicate canonical-looking text is the single largest source of drift, because the copies fall out of sync the moment one is edited.

---

## Step 1 — Triage: how much gate does this change need?

Triage is not overhead you do *before* the real work — it **is** the first two checks. You cannot classify a change without listing what it touches and naming what kind of change it is, and those two facts are exactly what tells you how much more to do.

**The pivot question:** does this change touch a **contract surface** — anything another component reads, writes, parses, or depends on? Contract surfaces include: protocol/schema fields, queue entry shapes, handoff payloads, writer authority (who may write where), migrations, persisted state, test contracts, CI merge gates, and boot/detection logic.

| Tier | Qualifies when… | Apply |
|---|---|---|
| **T0 — Local** | Adds new, isolated behavior. Changes **no** existing contract, schema, writer target, persisted-state shape, test/CI gate, or migration. (Most doc edits, new optional helpers, pure additions.) | The **4 universal checks** below. |
| **T1 — Single contract** | Touches **exactly one** contract surface. No breaking change, no migration, no claim to close prior findings. | Universal checks **+ the T1 contract checks**. |
| **T2 — Breaking / stateful / multi-surface** | **Any** of: a breaking change; a schema bump; a migration; a multi-step queue/handoff/lifecycle; a change to *where a component writes or what it parses*; a claim to close prior findings; or dependence on proposal merge order. | **All checks** + the full reviewer procedure in `references/reviewing.md`. |

**Escalation rule (the one place to be conservative):** if a change *could* alter where a component writes, what it parses, or how it interprets existing state, treat it as **breaking until proven otherwise** → T2. When genuinely unsure between two tiers, pick the higher one. Leanness is the default for clearly-local work, not a way to wish risk away.

---

## Step 2 — The checks

Each check below names the **failure it prevents** and what you **produce** to satisfy it. Heavy checks point to `references/authoring.md` for their full template (matrices, field lists, question sets). A reviewer reads the same list as "verify this holds"; severities live in `references/reviewing.md`.

### Universal checks (every tier, T0 included)

- **Touched surfaces** — *prevents: silently editing something you didn't account for.* List every surface the change affects (file / section / read-or-write / behavior changed / migration? / tests?). Include conditional surfaces: if merge order, flags, optional deps, or owner choices change which files get edited, list each with its trigger condition.
- **Change classification** — *prevents: a breaking change disguised as additive.* Label the change: additive / breaking / schema-affecting / write-contract-affecting / migration-affecting / test-affecting / CI-affecting / governance-affecting. Apply the escalation rule.
- **Canonical source** — *prevents: drift between copies of a rule.* Every rule has one home; every restatement says "mirrors §X".
- **Copyable prescription hygiene** — *prevents: a known-wrong snippet becoming the canonical record because someone pasted it.* Any block meant to be copied into implementation must be final and correct. Don't ship wrong text with a "fix this later" note; fix the text. Notes may explain rejected alternatives or prior mistakes — the copyable block itself must be final.
- **Claim-to-evidence** *(only if the design claims to close/cover/resolve something; else N/A)* — *prevents: declaring a behavioral requirement "done" on the strength of a text-existence check.* Every "closes / covers / implements / addresses / resolves / fully validates" claim traces to its source requirement, and the proof **matches the kind** of requirement: behavioral requirement → behavioral proof; structural requirement → structural proof may suffice. State coverage honestly with the partial-closure vocabulary: `fully resolves` · `partially mitigates` · `documents only` · `manual verification only` · `deferred to follow-up` · `accepted-risk-owner`. See `references/authoring.md` for the traceability table.

### T1 contract checks (add when the change touches a contract surface)

- **Writer authority** — *prevents: two writers silently fighting over one target.* For each writable surface touched: owner · authorized writers · allowed ops · forbidden ops · conflict behavior · halt condition. (Full table in `references/authoring.md`.)
- **Read/write fallback** — *prevents: data loss when old and new locations disagree.* For any relocated or dual-located artifact, specify old/new **read** location, old/new **write** location, and behavior when the new field is missing, absent, or in conflict with the old. Read fallback and write fallback are different contracts — specify both.
- **Test every contract, not just every feature** — *prevents: shipping the contract you changed with tests only for the feature you added.* Cover the contracts the change touches (happy path, unmigrated fallback, missing target, dirty tree, partial failure, stale location, duplicate-source conflict, reboot/retry, writer-boundary). Classify each proof: `structural` / `behavioral` / `integration` / `manual` / `CI-enforcement`. A structural assertion does not satisfy a behavioral requirement unless the design explicitly downgrades the requirement and records accepted risk.
- **CI: signal vs enforcement** *(if CI touched)* — *prevents: claiming CI blocks bad merges when the job merely runs.* Classify CI as `signal-only` (runs, merge not mechanically blocked) or `merge-blocking` (branch protection requires it). A merge-blocking claim must include the enforcement setup or an owner criterion for enabling required checks.
- **No summary/mechanics contradiction** — *prevents: a proposal that disagrees with itself.* The summary, mechanics, compatibility table, touched-surface table, test plan, CI plan, open questions, and gate must all agree. "One commit" in the mechanics cannot become "one commit plus a follow-up" in the summary.
- **Open questions don't hide required semantics** — *prevents: shipping with a correctness hole labelled "TBD".* Open questions may ask for preference (a default is fine). They may not defer core correctness ("Is this hot-reload safe?" is not an open question — resolve it before review).

### T2 checks (add for breaking / stateful / multi-surface changes)

- **Hot-reload safety** — *prevents: old and new components writing different sources of truth.* If "hot-reload safe" is claimed, answer: what do already-running components do before re-reading? what do reloaded-but-unmigrated components read? where do they write? can old and new write different truths? what if migration hasn't run? Any "they might diverge" means it is **not** hot-reload safe.
- **State machine** — *prevents: an "obvious" transition nobody actually defined.* Every queue / migration / lifecycle / handoff enumerates states and transitions: not-started → in-progress → committed/submitted → partial-failure → recovered. Name terminal states and halt branches. No implicit transitions.
- **Partial-failure matrix** — *prevents: an interrupted multi-step operation leaving the system in an undefined state.* For each multi-step op, a row per step: what was written · what is committed · what happens if the next step fails · owner recovery action · automatic retry allowed? (Template in `references/authoring.md`.)
- **Dirty-worktree rule** — *prevents: a recovery routine silently destroying uncommitted work.* Any migration or auto-commit defines dirty-tree behavior (require clean tree / require specific paths clean / pathspec-limit staging and rollback). Never use broad force-restore (`git checkout -- .` or any VCS equivalent) that can overwrite uncommitted work.
- **Migration contract** — *prevents: a migration that can't be re-run, rolled back, or audited.* Define: source shape · target shape · trigger · idempotency · conflict detection · partial-state detection · rollback/repair · commit count · audit entry · tests.
- **Bootstrap triggers end-to-end** — *prevents: a new trigger that only fires on the happy boot path.* If a new detection condition is introduced, show the condition, which boot component checks it, when it runs, and whether normal boot / hot reload / reinstall / recovery all trigger it.
- **Revision/schema consistency** *(versioned docs)* — *prevents: a header that lies about the document's own version.* Verify header revision == latest revision-history entry; header schema == current schema declaration; latest revision-history schema annotation matches; the implementation source is cited correctly. (`scripts/gate-lint.py` checks this mechanically.)

---

## Step 3 — Embed the gate section

Every T1/T2 design embeds a quality-gate section so the checks are visible and auditable, not just claimed. Copy `assets/quality-gate-template.md` and fill it in. It has five sub-sections, in order:

- **N.1 Canonical contract surfaces** — one row per touched surface (incl. conditional), marking canonical vs mirror, and `[x]` / `[ ]` / `[N/A — rationale]`.
- **N.2 State / handoff invariants** — five system-level invariants, each `[x]` with a pointer or `[N/A — rationale]`.
- **N.3 Quality checklist** — the per-check boxes, each `[x]` with a pointer or `[N/A — rationale]`.
- **N.4 Review findings** — author leaves empty; reviewers append findings (`P1-001`, severity, location, finding, status, resolution).
- **N.5 Approval criteria** — the closing gate; the owner cannot approve until all applicable boxes are `[x]`.

**N/A convention:** any N.2/N.3 box may be `[N/A — <one-line rationale>]` when the criterion genuinely does not apply to the change's scope. A bare `[N/A]` with no rationale does **not** satisfy the gate. If a body section actually satisfies the criterion, mark `[x]` and point to it — don't mark N/A. (T0 changes may skip the embedded section and instead record the four universal checks inline; the section becomes mandatory at T1+.)

## Step 4 — Lint (optional accelerator)

`scripts/gate-lint.py` mechanically checks the deterministic items so reviewers spend judgment where it matters. It is **optional and dependency-free** (Python stdlib only) — the gate is fully valid without it.

```bash
python3 scripts/gate-lint.py path/to/design.md
```

It flags: bare `[N/A]` without rationale, revision/schema header disagreement, banned force-restore text inside copyable blocks, closure-verbs with no traceability table, and duplication-drift terms used inconsistently. It reports; it never edits.

## Step 5 — Review

For anything above T0, run the review procedure in **`references/reviewing.md`**. It orders the passes **severity-first** (the passes most likely to surface a blocker run first) and supports an **early-stop rule**: once a P1 makes the design unapprovable, stop and report which passes ran. `references/reviewing.md` also holds the recovery scenarios and the reviewer write protocol (how to append findings without races or lost work).

---

## The short rule

Before review, the author should be able to answer this for every behavior the design introduces:

> **Who reads it, who writes it, where is the canonical rule, what happens when it fails halfway, which test proves that — and does that proof exercise the same kind of requirement being claimed?**

If any answer is missing, the design is not ready for review.

## Severity discipline

| Severity | Meaning | Blocks approval? |
|---|---|---|
| **P1** | Correctness-blocking: fundamental gap, contradiction, false closure claim, or behavior-changing omission | Yes, until resolved |
| **P2** | Significant but acceptable with rationale: missing edge case, incomplete test, ambiguous wording, weak enforcement | Yes, unless the owner explicitly accepts |
| **P3** | Informational or stylistic: naming, formatting, minor wording | No |

Don't inflate P3 to P2 to look thorough, and don't downgrade P1 to P2 to unblock approval. Per-check severities and the pass-by-pass procedure are in `references/reviewing.md`.
