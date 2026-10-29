"""Offline environment contracts; no model calls or host experiment execution."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from automatic_experiment import executor  # noqa: E402


def test_windows_uses_registered_default_or_explicit_distribution(monkeypatch):
    monkeypatch.setattr(executor, "IS_WINDOWS", True)
    monkeypatch.delenv("AE_WSL_DISTRO", raising=False)
    assert executor._wsl_prefix() == ["wsl.exe", "--"]
    monkeypatch.setenv("AE_WSL_DISTRO", "Research Sandbox")
    assert executor._wsl_prefix() == ["wsl.exe", "-d", "Research Sandbox", "--"]
    monkeypatch.setattr(executor, "IS_WINDOWS", False)
    assert executor._wsl_prefix() == []


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-8"])
def test_wsl_diagnostics_are_readable(encoding):
    message = "未安装隔离运行环境。"
    assert executor._decode_probe_output(message.encode(encoding)) == message


@pytest.mark.parametrize(
    "failure,expected",
    [(FileNotFoundError("missing"), 127), (subprocess.TimeoutExpired("wsl", 2), 124)],
)
def test_unavailable_runtime_returns_probe_failure(monkeypatch, failure, expected):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(executor.subprocess, "run", fail)
    result = executor._run_wsl(["bwrap", "--version"], timeout=2)
    assert result.returncode == expected
    assert result.stderr


def test_probe_uses_selected_distribution_and_returns_decoded_output(monkeypatch):
    monkeypatch.setattr(executor, "IS_WINDOWS", True)
    monkeypatch.setenv("AE_WSL_DISTRO", "Sandbox-A")
    captured = []

    def run(command, **kwargs):
        captured.append(command)
        assert "text" not in kwargs
        assert "DEEPSEEK_API_KEY" not in kwargs["env"]
        return subprocess.CompletedProcess(
            command, 50, "未安装。".encode("utf-16-le"), b""
        )

    monkeypatch.setattr(executor.subprocess, "run", run)
    result = executor._run_wsl(["bwrap", "--version"])
    assert captured == [["wsl.exe", "-d", "Sandbox-A", "--", "bwrap", "--version"]]
    assert result.stdout == "未安装。"


def _snapshot(ready=False):
    return {
        "ready": ready,
        "execution_backend": "windows_wsl_bridge",
        "wsl_distro": None,
        "diagnostic": None if ready else "Isolated runtime is not installed",
    }


def test_doctor_missing_runtime_does_not_create_or_execute_probe(monkeypatch):
    monkeypatch.setattr(executor, "IS_MACOS", False)
    monkeypatch.setattr(executor, "IS_WINDOWS", True)
    monkeypatch.setattr(executor, "runtime_environment_snapshot", _snapshot)
    monkeypatch.setattr(executor, "_linux_runtime_files", lambda: {"ready": False})
    monkeypatch.setattr(
        executor, "active_runs_root", lambda: pytest.fail("must not create a run")
    )
    monkeypatch.setattr(executor.shutil, "which", lambda name: None)
    result = executor.doctor()
    assert result["status"] == "boundary_blocked"
    assert result["sandbox_probe_executed"] is False
    assert result["checks"]["sandbox_probe"]["ready"] is False
    assert result["detected_alternative_launchers"] == {
        "docker": False,
        "podman": False,
    }


def test_inspection_never_claims_verified_isolation(monkeypatch):
    monkeypatch.setattr(executor, "IS_MACOS", False)
    monkeypatch.setattr(
        executor, "runtime_environment_snapshot", lambda: _snapshot(True)
    )
    monkeypatch.setattr(executor, "_linux_runtime_files", lambda: {"ready": True})
    monkeypatch.setattr(
        executor, "active_runs_root", lambda: pytest.fail("read-only mode")
    )
    result = executor.doctor(probe=False)
    assert result["status"] == "inspection_only"
    assert not result["checks"]["sandbox_probe"]["ready"]


def test_locked_package_probe_uses_one_directory_and_selected_python(monkeypatch):
    monkeypatch.setenv("AE_SANDBOX_PYTHON", "/usr/bin/python3.12")
    monkeypatch.delenv("AE_SANDBOX_SITE_PACKAGES", raising=False)
    seen = []

    def run(args, **kwargs):
        seen.append(args)
        if "import site,json" in args[-1]:
            body = ["/empty/site-packages", "/locked/site-packages"]
        elif "/empty/site-packages" in args[-1]:
            body = {}
        else:
            body = executor.EXPECTED_PACKAGES
        return subprocess.CompletedProcess(args, 0, json.dumps(body), "")

    monkeypatch.setattr(executor, "_run_wsl", run)
    path, installed, diagnostic = executor._locked_site_packages()
    assert path == "/locked/site-packages"
    assert installed == executor.EXPECTED_PACKAGES
    assert diagnostic is None
    assert all(args[0] == "/usr/bin/python3.12" for args in seen)


def test_locked_packages_reject_cross_directory_version_mix(monkeypatch):
    monkeypatch.delenv("AE_SANDBOX_SITE_PACKAGES", raising=False)
    names = list(executor.EXPECTED_PACKAGES)

    def run(args, **kwargs):
        if "import site,json" in args[-1]:
            body = ["/site/one", "/site/two"]
        else:
            chosen = names[:3] if "/site/one" in args[-1] else names[3:]
            body = {name: executor.EXPECTED_PACKAGES[name] for name in chosen}
        return subprocess.CompletedProcess(args, 0, json.dumps(body), "")

    monkeypatch.setattr(executor, "_run_wsl", run)
    _, installed, diagnostic = executor._locked_site_packages()
    assert installed != executor.EXPECTED_PACKAGES
    assert diagnostic


def test_host_package_path_cannot_substitute_for_linux_runtime(monkeypatch):
    monkeypatch.setenv("AE_SANDBOX_SITE_PACKAGES", "C:/host/site-packages")
    monkeypatch.setattr(
        executor, "_run_wsl", lambda *a, **k: pytest.fail("invalid path")
    )
    assert (
        executor._locked_site_packages()[2]
        == "AE_SANDBOX_SITE_PACKAGES must be an absolute Linux path"
    )


def test_unready_runtime_stops_before_launching_worker(monkeypatch, tmp_path):
    attempt = tmp_path / "attempts" / "attempt-001"
    attempt.mkdir(parents=True)
    (attempt / "attempt.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(executor, "verify_attempt_immutable", lambda *args: None)
    monkeypatch.setattr(executor, "runtime_environment_snapshot", _snapshot)
    monkeypatch.setattr(
        executor.subprocess, "Popen", lambda *a, **k: pytest.fail("no host fallback")
    )
    with pytest.raises(executor.ExecutionError, match="locked execution environment"):
        executor.execute_attempt(
            tmp_path,
            {"current_attempt": "attempt-001"},
            {"resource_budget": {"gpu_count": 0}},
            "attempt-001",
        )
    assert not (attempt / "execution.json").exists()


def _runtime_sources(tmp_path, monkeypatch, runner_bytes):
    source = tmp_path / "src" / "automatic_experiment"
    source.mkdir(parents=True)
    runner = source / "sandbox_runner.sh"
    runner.write_bytes(runner_bytes)
    (source / "sandbox_worker.py").write_bytes(b"# trusted worker fixture\n")
    monkeypatch.setattr(executor, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(executor, "_backend_path", lambda path: "/selected/runner.sh")
    return runner


@pytest.mark.parametrize("runner_bytes", [b"#!/bin/bash\r\nexit 0\r\n", b"exit 0\r"])
def test_runner_cr_bytes_are_rejected_before_shell_probe(
    tmp_path, monkeypatch, runner_bytes
):
    _runtime_sources(tmp_path, monkeypatch, runner_bytes)
    monkeypatch.setattr(
        executor, "_run_wsl", lambda *a, **kw: pytest.fail("CR runner must be rejected")
    )
    result = executor._linux_runtime_files()
    assert not result["ready"]
    assert result["runner_lf_only"] is False
    assert result["runner_syntax"]["executed"] is False


@pytest.mark.parametrize("return_code", [0, 2])
def test_runner_syntax_is_checked_by_selected_linux_shell(
    tmp_path, monkeypatch, return_code
):
    runner = _runtime_sources(tmp_path, monkeypatch, b"#!/bin/bash\nexit 0\n")
    calls = []

    def parse(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(
            args, return_code, "", "bad syntax" if return_code else ""
        )

    monkeypatch.setattr(executor, "_run_wsl", parse)
    result = executor._linux_runtime_files()
    assert calls == [["bash", "-n", "/selected/runner.sh"]]
    assert result["runner_sha256"] == executor.file_sha256(runner)
    assert result["runner_lf_only"] is True
    assert result["runner_syntax"]["return_code"] == return_code
    assert result["ready"] is (return_code == 0)


def test_doctor_ready_dependencies_do_not_hide_invalid_runner(monkeypatch):
    monkeypatch.setattr(executor, "IS_MACOS", False)
    monkeypatch.setattr(
        executor, "runtime_environment_snapshot", lambda: _snapshot(True)
    )
    files = {"ready": False, "runner_lf_only": False, "runner_syntax": {"ready": False}}
    monkeypatch.setattr(executor, "_linux_runtime_files", lambda: files)
    monkeypatch.setattr(
        executor,
        "active_runs_root",
        lambda: pytest.fail("invalid runner must not execute probe"),
    )
    result = executor.doctor()
    assert result["status"] == "boundary_blocked"
    assert result["checks"]["runtime_prerequisites"]["ready"] is True
    assert result["checks"]["runtime_files"] == files
    assert result["checks"]["sandbox_probe"]["skipped"] is True


def test_executor_rejects_invalid_runner_even_without_doctor(monkeypatch, tmp_path):
    attempt = tmp_path / "attempts" / "attempt-001"
    attempt.mkdir(parents=True)
    (attempt / "attempt.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(executor, "IS_MACOS", False)
    monkeypatch.setattr(executor, "IS_WINDOWS", True)
    monkeypatch.setattr(executor, "verify_attempt_immutable", lambda *a: None)
    monkeypatch.setattr(
        executor, "runtime_environment_snapshot", lambda: _snapshot(True)
    )
    monkeypatch.setattr(executor, "_linux_runtime_files", lambda: {"ready": False})
    monkeypatch.setattr(
        executor.subprocess,
        "Popen",
        lambda *a, **kw: pytest.fail("must not execute invalid runner"),
    )
    with pytest.raises(executor.ExecutionError, match="syntax or LF"):
        executor.execute_attempt(
            tmp_path,
            {"current_attempt": "attempt-001"},
            {"resource_budget": {"gpu_count": 0}},
            "attempt-001",
        )
    assert not (attempt / "execution.json").exists()


@pytest.mark.parametrize("name", ["stdout.txt", "stderr.txt"])
def test_utf8_child_logs_remain_byte_identical(tmp_path, name):
    raw = "Original child stderr: 中文诊断\n".encode("utf-8")
    path = tmp_path / name
    path.write_bytes(raw)
    result = executor._normalize_execution_log(path, run_root=tmp_path)
    assert result["ready"]
    assert result["host_diagnostics"] == []
    assert result["raw_transport"] is None
    assert path.read_bytes() == raw


def test_mixed_utf16_launcher_and_chinese_utf8_child_are_not_decoded_together(tmp_path):
    host = "wsl: 宿主启动诊断\r\n"
    child = "科学子进程原始错误：边界值不可用。\n".encode("utf-8")
    raw = host.encode("utf-16-le") + child
    path = tmp_path / "stderr.txt"
    path.write_bytes(raw)
    result = executor._normalize_execution_log(path, run_root=tmp_path)
    assert result["ready"]
    assert result["host_diagnostics"] == [host]
    assert path.read_bytes() == child
    archived = tmp_path / result["raw_transport"]["path"]
    assert archived.read_bytes() == raw
    assert result["raw_transport"]["sha256"] == executor.file_sha256(archived)


@pytest.mark.parametrize("newline", ["", "\r\n"])
def test_pure_utf16_launcher_diagnostic_has_no_invented_child_text(tmp_path, newline):
    host = "wsl: 宿主诊断" + newline
    path = tmp_path / "stderr.txt"
    path.write_bytes(host.encode("utf-16"))
    result = executor._normalize_execution_log(path, run_root=tmp_path)
    assert result["ready"]
    assert result["host_diagnostics"] == [host]
    assert path.read_bytes() == b""
    assert (tmp_path / "stderr.raw.bin").read_bytes() == host.encode("utf-16")


def test_each_launcher_line_is_separated_before_utf8_child(tmp_path):
    hosts = ["wsl: 宿主第一行\n", "wsl: 宿主第二行\r\n"]
    child = "原始错误行\n".encode()
    path = tmp_path / "stdout.txt"
    path.write_bytes(b"".join(host.encode("utf-16-le") for host in hosts) + child)
    result = executor._normalize_execution_log(path, run_root=tmp_path)
    assert result["host_diagnostics"] == hosts
    assert path.read_bytes() == child


def test_invalid_child_encoding_is_a_recorded_failure_not_lossy_scientific_text(
    tmp_path,
):
    raw = "wsl: host diagnostic\n".encode("utf-16-le") + b"\xffinvalid child"
    path = tmp_path / "stderr.txt"
    path.write_bytes(raw)
    result = executor._normalize_execution_log(path, run_root=tmp_path)
    assert result["ready"] is False
    assert result["child_text_available"] is False
    assert (tmp_path / "stderr.raw.bin").read_bytes() == raw
    assert "\ufffd" not in path.read_text(encoding="utf-8")
