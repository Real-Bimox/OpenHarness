from __future__ import annotations

import os
import platform
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

_READINESS_TIMEOUT = 5.0


class BindError(RuntimeError):
    pass


@dataclass
class HealthServerHandle:
    server: Any
    thread: threading.Thread
    host: str
    port: int

    def stop(self, timeout: float = 5.0) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=timeout)


def _version() -> str:
    try:
        from openharness.cli import __version__

        return __version__
    except Exception:
        return "unknown"


@asynccontextmanager
async def _lifespan(app: Any):
    yield


def create_health_app(store: Any | None = None) -> Any:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI(title="OpenHarness Health", lifespan=_lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "platform": "openharness", "version": _version()}

    @app.get("/health/detailed")
    async def health_detailed() -> Any:
        from openharness.diagnostics.snapshot import build_status

        doc = build_status(probe=True)
        probe = doc.get("thread_probe") or {}
        top_status = "ok" if probe.get("status") == "ok" else "degraded"
        http_status = 200 if top_status == "ok" else 503
        body = {**doc, "status": top_status, "platform": "openharness"}
        return JSONResponse(content=body, status_code=http_status)

    @app.get("/api/status")
    async def api_status() -> dict[str, Any]:
        from openharness.diagnostics.snapshot import build_status

        doc = build_status(probe=False)
        if store is not None:
            state = store.get()
            doc["app_state"] = {
                "model": state.model,
                "provider": state.provider,
                "auth_status": state.auth_status,
                "mcp_connected": state.mcp_connected,
                "mcp_failed": state.mcp_failed,
            }
        return doc

    @app.get("/api/system/stats")
    async def system_stats() -> dict[str, Any]:
        return _system_stats()

    @app.get("/v1/capabilities")
    async def capabilities() -> dict[str, Any]:
        return _capabilities()

    return app


def start_health_server_background(
    host: str = "127.0.0.1",
    port: int = 8642,
    store: Any | None = None,
) -> HealthServerHandle:
    import socket
    import uvicorn

    app = create_health_app(store=store)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    startup_errors: list[BaseException] = []

    def _run_server() -> None:
        try:
            server.run()
        except BaseException as exc:
            startup_errors.append(exc)

    thread = threading.Thread(target=_run_server, daemon=True, name="health-server")
    thread.start()
    handle = HealthServerHandle(server=server, thread=thread, host=host, port=port)
    deadline = time.monotonic() + _READINESS_TIMEOUT
    while time.monotonic() < deadline:
        actual_port = _discover_bound_port(server)
        if actual_port is not None:
            handle.port = actual_port
            return handle
        if not thread.is_alive():
            break
        if port != 0:
            try:
                with socket.create_connection((host, port), timeout=0.05):
                    return handle
            except (ConnectionRefusedError, OSError):
                pass
        time.sleep(0.05)
    handle.stop(timeout=1.0)
    if not thread.is_alive():
        if startup_errors:
            raise BindError(f"Health server failed to bind {host}:{port}: {startup_errors[-1]}")
        raise BindError(f"Health server failed to bind {host}:{port}")
    raise BindError(f"Health server did not bind within {_READINESS_TIMEOUT}s")


def _discover_bound_port(server: Any) -> int | None:
    for bound_server in getattr(server, "servers", None) or []:
        for sock in getattr(bound_server, "sockets", None) or []:
            try:
                sockname = sock.getsockname()
            except OSError:
                continue
            if isinstance(sockname, tuple) and len(sockname) >= 2:
                return int(sockname[1])
    return None


def _system_stats() -> dict[str, Any]:
    stats: dict[str, Any] = {
        "os": platform.system(),
        "os_release": platform.release(),
        "arch": platform.machine(),
        "hostname": platform.node(),
        "python_version": platform.python_version(),
        "openharness_version": _version(),
        "cpu_count": os.cpu_count(),
        "psutil": False,
    }
    try:
        import psutil

        stats["psutil"] = True
        proc = psutil.Process()
        stats["memory"] = {
            "total": psutil.virtual_memory().total,
            "available": psutil.virtual_memory().available,
            "percent": psutil.virtual_memory().percent,
        }
        disk_path = os.getcwd() if platform.system() == "Windows" else "/"
        disk = psutil.disk_usage(disk_path)
        stats["disk"] = {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": disk.percent,
        }
        stats["cpu_percent"] = psutil.cpu_percent(interval=0)
        try:
            stats["load_avg"] = list(os.getloadavg())
        except (AttributeError, OSError):
            stats["load_avg"] = None
        stats["process"] = {
            "pid": os.getpid(),
            "rss": proc.memory_info().rss,
            "create_time": proc.create_time(),
            "num_threads": proc.num_threads(),
        }
    except ImportError:
        pass
    except Exception:
        pass
    return stats


def _capabilities() -> dict[str, Any]:
    return {
        "object": "openharness.capabilities",
        "platform": "openharness",
        "version": _version(),
        "features": {
            "headless_protocol": True,
            "mcp_server": True,
            "multi_agent": True,
            "skill_learning_loop": True,
            "conversation_search": True,
            "cron_scheduler": True,
        },
        "endpoints": {
            "health": {"method": "GET", "path": "/health"},
            "health_detailed": {"method": "GET", "path": "/health/detailed"},
            "status": {"method": "GET", "path": "/api/status"},
            "system_stats": {"method": "GET", "path": "/api/system/stats"},
            "capabilities": {"method": "GET", "path": "/v1/capabilities"},
        },
    }
