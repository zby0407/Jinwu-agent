"""Atomic run persistence and resumable workflow state."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from research_layout import (
    PROJECT_ROOT as PROJECT_ROOT,
)
from research_layout import (
    contract_inputs_root,
    contract_runs_root,
)

from .contracts import SESSION_VERSION, canonical_sha256

RUNS_ROOT = contract_runs_root("experiment")
INPUTS_ROOT = contract_inputs_root("experiment")
_TASK_WORKSPACE_ROOT: ContextVar[Path | None] = ContextVar(
    "automatic_experiment_task_workspace_root", default=None
)
_FILE_LOCKS: dict[str, threading.RLock] = {}
_FILE_LOCKS_GUARD = threading.Lock()
_FILE_LOCK_DEPTH = threading.local()
SAFE_RUN_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}-\d{8}T\d{6}Z-[0-9a-f]{8}$")
CHECKPOINTS = {
    "request_bound",
    "inputs_snapshotted",
    "design_validated",
    "stage_transitioned",
    "attempt_prepared",
    "execution_started",
    "execution_finished",
    "verification_finished",
    "report_finalized",
}


class StateError(RuntimeError):
    """Invalid or inconsistent persisted run state."""


def _thread_file_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _FILE_LOCKS_GUARD:
        return _FILE_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    """Serialize one task-local transition across threads and processes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path.resolve())
    depths = getattr(_FILE_LOCK_DEPTH, "depths", None)
    if depths is None:
        depths = {}
        _FILE_LOCK_DEPTH.depths = depths
    if depths.get(key, 0) > 0:
        depths[key] += 1
        try:
            yield
        finally:
            depths[key] -= 1
        return
    with _thread_file_lock(path):
        with path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            depths[key] = 1
            try:
                yield
            finally:
                depths.pop(key, None)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def task_workspace(root: str | Path) -> Iterator[None]:
    """Scope automatic-experiment persistence to one parent task workspace."""

    resolved = Path(root).expanduser().resolve()
    token = _TASK_WORKSPACE_ROOT.set(resolved)
    try:
        yield
    finally:
        _TASK_WORKSPACE_ROOT.reset(token)


def current_task_workspace() -> Path | None:
    return _TASK_WORKSPACE_ROOT.get()


def runs_root() -> Path:
    workspace = current_task_workspace()
    return (workspace / "experiment" / "runs") if workspace else RUNS_ROOT


def inputs_root() -> Path:
    workspace = current_task_workspace()
    return (workspace / "inputs") if workspace else INPUTS_ROOT


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = content.encode("utf-8")
    with tempfile.NamedTemporaryFile(
        mode="wb",
        delete=False,
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    ) as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    try:
        for attempt in range(6):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.02 * (2**attempt))
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_write_json(path: Path, payload: object) -> None:
    content = (
        json.dumps(
            payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
        )
        + "\n"
    )
    atomic_write_text(path, content)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"cannot read JSON state: {path}") from exc
    if not isinstance(payload, dict):
        raise StateError(f"state must be a JSON object: {path}")
    return payload


def safe_run_id(value: str) -> str:
    if SAFE_RUN_ID.fullmatch(value) is None:
        raise StateError("run_id has an invalid format")
    return value


def run_path(run_id: str) -> Path:
    root = runs_root()
    candidate = root / safe_run_id(run_id)
    resolved_root = root.resolve()
    resolved_parent = candidate.parent.resolve()
    if resolved_parent != resolved_root:
        raise StateError("run path escaped the run root")
    return candidate


def _slug(task_name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", task_name).strip("_")
    return (normalized or "experiment")[:32]


def create_run(
    request: dict[str, Any],
    *,
    request_fingerprint: str | None = None,
    lineage: dict[str, Any] | None = None,
) -> tuple[str, Path, dict[str, Any]]:
    root_store = runs_root()
    root_store.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = canonical_sha256(request)[:8]
    base = f"{_slug(request['task_name'])}-{timestamp}-{digest}"
    run_id = base
    suffix = 1
    while (root_store / run_id).exists():
        run_id = f"{base[:-8]}{suffix:02d}{digest[2:]}"
        suffix += 1
    root = root_store / run_id
    root.mkdir(parents=False, exist_ok=False)
    for name in ("inputs", "attempts", "public", "stage_artifacts", "stages"):
        (root / name).mkdir()
    atomic_write_json(root / "request.json", request)
    state = {
        "schema_version": SESSION_VERSION,
        "run_id": run_id,
        "phase": "request_bound",
        "request_sha256": canonical_sha256(request),
        "request_fingerprint": request_fingerprint,
        "input_fingerprint": None,
        "lineage": lineage
        or {
            "mode": "fresh",
            "source_run_id": None,
            "matching_run_ids": [],
        },
        "request_path": "request.json",
        "response_path": None,
        "design_path": None,
        "input_manifest_path": None,
        "current_attempt": None,
        "current_stage_id": None,
        "attempt_count": 0,
        "remaining_attempts": request["run_budget"]["max_total_attempts"],
        "stage_attempt_counts": {},
        "stage_history": [],
        "artifact_lineage": [],
        "budget_usage": {
            "total_wall_seconds": 0.0,
            "attempts_used": 0,
            "stages_started": 0,
            "stages_completed": 0,
        },
        "run_deadline_seconds": request["run_budget"]["total_wall_seconds"],
        "last_error": None,
        "outcome": None,
        "verified_record_sha256": None,
        "report_sha256": None,
        "audit_sha256": None,
        "report_assets": [],
        "cancel_requested": False,
        "checkpoints": [
            {
                "name": "request_bound",
                "created_at": utc_now(),
                "details": {"request_sha256": canonical_sha256(request)},
            }
        ],
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    atomic_write_json(root / "state.json", state)
    return run_id, root, state


def load_state(run_id: str) -> tuple[Path, dict[str, Any]]:
    root = run_path(run_id)
    if not root.is_dir():
        raise StateError(f"run not found: {run_id}")
    state = read_json(root / "state.json")
    if state.get("schema_version") != SESSION_VERSION or state.get("run_id") != run_id:
        raise StateError("run state identity mismatch")
    return root, state


def save_state(root: Path, state: dict[str, Any]) -> dict[str, Any]:
    state["updated_at"] = utc_now()
    atomic_write_json(root / "state.json", state)
    return state


def checkpoint(
    root: Path,
    state: dict[str, Any],
    name: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if name not in CHECKPOINTS:
        raise StateError(f"unknown checkpoint: {name}")
    state["phase"] = name
    state["checkpoints"].append(
        {"name": name, "created_at": utc_now(), "details": details or {}}
    )
    return save_state(root, state)


def latest_run_id() -> str | None:
    root = runs_root()
    if not root.is_dir():
        return None
    states = sorted(
        (path for path in root.glob("*/state.json") if path.is_file()),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not states:
        return None
    return states[0].parent.name
