"""WSL2/bubblewrap execution, doctor probes, cancellation, and resource facts."""

from __future__ import annotations

import json
import os
import re
import shutil
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

# An empty distribution uses WSL's registered default, never a developer's name.
# These settings affect only the isolated Linux runtime; there is no host-Python
# fallback when WSL or its required isolation primitives are unavailable.
WSL_DISTRO = ""
WSL_PYTHON = "/usr/bin/python3"
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


def _wsl_prefix() -> list[str]:
    if not IS_WINDOWS:
        return []
    distro = os.environ.get("AE_WSL_DISTRO", WSL_DISTRO).strip()
    return ["wsl.exe", *(["-d", distro] if distro else []), "--"]


def _linux_python() -> str:
    return os.environ.get("AE_SANDBOX_PYTHON", WSL_PYTHON).strip() or WSL_PYTHON


def _decode_probe_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    # The Windows WSL launcher emits UTF-16 even when the Linux child would use
    # UTF-8. Decode that diagnostic without leaking NUL-filled unreadable text.
    if value.startswith((b"\xff\xfe", b"\xfe\xff")):
        return value.decode("utf-16", errors="replace")
    if b"\x00" in value:
        return value.decode("utf-16-le", errors="replace")
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        if len(value) % 2 == 0:
            return value.decode("utf-16-le", errors="replace")
        return value.decode("utf-8", errors="replace")


def _run_wsl(args: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
    command = [*_wsl_prefix(), *args]
    try:
        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=_safe_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return subprocess.CompletedProcess(
            command,
            result.returncode,
            _decode_probe_output(result.stdout),
            _decode_probe_output(result.stderr),
        )
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            command, 124, "", f"Isolated runtime probe exceeded {timeout} seconds"
        )


def _linux_runtime_files() -> dict[str, Any]:
    """Check the actual runner bytes and parse them with the execution shell.

    Git Bash can silently ignore CR characters. Only the selected Linux runtime's
    bash is authoritative, and no runner statement is executed by ``bash -n``.
    """
    runner = PROJECT_ROOT / "src" / "automatic_experiment" / "sandbox_runner.sh"
    worker = PROJECT_ROOT / "src" / "automatic_experiment" / "sandbox_worker.py"
    syntax: dict[str, Any] = {
        "ready": False,
        "executed": False,
        "return_code": None,
        "diagnostic": "Execution runtime files are missing",
    }
    result: dict[str, Any] = {
        "ready": False,
        "runner_sha256": file_sha256(runner) if runner.is_file() else None,
        "worker_sha256": file_sha256(worker) if worker.is_file() else None,
        "runner_lf_only": False,
        "runner_syntax": syntax,
    }
    if not runner.is_file() or not worker.is_file():
        return result
    try:
        result["runner_lf_only"] = b"\r" not in runner.read_bytes()
    except OSError:
        syntax["diagnostic"] = "Execution runner bytes cannot be read"
        return result
    if not result["runner_lf_only"]:
        syntax["diagnostic"] = (
            "Execution runner must use LF line endings without CR bytes"
        )
        return result
    parsed = _run_wsl(["bash", "-n", _backend_path(runner)])
    syntax.update(
        ready=parsed.returncode == 0,
        executed=True,
        return_code=parsed.returncode,
        diagnostic=(parsed.stderr or parsed.stdout).strip()[-1000:] or None,
    )
    result["ready"] = syntax["ready"]
    return result


def _normalize_execution_log(path: Path, *, run_root: Path) -> dict[str, Any]:
    """Separate leading UTF-16 WSL diagnostics from unchanged UTF-8 child logs.

    The launcher and Linux child can write different encodings to one pipe.
    Never decode the entire mixed stream as UTF-16 just because it contains NULs.
    Any transformed log retains its exact original transport bytes and digest.
    """
    raw = path.read_bytes()
    child = raw
    host_diagnostics = []
    while True:
        line = child[2:] if child.startswith(b"\xff\xfe") else child
        if not line.lower().startswith(b"w\x00s\x00l\x00:\x00"):
            break
        end = next(
            (
                index + 2
                for index in range(0, len(line) - 1, 2)
                if line[index : index + 2] == b"\n\x00"
            ),
            len(line),
        )
        try:
            decoded = line[:end].decode("utf-16-le")
        except UnicodeDecodeError:
            break
        host_diagnostics.append(decoded)
        child = line[end:]
    result: dict[str, Any] = {
        "ready": True,
        "child_encoding": "utf-8",
        "child_text_available": True,
        "host_diagnostics": host_diagnostics,
        "raw_transport": None,
    }
    try:
        child.decode("utf-8")
    except UnicodeDecodeError:
        result.update(ready=False, child_text_available=False)
    if host_diagnostics or not result["ready"]:
        raw_path = path.with_name(path.stem + ".raw.bin")
        with raw_path.open("xb") as handle:
            handle.write(raw)
        result["raw_transport"] = {
            "path": raw_path.relative_to(run_root).as_posix(),
            "size_bytes": len(raw),
            "sha256": file_sha256(raw_path),
        }
        if result["ready"]:
            # The scientific child bytes are copied exactly, not re-encoded.
            path.write_bytes(child)
        else:
            path.write_bytes(
                b"Child log is not valid UTF-8; exact transport bytes are retained in the declared raw log.\n"
            )
            result["diagnostic"] = (
                "Child log cannot be represented as verified UTF-8 text"
            )
    return result


def _locked_site_packages() -> tuple[str, dict[str, str], str | None]:
    configured = os.environ.get("AE_SANDBOX_SITE_PACKAGES", "").strip()
    if configured and not configured.startswith("/"):
        return "", {}, "AE_SANDBOX_SITE_PACKAGES must be an absolute Linux path"
    site_probe = _run_wsl(
        [
            _linux_python(),
            "-I",
            "-c",
            "import site,json; print(json.dumps("
            + (
                f"[{configured!r}]"
                if configured
                else "site.getsitepackages()+[site.getusersitepackages()]"
            )
            + "))",
        ]
    )
    if site_probe.returncode != 0:
        return (
            "",
            {},
            (
                site_probe.stderr
                or site_probe.stdout
                or "locked site-packages path is unavailable"
            ).strip(),
        )
    try:
        candidates = json.loads(site_probe.stdout)
    except json.JSONDecodeError:
        return "", {}, "locked site-packages probe returned invalid JSON"
    if not isinstance(candidates, list) or not all(
        isinstance(p, str) for p in candidates
    ):
        return "", {}, "locked site-packages probe returned invalid paths"
    best_path, best_installed = "", {}
    for site_path in dict.fromkeys(candidates):
        package_script = (
            "import importlib.metadata as m,json;"
            f"names={json.dumps(list(EXPECTED_PACKAGES))};"
            f"found={{d.metadata['Name'].lower().replace('_','-'):d.version for d in m.distributions(path=[{site_path!r}])}};"
            "print(json.dumps({n:found.get('scikit-learn' if n=='sklearn' else n) for n in names if ('scikit-learn' if n=='sklearn' else n) in found}))"
        )
        packages = _run_wsl([_linux_python(), "-I", "-c", package_script])
        try:
            installed = (
                json.loads(packages.stdout) if packages.returncode == 0 else None
            )
        except json.JSONDecodeError:
            installed = None
        if not isinstance(installed, dict):
            continue
        installed = {str(k): str(v) for k, v in installed.items()}
        if all(installed.get(n) == v for n, v in EXPECTED_PACKAGES.items()):
            return site_path, installed, None
        if len(installed) > len(best_installed):
            best_path, best_installed = site_path, installed
    return (
        best_path,
        best_installed,
        "No single locked site-packages directory matches requirements.lock",
    )


def runtime_environment_snapshot() -> dict[str, Any]:
    if IS_MACOS:
        return sandbox_macos.runtime_snapshot(EXPECTED_PACKAGES)
    python = _run_wsl([_linux_python(), "--version"])
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
        "wsl_distro": os.environ.get("AE_WSL_DISTRO", WSL_DISTRO).strip() or None,
        "python": _linux_python(),
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
        runtime_files = _linux_runtime_files()
        runtime_environment["runtime_files"] = runtime_files
        if not runtime_files["ready"]:
            raise ExecutionError(
                "execution runner failed Linux syntax or LF validation"
            )
        runner = PROJECT_ROOT / "src" / "automatic_experiment" / "sandbox_runner.sh"
        for required in (runner, worker):
            if not required.is_file():
                raise ExecutionError(
                    f"execution runtime is incomplete: {required.name}"
                )
        bridge_prefix = _wsl_prefix()
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
            "--python",
            _linux_python(),
            "--site-packages",
            runtime_environment["locked_site_packages"],
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
            "--python",
            _linux_python(),
            "--site-packages",
            runtime_environment["locked_site_packages"],
        ]
        sandbox_policy = {
            "backend": (
                f"windows_wsl_bridge/{os.environ.get('AE_WSL_DISTRO', WSL_DISTRO).strip() or 'default'}+bubblewrap"
                if IS_WINDOWS
                else "native_linux+bubblewrap"
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
    log_encodings = {
        "stdout": _normalize_execution_log(stdout_path, run_root=run_root),
        "stderr": _normalize_execution_log(stderr_path, run_root=run_root),
    }
    if any(not row["ready"] for row in log_encodings.values()):
        stop_reason = stop_reason or "transport_log_encoding"
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
        "execution_backend": runtime_environment["execution_backend"],
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
            "encoding": log_encodings["stdout"],
        },
        "stderr": {
            "path": stderr_path.relative_to(run_root).as_posix(),
            "size_bytes": stderr_size,
            "sha256": file_sha256(stderr_path),
            "truncated": stderr_size > budget["stderr_kb"] * 1024,
            "encoding": log_encodings["stderr"],
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


def doctor(*, probe: bool = True) -> dict[str, Any]:
    """Inspect prerequisites and, only when available, test real isolation.

    ``probe=False`` is a read-only environment inspection. It never claims
    readiness because namespace, host-file and network isolation remain untested.
    Missing prerequisites also return a structured blocker without making a run
    directory or attempting to execute experiment code.
    """
    if IS_MACOS:
        if not probe:
            snapshot = runtime_environment_snapshot()
            return {
                "schema_version": "automatic-experiment-doctor-v1",
                "status": "inspection_only",
                "platform": "macos",
                "runtime_environment": snapshot,
                "sandbox_probe_executed": False,
                "runtime_installation_permitted": False,
            }
        return _doctor_macos()
    snapshot = runtime_environment_snapshot()
    runtime_files = _linux_runtime_files()
    ready = snapshot["ready"] and runtime_files["ready"]
    if not ready or not probe:
        return {
            "schema_version": "automatic-experiment-doctor-v1",
            "status": "boundary_blocked" if not ready else "inspection_only",
            "platform": "windows" if IS_WINDOWS else "linux",
            "project_root": str(PROJECT_ROOT),
            "wsl_distro": snapshot["wsl_distro"],
            "runtime_environment": snapshot,
            "sandbox_probe_executed": False,
            "runtime_installation_permitted": False,
            "detected_alternative_launchers": {
                name: shutil.which(name) is not None for name in ("docker", "podman")
            },
            "checks": {
                "runtime_prerequisites": {
                    "ready": snapshot["ready"],
                    "diagnostic": snapshot["diagnostic"],
                },
                "runtime_files": runtime_files,
                "sandbox_probe": {
                    "ready": False,
                    "skipped": True,
                    "diagnostic": (
                        "Isolation cannot be tested until runtime prerequisites and runner checks are ready"
                        if not ready
                        else "Read-only inspection requested; actual isolation remains unverified"
                    ),
                },
            },
            "next_steps": [
                "Provide an installed Linux isolation runtime with bubblewrap and locked dependencies",
                "Optionally select AE_WSL_DISTRO, AE_SANDBOX_PYTHON and AE_SANDBOX_SITE_PACKAGES",
                "Require LF runner bytes and a successful syntax check by the selected Linux bash",
                "Run the full doctor and require sandbox_probe.ready before research execution",
            ],
        }
    checks: dict[str, Any] = {}
    version = _run_wsl(["bwrap", "--version"])
    checks["bubblewrap"] = {
        "ready": version.returncode == 0,
        "version": (version.stdout or version.stderr).strip(),
    }
    python = _run_wsl([_linux_python(), "--version"])
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
    checks["runtime_files"] = runtime_files
    runs_root = active_runs_root()
    runs_root.mkdir(parents=True, exist_ok=True)
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
            "--ro-bind",
            _linux_python(),
            "/runtime/python",
            "/runtime/python",
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
        "wsl_distro": os.environ.get("AE_WSL_DISTRO", WSL_DISTRO).strip() or None,
        "sandbox_probe_executed": True,
        "gpu_policy": "gpu_count must remain zero in V1",
        "runtime_installation_permitted": False,
        "checks": checks,
    }
