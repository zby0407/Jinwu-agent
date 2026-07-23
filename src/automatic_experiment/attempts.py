"""Immutable attempt construction."""

from __future__ import annotations

import difflib
import sys
from pathlib import Path
from typing import Any

from .contracts import canonical_sha256, experiment_stage, stage_execution
from .policy import validate_code_files, verify_dependencies
from .state import atomic_write_json, atomic_write_text, file_sha256, utc_now


class AttemptError(RuntimeError):
    """An attempt cannot be safely created or reused."""


def _attempt_id(number: int) -> str:
    if not 1 <= number <= 999:
        raise AttemptError("attempt number is out of range")
    return f"attempt-{number:03d}"


def prepare_attempt(
    run_root: Path,
    state: dict[str, Any],
    request: dict[str, Any],
    design: dict[str, Any],
    files: object,
    *,
    stage_id: str,
    parent_attempt: str | None,
    change_reason: str,
) -> tuple[str, dict[str, Any]]:
    if state["remaining_attempts"] <= 0:
        raise AttemptError("no attempt budget remains")
    if parent_attempt is not None and parent_attempt != state["current_attempt"]:
        raise AttemptError("parent_attempt must match the latest immutable attempt")
    stage = experiment_stage(design, stage_id)
    execution = stage_execution(design, stage_id)
    verify_dependencies(execution["dependencies"])
    required_measurements = set(stage["measurement_refs"])
    stage_result_ids = set(stage["result_refs"])
    required_result_contracts = {
        row["id"]: {
            field: row[field]
            for field in ("display_name", "value_kind", "unit", "role")
        }
        for row in design["result_plan"]
        if row["id"] in stage_result_ids
    }
    required_endpoints = {
        ref
        for criterion in design["criteria"]
        if criterion["id"] in set(stage["criterion_refs"])
        for ref in criterion["endpoint_refs"]
    }
    generated = validate_code_files(
        files,
        execution["dependencies"],
        required_measurements=required_measurements,
        required_results=stage_result_ids,
        required_result_contracts=required_result_contracts,
        required_endpoints=required_endpoints,
        expected_artifacts=set(execution["expected_artifacts"]),
        required_consumed_artifacts=set(stage["consumes_artifact_ids"]),
        primary_estimand=design["interpretation_policy"]["primary_estimand"],
    )
    if parent_attempt is not None:
        parent_code_root = run_root / "attempts" / parent_attempt / "code"
        generated_by_path = {row["path"]: row["content"] for row in generated}
        parent_by_path = {
            path.relative_to(parent_code_root).as_posix(): path.read_text(encoding="utf-8")
            for path in parent_code_root.rglob("*")
            if path.is_file() and path.name != "worker_request.json"
        }
        if generated_by_path == parent_by_path:
            raise AttemptError(
                "repair attempt is unchanged from its parent; diagnose and change the code "
                "before consuming another attempt"
            )
    number = state["attempt_count"] + 1
    attempt_id = _attempt_id(number)
    root = run_root / "attempts" / attempt_id
    if root.exists():
        raise AttemptError("attempt directory already exists")
    code_root = root / "code"
    output_root = root / "output"
    code_root.mkdir(parents=True)
    output_root.mkdir()
    file_rows: list[dict[str, Any]] = []
    code_changes: list[dict[str, Any]] = []
    generated_paths: set[str] = set()
    parent_code_root = (
        run_root / "attempts" / parent_attempt / "code"
        if parent_attempt is not None
        else None
    )
    for item in generated:
        path = code_root / Path(*item["path"].split("/"))
        generated_paths.add(item["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise AttemptError("generated code would overwrite an existing file")
        atomic_write_text(path, item["content"])
        parent_path = (
            parent_code_root / Path(*item["path"].split("/"))
            if parent_code_root is not None
            else None
        )
        parent_text = (
            parent_path.read_text(encoding="utf-8")
            if parent_path is not None and parent_path.is_file()
            else None
        )
        change_kind = (
            "added"
            if parent_text is None
            else "unchanged"
            if parent_text == item["content"]
            else "modified"
        )
        diff_text = ""
        if change_kind != "unchanged":
            diff_text = "".join(
                difflib.unified_diff(
                    (parent_text or "").splitlines(keepends=True),
                    item["content"].splitlines(keepends=True),
                    fromfile=f"{parent_attempt or 'empty'}/{item['path']}",
                    tofile=f"{attempt_id}/{item['path']}",
                )
            )
            if len(diff_text.encode("utf-8")) > 64 * 1024:
                diff_text = diff_text.encode("utf-8")[: 64 * 1024].decode(
                    "utf-8", errors="ignore"
                ) + "\n... diff truncated ...\n"
        code_changes.append(
            {
                "path": item["path"],
                "change_kind": change_kind,
                "parent_sha256": (
                    file_sha256(parent_path)
                    if parent_path is not None and parent_path.is_file()
                    else None
                ),
                "current_sha256": file_sha256(path),
                "unified_diff": diff_text,
            }
        )
        file_rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
        )
    if parent_code_root is not None and parent_code_root.is_dir():
        for parent_path in sorted(parent_code_root.rglob("*")):
            if not parent_path.is_file() or parent_path.name == "worker_request.json":
                continue
            relative = parent_path.relative_to(parent_code_root).as_posix()
            if relative in generated_paths:
                continue
            code_changes.append(
                {
                    "path": relative,
                    "change_kind": "removed",
                    "parent_sha256": file_sha256(parent_path),
                    "current_sha256": None,
                    "unified_diff": "".join(
                        difflib.unified_diff(
                            parent_path.read_text(encoding="utf-8").splitlines(
                                keepends=True
                            ),
                            [],
                            fromfile=f"{parent_attempt}/{relative}",
                            tofile=f"{attempt_id}/{relative}",
                        )
                    ),
                }
            )
    input_manifest = {}
    manifest_path = run_root / "input_snapshot.json"
    if manifest_path.is_file():
        import json

        input_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if sys.platform == "darwin":
        # The macOS seatbelt backend does not remap paths; the worker uses the
        # same host-absolute roots the seatbelt profile keeps read-only or
        # writable (output only).
        sandbox_roots = {
            "input_root": str((run_root / "inputs").resolve()),
            "prior_artifact_root": str((run_root / "stage_artifacts").resolve()),
            "output_root": str(output_root.resolve()),
        }
    else:
        # The WSL2/bubblewrap backend bind-mounts the same roots at /workspace.
        sandbox_roots = {
            "input_root": "/workspace/input",
            "prior_artifact_root": "/workspace/prior",
            "output_root": "/workspace/output",
        }
    worker_request = {
        "schema_version": "automatic-experiment-worker-request-v1",
        "run_id": state["run_id"],
        "attempt_id": attempt_id,
        "stage_id": stage_id,
        "task": request["task"],
        "seed": execution["seed"],
        **sandbox_roots,
        "input_manifest": input_manifest,
        "prior_artifacts": [
            {
                "id": artifact_id,
                "path": next(
                    row["path"]
                    for row in design["artifact_plan"]
                    if row["id"] == artifact_id
                ),
            }
            for artifact_id in stage["consumes_artifact_ids"]
        ],
        "expected_artifacts": execution["expected_artifacts"],
    }
    atomic_write_json(code_root / "worker_request.json", worker_request)
    file_rows.append(
        {
            "path": "code/worker_request.json",
            "size_bytes": (code_root / "worker_request.json").stat().st_size,
            "sha256": file_sha256(code_root / "worker_request.json"),
        }
    )
    file_rows.sort(key=lambda row: row["path"])
    metadata = {
        "schema_version": "automatic-experiment-attempt-v1",
        "attempt_id": attempt_id,
        "stage_id": stage_id,
        "created_at": utc_now(),
        "parent_attempt": parent_attempt,
        "change_reason": change_reason.strip(),
        "request_sha256": canonical_sha256(request),
        "design_sha256": canonical_sha256(design),
        "code_bundle_sha256": canonical_sha256(
            [
                {
                    "path": row["path"].removeprefix("code/"),
                    "sha256": row["sha256"],
                    "size_bytes": row["size_bytes"],
                }
                for row in file_rows
                if row["path"].startswith("code/")
                and row["path"] != "code/worker_request.json"
            ]
        ),
        "files": file_rows,
        "code_changes": code_changes,
        "status": "prepared",
    }
    atomic_write_json(root / "attempt.json", metadata)
    return attempt_id, metadata


def verify_attempt_immutable(attempt_root: Path, metadata: dict[str, Any]) -> None:
    for row in metadata["files"]:
        path = attempt_root / Path(*row["path"].split("/"))
        if not path.is_file():
            raise AttemptError(f"attempt file is missing: {row['path']}")
        if path.stat().st_size != row["size_bytes"] or file_sha256(path) != row["sha256"]:
            raise AttemptError(f"attempt file changed after preparation: {row['path']}")
