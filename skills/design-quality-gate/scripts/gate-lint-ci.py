#!/usr/bin/env python3
"""gate-lint-ci.py — run the Design Quality Gate linter over CHANGED design docs.

CI driver for skills/design-quality-gate/scripts/gate-lint.py (C2). It lints the
design-of-record documents changed vs a base ref — docs/proposals/*.md and
docs/specs/*.md by default — and fails the build if any changed document has a
gate-lint finding. This makes the Design Quality Gate a computed gate
(AGENTS.md §4), not a documentation-only discipline.

Forward-looking by design: only CHANGED documents are linted; legacy/unchanged
documents are never touched, so a pre-existing finding in the committed corpus
does not break CI (the corpus is not all gate-lint-clean today, and fixing it
belongs to its owners). This mirrors the posture of attribution-check.py and
sync-safety.py.

Read-only: reads git metadata and file contents only; writes nothing; never
touches factory-gate.py or the merge path. Python standard library only.

  skills/design-quality-gate/scripts/gate-lint-ci.py [--repo R] [--base ref] [--path-glob G ...]

Exit codes:
  0 - CLEAN     (no changed design docs, or every changed design doc is clean)
  2 - FINDINGS  (one or more changed design docs have gate-lint findings)
  3 - error     (e.g. base ref unresolvable) — fail-closed, never a silent pass
"""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
GATE_LINT = SCRIPT_DIR / "gate-lint.py"

DEFAULT_GLOBS = ["docs/proposals/*.md", "docs/specs/*.md"]


def git(repo: Path, *args: str) -> tuple[int, str, str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def changed_files(repo: Path, base: str) -> tuple[list[str] | None, str]:
    """Files Added/Modified/Renamed on HEAD since its merge-base with base."""
    rc, merge_base, err = git(repo, "merge-base", base, "HEAD")
    if rc != 0:
        return None, err or f"cannot compute merge-base {base} HEAD"
    rc, out, err = git(repo, "diff", "--name-only", "--diff-filter=AMR", f"{merge_base}..HEAD")
    if rc != 0:
        return None, err or f"cannot diff {merge_base}..HEAD"
    return [line for line in out.splitlines() if line], ""


def matches(path: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, glob) for glob in globs)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=".", help="repository to inspect (default: .)")
    parser.add_argument("--base", default="origin/main", help="base ref to diff against (default: origin/main)")
    parser.add_argument(
        "--path-glob",
        action="append",
        default=[],
        dest="globs",
        help="design-of-record glob to lint (repeatable; default: docs/proposals/*.md docs/specs/*.md)",
    )
    args = parser.parse_args(argv)

    if not GATE_LINT.exists():
        print(f"error: {GATE_LINT} not found", file=sys.stderr)
        return 3

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        print(f"error: --repo {repo} is not a directory", file=sys.stderr)
        return 3

    globs = args.globs or DEFAULT_GLOBS

    files, err = changed_files(repo, args.base)
    if files is None:
        print(f"error: {err}", file=sys.stderr)
        return 3

    design = [path for path in files if matches(path, globs)]
    print(f"gate-lint-ci: {len(files)} changed file(s); {len(design)} changed design-of-record doc(s)")
    print(f"globs: {', '.join(globs)}")
    if not design:
        print("CLEAN: no changed design-of-record documents to lint")
        return 0

    findings = []
    for rel in design:
        target = repo / rel
        result = subprocess.run(
            [sys.executable, str(GATE_LINT), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        if result.returncode == 1:
            findings.append(rel)
        elif result.returncode not in (0, 1):
            # gate-lint hit a usage/IO error on a file we handed it — fail closed.
            print(f"error: gate-lint returned {result.returncode} for {rel}", file=sys.stderr)
            return 3

    if findings:
        print(f"\nFINDINGS: {len(findings)} changed design-of-record doc(s) have gate-lint findings:")
        for rel in findings:
            print(f"  - {rel}")
        return 2
    print(f"\nCLEAN: all {len(design)} changed design-of-record doc(s) pass gate-lint")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
