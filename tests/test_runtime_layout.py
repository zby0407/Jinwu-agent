"""Tests for source-checkout and desktop-bundle resource resolution."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from jw import runtime_layout

ROOT = Path(__file__).resolve().parents[1]


def test_source_checkout_defaults_to_repository_root(monkeypatch) -> None:
    monkeypatch.delenv("JW_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("JW_WEBUI_DIST", raising=False)

    assert runtime_layout.project_root() == ROOT
    assert runtime_layout.src_root() == ROOT / "src"
    assert runtime_layout.webui_dist_file() == ROOT / "webui" / "dist" / "server.js"


def test_project_root_override_controls_src_and_default_webui(
    tmp_path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("JW_PROJECT_ROOT", str(project))
    monkeypatch.delenv("JW_WEBUI_DIST", raising=False)

    assert runtime_layout.project_root() == project
    assert runtime_layout.src_root() == project / "src"
    assert runtime_layout.webui_dist_file() == project / "webui" / "dist" / "server.js"


def test_webui_override_accepts_a_directory(tmp_path, monkeypatch) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    monkeypatch.setenv("JW_WEBUI_DIST", str(dist))

    assert runtime_layout.webui_dist_file() == dist / "server.js"


def test_webui_override_accepts_the_server_file(tmp_path, monkeypatch) -> None:
    server = tmp_path / "bundle" / "server.js"
    server.parent.mkdir()
    server.write_text("// standalone entrypoint\n", encoding="utf-8")
    monkeypatch.setenv("JW_WEBUI_DIST", str(server))

    assert runtime_layout.webui_dist_file() == server


def test_node_binary_prefers_bundled_runtime(tmp_path, monkeypatch) -> None:
    node = tmp_path / "node"
    node.write_text("#!/bin/sh\n", encoding="utf-8")
    node.chmod(0o755)
    monkeypatch.setenv("JW_NODE_BIN", str(node))

    assert runtime_layout.node_binary() == str(node)


def test_node_binary_rejects_missing_bundled_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JW_NODE_BIN", str(tmp_path / "missing-node"))

    assert runtime_layout.node_binary() is None


def test_node_binary_falls_back_to_path(monkeypatch) -> None:
    monkeypatch.delenv("JW_NODE_BIN", raising=False)
    monkeypatch.setattr(
        runtime_layout.shutil, "which", lambda command: f"/fake/{command}"
    )

    assert runtime_layout.node_binary() == "/fake/node"


def test_langgraph_command_reads_explicit_json(monkeypatch) -> None:
    command = [sys.executable, "-m", "langgraph_cli"]
    monkeypatch.setenv("JW_LANGGRAPH_COMMAND_JSON", json.dumps(command))

    assert runtime_layout.langgraph_command() == command


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "{}",
        "[]",
        '["python", 3]',
        '[""]',
    ],
)
def test_langgraph_command_rejects_malformed_overrides(monkeypatch, raw) -> None:
    monkeypatch.setenv("JW_LANGGRAPH_COMMAND_JSON", raw)

    with pytest.raises(ValueError, match="JW_LANGGRAPH_COMMAND_JSON"):
        runtime_layout.langgraph_command()


def test_langgraph_command_is_none_without_override(monkeypatch) -> None:
    monkeypatch.delenv("JW_LANGGRAPH_COMMAND_JSON", raising=False)

    assert runtime_layout.langgraph_command() is None
