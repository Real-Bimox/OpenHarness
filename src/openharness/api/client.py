"""Anthropic API client wrapper with retry logic."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Protocol

from anthropic import APIError, APIStatusError, AsyncAnthropic

from openharness.api.errors import (
    AuthenticationFailure,
    OpenHarnessApiError,
    RateLimitFailure,
    RequestFailure,
)
from openharness.auth.external import (
    claude_attribution_header,
    claude_oauth_betas,
    claude_oauth_headers,
    get_claude_code_session_id,
)
from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, assistant_message_from_api

log = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 3
BASE_DELAY = 1.0  # seconds
MAX_DELAY = 30.0
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 529}
OAUTH_BETA_HEADER = "oauth-2025-04-20"


@dataclass(frozen=True)
class ApiMessageRequest:
    """Input parameters for a model invocation."""

    model: str
    messages: list[ConversationMessage]
    system_prompt: str | None = None
    max_tokens: int = 4096
    tools: list[dict[str, Any]] = field(default_factory=list)
    effort: str | None = None
    # Length of the stable system-prompt prefix in characters. Content after
    # this offset (e.g. per-line relevant-memories) changes between turns and
    # must stay outside the provider's prompt cache.
    system_cache_stable_chars: int | None = None


@dataclass(frozen=True)
class ApiTextDeltaEvent:
    """Incremental text produced by the model."""

    text: str


@dataclass(frozen=True)
class ApiMessageCompleteEvent:
    """Terminal event containing the full assistant message."""

    message: ConversationMessage
    usage: UsageSnapshot
    stop_reason: str | None = None


@dataclass(frozen=True)
class ApiRetryEvent:
    """A recoverable upstream failure that will be retried automatically."""

    message: str
    attempt: int
    max_attempts: int
    delay_seconds: float


@dataclass(frozen=True)
class ProviderFallbackEvent:
    """The resilient client switched to a fallback provider/model mid-turn."""

    reason: str
    from_model: str
    to_provider: str
    to_model: str


@dataclass(frozen=True)
class CredentialRotatedEvent:
    """The resilient client rotated to another credential for the provider."""

    reason: str
    provider: str


ApiStreamEvent = (
    ApiTextDeltaEvent
    | ApiMessageCompleteEvent
    | ApiRetryEvent
    | ProviderFallbackEvent
    | CredentialRotatedEvent
)


class SupportsStreamingMessages(Protocol):
    """Protocol used by the query engine in tests and production."""

    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        """Yield streamed events for the request."""


def _is_retryable(exc: Exception) -> bool:
    """Check if an exception is retryable."""
    if isinstance(exc, APIStatusError):
        return exc.status_code in RETRYABLE_STATUS_CODES
    if isinstance(exc, APIError):
        return True  # Network errors are retryable
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    return False


def _get_retry_delay(attempt: int, exc: Exception | None = None) -> float:
    """Calculate delay with exponential backoff and jitter."""
    import random

    # Check for Retry-After header
    if isinstance(exc, APIStatusError):
        retry_after = getattr(exc, "headers", {})
        if hasattr(retry_after, "get"):
            val = retry_after.get("retry-after")
            if val:
                try:
                    return min(float(val), MAX_DELAY)
                except (ValueError, TypeError):
                    pass

    delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
    jitter = random.uniform(0, delay * 0.25)
    return delay + jitter


class AnthropicApiClient:
    """Thin wrapper around the Anthropic async SDK with retry logic."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        auth_token: str | None = None,
        base_url: str | None = None,
        claude_oauth: bool = False,
        auth_token_resolver: Callable[[], str] | None = None,
        prompt_caching: bool = True,
    ) -> None:
        self._api_key = api_key
        self._auth_token = auth_token
        self._base_url = base_url
        self._claude_oauth = claude_oauth
        self._auth_token_resolver = auth_token_resolver
        self._session_id = get_claude_code_session_id() if claude_oauth else ""
        self._prompt_caching = prompt_caching
        self._client = self._create_client()
        self._stale_close_tasks: set[asyncio.Task[None]] = set()

    def _create_client(self) -> AsyncAnthropic:
        kwargs: dict[str, Any] = {}
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._auth_token:
            kwargs["auth_token"] = self._auth_token
            kwargs["default_headers"] = (
                claude_oauth_headers()
                if self._claude_oauth
                else {"anthropic-beta": OAUTH_BETA_HEADER}
            )
        if self._base_url:
            kwargs["base_url"] = self._base_url
        return AsyncAnthropic(**kwargs)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.close()

    def _refresh_client_auth(self) -> None:
        if not self._claude_oauth or self._auth_token_resolver is None:
            return
        next_token = self._auth_token_resolver()
        if next_token and next_token != self._auth_token:
            self._auth_token = next_token
            previous = self._client
            self._client = self._create_client()
            # Close the replaced client's connection pool instead of leaking
            # it; fire-and-forget with a held reference so GC can't cancel it.
            close = getattr(previous, "close", None)
            if close is None:
                return
            try:
                task = asyncio.get_running_loop().create_task(close())
            except RuntimeError:
                pass
            else:
                self._stale_close_tasks.add(task)
                task.add_done_callback(self._stale_close_tasks.discard)

    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        """Yield text deltas and the final assistant message with retry on transient errors."""
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                self._refresh_client_auth()
                async for event in self._stream_once(request):
                    yield event
                return  # Success
            except OpenHarnessApiError:
                raise  # Auth errors are not retried
            except Exception as exc:
                last_error = exc
                if attempt >= MAX_RETRIES or not _is_retryable(exc):
                    if isinstance(exc, APIError):
                        raise _translate_api_error(exc) from exc
                    raise RequestFailure(str(exc)) from exc

                delay = _get_retry_delay(attempt, exc)
                status = getattr(exc, "status_code", "?")
                log.warning(
                    "API request failed (attempt %d/%d, status=%s), retrying in %.1fs: %s",
                    attempt + 1, MAX_RETRIES + 1, status, delay, exc,
                )
                yield ApiRetryEvent(
                    message=str(exc),
                    attempt=attempt + 1,
                    max_attempts=MAX_RETRIES + 1,
                    delay_seconds=delay,
                )
                await asyncio.sleep(delay)

        if last_error is not None:
            if isinstance(last_error, APIError):
                raise _translate_api_error(last_error) from last_error
            raise RequestFailure(str(last_error)) from last_error

    def _system_param(self, request: ApiMessageRequest) -> str | list[dict[str, Any]] | None:
        """Build the system parameter, with a cache breakpoint when enabled.

        Block layout: [attribution (OAuth only)][stable prefix + cache_control]
        [dynamic tail]. The stable prefix is everything before
        ``system_cache_stable_chars``; per-line content after it stays out of
        the provider cache so it cannot invalidate the prefix.
        """
        system = request.system_prompt or ""
        attribution = claude_attribution_header() if self._claude_oauth else ""
        if not self._prompt_caching:
            if attribution:
                return f"{attribution}\n{system}" if system else attribution
            return system or None

        blocks: list[dict[str, Any]] = []
        if attribution:
            blocks.append({"type": "text", "text": attribution})
        boundary = request.system_cache_stable_chars
        stable = system if boundary is None else system[:boundary]
        tail = "" if boundary is None else system[boundary:]
        if stable:
            blocks.append(
                {"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}}
            )
        if tail:
            blocks.append({"type": "text", "text": tail})
        return blocks or None

    @staticmethod
    def _tools_with_cache_marker(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Mark the last tool so the whole tool array becomes a cache prefix.

        The registry's schema list is shared and cached; only the last entry
        is copied for the marker.
        """
        marked = list(tools)
        marked[-1] = {**marked[-1], "cache_control": {"type": "ephemeral"}}
        return marked

    @staticmethod
    def _mark_history_prefix(messages: list[dict[str, Any]]) -> None:
        """Set a cache breakpoint on the last block of the previous turn.

        Everything up to and including the prior turn is a stable prefix;
        only the newest message changes between requests.
        """
        if len(messages) < 2:
            return
        content = messages[-2].get("content")
        if not isinstance(content, list) or not content:
            return
        last_block = content[-1]
        if isinstance(last_block, dict) and last_block.get("type") in {"text", "tool_result"}:
            content[-1] = {**last_block, "cache_control": {"type": "ephemeral"}}

    async def _stream_once(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        """Single attempt at streaming a message."""
        params: dict[str, Any] = {
            "model": request.model,
            "messages": [message.to_api_param() for message in request.messages],
            "max_tokens": request.max_tokens,
        }
        system_param = self._system_param(request)
        if system_param:
            params["system"] = system_param
        if request.tools:
            params["tools"] = (
                self._tools_with_cache_marker(request.tools)
                if self._prompt_caching
                else request.tools
            )
        if self._prompt_caching:
            self._mark_history_prefix(params["messages"])
        if self._claude_oauth:
            params["betas"] = claude_oauth_betas()
            params["metadata"] = {
                "user_id": json.dumps(
                    {
                        "device_id": "openharness",
                        "session_id": self._session_id,
                        "account_uuid": "",
                    },
                    separators=(",", ":"),
                )
            }
            params["extra_headers"] = {"x-client-request-id": str(uuid.uuid4())}

        try:
            stream_api = self._client.beta.messages if self._claude_oauth else self._client.messages
            async with stream_api.stream(**params) as stream:
                async for event in stream:
                    if getattr(event, "type", None) != "content_block_delta":
                        continue
                    delta = getattr(event, "delta", None)
                    if getattr(delta, "type", None) != "text_delta":
                        continue
                    text = getattr(delta, "text", "")
                    if text:
                        yield ApiTextDeltaEvent(text=text)

                final_message = await stream.get_final_message()
        except APIError as exc:
            if isinstance(exc, APIStatusError) and exc.status_code in RETRYABLE_STATUS_CODES:
                raise  # Let retry logic handle it
            raise _translate_api_error(exc) from exc

        usage = getattr(final_message, "usage", None)
        yield ApiMessageCompleteEvent(
            message=assistant_message_from_api(final_message),
            usage=UsageSnapshot(
                input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
                cache_creation_input_tokens=int(
                    getattr(usage, "cache_creation_input_tokens", 0) or 0
                ),
                cache_read_input_tokens=int(
                    getattr(usage, "cache_read_input_tokens", 0) or 0
                ),
            ),
            stop_reason=getattr(final_message, "stop_reason", None),
        )


def _translate_api_error(exc: APIError) -> OpenHarnessApiError:
    name = exc.__class__.__name__
    if name in {"AuthenticationError", "PermissionDeniedError"}:
        return AuthenticationFailure(str(exc))
    if name == "RateLimitError":
        return RateLimitFailure(str(exc))
    return RequestFailure(str(exc))
