"""Resilient wrapper that adds the recovery state machine in one place.

Learned from hermes-agent's recovery loop (spec + deviations in
docs/proposals/error-recovery.md). hermes runs recovery inside its 4,200-line
conversation loop with state scattered across ``TurnRetryState`` and a dozen
``agent._*`` counters; OpenHarness composes it as a wrapper client that
implements ``SupportsStreamingMessages``, so the engine is unchanged and the
state machine lives in one auditable place with a single attempt budget.

The ``ClassifiedError`` flags are the sole policy authority here — this layer
reads them, never re-derives behavior from the reason.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import AsyncIterator, Callable

from openharness.api.client import (
    ApiMessageRequest,
    ApiRetryEvent,
    ApiStreamEvent,
    CredentialRotatedEvent,
    ProviderFallbackEvent,
    SupportsStreamingMessages,
)
from openharness.api.credentials import CredentialPool
from openharness.api.error_classifier import classify_error, parse_retry_after
from openharness.api.errors import OpenHarnessApiError

log = logging.getLogger(__name__)

_BASE_DELAY = 1.0
_MAX_DELAY = 60.0


class FallbackTarget:
    """A lazily-built fallback client plus the model it switches to."""

    def __init__(self, *, provider: str, model: str, factory: Callable[[], SupportsStreamingMessages]):
        self.provider = provider
        self.model = model
        self._factory = factory
        self._client: SupportsStreamingMessages | None = None

    def client(self) -> SupportsStreamingMessages:
        if self._client is None:
            self._client = self._factory()
        return self._client


class ResilientApiClient:
    """Wrap a primary client with credential rotation and provider fallback.

    Restoration is per turn: each ``stream_message`` starts from the primary
    client and credential, matching hermes's per-turn primary restoration, so
    a temporary fallback never becomes permanent.
    """

    def __init__(
        self,
        primary: SupportsStreamingMessages,
        *,
        primary_model: str,
        rebuild_primary: Callable[[str], SupportsStreamingMessages] | None = None,
        credential_pool: CredentialPool | None = None,
        fallbacks: list[FallbackTarget] | None = None,
        max_retries: int = 3,
    ) -> None:
        self._primary = primary
        self._primary_model = primary_model
        self._rebuild_primary = rebuild_primary
        self._pool = credential_pool
        self._fallbacks = fallbacks or []
        self._max_retries = max(1, max_retries)
        # Hard per-turn attempt ceiling: retries + one hop per fallback +
        # one rotation per pooled credential. Replaces hermes's scattered,
        # effectively-unbounded counters.
        self._attempt_ceiling = self._max_retries + len(self._fallbacks) + (len(self._pool) if self._pool else 0) + 2

    async def close(self) -> None:
        close = getattr(self._primary, "close", None)
        if close is not None:
            await close()
        for target in self._fallbacks:
            if target._client is not None:
                tclose = getattr(target._client, "close", None)
                if tclose is not None:
                    await tclose()

    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        client = self._primary
        model = self._primary_model
        fallback_index = 0
        retry_count = 0
        attempts = 0

        while True:
            attempts += 1
            if attempts > self._attempt_ceiling:
                raise OpenHarnessApiError("recovery attempt ceiling exceeded")
            current_request = request if model == self._primary_model else _retarget(request, model)
            try:
                async for event in client.stream_message(current_request):
                    yield event
                return
            except OpenHarnessApiError as exc:
                # Already-translated terminal errors (the inner client decided
                # not to retry). The classifier stays the sole policy
                # authority here too: fall back only when it says so.
                classified = classify_error(exc)
                if not classified.should_fallback:
                    raise
                advanced = self._advance_fallback(fallback_index)
                if advanced is None:
                    raise
                client, model, fallback_index = advanced
                yield ProviderFallbackEvent(
                    reason=classified.reason.value,
                    from_model=self._primary_model,
                    to_provider=client.__class__.__name__,
                    to_model=model,
                )
                retry_count = 0
                continue
            except Exception as exc:
                classified = classify_error(exc)

                # 1. Credential rotation within the provider.
                if classified.should_rotate_credential and self._pool is not None and self._pool.has_alternatives():
                    new_key = self._pool.mark_failure(classified.reason.value, retry_after=parse_retry_after(exc))
                    if new_key is not None and self._rebuild_primary is not None and model == self._primary_model:
                        try:
                            client = self._rebuild_primary(new_key)
                            self._primary = client
                            yield CredentialRotatedEvent(reason=classified.reason.value, provider=self._pool.provider)
                            continue
                        except Exception:
                            pass

                # 2. Provider fallback.
                if classified.should_fallback:
                    advanced = self._advance_fallback(fallback_index)
                    if advanced is not None:
                        client, model, fallback_index = advanced
                        yield ProviderFallbackEvent(
                            reason=classified.reason.value,
                            from_model=self._primary_model,
                            to_provider=client.__class__.__name__,
                            to_model=model,
                        )
                        retry_count = 0
                        continue

                # 3. Plain retry with backoff.
                if classified.retryable and retry_count < self._max_retries:
                    retry_count += 1
                    delay = self._backoff(retry_count, exc)
                    yield ApiRetryEvent(
                        message=classified.message[:200],
                        attempt=retry_count,
                        max_attempts=self._max_retries,
                        delay_seconds=delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                # 4. Exhausted: one last fallback try, else raise.
                advanced = self._advance_fallback(fallback_index)
                if advanced is not None:
                    client, model, fallback_index = advanced
                    yield ProviderFallbackEvent(
                        reason=classified.reason.value,
                        from_model=self._primary_model,
                        to_provider=client.__class__.__name__,
                        to_model=model,
                    )
                    retry_count = 0
                    continue
                raise

    def _advance_fallback(self, index: int):
        if index >= len(self._fallbacks):
            return None
        target = self._fallbacks[index]
        return target.client(), target.model, index + 1

    @staticmethod
    def _backoff(attempt: int, exc: Exception) -> float:
        retry_after = parse_retry_after(exc)
        if retry_after is not None:
            return min(retry_after, _MAX_DELAY)
        delay = min(_BASE_DELAY * (2 ** (attempt - 1)), _MAX_DELAY)
        return delay + random.uniform(0, delay * 0.25)


def _retarget(request: ApiMessageRequest, model: str) -> ApiMessageRequest:
    """Return a copy of the request aimed at a fallback model."""
    import dataclasses

    return dataclasses.replace(request, model=model)
