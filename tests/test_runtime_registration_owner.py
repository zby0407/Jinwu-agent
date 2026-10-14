"""Exiting a client must preserve another live deployment's registration."""

import json
from types import SimpleNamespace

import pytest

from jw.langgraph_dev import manager


@pytest.mark.parametrize("pid_file_exists", [True, False])
@pytest.mark.parametrize("old_handle", [True, False])
def test_external_live_registration_survives_unowned_cleanup(
    tmp_path, monkeypatch, pid_file_exists, old_handle
):
    paths = manager.LanggraphRuntimePaths.for_directory(tmp_path)
    monkeypatch.setattr(manager, "RUNTIME", paths)
    monkeypatch.setattr(manager, "_PROCESS", None)
    monkeypatch.setattr(manager.psutil, "pid_exists", lambda pid: pid == 123)
    if pid_file_exists:
        paths.pid_file.write_text("123", encoding="utf-8")
    paths.workspace_sidecar.write_text(
        json.dumps({"workspace": str(tmp_path), "pid": 123}), encoding="utf-8"
    )
    before = paths.workspace_sidecar.read_bytes()
    old = SimpleNamespace(pid=99, poll=lambda: 0) if old_handle else None
    manager.stop_langgraph_dev(old)
    assert paths.workspace_sidecar.read_bytes() == before
    if pid_file_exists:
        assert paths.pid_file.read_text() == "123"


@pytest.mark.parametrize("owned_handle", [True, False])
def test_owned_or_dead_registration_still_cleans_up(
    tmp_path, monkeypatch, owned_handle
):
    paths = manager.LanggraphRuntimePaths.for_directory(tmp_path)
    monkeypatch.setattr(manager, "RUNTIME", paths)
    monkeypatch.setattr(manager, "_PROCESS", None)
    monkeypatch.setattr(manager.psutil, "pid_exists", lambda pid: False)
    paths.pid_file.write_text("123", encoding="utf-8")
    paths.workspace_sidecar.write_text(
        json.dumps({"workspace": str(tmp_path), "pid": 123}), encoding="utf-8"
    )
    own = SimpleNamespace(pid=123, poll=lambda: 0) if owned_handle else None
    manager.stop_langgraph_dev(own)
    assert not paths.workspace_sidecar.exists()
    assert not paths.pid_file.exists()
