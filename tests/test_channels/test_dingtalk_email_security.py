"""Agent-agnostic regressions for DingTalk and Email user-facing strings.

Mirrors ``test_telegram_security.py``: user-facing text emitted by a channel
must not leak the vestigial ``nanobot`` codename and should use a neutral
(or configured) value instead.
"""

from __future__ import annotations

from email.message import EmailMessage

import pytest

from openharness.channels.bus.events import OutboundMessage
from openharness.channels.bus.queue import MessageBus
from openharness.channels.impl.dingtalk import DingTalkChannel
from openharness.channels.impl.email import EmailChannel
from openharness.config.schema import DingTalkConfig, EmailConfig


@pytest.mark.asyncio
async def test_dingtalk_reply_title_is_neutral(monkeypatch):
    """DingTalk markdown replies must use a neutral title, never 'nanobot'."""
    channel = DingTalkChannel(
        DingTalkConfig(client_id="cid", client_secret="secret", allow_from=["*"]),
        MessageBus(),
    )

    captured: list[dict] = []

    async def fake_get_token():
        return "token"

    async def fake_send_batch(token, chat_id, msg_key, msg_param):
        captured.append({"msg_key": msg_key, "msg_param": msg_param})
        return True

    monkeypatch.setattr(channel, "_get_access_token", fake_get_token)
    monkeypatch.setattr(channel, "_send_batch_message", fake_send_batch)

    await channel.send(
        OutboundMessage(channel="dingtalk", chat_id="staff-1", content="hello world")
    )

    markdown_calls = [c for c in captured if c["msg_key"] == "sampleMarkdown"]
    assert markdown_calls, "expected a markdown reply to be sent"
    title = markdown_calls[0]["msg_param"]["title"]
    assert "nanobot" not in title.lower()
    assert title == "Reply"


@pytest.mark.asyncio
async def test_email_subject_fallback_is_neutral(monkeypatch):
    """Email replies with no prior subject must fall back to a neutral subject."""
    channel = EmailChannel(
        EmailConfig(
            consent_granted=True,
            smtp_host="smtp.example.com",
            from_address="bot@example.com",
            subject_prefix="Re: ",
            allow_from=["*"],
        ),
        MessageBus(),
    )

    sent: list[EmailMessage] = []

    def fake_smtp_send(msg: EmailMessage) -> None:
        sent.append(msg)

    monkeypatch.setattr(channel, "_smtp_send", fake_smtp_send)

    # No prior inbound subject recorded for this recipient -> fallback applies.
    await channel.send(
        OutboundMessage(channel="email", chat_id="user@example.com", content="hi")
    )

    assert sent, "expected an email to be sent"
    subject = sent[0]["Subject"]
    assert "nanobot" not in subject.lower()
    assert subject == "Re: Reply"


def test_email_reply_subject_fallback_is_neutral():
    """_reply_subject must use a neutral fallback when given an empty base."""
    channel = EmailChannel(EmailConfig(subject_prefix="Re: "), MessageBus())

    subject = channel._reply_subject("")

    assert "nanobot" not in subject.lower()
    assert subject == "Re: Reply"
