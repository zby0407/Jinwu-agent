from __future__ import annotations

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from jw.cli import reproduction as command
from jw.cli._app import app
from jw.langgraph_dev import manager


def _ignore_config_env(monkeypatch):
    monkeypatch.setattr(command, "apply_config_to_env", lambda config: None)


def _config(tmp_path, *, key="key"):
    return SimpleNamespace(
        dashscope_api_key=key,
        dangerous_mode=False,
        default_workdir=str(tmp_path),
        langgraph_dev_port=6174,
        langgraph_dev_file_persistence=True,
        langgraph_dev_jobs_per_worker=4,
    )


def _submitted():
    return {
        "schema_version": "jw-reproduction-launch-v1",
        "suite_id": "solar-h1-h2-v1",
        "batch_id": "batch",
        "status": "submitted",
        "model": {"name": "qwen3.7-max", "provider": "dashscope"},
        "runs": [
            {
                "case_id": case,
                "thread_id": f"thread-{case}",
                "run_id": f"run-{case}",
                "workspace": f"workspace-{case}",
                "prompt_sha256": "a" * 64,
            }
            for case in ("H1", "H2")
        ],
        "errors": [],
    }


def test_reproduce_starts_full_backend_waits_and_stops(monkeypatch, tmp_path):
    _ignore_config_env(monkeypatch)
    process = object()
    events = []
    monkeypatch.setattr(command, "get_effective_config", lambda: _config(tmp_path))
    monkeypatch.setattr(manager, "is_langgraph_dev_running", lambda **kwargs: False)
    monkeypatch.setattr(manager, "_is_port_occupied", lambda port: False)

    def start(**kwargs):
        events.append(("start", kwargs))
        return process

    monkeypatch.setattr(manager, "start_langgraph_dev", start)
    monkeypatch.setattr(
        manager, "stop_langgraph_dev", lambda value: events.append(("stop", value))
    )
    monkeypatch.setattr(command, "_submit", lambda url: (201, _submitted()))
    monkeypatch.setattr(
        command, "_wait_for_terminal", lambda url, runs: events.append(("wait", runs))
    )

    result = CliRunner().invoke(app, ["reproduce"])
    assert result.exit_code == 0, result.output
    assert events[0][0] == "start"
    assert events[0][1]["deploy_mode"] is True
    assert events[1][0] == "wait"
    assert events[-1] == ("stop", process)
    assert "不代表实验或科研审查成功" in result.output


def test_reproduce_detach_requires_and_reuses_external_backend(monkeypatch, tmp_path):
    _ignore_config_env(monkeypatch)
    monkeypatch.setattr(command, "get_effective_config", lambda: _config(tmp_path))
    monkeypatch.setattr(manager, "_is_port_occupied", lambda port: False)
    monkeypatch.setattr(manager, "is_langgraph_dev_running", lambda **kwargs: False)
    missing = CliRunner().invoke(app, ["reproduce", "--detach"])
    assert missing.exit_code == 2
    assert "仅允许复用" in missing.output

    monkeypatch.setattr(manager, "is_langgraph_dev_running", lambda **kwargs: True)
    monkeypatch.setattr(command, "_verify_running_workspace", lambda workspace: None)
    monkeypatch.setattr(command, "_submit", lambda url: (201, _submitted()))
    monkeypatch.setattr(
        command,
        "_wait_for_terminal",
        lambda *args: (_ for _ in ()).throw(AssertionError("detach must not wait")),
    )
    reused = CliRunner().invoke(app, ["reproduce", "--detach"])
    assert reused.exit_code == 0, reused.output
    assert "外部后端将继续运行" in reused.output


def test_reproduce_rejects_missing_key_and_partial_submission(monkeypatch, tmp_path):
    _ignore_config_env(monkeypatch)
    monkeypatch.setattr(
        command, "get_effective_config", lambda: _config(tmp_path, key="")
    )
    missing = CliRunner().invoke(app, ["reproduce"])
    assert missing.exit_code == 2
    assert "DASHSCOPE_API_KEY" in missing.output

    monkeypatch.setattr(command, "get_effective_config", lambda: _config(tmp_path))
    monkeypatch.setattr(manager, "is_langgraph_dev_running", lambda **kwargs: True)
    monkeypatch.setattr(manager, "_is_port_occupied", lambda port: True)
    monkeypatch.setattr(command, "_verify_running_workspace", lambda workspace: None)
    body = _submitted()
    body["status"] = "partial"
    body["runs"] = body["runs"][:1]
    body["errors"] = [{"case_id": "H2", "stage": "submit", "message": "failed"}]
    monkeypatch.setattr(command, "_submit", lambda url: (207, body))
    partial = CliRunner().invoke(app, ["reproduce", "--detach"])
    assert partial.exit_code == 2
    assert "H2/submit" in partial.output


def test_reproduce_ctrl_c_stops_owned_backend(monkeypatch, tmp_path):
    _ignore_config_env(monkeypatch)
    process = object()
    stopped = []
    monkeypatch.setattr(command, "get_effective_config", lambda: _config(tmp_path))
    monkeypatch.setattr(manager, "is_langgraph_dev_running", lambda **kwargs: False)
    monkeypatch.setattr(manager, "_is_port_occupied", lambda port: False)
    monkeypatch.setattr(manager, "start_langgraph_dev", lambda **kwargs: process)
    monkeypatch.setattr(manager, "stop_langgraph_dev", stopped.append)
    monkeypatch.setattr(command, "_submit", lambda url: (201, _submitted()))
    monkeypatch.setattr(
        command,
        "_wait_for_terminal",
        lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    result = CliRunner().invoke(app, ["reproduce"])
    assert result.exit_code == 130
    assert stopped == [process]
    assert "Ctrl+C" in result.output


def test_submit_reports_unsupported_or_unavailable_backend(monkeypatch):
    class Response:
        status_code = 404

        def json(self):
            return {"detail": "not found"}

    monkeypatch.setattr(command.httpx, "post", lambda *args, **kwargs: Response())
    with pytest.raises(RuntimeError, match="不支持 reproduce 接口"):
        command._submit("http://localhost:6174")

    def unavailable(*args, **kwargs):
        raise command.httpx.ConnectError("offline")

    monkeypatch.setattr(command.httpx, "post", unavailable)
    with pytest.raises(command.httpx.ConnectError, match="offline"):
        command._submit("http://localhost:6174")


def test_running_workspace_mismatch_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(
        manager,
        "_read_workspace_sidecar",
        lambda: {"workspace": str(tmp_path / "other")},
    )
    with pytest.raises(ValueError, match="不一致"):
        command._verify_running_workspace(tmp_path)


def test_wait_tracks_both_runs_to_terminal(monkeypatch):
    statuses = {
        "run-H1": iter(("running", "success")),
        "run-H2": iter(("pending", "error")),
    }

    class Runs:
        def get(self, *, thread_id, run_id):
            return {"status": next(statuses[run_id])}

    monkeypatch.setattr(
        command,
        "get_langgraph_sync_client",
        lambda **kwargs: SimpleNamespace(runs=Runs()),
    )
    monkeypatch.setattr(command.time, "sleep", lambda seconds: None)
    command._wait_for_terminal("http://localhost:6174", _submitted()["runs"])
