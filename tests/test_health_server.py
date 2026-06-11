from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

import httpx
import urllib.request
from unittest.mock import patch

from openharness.api.health_server import (
    BindError,
    create_health_app,
    start_health_server_background,
)


def _require_local_sockets() -> None:
    import socket

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", 0))
        finally:
            sock.close()
    except OSError as exc:
        pytest.skip(f"local loopback sockets unavailable: {exc}")


async def _get(path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=create_health_app(**kwargs))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path)


async def test_health_liveness():
    r = await _get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["platform"] == "openharness"
    assert "version" in body


async def test_health_detailed_returns_build_status_keys():
    r = await _get("/health/detailed")
    body = r.json()
    assert "thread_probe" in body
    assert "status_schema_version" in body
    assert "generated_at" in body
    assert body["platform"] == "openharness"


async def test_health_detailed_200_on_ok_probe():
    r = await _get("/health/detailed")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_health_detailed_503_on_probe_timeout():
    fake_status = {
        "thread_probe": {"status": "timeout", "duration_ms": 2000.0},
        "recorder": {"enabled": True},
        "status_schema_version": 1,
    }
    with patch("openharness.diagnostics.snapshot.build_status", return_value=fake_status):
        r = await _get("/health/detailed")
    assert r.status_code == 503
    assert r.json()["status"] == "degraded"
    assert "thread_probe" in r.json()


async def test_health_detailed_200_with_recorder_disabled():
    fake_status = {
        "thread_probe": {"status": "ok", "duration_ms": 0.5},
        "recorder": {"enabled": False},
        "status_schema_version": 1,
    }
    with patch("openharness.diagnostics.snapshot.build_status", return_value=fake_status):
        r = await _get("/health/detailed")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_api_status_without_store():
    r = await _get("/api/status")
    assert r.status_code == 200
    assert "app_state" not in r.json()


async def test_api_status_with_store():
    from openharness.state import AppState, AppStateStore

    store = AppStateStore(AppState(model="test-model", permission_mode="default", theme="default"))
    r = await _get("/api/status", store=store)
    assert r.status_code == 200
    assert r.json()["app_state"]["model"] == "test-model"
    assert "provider" in r.json()["app_state"]


async def test_system_stats():
    r = await _get("/api/system/stats")
    assert r.status_code == 200
    body = r.json()
    assert "os" in body
    assert "psutil" in body
    assert "cpu_count" in body
    assert "openharness_version" in body


async def test_system_stats_cpu_percent_is_number():
    r = await _get("/api/system/stats")
    body = r.json()
    if body["psutil"]:
        assert isinstance(body["cpu_percent"], (int, float))
        assert not isinstance(body["cpu_percent"], tuple)


async def test_capabilities():
    r = await _get("/v1/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "openharness.capabilities"
    assert body["platform"] == "openharness"
    assert len(body["endpoints"]) == 5
    assert "version" in body


def test_background_thread_reachable():
    _require_local_sockets()
    handle = start_health_server_background(port=0)
    try:
        assert handle.port > 0
        assert handle.thread.is_alive()
        resp = urllib.request.urlopen(f"http://127.0.0.1:{handle.port}/health", timeout=5)
        assert resp.status == 200
    finally:
        handle.stop()


def test_background_bind_failure():
    import socket

    _require_local_sockets()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    conflict_port = sock.getsockname()[1]
    try:
        with pytest.raises(BindError):
            start_health_server_background(port=conflict_port)
    finally:
        sock.close()


_SENSITIVE_STRINGS = [
    "sk-ant-api03-test-key-do-not-use",
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_testGitHubToken",
]


async def test_no_secrets_in_endpoints():
    for path in ("/health", "/health/detailed", "/api/status", "/api/system/stats", "/v1/capabilities"):
        body = (await _get(path)).text
        for secret in _SENSITIVE_STRINGS:
            assert secret not in body, f"Secret leaked in {path}"


async def test_seeded_api_key_absent_from_status():
    fake_status = {
        "status_schema_version": 1,
        "auth": {"active_profile": "test", "provider": "anthropic", "model": "claude-sonnet-4-6"},
        "recorder": {"enabled": True},
        "thread_probe": None,
    }
    with patch("openharness.diagnostics.snapshot.build_status", return_value=fake_status):
        for path in ("/api/status", "/health/detailed"):
            r = await _get(path)
            for secret in _SENSITIVE_STRINGS:
                assert secret not in r.text, f"Secret leaked in {path}"
