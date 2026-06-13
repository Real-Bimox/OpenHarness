# Reviewing reference

Load this when reviewing a T1/T2 design. Append findings to the design's `N.4 Review findings` table as you go, following the [write protocol](#reviewer-write-protocol) at the bottom.

## Contents

- [How to run a review](#how-to-run-a-review)
- [The passes (severity-first order)](#the-passes-severity-first-order)
- [Recovery scenarios](#recovery-scenarios)
- [Reviewer write protocol](#reviewer-write-protocol)

## How to run a review

Run the passes **in the order below**, which is sorted by how likely each is to surface a blocker — so the cheapest path to "this can't be approved yet" runs first.

**Early-stop rule:** the moment a P1 finding makes the design unapprovable, you may stop and report. Record which passes completed and which were skipped, so the next reviewer resumes from the right place. Don't grind through ten passes to be polite when pass 1 already found a fatal contradiction.

**Mechanical passes first, optionally automated:** passes marked *(lint)* below are fully checked by `python3 ../scripts/gate-lint.py <doc>`. If you ran the linter, you can treat those passes as done unless the linter flagged something to investigate.

Severity reference: **P1** = correctness-blocking (blocks approval until resolved). **P2** = significant but acceptable with explicit owner rationale. **P3** = informational/stylistic (does not block). Don't inflate P3→P2 to look thorough; don't downgrade P1→P2 to unblock.

## The passes (severity-first order)

### Pass A — Claim coverage
For every "closes / covers / implements / addresses / resolves" claim: locate the source requirement, identify its type (structural / behavioral / integration / manual / enforcement), and verify the proposed proof type matches and coverage is marked honestly.

- Behavioral requirement satisfied only by a structural text check → **P1** if the design claims full closure; **P2** if marked partial but not tracked.

### Pass B — Contract surface
Enumerate every protocol / schema / boot / doc / test / CI / script surface the design claims to affect. Verify each exists or is being created, has a concrete prescription, and appears in N.1 with `Checked = [x]`. Verify conditional surfaces list their trigger condition.

- Behavior change with no concrete prescription → **P1**. Missing surface in N.1 → **P2**.

### Pass C — Ownership
For every writable surface: exactly one named writer (or `none`), allowed ops, forbidden ops, conflict behavior, halt condition. The N.3 "writer authority table" box must point at a real table, not the surfaces table or a sentence in mechanics.

- Multiple writers with no explicit conflict-resolution → **P1**.

### Pass D — Partial failure & recovery
For every multi-step op ask: "if step N succeeds and N+1 fails, what state are we in, what's committed vs lost, who recovers, is auto-retry allowed?" Then check the [recovery scenarios](#recovery-scenarios) relevant to the design.

- Recovery that uses broad force-restore / can overwrite uncommitted work → **P1**. Missing partial-failure handling → **P2**. Relevant recovery scenario uncovered → **P2**.

### Pass E — State machine
For each queue / migration / lifecycle / handoff: are all states enumerated, all transitions explicit, terminal states identified, halt branches named?

- N.3 "state machine included" is `[x]` but the document has no enumeration → **P1**. Missing state, undefined transition, or unnamed halt → **P2**.

### Pass F — Duplication drift *(lint)*
Scan the body for repeated rule terms — e.g. `atomic`, `supersede`, `skip-logic`, `writer-authority`, `halt`, `commit`, `queue`, `dispatch`, `re-arm`, `hot-reload`, `bootstrap`, `CI`, `test`, `coverage`, `schema`, `revision`. For each repeated term, verify all usages agree.

- A wording mismatch between two ostensibly-canonical phrasings → **P1**.

### Pass G — Test alignment
For each P1/P2 risk found in earlier passes, verify the test plan has corresponding proof of the right type: structural asserts the rule introduced; behavioral exercises the contract end-to-end; integration tests cross-surface interaction; manual steps are unambiguous; CI-enforcement proves merges are *blocked*, not merely that jobs run.

- Old test reused with a stale assumption → **P1**. Test missing for a P1/P2 risk → **P2**.

### Pass H — Invariants
For each N.2 invariant, verify the design satisfies it and the box is marked correctly.

- Open `[ ]` → **P1**. Bare `[N/A]` without rationale → **P2** *(lint)*.

### Pass I — Prescription & governance hygiene *(lint)*
Check: no known-wrong text in copyable blocks; conditional surfaces don't contradict "files not touched" claims; open questions don't hide required semantics; CI classified signal-only vs merge-blocking; header/revision/schema claims internally consistent; N/A entries don't contradict body sections that satisfy the criterion.

- Known-wrong copyable text → **P2**, or **P1** if it would create a false canonical record. Conditional-surface contradiction → **P2**. CI merge-blocking overclaim → **P2**.

## Recovery scenarios

Verify the design answers the scenarios relevant to its scope. Each uncovered relevant scenario is **P2**; recovery that violates the dirty-worktree rule is **P1**.

- Reboot mid-workflow
- Crash mid-dialogue
- Dirty worktree
- Partial commit
- Schema mismatch
- Failed CI run
- Failed enforcement setup (if CI is claimed merge-blocking)

## Reviewer write protocol

When writing findings to N.4, follow this to prevent races, scope creep, and lost work:

1. Re-read the design immediately before writing.
2. Pre-flight: verify the design has no uncommitted local edits.
3. Append-only writes to N.4. Verify the append-only scope before staging.
4. Stage only the design document; verify staged scope.
5. Commit with a message identifying the review pass.
6. If staging or commit fails after the append, **halt and report** whether the file is modified, staged, or both. Do not roll back automatically.

**Author-side edits:** the author may update existing N.4 rows' status and resolution while the document is still editable. Once approved, the body and gate are frozen; further findings append as new rows with status `accepted-risk-owner` or `deferred-to-followup`.

| Scenario | Behavior |
|---|---|
| Reviewer session ends mid-pass | Note which passes completed; next reviewer resumes from the next uncompleted pass. |
| Append succeeds but commit fails | Do not roll back. Report file state. Owner decides. |
| Two reviewers edit concurrently | Pre-flight catches the dirty path on the second reviewer. Owner serializes. |
| Author edits between reviewer passes | Pre-flight catches it. Owner decides whether to commit author edits first or fold them into the review. |

## N.5 approval criteria

The owner cannot mark the design approved until all applicable criteria hold:

- No open P1 findings.
- No open P2 findings unless explicitly accepted by the owner.
- Tests or verification cover every P1/P2 class found during review.
- Open questions resolved or explicitly deferred.
- All "fully resolves / closes" claims have full source-requirement coverage, or are reworded as partial/deferred/accepted-risk.
- Conditional touched surfaces and merge-order dependencies resolved or explicitly deferred.
