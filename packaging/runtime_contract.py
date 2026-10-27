"""Runtime manifest contract shared by the JW desktop packaging scripts.

The Electron shell receives ``packaging/dist/runtime`` as its
``resources/runtime`` folder.  The only supported way for the shell to discover
the private interpreter, Node.js, WebUI and bundled project files is the
``runtime-manifest.json`` document written by ``packaging/build_runtime.py``.
Every path inside that document is relative to the runtime root, which makes the
runtime relocatable (a requirement for both the macOS ``.app`` bundle and the
Windows ``win-unpacked`` folder).

This module is deliberately dependency free: it only uses the Python standard
library so it can run on a bare runner, without network access and without any
API key.  It is imported by ``build_runtime.py`` (self-check before publishing a
runtime) and by ``smoke_test.py`` (CI / local verification).
"""

from __future__ import annotations

import json
import os
import platform as _platform
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

MANIFEST_FILENAME = "runtime-manifest.json"
SCHEMA_VERSION = 1

SUPPORTED_PLATFORMS = ("darwin", "win32")
SUPPORTED_ARCHES = ("arm64", "x64")

#: Keys the Electron shell is allowed to rely on.  Anything else is reported as
#: a warning so the manifest can grow without silently breaking older shells.
REQUIRED_KEYS = (
    "schema_version",
    "platform",
    "arch",
    "python",
    "node",
    "langgraph",
    "webui",
    "projectRoot",
    "pythonVersion",
    "nodeVersion",
    "jwVersion",
    "webuiVersion",
)

#: Directory/file names that must never end up in the bundled project resources.
_FORBIDDEN_PROJECT_DIRS = {
    ".git": "fail",
    ".venv": "fail",
    "venv": "fail",
    "node_modules": "fail",
    "__pycache__": "warn",
    ".pytest_cache": "warn",
    ".ruff_cache": "warn",
    ".mypy_cache": "warn",
    ".cache": "warn",
    ".ipynb_checkpoints": "warn",
}
_FORBIDDEN_PROJECT_FILES = {
    ".env": "fail",
    ".DS_Store": "warn",
    "sessions.db": "fail",
}
_FORBIDDEN_PROJECT_SUFFIXES = {
    ".db": "fail",
    ".sqlite": "fail",
    ".sqlite3": "fail",
    ".pem": "fail",
    ".key": "fail",
    ".p12": "fail",
    ".pfx": "fail",
    ".log": "warn",
    ".pyc": "warn",
    ".pyo": "warn",
}


class ProbeError(RuntimeError):
    """Raised when a probed binary could not be executed at all."""


@dataclass
class CheckReport:
    """Result of validating a runtime directory against the contract."""

    root: Path
    manifest: dict | None = None
    checks: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    facts: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.problems

    def add(self, status: str, name: str, detail: str = "") -> None:
        self.checks.append({"name": name, "status": status, "detail": detail})
        if status == "fail":
            self.problems.append(f"{name}: {detail}" if detail else name)
        elif status == "warn":
            self.warnings.append(f"{name}: {detail}" if detail else name)

    def as_dict(self) -> dict:
        return {
            "root": str(self.root),
            "ok": self.ok,
            "platform": (self.manifest or {}).get("platform"),
            "arch": (self.manifest or {}).get("arch"),
            "problems": self.problems,
            "warnings": self.warnings,
            "facts": self.facts,
            "checks": self.checks,
        }


def host_platform() -> str:
    """Return the platform token used by the manifest (``darwin``/``win32``)."""
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("win"):
        return "win32"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def host_arch() -> str:
    """Return the architecture token used by the manifest (``arm64``/``x64``)."""
    machine = _platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("x86_64", "amd64", "x64"):
        return "x64"
    return machine


def python_relpath(platform: str) -> str:
    """Relative path of the interpreter inside the runtime root."""
    return "python/python.exe" if platform == "win32" else "python/bin/python3"


def node_relpath(platform: str) -> str:
    """Relative path of the Node.js executable inside the runtime root."""
    return "node/node.exe" if platform == "win32" else "node/bin/node"


def build_manifest(
    *,
    platform: str,
    arch: str,
    python_version: str,
    node_version: str,
    jw_version: str,
    webui_version: str,
) -> dict:
    """Create the manifest document for a fully assembled runtime."""
    python_rel = python_relpath(platform)
    return {
        "schema_version": SCHEMA_VERSION,
        "platform": platform,
        "arch": arch,
        "python": python_rel,
        "node": node_relpath(platform),
        "langgraph": {"executable": python_rel, "args": ["-m", "langgraph_cli"]},
        "webui": "webui/dist/server.js",
        "projectRoot": "project",
        "pythonVersion": python_version,
        "nodeVersion": node_version,
        "jwVersion": jw_version,
        "webuiVersion": webui_version,
    }


def load_manifest(root: Path) -> dict:
    """Read ``runtime-manifest.json`` from *root* (raises on invalid JSON)."""
    manifest_path = Path(root) / MANIFEST_FILENAME
    with manifest_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("runtime manifest must be a JSON object")
    return data


def dir_size(path: Path) -> int:
    """Total size in bytes of *path* (symlinks are not followed)."""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path, followlinks=False):
        for name in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                continue
    return total


# ---------------------------------------------------------------------------
# Probing helpers
# ---------------------------------------------------------------------------

_KEEP_ENV_KEYS = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SYSTEMROOT",
    "SystemRoot",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "LANG",
    "LC_ALL",
)


def probe_env() -> dict[str, str]:
    """A minimal environment so a probe can never inherit dev-shell leakage."""
    env = {key: os.environ[key] for key in _KEEP_ENV_KEYS if key in os.environ}
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_probe(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: float = 180.0,
) -> subprocess.CompletedProcess[str]:
    """Run a command with a sanitized environment and captured text output."""
    try:
        return subprocess.run(
            [str(part) for part in cmd],
            cwd=str(cwd),
            env=probe_env(),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:  # pragma: no cover - platform
        raise ProbeError(f"cannot execute {str(cmd[0])!r}: {exc}") from exc


def _tail(text: str, limit: int = 1200) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return "..." + text[-limit:]


def _safe_relpath(
    root: Path, rel: object, report: CheckReport, label: str
) -> Path | None:
    """Validate a manifest path and resolve it under *root*."""
    if not isinstance(rel, str) or not rel.strip():
        report.add("fail", label, f"expected a non-empty relative path, got {rel!r}")
        return None
    if "\\" in rel:
        report.add("fail", label, f"manifest paths must use '/', got {rel!r}")
        return None
    pure = PurePosixPath(rel)
    if pure.is_absolute() or any(part in ("..", "") for part in pure.parts):
        report.add("fail", label, f"not a safe relative path: {rel!r}")
        return None
    return root.joinpath(*pure.parts)


def _check_executable(path: Path, report: CheckReport, label: str) -> None:
    if not path.is_file():
        report.add("fail", label, f"missing file: {path}")
        return
    if os.name == "posix" and not os.access(path, os.X_OK):
        report.add("fail", label, f"not executable: {path}")
        return
    report.add("ok", label, str(path))


# ---------------------------------------------------------------------------
# Contract validation
# ---------------------------------------------------------------------------


def check_runtime(
    root: Path,
    *,
    require_host_match: bool = True,
    probe_binaries: bool = True,
    scan_project: bool = True,
) -> CheckReport:
    """Validate *root* against the runtime manifest contract.

    When ``probe_binaries`` is false or the target platform does not match the
    host, executable/import probes are skipped (static checks still run).
    """
    report = CheckReport(root=Path(root))
    root = report.root

    if not root.is_dir():
        report.add("fail", "runtime-root", f"not a directory: {root}")
        return report
    report.add("ok", "runtime-root", str(root))

    manifest_path = root / MANIFEST_FILENAME
    if not manifest_path.is_file():
        report.add("fail", "manifest", f"missing {MANIFEST_FILENAME} in {root}")
        return report
    try:
        manifest = load_manifest(root)
    except (OSError, ValueError) as exc:
        report.add("fail", "manifest", f"cannot parse {MANIFEST_FILENAME}: {exc}")
        return report
    report.manifest = manifest
    report.add("ok", "manifest", str(manifest_path))

    missing = [key for key in REQUIRED_KEYS if key not in manifest]
    if missing:
        report.add("fail", "manifest-keys", f"missing keys: {', '.join(missing)}")
    extra = [key for key in manifest if key not in REQUIRED_KEYS]
    if extra:
        report.add("warn", "manifest-extra-keys", ", ".join(sorted(extra)))

    if manifest.get("schema_version") != SCHEMA_VERSION:
        report.add(
            "fail",
            "schema_version",
            f"expected {SCHEMA_VERSION}, got {manifest.get('schema_version')!r}",
        )
    else:
        report.add("ok", "schema_version", str(SCHEMA_VERSION))

    platform = manifest.get("platform")
    arch = manifest.get("arch")
    if platform not in SUPPORTED_PLATFORMS:
        report.add("fail", "platform", f"unsupported platform: {platform!r}")
    else:
        report.add("ok", "platform", platform)
    if arch not in SUPPORTED_ARCHES:
        report.add("fail", "arch", f"unsupported arch: {arch!r}")
    else:
        report.add("ok", "arch", arch)

    # ---- langgraph contract -------------------------------------------------
    langgraph = manifest.get("langgraph")
    if not isinstance(langgraph, dict):
        report.add("fail", "langgraph", "expected an object with executable/args")
    else:
        if langgraph.get("executable") != manifest.get("python"):
            report.add(
                "fail",
                "langgraph.executable",
                f"must equal the python entry ({manifest.get('python')!r}), "
                f"got {langgraph.get('executable')!r}",
            )
        else:
            report.add("ok", "langgraph.executable", str(langgraph.get("executable")))
        args = langgraph.get("args")
        if args != ["-m", "langgraph_cli"]:
            report.add(
                "fail",
                "langgraph.args",
                f"expected ['-m', 'langgraph_cli'], got {args!r}",
            )
        else:
            report.add("ok", "langgraph.args", "-m langgraph_cli")

    # ---- static path checks -------------------------------------------------
    python_path = _safe_relpath(root, manifest.get("python"), report, "python")
    node_path = _safe_relpath(root, manifest.get("node"), report, "node")
    webui_path = _safe_relpath(root, manifest.get("webui"), report, "webui")
    project_root = _safe_relpath(
        root, manifest.get("projectRoot"), report, "projectRoot"
    )

    expected_python = (
        python_relpath(platform) if platform in SUPPORTED_PLATFORMS else None
    )
    expected_node = node_relpath(platform) if platform in SUPPORTED_PLATFORMS else None
    if expected_python and manifest.get("python") != expected_python:
        report.add(
            "fail",
            "python-path",
            f"expected {expected_python!r} for {platform}, got {manifest.get('python')!r}",
        )
    if expected_node and manifest.get("node") != expected_node:
        report.add(
            "fail",
            "node-path",
            f"expected {expected_node!r} for {platform}, got {manifest.get('node')!r}",
        )
    if manifest.get("webui") != "webui/dist/server.js":
        report.add(
            "fail",
            "webui-path",
            f"expected 'webui/dist/server.js', got {manifest.get('webui')!r}",
        )
    if manifest.get("projectRoot") != "project":
        report.add(
            "fail",
            "project-root",
            f"expected 'project', got {manifest.get('projectRoot')!r}",
        )

    if python_path is not None:
        _check_executable(python_path, report, "python-file")
    if node_path is not None:
        _check_executable(node_path, report, "node-file")
    if webui_path is not None:
        if not webui_path.is_file():
            report.add("fail", "webui-server", f"missing file: {webui_path}")
        elif webui_path.stat().st_size <= 0:
            report.add("fail", "webui-server", f"empty file: {webui_path}")
        else:
            report.add("ok", "webui-server", str(webui_path))

    webui_dir = root / "webui" / "dist"
    for req in ("package.json", ".next", "node_modules"):
        candidate = webui_dir / req
        if candidate.exists():
            report.add("ok", f"webui/{req}", str(candidate))
        else:
            report.add("fail", f"webui/{req}", f"missing {candidate}")

    if project_root is not None:
        if not project_root.is_dir():
            report.add("fail", "project", f"missing directory: {project_root}")
        else:
            for name in ("research", "src"):
                sub = project_root / name
                if sub.is_dir():
                    report.add("ok", f"project/{name}", str(sub))
                else:
                    report.add("fail", f"project/{name}", f"missing {sub}")
            if scan_project:
                _scan_project(project_root, report)

    licenses_dir = root / "licenses"
    if not licenses_dir.is_dir():
        report.add("fail", "licenses", f"missing directory: {licenses_dir}")
    else:
        present = sorted(p.name for p in licenses_dir.iterdir() if p.is_file())
        if not present:
            report.add("fail", "licenses", "directory is empty")
        else:
            report.add("ok", "licenses", ", ".join(present))

    # ---- executable probes --------------------------------------------------
    native = host_platform() == platform and host_arch() == arch
    if not probe_binaries:
        report.add("skip", "probes", "binary probes disabled")
    elif not native:
        message = (
            f"runtime targets {platform}/{arch} but this host is "
            f"{host_platform()}/{host_arch()}"
        )
        if require_host_match:
            report.add(
                "fail", "host-match", message + "; cannot execute target binaries"
            )
        else:
            report.add("skip", "host-match", message + "; static checks only")
    elif python_path is not None and node_path is not None and webui_path is not None:
        _probe_python(root, python_path, manifest, report)
        _probe_node(root, node_path, manifest, report)
        _probe_webui(root, node_path, webui_path, report)

    return report


def _probe_python(
    root: Path,
    python_path: Path,
    manifest: dict,
    report: CheckReport,
) -> None:
    report.facts["python"] = str(python_path)

    try:
        result = run_probe(
            [
                python_path,
                "-I",
                "-X",
                "utf8",
                "-c",
                "import jw, langgraph_cli; print('imports-ok')",
            ],
            cwd=root,
        )
    except ProbeError as exc:
        report.add("fail", "python-imports", str(exc))
    else:
        if result.returncode == 0 and "imports-ok" in result.stdout:
            report.add("ok", "python-imports", "jw + langgraph_cli importable")
        else:
            report.add(
                "fail",
                "python-imports",
                f"exit {result.returncode}: {_tail(result.stderr or result.stdout)}",
            )

    try:
        result = run_probe(
            [python_path, "-I", "-X", "utf8", "-m", "jw", "--help"], cwd=root
        )
    except ProbeError as exc:
        report.add("fail", "python-jw-cli", str(exc))
    else:
        if result.returncode == 0 and "Usage" in result.stdout:
            report.add("ok", "python-jw-cli", "-m jw --help exit 0")
        else:
            report.add(
                "fail",
                "python-jw-cli",
                f"exit {result.returncode}: {_tail(result.stderr or result.stdout)}",
            )

    try:
        result = run_probe(
            [python_path, "-I", "-X", "utf8", "-m", "langgraph_cli", "--help"],
            cwd=root,
        )
    except ProbeError as exc:
        report.add("fail", "python-langgraph-cli", str(exc))
    else:
        if result.returncode == 0:
            report.add("ok", "python-langgraph-cli", "-m langgraph_cli --help exit 0")
        else:
            report.add(
                "fail",
                "python-langgraph-cli",
                f"exit {result.returncode}: {_tail(result.stderr or result.stdout)}",
            )

    try:
        result = run_probe(
            [python_path, "-I", "-X", "utf8", "-c", "import sys; print(sys.prefix)"],
            cwd=root,
        )
        prefix = Path(result.stdout.strip()).resolve()
        expected = (root / "python").resolve()
        if result.returncode == 0 and (
            prefix == expected or expected in prefix.parents
        ):
            report.add("ok", "python-relocated", f"sys.prefix={prefix}")
        else:
            report.add(
                "fail",
                "python-relocated",
                f"sys.prefix={prefix} does not point inside {expected}",
            )
    except (ProbeError, OSError) as exc:
        report.add("fail", "python-relocated", str(exc))

    for name, cmd in (
        (
            "pythonVersion",
            [
                python_path,
                "-I",
                "-X",
                "utf8",
                "-c",
                "import platform; print(platform.python_version())",
            ],
        ),
        (
            "jwVersion",
            [
                python_path,
                "-I",
                "-X",
                "utf8",
                "-c",
                "import importlib.metadata as m; print(m.version('jw-agent'))",
            ],
        ),
    ):
        try:
            result = run_probe(cmd, cwd=root)
        except ProbeError as exc:
            report.add("fail", f"probe-{name}", str(exc))
            continue
        if result.returncode != 0:
            report.add(
                "fail",
                f"probe-{name}",
                f"exit {result.returncode}: {_tail(result.stderr or result.stdout)}",
            )
            continue
        actual = result.stdout.strip()
        report.facts[name] = actual
        declared = str(manifest.get(name, ""))
        if declared.lstrip("v") != actual.lstrip("v"):
            report.add(
                "fail",
                f"manifest-{name}",
                f"manifest says {declared!r}, runtime reports {actual!r}",
            )
        else:
            report.add("ok", f"manifest-{name}", actual)


def _probe_node(
    root: Path,
    node_path: Path,
    manifest: dict,
    report: CheckReport,
) -> None:
    report.facts["node"] = str(node_path)
    try:
        result = run_probe([node_path, "--version"], cwd=root)
    except ProbeError as exc:
        report.add("fail", "node-version", str(exc))
        return
    if result.returncode != 0:
        report.add(
            "fail",
            "node-version",
            f"exit {result.returncode}: {_tail(result.stderr or result.stdout)}",
        )
        return
    actual = result.stdout.strip()
    report.facts["nodeVersion"] = actual
    declared = str(manifest.get("nodeVersion", ""))
    if declared.lstrip("v") != actual.lstrip("v"):
        report.add(
            "fail",
            "manifest-nodeVersion",
            f"manifest says {declared!r}, runtime reports {actual!r}",
        )
        return
    major = actual.lstrip("v").split(".")[0]
    report.add("ok", "node-version", f"{actual} (major {major})")


def _probe_webui(
    root: Path, node_path: Path, webui_path: Path, report: CheckReport
) -> None:
    dist_dir = root / "webui" / "dist"
    package_json = dist_dir / "package.json"
    if package_json.is_file():
        try:
            payload = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            report.add("warn", "webui-package-json", f"unreadable: {exc}")
            payload = {}
        version = payload.get("version")
        if version:
            report.facts["webuiVersion"] = str(version)
            declared = str((report.manifest or {}).get("webuiVersion", ""))
            if declared != str(version):
                report.add(
                    "fail",
                    "manifest-webuiVersion",
                    f"manifest says {declared!r}, webui/dist/package.json says {version!r}",
                )
            else:
                report.add("ok", "manifest-webuiVersion", str(version))
        if payload.get("type") not in (None, "module"):
            report.add(
                "warn",
                "webui-module-type",
                f"expected type=module, got {payload.get('type')!r}",
            )

    try:
        result = run_probe([node_path, "--check", webui_path], cwd=root)
    except ProbeError as exc:
        report.add("fail", "webui-syntax", str(exc))
        return
    if result.returncode == 0:
        report.add("ok", "webui-syntax", "node --check server.js")
    else:
        report.add("fail", "webui-syntax", _tail(result.stderr or result.stdout))


def _scan_project(project_root: Path, report: CheckReport) -> None:
    """Reject secrets/databases/caches that never belong in a shipped runtime."""
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(project_root, followlinks=False):
        scanned += len(dirnames) + len(filenames)
        if scanned > 250_000:
            report.add("warn", "project-scan", "scan truncated after 250000 entries")
            return
        for name in list(dirnames):
            kind = _FORBIDDEN_PROJECT_DIRS.get(name)
            if kind:
                report.add(
                    kind,
                    "project-forbidden-dir",
                    f"{Path(dirpath, name).relative_to(project_root)}",
                )
            if name.startswith(".venv"):
                report.add(
                    "fail",
                    "project-forbidden-dir",
                    f"{Path(dirpath, name).relative_to(project_root)}",
                )
        for name in filenames:
            kind = _FORBIDDEN_PROJECT_FILES.get(name)
            if name.startswith(".env") and name not in (
                ".env.example",
                ".env.template",
            ):
                kind = kind or "fail"
            if kind is None:
                for suffix, suffix_kind in _FORBIDDEN_PROJECT_SUFFIXES.items():
                    if name.endswith(suffix):
                        kind = suffix_kind
                        break
            if kind:
                report.add(
                    kind,
                    "project-forbidden-file",
                    f"{Path(dirpath, name).relative_to(project_root)}",
                )
