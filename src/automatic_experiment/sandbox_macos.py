"""macOS seatbelt (``sandbox-exec``) backend helpers for the experiment sandbox.

The Windows backend executes workers inside WSL2/bubblewrap. macOS has no
bubblewrap; this module provides the equivalent host-side pieces through the
built-in ``sandbox-exec`` seatbelt facility:

- interpreter resolution (dedicated ``.venv-sandbox`` first, PATH fallback)
- locked site-packages and package-version probes (same contract as WSL)
- seatbelt profile generation (no network, read-only code/inputs,
  writable attempt output plus a per-attempt scratch directory)

The per-attempt supervision (rlimits, wall guard, resource facts) lives in
``sandbox_supervisor.py``, which mirrors ``sandbox_runner.sh``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .state import PROJECT_ROOT

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
BACKEND_KIND = "fixed_macos_seatbelt_python"
BACKEND_LABEL = "macOS seatbelt (sandbox-exec)"


def sandbox_python() -> str | None:
    """Resolve the interpreter trusted for sandboxed execution.

    Resolution order: ``AE_SANDBOX_PYTHON`` override, the dedicated locked
    ``.venv-sandbox`` environment, then ``python3`` on PATH.
    """

    override = os.environ.get("AE_SANDBOX_PYTHON", "").strip()
    if override:
        return override if Path(override).is_file() else None
    venv = PROJECT_ROOT / ".venv-sandbox" / "bin" / "python3"
    if venv.is_file():
        return str(venv)
    return shutil.which("python3")


def _probe(python: str, script: str, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [python, "-I", "-c", script],
        cwd=PROJECT_ROOT,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def locked_site_packages(
    python: str, expected: dict[str, str]
) -> tuple[str, dict[str, str], str | None]:
    """Return (site-packages path, installed versions, diagnostic).

    Mirrors the WSL ``_locked_site_packages`` probe: versions are read from
    the interpreter's own site-packages, the same directory the worker later
    prepends to ``sys.path`` through ``AE_LOCKED_SITE_PACKAGES``.
    """

    site_probe = _probe(
        python, "import site,json;print(json.dumps(site.getsitepackages()))"
    )
    candidates: list[str] = []
    if site_probe.returncode == 0:
        try:
            value = json.loads(site_probe.stdout)
            if isinstance(value, list):
                candidates = [str(item) for item in value]
        except json.JSONDecodeError:
            pass
    site_path = next((item for item in candidates if Path(item).is_dir()), "")
    if not site_path:
        return "", {}, (site_probe.stderr or "locked site-packages path is unavailable").strip()
    package_script = (
        "import importlib.metadata as m,json;"
        f"names={json.dumps(list(expected))};"
        "print(json.dumps({n:m.version('scikit-learn' if n=='sklearn' else n) for n in names}))"
    )
    packages = _probe(python, package_script)
    if packages.returncode != 0:
        return site_path, {}, packages.stderr.strip()
    try:
        installed = json.loads(packages.stdout)
    except json.JSONDecodeError:
        return site_path, {}, "locked package probe returned invalid JSON"
    if not isinstance(installed, dict):
        return site_path, {}, "locked package probe returned a non-object"
    return site_path, {str(key): str(value) for key, value in installed.items()}, None


def runtime_snapshot(expected: dict[str, str]) -> dict[str, Any]:
    """macOS equivalent of the WSL ``runtime_environment_snapshot``."""

    sandbox_exec_ready = Path(SANDBOX_EXEC).is_file() and os.access(SANDBOX_EXEC, os.X_OK)
    python = sandbox_python()
    python_version = ""
    site_path = ""
    installed: dict[str, str] = {}
    diagnostic: str | None = None
    if python is None:
        diagnostic = "no trusted python3 interpreter found (set AE_SANDBOX_PYTHON)"
    else:
        version = _probe(python, "import sys;print(sys.version.split()[0])")
        python_version = (version.stdout or version.stderr).strip()
        site_path, installed, diagnostic = locked_site_packages(python, expected)
    mismatches = {
        name: {"expected": want, "installed": installed.get(name)}
        for name, want in expected.items()
        if installed.get(name) != want
    }
    ready = (
        sandbox_exec_ready
        and python is not None
        and bool(python_version)
        and bool(site_path)
        and diagnostic is None
        and not mismatches
    )
    return {
        "ready": ready,
        "backend": "macos_seatbelt",
        "sandbox_exec": SANDBOX_EXEC,
        "sandbox_exec_ready": sandbox_exec_ready,
        "python": python,
        "python_version": python_version,
        "locked_site_packages": site_path,
        "packages": installed,
        "package_mismatches": mismatches,
        "diagnostic": diagnostic,
        "gpu_count": 0,
    }


def build_seatbelt_profile(
    *,
    python: str,
    code_root: Path | None,
    input_root: Path | None,
    prior_root: Path | None,
    output_root: Path,
    scratch_root: Path,
    site_packages: str = "",
    extra_read_literals: list[Path] | None = None,
) -> str:
    """Render one SBPL seatbelt profile for a sandboxed worker process.

    Reads are limited to the operating system, the interpreter, the locked
    site-packages, and the run's own code/input/prior/output trees; writes are
    limited to the attempt output and the per-attempt scratch directory;
    network is denied. This mirrors the bubblewrap mount policy as closely as
    seatbelt allows.
    """

    python_prefix = Path(python).resolve().parents[1]
    # A venv interpreter symlinks to the base python; both the resolved base
    # prefix and the venv's own root (pyvenv.cfg, bin/, lib/) must be readable.
    venv_prefix = Path(python).parents[1]
    read_roots: list[Path] = [
        Path("/usr"),
        Path("/System"),
        Path("/Library/Frameworks"),
        Path("/bin"),
        Path("/sbin"),
        Path("/dev"),
        Path("/private/etc"),
        python_prefix,
    ]
    if venv_prefix != python_prefix:
        read_roots.append(venv_prefix)
    if site_packages:
        read_roots.append(Path(site_packages).resolve())
    for candidate in (code_root, input_root, prior_root):
        if candidate is not None:
            read_roots.append(Path(candidate).resolve())
    write_roots = [Path(output_root).resolve(), Path(scratch_root).resolve()]

    lines = [
        "(version 1)",
        "(deny default)",
        "(allow process*)",
        "(allow mach-lookup)",
        "(allow sysctl-read)",
        "(allow file-read-metadata)",
    ]
    lines.append("(allow file-read*")
    # Path resolution on macOS reads the root directory itself; without this
    # literal every exec aborts inside dyld before user code starts.
    lines.append('  (literal "/")')
    for root in read_roots:
        lines.append(f'  (subpath "{root.as_posix()}")')
    for literal in extra_read_literals or []:
        lines.append(f'  (literal "{Path(literal).resolve().as_posix()}")')
    lines.append('  (literal "/dev/null"))')
    lines.append("(allow file-write*")
    for root in write_roots:
        lines.append(f'  (subpath "{root.as_posix()}")')
    lines.append('  (literal "/dev/null"))')
    lines.append("(deny network*)")
    return "\n".join(lines) + "\n"


def sandbox_policy_facts() -> dict[str, Any]:
    """Honest sandbox_policy block for the execution facts on macOS."""

    return {
        "backend": BACKEND_LABEL,
        "user_namespace": False,
        "pid_namespace": False,
        "network_namespace": False,
        "ipc_namespace": False,
        "uts_namespace": False,
        "new_session": True,
        "host_project_mounted": False,
        "home_mounted": False,
        "input_snapshot_read_only": True,
        "attempt_code_read_only": True,
        "attempt_output_only_writable_mount": True,
        "locked_site_packages_read_only": True,
        "network_isolation": True,
        "host_file_reads_restricted": True,
        "memory_rlimit_enforced": False,
        "gpu_visible": False,
    }
