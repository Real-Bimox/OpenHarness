# Design Quality Gate v3

A project-agnostic pre-implementation design-review discipline for proposals, specs, and RFCs that affect protocols, schemas, handoffs, agents, tests, CI, or governance. Drop this folder into any repository — it has no project-specific coupling and no external dependencies.

Its purpose: catch cross-document drift, missing failure modes, overclaimed coverage, and incomplete contracts **before** implementation, when a fix costs a sentence instead of a migration.

## What v3 changes

v3 keeps every check from v2 but reorganizes for **agility and intelligence at the same time** — rigor that follows risk instead of taxing every change equally:

- **Risk triage (lean by default).** A 30-second classification routes each change to a tier — T0 (local/additive) answers four questions; T2 (breaking/stateful/multi-surface) gets the full treatment. Most changes take the fast path.
- **One canonical list of checks.** v2 stated each concern twice — as an author "criterion" and again as a reviewer "pass" — which violated its own single-canonical-source rule and was a drift hazard. v3 states each check once; authoring and reviewing are two lenses on it.
- **Progressive disclosure.** `SKILL.md` is the whole discipline for everyday use. The exhaustive reviewer procedure, recovery tables, and heavy templates load from `references/` only when needed.
- **Mechanized boring checks.** `scripts/gate-lint.py` (stdlib-only, optional) handles the pure pattern-matching checks — bare `[N/A]`, revision/schema header agreement, banned force-restore text, closure-claims-without-a-trace-table, duplication drift — freeing judgment for the checks that need it.
- **Severity-first review.** The reviewer passes are ordered by how likely each is to surface a blocker, so the early-stop rule fires sooner.

All v2 capabilities are preserved: claim-to-evidence discipline, structural-vs-behavioral proof classification, conditional touched surfaces, copyable-prescription hygiene, CI signal-vs-enforcement, and the revision/schema consistency pattern.

## Files

```text
design-quality-gate/
├── SKILL.md                          # the discipline: triage → checks → gate → lint → review
├── README.md                         # this file — orientation
├── assets/
│   └── quality-gate-template.md      # copyable N.1–N.5 gate section
├── references/
│   ├── authoring.md                  # full templates: surfaces, writer authority, matrices, traceability, N/A
│   └── reviewing.md                  # severity-first passes, recovery scenarios, reviewer write protocol
└── scripts/
    └── gate-lint.py                  # optional, dependency-free mechanical linter
```

## How to use

1. **Triage** the change into a tier (SKILL.md Step 1).
2. **Apply that tier's checks** while drafting (Step 2).
3. **Embed the gate section** from `assets/quality-gate-template.md` (T1+).
4. **Lint** with `python3 scripts/gate-lint.py <doc>` to clear the mechanical checks (optional).
5. **Review** above T0 using `references/reviewing.md`.

For a small change that genuinely touches no contract surface, the triage step lands it at T0 — record the four universal checks inline and move on. For anything touching a shared contract, the gate scales up accordingly.

## Adopting it in a project

This skill is self-contained and generic. A project that wants the gate to be mandatory can say so in its own `AGENTS.md` / contributing guide — e.g. "every numbered proposal and spec MUST embed the quality-gate section and pass it before approval" — and point at `skills/design-quality-gate/SKILL.md`. The skill itself stays project-neutral so the same copy works everywhere.
