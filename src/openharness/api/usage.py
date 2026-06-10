"""Usage tracking models."""

from __future__ import annotations

from pydantic import BaseModel


class UsageSnapshot(BaseModel):
    """Token usage returned by the model provider."""

    input_tokens: int = 0
    output_tokens: int = 0
    # Prompt-caching counters (Anthropic-format providers). Cache reads are
    # billed separately from regular input tokens and are the signal that
    # the cache breakpoints are effective.
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """Return the total number of accounted tokens."""
        return self.input_tokens + self.output_tokens
