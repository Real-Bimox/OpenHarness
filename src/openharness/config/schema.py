"""Compatibility channel config models.

These models keep the synced channel adapters importable while the main
OpenHarness settings system evolves independently.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _CompatModel(BaseModel):
    """Base model that tolerates adapter-specific extra fields."""

    model_config = ConfigDict(extra="allow")


class ProviderApiKeyConfig(_CompatModel):
    api_key: str = ""


class ProviderConfigs(_CompatModel):
    groq: ProviderApiKeyConfig = Field(default_factory=ProviderApiKeyConfig)


class BaseChannelConfig(_CompatModel):
    enabled: bool = False
    # Secure default: enabling a channel does not automatically trust every
    # remote sender. Operators must explicitly allow specific identities, or
    # intentionally set ["*"] when they want open access.
    allow_from: list[str] = Field(default_factory=list)


class TelegramConfig(BaseChannelConfig):
    token: str = ""
    chat_id: str | None = None
    proxy: str | None = None
    reply_to_message: bool = True
    bot_name: str = "ohmo"


class DirectMessageConfig(_CompatModel):
    enabled: bool = True
    policy: str = "open"
    allow_from: list[str] = Field(default_factory=list)


class SlackConfig(BaseChannelConfig):
    bot_token: str = ""
    app_token: str = ""
    signing_secret: str = ""
    mode: str = "socket"
    reply_in_thread: bool = True
    react_emoji: str = "eyes"
    dm: DirectMessageConfig = Field(default_factory=DirectMessageConfig)
    group_policy: str = "mention"
    group_allow_from: list[str] = Field(default_factory=list)


class DiscordConfig(BaseChannelConfig):
    token: str = ""
    gateway_url: str = "wss://gateway.discord.gg/?v=10&encoding=json"
    intents: int = 513
    group_policy: str = "mention"


class FeishuConfig(BaseChannelConfig):
    app_id: str = ""
    app_secret: str = ""
    encrypt_key: str = ""
    verification_token: str = ""
    # Group reply policy is enforced by ohmo gateway because managed-group
    # metadata lives outside the generic Feishu channel adapter.
    group_policy: str = "managed_or_mention"
    bot_open_id: str = ""
    bot_names: list[str] = Field(default_factory=lambda: ["ohmo", "openclaw", "openharness"])
    domain: str = "https://open.feishu.cn"  # use https://open.larksuite.com for Lark international


class DingTalkConfig(BaseChannelConfig):
    client_id: str = ""
    client_secret: str = ""
    robot_code: str = ""


class EmailConfig(BaseChannelConfig):
    consent_granted: bool = False
    poll_interval_seconds: int = 30
    auto_reply_enabled: bool = True
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    from_address: str = ""
    smtp_use_ssl: bool = False
    smtp_use_tls: bool = True
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_use_ssl: bool = True
    imap_mailbox: str = "INBOX"
    mark_seen: bool = True
    max_body_chars: int = 20_000
    subject_prefix: str = "Re: "


class QQConfig(BaseChannelConfig):
    token: str = ""
    app_id: str = ""
    app_secret: str = ""
    secret: str = ""


class MatrixConfig(BaseChannelConfig):
    homeserver: str = ""
    access_token: str = ""
    user_id: str = ""
    device_id: str = ""
    e2ee_enabled: bool = False
    sync_stop_grace_seconds: int = 10
    max_media_bytes: int = 20 * 1024 * 1024
    allow_room_mentions: bool = True
    group_policy: str = "mention"
    group_allow_from: list[str] = Field(default_factory=list)


class WhatsAppConfig(BaseChannelConfig):
    access_token: str = ""
    phone_number_id: str = ""
    verify_token: str = ""
    bridge_url: str = ""
    bridge_token: str = ""


class MochatConfig(BaseChannelConfig):
    endpoint: str = ""
    token: str = ""


class ChannelConfigs(_CompatModel):
    send_progress: bool = True
    send_tool_hints: bool = True
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    slack: SlackConfig = Field(default_factory=SlackConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    dingtalk: DingTalkConfig = Field(default_factory=DingTalkConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    qq: QQConfig = Field(default_factory=QQConfig)
    matrix: MatrixConfig = Field(default_factory=MatrixConfig)
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    mochat: MochatConfig = Field(default_factory=MochatConfig)


class Config(_CompatModel):
    channels: ChannelConfigs = Field(default_factory=ChannelConfigs)
    providers: ProviderConfigs = Field(default_factory=ProviderConfigs)
