<!--
Quality Gate section template. Copy this block into a design document and replace N
with the section number. Fill every box: [x] with a pointer, [ ] if open, or
[N/A — <one-line rationale>]. A bare [N/A] does not satisfy the gate.
T0 (local/additive) changes may skip this section and record the four universal
checks inline instead; the section is mandatory at T1+.
-->

## N. Quality Gate

### N.1 Canonical contract surfaces

| Surface | Canonical? | Mirrors / references | Checked |
|---|---:|---|---|
| <file/section> | yes | <who obeys it> | [ ] |
| <test file> | no, mirrors §X | structural assertion only; behavioral deferred | [ ] |
| <workflow> | partial | CI signal-only unless branch protection enabled | [ ] |

> One row per touched surface, including conditional surfaces (add the trigger condition).

### N.2 State / Handoff Invariants

- [ ] Every queue entry shape has one parser and one dispatch behavior.
- [ ] Every writer has an explicit allowlist.
- [ ] Every multi-step handoff defines partial-failure behavior.
- [ ] Every durable artifact has ownership, collision, recovery, and deletion/supersede semantics.
- [ ] Every repeated rule names its canonical source.

### N.3 Quality Checklist

- [ ] Touched surfaces listed, including conditional surfaces
- [ ] Canonical source for each rule declared
- [ ] Change type classified
- [ ] Copyable implementation prescriptions contain no known-bad text
- [ ] Claim-to-evidence coverage table included for prior findings or inherited requirements
- [ ] Proof type classified for every test or verification item
- [ ] Writer authority table included
- [ ] Read/write fallback defined
- [ ] Tests map to all P1/P2 risks
- [ ] CI classified as signal-only or merge-blocking, if CI is touched
- [ ] Hot-reload safety proven or schema bump declared
- [ ] State machine included
- [ ] Partial failure matrix included
- [ ] Migration contract included, if applicable

### N.4 Review Findings

| ID | Severity | Location | Finding | Status | Resolution / Evidence |
|---|---|---|---|---|---|
| P_-001 | | | | open | |

> Author starts this table empty (one placeholder row). Reviewers append findings; resolution updates the same row. Severity: P1 = correctness-blocking · P2 = significant, owner may accept · P3 = informational.

### N.5 Approval Criteria

- [ ] No open P1 findings.
- [ ] No open P2 findings unless explicitly accepted by owner.
- [ ] Tests or verification cover every P1/P2 class found during review.
- [ ] Open questions resolved or explicitly deferred.
- [ ] All "fully resolves / closes" claims have full source-requirement coverage, or are reworded as partial/deferred/accepted risk.
- [ ] Conditional touched surfaces and merge-order dependencies are resolved or explicitly deferred.
