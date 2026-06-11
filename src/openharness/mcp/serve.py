"""Expose OpenHarness's local features as an MCP server (``oh --mcp-serve``).

Learned from hermes-agent's ``mcp_serve.py``: a FastMCP stdio server lets
Claude Code, Cursor, Zed, and other MCP hosts drive OpenHarness. This server
wraps the SAME internal operations as the headless JSONL protocol, so the two
surfaces cannot drift: conversation search, session listing, skill-loop
status/curation, and recovery/fallback status.

Built on the official ``mcp`` SDK already vendored for the client, so it adds
no runtime dependency. Submit/streaming over MCP is intentionally out of
scope for this first server (documented in docs/proposals/conversation-search.md
and error-recovery.md as follow-up); the headless JSONL protocol remains the
stateful turn-execution surface.
"""

from __future__ import annotations

import json
from typing import Any


def build_server():
    """Construct the FastMCP server with all read/maintenance tools registered."""
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("openharness")

    @server.tool()
    def search_sessions(
        query: str = "",
        project: str = "all",
        limit: int = 5,
        session_id: str = "",
        around_message_id: int | None = None,
        window: int = 5,
    ) -> str:
        """Search past OpenHarness conversations (full-text, no model calls).

        Provide `query` to discover; `session_id` to read a session;
        `session_id` + `around_message_id` to scroll; nothing to browse recent.
        """
        from openharness.services.conversation_index import get_conversation_index

        index = get_conversation_index()
        if session_id and around_message_id is not None:
            result = {"mode": "scroll", **index.around(session_id, around_message_id, window=window)}
        elif session_id:
            result = {"mode": "read", **index.read_session(session_id)}
        elif query.strip():
            result = {"mode": "discover", **index.search(query, project=project, limit=limit)}
        else:
            result = {"mode": "browse", **index.browse(project=project, limit=limit)}
        return json.dumps(result, ensure_ascii=False)

    @server.tool()
    def list_sessions(project: str = "all", limit: int = 20) -> str:
        """List recently active OpenHarness sessions with previews."""
        from openharness.services.conversation_index import get_conversation_index

        return json.dumps(get_conversation_index().browse(project=project, limit=limit), ensure_ascii=False)

    @server.tool()
    def skill_loop_status() -> str:
        """Report skill usage telemetry, lifecycle states, and pending writes."""
        from openharness.services.skill_approval import list_pending
        from openharness.services.skill_curator import load_state
        from openharness.skills.usage import load_records

        records = load_records()
        payload: dict[str, Any] = {
            "skills": {
                name: {
                    "state": rec.get("state", "active"),
                    "use_count": rec.get("use_count", 0),
                    "patch_count": rec.get("patch_count", 0),
                    "pinned": bool(rec.get("pinned")),
                    "agent_created": rec.get("created_by") == "agent",
                }
                for name, rec in records.items()
            },
            "pending_writes": len(list_pending()),
            "curator": load_state().get("last_report", {}),
        }
        return json.dumps(payload, ensure_ascii=False)

    @server.tool()
    def run_skill_curator(dry_run: bool = True) -> str:
        """Run the skill curator (lifecycle pass; LLM consolidation unless dry_run)."""
        import asyncio

        from openharness.services.skill_curator import run_curator

        report = asyncio.run(run_curator(dry_run=dry_run))
        return json.dumps(report, ensure_ascii=False)

    @server.tool()
    def recovery_status() -> str:
        """Report the configured provider fallback chain and credential pools."""
        from openharness.api.credentials import build_credential_pools
        from openharness.config.settings import load_settings

        settings = load_settings()
        pools = build_credential_pools(settings)
        payload = {
            "fallback_providers": [
                {"provider": entry.provider, "model": entry.model, "base_url": entry.base_url}
                for entry in settings.fallback_providers
            ],
            "credential_pools": {provider: len(pool) for provider, pool in pools.items()},
            "api_max_retries": settings.api_max_retries,
        }
        return json.dumps(payload, ensure_ascii=False)

    return server


def run_mcp_server() -> None:
    """Run the stdio MCP server (blocking)."""
    build_server().run()
