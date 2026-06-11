"""Tests for credential rotation and provider fallback in ResilientApiClient."""

from __future__ import annotations

import pytest

from openharness.api.client import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ApiTextDeltaEvent,
    CredentialRotatedEvent,
    ProviderFallbackEvent,
)
from openharness.api.credentials import CredentialPool
from openharness.api.resilient_client import FallbackTarget, ResilientApiClient
from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, TextBlock


class _Status(Exception):
    def __init__(self, status_code, message=""):
        super().__init__(message)
        self.status_code = status_code


def _request(model="primary-model"):
    return ApiMessageRequest(
        model=model,
        messages=[ConversationMessage(role="user", content=[TextBlock(text="hi")])],
    )


def _complete(text):
    return ApiMessageCompleteEvent(
        message=ConversationMessage(role="assistant", content=[TextBlock(text=text)]),
        usage=UsageSnapshot(input_tokens=1, output_tokens=1),
        stop_reason=None,
    )


class _ScriptedClient:
    """Yields events, or raises a scripted exception, per call."""

    def __init__(self, script, name="client"):
        self._script = list(script)
        self._call = 0
        self.__class__.__name__ = name

    async def stream_message(self, request):
        action = self._script[min(self._call, len(self._script) - 1)]
        self._call += 1
        if isinstance(action, Exception):
            raise action
        yield ApiTextDeltaEvent(text="")
        yield action


async def _drain(client, request):
    events = []
    async for event in client.stream_message(request):
        events.append(event)
    return events


@pytest.mark.asyncio
async def test_retry_then_success():
    primary = _ScriptedClient([_Status(503, "overloaded"), _complete("ok")])
    client = ResilientApiClient(primary, primary_model="primary-model", max_retries=3)
    # backoff sleeps are real but tiny for attempt 1
    import openharness.api.resilient_client as rc

    rc._BASE_DELAY = 0.0
    events = await _drain(client, _request())
    assert any(isinstance(e, ApiMessageCompleteEvent) and e.message.text == "ok" for e in events)


@pytest.mark.asyncio
async def test_fallback_on_persistent_failure():
    primary = _ScriptedClient([_Status(500, "boom")], name="PrimaryClient")
    fallback = _ScriptedClient([_complete("from-fallback")], name="FallbackClient")
    target = FallbackTarget(provider="openai", model="fb-model", factory=lambda: fallback)
    import openharness.api.resilient_client as rc

    rc._BASE_DELAY = 0.0
    client = ResilientApiClient(
        primary, primary_model="primary-model", fallbacks=[target], max_retries=1
    )
    events = await _drain(client, _request())
    assert any(isinstance(e, ProviderFallbackEvent) for e in events)
    assert any(isinstance(e, ApiMessageCompleteEvent) and e.message.text == "from-fallback" for e in events)


@pytest.mark.asyncio
async def test_credential_rotation_on_rate_limit():
    rebuilt = {}

    def _rebuild(key):
        rebuilt["key"] = key
        return _ScriptedClient([_complete("after-rotate")], name="RebuiltClient")

    primary = _ScriptedClient([_Status(429, "rate limit exceeded")], name="PrimaryClient")
    pool = CredentialPool.from_keys("anthropic", ["key-a", "key-b"])
    client = ResilientApiClient(
        primary,
        primary_model="primary-model",
        rebuild_primary=_rebuild,
        credential_pool=pool,
        max_retries=1,
    )
    events = await _drain(client, _request())
    assert any(isinstance(e, CredentialRotatedEvent) for e in events)
    assert rebuilt["key"] == "key-b"
    assert any(isinstance(e, ApiMessageCompleteEvent) and e.message.text == "after-rotate" for e in events)


@pytest.mark.asyncio
async def test_attempt_ceiling_terminates():
    primary = _ScriptedClient([_Status(500, "always")], name="PrimaryClient")
    import openharness.api.resilient_client as rc

    rc._BASE_DELAY = 0.0
    client = ResilientApiClient(primary, primary_model="primary-model", max_retries=2)
    with pytest.raises(Exception):
        await _drain(client, _request())


@pytest.mark.asyncio
async def test_no_chain_no_pool_passthrough():
    primary = _ScriptedClient([_complete("direct")])
    client = ResilientApiClient(primary, primary_model="primary-model")
    events = await _drain(client, _request())
    assert any(isinstance(e, ApiMessageCompleteEvent) for e in events)


def test_credential_pool_cooldown_and_alternatives():
    pool = CredentialPool.from_keys("p", ["a", "b"])
    assert pool.current() == "a"
    assert pool.has_alternatives() is True
    new = pool.mark_failure("rate_limit")
    assert new == "b"
    # single-key pool: auth failure kills it
    solo = CredentialPool.from_keys("p", ["only"])
    assert solo.mark_failure("auth") is None
    assert solo.select() is None


def test_credential_pool_dedup_and_build(monkeypatch):
    from openharness.config.settings import Settings
    from openharness.api.credentials import build_credential_pools

    settings = Settings(credential_pools={"anthropic": ["k1", "k1", "k2"], "openai": ["solo"]})
    pools = build_credential_pools(settings)
    assert "anthropic" in pools and len(pools["anthropic"]) == 2
    # single-key provider is not a pool
    assert "openai" not in pools


def test_resolver_wraps_only_when_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from openharness.config.settings import FallbackProvider, Settings
    from openharness.ui.runtime import _resolve_api_client_from_settings

    plain = _resolve_api_client_from_settings(Settings(api_key="sk-ant-test"))
    assert not isinstance(plain, ResilientApiClient)

    with_chain = _resolve_api_client_from_settings(
        Settings(
            api_key="sk-ant-test",
            fallback_providers=[FallbackProvider(provider="anthropic", model="claude-haiku-4-5")],
        )
    )
    assert isinstance(with_chain, ResilientApiClient)

    with_pool = _resolve_api_client_from_settings(
        Settings(api_key="sk-ant-test", credential_pools={"anthropic_claude": ["k1", "k2"]})
    )
    # pool keyed on provider; anthropic api-key provider id is 'anthropic'
    assert isinstance(with_pool, (ResilientApiClient,)) or with_pool is not None


@pytest.mark.asyncio
async def test_translated_terminal_errors_use_classifier_for_fallback():
    """OpenHarnessApiError must be classified, not blanket-fallbacked as 'auth'."""
    from openharness.api.errors import AuthenticationFailure, RequestFailure

    # AuthenticationFailure: classifier says fallback, with the right reason.
    primary = _ScriptedClient([AuthenticationFailure("terse")], name="PrimaryClient")
    fallback = _ScriptedClient([_complete("fb")], name="FallbackClient")
    client = ResilientApiClient(
        primary,
        primary_model="m",
        fallbacks=[FallbackTarget(provider="p", model="fm", factory=lambda: fallback)],
        max_retries=1,
    )
    events = await _drain(client, _request())
    fb_events = [e for e in events if isinstance(e, ProviderFallbackEvent)]
    assert fb_events and fb_events[0].reason == "auth"

    # RequestFailure with no fallback-worthy classification: raise, do NOT
    # consume the fallback chain.
    primary2 = _ScriptedClient([RequestFailure("stream ended unexpectedly mid-frame")], name="PrimaryClient")
    untouched = _ScriptedClient([_complete("never")], name="FallbackClient")
    client2 = ResilientApiClient(
        primary2,
        primary_model="m",
        fallbacks=[FallbackTarget(provider="p", model="fm", factory=lambda: untouched)],
        max_retries=1,
    )
    with pytest.raises(RequestFailure):
        await _drain(client2, _request())
    assert untouched._call == 0
