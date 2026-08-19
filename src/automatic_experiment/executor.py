"""WSL2/bubblewrap execution, doctor probes, cancellation, and resource facts."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from . import sandbox_macos
from .attempts import verify_attempt_immutable
from .paths import output_inventory
from .state import (
    PROJECT_ROOT,
    atomic_write_json,
    file_sha256,
    read_json,
    utc_now,
)
from .state import (
    runs_root as active_runs_root,
)

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

WSL_DISTRO = "Ubuntu-E"
WSL_PYTHON = "/usr/bin/python3" if IS_LINUX else "python3"
EXPECTED_PACKAGES = {
    "astropy": "8.0.1",
    "h5netcdf": "1.8.1",
    "h5py": "3.16.0",
    "numpy": "2.4.6",
    "pandas": "3.0.3",
    "scipy": "1.17.1",
    "sklearn": "1.8.0",
    "matplotlib": "3.10.6",
    "pyarrow": "25.0.0",
    "jsonschema": "4.10.3",
    "xarray": "2026.7.0",
}
RESOURCE_LINE = re.compile(r"^\s*([^:]+):\s*(.*?)\s*$")


class ExecutionError(RuntimeError):
    """The fixed execution backend could not produce trustworthy facts."""


def windows_to_wsl(path: Path) -> str:
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    if len(drive) != 1 or not drive.isalpha():
        raise ExecutionError("only local drive paths can be mapped into WSL")
    tail = resolved.as_posix().split(":", 1)[1]
    return f"/mnt/{drive}{tail}"


def _backend_path(path: Path) -> str:
    if IS_WINDOWS:
        return windows_to_wsl(path)
    if IS_LINUX:
        return str(path.resolve())
    raise ExecutionError("WSL backend paths require Windows or Linux")


def _safe_environment() -> dict[str, str]:
    result = {
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name in ("SystemRoot", "WINDIR", "PATH", "TEMP", "TMP"):
        value = os.environ.get(name)
        if value is not None:
            result[name] = value
    return result


def _run_wsl(args: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
    command = ["wsl.exe", "-d", WSL_DISTRO, "--", *args] if IS_WINDOWS else args
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=_safe_environment(),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def _locked_site_packages() -> tuple[str, dict[str, str], str | None]:
    site_probe = _run_wsl(
        [WSL_PYTHON, "-I", "-c", "import site; print(site.getusersitepackages())"]
    )
    site_path = site_probe.stdout.strip() if site_probe.returncode == 0 else ""
    if not site_path:
        return (
            "",
            {},
            (site_probe.stderr or "locked site-packages path is unavailable").strip(),
        )
    package_script = (
        "import site,sys,importlib.metadata as m,json;"
        "sys.path.insert(0,site.getusersitepackages());"
        f"names={json.dumps(list(EXPECTED_PACKAGES))};"
        "print(json.dumps({n:m.version('scikit-learn' if n=='sklearn' else n) for n in names}))"
    )
    packages = _run_wsl([WSL_PYTHON, "-I", "-c", package_script])
    if packages.returncode != 0:
        return site_path, {}, packages.stderr.strip()
    try:
        installed = json.loads(packages.stdout)
    except json.JSONDecodeError:
        return site_path, {}, "locked package probe returned invalid JSON"
    if not isinstance(installed, dict):
        return site_path, {}, "locked package probe returned a non-object"
    return site_path, {str(key): str(value) for key, value in installed.items()}, None


def runtime_environment_snapshot() -> dict[str, Any]:
    if IS_MACOS:
        return sandbox_macos.runtime_snapshot(EXPECTED_PACKAGES)
    python = _run_wsl([WSL_PYTHON, "--version"])
    bubblewrap = _run_wsl(["bwrap", "--version"])
    site_path, installed, diagnostic = _locked_site_packages()
    mismatches = {
        name: {"expected": expected, "installed": installed.get(name)}
        for name, expected in EXPECTED_PACKAGES.items()
        if installed.get(name) != expected
    }
    ready = (
        python.returncode == 0
        and bubblewrap.returncode == 0
        and bool(site_path)
        and diagnostic is None
        and not mismatches
    )
    return {
        "ready": ready,
        "execution_backend": "native_wsl" if IS_LINUX else "windows_wsl_bridge",
        "host_os": "linux" if IS_LINUX else "windows",
        "wsl_distro": WSL_DISTRO,
        "python_version": (python.stdout or python.stderr).strip(),
        "bubblewrap_version": (bubblewrap.stdout or bubblewrap.stderr).strip(),
        "locked_site_packages": site_path,
        "packages": installed,
        "package_mismatches": mismatches,
        "diagnostic": diagnostic,
        "gpu_count": 0,
    }


def _parse_resource_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = RESOURCE_LINE.match(line)
        if match:
            values[match.group(1).strip()] = match.group(2).strip()
    return {
        "user_cpu_seconds": float(values.get("User time (seconds)", "0") or 0),
        "system_cpu_seconds": float(values.get("System time (seconds)", "0") or 0),
        "max_rss_kb": int(values.get("Maximum resident set size (kbytes)", "0") or 0),
        "major_page_faults": int(
            values.get("Major (requiring I/O) page faults", "0") or 0
        ),
        "minor_page_faults": int(
            values.get("Minor (reclaiming a frame) page faults", "0") or 0
        ),
    }


def _output_tree_size(output_root: Path) -> int:
    """Measure current output usage while tolerating atomic file replacement."""

    total = 0
    for path in output_root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            # A worker may rename a temporary file between is_file() and stat().
            continue
    return total


def _targeted_wsl_kill(pid_file: Path) -> None:
    if not pid_file.is_file():
        return
    raw = pid_file.read_text(encoding="ascii", errors="ignore").strip()
    if not raw.isdigit():
        return
    pid = int(raw)
    if pid <= 1:
        return
    _run_wsl(
        [
            "bash",
            "-lc",
            f"kill -TERM -- -{pid} 2>/dev/null || true; "
            f"sleep 1; kill -KILL -- -{pid} 2>/dev/null || true",
        ],
        timeout=5,
    )


def _targeted_macos_kill(pid_file: Path) -> None:
    if not pid_file.is_file():
        return
    raw = pid_file.read_text(encoding="ascii", errors="ignore").strip()
    if not raw.isdigit():
        return
    pid = int(raw)
    if pid <= 1:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        if sig == signal.SIGTERM:
            time.sleep(1)


def _targeted_kill(pid_file: Path) -> None:
    if IS_MACOS:
        _targeted_macos_kill(pid_file)
    else:
        _targeted_wsl_kill(pid_file)


def execute_attempt(
    run_root: Path,
    state: dict[str, Any],
    request: dict[str, Any],
    attempt_id: str,
) -> dict[str, Any]:
    if state["current_attempt"] != attempt_id:
        raise ExecutionError("only the latest prepared attempt may execute")
    attempt_root = run_root / "attempts" / attempt_id
    metadata = read_json(attempt_root / "attempt.json")
    verify_attempt_immutable(attempt_root, metadata)
    if (attempt_root / "execution.json").exists():
        raise ExecutionError("attempt execution facts are immutable and already exist")
    budget = request["resource_budget"]
    if budget["gpu_count"] != 0:
        raise ExecutionError("GPU execution is boundary-blocked in V1")
    runtime_environment = runtime_environment_snapshot()
    if not runtime_environment["ready"]:
        raise ExecutionError(
            "locked execution environment does not match requirements.lock"
        )
    worker = PROJECT_ROOT / "src" / "automatic_experiment" / "sandbox_worker.py"
    if IS_MACOS:
        supervisor = (
            PROJECT_ROOT / "src" / "automatic_experiment" / "sandbox_supervisor.py"
        )
        for required in (supervisor, worker):
            if not required.is_file():
                raise ExecutionError(
                    f"execution runtime is incomplete: {required.name}"
                )
        command = [
            str(runtime_environment["python"]),
            str(supervisor),
            "--attempt-root",
            str(attempt_root),
            "--runtime-file",
            str(worker),
            "--wall-seconds",
            str(budget["wall_seconds"]),
            "--cpu-seconds",
            str(budget["cpu_seconds"]),
            "--memory-bytes",
            str(budget["memory_mb"] * 1024 * 1024),
            "--file-bytes",
            str(budget["single_file_mb"] * 1024 * 1024),
            "--python",
            str(runtime_environment["python"]),
            "--site-packages",
            str(runtime_environment["locked_site_packages"]),
        ]
        command_kind = sandbox_macos.BACKEND_KIND
        command_arguments = [
            str(runtime_environment["python"]),
            "src/automatic_experiment/sandbox_supervisor.py",
            "--attempt-root",
            f"experiment/runs/{state['run_id']}/attempts/{attempt_id}",
            "--runtime-file",
            "src/automatic_experiment/sandbox_worker.py",
            "--wall-seconds",
            str(budget["wall_seconds"]),
            "--cpu-seconds",
            str(budget["cpu_seconds"]),
            "--memory-bytes",
            str(budget["memory_mb"] * 1024 * 1024),
            "--file-bytes",
            str(budget["single_file_mb"] * 1024 * 1024),
        ]
        sandbox_policy = sandbox_macos.sandbox_policy_facts()
    elif IS_WINDOWS or IS_LINUX:
        runner = PROJECT_ROOT / "src" / "automatic_experiment" / "sandbox_runner.sh"
        for required in (runner, worker):
            if not required.is_file():
                raise ExecutionError(
                    f"execution runtime is incomplete: {required.name}"
                )
        bridge_prefix = ["wsl.exe", "-d", WSL_DISTRO, "--"] if IS_WINDOWS else []
        command = [
            *bridge_prefix,
            "bash",
            _backend_path(runner),
            "--attempt-root",
            _backend_path(attempt_root),
            "--runtime-file",
            _backend_path(worker),
            "--wall-seconds",
            str(budget["wall_seconds"]),
            "--cpu-seconds",
            str(budget["cpu_seconds"]),
            "--memory-bytes",
            str(budget["memory_mb"] * 1024 * 1024),
            "--file-bytes",
            str(budget["single_file_mb"] * 1024 * 1024),
        ]
        command_kind = (
            "fixed_windows_wsl_bridge_bubblewrap_python"
            if IS_WINDOWS
            else "fixed_native_wsl_bubblewrap_python"
        )
        command_arguments = [
            *bridge_prefix,
            "bash",
            "src/automatic_experiment/sandbox_runner.sh",
            "--attempt-root",
            f"runs/{state['run_id']}/attempts/{attempt_id}",
            "--runtime-file",
            "src/automatic_experiment/sandbox_worker.py",
            "--wall-seconds",
            str(budget["wall_seconds"]),
            "--cpu-seconds",
            str(budget["cpu_seconds"]),
            "--memory-bytes",
            str(budget["memory_mb"] * 1024 * 1024),
            "--file-bytes",
            str(budget["single_file_mb"] * 1024 * 1024),
        ]
        sandbox_policy = {
            "backend": (
                f"windows_wsl_bridge/{WSL_DISTRO}+bubblewrap"
                if IS_WINDOWS
                else f"native_wsl/{WSL_DISTRO}+bubblewrap"
            ),
            "user_namespace": True,
            "pid_namespace": True,
            "network_namespace": True,
            "ipc_namespace": True,
            "uts_namespace": True,
            "new_session": True,
            "host_project_mounted": False,
            "home_mounted": False,
            "input_snapshot_read_only": True,
            "attempt_code_read_only": True,
            "attempt_output_only_writable_mount": True,
            "locked_site_packages_read_only": True,
            "gpu_visible": False,
        }
    else:
        raise ExecutionError(
            f"sandboxed execution is not supported on this platform ({sys.platform or os.name})"
        )
    stdout_path = attempt_root / "stdout.txt"
    stderr_path = attempt_root / "stderr.txt"
    cancel_path = run_root / "cancel.requested"
    started_at = utc_now()
    start = time.monotonic()
    stop_reason: str | None = None
    with (
        stdout_path.open("wb") as stdout_handle,
        stderr_path.open("wb") as stderr_handle,
    ):
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            env=_safe_environment(),
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        while process.poll() is None:
            elapsed = time.monotonic() - start
            output_bytes = _output_tree_size(attempt_root / "output")
            if cancel_path.exists():
                stop_reason = "cancelled_by_user"
            elif stdout_path.stat().st_size > budget["stdout_kb"] * 1024:
                stop_reason = "stdout_budget"
            elif stderr_path.stat().st_size > budget["stderr_kb"] * 1024:
                stop_reason = "stderr_budget"
            elif output_bytes > budget["disk_mb"] * 1024 * 1024:
                stop_reason = "disk_budget"
            elif elapsed > budget["wall_seconds"] + 10:
                stop_reason = "wall_budget_parent_guard"
            if stop_reason is not None:
                _targeted_kill(attempt_root / "sandbox.pid")
                process.kill()
                break
            time.sleep(0.1)
        exit_code = process.wait(timeout=10)
    ended_at = utc_now()
    elapsed_seconds = round(time.monotonic() - start, 6)
    if cancel_path.exists():
        cancel_path.unlink()
    sandbox_exit = {}
    if (attempt_root / "sandbox_exit.json").is_file():
        sandbox_exit = read_json(attempt_root / "sandbox_exit.json")
    sandbox_start = {"attempts": 1, "retries": 0}
    if (attempt_root / "sandbox_start.json").is_file():
        sandbox_start = read_json(attempt_root / "sandbox_start.json")
    wrapper_exit = sandbox_exit.get("exit_code")
    if stop_reason is None and wrapper_exit == 124:
        stop_reason = "wall_budget"
    elif stop_reason is None and wrapper_exit in {137, 152}:
        stop_reason = "resource_budget"
    elif (
        stop_reason is None
        and wrapper_exit not in {None, 0}
        and elapsed_seconds >= budget["wall_seconds"] * 0.9
    ):
        stop_reason = "wall_budget"
    inventory_error = None
    try:
        inventory = output_inventory(
            attempt_root / "output",
            budget["disk_mb"],
            budget["single_file_mb"],
        )
    except Exception as exc:
        inventory = []
        inventory_error = str(exc)
        stop_reason = stop_reason or "output_policy"
    stdout_size = stdout_path.stat().st_size
    stderr_size = stderr_path.stat().st_size
    facts = {
        "schema_version": "automatic-experiment-execution-facts-v1",
        "run_id": state["run_id"],
        "attempt_id": attempt_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "wall_seconds": elapsed_seconds,
        "execution_backend": "windows_wsl_bridge" if IS_WINDOWS else "native_wsl",
        "command_kind": command_kind,
        "command_arguments": command_arguments,
        "host_process_exit_code": exit_code,
        "windows_process_exit_code": exit_code if IS_WINDOWS else None,
        "sandbox_exit_code": wrapper_exit,
        "sandbox_start_attempts": sandbox_start.get("attempts", 1),
        "sandbox_start_retries": sandbox_start.get("retries", 0),
        "stop_reason": stop_reason,
        "stdout": {
            "path": stdout_path.relative_to(run_root).as_posix(),
            "size_bytes": stdout_size,
            "sha256": file_sha256(stdout_path),
            "truncated": stdout_size > budget["stdout_kb"] * 1024,
        },
        "stderr": {
            "path": stderr_path.relative_to(run_root).as_posix(),
            "size_bytes": stderr_size,
            "sha256": file_sha256(stderr_path),
            "truncated": stderr_size > budget["stderr_kb"] * 1024,
        },
        "resource_usage": _parse_resource_file(attempt_root / "resource.txt"),
        "runtime_environment": runtime_environment,
        "output_inventory": inventory,
        "output_inventory_error": inventory_error,
        "sandbox_policy": sandbox_policy,
    }
    atomic_write_json(attempt_root / "execution.json", facts)
    return facts


def request_stop(run_root: Path) -> dict[str, Any]:
    marker = run_root / "cancel.requested"
    marker.write_text(utc_now() + "\n", encoding="utf-8")
    attempt_id = None
    state_path = run_root / "state.json"
    if state_path.is_file():
        attempt_id = read_json(state_path).get("current_attempt")
    if isinstance(attempt_id, str):
        _targeted_kill(run_root / "attempts" / attempt_id / "sandbox.pid")
    return {
        "status": "stop_requested",
        "run_id": run_root.name,
        "attempt_id": attempt_id,
    }


def _doctor_macos() -> dict[str, Any]:
    checks: dict[str, Any] = {}
    checks["sandbox_exec"] = {
        "ready": Path(sandbox_macos.SANDBOX_EXEC).is_file()
        and os.access(sandbox_macos.SANDBOX_EXEC, os.X_OK),
        "path": sandbox_macos.SANDBOX_EXEC,
    }
    python = sandbox_macos.sandbox_python()
    if python is None:
        checks["python"] = {
            "ready": False,
            "version": "",
            "diagnostic": "no trusted python3 interpreter found (set AE_SANDBOX_PYTHON)",
        }
        site_packages, installed, package_diagnostic = (
            "",
            {},
            checks["python"]["diagnostic"],
        )
    else:
        version = sandbox_macos._probe(
            python, "import sys;print(sys.version.split()[0])"
        )
        checks["python"] = {
            "ready": version.returncode == 0,
            "path": python,
            "version": (version.stdout or version.stderr).strip(),
        }
        site_packages, installed, package_diagnostic = (
            sandbox_macos.locked_site_packages(python, EXPECTED_PACKAGES)
        )
    mismatches = {
        name: {"expected": expected, "installed": installed.get(name)}
        for name, expected in EXPECTED_PACKAGES.items()
        if installed.get(name) != expected
    }
    checks["locked_packages"] = {
        "ready": bool(site_packages) and package_diagnostic is None and not mismatches,
        "installed": installed,
        "mismatches": mismatches,
        "site_packages": site_packages,
        "diagnostic": package_diagnostic,
    }
    supervisor = PROJECT_ROOT / "src" / "automatic_experiment" / "sandbox_supervisor.py"
    worker = PROJECT_ROOT / "src" / "automatic_experiment" / "sandbox_worker.py"
    backend = PROJECT_ROOT / "src" / "automatic_experiment" / "sandbox_macos.py"
    checks["runtime_files"] = {
        "ready": supervisor.is_file() and worker.is_file() and backend.is_file(),
        "supervisor_sha256": file_sha256(supervisor) if supervisor.is_file() else None,
        "worker_sha256": file_sha256(worker) if worker.is_file() else None,
        "backend_sha256": file_sha256(backend) if backend.is_file() else None,
    }
    runs_root = active_runs_root()
    runs_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".doctor-", dir=runs_root
    ) as temporary_name:
        temporary = Path(temporary_name)
        code = temporary / "code"
        output = temporary / "output"
        scratch = temporary / ".scratch"
        inputs = temporary / "inputs"
        prior = temporary / "stage_artifacts"
        for directory in (code, output, scratch, inputs, prior):
            directory.mkdir()
        probe = code / "probe.py"
        probe.write_text(
            "import json,os,socket,sys\n"
            "sys.path.insert(0, os.environ.get('AE_LOCKED_SITE_PACKAGES',''))\n"
            "from pathlib import Path\n"
            "host_visible=False\n"
            "try:\n"
            f"    Path({json.dumps(str(PROJECT_ROOT / 'pyproject.toml'))}).read_text(encoding='utf-8')\n"
            "    host_visible=True\n"
            "except OSError:\n"
            "    pass\n"
            "network=False\n"
            "try:\n"
            "    socket.create_connection(('1.1.1.1',53),timeout=0.5); network=True\n"
            "except OSError:\n"
            "    pass\n"
            "import numpy\n"
            "Path(os.environ['AE_PROBE_OUTPUT']).write_text(json.dumps("
            "{'host_visible':host_visible,'network_connected':network,"
            "'numpy_version':numpy.__version__}),encoding='utf-8')\n",
            encoding="utf-8",
        )
        probe_payload: dict[str, Any] = {}
        probe_diagnostic = ""
        probe_returncode = 1
        if python is not None and site_packages:
            profile = sandbox_macos.build_seatbelt_profile(
                python=python,
                code_root=code,
                input_root=inputs,
                prior_root=prior,
                output_root=output,
                scratch_root=scratch,
                site_packages=site_packages,
            )
            profile_path = scratch / "seatbelt.sb"
            profile_path.write_text(profile, encoding="utf-8")
            env = {
                "PATH": "/usr/bin:/bin",
                "HOME": str(scratch),
                "TMPDIR": str(scratch),
                "PYTHONDONTWRITEBYTECODE": "1",
                "AE_LOCKED_SITE_PACKAGES": site_packages,
                "AE_PROBE_OUTPUT": str(output / "probe.json"),
            }
            completed = subprocess.run(
                [
                    sandbox_macos.SANDBOX_EXEC,
                    "-f",
                    str(profile_path),
                    python,
                    "-I",
                    "-B",
                    str(probe),
                ],
                cwd=str(scratch),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
            )
            probe_returncode = completed.returncode
            probe_diagnostic = (completed.stderr or "")[-1000:]
            probe_file = output / "probe.json"
            if probe_file.is_file():
                try:
                    probe_payload = json.loads(probe_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    probe_payload = {}
        checks["sandbox_probe"] = {
            "ready": (
                probe_returncode == 0
                and probe_payload.get("host_visible") is False
                and probe_payload.get("network_connected") is False
                and probe_payload.get("numpy_version") == EXPECTED_PACKAGES["numpy"]
            ),
            "return_code": probe_returncode,
            "payload": probe_payload,
            "diagnostic": probe_diagnostic,
        }
    ready = all(row.get("ready") is True for row in checks.values())
    return {
        "schema_version": "automatic-experiment-doctor-v1",
        "status": "ready" if ready else "boundary_blocked",
        "platform": "macos",
        "project_root": str(PROJECT_ROOT),
        "gpu_policy": "gpu_count must remain zero in V1",
        "runtime_installation_permitted": False,
        "checks": checks,
    }


def doctor() -> dict[str, Any]:
    if IS_MACOS:
        return _doctor_macos()
    checks: dict[str, Any] = {}
    version = _run_wsl(["bwrap", "--version"])
    checks["bubblewrap"] = {
        "ready": version.returncode == 0,
        "version": (version.stdout or version.stderr).strip(),
    }
    python = _run_wsl([WSL_PYTHON, "--version"])
    checks["python"] = {
        "ready": python.returncode == 0,
        "version": (python.stdout or python.stderr).strip(),
    }
    site_packages, installed, package_diagnostic = _locked_site_packages()
    mismatches = {
        name: {"expected": expected, "installed": installed.get(name)}
        for name, expected in EXPECTED_PACKAGES.items()
        if installed.get(name) != expected
    }
    checks["locked_packages"] = {
        "ready": not mismatches,
        "installed": installed,
        "mismatches": mismatches,
        "site_packages": site_packages,
        "diagnostic": package_diagnostic,
    }
    checks["locked_packages"]["ready"] = (
        bool(site_packages) and package_diagnostic is None and not mismatches
    )
    runner = PROJECT_ROOT / "src" / "automatic_experiment" / "sandbox_runner.sh"
    worker = PROJECT_ROOT / "src" / "automatic_experiment" / "sandbox_worker.py"
    checks["runtime_files"] = {
        "ready": runner.is_file() and worker.is_file(),
        "runner_sha256": file_sha256(runner) if runner.is_file() else None,
        "worker_sha256": file_sha256(worker) if worker.is_file() else None,
    }
    runs_root = active_runs_root()
    runs_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".doctor-", dir=runs_root
    ) as temporary_name:
        temporary = Path(temporary_name)
        code = temporary / "code"
        output = temporary / "output"
        code.mkdir()
        output.mkdir()
        probe = code / "probe.py"
        probe.write_text(
            "from pathlib import Path\n"
            "import socket\n"
            "visible=Path('/mnt').exists() or Path('/workspace/.pi/settings.json').exists()\n"
            "network=False\n"
            "try:\n"
            " socket.create_connection(('1.1.1.1',53),timeout=0.5); network=True\n"
            "except OSError: pass\n"
            "Path('/workspace/output/probe.json').write_text("
            "__import__('json').dumps({'host_visible':visible,'network_connected':network,"
            "'numpy_version':numpy.__version__}),encoding='utf-8')\n",
            encoding="utf-8",
        )
        runtime = temporary / "runtime.py"
        runtime.write_text(
            "import sys\n"
            "sys.path.insert(0, '/runtime/site-packages')\n"
            "import numpy\n" + probe.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        bwrap_args = [
            "bwrap",
            "--die-with-parent",
            "--new-session",
            "--unshare-user",
            "--uid",
            "65534",
            "--gid",
            "65534",
            "--unshare-pid",
            "--unshare-net",
            "--unshare-ipc",
            "--unshare-uts",
            "--clearenv",
            "--setenv",
            "HOME",
            "/tmp",
            "--ro-bind",
            "/usr",
            "/usr",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/workspace",
            "--dir",
            "/runtime",
            "--ro-bind",
            site_packages,
            "/runtime/site-packages",
            "--bind",
            _backend_path(output),
            "/workspace/output",
            "--ro-bind",
            _backend_path(runtime),
            "/runtime.py",
            "/usr/bin/python3",
            "-I",
            "-B",
            "/runtime.py",
        ]
        for system_path in ("/lib", "/lib64", "/etc/ld.so.cache"):
            present = _run_wsl(["test", "-e", system_path])
            if present.returncode == 0:
                insert_at = bwrap_args.index("--proc")
                bwrap_args[insert_at:insert_at] = [
                    "--ro-bind",
                    system_path,
                    system_path,
                ]
        probe_result = _run_wsl(bwrap_args, timeout=10)
        probe_payload: dict[str, Any] = {}
        probe_file = output / "probe.json"
        if probe_file.is_file():
            try:
                probe_payload = json.loads(probe_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                probe_payload = {}
        checks["sandbox_probe"] = {
            "ready": (
                probe_result.returncode == 0
                and probe_payload.get("host_visible") is False
                and probe_payload.get("network_connected") is False
                and probe_payload.get("numpy_version") == EXPECTED_PACKAGES["numpy"]
            ),
            "return_code": probe_result.returncode,
            "payload": probe_payload,
            "diagnostic": probe_result.stderr[-1000:],
        }
    ready = all(row.get("ready") is True for row in checks.values())
    return {
        "schema_version": "automatic-experiment-doctor-v1",
        "status": "ready" if ready else "boundary_blocked",
        "project_root": str(PROJECT_ROOT),
        "wsl_distro": WSL_DISTRO,
        "gpu_policy": "gpu_count must remain zero in V1",
        "runtime_installation_permitted": False,
        "checks": checks,
    }
