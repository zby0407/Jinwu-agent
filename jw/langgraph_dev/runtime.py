"""Process-local graph initialization and secret-free readiness reporting."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

_lock = threading.RLock()
_status_lock = threading.Lock()
_graphs: dict[str, Any] = {}
_states: dict[str, dict] = {}
_started = time.monotonic()
_logger = logging.getLogger(__name__)
maintenance = False
run_requests = 0


def resolve_graph(name: str, builder: Callable[[], Any]) -> Any:
    # Legacy factories share MCP/model caches, so serialize construction only.
    # Status readers never wait for model construction to finish.
    with _lock:
        if name in _graphs:
            return _graphs[name]
        start = time.monotonic()
        with _status_lock:
            _states[name] = {"state": "warming", "elapsedMs": 0}
        try:
            graph = builder()
        except Exception as exc:
            with _status_lock:
                _states[name] = {
                    "state": "failed",
                    "errorType": type(exc).__name__,
                    "message": "智能体初始化失败，请检查 API 配置及后台日志。",
                    "elapsedMs": round((time.monotonic() - start) * 1000),
                }
            _logger.error(
                "graph initialization failed: %s (%s)", name, type(exc).__name__
            )
            raise
        _graphs[name] = graph
        elapsed = round((time.monotonic() - start) * 1000)
        with _status_lock:
            _states[name] = {"state": "ready", "elapsedMs": elapsed}
        _logger.info("startup phase=graph-ready graph=%s duration_ms=%d", name, elapsed)
        return graph


def runtime_status() -> dict:
    with _status_lock:
        main = dict(_states.get("JW", {"state": "warming", "elapsedMs": 0}))
    return {
        "version": 1,
        "backendReady": True,
        "canSend": main["state"] == "ready" and not maintenance,
        "agent": main,
        "maintenance": maintenance,
        "elapsedMs": round((time.monotonic() - _started) * 1000),
        "modelConnectivity": "untested",
    }
