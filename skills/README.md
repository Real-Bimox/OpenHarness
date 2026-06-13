# OpenHarness skills

Reusable agent disciplines for working in this repository. A skill here is an inert Markdown discipline (read as data, per `AGENTS.md` §3) that any agent — human-directed or delegated (§6) — loads and follows when its trigger applies. Skills are design/process discipline, not runtime code; they never sit on the merge-authority path.

## Skills

| Skill | Purpose | Mandatory when |
|-------|---------|----------------|
| [design-quality-gate](design-quality-gate/SKILL.md) | **(v3)** Risk-tiered pre-implementation design review — triages each change (T0 local · T1 single-contract · T2 breaking/stateful), then catches cross-document drift, missing failure modes, overclaimed coverage, and incomplete contracts **before** implementation. Self-contained: `SKILL.md` + `assets/` template + `references/` + optional `scripts/gate-lint.py`. | **Always**, for every numbered proposal and spec (each triages to T1/T2). Embedding the gate section and passing it (no open P1; no unaccepted P2) is a hard DRAFT→APPROVED lifecycle condition — see [`AGENTS.md`](../AGENTS.md) §7. |

## Adding a skill

Each skill is its own subdirectory containing:
- `SKILL.md` *(required)* — the discipline itself, with YAML frontmatter (`name`, `description`).
- `README.md` *(optional)* — orientation / when-to-use.
- `assets/`, `references/`, `scripts/` *(optional)* — copyable templates, deep-dive references loaded on demand, and dependency-free helper scripts.

Add a row to the table above when introducing a skill so the folder stays self-documenting (the repo is the only reliable memory, `AGENTS.md` §5). Keep skills agent-agnostic (§2) — role/generic names, never a model/vendor/tool brand.
