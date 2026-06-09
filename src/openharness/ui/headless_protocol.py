"""Protocol models for the local headless JSONL control mode."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class HeadlessRequest(BaseModel):
    """One request sent to ``oh --headless`` over stdin."""

    type: Literal[
        "submit",
        "submit_line",
        "resume",
        "continue",
        "list_sessions",
        "status",
        "interrupt",
        "shutdown",
        "permission_response",
    ]
    request_id: str | None = None
    id: str | None = None
    prompt: str | None = None
    line: str | None = None
    text: str | None = None
    session_id: str | None = None

    @property
    def correlation_id(self) -> str | None:
        return self.request_id or self.id

    @property
    def submitted_text(self) -> str:
        for value in (self.prompt, self.line, self.text):
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""


HEADLESS_PROTOCOL_VERSION = 1


__all__ = ["HEADLESS_PROTOCOL_VERSION", "HeadlessRequest"]
