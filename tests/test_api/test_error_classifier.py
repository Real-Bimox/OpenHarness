"""Tests for the typed error classifier."""

from __future__ import annotations

from openharness.api.error_classifier import (
    ClassifiedError,
    RecoveryReason,
    classify_error,
    parse_retry_after,
)


class _StatusError(Exception):
    def __init__(self, status_code, message="", body=None, headers=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body
        self.headers = headers or {}


def test_rate_limit_rotates_and_falls_back():
    c = classify_error(_StatusError(429, "rate limit exceeded"))
    assert c.reason is RecoveryReason.RATE_LIMIT
    assert c.retryable and c.should_rotate_credential and c.should_fallback


def test_auth_rotates_not_retryable():
    c = classify_error(_StatusError(401, "invalid api key"))
    assert c.reason is RecoveryReason.AUTH
    assert c.should_rotate_credential and c.should_fallback and not c.retryable


def test_billing_403():
    c = classify_error(_StatusError(403, "your credit balance is too low"))
    assert c.reason is RecoveryReason.BILLING
    assert c.should_fallback


def test_context_overflow_compresses():
    c = classify_error(_StatusError(400, "maximum context length exceeded"))
    assert c.reason is RecoveryReason.CONTEXT_OVERFLOW
    assert c.should_compress and c.retryable


def test_validation_before_overflow():
    # contains "max_tokens" overflow-ish text but is a parameter error
    c = classify_error(_StatusError(400, "Unsupported parameter: 'max_tokens' for this model"))
    assert c.reason is RecoveryReason.FORMAT_ERROR
    assert not c.should_compress


def test_content_policy_before_status():
    c = classify_error(_StatusError(400, "Your request was flagged by our safety system"))
    assert c.reason is RecoveryReason.CONTENT_POLICY
    assert c.should_fallback and not c.retryable


def test_payload_too_large():
    c = classify_error(_StatusError(413, "payload too large"))
    assert c.reason is RecoveryReason.PAYLOAD_TOO_LARGE
    assert c.should_compress


def test_overloaded_and_server_error():
    assert classify_error(_StatusError(529, "overloaded")).reason is RecoveryReason.OVERLOADED
    assert classify_error(_StatusError(500, "internal error")).reason is RecoveryReason.SERVER_ERROR
    assert classify_error(_StatusError(503, "unavailable")).retryable


def test_model_not_found_404():
    c = classify_error(_StatusError(404, "the model `x` does not exist: model not found"))
    assert c.reason is RecoveryReason.MODEL_NOT_FOUND
    assert c.should_fallback


def test_generic_404_is_retryable_unknown():
    c = classify_error(_StatusError(404, "not found"))
    assert c.reason is RecoveryReason.UNKNOWN and c.retryable


def test_transport_errors_without_status():
    assert classify_error(TimeoutError("timed out")).reason is RecoveryReason.TIMEOUT
    assert classify_error(ConnectionError("server disconnected")).reason is RecoveryReason.TIMEOUT
    assert classify_error(ConnectionError("server disconnected")).retryable


def test_cause_chain_walk():
    inner = _StatusError(429, "rate limit")
    outer = RuntimeError("wrapper")
    outer.__cause__ = inner
    c = classify_error(outer)
    assert c.reason is RecoveryReason.RATE_LIMIT


def test_retry_after_header_and_message():
    assert parse_retry_after(_StatusError(429, "x", headers={"retry-after": "12"})) == 12.0
    assert parse_retry_after(_StatusError(429, "please retry after 30s")) == 30.0
    assert parse_retry_after(_StatusError(429, "resets in 5 min")) == 300.0
    assert parse_retry_after(_StatusError(429, "no hint")) is None


def test_flags_are_self_consistent():
    # every classification is a valid ClassifiedError with bool flags
    for exc in (_StatusError(429, "x"), _StatusError(400, "bad"), TimeoutError("t")):
        c = classify_error(exc)
        assert isinstance(c, ClassifiedError)
        assert isinstance(c.retryable, bool)
