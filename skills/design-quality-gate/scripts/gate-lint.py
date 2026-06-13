#!/usr/bin/env python3
"""
gate-lint.py — mechanical checks for the Design Quality Gate.

Optional accelerator: it handles the deterministic, pattern-matching checks so
human and agent reviewers spend judgment on the checks that actually need it.
The gate is fully valid without it. Python standard library only — no install,
no dependencies. It reports findings; it never edits the document.

Usage:
    python3 gate-lint.py path/to/design.md [more.md ...]

Exit code 0 = clean, 1 = findings, 2 = usage error.

Each check maps to a reviewing.md pass:
  - bare [N/A]                  -> Pass H / I
  - force-restore in code block -> Pass D (P1 if it can overwrite work)
  - closure verb w/o trace table-> Pass A
  - revision/schema header      -> Pass I (revision/schema consistency)
  - duplication-drift terms     -> Pass F (reported for human confirmation)
"""

import re
import sys

CLOSURE_VERBS = re.compile(
    r"\b(closes|closed|covers|covered|implements|implemented|"
    r"addresses|addressed|resolves|resolved|fully validates)\b",
    re.IGNORECASE,
)

# Broad force-restore patterns that can silently overwrite uncommitted work.
FORCE_RESTORE = [
    re.compile(r"git\s+checkout\s+--\s+\."),
    re.compile(r"git\s+checkout\s+\.(?:\s|$)"),
    re.compile(r"git\s+reset\s+--hard"),
    re.compile(r"git\s+clean\s+-[a-z]*f"),
    re.compile(r"rm\s+-rf?\s+"),
]

DRIFT_TERMS = [
    "atomic", "supersede", "skip-logic", "writer-authority", "halt",
    "commit", "queue", "dispatch", "re-arm", "hot-reload", "bootstrap",
    "schema", "revision",
]


def find_code_block_lines(lines):
    """Return the set of 0-based line indices that sit inside ``` fenced blocks."""
    inside, fenced = set(), False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            inside.add(i)
    return inside


def check_bare_na(lines):
    out = []
    for i, line in enumerate(lines):
        # [N/A] is fine only when followed by a rationale: [N/A — ...] or [N/A - ...] or [N/A: ...]
        for m in re.finditer(r"\[N/?A\b([^\]]*)\]", line, re.IGNORECASE):
            tail = m.group(1).strip(" \t")
            if not re.match(r"^[—:-]\s*\S", tail):
                out.append((i + 1, "bare [N/A] without rationale", line.strip()))
    return out


def check_force_restore(lines, code_lines):
    out = []
    for i in code_lines:
        for pat in FORCE_RESTORE:
            if pat.search(lines[i]):
                out.append((i + 1, "force-restore in copyable block (can overwrite uncommitted work)", lines[i].strip()))
                break
    return out


def has_trace_table(text):
    # A traceability table has a header row mentioning the source requirement and proof type.
    lo = text.lower()
    return ("source requirement" in lo and "proof" in lo) or "traceability" in lo


def check_closure_claims(text):
    if CLOSURE_VERBS.search(text) and not has_trace_table(text):
        verbs = sorted({m.group(0).lower() for m in CLOSURE_VERBS.finditer(text)})
        return [(0, "closure claim(s) present but no source-requirement traceability table found",
                 "verbs: " + ", ".join(verbs))]
    return []


def check_revision_schema(lines):
    """Best-effort: if a header 'Revision: X' and a revision-history exist, they should agree.
    Skips silently when the document doesn't use this convention."""
    out = []
    ver = r"v?([0-9]+(?:\.[0-9]+)*)"  # matches 3, v3, 1.2 — same shape for header and history
    header_rev = None
    for line in lines[:40]:
        m = re.search(r"\brevision[:\s]+" + ver, line, re.IGNORECASE)
        if m:
            header_rev = m.group(1)
            break
    if not header_rev:
        return out  # convention not used; nothing to check
    # collect all revision-looking tokens after a 'revision history' heading
    hist_revs = []
    in_hist = False
    for line in lines:
        if re.search(r"revision\s+history", line, re.IGNORECASE):
            in_hist = True
            continue
        if in_hist:
            if re.match(r"^#{1,6}\s", line):  # next heading ends the section
                break
            for m in re.finditer(r"\b" + ver + r"\b", line):
                hist_revs.append(m.group(1))
    if hist_revs and header_rev not in hist_revs:
        out.append((0, "header revision not found in revision history",
                    f"header={header_rev}; history={', '.join(hist_revs)}"))
    return out


def check_drift_terms(lines, code_lines):
    """Report terms used many times across prose so a human can confirm they agree.
    Informational — the script cannot judge semantic agreement."""
    counts = {}
    for i, line in enumerate(lines):
        if i in code_lines:
            continue
        low = line.lower()
        for term in DRIFT_TERMS:
            n = low.count(term)
            if n:
                counts[term] = counts.get(term, 0) + n
    # only surface terms used enough to risk drift
    return sorted([(t, c) for t, c in counts.items() if c >= 4], key=lambda x: -x[1])


def lint(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print(f"  ! could not read {path}: {e}")
        return 1
    lines = text.splitlines()
    code_lines = find_code_block_lines(lines)

    p1, p2, info = [], [], []
    p2 += check_bare_na(lines)
    p1 += check_force_restore(lines, code_lines)
    p2 += check_closure_claims(text)
    p2 += check_revision_schema(lines)
    drift = check_drift_terms(lines, code_lines)

    print(f"\n=== {path} ===")
    if not p1 and not p2:
        print("  clean — no mechanical findings.")
    for ln, msg, ctx in p1:
        loc = f"line {ln}" if ln else "document"
        print(f"  P1  [{loc}] {msg}\n        > {ctx}")
    for ln, msg, ctx in p2:
        loc = f"line {ln}" if ln else "document"
        print(f"  P2  [{loc}] {msg}\n        > {ctx}")
    if drift:
        terms = ", ".join(f"{t}×{c}" for t, c in drift)
        print(f"  info  duplication-drift terms to confirm agree across surfaces: {terms}")

    return 1 if (p1 or p2) else 0


def main(argv):
    paths = argv[1:]
    if not paths:
        print(__doc__.strip())
        return 2
    rc = 0
    for p in paths:
        rc |= lint(p)
    print()
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
