"""Path allowlisting, immutable input snapshots, and output inventory."""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sysconfig
from pathlib import Path, PurePosixPath
from typing import Any

from research_layout import contract_inputs_root, contract_runs_root

from .contracts import canonical_sha256
from .state import (
    PROJECT_ROOT,
    atomic_write_json,
    current_task_workspace,
    file_sha256,
    inputs_root,
    runs_root,
    utc_now,
)

INPUTS_ROOT = contract_inputs_root("experiment")
RUNS_ROOT = contract_runs_root("experiment")
PROTECTED_SEGMENTS = {
    ".env",
    ".git",
    ".pi",
    "credentials",
    "credential",
    "secrets",
    "secret",
    "tests",
    "evals",
    "proofs",
    "oracle",
    "private",
    "hidden",
}
SECRET_NAME = re.compile(
    r"(?:api[_-]?key|access[_-]?token|secret|credential|password|private[_-]?key)",
    re.IGNORECASE,
)
SECRET_CONTENT = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{12,}|AKIA[0-9A-Z]{16}|"
    r"(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[^\s\"']{8,}|"
    r"authorization:\s*bearer\s+\S+)",
    re.IGNORECASE,
)
TABULAR_PROFILE_MAX_BYTES = 5 * 1024 * 1024
TABULAR_PROFILE_MAX_ROWS = 100_000
TEXT_PREVIEW_SUFFIXES = {
    ".csv",
    ".html",
    ".json",
    ".md",
    ".tsv",
    ".txt",
    ".yaml",
    ".yml",
}
TEXT_PREVIEW_MAX_BYTES = 64 * 1024
TABULAR_PREVIEW_MAX_BYTES = 16 * 1024
TEXT_PREVIEW_TOTAL_BYTES = 256 * 1024
TEXT_PREVIEW_MAX_FILES = 20
SCIENTIFIC_FORMATS = {
    ".fits": "fits",
    ".fit": "fits",
    ".fts": "fits",
    ".nc": "netcdf",
    ".nc4": "netcdf",
    ".cdf": "netcdf",
    ".h5": "hdf5",
    ".hdf5": "hdf5",
    ".hdf": "hdf5",
    ".parquet": "parquet",
}


class PathPolicyError(ValueError):
    """A requested path violates the local read/write policy."""


class InputMissingError(PathPolicyError):
    """An otherwise valid allowlisted input reference does not exist."""


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _parts_are_safe(relative: Path) -> bool:
    return all(part.lower() not in PROTECTED_SEGMENTS for part in relative.parts)


def _reject_special(path: Path) -> None:
    if path.is_symlink():
        raise PathPolicyError(f"symbolic links are not accepted: {path.name}")
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        raise PathPolicyError(f"junctions are not accepted: {path.name}")
    stat = path.stat(follow_symlinks=False)
    if path.is_file() and getattr(stat, "st_nlink", 1) > 1:
        raise PathPolicyError(f"multiply-linked files are not accepted: {path.name}")
    if SECRET_NAME.search(path.name):
        raise PathPolicyError(f"secret-like filename is not accepted: {path.name}")
    if path.name.startswith("."):
        raise PathPolicyError(f"hidden path is not accepted: {path.name}")


def _reject_secret_content(path: Path) -> None:
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        return
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PathPolicyError(f"input cannot be read safely: {path.name}") from exc
    if b"\x00" in raw[:8192]:
        return
    text = raw.decode("utf-8", errors="ignore")
    if SECRET_CONTENT.search(text):
        raise PathPolicyError(f"secret-like content is not accepted: {path.name}")


def _safe_relative_path(value: str) -> Path:
    if not value or "\x00" in value or "\\" in value:
        raise PathPolicyError("input paths must be non-empty relative POSIX paths")
    posix = PurePosixPath(value)
    if posix.is_absolute() or any(part in {"", ".", ".."} for part in posix.parts):
        raise PathPolicyError("input path must not be absolute or contain traversal")
    if ":" in posix.parts[0]:
        raise PathPolicyError("drive and device paths are not accepted")
    return Path(*posix.parts)


def resolve_input_reference(value: str) -> Path:
    relative = _safe_relative_path(value)
    workspace = current_task_workspace()
    if relative.parts[0] == "inputs":
        unresolved = inputs_root().parent / relative
    elif relative.parts[0] == "runs":
        unresolved = runs_root().parent / relative
    else:
        unresolved = (workspace or PROJECT_ROOT) / relative
    if not unresolved.exists():
        raise InputMissingError(f"input not found: {value}")
    # Reject the user-selected filesystem object before resolving it. Resolving
    # first would erase the fact that the final path is a symbolic link.
    _reject_special(unresolved)
    resolved = unresolved.resolve(strict=True)
    allowed_inputs_root = inputs_root().resolve()
    allowed_runs_root = runs_root().resolve()
    allowed = _is_within(allowed_inputs_root, resolved)
    if _is_within(allowed_runs_root, resolved):
        inside_runs = resolved.relative_to(allowed_runs_root)
        allowed = len(inside_runs.parts) >= 3 and inside_runs.parts[1] == "public"
    if not allowed:
        raise PathPolicyError(
            "inputs must be under inputs/ or a completed run public/ directory"
        )
    relative_to_allowed = (
        resolved.relative_to(allowed_inputs_root)
        if _is_within(allowed_inputs_root, resolved)
        else resolved.relative_to(allowed_runs_root)
    )
    if not _parts_are_safe(relative_to_allowed):
        raise PathPolicyError("input path contains a protected segment")
    _reject_special(resolved)
    return resolved


def _walk_source(source: Path) -> list[Path]:
    if source.is_file():
        return [source]
    files: list[Path] = []
    for root, dirnames, filenames in os.walk(source, followlinks=False):
        root_path = Path(root)
        safe_dirs: list[str] = []
        for name in sorted(dirnames):
            candidate = root_path / name
            _reject_special(candidate)
            if name.lower() in PROTECTED_SEGMENTS:
                raise PathPolicyError(f"protected directory is not accepted: {name}")
            safe_dirs.append(name)
        dirnames[:] = safe_dirs
        for name in sorted(filenames):
            candidate = root_path / name
            _reject_special(candidate)
            files.append(candidate)
            if len(files) > 1000:
                raise PathPolicyError(
                    "one input reference cannot contain more than 1000 files"
                )
    return files


def fingerprint_input_references(request: dict[str, Any]) -> dict[str, Any]:
    """Read-only identity for the current allowlisted inputs of one request."""

    rows: list[dict[str, Any]] = []
    for reference in request["input_refs"]:
        try:
            source = resolve_input_reference(reference["path"])
            files = _walk_source(source)
            base = source if source.is_dir() else source.parent
            file_rows: list[dict[str, Any]] = []
            for source_file in files:
                _reject_secret_content(source_file)
                file_rows.append(
                    {
                        "path": source_file.relative_to(base).as_posix(),
                        "size_bytes": source_file.stat().st_size,
                        "sha256": file_sha256(source_file),
                    }
                )
        except InputMissingError:
            rows.append(
                {
                    "id": reference["id"],
                    "source_path": reference["path"],
                    "status": "required_missing"
                    if reference["required"]
                    else "optional_missing",
                    "files": [],
                }
            )
            continue
        except PathPolicyError:
            rows.append(
                {
                    "id": reference["id"],
                    "source_path": reference["path"],
                    "status": "policy_blocked",
                    "files": [],
                }
            )
            continue
        rows.append(
            {
                "id": reference["id"],
                "source_path": reference["path"],
                "status": "snapshotted",
                "files": file_rows,
            }
        )
    identity = {"inputs": rows}
    return {
        "input_fingerprint": canonical_sha256(identity),
        "identity": identity,
    }


def fingerprint_input_snapshot(
    request: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Normalize a persisted snapshot to the same identity used by read-only lookup."""

    request_by_id = {row["id"]: row for row in request["input_refs"]}
    snapshot_by_id = {
        row["id"]: row
        for row in manifest.get("inputs", [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    rows: list[dict[str, Any]] = []
    missing_required = set(manifest.get("missing_required_ids", []))
    for reference in request["input_refs"]:
        snapshot_row = snapshot_by_id.get(reference["id"])
        if snapshot_row is None:
            status = (
                "required_missing"
                if reference["id"] in missing_required or reference["required"]
                else "optional_missing"
            )
            rows.append(
                {
                    "id": reference["id"],
                    "source_path": reference["path"],
                    "status": status,
                    "files": [],
                }
            )
            continue
        file_rows: list[dict[str, Any]] = []
        prefix = f"{reference['id']}/"
        for file_row in snapshot_row.get("files", []):
            stored_path = str(file_row["path"])
            relative = (
                stored_path[len(prefix) :]
                if stored_path.startswith(prefix)
                else stored_path
            )
            file_rows.append(
                {
                    "path": relative,
                    "size_bytes": file_row["size_bytes"],
                    "sha256": file_row["sha256"],
                }
            )
        file_rows.sort(key=lambda row: row["path"])
        rows.append(
            {
                "id": reference["id"],
                "source_path": reference["path"],
                "status": snapshot_row.get("status", "snapshotted"),
                "files": file_rows,
            }
        )
    unknown_ids = sorted(set(snapshot_by_id) - set(request_by_id))
    if unknown_ids:
        raise PathPolicyError(
            f"input snapshot contains ids absent from the request: {unknown_ids}"
        )
    identity = {"inputs": rows}
    return {
        "input_fingerprint": canonical_sha256(identity),
        "identity": identity,
    }


def _tabular_profile(path: Path) -> dict[str, Any] | None:
    suffix = path.suffix.lower()
    if suffix not in {".csv", ".tsv"}:
        return None
    delimiter = "\t" if suffix == ".tsv" else ","
    if path.stat().st_size > TABULAR_PROFILE_MAX_BYTES:
        return {
            "kind": "tabular",
            "format": suffix.lstrip("."),
            "profile_complete": False,
            "reason": "file exceeds the bounded structural-profile size limit",
        }
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            columns = [str(value) for value in (reader.fieldnames or [])]
            missing = {column: 0 for column in columns}
            rows_scanned = 0
            profile_complete = True
            for row in reader:
                if rows_scanned >= TABULAR_PROFILE_MAX_ROWS:
                    profile_complete = False
                    break
                rows_scanned += 1
                for column in columns:
                    value = row.get(column)
                    if value is None or not str(value).strip():
                        missing[column] += 1
    except (UnicodeError, csv.Error, OSError) as exc:
        return {
            "kind": "tabular",
            "format": suffix.lstrip("."),
            "profile_complete": False,
            "reason": f"structural profile unavailable: {type(exc).__name__}",
        }
    return {
        "kind": "tabular",
        "format": suffix.lstrip("."),
        "profile_complete": profile_complete,
        "columns": columns,
        "row_count": rows_scanned if profile_complete else None,
        "rows_scanned": rows_scanned,
        "missing_value_counts": missing,
    }


def _windows_to_wsl(path: Path) -> str:
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    if len(drive) != 1 or not drive.isalpha():
        raise PathPolicyError(
            "scientific metadata inspection requires a local drive path"
        )
    return f"/mnt/{drive}{resolved.as_posix().split(':', 1)[1]}"


def _scientific_profile(path: Path) -> dict[str, Any] | None:
    format_name = SCIENTIFIC_FORMATS.get(path.suffix.lower())
    if format_name is None:
        if path.suffix.lower() in {".gz", ".bz2", ".xz", ".zip", ".7z"}:
            raise PathPolicyError("compressed wrapper inputs are not accepted")
        return None
    if path.stat().st_size > 512 * 1024 * 1024:
        raise PathPolicyError(
            "scientific input exceeds the metadata-inspection size limit"
        )
    inspector = PROJECT_ROOT / "src" / "automatic_experiment" / "metadata_inspector.py"
    if os.name == "nt":
        try:
            site_probe = subprocess.run(
                [
                    "wsl.exe",
                    "-d",
                    "Ubuntu-E",
                    "--",
                    "python3",
                    "-I",
                    "-c",
                    "import site;print(site.getusersitepackages())",
                ],
                cwd=PROJECT_ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
        except FileNotFoundError:
            return {
                "kind": "scientific_container",
                "format": format_name,
                "profile_complete": False,
                "reason": "locked scientific metadata runtime is unavailable",
            }
        site_packages = site_probe.stdout.strip()
        if site_probe.returncode != 0 or not site_packages:
            raise PathPolicyError("locked scientific metadata runtime is unavailable")
        command_prefix = ["wsl.exe", "-d", "Ubuntu-E", "--"]
        inspector_path = _windows_to_wsl(inspector)
        input_path = _windows_to_wsl(path)
    else:
        if shutil.which("bwrap") is None:
            return {
                "kind": "scientific_container",
                "format": format_name,
                "profile_complete": False,
                "reason": "locked scientific metadata runtime is unavailable",
            }
        site_packages = sysconfig.get_paths()["purelib"]
        command_prefix = []
        inspector_path = str(inspector.resolve())
        input_path = str(path.resolve())
    command = [
        *command_prefix,
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
        "--setenv",
        "PYTHONDONTWRITEBYTECODE",
        "1",
        "--ro-bind",
        "/usr",
        "/usr",
        "--setenv",
        "OMP_NUM_THREADS",
        "1",
        "--setenv",
        "OPENBLAS_NUM_THREADS",
        "1",
        "--setenv",
        "MKL_NUM_THREADS",
        "1",
        "--setenv",
        "NUMEXPR_NUM_THREADS",
        "1",
        "--ro-bind",
        "/lib",
        "/lib",
        "--ro-bind",
        "/lib64",
        "/lib64",
        "--ro-bind",
        "/etc/ld.so.cache",
        "/etc/ld.so.cache",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/runtime",
        "--dir",
        "/input",
        "--ro-bind",
        site_packages,
        "/runtime/site-packages",
        "--ro-bind",
        inspector_path,
        "/runtime/metadata_inspector.py",
        "--ro-bind",
        input_path,
        "/input/data",
        "/usr/bin/prlimit",
        "--as=1610612736",
        "--cpu=12",
        "--fsize=1048576",
        "--nproc=16",
        "--nofile=128",
        "--",
        "/usr/bin/timeout",
        "15",
        "/usr/bin/python3",
        "-I",
        "-B",
        "/runtime/metadata_inspector.py",
        "--path",
        "/input/data",
        "--format",
        format_name,
    ]
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    if completed.returncode != 0 or len(completed.stdout.encode("utf-8")) > 64 * 1024:
        diagnostic = completed.stderr.strip().splitlines()[-1:] or [""]
        raise PathPolicyError(
            "scientific metadata inspection did not finish safely: "
            + diagnostic[0][:300]
        )
    try:
        profile = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise PathPolicyError(
            "scientific metadata inspection returned invalid output"
        ) from exc
    if profile.get("status") in {"dangerous_reference", "limit_exceeded"}:
        raise PathPolicyError(str(profile.get("reason") or profile["status"]))
    if profile.get("status") != "ok":
        return {
            "kind": "scientific_container",
            "format": format_name,
            "profile_complete": False,
            "reason": str(profile.get("reason") or "metadata unavailable")[:1000],
        }
    profile.pop("status", None)
    return profile


def snapshot_inputs(
    run_root: Path,
    request: dict[str, Any],
) -> dict[str, Any]:
    destination_root = run_root / "inputs"
    if any(destination_root.iterdir()):
        raise PathPolicyError("input snapshot is immutable and already exists")
    max_total = request["resource_budget"]["disk_mb"] * 1024 * 1024
    total = 0
    rows: list[dict[str, Any]] = []
    missing_required: list[str] = []
    for reference in request["input_refs"]:
        try:
            source = resolve_input_reference(reference["path"])
        except InputMissingError:
            if reference["required"]:
                missing_required.append(reference["id"])
                continue
            rows.append(
                {
                    "id": reference["id"],
                    "source_path": reference["path"],
                    "status": "optional_missing",
                    "files": [],
                }
            )
            continue
        files = _walk_source(source)
        copied: list[dict[str, Any]] = []
        base = source if source.is_dir() else source.parent
        for source_file in files:
            _reject_secret_content(source_file)
            size = source_file.stat().st_size
            total += size
            if total > max_total:
                raise PathPolicyError("input snapshot exceeds the run disk budget")
            relative = source_file.relative_to(base)
            target = destination_root / reference["id"] / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise PathPolicyError("input snapshot would overwrite an existing file")
            shutil.copyfile(source_file, target, follow_symlinks=False)
            copied_hash = file_sha256(target)
            if copied_hash != file_sha256(source_file):
                raise PathPolicyError("input changed while being snapshotted")
            copied.append(
                {
                    "path": target.relative_to(destination_root).as_posix(),
                    "size_bytes": size,
                    "sha256": copied_hash,
                    "profile": _tabular_profile(target) or _scientific_profile(target),
                }
            )
        rows.append(
            {
                "id": reference["id"],
                "source_path": reference["path"],
                "status": "snapshotted",
                "files": copied,
            }
        )
    manifest = {
        "schema_version": "automatic-experiment-input-snapshot-v1",
        "created_at": utc_now(),
        "total_bytes": total,
        "missing_required_ids": missing_required,
        "inputs": rows,
    }
    atomic_write_json(run_root / "input_snapshot.json", manifest)
    return manifest


def snapshot_input_previews(
    run_root: Path,
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    previews: list[dict[str, Any]] = []
    used_bytes = 0
    snapshot_root = run_root / "inputs"
    for input_row in manifest.get("inputs", []):
        for file_row in input_row.get("files", []):
            if len(previews) >= TEXT_PREVIEW_MAX_FILES:
                return previews
            relative = file_row["path"]
            path = snapshot_root / Path(*relative.split("/"))
            if path.suffix.lower() not in TEXT_PREVIEW_SUFFIXES:
                continue
            remaining = TEXT_PREVIEW_TOTAL_BYTES - used_bytes
            if remaining <= 0:
                return previews
            maximum = min(TEXT_PREVIEW_MAX_BYTES, remaining)
            if path.suffix.lower() in {".csv", ".tsv"}:
                maximum = min(maximum, TABULAR_PREVIEW_MAX_BYTES)
            raw = path.read_bytes()
            truncated = len(raw) > maximum
            if truncated:
                marker = b"\n...[preview middle omitted]...\n"
                head_size = (maximum - len(marker)) // 2
                tail_size = maximum - len(marker) - head_size
                selected = raw[:head_size] + marker + raw[-tail_size:]
            else:
                selected = raw
            content = selected.decode("utf-8-sig", errors="replace")
            used_bytes += len(selected)
            previews.append(
                {
                    "input_id": input_row["id"],
                    "path": relative,
                    "content": content,
                    "truncated": truncated,
                    "size_bytes": file_row["size_bytes"],
                    "sha256": file_row["sha256"],
                }
            )
    return previews


def safe_output_path(output_root: Path, relative_value: str) -> Path:
    relative = _safe_relative_path(relative_value)
    candidate = output_root / relative
    resolved_parent = candidate.parent.resolve(strict=True)
    if not _is_within(output_root.resolve(), resolved_parent):
        raise PathPolicyError("output path escaped the attempt output root")
    return candidate


def output_inventory(
    output_root: Path, disk_mb: int, single_file_mb: int
) -> list[dict[str, Any]]:
    root = output_root.resolve(strict=True)
    total = 0
    rows: list[dict[str, Any]] = []
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in dirnames:
            _reject_special(current_path / name)
        for name in filenames:
            path = current_path / name
            _reject_special(path)
            resolved = path.resolve(strict=True)
            if not _is_within(root, resolved):
                raise PathPolicyError("output escaped the attempt root")
            size = path.stat().st_size
            if size > single_file_mb * 1024 * 1024:
                raise PathPolicyError(
                    f"output file exceeds single-file budget: {path.name}"
                )
            total += size
            if total > disk_mb * 1024 * 1024:
                raise PathPolicyError("outputs exceed total disk budget")
            rows.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size_bytes": size,
                    "sha256": file_sha256(path),
                }
            )
    rows.sort(key=lambda row: row["path"])
    return rows
