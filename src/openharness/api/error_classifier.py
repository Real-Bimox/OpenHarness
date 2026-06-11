"""Typed, declarative error classification for the model layer.

Learned from hermes-agent's ``agent/error_classifier.py`` (spec + deviations
in docs/proposals/error-recovery.md). hermes proved which provider failures
matter and how to disambiguate them; this is the cleaner reimplementation its
own author recommended — a single ``ClassifiedError`` whose flags are the sole
recovery-policy authority, produced by an ordered rule table that is
specificity-sorted and unit-testable row by row, rather than ~15 ad-hoc
lowercased-substring lists scattered through a retry loop.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass


class RecoveryReason(str, enum.Enum):
    AUTH = "auth"
    BILLING = "billing"
    RATE_LIMIT = "rate_limit"
    CONTEXT_OVERFLOW = "context_overflow"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    SERVER_ERROR = "server_error"
    OVERLOADED = "overloaded"
    TIMEOUT = "timeout"
    CONTENT_POLICY = "content_policy"
    MODEL_NOT_FOUND = "model_not_found"
    FORMAT_ERROR = "format_error"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ClassifiedError:
    """The single recovery-policy authority for a failed model request."""

    reason: RecoveryReason
    status_code: int | None
    message: str
    retryable: bool
    should_rotate_credential: bool
    should_fallback: bool
    should_compress: bool


# --- pattern groups (lowercased substring markers) -------------------------

_CONTENT_POLICY = (
    "flagged for possible cybersecurity",
    "violates our usage policies",
    "violates openai's usage policies",
    "flagged by our safety",
    "content_filter",
    "responsibleaipolicyviolation",
)
_BILLING = (
    "insufficient_quota",
    "insufficient credits",
    "billing",
    "payment required",
    "spending limit",
    "credit balance is too low",
    "free tier",
)
_RATE_LIMIT = ("rate limit", "rate_limit", "too many requests", "quota exceeded", "resource_exhausted")
_CONTEXT_OVERFLOW = (
    "context length",
    "context_length_exceeded",
    "maximum context",
    "max_model_len",
    "too many tokens",
    "input is too long",
    "prompt is too long",
    "reduce the length",
    "context window",
)
_MODEL_NOT_FOUND = ("model not found", "model_not_found", "no such model", "invalid model", "model_not_available")
_REQUEST_VALIDATION = ("unknown parameter", "unsupported parameter", "unrecognized request argument")
_AUTH = ("invalid api key", "incorrect api key", "unauthorized", "authentication", "token expired", "invalid_token")
_TIMEOUT = ("timed out", "timeout", "deadline exceeded", "connection reset", "server disconnected", "peer closed")


def _has(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _make(
    reason: RecoveryReason,
    status: int | None,
    message: str,
    *,
    retryable: bool = False,
    rotate: bool = False,
    fallback: bool = False,
    compress: bool = False,
) -> ClassifiedError:
    return ClassifiedError(
        reason=reason,
        status_code=status,
        message=message,
        retryable=retryable,
        should_rotate_credential=rotate,
        should_fallback=fallback,
        should_compress=compress,
    )


def _extract(exc: BaseException) -> tuple[int | None, str]:
    """Pull a status code and a lowercased message, walking the cause chain."""
    status: int | None = None
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    depth = 0
    while current is not None and depth < 5 and id(current) not in seen:
        seen.add(id(current))
        depth += 1
        for attr in ("status_code", "status"):
            value = getattr(current, attr, None)
            if isinstance(value, int) and 100 <= value <= 599 and status is None:
                status = value
        body = getattr(current, "body", None)
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict) and isinstance(err.get("message"), str):
                parts.append(err["message"])
        parts.append(str(current))
        if status is None and current.__class__.__name__ == "RateLimitError":
            status = 429
        current = current.__cause__ or current.__context__
    return status, " ".join(parts).lower()


def classify_error(exc: BaseException) -> ClassifiedError:
    """Classify any exception into a recovery policy. Order = specificity."""
    status, text = _extract(exc)

    # 1. Content policy — before status, so a 400 safety block isn't a format error.
    if _has(text, _CONTENT_POLICY):
        return _make(RecoveryReason.CONTENT_POLICY, status, text, fallback=True)

    # 2. Transport/exception-type heuristics with no usable status.
    if status is None:
        if isinstance(exc, (TimeoutError,)) or _has(text, _TIMEOUT):
            return _make(RecoveryReason.TIMEOUT, status, text, retryable=True)
        if isinstance(exc, (ConnectionError, OSError)):
            return _make(RecoveryReason.TIMEOUT, status, text, retryable=True)

    # 3. Status-driven classification.
    if status == 401:
        return _make(RecoveryReason.AUTH, status, text, rotate=True, fallback=True)
    if status == 403:
        if _has(text, _BILLING):
            return _make(RecoveryReason.BILLING, status, text, rotate=True, fallback=True)
        return _make(RecoveryReason.AUTH, status, text, fallback=True)
    if status == 402:
        if _has(text, _RATE_LIMIT) and ("try again" in text or "reset" in text or "retry" in text):
            return _make(RecoveryReason.RATE_LIMIT, status, text, retryable=True, rotate=True, fallback=True)
        return _make(RecoveryReason.BILLING, status, text, rotate=True, fallback=True)
    if status == 404:
        if _has(text, _BILLING):
            return _make(RecoveryReason.BILLING, status, text, rotate=True, fallback=True)
        if _has(text, _MODEL_NOT_FOUND):
            return _make(RecoveryReason.MODEL_NOT_FOUND, status, text, fallback=True)
        return _make(RecoveryReason.UNKNOWN, status, text, retryable=True)
    if status == 413:
        return _make(RecoveryReason.PAYLOAD_TOO_LARGE, status, text, retryable=True, compress=True)
    if status == 429:
        if _has(text, _CONTEXT_OVERFLOW):
            return _make(RecoveryReason.CONTEXT_OVERFLOW, status, text, retryable=True, compress=True)
        return _make(RecoveryReason.RATE_LIMIT, status, text, retryable=True, rotate=True, fallback=True)
    if status == 400:
        # validation before overflow: "Unsupported parameter: 'max_tokens'"
        # contains the overflow marker but is not an overflow.
        if _has(text, _REQUEST_VALIDATION):
            return _make(RecoveryReason.FORMAT_ERROR, status, text, fallback=True)
        if _has(text, _CONTEXT_OVERFLOW):
            return _make(RecoveryReason.CONTEXT_OVERFLOW, status, text, retryable=True, compress=True)
        if _has(text, _MODEL_NOT_FOUND):
            return _make(RecoveryReason.MODEL_NOT_FOUND, status, text, fallback=True)
        if _has(text, _RATE_LIMIT):
            return _make(RecoveryReason.RATE_LIMIT, status, text, retryable=True, rotate=True, fallback=True)
        if _has(text, _BILLING):
            return _make(RecoveryReason.BILLING, status, text, rotate=True, fallback=True)
        return _make(RecoveryReason.FORMAT_ERROR, status, text, fallback=True)
    if status in (500, 502):
        if _has(text, _REQUEST_VALIDATION):
            return _make(RecoveryReason.FORMAT_ERROR, status, text, fallback=True)
        return _make(RecoveryReason.SERVER_ERROR, status, text, retryable=True)
    if status in (503, 529):
        return _make(RecoveryReason.OVERLOADED, status, text, retryable=True)
    if status is not None and 500 <= status < 600:
        return _make(RecoveryReason.SERVER_ERROR, status, text, retryable=True)

    # 4. Message-pattern fallback when status didn't decide.
    if _has(text, _AUTH):
        return _make(RecoveryReason.AUTH, status, text, rotate=True, fallback=True)
    if _has(text, _BILLING):
        return _make(RecoveryReason.BILLING, status, text, rotate=True, fallback=True)
    if _has(text, _RATE_LIMIT):
        return _make(RecoveryReason.RATE_LIMIT, status, text, retryable=True, rotate=True, fallback=True)
    if _has(text, _CONTEXT_OVERFLOW):
        return _make(RecoveryReason.CONTEXT_OVERFLOW, status, text, retryable=True, compress=True)
    if _has(text, _MODEL_NOT_FOUND):
        return _make(RecoveryReason.MODEL_NOT_FOUND, status, text, fallback=True)
    if _has(text, _TIMEOUT):
        return _make(RecoveryReason.TIMEOUT, status, text, retryable=True)

    if status is not None and 400 <= status < 500:
        return _make(RecoveryReason.FORMAT_ERROR, status, text, fallback=True)
    return _make(RecoveryReason.UNKNOWN, status, text, retryable=True)


def parse_retry_after(exc: BaseException) -> float | None:
    """Return a Retry-After delay in seconds if the provider supplied one."""
    headers = getattr(exc, "headers", None)
    if hasattr(headers, "get"):
        value = headers.get("retry-after")
        if value:
            try:
                return float(value)
            except (ValueError, TypeError):
                pass
    _, text = _extract(exc)
    match = re.search(r"retry after (\d+(?:\.\d+)?)\s*s", text) or re.search(r"resets in (\d+)\s*min", text)
    if match:
        value = float(match.group(1))
        return value * 60 if "min" in match.group(0) else value
    return None
