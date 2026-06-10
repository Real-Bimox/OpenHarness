# Local Privacy And Third-Party Reference Audit

Date: 2026-06-09

Branch: `proposal/headless-local-control-api`

## Scope

This audit checked the current OpenHarness tree for:

- Hardcoded credential-shaped values.
- Product-facing references to old upstream ownership.
- Model-visible prompts that identify as another tool.
- Passive third-party requests from local dashboard HTML.
- Network-capable built-in tools available during local bare/headless operation.

## Fixes Applied

- Removed a real-looking hardcoded API key from real-API test modules. Those tests now require `ANTHROPIC_API_KEY` in the local environment.
- Updated stale repository references in docs, installer scripts, issue templates, comments, and examples from old upstream names to `Real-Bimox/OpenHarness` or neutral local examples.
- Removed the non-runtime channel upstream sync artifact and its helper script.
- Changed coordinator and built-in agent prompts from third-party tool identity wording to OpenHarness-owned wording.
- Converted the built-in guide agent to a local OpenHarness guide that only uses `Glob`, `Grep`, and `Read`; it no longer instructs web fetching of external docs.
- Removed the bundled commit skill instruction to add attribution trailers.
- Removed Google Fonts links from autopilot dashboard HTML and generated dashboard output.
- Added `include_network_tools` to `create_default_tool_registry()` and wired `--bare` to exclude `web_fetch`, `web_search`, `image_to_text`, and `image_generation`.

## Remaining Intentional References

- `AGENTS.md` intentionally references upstream and owner-forbidden attribution-marker examples as policy text. It is not runtime behavior.
- `src/openharness/auth/external.py` keeps an exact local keychain service name for optional external CLI credential import compatibility. This does not run in local bare/headless operation unless a subscription-auth profile is explicitly selected.
- Provider, channel, OAuth, and web tool modules still contain external endpoints because those integrations remain part of the broader codebase. For local-only headless use, run with `--bare` and a local settings source so plugins, MCP, hooks, project-memory discovery, and network-capable built-in tools are excluded.

## Verification Commands

- Credential-shaped token scan across tracked text files, excluding binary assets and npm lockfiles.
- Ownership/reference scan across tracked text files, excluding binary assets and npm lockfiles.
