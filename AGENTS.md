# OpenHarness — project guidelines (agent host project memory)

> Standing rules for any AI agent working in this repository.
> Owner: Bahram Boutorabi. These rules override any conflicting default behavior.

## 1. Repository authority

This repository (`Real-Bimox/OpenHarness`) is an **independent fork** of `HKUDS/OpenHarness` and is the sole authority for this project going forward.

- All pushes, branches, tags, and PRs target `origin` (`Real-Bimox/OpenHarness`) only.
- **Never push to upstream** (`HKUDS/OpenHarness`) in any way — no pushes, no PRs, no issues filed on the project's behalf. Upstream is fetch-only reference material.
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
