"""Search past conversations from the derived FTS index.

Modeled on hermes-agent's ``session_search`` (four shapes inferred from the
arguments, zero LLM cost) with deliberate differences documented in
``docs/proposals/conversation-search.md``: per-message output budgets, honest
counts, a parser-based query sanitizer, and per-project scoping.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class SessionSearchInput(BaseModel):
    """All parameters optional; the shape is inferred.

    scroll = session_id + around_message_id; read = session_id only;
    browse = no query; discover = query.
    """

    query: str | None = Field(
        default=None,
        description=(
            "Search text for discovery. Terms are matched as substrings/words; "
            "AND, OR, NOT between terms and a trailing * for prefix are honored."
        ),
    )
    limit: int = Field(default=3, description="Max sessions for discovery (1-10).")
    sort: str | None = Field(
        default=None, description="Optional temporal bias for discovery: 'newest' or 'oldest'."
    )
    session_id: str | None = Field(default=None, description="Session to read or scroll.")
    around_message_id: int | None = Field(
        default=None, description="Message id to anchor a scroll window on."
    )
    window: int = Field(default=5, description="Messages per side when scrolling (1-20).")
    role_filter: str | None = Field(
        default=None, description="Comma-separated roles (default: user,assistant)."
    )
    project: str | None = Field(
        default=None,
        description="Project path to scope the search; 'all' searches every project. Defaults to the current project.",
    )


class SessionSearchTool(BaseTool):
    name = "session_search"
    description = (
        "Search your own past conversations (full-text, no LLM cost). Shapes: "
        "discovery (query=...), read (session_id=...), scroll (session_id + "
        "around_message_id, re-anchor on the first/last returned id to page), "
        "browse recent sessions (no arguments). Results return real stored "
        "messages with anchored context windows; messages_before/after are "
        "exact counts, so a value of 0 means a session edge. Prefer this over "
        "filesystem or web search when asked about prior work or decisions."
    )
    input_model = SessionSearchInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    async def execute(self, arguments: SessionSearchInput, context: ToolExecutionContext) -> ToolResult:
        import asyncio

        from openharness.config import load_settings
        from openharness.services.conversation_index import get_conversation_index

        try:
            if not load_settings().conversation_index_enabled:
                return ToolResult(
                    output=json.dumps(
                        {"success": False, "error": "Conversation indexing is disabled (conversation_index_enabled=false)."}
                    ),
                    is_error=True,
                )
        except Exception:
            pass

        current_session = str(context.metadata.get("session_id") or "") or None
        project = arguments.project or str(context.cwd)
        roles = (
            [part.strip() for part in arguments.role_filter.split(",") if part.strip()]
            if arguments.role_filter
            else None
        )

        def _run() -> dict:
            index = get_conversation_index()
            if arguments.session_id and arguments.around_message_id is not None:
                if current_session and arguments.session_id == current_session:
                    return {"error": "That session is your active conversation; its content is already in context."}
                result = index.around(
                    arguments.session_id, arguments.around_message_id, window=arguments.window
                )
                return {"mode": "scroll", **result}
            if arguments.session_id:
                if current_session and arguments.session_id == current_session:
                    return {"error": "That session is your active conversation; its content is already in context."}
                return {"mode": "read", **index.read_session(arguments.session_id)}
            if not (arguments.query or "").strip():
                browse = index.browse(project=project, limit=arguments.limit or 10, exclude_session=current_session)
                return {"mode": "browse", **browse}
            search = index.search(
                arguments.query or "",
                project=project,
                limit=arguments.limit,
                sort=arguments.sort,
                role_filter=roles,
                exclude_session=current_session,
            )
            return {"mode": "discover", "query": arguments.query, **search}

        result = await asyncio.to_thread(_run)
        if "error" in result:
            return ToolResult(output=json.dumps({"success": False, **result}), is_error=True)
        return ToolResult(output=json.dumps({"success": True, **result}, ensure_ascii=False))
