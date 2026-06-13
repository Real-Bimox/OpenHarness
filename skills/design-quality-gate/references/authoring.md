# Authoring reference

Load this when drafting a T1/T2 design and you need the full template for a check. The everyday discipline is in `../SKILL.md`; this file holds the detailed shapes the checks point to.

## Contents

- [Drafting order](#drafting-order)
- [Touched-surface table](#touched-surface-table)
- [Writer authority table](#writer-authority-table)
- [Read/write fallback](#readwrite-fallback)
- [State machine](#state-machine)
- [Partial-failure matrix](#partial-failure-matrix)
- [Migration contract](#migration-contract)
- [Source-requirement traceability](#source-requirement-traceability)
- [N/A patterns](#na-patterns)

## Drafting order

1. Draft the body: problem, proposal, mechanics, test plan, open questions.
2. **Triage** (SKILL.md Step 1): list touched surfaces and classify the change — this fixes the tier.
3. Populate the gate section (`../assets/quality-gate-template.md`):
   - N.1: a row per named surface, including conditional surfaces.
   - N.2: each invariant `[x]` with a pointer, or `[N/A — rationale]`.
   - N.3: each checklist item `[x]` with a pointer, or `[N/A — rationale]`.
4. If the design closes prior findings, add the [traceability table](#source-requirement-traceability).
5. Classify every proof as `structural` / `behavioral` / `integration` / `manual` / `CI-enforcement`.
6. Self-review for contradiction across summary, mechanics, surfaces, tests, CI, open questions, and the gate.
7. Verify copyable implementation blocks contain only final, correct text.
8. Run `python3 ../scripts/gate-lint.py <doc>` to catch the mechanical issues.
9. Leave N.4 empty (one placeholder row) and N.5 empty. Request review.

## Touched-surface table

One row per file/section the design reads or writes. Conditional surfaces get a row with their trigger condition.

| Surface | Read/Write | Behavior changed | Migration? | Tests | Trigger condition (if conditional) |
|---|---|---|---|---|---|
| Protocol §7 | read | new field semantics | no | structural + behavioral | — |
| `state.json` | write | adds `revision` key | yes | behavioral | — |
| Workflow Y | — | adds CI job | no | CI-enforcement | only if dep X merges first |

## Writer authority table

Every writable surface the design touches needs an explicit owner and boundary. Multiple writers without a stated conflict-resolution rule is a P1.

| Surface | Owner | Authorized writers | Allowed ops | Forbidden ops | Conflict behavior | Halt condition |
|---|---|---|---|---|---|---|
| `queue/inbox` | orchestrator | orchestrator only | append | rewrite, delete | reject second writer | halt on out-of-order seq |

`none` is a valid owner for surfaces that are read-only for all agents.

## Read/write fallback

Read fallback and write fallback are different contracts. For every relocated or dual-located artifact specify all of:

- Old read location / new read location
- Old write location / new write location
- Behavior when the new field exists but the file is missing
- Behavior when the new field is absent
- Behavior when both old and new locations contain content (the conflict case — usually the dangerous one)

## State machine

For every queue / migration / lifecycle / handoff, enumerate states and transitions explicitly. No implicit "obvious" transitions.

```
not-started ──> in-progress ──> committed
                   │                 │
                   ├──> partial-failure ──> recovered
                   └──> halted (terminal)
```

Name every terminal state and every halt branch. If the gate's "state machine included" box is `[x]`, the document must contain this enumeration — a sentence in the mechanics does not count.

## Partial-failure matrix

For every multi-step operation, one row per step:

| Step | What was written | What is committed | If next step fails… | Owner recovery action | Automatic retry allowed? |
|---|---|---|---|---|---|
| 1. stage files | working tree | nothing | safe; nothing persisted | none | yes |
| 2. commit | commit object | the commit | partial: commit exists, push pending | `git push` or reset to prior | yes (idempotent) |
| 3. push | remote ref | remote ref | local ahead of remote | re-push | yes |

## Migration contract

Every migration defines all of: source shape · target shape · trigger · idempotency (re-running is a no-op) · conflict detection · partial-state detection · rollback/repair path · commit count · audit entry · tests. A migration that cannot be safely re-run is incomplete.

## Source-requirement traceability

When a design claims to close or cover prior findings, inherited requirements, audit items, or previous test-plan items, include this table. The proof type must match the requirement type (a behavioral requirement is not closed by a structural assertion).

| Source requirement | Required behavior | Proposed proof | Proof type | Coverage | Residual risk / owner decision |
|---|---|---|---|---|---|
| Audit #12 | migration is idempotent | re-run test asserts no second commit | behavioral | full | — |
| RFC-4 §2 | header matches schema | lint check on header | structural | partial | behavioral test deferred to follow-up |

Coverage vocabulary: `full` / `partial` / `deferred` / `accepted-risk-owner`.

## N/A patterns

`[N/A — <rationale>]` is correct when a criterion genuinely does not apply. A bare `[N/A]` does not satisfy the gate. If a body section satisfies the criterion, mark `[x]` and point to it instead.

| Criterion | N/A is appropriate when… |
|---|---|
| Read/write fallback | No relocated artifact or alternate data source. |
| State machine | No multi-step lifecycle introduced; existing states unchanged. |
| Migration contract | No data migration; no schema bump. |
| Bootstrap triggers | Design does not change boot/detection logic. |
| CI classification | Design touches no tests, workflows, merge gates, or validation automation. |
| Claim-to-evidence table | Design claims to close no prior findings or inherited requirements. |
| One parser per queue shape | Design introduces no new queue or entry shape. |
| Explicit writer allowlist | Design introduces no new writer and changes no existing writer's target. |
| Partial-failure per handoff | Design introduces no multi-step handoff. |
| Durable-artifact lifecycle | Design introduces no durable artifact. |

A change to an *existing* writer's allowed target is a write-contract change — mark it `[x]`, never `[N/A]`.
