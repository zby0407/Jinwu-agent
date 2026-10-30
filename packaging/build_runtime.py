#!/usr/bin/env python3
"""Assemble the relocatable JW desktop runtime.

The output directory (``packaging/dist/runtime`` by default) is what
electron-builder copies into the packaged app as ``resources/runtime``.  The
Electron shell does **not** run installers on the end user machine: everything
this script assembles must already work from the final path.

Layout produced::

    runtime/
      python/                 # relocatable CPython 3.12 + locked deps + JW wheel
      node/                   # pinned Node.js LTS distribution
      webui/dist/             # Next.js standalone server (server.js + traced deps)
      jw/subagents/           # shipped Skills (SKILL.md bundles + skill_registry.json)
      project/research/       # bundled project resources (allow-list)
      project/src/
      licenses/               # shipped license files
      runtime-manifest.json   # contract consumed by the Electron shell

Design notes
------------
* The build must run on a **native** runner: the script refuses to cross-build
  because it has to execute the target interpreter to verify imports.
* Python comes from ``uv python install`` (python-build-standalone builds are
  relocatable).  Dependencies come from the frozen ``uv.lock`` so a runtime is
  reproducible from a commit.
* Node.js is downloaded from nodejs.org and verified against the official
  ``SHASUMS256.txt`` before extraction.
* All download/cache locations can be redirected with CLI flags or environment
  variables (``UV_CACHE_DIR``, ``UV_PYTHON_INSTALL_DIR``, ``npm_config_cache``,
  ``JW_BUILD_CACHE_DIR``, ``JW_PACKAGING_WORK_DIR``, ``JW_NODE_DIST_URL``) so CI
  never depends on a warm user home directory.
* Build artefacts are only written below ``packaging/dist`` (or the ``--out`` /
  ``--work-dir`` / ``--cache-dir`` paths you pass explicitly).

Escape hatches for air-gapped/local verification (never used by CI):

* ``--python-dir``        reuse an already extracted relocatable CPython
* ``--node-dist``         reuse an already extracted Node.js distribution
* ``--jw-wheel``          reuse an already built JW wheel
* ``--deps-site-packages`` install the locked dependencies by copying an
  existing site-packages directory instead of downloading from PyPI
* ``--webui-dist``        reuse an existing Next.js standalone build

Usage::

    python packaging/build_runtime.py --clean
    python packaging/build_runtime.py --clean --node-version 22.22.3
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform as _platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_OUT = SCRIPT_DIR / "dist" / "runtime"
DEFAULT_NODE_VERSION = "22.22.3"
SUPPORTED_PYTHON_SERIES = "3.12"
MANIFEST_FILENAME = "runtime-manifest.json"

PYTHON_BOOTSTRAP_IGNORE = (
    "__editable__*",
    "__editable___*",
    "jw_agent-*",
    "*.egg-link",
)

PROJECT_ALLOW_LIST = ("research", "src")
# The WebUI's skill page (``webui/src/lib/server/builtinSkills.js``) discovers
# the shipped Skills by walking candidate roots and reading
# ``<root>/jw/subagents/<bundle>/skills/<skill>/SKILL.md``. One of those roots is
# the runtime directory itself (``resolve(cwd, '..', '..')`` from
# ``webui/dist``), so the Skills must land at ``<runtime>/jw/subagents`` — the
# copy inside the wheel's site-packages is invisible to the WebUI.
BUNDLED_SKILLS_PACKAGE_DIR = ("jw", "subagents")
PROJECT_EXCLUDED_DIRS = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".cache",
    ".ipynb_checkpoints",
    "output",
    "outputs",
    "tmp",
    ".tmp",
    "tests",
    "workspace",
    "dist",
    "build",
}
PROJECT_EXCLUDED_FILES = {
    ".env",
    ".DS_Store",
    "sessions.db",
}
PROJECT_EXCLUDED_SUFFIXES = (
    ".db",
    ".sqlite",
    ".sqlite3",
    ".log",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".pyc",
    ".pyo",
)

LICENSE_FILES = ("LICENSE", "NOTICE", "THIRD_PARTY_LICENSES.md")


class BuildError(RuntimeError):
    """A packaging step failed; the half-built runtime is removed."""


# ---------------------------------------------------------------------------
# logging / subprocess helpers
# ---------------------------------------------------------------------------


def log(message: str) -> None:
    print(f"[build_runtime] {message}", flush=True)


def warn(message: str) -> None:
    print(f"[build_runtime] WARNING: {message}", flush=True)


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    label: str | None = None,
    timeout: float = 3_600.0,
    print_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a build step, streaming a bounded tail of its output to the log."""
    label = label or " ".join(str(part) for part in cmd[:3])
    log(f"$ {subprocess.list2cmdline([str(p) for p in cmd])}")
    try:
        result = subprocess.run(
            [str(part) for part in cmd],
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise BuildError(f"{label}: command not found: {cmd[0]!r}") from exc
    except subprocess.TimeoutExpired as exc:
        raise BuildError(f"{label}: timed out after {timeout:.0f}s") from exc

    if print_output:
        for stream_name, stream in (
            ("stdout", result.stdout),
            ("stderr", result.stderr),
        ):
            text = (stream or "").strip()
            if not text:
                continue
            lines = text.splitlines()
            if len(lines) > 60:
                lines = [*lines[:25], "... (truncated) ...", *lines[-25:]]
            for line in lines:
                print(f"    {stream_name} | {line}", flush=True)

    if result.returncode != 0:
        raise BuildError(
            f"{label} failed with exit code {result.returncode}\n"
            f"command: {subprocess.list2cmdline([str(p) for p in cmd])}\n"
            f"stderr: {(result.stderr or '').strip()[-4000:]}"
        )
    return result


def uv_executable() -> str:
    found = shutil.which("uv")
    if found:
        return found
    raise BuildError(
        "uv is required to install the private Python and build the JW wheel; "
        "install it from https://docs.astral.sh/uv/ (CI uses astral-sh/setup-uv)."
    )


def npm_argv() -> list[str]:
    """Return an argv prefix that works on POSIX, Windows and Git-Bash."""
    npm = shutil.which("npm")
    if os.name == "nt":
        npm_cmd = shutil.which("npm.cmd") or npm
        if not npm_cmd:
            raise BuildError("npm is required to build the WebUI")
        # ``.cmd`` scripts must go through cmd.exe; CreateProcess cannot run them.
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", npm_cmd]
    if not npm:
        raise BuildError("npm is required to build the WebUI")
    return [npm]


# ---------------------------------------------------------------------------
# environment
# ---------------------------------------------------------------------------


def build_environment(work_dir: Path, cache_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONUTF8", "1")
    env["UV_CACHE_DIR"] = os.environ.get("UV_CACHE_DIR") or str(cache_dir / "uv")
    env["UV_PYTHON_INSTALL_DIR"] = os.environ.get("UV_PYTHON_INSTALL_DIR") or str(
        work_dir / "uv-python"
    )
    env["UV_NO_PROGRESS"] = "1"
    env["UV_LINK_MODE"] = "copy"
    env["npm_config_cache"] = os.environ.get("npm_config_cache") or str(
        cache_dir / "npm"
    )
    env["npm_config_fund"] = "false"
    env["npm_config_audit"] = "false"
    env["npm_config_update_notifier"] = "false"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return env


# ---------------------------------------------------------------------------
# download helpers
# ---------------------------------------------------------------------------


def _http_get(url: str, *, timeout: float = 120.0, retries: int = 3) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "jw-desktop-packaging/1.0"}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
            if attempt < retries:
                delay = 2 ** (attempt - 1)
                warn(
                    f"download attempt {attempt}/{retries} failed ({exc}); retrying in {delay}s"
                )
                time.sleep(delay)
    raise BuildError(f"cannot download {url}: {last_error}")


def _download_file(url: str, destination: Path, *, timeout: float = 900.0) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    log(f"downloading {url}")
    request = urllib.request.Request(
        url, headers={"User-Agent": "jw-desktop-packaging/1.0"}
    )
    with (
        urllib.request.urlopen(request, timeout=timeout) as response,
        partial.open("wb") as handle,
    ):
        total = int(response.headers.get("Content-Length") or 0)
        copied = 0
        last_report = 0.0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            copied += len(chunk)
            now = time.monotonic()
            if now - last_report >= 5:
                last_report = now
                if total:
                    log(f"  {copied / 1048576:.1f} / {total / 1048576:.1f} MiB")
                else:
                    log(f"  {copied / 1048576:.1f} MiB")
    partial.replace(destination)
    log(
        f"downloaded {destination.name} ({destination.stat().st_size / 1048576:.1f} MiB)"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _node_archive_name(node_version: str, target_platform: str, arch: str) -> str:
    if target_platform == "darwin":
        return f"node-v{node_version}-darwin-{arch}.tar.gz"
    return f"node-v{node_version}-win-{arch}.zip"


def _fetch_shasums(
    node_version: str, cache_dir: Path, target_platform: str
) -> dict[str, str]:
    """Return ``{archive name: sha256}`` from the official SHASUMS256.txt."""
    base = os.environ.get("JW_NODE_DIST_URL", "https://nodejs.org/dist").rstrip("/")
    url = f"{base}/v{node_version}/SHASUMS256.txt"
    cached = cache_dir / "downloads" / f"SHASUMS256-v{node_version}.txt"
    text: str | None = None
    try:
        text = _http_get(url).decode("utf-8", errors="replace")
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(text, encoding="utf-8")
    except BuildError as exc:
        if cached.is_file():
            warn(f"cannot refresh {url} ({exc}); using cached SHASUMS256.txt")
            text = cached.read_text(encoding="utf-8")
        else:
            raise BuildError(
                f"cannot download the official Node.js checksums from {url}: {exc}"
            ) from exc

    checksums: dict[str, str] = {}
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        digest, name = parts[0].strip(), parts[1].strip().lstrip("*")
        if re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            checksums[name] = digest.lower()
    if not checksums:
        raise BuildError(f"SHASUMS256.txt from {url} did not contain any checksums")
    return checksums


def _safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                target = Path(member.filename)
                if target.is_absolute() or ".." in target.parts:
                    raise BuildError(
                        f"unsafe path in {archive.name}: {member.filename}"
                    )
            zf.extractall(destination)
        return

    with tarfile.open(archive) as tf:
        for member in tf.getmembers():
            target = Path(member.name)
            if target.is_absolute() or ".." in target.parts:
                raise BuildError(f"unsafe path in {archive.name}: {member.name}")
        try:
            tf.extractall(destination, filter="data")
        except TypeError:  # Python < 3.12 without extraction filters
            tf.extractall(destination)


def _single_top_level_dir(root: Path) -> Path:
    entries = [entry for entry in root.iterdir() if not entry.name.startswith(".")]
    if len(entries) != 1 or not entries[0].is_dir():
        raise BuildError(
            f"expected exactly one top-level directory in {root}, got {entries}"
        )
    return entries[0]


# ---------------------------------------------------------------------------
# runtime assembly steps
# ---------------------------------------------------------------------------


def _interpreter_in(root: Path, target_platform: str) -> Path | None:
    candidate = root / ("python.exe" if target_platform == "win32" else "bin/python3")
    return candidate if candidate.is_file() else None


def _probe_version(binary: Path, *args: str, cwd: Path) -> str:
    result = run(
        [binary, *args], cwd=cwd, label=f"probe {binary.name}", print_output=False
    )
    return result.stdout.strip()


def prepare_python(
    *,
    staging: Path,
    work_dir: Path,
    env: dict[str, str],
    args: argparse.Namespace,
) -> Path:
    destination = staging / "python"
    if args.python_dir:
        source = Path(args.python_dir).expanduser().resolve()
        if not source.is_dir():
            raise BuildError(f"--python-dir does not exist: {source}")
        log(f"using prebuilt Python from {source}")
        # Dependencies are installed/copied in the next phase. Avoid copying an
        # entire populated interpreter twice when using the offline escape hatch.
        shutil.copytree(
            source,
            destination,
            symlinks=True,
            ignore=shutil.ignore_patterns("site-packages")
            if args.deps_site_packages
            else None,
        )
    else:
        install_dir = Path(env["UV_PYTHON_INSTALL_DIR"])
        install_dir.mkdir(parents=True, exist_ok=True)
        run(
            [
                uv_executable(),
                "python",
                "install",
                SUPPORTED_PYTHON_SERIES,
                "--install-dir",
                str(install_dir),
            ],
            env=env,
            label="uv python install",
        )
        candidates = sorted(
            (
                entry
                for entry in install_dir.iterdir()
                if entry.is_dir()
                and entry.name.startswith(f"cpython-{SUPPORTED_PYTHON_SERIES}")
                and _interpreter_in(entry, args.platform)
            ),
            key=lambda entry: entry.name,
        )
        if not candidates:
            raise BuildError(
                f"uv python install produced no CPython {SUPPORTED_PYTHON_SERIES} "
                f"in {install_dir}"
            )
        source = candidates[-1]
        log(f"using uv-managed Python at {source}")
        shutil.copytree(source, destination, symlinks=True)

    interpreter = _interpreter_in(destination, args.platform)
    if interpreter is None:
        raise BuildError(
            f"copied Python distribution has no interpreter at the expected path "
            f"({destination / ('python.exe' if args.platform == 'win32' else 'bin/python3')})"
        )
    interpreter.chmod(interpreter.stat().st_mode | 0o755)

    # python-build-standalone marks its installs as externally managed (they are
    # meant to be used through a uv venv).  The desktop runtime *is* the private
    # environment, so the marker is removed and dependencies live in its own
    # site-packages.
    markers = list(destination.rglob("EXTERNALLY-MANAGED"))
    for marker in markers:
        marker.unlink()
        log(f"removed uv externally-managed marker: {marker.relative_to(destination)}")

    version = _probe_version(
        interpreter,
        "-I",
        "-c",
        "import platform; print(platform.python_version())",
        cwd=destination,
    )
    if not version.startswith(SUPPORTED_PYTHON_SERIES + "."):
        raise BuildError(f"expected CPython {SUPPORTED_PYTHON_SERIES}.x, got {version}")
    log(f"private Python {version} ready")
    return interpreter


def _site_packages_dir(
    interpreter: Path, python_root: Path, env: dict[str, str]
) -> Path:
    result = run(
        [
            interpreter,
            "-I",
            "-c",
            "import sysconfig; print(sysconfig.get_paths()['purelib'])",
        ],
        cwd=python_root,
        env=env,
        label="locate site-packages",
        print_output=False,
    )
    site_packages = Path(result.stdout.strip()).resolve()
    python_root = python_root.resolve()
    if python_root not in site_packages.parents:
        raise BuildError(
            f"site-packages {site_packages} is not inside the private Python {python_root}"
        )
    return site_packages


def _copy_site_packages(
    source: Path, destination: Path, *, excludes: tuple[str, ...]
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    skipped: list[str] = []
    for entry in source.iterdir():
        if any(entry.match(pattern) for pattern in excludes):
            skipped.append(entry.name)
            continue
        target = destination / entry.name
        if entry.is_dir() and not entry.is_symlink():
            shutil.copytree(entry, target, symlinks=True, dirs_exist_ok=True)
        else:
            shutil.copy2(entry, target, follow_symlinks=False)
    if skipped:
        log(
            f"skipped editable/stale artefacts in {source.name}: {', '.join(sorted(skipped))}"
        )


def install_python_packages(
    *,
    interpreter: Path,
    python_root: Path,
    work_dir: Path,
    staging: Path,
    env: dict[str, str],
    args: argparse.Namespace,
) -> Path:
    """Install locked dependencies plus the JW wheel into the private Python."""
    site_packages = _site_packages_dir(interpreter, python_root, env)

    if args.deps_site_packages:
        source = Path(args.deps_site_packages).expanduser().resolve()
        if not source.is_dir():
            raise BuildError(f"--deps-site-packages does not exist: {source}")
        warn(
            "installing dependencies from --deps-site-packages (air-gapped escape "
            "hatch); CI uses the frozen uv.lock instead"
        )
        _copy_site_packages(
            source,
            site_packages,
            excludes=(
                *PYTHON_BOOTSTRAP_IGNORE,
                "jw",
                "jw-*",
                "jw_agent-*",
                "research_review",
                "research_quality",
            ),
        )
    else:
        lock_file = REPO_ROOT / "uv.lock"
        if not lock_file.is_file():
            raise BuildError(f"missing {lock_file}; a frozen lockfile is required")
        requirements = work_dir / "requirements-runtime.txt"
        run(
            [
                uv_executable(),
                "export",
                "--frozen",
                "--no-dev",
                "--no-emit-project",
                "--format",
                "requirements-txt",
                "--output-file",
                str(requirements),
            ],
            cwd=REPO_ROOT,
            env=env,
            label="uv export",
        )
        run(
            [
                uv_executable(),
                "pip",
                "install",
                "--python",
                str(interpreter),
                "--break-system-packages",
                "--require-hashes",
                "--requirement",
                str(requirements),
            ],
            cwd=REPO_ROOT,
            env=env,
            label="install locked Python dependencies",
            timeout=5_400.0,
        )

    wheel = _resolve_wheel(work_dir=work_dir, env=env, args=args)
    run(
        [
            uv_executable(),
            "pip",
            "install",
            "--python",
            str(interpreter),
            "--break-system-packages",
            "--no-deps",
            str(wheel),
        ],
        cwd=REPO_ROOT,
        env=env,
        label="install JW wheel",
    )
    run(
        [interpreter, "-I", "-c", "import jw, langgraph_cli; print('imports-ok')"],
        cwd=staging,
        env=env,
        label="verify private Python imports",
    )
    return wheel


def _wheel_version(wheel: Path) -> str | None:
    match = re.match(r"jw_agent-([0-9][^-]*)-", wheel.name)
    return match.group(1) if match else None


def _resolve_wheel(
    *, work_dir: Path, env: dict[str, str], args: argparse.Namespace
) -> Path:
    if args.jw_wheel:
        wheel = Path(args.jw_wheel).expanduser().resolve()
        if not wheel.is_file() or wheel.suffix != ".whl":
            raise BuildError(f"--jw-wheel is not a wheel file: {wheel}")
        log(f"using prebuilt JW wheel {wheel.name}")
        return wheel

    wheels_dir = work_dir / "wheels"
    wheels_dir.mkdir(parents=True, exist_ok=True)
    run(
        [
            uv_executable(),
            "build",
            "--wheel",
            "--out-dir",
            str(wheels_dir),
        ],
        cwd=REPO_ROOT,
        env=env,
        label="uv build --wheel",
        timeout=1_800.0,
    )
    candidates = sorted(wheels_dir.glob("jw_agent-*.whl"))
    if not candidates:
        raise BuildError(f"uv build produced no jw_agent wheel in {wheels_dir}")
    return candidates[-1]


def prepare_node(
    *,
    staging: Path,
    work_dir: Path,
    cache_dir: Path,
    env: dict[str, str],
    args: argparse.Namespace,
) -> Path:
    destination = staging / "node"
    if args.node_dist:
        source = Path(args.node_dist).expanduser().resolve()
        if not source.is_dir():
            raise BuildError(f"--node-dist does not exist: {source}")
        log(f"using prebuilt Node.js distribution from {source}")
        shutil.copytree(source, destination, symlinks=True)
    else:
        archive_name = _node_archive_name(args.node_version, args.platform, args.arch)
        checksums = _fetch_shasums(args.node_version, cache_dir, args.platform)
        if archive_name not in checksums:
            raise BuildError(
                f"{archive_name} is not listed in the official SHASUMS256.txt for "
                f"Node.js v{args.node_version}"
            )
        expected = checksums[archive_name]
        archive = cache_dir / "downloads" / archive_name
        if archive.is_file():
            actual = sha256_file(archive)
            if actual == expected:
                log(f"reusing cached {archive_name} (sha256 verified)")
            else:
                warn(f"cached {archive_name} hash mismatch; re-downloading")
                archive.unlink()
        if not archive.is_file():
            base = os.environ.get("JW_NODE_DIST_URL", "https://nodejs.org/dist").rstrip(
                "/"
            )
            _download_file(f"{base}/v{args.node_version}/{archive_name}", archive)
            actual = sha256_file(archive)
            if actual != expected:
                archive.unlink(missing_ok=True)
                raise BuildError(
                    f"SHA-256 mismatch for {archive_name}: expected {expected}, "
                    f"got {actual}"
                )
            log(f"verified SHA-256 for {archive_name}: {expected}")
        extract_dir = work_dir / "node-extract"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        _safe_extract(archive, extract_dir)
        shutil.copytree(_single_top_level_dir(extract_dir), destination, symlinks=True)

    node_binary = destination / ("node.exe" if args.platform == "win32" else "bin/node")
    if not node_binary.is_file():
        raise BuildError(f"Node.js distribution has no executable at {node_binary}")
    node_binary.chmod(node_binary.stat().st_mode | 0o755)
    version = _probe_version(node_binary, "--version", cwd=destination).lstrip("v")
    expected_version = args.node_version.lstrip("v")
    if version != expected_version:
        warn(
            f"Node.js version mismatch: expected {expected_version}, got {version} "
            f"(manifest records the actual version)"
        )
    log(f"private Node.js {version} ready")
    return node_binary


def _webui_dirs() -> Path:
    return REPO_ROOT / "webui"


def validate_webui_dist(dist: Path, *, origin: str) -> None:
    """Fail loudly instead of shipping a half-built Next.js standalone server."""
    if not dist.is_dir():
        raise BuildError(f"{origin}: WebUI dist directory does not exist: {dist}")
    required_files = ("server.js", "package.json")
    missing_files = [name for name in required_files if not (dist / name).is_file()]
    required_dirs = (".next", "node_modules")
    missing_dirs = [name for name in required_dirs if not (dist / name).is_dir()]
    problems = []
    if missing_files:
        problems.append(f"missing files: {', '.join(missing_files)}")
    if missing_dirs:
        problems.append(f"missing directories: {', '.join(missing_dirs)}")
    if problems:
        raise BuildError(
            f"{origin}: {dist} is not a valid Next.js standalone build ({'; '.join(problems)}). "
            "Run `npm ci && npm run build` in webui/ or pass --webui-dist."
        )


def prepare_webui(
    *,
    staging: Path,
    env: dict[str, str],
    args: argparse.Namespace,
) -> Path:
    if args.webui_dist:
        source = Path(args.webui_dist).expanduser().resolve()
        validate_webui_dist(source, origin="--webui-dist")
        log(f"reusing WebUI standalone build from {source}")
    elif args.skip_webui:
        source = _webui_dirs() / "dist"
        validate_webui_dist(source, origin="--skip-webui")
        warn("--skip-webui: reusing the existing webui/dist build")
    else:
        webui = _webui_dirs()
        lock = webui / "package-lock.json"
        if not lock.is_file():
            raise BuildError(f"missing {lock}; cannot run a reproducible npm ci")
        npm = npm_argv()
        run([*npm, "ci"], cwd=webui, env=env, label="npm ci (webui)", timeout=2_400.0)
        run(
            [*npm, "run", "build"],
            cwd=webui,
            env=env,
            label="npm run build (webui)",
            timeout=2_400.0,
        )
        source = webui / "dist"
        validate_webui_dist(source, origin="npm run build")

    destination = staging / "webui" / "dist"
    destination.parent.mkdir(parents=True, exist_ok=True)
    log(f"copying WebUI standalone build ({dir_size_mib(source):.1f} MiB)")
    shutil.copytree(source, destination, symlinks=True)
    return source


def _project_ignore(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        full = Path(directory) / name
        if full.is_dir():
            if name in PROJECT_EXCLUDED_DIRS:
                ignored.add(name)
        elif (
            name in PROJECT_EXCLUDED_FILES
            or name.startswith(".env")
            or name.endswith(PROJECT_EXCLUDED_SUFFIXES)
        ):
            ignored.add(name)
    return ignored


def prepare_project_resources(*, staging: Path, args: argparse.Namespace) -> None:
    project_root = staging / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    for name in PROJECT_ALLOW_LIST:
        source = REPO_ROOT / name
        if not source.is_dir():
            raise BuildError(
                f"required project resource directory is missing: {source}"
            )
        log(f"copying project resource {name}/ ({dir_size_mib(source):.1f} MiB)")
        shutil.copytree(
            source,
            project_root / name,
            symlinks=True,
            ignore=_project_ignore,
        )

    if args.include_shared_project:
        shared = REPO_ROOT / "projects" / "default" / "shared"
        if not shared.is_dir():
            raise BuildError(f"--include-shared-project: missing {shared}")
        log(f"copying optional shared project data ({dir_size_mib(shared):.1f} MiB)")
        shutil.copytree(
            shared,
            project_root / "projects" / "default" / "shared",
            symlinks=True,
            ignore=_project_ignore,
        )


def prepare_bundled_skills(*, staging: Path) -> None:
    """Copy the shipped Skills (``jw/subagents``) to ``<runtime>/jw/subagents``.

    The WebUI shell has no access to the wheel's site-packages tree, so the
    Skills bundle is published next to ``python/`` / ``webui/`` where
    ``skillProjectRoots()`` already looks for ``jw/subagents``. The registry
    JSON travels with it, so the skill page can also resolve agent assignments.
    """
    source = REPO_ROOT.joinpath(*BUNDLED_SKILLS_PACKAGE_DIR)
    if not source.is_dir():
        raise BuildError(f"required bundled skills directory is missing: {source}")
    registry = source / "skill_registry.json"
    if not registry.is_file():
        raise BuildError(f"required skill registry is missing: {registry}")
    skills_root = source / "core" / "skills"
    if not skills_root.is_dir():
        raise BuildError(f"required bundled skills are missing: {skills_root}")
    target = staging.joinpath(*BUNDLED_SKILLS_PACKAGE_DIR)
    log(f"copying bundled skills ({dir_size_mib(source):.1f} MiB)")
    shutil.copytree(source, target, symlinks=True, ignore=_project_ignore)


def prepare_licenses(*, staging: Path, node_root: Path | None) -> None:
    licenses = staging / "licenses"
    licenses.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in LICENSE_FILES:
        source = REPO_ROOT / name
        if source.is_file():
            shutil.copy2(source, licenses / name)
            copied.append(name)
    if not copied:
        raise BuildError(
            "none of the license files exist in the repository root: "
            + ", ".join(LICENSE_FILES)
        )

    if node_root is not None:
        for candidate in ("LICENSE", "LICENSE.md"):
            node_license = node_root / candidate
            if node_license.is_file():
                shutil.copy2(node_license, licenses / "node-LICENSE.txt")
                copied.append("node-LICENSE.txt")
                break

    python_license = staging / "python" / "LICENSE"
    if python_license.is_file():
        shutil.copy2(python_license, licenses / "python-LICENSE.txt")
        copied.append("python-LICENSE.txt")

    log(f"licenses: {', '.join(copied)}")


def dir_size_mib(path: Path) -> float:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path, followlinks=False):
        for name in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                continue
    return total / 1048576


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------


def detect_versions(
    *,
    interpreter: Path,
    node_binary: Path,
    webui_source: Path,
    staging: Path,
    env: dict[str, str],
    args: argparse.Namespace,
) -> dict[str, str]:
    python_version = _probe_version(
        interpreter,
        "-I",
        "-c",
        "import platform; print(platform.python_version())",
        cwd=staging,
    )
    node_version = _probe_version(node_binary, "--version", cwd=staging).lstrip("v")

    jw_version = ""
    try:
        jw_version = _probe_version(
            interpreter,
            "-I",
            "-c",
            "import importlib.metadata as m; print(m.version('jw-agent'))",
            cwd=staging,
        )
    except BuildError:
        wheel = getattr(args, "_wheel", None)
        if isinstance(wheel, Path):
            jw_version = _wheel_version(wheel) or ""
    if not jw_version:
        raise BuildError("cannot determine the bundled JW version")

    webui_version = ""
    webui_package = webui_source / "package.json"
    if webui_package.is_file():
        try:
            webui_version = str(
                json.loads(webui_package.read_text(encoding="utf-8")).get("version")
                or ""
            )
        except (OSError, ValueError) as exc:
            warn(f"cannot read {webui_package}: {exc}")
    if not webui_version:
        source_package = _webui_dirs() / "package.json"
        if source_package.is_file():
            try:
                webui_version = str(
                    json.loads(source_package.read_text(encoding="utf-8")).get(
                        "version"
                    )
                    or ""
                )
            except (OSError, ValueError):
                webui_version = ""
    if not webui_version:
        raise BuildError("cannot determine the bundled WebUI version")

    return {
        "pythonVersion": python_version,
        "nodeVersion": node_version,
        "jwVersion": jw_version,
        "webuiVersion": webui_version,
    }


def write_manifest(
    staging: Path, *, args: argparse.Namespace, versions: dict[str, str]
) -> dict:
    python_rel = (
        "python/python.exe" if args.platform == "win32" else "python/bin/python3"
    )
    node_rel = "node/node.exe" if args.platform == "win32" else "node/bin/node"
    manifest = {
        "schema_version": 1,
        "platform": args.platform,
        "arch": args.arch,
        "python": python_rel,
        "node": node_rel,
        "langgraph": {"executable": python_rel, "args": ["-m", "langgraph_cli"]},
        "webui": "webui/dist/server.js",
        "projectRoot": "project",
        "pythonVersion": versions["pythonVersion"],
        "nodeVersion": versions["nodeVersion"],
        "jwVersion": versions["jwVersion"],
        "webuiVersion": versions["webuiVersion"],
    }
    manifest_path = staging / MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    log(f"wrote {manifest_path}")
    return manifest


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def host_platform() -> str:
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("win"):
        return "win32"
    return sys.platform


def host_arch() -> str:
    machine = _platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("x86_64", "amd64"):
        return "x64"
    return machine


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assemble the relocatable JW desktop runtime.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="Output runtime directory (copied by electron-builder as resources/runtime).",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove an existing output directory before building.",
    )
    parser.add_argument(
        "--platform",
        choices=("darwin", "win32"),
        default=host_platform(),
        help="Target platform; must match the host (native builds only).",
    )
    parser.add_argument(
        "--arch",
        choices=("arm64", "x64"),
        default=host_arch(),
        help="Target architecture; must match the host (native builds only).",
    )
    parser.add_argument(
        "--node-version",
        default=DEFAULT_NODE_VERSION,
        help="Exact Node.js LTS version to bundle (must exist on nodejs.org).",
    )
    parser.add_argument(
        "--skip-webui",
        action="store_true",
        help="Do not run npm build; reuse the existing webui/dist standalone server.",
    )
    parser.add_argument(
        "--webui-dist",
        type=Path,
        default=None,
        help="Reuse this Next.js standalone output instead of building the WebUI.",
    )
    parser.add_argument(
        "--include-shared-project",
        action="store_true",
        help="Also bundle projects/default/shared (can be large; off by default).",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Scratch directory (env JW_PACKAGING_WORK_DIR); defaults below packaging/dist.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Download/cache directory (env JW_BUILD_CACHE_DIR).",
    )
    parser.add_argument(
        "--keep-work",
        action="store_true",
        help="Keep the scratch directory for debugging.",
    )

    offline = parser.add_argument_group(
        "air-gapped / local verification escape hatches",
        "CI never uses these; they allow rebuilding a runtime without network.",
    )
    offline.add_argument(
        "--python-dir",
        type=Path,
        help="Reuse an extracted relocatable CPython distribution instead of uv python install.",
    )
    offline.add_argument(
        "--node-dist",
        type=Path,
        help="Reuse an extracted Node.js distribution instead of downloading it.",
    )
    offline.add_argument(
        "--jw-wheel",
        type=Path,
        help="Reuse an already built jw_agent wheel instead of uv build.",
    )
    offline.add_argument(
        "--deps-site-packages",
        type=Path,
        help="Copy locked dependencies from this site-packages directory instead of PyPI.",
    )
    return parser.parse_args(argv)


def validate_native_build(args: argparse.Namespace) -> None:
    if args.platform != host_platform() or args.arch != host_arch():
        raise BuildError(
            f"refusing to cross-build: requested {args.platform}/{args.arch}, host is "
            f"{host_platform()}/{host_arch()}. The build executes the target interpreter "
            "and Node.js binary to verify the runtime, so it must run on a native runner."
        )


def _remove(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def precompile_runtime(out_dir: Path, *, platform: str, env: dict[str, str]) -> None:
    """Compile the private runtime after all dependencies and JW are installed.

    The packaged runtime is replaced as a unit on upgrade. Build-owned bytecode
    avoids reopening thousands of source files at every Windows launch. Force
    regeneration here; source development environments keep normal validation.
    """
    run(
        [
            _interpreter_in(out_dir / "python", platform),
            "-I",
            "-m",
            "compileall",
            "-q",
            "-f",
            "-j",
            "1",
            "--invalidation-mode",
            "unchecked-hash",
            "-s",
            str(out_dir),
            "-p",
            "runtime",
            str(out_dir / "python" / ("Lib" if platform == "win32" else "lib")),
        ],
        cwd=out_dir,
        env=env,
        label="precompile private Python",
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_dir = args.out.expanduser().resolve()
    work_dir = (
        Path(args.work_dir).expanduser().resolve()
        if args.work_dir
        else out_dir.parent / ".work"
    )
    cache_dir = (
        Path(args.cache_dir).expanduser().resolve()
        if args.cache_dir
        else Path(os.environ.get("JW_BUILD_CACHE_DIR") or out_dir.parent / "cache")
    )

    staging = work_dir / "staging"
    moved = False

    try:
        validate_native_build(args)
        if out_dir.exists():
            if args.clean:
                log(f"removing existing runtime {out_dir}")
                _remove(out_dir)
            else:
                raise BuildError(
                    f"{out_dir} already exists; pass --clean to rebuild it"
                )

        _remove(staging)
        staging.mkdir(parents=True, exist_ok=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)
        env = build_environment(work_dir, cache_dir)
        log(f"platform={args.platform} arch={args.arch} node={args.node_version}")
        log(f"out={out_dir}")
        log(f"work={work_dir}")
        log(f"cache={cache_dir}")

        log("== private Python ==")
        interpreter = prepare_python(
            staging=staging, work_dir=work_dir, env=env, args=args
        )
        python_root = staging / "python"

        log("== Python dependencies + JW wheel ==")
        wheel = install_python_packages(
            interpreter=interpreter,
            python_root=python_root,
            work_dir=work_dir,
            staging=staging,
            env=env,
            args=args,
        )
        args._wheel = wheel  # used as a version fallback

        log("== private Node.js ==")
        node_binary = prepare_node(
            staging=staging,
            work_dir=work_dir,
            cache_dir=cache_dir,
            env=env,
            args=args,
        )

        log("== WebUI standalone ==")
        webui_source = prepare_webui(staging=staging, env=env, args=args)

        log("== project resources ==")
        prepare_project_resources(staging=staging, args=args)

        log("== bundled skills ==")
        prepare_bundled_skills(staging=staging)

        log("== licenses ==")
        prepare_licenses(staging=staging, node_root=staging / "node")

        log("== runtime manifest ==")
        versions = detect_versions(
            interpreter=interpreter,
            node_binary=node_binary,
            webui_source=webui_source,
            staging=staging,
            env=env,
            args=args,
        )
        manifest = write_manifest(staging, args=args, versions=versions)

        log("== publishing runtime ==")
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(out_dir))
        moved = True

        log("== precompile private Python with relocatable filenames ==")
        precompile_runtime(out_dir, platform=args.platform, env=env)

        log("== self-check from the final path ==")
        sys.path.insert(0, str(SCRIPT_DIR))
        from runtime_contract import check_runtime  # local sibling module

        report = check_runtime(out_dir, require_host_match=True, probe_binaries=True)
        for check in report.checks:
            if check["status"] == "fail":
                print(f"    [fail] {check['name']}: {check['detail']}", flush=True)
        if not report.ok:
            raise BuildError(
                "runtime self-check failed:\n  - " + "\n  - ".join(report.problems)
            )

        size_mib = dir_size_mib(out_dir)
        log(f"runtime ready: {out_dir} ({size_mib:.1f} MiB)")
        log(
            f"  python {manifest['pythonVersion']} / node {manifest['nodeVersion']} / "
            f"jw {manifest['jwVersion']} / webui {manifest['webuiVersion']}"
        )
        log(
            "  layout: python/ node/ webui/dist/ jw/subagents/ "
            "project/{research,src}/ licenses/ "
            f"{MANIFEST_FILENAME}"
        )
        return 0

    except BuildError as exc:
        print(f"[build_runtime] ERROR: {exc}", flush=True)
        print(f"::error title=build_runtime::{exc}", flush=True)
        _remove(staging)
        if moved:
            _remove(out_dir)
        return 1
    finally:
        if not args.keep_work:
            _remove(work_dir)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
