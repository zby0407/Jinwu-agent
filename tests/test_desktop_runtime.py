"""Desktop startup and diagnostics regressions; no live model requests."""

import json
import subprocess
import sys
import threading
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from jw.langgraph_dev import runtime
from jw.langgraph_dev.maintenance import (
    DesktopMaintenanceMiddleware,
    desktop_maintenance,
)
from jw.provider_diagnostics import diagnose_provider_error


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(runtime, "_graphs", {})
    monkeypatch.setattr(runtime, "_states", {})
    monkeypatch.setattr(runtime, "maintenance", False)
    monkeypatch.setattr(runtime, "run_requests", 0)


def test_readiness_does_not_wait_for_concurrent_initialization():
    entered, release = threading.Event(), threading.Event()
    calls = []
    graph = object()

    def build():
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return graph

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(runtime.resolve_graph, "JW", build)
        assert entered.wait(5)
        second = pool.submit(runtime.resolve_graph, "JW", build)
        assert runtime.runtime_status()["canSend"] is False
        release.set()
        assert first.result() is second.result() is graph
    assert len(calls) == 1
    assert runtime.runtime_status()["canSend"] is True


def test_optional_graph_failure_does_not_block_main():
    runtime.resolve_graph("JW", object)

    def fail():
        raise ValueError("synthetic-key-not-for-output")

    with pytest.raises(ValueError):
        runtime.resolve_graph("JW-reproduction", fail)
    assert runtime.runtime_status()["canSend"] is True
    assert "synthetic-key" not in json.dumps(runtime._states)


def test_main_failure_is_visible_without_secret():
    def fail():
        raise RuntimeError("synthetic-key-not-for-output")

    with pytest.raises(RuntimeError):
        runtime.resolve_graph("JW", fail)
    status = runtime.runtime_status()
    assert status["agent"]["state"] == "failed"
    assert status["canSend"] is False
    assert "synthetic-key" not in json.dumps(status)


def test_setup_and_graph_registration_do_not_import_models():
    script = """
import sys
import jw.desktop_setup
import jw.langgraph_dev.main_graph
import jw.langgraph_dev.graphs
import jw.langgraph_dev.reproduction_graph
assert 'jw.agent' not in sys.modules
assert 'jw.config.onboard.wizard' not in sys.modules
assert not any(m.startswith('langchain') for m in sys.modules)
"""
    subprocess.run(
        [sys.executable, "-c", script], check=True, capture_output=True, timeout=15
    )


def test_desktop_launcher_accepts_lowercase_log_level_and_cleans_child(
    tmp_path, monkeypatch
):
    from jw import desktop_server
    from jw.config import JWConfig
    import jw.config
    import jw.langgraph_dev.manager as manager

    monkeypatch.chdir(tmp_path)
    child = SimpleNamespace(poll=lambda: 0, returncode=0)
    stopped = []
    monkeypatch.setattr(
        jw.config, "get_effective_config", lambda: JWConfig(log_level="warning")
    )
    monkeypatch.setattr(jw.config, "apply_config_to_env", lambda _cfg: None)
    monkeypatch.setattr(manager, "start_langgraph_dev", lambda **_kw: child)
    monkeypatch.setattr(manager, "stop_langgraph_dev", stopped.append)
    assert desktop_server.main(["--workdir", str(tmp_path), "--port", "6174"]) == 1
    assert stopped == [child]


@pytest.mark.parametrize(
    "name,status,text,code,retry",
    [
        ("ProxyError", None, "proxy down", "PROXY_CONNECT", True),
        ("SSLEOFError", None, "tls", "TLS_HANDSHAKE", True),
        ("ReadTimeout", None, "timeout", "TIMEOUT", True),
        ("HTTPError", 401, "key", "AUTH", False),
        ("HTTPError", 403, "permission", "AUTH", False),
        ("HTTPError", 429, "quota", "QUOTA", False),
        ("HTTPError", 404, "model", "MODEL_UNAVAILABLE", False),
        ("ConnectError", None, "offline", "CONNECTION", True),
        ("RuntimeError", None, "other", "UNKNOWN", False),
    ],
)
def test_diagnostics_are_stable_and_secret_free(name, status, text, code, retry):
    exc = type(name, (Exception,), {"status_code": status})(text + " synthetic-key")
    outer = RuntimeError("synthetic-key")
    outer.__cause__ = exc
    info = diagnose_provider_error(outer, "direct")
    assert (info["code"], info["retryable"], info["route"]) == (code, retry, "direct")
    assert "synthetic-key" not in json.dumps(info)
    assert "直连可达" not in info["message"]


def test_maintenance_drains_creation_and_allows_existing_run_reads(monkeypatch):
    monkeypatch.setenv("JW_DESKTOP_CONTROL_TOKEN", "isolated-test-token")

    async def run(request):
        return JSONResponse({"inFlight": runtime.run_requests})

    app = Starlette(
        middleware=[Middleware(DesktopMaintenanceMiddleware)],
        routes=[
            Route("/api/desktop-maintenance", desktop_maintenance, methods=["POST"]),
            Route("/threads/t/runs", run, methods=["POST", "GET"]),
        ],
    )
    client = TestClient(app)
    assert client.post("/threads/t/runs").json()["inFlight"] == 1
    assert runtime.run_requests == 0
    headers = {"x-jw-desktop-token": "isolated-test-token"}
    assert (
        client.post(
            "/api/desktop-maintenance", headers=headers, json={"enabled": True}
        ).json()["inFlight"]
        == 0
    )
    assert client.post("/threads/t/runs").status_code == 503
    assert client.get("/threads/t/runs").status_code == 200
    client.post("/api/desktop-maintenance", headers=headers, json={"enabled": False})
    assert client.post("/threads/t/runs").status_code == 200
