from __future__ import annotations

from openharness.engine.messages import ConversationMessage, ImageBlock, TextBlock
from openharness.api.client import AnthropicApiClient, OAUTH_BETA_HEADER


def test_anthropic_client_adds_oauth_beta_header(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeAsyncAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("openharness.api.client.AsyncAnthropic", _FakeAsyncAnthropic)

    AnthropicApiClient(auth_token="oauth-token")

    assert captured["auth_token"] == "oauth-token"
    assert captured["default_headers"] == {"anthropic-beta": OAUTH_BETA_HEADER}


def test_anthropic_client_uses_api_key_without_oauth_beta(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeAsyncAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("openharness.api.client.AsyncAnthropic", _FakeAsyncAnthropic)

    AnthropicApiClient(api_key="api-key")

    assert captured["api_key"] == "api-key"
    assert "default_headers" not in captured


def test_anthropic_client_adds_claude_oauth_identity_headers(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeAsyncAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("openharness.api.client.AsyncAnthropic", _FakeAsyncAnthropic)
    monkeypatch.setattr(
        "openharness.auth.external.get_claude_code_version",
        lambda: "2.1.92",
    )
    monkeypatch.setattr(
        "openharness.auth.external.get_claude_code_session_id",
        lambda: "session-123",
    )
    monkeypatch.setattr(
        "openharness.api.client.get_claude_code_session_id",
        lambda: "session-123",
    )
    monkeypatch.setattr(
        "openharness.api.client.claude_attribution_header",
        lambda: "x-anthropic-billing-header: cc_version=2.1.92; cc_entrypoint=cli;",
    )

    AnthropicApiClient(auth_token="oauth-token", claude_oauth=True)

    headers = captured["default_headers"]
    assert captured["auth_token"] == "oauth-token"
    assert headers["x-app"] == "cli"
    assert headers["user-agent"] == "claude-cli/2.1.92 (external, cli)"
    assert headers["X-Claude-Code-Session-Id"] == "session-123"
    assert "oauth-2025-04-20" in headers["anthropic-beta"]
    assert "claude-code-20250219" in headers["anthropic-beta"]


def test_conversation_message_serializes_image_block_for_anthropic():
    message = ConversationMessage(
        role="user",
        content=[
            TextBlock(text="Describe this."),
            ImageBlock(media_type="image/png", data="YWJj", source_path="/tmp/example.png"),
        ],
    )

    assert message.to_api_param() == {
        "role": "user",
        "content": [
            {"type": "text", "text": "Describe this."},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "YWJj",
                },
            },
        ],
    }


def test_anthropic_client_refreshes_claude_token_on_request(monkeypatch):
    captured_tokens: list[str] = []

    class _FakeStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def __aiter__(self):
            if False:
                yield None
            return

        async def get_final_message(self):
            class _Usage:
                input_tokens = 1
                output_tokens = 1

            class _Message:
                usage = _Usage()
                stop_reason = "end_turn"
                role = "assistant"
                content = []

            return _Message()

    class _FakeMessages:
        def __init__(self):
            self.last_params = None

        def stream(self, **params):
            self.last_params = params
            return _FakeStream()

    class _FakeBeta:
        def __init__(self):
            self.messages = _FakeMessages()

    class _FakeAsyncAnthropic:
        def __init__(self, **kwargs):
            captured_tokens.append(kwargs["auth_token"])
            self.beta = _FakeBeta()
            self.messages = _FakeMessages()

    monkeypatch.setattr("openharness.api.client.AsyncAnthropic", _FakeAsyncAnthropic)
    monkeypatch.setattr(
        "openharness.auth.external.get_claude_code_session_id",
        lambda: "session-123",
    )
    monkeypatch.setattr(
        "openharness.api.client.get_claude_code_session_id",
        lambda: "session-123",
    )
    monkeypatch.setattr(
        "openharness.api.client.claude_attribution_header",
        lambda: "x-anthropic-billing-header: cc_version=2.1.92; cc_entrypoint=cli;",
    )

    current_token = {"value": "initial-token"}

    client = AnthropicApiClient(
        auth_token="initial-token",
        claude_oauth=True,
        auth_token_resolver=lambda: current_token["value"],
    )
    current_token["value"] = "refreshed-token"

    from openharness.api.client import ApiMessageRequest

    async def _run():
        events = []
        async for event in client.stream_message(
            ApiMessageRequest(
                model="claude-sonnet-4-6",
                messages=[],
                system_prompt="system prompt",
            )
        ):
            events.append(event)
        return events

    import asyncio

    events = asyncio.run(_run())

    assert captured_tokens == ["initial-token", "refreshed-token"]
    assert events
    assert client._client.beta.messages.last_params["metadata"] == {
        "user_id": '{"device_id":"openharness","session_id":"session-123","account_uuid":""}'
    }
    assert "oauth-2025-04-20" in client._client.beta.messages.last_params["betas"]
    system_blocks = client._client.beta.messages.last_params["system"]
    assert system_blocks[0]["text"].startswith(
        "x-anthropic-billing-header: cc_version=2.1.92; cc_entrypoint=cli;"
    )
    assert system_blocks[1]["text"] == "system prompt"
    assert system_blocks[1]["cache_control"] == {"type": "ephemeral"}


def _request_with_history():
    from openharness.api.client import ApiMessageRequest
    from openharness.engine.messages import ConversationMessage, TextBlock

    messages = [
        ConversationMessage(role="user", content=[TextBlock(text="first question")]),
        ConversationMessage(role="assistant", content=[TextBlock(text="first answer")]),
        ConversationMessage(role="user", content=[TextBlock(text="second question")]),
    ]
    return ApiMessageRequest(
        model="claude-sonnet-4-6",
        messages=messages,
        system_prompt="STABLE-PREFIX\n\n# Relevant Memories\ndynamic tail",
        system_cache_stable_chars=len("STABLE-PREFIX"),
        tools=[
            {"name": "grep", "description": "g", "input_schema": {}},
            {"name": "read_file", "description": "r", "input_schema": {}},
        ],
    )


def test_prompt_caching_request_shape(monkeypatch):
    class _FakeAsyncAnthropic:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("openharness.api.client.AsyncAnthropic", _FakeAsyncAnthropic)
    client = AnthropicApiClient(api_key="k", prompt_caching=True)
    request = _request_with_history()

    system = client._system_param(request)
    assert isinstance(system, list)
    assert system[0]["text"] == "STABLE-PREFIX"
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert system[1]["text"].lstrip().startswith("# Relevant Memories")
    assert "cache_control" not in system[1]

    tools = client._tools_with_cache_marker(request.tools)
    assert "cache_control" not in tools[0]
    assert tools[1]["cache_control"] == {"type": "ephemeral"}
    # The shared schema list must not be mutated in place.
    assert "cache_control" not in request.tools[1]

    messages = [m.to_api_param() for m in request.messages]
    client._mark_history_prefix(messages)
    prev_turn_last_block = messages[-2]["content"][-1]
    assert prev_turn_last_block["cache_control"] == {"type": "ephemeral"}
    assert all(
        "cache_control" not in block
        for block in messages[-1]["content"]
        if isinstance(block, dict)
    )


def test_prompt_caching_oauth_attribution_is_first_uncached_block(monkeypatch):
    class _FakeAsyncAnthropic:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("openharness.api.client.AsyncAnthropic", _FakeAsyncAnthropic)
    monkeypatch.setattr("openharness.api.client.get_claude_code_session_id", lambda: "s1")
    monkeypatch.setattr(
        "openharness.api.client.claude_attribution_header", lambda: "ATTRIBUTION"
    )
    client = AnthropicApiClient(auth_token="t", claude_oauth=True, prompt_caching=True)
    system = client._system_param(_request_with_history())
    assert system[0] == {"type": "text", "text": "ATTRIBUTION"}
    assert system[1]["cache_control"] == {"type": "ephemeral"}


def test_prompt_caching_disabled_keeps_string_system(monkeypatch):
    class _FakeAsyncAnthropic:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("openharness.api.client.AsyncAnthropic", _FakeAsyncAnthropic)
    client = AnthropicApiClient(api_key="k", prompt_caching=False)
    system = client._system_param(_request_with_history())
    assert isinstance(system, str)
    assert system.startswith("STABLE-PREFIX")


def test_usage_snapshot_carries_cache_counters():
    from openharness.api.usage import UsageSnapshot
    from openharness.engine.cost_tracker import CostTracker

    tracker = CostTracker()
    tracker.add(UsageSnapshot(input_tokens=10, output_tokens=2, cache_read_input_tokens=90))
    tracker.add(
        UsageSnapshot(
            input_tokens=5,
            output_tokens=1,
            cache_creation_input_tokens=100,
            cache_read_input_tokens=50,
        )
    )
    total = tracker.total
    assert total.cache_read_input_tokens == 140
    assert total.cache_creation_input_tokens == 100
    assert total.model_dump()["cache_read_input_tokens"] == 140
