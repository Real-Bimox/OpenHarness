"""Work secret helpers."""

from __future__ import annotations

import base64
import json
from urllib.parse import urlsplit

from openharness.bridge.types import WorkSecret


def encode_work_secret(secret: WorkSecret) -> str:
    """Encode a work secret as base64url JSON."""
    data = json.dumps(secret.__dict__, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def decode_work_secret(secret: str) -> WorkSecret:
    """Decode and validate a base64url work secret."""
    padding = "=" * (-len(secret) % 4)
    raw = base64.urlsafe_b64decode((secret + padding).encode("utf-8"))
    data = json.loads(raw.decode("utf-8"))
    if data.get("version") != 1:
        raise ValueError(f"Unsupported work secret version: {data.get('version')}")
    if not data.get("session_ingress_token"):
        raise ValueError("Invalid work secret: missing session_ingress_token")
    if not isinstance(data.get("api_base_url"), str):
        raise ValueError("Invalid work secret: missing api_base_url")
    _validate_api_base_url(data["api_base_url"])
    return WorkSecret(
        version=data["version"],
        session_ingress_token=data["session_ingress_token"],
        api_base_url=data["api_base_url"],
    )


def build_sdk_url(api_base_url: str, session_id: str) -> str:
    """Build a session ingress WebSocket URL."""
    parsed = _validate_api_base_url(api_base_url)
    hostname = (parsed.hostname or "").lower()
    is_local = hostname in {"localhost", "127.0.0.1", "::1"}
    protocol = "ws" if is_local else "wss"
    version = "v2" if is_local else "v1"
    host = parsed.netloc.rstrip("/")
    return f"{protocol}://{host}/{version}/session_ingress/ws/{session_id}"


def _validate_api_base_url(api_base_url: str):
    parsed = urlsplit(api_base_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Invalid work secret: api_base_url must be an http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("Invalid work secret: api_base_url must not contain credentials")
    return parsed
