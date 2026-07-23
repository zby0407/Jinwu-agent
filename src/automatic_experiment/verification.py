"""Deterministic execution verification and record construction."""

from __future__ import annotations

import csv
import json
import math
import re
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from .attempts import AttemptError, verify_attempt_immutable
from .contracts import (
    P_VALUE_PLAN,
    P_VALUE_REQUEST,
    RECORD_VERSION,
    canonical_sha256,
    experiment_stage,
    stage_execution,
    validate_scientific_assessment,
    validate_worker_result,
)
from .paths import PathPolicyError, output_inventory, safe_output_path
from .state import atomic_write_json, file_sha256, read_json, runs_root, utc_now

SECRET_CONTENT = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{12,}|AKIA[0-9A-Z]{16}|"
    r"(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[^\s\"']{8,}|"
    r"authorization:\s*bearer\s+\S+)",
    re.IGNORECASE,
)

TEXTUAL_INPUT_SUFFIXES = {".csv", ".tsv", ".json", ".md", ".txt"}
MAX_QUANTITATIVE_INPUT_FILE_BYTES = 256 * 1024
MAX_QUANTITATIVE_INPUT_TOTAL_BYTES = 1024 * 1024


class VerificationError(RuntimeError):
    """Execution facts or outputs cannot support a trustworthy record."""


class AssessmentRequired(VerificationError):
    """A successful deterministic precheck needs model scientific interpretation."""

    def __init__(
        self,
        worker_result: dict[str, Any],
        facts: dict[str, Any],
        verified_artifact_sources: dict[str, Path],
        design: dict[str, Any],
        paired_comparison_evidence: list[dict[str, Any]],
        stage_id: str,
    ) -> None:
        inventory = {
            row["path"]: row for row in facts.get("output_inventory", [])
        }
        verified_artifacts = []
        artifact_evidence = []
        for artifact in worker_result["artifacts"]:
            if artifact["path"] not in verified_artifact_sources:
                continue
            observed = inventory.get(artifact["path"], {})
            verified_artifacts.append(
                {
                    "path": artifact["path"],
                    "kind": artifact["kind"],
                    "description": artifact["description"],
                    "size_bytes": observed.get("size_bytes"),
                    "sha256": observed.get("sha256"),
                }
            )
            artifact_evidence.append(
                _bounded_artifact_evidence(
                    artifact,
                    verified_artifact_sources[artifact["path"]],
                )
            )
        self.preview = {
            "worker_result": worker_result,
            "verified_artifacts": verified_artifacts,
            "verified_artifact_evidence": artifact_evidence,
            "criterion_evidence": _criterion_evidence(design, worker_result, stage_id),
            "paired_comparison_evidence": paired_comparison_evidence,
            "execution_summary": {
                "started_at": facts["started_at"],
                "ended_at": facts["ended_at"],
                "wall_seconds": facts["wall_seconds"],
                "sandbox_exit_code": facts["sandbox_exit_code"],
                "windows_process_exit_code": facts["windows_process_exit_code"],
                "stop_reason": facts["stop_reason"],
            },
        }
        super().__init__("successful execution requires a scientific assessment")


def _sandbox_isolation_passed(policy: dict[str, Any]) -> bool:
    """Evaluate the effective sandbox boundary for the active platform."""

    common = all(
        policy.get(field) is expected
        for field, expected in (
            ("new_session", True),
            ("host_project_mounted", False),
            ("home_mounted", False),
            ("input_snapshot_read_only", True),
            ("attempt_code_read_only", True),
            ("attempt_output_only_writable_mount", True),
            ("locked_site_packages_read_only", True),
        )
    )
    backend = str(policy.get("backend", "")).casefold()
    if "seatbelt" in backend:
        return (
            common
            and policy.get("network_isolation") is True
            and policy.get("host_file_reads_restricted") is True
        )
    return common and all(
        policy.get(field) is True
        for field in ("user_namespace", "pid_namespace", "network_namespace")
    )


def _immutable_input_basis_texts(
    run_root: Path,
    design: dict[str, Any],
) -> list[str]:
    """Read a bounded, hash-verified numeric basis from selected text inputs."""

    manifest_path = run_root / "input_snapshot.json"
    if not manifest_path.is_file():
        return []
    manifest = read_json(manifest_path)
    selected_ids = set(design.get("input_ids", []))
    inputs_root = (run_root / "inputs").resolve()
    texts: list[str] = []
    consumed = 0
    for input_row in manifest.get("inputs", []):
        if input_row.get("id") not in selected_ids:
            continue
        for file_row in input_row.get("files", []):
            relative_path = file_row.get("path")
            if not isinstance(relative_path, str):
                continue
            source = (inputs_root / relative_path).resolve()
            try:
                source.relative_to(inputs_root)
            except ValueError:
                continue
            if not source.is_file() or source.suffix.casefold() not in TEXTUAL_INPUT_SUFFIXES:
                continue
            expected_size = file_row.get("size_bytes")
            observed_size = source.stat().st_size
            if (
                not isinstance(expected_size, int)
                or observed_size != expected_size
                or observed_size > MAX_QUANTITATIVE_INPUT_FILE_BYTES
                or consumed + observed_size > MAX_QUANTITATIVE_INPUT_TOTAL_BYTES
                or file_row.get("sha256") != file_sha256(source)
            ):
                continue
            try:
                texts.append(source.read_text(encoding="utf-8"))
            except UnicodeDecodeError:
                continue
            profile = file_row.get("profile")
            if isinstance(profile, dict):
                texts.append(json.dumps(profile, ensure_ascii=False, sort_keys=True))
            consumed += observed_size
    return texts


def _json_field_paths(value: Any, prefix: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            label = f"{prefix}.{key}" if prefix else str(key)
            paths.append(label)
            paths.extend(_json_field_paths(child, label))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            paths.extend(_json_field_paths(child, f"{prefix}[{index}]"))
    return paths


def _unrequested_inferential_output_errors(
    task_text: str,
    worker_result: dict[str, Any],
    artifact_sources: dict[str, Path],
) -> list[str]:
    """Reject p-value fields hidden in output artifacts when they were not requested."""

    if P_VALUE_REQUEST.search(task_text):
        return []
    errors: list[str] = []
    maximum_json_bytes = 8 * 1024 * 1024
    for artifact in worker_result.get("artifacts", []):
        path = str(artifact.get("path", ""))
        source = artifact_sources.get(path)
        if source is None or (artifact.get("kind") != "json" and source.suffix.lower() != ".json"):
            continue
        if P_VALUE_PLAN.search(path):
            errors.append(f"unrequested p-value output path: {path}")
            continue
        if source.stat().st_size > maximum_json_bytes:
            errors.append(
                f"JSON artifact is too large for bounded inferential-output audit: {path}"
            )
            continue
        try:
            parsed = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f"JSON artifact could not be audited: {path}: {exc}")
            continue
        leaked_fields = sorted(
            field for field in _json_field_paths(parsed) if P_VALUE_PLAN.search(field)
        )
        if leaked_fields:
            errors.append(
                f"unrequested p-value fields in {path}: {leaked_fields}"
            )
    return errors


def _active_stage_design(
    design: dict[str, Any],
    stage_id: str,
) -> dict[str, Any]:
    """Create a read-only scientific view containing only the active stage's outputs."""

    stage = experiment_stage(design, stage_id)
    criterion_ids = set(stage["criterion_refs"])
    measurement_ids = set(stage["measurement_refs"])
    result_ids = set(stage["result_refs"])
    active = deepcopy(design)
    active["criteria"] = [
        row for row in active["criteria"] if row["id"] in criterion_ids
    ]
    active["measurement_plan"] = [
        row for row in active["measurement_plan"] if row["name"] in measurement_ids
    ]
    active["result_plan"] = [
        row for row in active["result_plan"] if row["id"] in result_ids
    ]
    active["paired_comparison_audits"] = [
        row
        for row in active["paired_comparison_audits"]
        if {
            name
            for name in (
                row["baseline_measurement"],
                row["candidate_measurement"],
                row["delta_measurement"],
            )
            if name is not None
        }.issubset(measurement_ids)
    ]
    return active


def _attempt_history(
    run_root: Path,
    current_attempt_id: str,
    current_outcome: str,
    current_reason: str,
    current_facts: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    attempts_root = run_root / "attempts"
    if not attempts_root.is_dir():
        return rows
    for attempt_root in sorted(
        path for path in attempts_root.iterdir() if path.is_dir()
    ):
        metadata_path = attempt_root / "attempt.json"
        if not metadata_path.is_file():
            continue
        metadata = read_json(metadata_path)
        attempt_id = metadata["attempt_id"]
        prior_record_path = attempt_root / "record.json"
        prior_record = (
            read_json(prior_record_path)
            if prior_record_path.is_file() and attempt_id != current_attempt_id
            else None
        )
        execution_path = attempt_root / "execution.json"
        execution = (
            current_facts
            if attempt_id == current_attempt_id
            else read_json(execution_path)
            if execution_path.is_file()
            else None
        )
        rows.append(
            {
                "attempt_id": attempt_id,
                "stage_id": metadata.get("stage_id"),
                "parent_attempt": metadata.get("parent_attempt"),
                "created_at": metadata["created_at"],
                "change_reason": metadata["change_reason"],
                "design_sha256": metadata["design_sha256"],
                "files": metadata.get("files", []),
                "code_changes": metadata.get("code_changes", []),
                "execution_summary": (
                    {
                        "started_at": execution["started_at"],
                        "ended_at": execution["ended_at"],
                        "wall_seconds": execution["wall_seconds"],
                        "windows_process_exit_code": execution[
                            "windows_process_exit_code"
                        ],
                        "sandbox_exit_code": execution["sandbox_exit_code"],
                        "stop_reason": execution["stop_reason"],
                    }
                    if execution is not None
                    else None
                ),
                "verification_outcome": (
                    current_outcome
                    if attempt_id == current_attempt_id
                    else prior_record.get("outcome")
                    if prior_record is not None
                    else None
                ),
                "verification_reason": (
                    current_reason
                    if attempt_id == current_attempt_id
                    else prior_record.get("outcome_reason")
                    if prior_record is not None
                    else None
                ),
            }
        )
    return rows


def _read_text_bounded(path: Path, maximum: int = 2 * 1024 * 1024) -> str:
    if path.stat().st_size > maximum:
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _secret_scan(attempt_root: Path, inventory: list[dict[str, Any]]) -> None:
    for row in inventory:
        relative = row["path"]
        if relative.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".pdf", ".npy", ".npz")):
            continue
        path = attempt_root / "output" / Path(*relative.split("/"))
        if SECRET_CONTENT.search(_read_text_bounded(path)):
            raise VerificationError("output contains secret-like content and cannot be released")
    for name in ("stdout.txt", "stderr.txt"):
        path = attempt_root / name
        if path.is_file() and SECRET_CONTENT.search(_read_text_bounded(path)):
            raise VerificationError("execution log contains secret-like content and cannot be released")


def _bounded_artifact_evidence(
    artifact: dict[str, Any],
    path: Path,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "path": artifact["path"],
        "kind": artifact["kind"],
        "size_bytes": path.stat().st_size,
        "sha256": file_sha256(path),
    }
    if artifact["kind"] == "json" and path.stat().st_size <= 128 * 1024:
        try:
            row["parsed_json"] = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            row["parse_error"] = "JSON artifact could not be parsed for scientific review."
    elif artifact["kind"] in {"csv", "text", "markdown"}:
        text = _read_text_bounded(path, 32 * 1024)
        if text:
            row["text_excerpt"] = text
            row["truncated"] = path.stat().st_size > len(text.encode("utf-8"))
    return row


def _criterion_evidence(
    design: dict[str, Any],
    worker_result: dict[str, Any],
    stage_id: str | None = None,
) -> list[dict[str, Any]]:
    measurement_by_name = {
        row["name"]: row for row in worker_result["measurements"]
    }
    endpoint_by_id = {
        row["id"]: row for row in worker_result["endpoint_results"]
    }
    result_by_id = {row["id"]: row for row in worker_result.get("result_items", [])}
    rows: list[dict[str, Any]] = []
    stage_criterion_ids = (
        set(experiment_stage(design, stage_id)["criterion_refs"])
        if stage_id is not None
        else {row["id"] for row in design["criteria"]}
    )
    for criterion in design["criteria"]:
        if criterion["id"] not in stage_criterion_ids:
            continue
        rows.append(
            {
                "criterion_id": criterion["id"],
                "statement": criterion["statement"],
                "basis_kind": criterion["basis_kind"],
                "basis_text": criterion["basis_text"],
                "source_refs": criterion["source_refs"],
                "artifact_refs": criterion["artifact_refs"],
                "measurements": [
                    measurement_by_name[ref]
                    for ref in criterion["measurement_refs"]
                    if ref in measurement_by_name
                ],
                "typed_results": [
                    result_by_id[ref]
                    for ref in criterion.get("result_refs", [])
                    if ref in result_by_id
                ],
                "endpoints": [
                    endpoint_by_id[ref]
                    for ref in criterion["endpoint_refs"]
                    if ref in endpoint_by_id
                ],
            }
        )
    return rows


def _nested_values_for_key(value: Any, key: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, dict):
        if key in value:
            found.append(value[key])
        for child in value.values():
            found.extend(_nested_values_for_key(child, key))
    elif isinstance(value, list):
        for child in value:
            found.extend(_nested_values_for_key(child, key))
    return found


def _same_number(left: Any, right: float) -> bool:
    if isinstance(left, bool) or not isinstance(left, (int, float)):
        return False
    observed = float(left)
    return math.isfinite(observed) and math.isclose(
        observed,
        right,
        rel_tol=1e-9,
        abs_tol=1e-12,
    )


def _same_result_value(left: Any, right: Any) -> bool:
    if isinstance(right, bool):
        return isinstance(left, bool) and left is right
    if isinstance(right, (int, float)) and not isinstance(right, bool):
        return _same_number(left, float(right))
    return isinstance(left, str) and isinstance(right, str) and left == right


def _measurement_artifact_errors(
    worker_result: dict[str, Any],
    verified_artifact_sources: dict[str, Path],
) -> list[str]:
    artifacts = {row["path"]: row for row in worker_result["artifacts"]}
    parsed_json: dict[str, Any] = {}
    errors: list[str] = []
    unavailable: set[str] = set()
    missing_keys: dict[str, list[str]] = {}
    for measurement in [
        *worker_result["measurements"],
        *worker_result.get("result_items", []),
    ]:
        source_ref = measurement["source_artifact"]
        if source_ref is None:
            continue
        artifact = artifacts[source_ref]
        if artifact["kind"] != "json":
            continue
        source = verified_artifact_sources.get(source_ref)
        if source is None or source.stat().st_size > 2 * 1024 * 1024:
            if source_ref not in unavailable:
                errors.append(f"reported values cannot be checked against JSON artifact {source_ref}")
                unavailable.add(source_ref)
            continue
        if source_ref not in parsed_json:
            try:
                parsed_json[source_ref] = json.loads(
                    source.read_text(encoding="utf-8")
                )
            except (UnicodeDecodeError, json.JSONDecodeError):
                errors.append(f"{source_ref} is not valid UTF-8 JSON")
                unavailable.add(source_ref)
                continue
        if source_ref in unavailable:
            continue
        value_id = measurement.get("name", measurement.get("id"))
        candidates = _nested_values_for_key(
            parsed_json[source_ref],
            value_id,
        )
        if not any(
            _same_result_value(candidate, measurement["value"])
            for candidate in candidates
        ):
            missing_keys.setdefault(source_ref, []).append(str(value_id))
    for source_ref, value_ids in missing_keys.items():
        errors.append(
            f"reported values are not traceable to equal JSON fields in {source_ref}; "
            "use each exact measurement name or result id once as a JSON key "
            f"(nesting is allowed): {value_ids}"
        )
    return errors


def _comparison_consistency_errors(
    worker_result: dict[str, Any],
    design: dict[str, Any] | None = None,
) -> list[str]:
    """Reject internally inconsistent comparison-difference claims.

    A validated paired-comparison declaration is authoritative about the
    subtraction direction.  The ``*_improvement`` spelling alone must never
    silently impose one direction because some studies define improvement as
    candidate minus baseline (where a negative error delta is beneficial).
    """

    measurements = {
        row["name"]: row
        for row in worker_result["measurements"]
        if isinstance(row.get("value"), (int, float))
        and not isinstance(row.get("value"), bool)
    }
    declared_deltas = {
        row["delta_measurement"]: row
        for row in (design or {}).get("paired_comparison_audits", [])
        if isinstance(row, dict)
        and isinstance(row.get("delta_measurement"), str)
        and row.get("delta_formula")
        in {"baseline_minus_candidate", "candidate_minus_baseline"}
    }
    errors: list[str] = []
    for name, improvement in measurements.items():
        declared = declared_deltas.get(name)
        if declared is not None:
            baseline_name = declared["baseline_measurement"]
            candidate_name = declared["candidate_measurement"]
            if baseline_name not in measurements or candidate_name not in measurements:
                continue
            baseline = measurements[baseline_name]
            candidate = measurements[candidate_name]
            if (
                baseline.get("unit") != candidate.get("unit")
                or baseline.get("unit") != improvement.get("unit")
            ):
                continue
            if declared["delta_formula"] == "baseline_minus_candidate":
                expected = float(baseline["value"]) - float(candidate["value"])
                expression = f"{baseline_name} - {candidate_name}"
            else:
                expected = float(candidate["value"]) - float(baseline["value"])
                expression = f"{candidate_name} - {baseline_name}"
            observed = float(improvement["value"])
            tolerance = max(2e-6, abs(expected) * 1e-6)
            if not math.isclose(
                observed,
                expected,
                rel_tol=1e-6,
                abs_tol=tolerance,
            ):
                errors.append(
                    f"{name}={observed:.12g} does not equal "
                    f"{expression}={expected:.12g} under the declared delta formula"
                )
            continue

        marker = "_improvement"
        if marker not in name:
            continue
        base, suffix = name.split(marker, 1)
        candidate_pairs = [
            (f"raw_{base}{suffix}", f"calibrated_{base}{suffix}"),
            (f"{base}_raw{suffix}", f"{base}_calibrated{suffix}"),
        ]
        if "_" in base:
            scope, metric = base.rsplit("_", 1)
            candidate_pairs.extend(
                [
                    (
                        f"{scope}_raw_{metric}{suffix}",
                        f"{scope}_calibrated_{metric}{suffix}",
                    ),
                    (
                        f"raw_{scope}_{metric}{suffix}",
                        f"calibrated_{scope}_{metric}{suffix}",
                    ),
                ]
            )
        pair = next(
            (
                (raw_name, calibrated_name)
                for raw_name, calibrated_name in candidate_pairs
                if raw_name in measurements and calibrated_name in measurements
            ),
            None,
        )
        if pair is None:
            continue
        raw_name, calibrated_name = pair
        raw = measurements[raw_name]
        calibrated = measurements[calibrated_name]
        if raw.get("unit") != calibrated.get("unit") or raw.get("unit") != improvement.get(
            "unit"
        ):
            continue
        expected = float(raw["value"]) - float(calibrated["value"])
        observed = float(improvement["value"])
        tolerance = max(2e-6, abs(expected) * 1e-6)
        if not math.isclose(
            observed,
            expected,
            rel_tol=1e-6,
            abs_tol=tolerance,
        ):
            errors.append(
                f"{name}={observed:.12g} does not equal "
                f"{raw_name} - {calibrated_name}={expected:.12g}; "
                "comparison measurements may mix different populations or splits"
            )
    return errors


def _finite_csv_number(value: object, label: str) -> float:
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise VerificationError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise VerificationError(f"{label} must be finite")
    return result


def _read_csv_rows(path: Path, label: str) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = list(reader.fieldnames or [])
            return columns, [dict(row) for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        raise VerificationError(f"{label} is not a readable UTF-8 CSV") from exc


def _metric_value(metric: str, predictions: list[float], targets: list[float]) -> float:
    if not predictions or len(predictions) != len(targets):
        raise VerificationError("paired comparison requires one or more aligned rows")
    errors = [prediction - target for prediction, target in zip(predictions, targets, strict=True)]
    if metric == "mae":
        return sum(abs(value) for value in errors) / len(errors)
    if metric == "rmse":
        return math.sqrt(sum(value * value for value in errors) / len(errors))
    if metric == "mean_signed_error":
        return sum(errors) / len(errors)
    raise VerificationError(f"unsupported paired comparison metric: {metric}")


def _close_measurement(observed: float, expected: float) -> bool:
    return math.isclose(
        observed,
        expected,
        rel_tol=1e-7,
        # Worker summaries and both columns of row-level evidence are commonly
        # rounded to six decimal places.  Recomputing a difference from those
        # two rounded columns can accumulate at most one unit in the last
        # reported place; larger discrepancies still fail verification.
        abs_tol=max(1.0000001e-6, abs(expected) * 1e-7),
    )


def _paired_comparison_audit_errors(
    run_root: Path,
    design: dict[str, Any],
    worker_result: dict[str, Any],
    verified_artifact_sources: dict[str, Path],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Recompute declared paired comparisons from immutable inputs and row evidence."""

    measurements = {
        row["name"]: row
        for row in worker_result["measurements"]
        if isinstance(row.get("value"), (int, float))
        and not isinstance(row.get("value"), bool)
    }
    errors: list[str] = []
    trusted_rows: list[dict[str, Any]] = []
    for audit in design.get("paired_comparison_audits", []):
        audit_id = audit["id"]
        artifact_path = verified_artifact_sources.get(audit["evidence_artifact"])
        if artifact_path is None:
            errors.append(
                f"{audit_id}: paired comparison evidence artifact is unavailable: "
                f"{audit['evidence_artifact']}"
            )
            continue
        source_root = run_root / "inputs" / audit["source_input_id"]
        source_candidates: list[tuple[Path, list[str], list[dict[str, str]]]] = []
        if source_root.is_dir():
            for source_path in sorted(source_root.rglob("*.csv")):
                try:
                    columns, source_rows = _read_csv_rows(
                        source_path,
                        f"{audit_id} source input",
                    )
                except VerificationError:
                    continue
                required = {
                    audit["source_row_id_column"],
                    audit["source_target_column"],
                    audit["source_baseline_column"],
                }
                if required.issubset(columns):
                    source_candidates.append((source_path, columns, source_rows))
        if len(source_candidates) != 1:
            errors.append(
                f"{audit_id}: source_input_id must resolve to exactly one CSV containing "
                "the declared row id, target, and baseline columns"
            )
            continue
        source_path, _, source_rows = source_candidates[0]
        try:
            evidence_columns, evidence_rows = _read_csv_rows(
                artifact_path,
                f"{audit_id} evidence artifact",
            )
        except VerificationError as exc:
            errors.append(str(exc))
            continue
        required_evidence = {
            audit["evidence_row_id_column"],
            audit["evidence_target_column"],
            audit["evidence_baseline_column"],
            audit["evidence_candidate_column"],
        }
        missing_columns = sorted(required_evidence - set(evidence_columns))
        if missing_columns:
            errors.append(
                f"{audit_id}: evidence artifact is missing columns {missing_columns}"
            )
            continue
        # A shared evidence table may evaluate several comparisons in different row
        # subsets (e.g. phase-split backtests).  When the audit declares a
        # row_filter, restrict recomputation to the evidence rows whose filter
        # column value is in the declared list; the filter column must be present.
        row_filter = audit.get("row_filter")
        if row_filter is not None:
            filter_column = row_filter["column"]
            filter_values = {str(v) for v in row_filter["in"]}
            if filter_column not in evidence_columns:
                errors.append(
                    f"{audit_id}: row_filter column {filter_column!r} is absent from the evidence artifact"
                )
                continue
            evidence_rows = [
                row
                for row in evidence_rows
                if str(row.get(filter_column, "")).strip() in filter_values
            ]
        source_by_id: dict[str, dict[str, str]] = {}
        duplicate_source_ids: set[str] = set()
        for row in source_rows:
            row_id = str(row.get(audit["source_row_id_column"], "")).strip()
            if not row_id:
                errors.append(f"{audit_id}: source row id cannot be empty")
                continue
            if row_id in source_by_id:
                duplicate_source_ids.add(row_id)
            source_by_id[row_id] = row
        if duplicate_source_ids:
            errors.append(
                f"{audit_id}: source row ids are not unique: "
                f"{sorted(duplicate_source_ids)[:10]}"
            )
            continue
        targets: list[float] = []
        baselines: list[float] = []
        candidates: list[float] = []
        evidence_ids: set[str] = set()
        evidence_id_order: list[str] = []
        audit_failed = False
        for row_index, row in enumerate(evidence_rows, start=2):
            row_id = str(row.get(audit["evidence_row_id_column"], "")).strip()
            if not row_id or row_id in evidence_ids:
                errors.append(
                    f"{audit_id}: evidence row ids must be non-empty and unique "
                    f"(CSV row {row_index})"
                )
                audit_failed = True
                continue
            evidence_ids.add(row_id)
            evidence_id_order.append(row_id)
            source_row = source_by_id.get(row_id)
            if source_row is None:
                errors.append(
                    f"{audit_id}: evidence row {row_id!r} is absent from immutable input"
                )
                audit_failed = True
                continue
            # A shared evidence table may evaluate several comparisons in different
            # row subsets (e.g. phase-split backtests), leaving the candidate column
            # blank for rows that belong to another comparison.  Those rows are not
            # part of this audit and must be skipped before any numeric parsing.
            candidate_raw = str(row.get(audit["evidence_candidate_column"], "")).strip()
            if candidate_raw == "":
                continue
            try:
                source_target = _finite_csv_number(
                    source_row[audit["source_target_column"]],
                    f"{audit_id} source target at {row_id}",
                )
                source_baseline = _finite_csv_number(
                    source_row[audit["source_baseline_column"]],
                    f"{audit_id} source baseline at {row_id}",
                )
                evidence_target = _finite_csv_number(
                    row[audit["evidence_target_column"]],
                    f"{audit_id} evidence target at {row_id}",
                )
                evidence_baseline = _finite_csv_number(
                    row[audit["evidence_baseline_column"]],
                    f"{audit_id} evidence baseline at {row_id}",
                )
                evidence_candidate = _finite_csv_number(
                    row[audit["evidence_candidate_column"]],
                    f"{audit_id} evidence candidate at {row_id}",
                )
            except (KeyError, VerificationError) as exc:
                errors.append(str(exc))
                audit_failed = True
                continue
            if not _close_measurement(evidence_target, source_target):
                errors.append(
                    f"{audit_id}: target value for row {row_id!r} does not match "
                    "the immutable input"
                )
                audit_failed = True
            if (
                audit["comparison_kind"] == "source_baseline_vs_candidate"
                and not _close_measurement(evidence_baseline, source_baseline)
            ):
                errors.append(
                    f"{audit_id}: baseline value for row {row_id!r} does not match "
                    "the immutable input"
                )
                audit_failed = True
            targets.append(source_target)
            baselines.append(
                source_baseline
                if audit["comparison_kind"] == "source_baseline_vs_candidate"
                else evidence_baseline
            )
            candidates.append(evidence_candidate)
        if audit_failed or not targets:
            if not targets:
                errors.append(f"{audit_id}: paired comparison evidence contains no valid rows")
            continue
        baseline_value = _metric_value(audit["metric"], baselines, targets)
        candidate_value = _metric_value(audit["metric"], candidates, targets)
        expected_measurements = {
            audit["baseline_measurement"]: baseline_value,
            audit["candidate_measurement"]: candidate_value,
        }
        if audit["delta_measurement"] is not None:
            delta_value = (
                baseline_value - candidate_value
                if audit["delta_formula"] == "baseline_minus_candidate"
                else candidate_value - baseline_value
            )
            expected_measurements[audit["delta_measurement"]] = delta_value
        measurement_failures: list[str] = []
        for measurement_name, expected_value in expected_measurements.items():
            observed_row = measurements.get(measurement_name)
            if observed_row is None:
                measurement_failures.append(
                    f"{measurement_name} is absent from worker measurements"
                )
                continue
            observed_value = float(observed_row["value"])
            if not _close_measurement(observed_value, expected_value):
                measurement_failures.append(
                    f"{measurement_name}={observed_value:.12g}, trusted recomputation="
                    f"{expected_value:.12g}"
                )
        if measurement_failures:
            errors.append(
                f"{audit_id}: paired comparison target or metric is inconsistent: "
                + "; ".join(measurement_failures)
            )
            continue
        better_count = 0
        tied_count = 0
        worse_count = 0
        for baseline, candidate, target in zip(
            baselines,
            candidates,
            targets,
            strict=True,
        ):
            baseline_error = abs(baseline - target)
            candidate_error = abs(candidate - target)
            if math.isclose(
                baseline_error,
                candidate_error,
                rel_tol=1e-9,
                abs_tol=1e-12,
            ):
                tied_count += 1
            elif candidate_error < baseline_error:
                better_count += 1
            else:
                worse_count += 1
        trusted_rows.append(
            {
                "id": audit_id,
                "evaluation_scope": audit["evaluation_scope"],
                "source_input_id": audit["source_input_id"],
                "source_csv": source_path.relative_to(run_root / "inputs").as_posix(),
                "evidence_artifact": audit["evidence_artifact"],
                "evidence_artifact_sha256": file_sha256(artifact_path),
                "row_count": len(targets),
                "row_ids": evidence_id_order,
                "candidate_better_absolute_error_count": better_count,
                "candidate_tied_absolute_error_count": tied_count,
                "candidate_worse_absolute_error_count": worse_count,
                "all_candidate_absolute_errors_lower": (
                    better_count == len(targets)
                    and tied_count == 0
                    and worse_count == 0
                ),
                "metric": audit["metric"],
                "comparison_kind": audit["comparison_kind"],
                "target_column": audit["source_target_column"],
                "source_baseline_column": audit["source_baseline_column"],
                "baseline_evidence_column": audit["evidence_baseline_column"],
                "baseline_model_input_columns": audit["baseline_model_input_columns"],
                "baseline_model_target_column": audit["baseline_model_target_column"],
                "baseline_fit_condition": audit["baseline_fit_condition"],
                "candidate_fit_condition": audit["candidate_fit_condition"],
                "model_input_columns": audit["candidate_model_input_columns"],
                "model_target_column": audit["candidate_model_target_column"],
                "fit_evaluation_relation": audit["fit_evaluation_relation"],
                "evaluation_target_usage": audit["evaluation_target_usage"],
                "candidate_column": audit["evidence_candidate_column"],
                "recomputed_measurements": expected_measurements,
            }
        )
    return errors, trusted_rows


def _paired_directional_result_errors(
    design: dict[str, Any],
    worker_result: dict[str, Any],
    trusted_comparisons: list[dict[str, Any]],
) -> list[str]:
    """Reject text results that reverse a trusted paired error comparison.

    This check is intentionally narrow. It applies only to MAE/RMSE direction
    results that explicitly refer to the candidate condition, and it derives
    the direction from the recomputed measurements rather than from a result
    identifier or a model-authored interpretation.
    """

    audits = {
        str(row.get("id")): row
        for row in design.get("paired_comparison_audits", [])
        if isinstance(row, dict) and row.get("metric") in {"mae", "rmse"}
    }
    result_plan = {
        str(row.get("id")): row
        for row in design.get("result_plan", [])
        if isinstance(row, dict)
    }
    observed_results = {
        str(row.get("id")): row
        for row in worker_result.get("result_items", [])
        if isinstance(row, dict)
        and row.get("value_kind") in {"text", "category"}
        and isinstance(row.get("value"), str)
    }

    lower_words = re.compile(
        r"降低|下降|减少|减小|更低|低于|改善|好转|"
        r"\b(?:lower|lowered|decreas(?:e|ed)|reduc(?:e|ed)|improv(?:e|ed)|better)\b",
        re.IGNORECASE,
    )
    higher_words = re.compile(
        r"升高|上升|增加|增大|更高|高于|恶化|变差|"
        r"\b(?:higher|increas(?:e|ed)|rais(?:e|ed)|wors(?:e|ened))\b",
        re.IGNORECASE,
    )
    equal_words = re.compile(
        r"不变|相同|持平|\b(?:unchanged|same|equal)\b",
        re.IGNORECASE,
    )

    def condition_aliases(condition: object, measurement_name: object) -> set[str]:
        source = " ".join(
            (
                str(condition or ""),
                str(measurement_name or ""),
            )
        ).lower()
        searchable_source = re.sub(r"[_-]+", " ", source)
        aliases = {
            token
            for token in re.split(r"[^a-z0-9\u3400-\u9fff]+", source)
            if len(token) >= 3
        }
        aliases.difference_update(
            {
                "mae",
                "rmse",
                "mse",
                "error",
                "metric",
                "measurement",
                "candidate",
                "baseline",
                "holdout",
                "validation",
            }
        )
        if re.search(
            r"\b(?:exclude|excluded|without|remove|removed|filter|filtered)\b|"
            r"排除|剔除|移除|不含|不包含",
            searchable_source,
        ):
            aliases.update(
                {
                    "exclude",
                    "excluded",
                    "without",
                    "remove",
                    "removed",
                    "排除",
                    "剔除",
                    "移除",
                    "不含",
                    "不包含",
                }
            )
        if re.search(
            r"\b(?:include|included|with|retain|retained|all)\b|保留|包含|纳入",
            searchable_source,
        ):
            aliases.update(
                {
                    "include",
                    "included",
                    "with",
                    "retain",
                    "retained",
                    "保留",
                    "包含",
                    "纳入",
                }
            )
        return aliases

    def mentions_condition(text: str, aliases: set[str]) -> bool:
        normalized = re.sub(r"[_-]+", " ", text.lower())
        return any(
            (
                alias in normalized
                if re.search(r"[\u3400-\u9fff]", alias)
                else re.search(rf"\b{re.escape(alias)}\b", normalized) is not None
            )
            for alias in aliases
        )

    errors: list[str] = []
    for trusted in trusted_comparisons:
        audit = audits.get(str(trusted.get("id")))
        if audit is None:
            continue
        values = trusted.get("recomputed_measurements") or {}
        baseline_name = audit.get("baseline_measurement")
        candidate_name = audit.get("candidate_measurement")
        if baseline_name not in values or candidate_name not in values:
            continue
        baseline_value = float(values[baseline_name])
        candidate_value = float(values[candidate_name])
        if math.isclose(
            baseline_value,
            candidate_value,
            rel_tol=1e-9,
            abs_tol=1e-12,
        ):
            trusted_direction = "equal"
        elif candidate_value < baseline_value:
            trusted_direction = "lower"
        else:
            trusted_direction = "higher"

        metric_markers = (
            ("mae", "平均绝对误差")
            if audit["metric"] == "mae"
            else ("rmse", "均方根误差")
        )
        candidate_aliases = condition_aliases(
            audit.get("candidate_fit_condition"),
            candidate_name,
        )
        for result_id, observed in observed_results.items():
            planned = result_plan.get(result_id)
            if planned is None:
                continue
            observed_text = str(observed["value"]).strip()
            context = " ".join(
                (
                    result_id,
                    str(planned.get("display_name", "")),
                    str(planned.get("scientific_meaning", "")),
                    observed_text,
                )
            ).lower()
            if not any(marker in context for marker in metric_markers):
                continue
            if not mentions_condition(context, candidate_aliases):
                continue

            says_lower = lower_words.search(observed_text) is not None
            says_higher = higher_words.search(observed_text) is not None
            says_equal = equal_words.search(observed_text) is not None
            claimed = {
                direction
                for direction, present in (
                    ("lower", says_lower),
                    ("higher", says_higher),
                    ("equal", says_equal),
                )
                if present
            }
            if not claimed:
                continue
            if claimed != {trusted_direction}:
                errors.append(
                    f"{result_id} contradicts trusted paired comparison "
                    f"{audit['id']}: candidate {audit['metric']} is "
                    f"{trusted_direction}, but the typed result says "
                    f"{observed_text!r}"
                )
    return errors


def _execution_outcome(facts: dict[str, Any]) -> tuple[str | None, str | None]:
    reason = facts.get("stop_reason")
    if reason == "cancelled_by_user":
        return "cancelled_by_user", "用户请求终止了当前沙箱进程。"
    if reason in {
        "stdout_budget",
        "stderr_budget",
        "disk_budget",
        "wall_budget_parent_guard",
        "wall_budget",
        "resource_budget",
    }:
        return "budget_stopped", f"执行触发资源预算：{reason}。"
    if reason is not None:
        return "technical_failure", f"执行触发安全或输出策略：{reason}。"
    if facts.get("windows_process_exit_code") != 0 or facts.get("sandbox_exit_code") != 0:
        return "technical_failure", "沙箱进程以非零状态退出。"
    return None, None


def _exact_replay_reproduction_errors(
    state: dict[str, Any],
    design: dict[str, Any],
    worker_result: dict[str, Any],
    verified_artifact_sources: dict[str, Path],
    stage_id: str,
) -> list[str]:
    lineage = state.get("lineage")
    if not isinstance(lineage, dict) or lineage.get("mode") != "exact_replay":
        return []
    source_run_id = lineage.get("source_run_id")
    if not isinstance(source_run_id, str):
        return ["exact replay lineage has no source run id"]
    source_root = runs_root() / source_run_id
    source_record_path = source_root / "record.json"
    if not source_record_path.is_file():
        return ["exact replay source record is unavailable"]
    source_record = read_json(source_record_path)
    source_payload = dict(source_record)
    stored_hash = source_payload.pop("record_sha256", None)
    if stored_hash != canonical_sha256(source_payload):
        return ["exact replay source record changed after replay preparation"]
    source_stage_record = source_record
    for row in source_record.get("stage_history", []):
        if row.get("stage_id") != stage_id or not isinstance(row.get("record_path"), str):
            continue
        candidate = source_root / Path(*row["record_path"].split("/"))
        if candidate.is_file():
            source_stage_record = read_json(candidate)
        break
    source_worker = source_stage_record.get("worker_result")
    if not isinstance(source_worker, dict):
        return ["exact replay source lacks a verified worker result"]
    errors: list[str] = []
    source_measurements = {
        row["name"]: {
            "value": row["value"],
            "unit": row["unit"],
            "role": row["role"],
        }
        for row in source_worker.get("measurements", [])
    }
    current_measurements = {
        row["name"]: {
            "value": row["value"],
            "unit": row["unit"],
            "role": row["role"],
        }
        for row in worker_result.get("measurements", [])
    }
    if canonical_sha256(source_measurements) != canonical_sha256(current_measurements):
        errors.append("deterministic replay measurements differ from the source run")
    source_typed_results = source_worker.get("result_items", [])
    current_typed_results = worker_result.get("result_items", [])
    if canonical_sha256(source_typed_results) != canonical_sha256(current_typed_results):
        errors.append("deterministic replay typed results differ from the source run")
    source_artifacts = {
        row["path"]: row
        for row in source_stage_record.get("public_artifacts", [])
        if Path(str(row.get("path", ""))).name != "worker_result.json"
    }
    for artifact in worker_result.get("artifacts", []):
        artifact_path = artifact["path"]
        source_rows = [
            row
            for path, row in source_artifacts.items()
            if path == f"public/{artifact_path}"
            or path.endswith(f"/{artifact_path}")
        ]
        current_path = verified_artifact_sources.get(artifact_path)
        if len(source_rows) != 1 or current_path is None:
            errors.append(
                f"deterministic replay artifact cannot be matched: {artifact_path}"
            )
            continue
        if source_rows[0].get("sha256") != file_sha256(current_path):
            errors.append(
                f"deterministic replay artifact differs from source: {artifact_path}"
            )
    if stage_execution(design, stage_id).get("deterministic") is not True:
        return []
    return errors


def _record_replay_metadata(
    run_root: Path,
    state: dict[str, Any],
    request: dict[str, Any],
    design: dict[str, Any] | None,
    worker_result: dict[str, Any] | None,
    stage_id: str,
) -> dict[str, Any]:
    lineage = state.get("lineage")
    if not isinstance(lineage, dict):
        lineage = {
            "mode": "legacy_unspecified",
            "source_run_id": request.get("replay_of"),
            "matching_run_ids": [],
        }
    result = dict(lineage)
    result.setdefault("mode", "fresh")
    result.setdefault("source_run_id", request.get("replay_of"))
    result.setdefault("matching_run_ids", [])
    result["workflow_resume_supported"] = True
    result["algorithm_resume_claimed"] = False
    result["pi_command"] = f"/automatic-experiment 重放 {state['run_id']}"
    if design is not None:
        result["current_design_sha256"] = canonical_sha256(design)
        result["current_parameters_sha256"] = canonical_sha256(
            {
                "resource_budget": request["resource_budget"],
                "seed_policy": request["seed_policy"],
                "experiment_stages": design["experiment_stages"],
            }
        )
    manifest_path = run_root / "input_snapshot.json"
    if manifest_path.is_file():
        result["current_input_snapshot_sha256"] = canonical_sha256(
            read_json(manifest_path)
        )
        result["current_input_fingerprint"] = state.get("input_fingerprint")
    attempt = read_json(run_root / "attempts" / state["current_attempt"] / "attempt.json")
    result["current_code_bundle_sha256"] = attempt.get("code_bundle_sha256")
    stage_attempts = {
        row["stage_id"]: row["attempt_id"]
        for row in state.get("stage_history", [])
        if isinstance(row, dict)
        and isinstance(row.get("stage_id"), str)
        and isinstance(row.get("attempt_id"), str)
    }
    stage_attempts[stage_id] = state["current_attempt"]
    current_code_sha256: dict[str, dict[str, str]] = {}
    for completed_stage_id, attempt_id in stage_attempts.items():
        metadata = read_json(run_root / "attempts" / attempt_id / "attempt.json")
        current_code_sha256[completed_stage_id] = {
            row["path"].removeprefix("code/"): row["sha256"]
            for row in metadata.get("files", [])
            if row["path"].startswith("code/")
            and row["path"] != "code/worker_request.json"
        }
    result["current_code_sha256"] = current_code_sha256
    if result["mode"] == "exact_replay":
        identity_checks = {
            "input_fingerprint_match": (
                result.get("source_input_fingerprint")
                == result.get("current_input_fingerprint")
            ),
            "design_sha256_match": (
                result.get("source_design_sha256")
                == result.get("current_design_sha256")
            ),
            "code_sha256_match": (
                result.get("source_code_sha256")
                == result.get("current_code_sha256")
            ),
            "parameters_sha256_match": (
                result.get("source_parameters_sha256")
                == result.get("current_parameters_sha256")
            ),
            "environment_sha256_match": (
                result.get("source_environment_sha256")
                == result.get("current_environment_sha256")
            ),
        }
        if (
            design is not None
            and stage_execution(design, stage_id).get("deterministic") is True
        ):
            identity_checks["deterministic_measurements_reproduced"] = (
                worker_result is not None
            )
            result["measurement_reproduction_status"] = "verified"
        else:
            result["measurement_reproduction_status"] = (
                "not_applicable_non_deterministic"
            )
        result["identity_checks"] = identity_checks
        result["exact_replay_verified"] = all(identity_checks.values())
    return result


def verify_attempt(
    run_root: Path,
    state: dict[str, Any],
    request: dict[str, Any],
    response: dict[str, Any],
    design: dict[str, Any],
    attempt_id: str,
    scientific_assessment: dict[str, Any] | None,
    *,
    stage_id: str,
    persist_run_record: bool = True,
) -> dict[str, Any]:
    attempt_root = run_root / "attempts" / attempt_id
    facts = read_json(attempt_root / "execution.json")
    attempt = read_json(attempt_root / "attempt.json")
    if attempt.get("stage_id") != stage_id:
        raise VerificationError("attempt stage identity does not match the active stage")
    stage = experiment_stage(design, stage_id)
    execution = stage_execution(design, stage_id)
    active_design = _active_stage_design(design, stage_id)
    outcome, reason = _execution_outcome(facts)
    worker_result: dict[str, Any] | None = None
    assessment: dict[str, Any] | None = None
    verified_artifact_sources: dict[str, Path] = {}
    paired_comparison_evidence: list[dict[str, Any]] = []
    try:
        verify_attempt_immutable(attempt_root, attempt)
        attempt_files_immutable = True
    except AttemptError as exc:
        attempt_files_immutable = False
        outcome = "boundary_blocked"
        reason = str(exc)

    sandbox_isolation = _sandbox_isolation_passed(facts["sandbox_policy"])
    if not sandbox_isolation and outcome is None:
        outcome = "boundary_blocked"
        reason = "sandbox policy did not establish the required isolation boundary"

    try:
        current_inventory = output_inventory(
            attempt_root / "output",
            request["resource_budget"]["disk_mb"],
            request["resource_budget"]["single_file_mb"],
        )
        outputs_immutable = current_inventory == facts.get("output_inventory", [])
        output_integrity_error = None
    except Exception as exc:
        outputs_immutable = False
        output_integrity_error = str(exc)
    if not outputs_immutable and outcome is None:
        outcome = "boundary_blocked"
        reason = "attempt outputs changed after immutable execution facts were recorded"

    verification_checks: list[dict[str, Any]] = [
        {"check": "attempt_files_immutable", "passed": attempt_files_immutable},
        {"check": "sandbox_isolation", "passed": sandbox_isolation},
        {
            "check": "attempt_outputs_immutable",
            "passed": outputs_immutable,
            "error": output_integrity_error,
        },
    ]
    inventory = facts.get("output_inventory", [])
    try:
        _secret_scan(attempt_root, inventory)
        verification_checks.append({"check": "secret_scan", "passed": True})
    except VerificationError as exc:
        verification_checks.append({"check": "secret_scan", "passed": False})
        outcome = "boundary_blocked"
        reason = str(exc)
    result_path = attempt_root / "output" / "result.json"
    if outcome is None:
        if not result_path.is_file():
            outcome = "technical_failure"
            reason = "沙箱退出成功但没有产生受信任的 result.json。"
            verification_checks.append({"check": "worker_result_present", "passed": False})
        else:
            verification_checks.append({"check": "worker_result_present", "passed": True})
            try:
                worker_result = validate_worker_result(
                    json.loads(result_path.read_text(encoding="utf-8"))
                )
                verification_checks.append({"check": "worker_result_contract", "passed": True})
            except (json.JSONDecodeError, ValueError) as exc:
                verification_checks.append({"check": "worker_result_contract", "passed": False})
                outcome = "technical_failure"
                reason = f"worker result 未通过合同检查：{exc}"
    if outcome is None and worker_result is not None:
        missing_artifacts: list[str] = []
        unsafe_artifacts: list[str] = []
        for artifact in worker_result["artifacts"]:
            try:
                source = safe_output_path(attempt_root / "output", artifact["path"])
            except PathPolicyError:
                unsafe_artifacts.append(artifact["path"])
                continue
            if source.is_file():
                verified_artifact_sources[artifact["path"]] = source
            else:
                missing_artifacts.append(artifact["path"])
        expected = set(execution["expected_artifacts"])
        declared = {row["path"] for row in worker_result["artifacts"]}
        undeclared_expected = sorted(expected - declared)
        produced = {
            row["path"]
            for row in inventory
            if isinstance(row, dict)
            and isinstance(row.get("path"), str)
            and row["path"] != "result.json"
        }
        undeclared_outputs = sorted(produced - declared)
        artifacts_valid = not (
            missing_artifacts
            or unsafe_artifacts
            or undeclared_expected
            or undeclared_outputs
        )
        verification_checks.append(
            {
                "check": "declared_artifacts_present",
                "passed": artifacts_valid,
                "missing": sorted(missing_artifacts),
                "unsafe": sorted(unsafe_artifacts),
                "expected_not_declared": undeclared_expected,
                "outputs_not_declared": undeclared_outputs,
            }
        )
        if not artifacts_valid:
            outcome = "technical_failure"
            details = []
            if missing_artifacts:
                details.append(f"missing={sorted(missing_artifacts)}")
            if unsafe_artifacts:
                details.append(f"unsafe={sorted(unsafe_artifacts)}")
            if undeclared_expected:
                details.append(f"expected_not_declared={undeclared_expected}")
            if undeclared_outputs:
                details.append(f"outputs_not_declared={undeclared_outputs}")
            reason = "worker artifacts failed verification: " + ", ".join(details)
    if outcome is None and worker_result is not None:
        inferential_output_errors = _unrequested_inferential_output_errors(
            request["task"],
            worker_result,
            verified_artifact_sources,
        )
        verification_checks.append(
            {
                "check": "unrequested_inferential_outputs",
                "passed": not inferential_output_errors,
                "errors": inferential_output_errors,
            }
        )
        if inferential_output_errors:
            outcome = "technical_failure"
            reason = (
                "worker artifacts contain an unrequested inferential output: "
                + "; ".join(inferential_output_errors)
            )
    if outcome is None and worker_result is not None:
        required_measurements = set(stage["measurement_refs"])
        required_results = set(stage["result_refs"])
        required_endpoints = {
            ref
            for criterion in design["criteria"]
            if criterion["id"] in set(stage["criterion_refs"])
            for ref in criterion["endpoint_refs"]
        }
        actual_measurements = {row["name"] for row in worker_result["measurements"]}
        actual_results = {row["id"] for row in worker_result["result_items"]}
        actual_endpoints = {row["id"] for row in worker_result["endpoint_results"]}
        missing_measurements = sorted(required_measurements - actual_measurements)
        missing_results = sorted(required_results - actual_results)
        missing_endpoints = sorted(required_endpoints - actual_endpoints)
        estimand_matches = (
            worker_result["scientific_payload"]["primary_estimand"]
            == design["interpretation_policy"]["primary_estimand"]
        )
        coverage_valid = (
            not missing_measurements
            and not missing_results
            and not missing_endpoints
            and estimand_matches
        )
        verification_checks.append(
            {
                "check": "design_output_coverage",
                "passed": coverage_valid,
                "missing_measurements": missing_measurements,
                "missing_results": missing_results,
                "missing_endpoints": missing_endpoints,
                "primary_estimand_matches": estimand_matches,
            }
        )
        if not coverage_valid:
            outcome = "technical_failure"
            reason = (
                "worker result does not cover the validated research design: "
                f"missing_measurements={missing_measurements}, "
                f"missing_results={missing_results}, "
                f"missing_endpoints={missing_endpoints}, "
                f"primary_estimand_matches={estimand_matches}"
            )
    if outcome is None and worker_result is not None:
        planned_by_name = {
            row["name"]: row
            for row in design["measurement_plan"]
            if row["name"] in set(stage["measurement_refs"])
        }
        observed_by_name = {
            row["name"]: row for row in worker_result["measurements"]
        }
        measurement_plan_errors: list[str] = []
        for name in sorted(set(planned_by_name) - set(observed_by_name)):
            measurement_plan_errors.append(f"planned measurement is missing: {name}")
        for name in sorted(set(observed_by_name) - set(planned_by_name)):
            measurement_plan_errors.append(f"unplanned measurement was emitted: {name}")
        for name in sorted(set(planned_by_name) & set(observed_by_name)):
            planned = planned_by_name[name]
            observed = observed_by_name[name]
            if planned["role"] != observed["role"]:
                measurement_plan_errors.append(
                    f"{name} role differs: planned={planned['role']}, observed={observed['role']}"
                )
            if planned["unit"] != observed["unit"]:
                measurement_plan_errors.append(
                    f"{name} unit differs: planned={planned['unit']!r}, "
                    f"observed={observed['unit']!r}"
                )
        verification_checks.append(
            {
                "check": "measurement_plan_matches_worker_result",
                "passed": not measurement_plan_errors,
                "errors": measurement_plan_errors,
            }
        )
        if measurement_plan_errors:
            outcome = "technical_failure"
            reason = (
                "worker measurements do not match the validated measurement plan: "
                + "; ".join(measurement_plan_errors)
            )
    if outcome is None and worker_result is not None:
        planned_results = {
            row["id"]: row
            for row in design["result_plan"]
            if row["id"] in set(stage["result_refs"])
        }
        observed_results = {row["id"]: row for row in worker_result["result_items"]}
        result_plan_errors: list[str] = []
        for result_id in sorted(set(planned_results) - set(observed_results)):
            result_plan_errors.append(f"planned typed result is missing: {result_id}")
        for result_id in sorted(set(observed_results) - set(planned_results)):
            result_plan_errors.append(f"unplanned typed result was emitted: {result_id}")
        for result_id in sorted(set(planned_results) & set(observed_results)):
            planned = planned_results[result_id]
            observed = observed_results[result_id]
            for field in ("display_name", "value_kind", "role", "unit"):
                if planned[field] != observed[field]:
                    result_plan_errors.append(
                        f"{result_id} {field} differs from the validated plan"
                    )
        verification_checks.append(
            {
                "check": "typed_result_plan_matches_worker_result",
                "passed": not result_plan_errors,
                "errors": result_plan_errors,
            }
        )
        if result_plan_errors:
            outcome = "technical_failure"
            reason = (
                "worker typed results do not match the validated stage plan: "
                + "; ".join(result_plan_errors)
            )
    if outcome is None and worker_result is not None:
        consistency_errors = _measurement_artifact_errors(
            worker_result,
            verified_artifact_sources,
        )
        consistency_errors.extend(
            _comparison_consistency_errors(worker_result, active_design)
        )
        paired_errors, paired_comparison_evidence = _paired_comparison_audit_errors(
            run_root,
            active_design,
            worker_result,
            verified_artifact_sources,
        )
        consistency_errors.extend(paired_errors)
        directional_result_errors = _paired_directional_result_errors(
            active_design,
            worker_result,
            paired_comparison_evidence,
        )
        consistency_errors.extend(directional_result_errors)
        verification_checks.append(
            {
                "check": "measurement_consistency",
                "passed": not consistency_errors,
                "errors": consistency_errors,
            }
        )
        verification_checks.append(
            {
                "check": "paired_comparison_recomputation",
                "passed": not paired_errors,
                "audits": paired_comparison_evidence,
                "errors": paired_errors,
            }
        )
        verification_checks.append(
            {
                "check": "typed_result_scientific_consistency",
                "passed": not directional_result_errors,
                "errors": directional_result_errors,
            }
        )
        if consistency_errors:
            outcome = "technical_failure"
            reason = "worker results failed consistency checks: " + "; ".join(
                consistency_errors
            )
    if outcome is None and worker_result is not None:
        replay_errors = _exact_replay_reproduction_errors(
            state,
            design,
            worker_result,
            verified_artifact_sources,
            stage_id,
        )
        verification_checks.append(
            {
                "check": "exact_replay_reproduction",
                "passed": not replay_errors,
                "applicable": (
                    isinstance(state.get("lineage"), dict)
                    and state["lineage"].get("mode") == "exact_replay"
                    and execution.get("deterministic") is True
                ),
                "status": (
                    "verified"
                    if (
                        isinstance(state.get("lineage"), dict)
                        and state["lineage"].get("mode") == "exact_replay"
                        and execution.get("deterministic") is True
                        and not replay_errors
                    )
                    else "not_applicable_non_deterministic"
                    if (
                        isinstance(state.get("lineage"), dict)
                        and state["lineage"].get("mode") == "exact_replay"
                        and execution.get("deterministic") is not True
                    )
                    else "not_applicable"
                ),
                "errors": replay_errors,
            }
        )
        if replay_errors:
            outcome = "technical_failure"
            reason = "exact replay reproduction failed: " + "; ".join(replay_errors)
    if outcome is None and worker_result is not None:
        if scientific_assessment is None:
            raise AssessmentRequired(
                worker_result,
                facts,
                verified_artifact_sources,
                active_design,
                paired_comparison_evidence,
                stage_id,
            )
        assessment = validate_scientific_assessment(
            scientific_assessment,
            active_design,
            worker_result,
            task_text=request["task"],
            stage_id=stage_id,
            evidence_basis_texts=_immutable_input_basis_texts(
                run_root,
                active_design,
            ),
        )
        outcome = assessment["proposed_outcome"]
        reason = assessment["rationale"]
        verification_checks.append({"check": "scientific_assessment_contract", "passed": True})
    if outcome is None or reason is None:
        raise VerificationError("verification did not resolve a terminal outcome")

    public_rows: list[dict[str, Any]] = []
    public_root = run_root / "public"
    if worker_result is not None and outcome not in {"boundary_blocked"}:
        release_root = (
            public_root / "stages" / stage_id
            if outcome
            in {
                "completed_interpretable",
                "partial_result",
                "scientific_null",
                "high_uncertainty",
            }
            else public_root / "attempts" / attempt_id
        )
        release_root.mkdir(parents=True, exist_ok=True)
        for artifact in worker_result["artifacts"]:
            source = verified_artifact_sources.get(artifact["path"])
            if source is None:
                continue
            target = release_root / Path(*artifact["path"].split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise VerificationError(f"public artifact would overwrite an existing file: {artifact['path']}")
            shutil.copyfile(source, target)
            public_rows.append(
                {
                    "path": target.relative_to(run_root).as_posix(),
                    "kind": artifact["kind"],
                    "description": artifact["description"],
                    "size_bytes": target.stat().st_size,
                    "sha256": file_sha256(target),
                }
            )
        result_target = release_root / "worker_result.json"
        if not result_target.exists():
            shutil.copyfile(result_path, result_target)
            public_rows.append(
                {
                    "path": result_target.relative_to(run_root).as_posix(),
                    "kind": "json",
                    "description": "受信任机器核验结果",
                    "size_bytes": result_target.stat().st_size,
                    "sha256": file_sha256(result_target),
                }
            )
    public_rows.sort(key=lambda row: row["path"])
    criterion_evidence = (
        _criterion_evidence(design, worker_result, stage_id)
        if worker_result is not None
        else []
    )
    if assessment is not None:
        assessment_by_id = {
            row["criterion_id"]: row for row in assessment["criterion_results"]
        }
        for row in criterion_evidence:
            decision = assessment_by_id.get(row["criterion_id"])
            if decision is not None:
                row["assessment_status"] = decision["status"]
                row["assessment_explanation"] = decision["explanation"]
    record = {
        "schema_version": RECORD_VERSION,
        "run_id": state["run_id"],
        "created_at": state["created_at"],
        "verified_at": utc_now(),
        "lifecycle_phase": "verification_finished",
        "stage_id": stage_id,
        "execution_state": (
            "completed"
            if outcome in {
                "completed_interpretable",
                "partial_result",
                "scientific_null",
                "high_uncertainty",
            }
            else "stopped"
            if outcome in {"budget_stopped", "cancelled_by_user"}
            else "failed"
        ),
        "outcome": outcome,
        "outcome_reason": reason,
        "task_name": request["task_name"],
        "task": request["task"],
        "request_sha256": canonical_sha256(request),
        "response_sha256": canonical_sha256(response),
        "design_sha256": canonical_sha256(design),
        "final_stage_id": stage_id,
        "stage_history": state.get("stage_history", []),
        "artifact_lineage": state.get("artifact_lineage", []),
        "budget_usage": state.get("budget_usage", {}),
        "input_snapshot": (
            read_json(run_root / "input_snapshot.json")
            if (run_root / "input_snapshot.json").is_file()
            else None
        ),
        "attempt": attempt,
        "attempt_history": _attempt_history(
            run_root,
            attempt_id,
            outcome,
            reason,
            facts,
        ),
        "execution_facts": facts,
        "worker_result": worker_result,
        "scientific_assessment": assessment,
        "evidence_ledger": {
            "research_frame": design["research_frame"],
            "criteria": criterion_evidence,
            "paired_comparisons": paired_comparison_evidence,
        },
        "verification_checks": verification_checks,
        "public_artifacts": public_rows,
        "replay": _record_replay_metadata(
            run_root,
            state,
            request,
            design,
            worker_result,
            stage_id,
        ),
    }
    record["record_sha256"] = canonical_sha256(record)
    atomic_write_json(attempt_root / "record.json", record)
    stage_root = run_root / "stages" / stage_id
    stage_root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(stage_root / "record.json", record)
    if persist_run_record:
        atomic_write_json(run_root / "record.json", record)
    return record


def create_early_record(
    run_root: Path,
    state: dict[str, Any],
    request: dict[str, Any],
    response: dict[str, Any] | None,
    outcome: str,
    reason: str,
) -> dict[str, Any]:
    phase = str(state.get("phase") or "request_bound")
    execution_state = (
        "not_started"
        if phase
        in {
            "request_bound",
            "inputs_snapshotted",
            "design_validated",
            "stage_transitioned",
        }
        else "prepared_not_started"
        if phase == "attempt_prepared"
        else "started_unconfirmed"
        if phase == "execution_started"
        else "finished_unverified"
        if phase == "execution_finished"
        else "stopped"
    )
    current_attempt = state.get("current_attempt")
    attempt_root = (
        run_root / "attempts" / current_attempt
        if isinstance(current_attempt, str)
        else None
    )
    attempt = (
        read_json(attempt_root / "attempt.json")
        if attempt_root is not None and (attempt_root / "attempt.json").is_file()
        else None
    )
    execution_facts = (
        read_json(attempt_root / "execution.json")
        if attempt_root is not None and (attempt_root / "execution.json").is_file()
        else None
    )
    design = (
        read_json(run_root / "design.json")
        if (run_root / "design.json").is_file()
        else None
    )
    record = {
        "schema_version": RECORD_VERSION,
        "run_id": state["run_id"],
        "created_at": state["created_at"],
        "verified_at": utc_now(),
        "lifecycle_phase": "verification_finished",
        "execution_state": execution_state,
        "outcome": outcome,
        "outcome_reason": reason,
        "task_name": request["task_name"],
        "task": request["task"],
        "request_sha256": canonical_sha256(request),
        "response_sha256": (
            canonical_sha256(response) if response is not None else None
        ),
        "design_sha256": (
            canonical_sha256(design) if design is not None else None
        ),
        "final_stage_id": state.get("current_stage_id"),
        "stage_history": state.get("stage_history", []),
        "artifact_lineage": state.get("artifact_lineage", []),
        "budget_usage": state.get("budget_usage", {}),
        "input_snapshot": (
            read_json(run_root / "input_snapshot.json")
            if (run_root / "input_snapshot.json").is_file()
            else None
        ),
        "attempt": attempt,
        "attempt_history": (
            _attempt_history(
                run_root,
                current_attempt,
                outcome,
                reason,
                execution_facts,
            )
            if isinstance(current_attempt, str) and execution_facts is not None
            else []
        ),
        "execution_facts": execution_facts,
        "worker_result": None,
        "scientific_assessment": None,
        "evidence_ledger": None,
        "verification_checks": [
            {"check": "no_false_execution_claim", "passed": True},
        ],
        "public_artifacts": [],
        "replay": {
            **(
                state["lineage"]
                if isinstance(state.get("lineage"), dict)
                else {
                    "mode": "legacy_unspecified",
                    "source_run_id": request["replay_of"],
                    "matching_run_ids": [],
                }
            ),
            "workflow_resume_supported": outcome
            in {"clarification_required", "input_missing"},
            "algorithm_resume_claimed": False,
            "pi_command": f"/automatic-experiment 继续 {state['run_id']}",
        },
    }
    record["record_sha256"] = canonical_sha256(record)
    atomic_write_json(run_root / "record.json", record)
    return record
