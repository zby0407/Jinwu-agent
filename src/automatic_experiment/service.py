"""Application service used exclusively by the Pi bridge."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from research_layout import EXPERIMENT_RESOURCE_ROOT

from .attempts import prepare_attempt, verify_attempt_immutable
from .contracts import (
    AMBIGUOUS_FLAGGED_RETENTION_LANGUAGE,
    CODE_LIKE_READER_IDENTIFIER,
    CONTRAST_PROMISE,
    DESIGN_VERSION,
    ENTRY_RESULT_VERSION,
    HARD_NUMERIC_CUTOFF,
    NUMBER_TOKEN,
    OUTCOMES,
    R_SQUARED_PLAN,
    RECORD_VERSION,
    REQUEST_VERSION,
    RESPONSE_VERSION,
    SENSITIVITY_CONTEXT,
    STAGE_OUTCOMES,
    TERMINAL_STAGE_TARGETS,
    UNCALIBRATED_BASELINE_LANGUAGE,
    ContractError,
    _linked_sensitivity_roles,
    _loss_delta_direction_conflicts,
    _paired_measurements_requiring_audit,
    _same_fitted_condition,
    _sensitivity_criterion_roles,
    canonical_sha256,
    default_request,
    experiment_stage,
    stage_execution,
    validate_design,
    validate_request,
    validate_response,
)
from .executor import doctor as executor_doctor
from .executor import execute_attempt as execute_in_sandbox
from .executor import request_stop, runtime_environment_snapshot
from .paths import (
    PathPolicyError,
    fingerprint_input_references,
    fingerprint_input_snapshot,
    resolve_input_reference,
    snapshot_input_previews,
    snapshot_inputs,
)
from .policy import validate_code_files, verify_dependencies
from .reporting import finalize_report
from .state import (
    atomic_write_json,
    checkpoint,
    create_run,
    file_sha256,
    latest_run_id,
    load_state,
    read_json,
    save_state,
    utc_now,
)
from .verification import AssessmentRequired, create_early_record, verify_attempt


class ServiceError(RuntimeError):
    """The requested lifecycle transition is invalid."""


_LOG_SECRET = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{12,}|AKIA[0-9A-Z]{16}|"
    r"(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[^\s\"']{8,}|"
    r"authorization:\s*bearer\s+\S+)",
    re.IGNORECASE,
)

_EXTERNAL_DATA_REQUEST = re.compile(
    r"(?:现有|已有|workspace|本地|provided|existing).{0,24}(?:数据|文件|data|file)|"
    r"(?:数据文件|输入文件|data\s*file|input\s*file)|"
    r"(?:读取|使用|基于|分析|检验|预测|read|use|based\s+on|analy|test|predict)"
    r".{0,32}(?:数据|文件|dataset|data|file)|"
    r"\.(?:csv|tsv|parquet|json|fits?|nc|h5)\b",
    re.IGNORECASE,
)
_SYNTHETIC_DATA_REQUEST = re.compile(
    r"(?:合成|模拟|生成|固定值|内置值|synthetic|simulat|generated|literal|fixture)",
    re.IGNORECASE,
)


def _requires_declared_input(request: dict[str, Any]) -> bool:
    task = str(request.get("task") or "")
    return bool(_EXTERNAL_DATA_REQUEST.search(task)) and not bool(
        _SYNTHETIC_DATA_REQUEST.search(task)
    )


def _diagnostic_excerpt(root: Path, relative_path: str, maximum: int = 4000) -> str:
    path = root / Path(*relative_path.split("/"))
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return _LOG_SECRET.sub("[REDACTED]", text[-maximum:])


def _blockers_describe_missing_input(blockers: list[str]) -> bool:
    text = " ".join(blockers).casefold()
    markers = (
        "required_input",
        "input_missing",
        "missing_input",
        "file_missing",
        "input file missing",
        "input not found",
        "file not found",
        "输入文件缺失",
        "输入缺失",
        "文件缺失",
        "未在输入目录",
        "未找到",
        "找不到",
    )
    return any(marker in text for marker in markers)


def _snapshot_has_verified_files(snapshot: dict[str, Any]) -> bool:
    return any(
        row.get("status") == "snapshotted" and bool(row.get("files"))
        for row in snapshot.get("inputs", [])
        if isinstance(row, dict)
    )


def _load_request(root: Path) -> dict[str, Any]:
    return validate_request(read_json(root / "request.json"))


def _elapsed_run_seconds(state: dict[str, Any]) -> float:
    created = datetime.fromisoformat(str(state["created_at"]).replace("Z", "+00:00"))
    return max(0.0, (datetime.now(timezone.utc) - created).total_seconds())


def _remaining_run_seconds(
    state: dict[str, Any],
    request: dict[str, Any],
) -> int:
    return max(
        0,
        int(request["run_budget"]["total_wall_seconds"] - _elapsed_run_seconds(state)),
    )


def _require_run_budget(
    state: dict[str, Any],
    request: dict[str, Any],
    *,
    require_unallocated_attempt: bool = True,
) -> int:
    remaining = _remaining_run_seconds(state, request)
    attempts_exhausted = (
        require_unallocated_attempt and state.get("remaining_attempts", 0) <= 0
    )
    if remaining <= 0 or attempts_exhausted:
        raise ServiceError(
            "本次运行的总时间或总尝试预算已用尽；系统将保留现有证据并生成报告。"
        )
    return remaining


def _validated_current_record(root: Path, state: dict[str, Any]) -> dict[str, Any]:
    if state["phase"] not in {"verification_finished", "report_finalized"}:
        raise ServiceError(
            "the current attempt has not been verified; finalize is not allowed"
        )
    record = read_json(root / "record.json")
    stored_hash = record.get("record_sha256")
    hash_payload = dict(record)
    hash_payload.pop("record_sha256", None)
    if (
        not isinstance(stored_hash, str)
        or stored_hash != canonical_sha256(hash_payload)
        or stored_hash != state.get("verified_record_sha256")
    ):
        raise ServiceError("record.json does not match the verified state")
    current_attempt = state.get("current_attempt")
    record_attempt = record.get("attempt")
    if current_attempt is not None:
        if (
            not isinstance(record_attempt, dict)
            or record_attempt.get("attempt_id") != current_attempt
        ):
            raise ServiceError(
                "record.json belongs to an older attempt and cannot be finalized"
            )
    elif record_attempt is not None:
        raise ServiceError("early terminal record unexpectedly contains an attempt")
    return record


def _request_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if "request" in payload:
        return validate_request(payload["request"])
    raw = payload.get("request_input")
    if not isinstance(raw, str):
        raise ServiceError("request_input must be text")
    if raw.startswith("@"):
        path = resolve_input_reference(raw[1:].strip())
        if not path.is_file() or path.stat().st_size > 1024 * 1024:
            raise ServiceError(
                "advanced request must be one UTF-8 JSON file no larger than 1 MiB"
            )
        return validate_request(json.loads(path.read_text(encoding="utf-8")))
    return validate_request(default_request(raw))


def _request_fingerprint(request: dict[str, Any]) -> str:
    identity = dict(request)
    identity["replay_of"] = None
    return canonical_sha256(identity)


def _snapshot_fingerprints(
    root: Path,
    request: dict[str, Any],
) -> tuple[str, str]:
    manifest = read_json(root / "input_snapshot.json")
    return (
        _request_fingerprint(request),
        fingerprint_input_snapshot(request, manifest)["input_fingerprint"],
    )


def _authoring_guide() -> dict[str, Any]:
    """Return the exact current shapes needed by the planning and coding turns."""

    final_stage_outcome_rules = {
        "completed": "plain-language condition for a verified completed stage",
        "inconclusive": "plain-language condition for valid but inconclusive evidence",
        "input_missing": "plain-language condition for unavailable required input",
        "evidence_conflict": "plain-language condition for unreconciled evidence",
        "method_invalid": "plain-language condition showing the method is unsuitable",
        "technical_failure": "plain-language condition for a code or artifact failure",
        "budget_reached": "plain-language condition for exhausted time or attempts",
    }
    final_stage_transitions = {
        "completed": "completed_interpretable",
        "inconclusive": "high_uncertainty",
        "input_missing": "input_missing",
        "evidence_conflict": "high_uncertainty",
        "method_invalid": "method_mismatch",
        "technical_failure": "technical_failure",
        "budget_reached": "budget_stopped",
    }

    return {
        "response": {
            "host_supplied_fields": [
                "schema_version",
                "task_name",
                "task",
                "normalized_task",
            ],
            "exact_fields": [
                "schema_version",
                "task_name",
                "task",
                "response_kind",
                "normalized_task",
                "design_summary",
                "clarifications",
                "blockers",
                "method_fit",
            ],
            "response_kind": [
                "experiment_ready",
                "clarification_required",
                "execution_blocked",
            ],
            "method_fit": ["suitable", "uncertain", "incompatible"],
            "rule": (
                "The host restores task identity. Clarifications and blockers end with a "
                "report and need no design."
            ),
        },
        "design": {
            "host_supplied_fields": [
                "schema_version",
                "task_name",
                "normalized_task",
            ],
            "exact_fields": [
                "schema_version",
                "task_name",
                "normalized_task",
                "design_summary",
                "method_fit",
                "input_ids",
                "research_frame",
                "measurement_plan",
                "result_plan",
                "method_decisions",
                "paired_comparison_audits",
                "criteria",
                "artifact_plan",
                "experiment_stages",
                "interpretation_policy",
            ],
            "object_shapes": {
                "research_frame": {
                    "primary_question": "text",
                    "analysis_mode": "task-specific text",
                    "claim_scope": "text",
                    "input_evidence": [
                        {
                            "input_id": "one design input id",
                            "role": "text",
                            "intended_use": "text",
                            "limitations": "text",
                        }
                    ],
                    "supported_questions": ["at least one text item"],
                    "deferred_questions": [],
                    "assumptions": [],
                    "threats_to_validity": ["at least one text item"],
                    "literature_basis": "text; say no external source is needed when applicable",
                },
                "measurement_plan_item": {
                    "name": "safe id",
                    "display_name": "reader-facing text",
                    "role": "primary|secondary|diagnostic",
                    "unit": "text, may be empty",
                    "scientific_meaning": "text",
                },
                "result_plan_item": {
                    "id": "safe id",
                    "display_name": "reader-facing text",
                    "value_kind": "boolean|category|text; number/count only as diagnostics",
                    "role": "primary|secondary|diagnostic",
                    "unit": "text, may be empty",
                    "scientific_meaning": "text",
                },
                "method_decision_item": {
                    "id": "safe id",
                    "decision_key": "open task-specific safe id",
                    "decision": "text",
                    "rationale": "text",
                    "basis_kind": "user_request|located_source|data_derived|method_standard|bounded_pragmatic_choice",
                    "source_refs": [],
                    "alternatives": [],
                    "claim_limit": "text",
                },
                "criterion_item": {
                    "id": "safe id",
                    "statement": "reader-facing text",
                    "basis_kind": "user_request|located_source|data_derived|method_standard|qualitative_no_fixed_threshold",
                    "basis_text": "text including provenance for every numeric cutoff",
                    "source_refs": [],
                    "artifact_refs": ["artifact paths, not ids"],
                    "measurement_refs": [],
                    "result_refs": [],
                    "endpoint_refs": [],
                },
                "paired_comparison_item": {
                    "id": "safe id",
                    "comparison_kind": "source_baseline_vs_candidate|candidate_vs_candidate",
                    "evaluation_scope": "evaluation population; no row counts",
                    "row_filter": "null = all rows; or {column, in[]} = rows with listed values",
                    "source_input_id": "one design input id",
                    "source_row_id_column": "source column name",
                    "source_target_column": "source comparison-coordinate column",
                    "source_baseline_column": "source candidate-reading column",
                    "candidate_model_input_columns": [
                        "include source_baseline_column; exclude source_target_column"
                    ],
                    "candidate_model_target_column": "equal source_target_column",
                    "baseline_model_input_columns": [
                        "empty for source baseline; else fitted-condition inputs"
                    ],
                    "baseline_model_target_column": "null for source baseline; else source_target_column",
                    "baseline_fit_condition": "null for source baseline; else distinct text",
                    "candidate_fit_condition": "text",
                    "fit_evaluation_relation": "disjoint_rows",
                    "evaluation_target_usage": "metrics_and_evidence_only",
                    "evidence_artifact": "relative CSV path",
                    "evidence_row_id_column": "distinct evidence column",
                    "evidence_target_column": "distinct evidence column",
                    "evidence_baseline_column": "distinct evidence column",
                    "evidence_candidate_column": "distinct evidence column",
                    "metric": "mae|rmse|mean_signed_error",
                    "baseline_measurement": "planned measurement for metric",
                    "candidate_measurement": "different planned measurement for metric",
                    "delta_measurement": "planned delta measurement or null",
                    "delta_formula": "baseline_minus_candidate|candidate_minus_baseline|null",
                },
                "artifact_plan_item": {
                    "id": "safe id",
                    "path": "safe relative POSIX output path, not result.json",
                    "kind": "json|csv|text|markdown|image|fits|netcdf|hdf5|parquet|other",
                    "description": "text",
                    "producer_stage_id": "one stage id",
                },
                "interpretation_policy": {
                    "primary_estimand": "exact text later copied into worker scientific_payload",
                    "null_rule": "text",
                    "uncertainty_rule": "text",
                    "partial_rule": "text",
                },
            },
            "stage_fields": [
                "id",
                "objective",
                "input_ids",
                "consumes_artifact_ids",
                "produces_artifact_ids",
                "prerequisite_stage_ids",
                "join_policy",
                "method_outline",
                "measurement_refs",
                "result_refs",
                "endpoint_ids",
                "criterion_refs",
                "outcome_rules",
                "transitions",
                "execution",
            ],
            "stage_nested_shapes": {
                "join_policy": "all|any, including the first stage with no prerequisites",
                "outcome_rules": final_stage_outcome_rules,
                "transitions": final_stage_transitions,
                "transition_rule": (
                    "Both objects contain all seven keys. Each transition targets a later stage "
                    "or terminal state; use a later stage only when that outcome requires it."
                ),
                "execution": {
                    "entry_file": "experiment.py",
                    "dependencies": [
                        "reviewed locked third-party imports only; omit standard-library modules"
                    ],
                    "deterministic": True,
                    "seed": 1729,
                    "expected_artifacts": [
                        "exact output-root-relative paths; use [] when none"
                    ],
                },
            },
            "stage_outcomes": sorted(STAGE_OUTCOMES),
            "terminal_targets": sorted(TERMINAL_STAGE_TARGETS),
            "paired_comparison_rule": (
                "Use [] unless rows are paired."
            ),
            "rules": [
                "Use one to five forward-only stages and only methods this task needs.",
                "Each stage creates scientific output; no verify, report, or reformat-only stages.",
                "design_summary stage count must match experiment_stages.",
                "Do not require a model, split, metric, baseline, ablation, or robustness step unless needed.",
                "Use measurements for numeric answers and typed results for other values; never duplicate the same fact.",
                "Every artifact has one producer and later stages consume it by id.",
                "Each output has one producer; merge recomputing stages.",
                "Never create report.md, audit.md, state files, or a report stage.",
                "Each criterion cites an output; never invent a minimum sample-count or numeric pass gate.",
                "Bind every answer-bearing measurement or typed result to a criterion; remove unrelated diagnostics.",
                "Do not add p-values or significance tests unless the user explicitly requested inferential testing.",
                "Paired delta meanings state which condition is subtracted from which and match delta_formula.",
                "Sensitivity uses one fixed evaluation set; say include or exclude flagged observations explicitly and report both estimates plus requested differences.",
                "The same fit, evaluation scope, and metric reuse one measurement name across audits.",
                "Declare a shared uncalibrated baseline once; omit raw-to-condition deltas unless requested.",
                "Only add R-squared when requested or required by a located source; parameter prose follows the declared model direction.",
                "Use paired_comparison_audits for row-paired raw-versus-calibrated or fitted-condition comparisons.",
                "A fitting-only flag keeps all evaluation rows; reuse their uncalibrated baseline.",
                "Use one sufficient error metric unless the question needs more.",
                "Use the user's language and scientific terms, not filenames, fields, codes, or contract jargon.",
            ],
        },
        "worker_result": {
            "schema_version_value": "automatic-experiment-worker-result-v1",
            "exact_fields": [
                "schema_version",
                "execution_completed",
                "measurements",
                "result_items",
                "artifacts",
                "warnings",
                "endpoint_results",
                "scientific_payload",
            ],
            "result_value_kinds": ["number", "count", "boolean", "category", "text"],
            "artifact_kinds": [
                "json",
                "csv",
                "text",
                "markdown",
                "image",
                "fits",
                "netcdf",
                "hdf5",
                "parquet",
                "other",
            ],
            "item_shapes": {
                "measurement": {
                    "name": "declared measurement name",
                    "value": "finite number",
                    "unit": "declared unit",
                    "role": "primary|secondary|diagnostic",
                    "source_artifact": "fixed literal/constant artifact path or null",
                },
                "result_item": {
                    "id": "declared result id",
                    "display_name": "declared display name",
                    "value_kind": "number|count|boolean|category|text",
                    "value": "value matching value_kind",
                    "unit": "declared unit",
                    "role": "primary|secondary|diagnostic",
                    "source_artifact": "fixed literal/constant artifact path or null",
                },
                "artifact": {
                    "path": "exact declared relative path",
                    "kind": "declared artifact kind",
                    "description": "text",
                },
                "endpoint_result": {
                    "id": "declared endpoint id",
                    "status": "completed|failed|not_evaluated",
                    "summary": "text",
                },
                "scientific_payload": {
                    "primary_estimand": "exact design interpretation_policy.primary_estimand",
                    "estimate": "finite number or null",
                    "interval": "[low, high] or null",
                    "equivalence_bounds": "[low, high] or null",
                    "sensitivity": "text or null",
                    "uncertainty_reasons": [],
                },
            },
            "rules": [
                "Emit only results, endpoints, and artifacts declared by the active stage.",
                "Use context input Paths and read every declared prior-stage artifact from artifact_path_by_id; recomputing it from raw input is not an acceptable substitute.",
                "Large tables, arrays, figures, and scientific containers remain hashed artifacts.",
                "A JSON source must contain each exact measurement name or result id once as an equal-valued key; nesting is allowed. Do not create duplicate result_items or duplicate JSON keys.",
                "Copy the validated primary_estimand exactly.",
                "Set scientific_payload.interval to null for descriptive or deterministic work. Return an interval only when the validated design explicitly names a reproducible interval-estimation method; never manufacture an interval by adding and subtracting a convenient constant.",
            ],
        },
        "scientific_assessment": {
            "exact_fields": [
                "proposed_outcome",
                "stage_outcome",
                "rationale",
                "criterion_results",
                "uncertainty_reasons",
                "null_assessment",
                "report_narrative",
            ],
            "object_shapes": {
                "criterion_result": {
                    "criterion_id": "every design criterion exactly once",
                    "status": "met|not_met|uncertain|not_evaluated",
                    "explanation": "reader-facing text based on verified evidence",
                },
                "null_assessment": {
                    "estimand": "exact primary estimand",
                    "interval": "[low, high] or null",
                    "equivalence_bounds": "[low, high] or null",
                    "power_or_sensitivity": "text or null",
                },
                "report_narrative": {
                    "title": "text",
                    "objective": "text",
                    "data_scope": "text",
                    "method": "text",
                    "interpretation": "text",
                    "evidence_strength": "text",
                    "claim_boundary": "text",
                    "limitations": ["one to eight text items"],
                    "next_steps": ["one to eight text items"],
                },
            },
            "rule": (
                "Write only after verification. Use verified facts; distinguish technical failure "
                "from inconclusive evidence, missing input, conflict, and an invalid method. Every "
                "reported number must come from verified results, immutable inputs, or a predeclared basis. State "
                "scientific findings, estimates, and units—not code success, workflow status, or "
                "replay instructions. Do not expose raw filenames, fields, category codes, "
                "untraceable thresholds, or unchecked equations in report_narrative."
            ),
        },
    }


def _stage_worker_output_guide(
    design: dict[str, Any],
    stage_id: str,
) -> dict[str, Any]:
    """Expose the active stage's exact output identifiers in one compact block."""

    stage = experiment_stage(design, stage_id)
    artifact_paths = {
        row["id"]: row["path"] for row in design.get("artifact_plan", [])
    }
    expected_artifacts = list(stage["execution"]["expected_artifacts"])
    json_artifacts = [
        artifact_paths[artifact_id]
        for artifact_id in stage["produces_artifact_ids"]
        if artifact_id in artifact_paths
        and next(
            (
                row.get("kind")
                for row in design.get("artifact_plan", [])
                if row.get("id") == artifact_id
            ),
            None,
        )
        == "json"
    ]
    exact_value_ids = [*stage["measurement_refs"], *stage["result_refs"]]
    return {
        "schema_version": "automatic-experiment-worker-result-v1",
        "execution_completed": True,
        "measurement_names": list(stage["measurement_refs"]),
        "result_item_ids": list(stage["result_refs"]),
        "endpoint_ids": list(stage["endpoint_ids"]),
        "artifact_paths": expected_artifacts,
        "json_artifact_paths": json_artifacts,
        "json_traceability": {
            "exact_value_keys": exact_value_ids,
            "rule": (
                "For every measurement or result item that cites a JSON source_artifact, "
                "write its exact name or id once as the JSON key with the same value. "
                "The key may appear at any nesting level; do not duplicate the value."
            ),
        },
        "source_artifact_rule": (
            "Use an exact artifact-path string literal, a local constant assigned that literal, "
            "or null; reject helpers, expressions, and computed paths."
        ),
    }


def _design_repair_guide(issues: list[dict[str, Any]]) -> dict[str, Any]:
    """Return only the contract fragments needed to repair current issues.

    The complete guide is useful on the first design turn, but repeating it
    after every failed validation makes the repair response larger than the
    design itself and encourages lossy full rewrites.
    """

    full = _authoring_guide()["design"]
    paths = " ".join(str(row.get("field_path") or "") for row in issues)
    shape_keys: list[str] = []
    field_to_shape = (
        ("research_frame", "research_frame"),
        ("measurement_plan", "measurement_plan_item"),
        ("result_plan", "result_plan_item"),
        ("method_decisions", "method_decision_item"),
        ("paired_comparison_audits", "paired_comparison_item"),
        ("criteria", "criterion_item"),
        ("artifact_plan", "artifact_plan_item"),
        ("interpretation_policy", "interpretation_policy"),
    )
    for field_name, shape_name in field_to_shape:
        if field_name in paths:
            shape_keys.append(shape_name)

    guide: dict[str, Any] = {
        "host_supplied_fields": full["host_supplied_fields"],
        "exact_fields": full["exact_fields"],
        "object_shapes": {
            key: full["object_shapes"][key] for key in dict.fromkeys(shape_keys)
        },
        "repair_rules": [
            "Preserve fields not named in issues; repair all listed paths in one submission.",
            "Reader-facing text uses scientific names, never raw columns, category codes, filenames, or contract jargon.",
            "Do not add a paired comparison unless an issue explicitly requires one; when required, use exactly paired_comparison_item.",
        ],
    }
    if "experiment_stages" in paths:
        guide["stage_fields"] = full["stage_fields"]
        guide["stage_nested_shapes"] = full["stage_nested_shapes"]
    return guide


def _design_schema_issues(
    design_payload: dict[str, Any],
    request_payload: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Return all independently visible shape and cross-field issues in one response."""

    schema_path = (
        EXPERIMENT_RESOURCE_ROOT
        / "specs"
        / "automatic_experiment_design_v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    issues: list[dict[str, str]] = []
    for error in sorted(
        validator.iter_errors(design_payload),
        key=lambda item: (list(item.absolute_path), item.message),
    ):
        path = "design"
        for part in error.absolute_path:
            path += f"[{part}]" if isinstance(part, int) else f".{part}"
        issues.append(
            {
                "field_path": path,
                "message": error.message,
                "suggestion": "按 authoring_guide 中对应 object_shapes 或 stage_nested_shapes 一次性修正。",
            }
        )
    raw_input_ids = design_payload.get("input_ids")
    research_frame = design_payload.get("research_frame")
    input_evidence = (
        research_frame.get("input_evidence")
        if isinstance(research_frame, dict)
        else None
    )
    if isinstance(raw_input_ids, list) and isinstance(input_evidence, list):
        expected_input_ids = [
            row for row in raw_input_ids if isinstance(row, str) and row
        ]
        described_input_ids = [
            row.get("input_id")
            for row in input_evidence
            if isinstance(row, dict) and isinstance(row.get("input_id"), str)
        ]
        if (
            len(expected_input_ids) != len(raw_input_ids)
            or len(set(expected_input_ids)) != len(expected_input_ids)
            or len(described_input_ids) != len(input_evidence)
            or len(set(described_input_ids)) != len(described_input_ids)
            or set(described_input_ids) != set(expected_input_ids)
        ):
            issues.append(
                {
                    "field_path": "design.research_frame.input_evidence",
                    "message": (
                        "input evidence must describe each selected design input "
                        "exactly once"
                    ),
                    "suggestion": (
                        "为 input_ids 中每份实际使用的材料各写一项角色、用途与局限，"
                        "不要遗漏、重复或加入未选材料。"
                    ),
                }
            )
    raw_audits = design_payload.get("paired_comparison_audits", [])
    audits = raw_audits if isinstance(raw_audits, list) else []
    for index, audit in enumerate(audits):
        if not isinstance(audit, dict):
            continue
        label = f"design.paired_comparison_audits[{index}]"
        for field in ("baseline_fit_condition", "candidate_fit_condition"):
            field_value = audit.get(field)
            if isinstance(field_value, str) and AMBIGUOUS_FLAGGED_RETENTION_LANGUAGE.search(
                field_value
            ):
                issues.append(
                    {
                        "field_path": f"{label}.{field}",
                        "message": "quality-flag inclusion language is ambiguous",
                        "suggestion": "拟合条件明确写成包含或排除被标记观测，不要用“仅保留标记”表示保留其余观测。",
                    }
                )
        target = audit.get("source_target_column")
        model_inputs = audit.get("candidate_model_input_columns")
        if (
            isinstance(target, str)
            and isinstance(model_inputs, list)
            and target in model_inputs
        ):
            issues.append(
                {
                    "field_path": f"{label}.candidate_model_input_columns",
                    "message": "candidate model inputs must exclude the evaluation target column",
                    "suggestion": "候选方法在评价行上只能读取预测时可用的输入，不得把待比较目标作为输入。",
                }
            )
        delta_measurement = audit.get("delta_measurement")
        delta_formula = audit.get("delta_formula")
        if (delta_measurement is None) != (delta_formula is None):
            issues.append(
                {
                    "field_path": f"{label}.delta_measurement",
                    "message": "delta_measurement and delta_formula must both be null or both be set",
                    "suggestion": "需要核验差值时同时声明差值测量和相减方向；不核验差值时两项都设为 null。",
                }
            )
        evaluation_scope = audit.get("evaluation_scope")
        if isinstance(evaluation_scope, str) and re.search(
            r"\d+\s*(?:行|条|对)(?:观测)?\s*(?:评估集|评价集|留出集)",
            evaluation_scope,
        ):
            issues.append(
                {
                    "field_path": f"{label}.evaluation_scope",
                    "message": "evaluation_scope must not hard-code a row count",
                    "suggestion": "只描述评价条件与样本范围；实际观测数由逐项证据核对后写入报告，避免设计数字与真实行数矛盾。",
                }
            )
        if (
            audit.get("comparison_kind") == "candidate_vs_candidate"
            and isinstance(evaluation_scope, str)
            and re.search(
                r"(?:保留|排除|剔除|仅保留|只保留)[^。；;]{0,12}"
                r"(?:质量)?标记[^。；;]{0,16}(?:评价|评估|留出|测试)",
                evaluation_scope,
                re.IGNORECASE,
            )
        ):
            issues.append(
                {
                    "field_path": f"{label}.evaluation_scope",
                    "message": "evaluation scope mixes a fitted quality-flag condition into the fixed evaluation rows",
                    "suggestion": "只描述两种拟合条件共用的同一批固定留出观测；包含或排除被标记观测应写在 fit_condition 中。",
                }
            )

    raw_measurement_plan = design_payload.get("measurement_plan", [])
    measurement_rows = (
        raw_measurement_plan if isinstance(raw_measurement_plan, list) else []
    )
    measurement_plan = {
        row.get("name"): row
        for row in measurement_rows
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    }
    for index, row in enumerate(measurement_rows):
        if not isinstance(row, dict):
            continue
        reader_text = " ".join(
            (
                str(row.get("display_name", "")),
                str(row.get("scientific_meaning", "")),
            )
        )
        if CODE_LIKE_READER_IDENTIFIER.search(reader_text):
            issues.append(
                {
                    "field_path": f"design.measurement_plan[{index}]",
                    "message": "reader-facing measurement text exposes a raw field or category name",
                    "suggestion": "把原始列名或类别代码改写成自然语言科研名称。",
                }
            )
        if AMBIGUOUS_FLAGGED_RETENTION_LANGUAGE.search(reader_text):
            issues.append(
                {
                    "field_path": f"design.measurement_plan[{index}]",
                    "message": "quality-flag inclusion language is ambiguous",
                    "suggestion": "明确写成“包含被标记观测的拟合”或“排除被标记观测的拟合”；不要用“仅保留标记观测”表示保留其余正常观测。",
                }
            )
        if (
            re.search(
                r"(?:^|_)mse(?:_|$)|\bMSE\b",
                f"{row.get('name', '')} {row.get('display_name', '')}",
                re.IGNORECASE,
            )
            and re.search(
                r"(?:平均有符号|平均符号|mean\s+signed)",
                reader_text,
                re.IGNORECASE,
            )
        ):
            issues.append(
                {
                    "field_path": f"design.measurement_plan[{index}].name",
                    "message": "MSE cannot name a mean signed error",
                    "suggestion": "MSE 表示均方误差；平均有符号误差使用 mean_signed_error 或 bias，并同步显示名。",
                }
            )
    source_audits = [
        row
        for row in audits
        if isinstance(row, dict)
        and row.get("comparison_kind") == "source_baseline_vs_candidate"
    ]
    duplicate_links_seen: set[tuple[str, str]] = set()
    for condition_audit in audits:
        if (
            not isinstance(condition_audit, dict)
            or condition_audit.get("comparison_kind") != "candidate_vs_candidate"
        ):
            continue
        for side in ("baseline", "candidate"):
            for source_audit in source_audits:
                if not all(
                    condition_audit.get(field) == source_audit.get(field)
                    for field in (
                        "source_input_id",
                        "source_target_column",
                        "source_baseline_column",
                        "metric",
                    )
                ) or not _same_fitted_condition(
                    condition_audit.get(f"{side}_fit_condition"),
                    source_audit.get("candidate_fit_condition"),
                ):
                    continue
                condition_measurement = str(
                    condition_audit.get(f"{side}_measurement", "")
                )
                source_measurement = str(
                    source_audit.get("candidate_measurement", "")
                )
                link_key = (condition_measurement, source_measurement)
                if (
                    condition_measurement
                    and source_measurement
                    and condition_measurement != source_measurement
                    and link_key not in duplicate_links_seen
                ):
                    duplicate_links_seen.add(link_key)
                    issues.append(
                        {
                            "field_path": "design.paired_comparison_audits",
                            "message": "one fitted condition is declared under two measurement names",
                            "suggestion": "同一拟合条件在同一评价观测上的指标只声明一次；在敏感性比较中复用已有测量名并删除重复 measurement_plan 项。",
                        }
                    )
                if (
                    condition_measurement == source_measurement
                    and str(condition_audit.get("evaluation_scope", "")).strip().casefold()
                    != str(source_audit.get("evaluation_scope", "")).strip().casefold()
                ):
                    issues.append(
                        {
                            "field_path": "design.paired_comparison_audits",
                            "message": "linked fitted-condition comparisons use different evaluation scopes",
                            "suggestion": "所有复用该拟合条件的比较使用完全相同、只描述固定评价观测的 evaluation_scope。",
                        }
                    )
    raw_criteria = design_payload.get("criteria", [])
    criteria = raw_criteria if isinstance(raw_criteria, list) else []
    all_criterion_measurement_refs = {
        ref
        for criterion in criteria
        if isinstance(criterion, dict)
        for ref in criterion.get("measurement_refs", [])
        if isinstance(ref, str)
    }
    referenced_measurements: set[str] = set()
    referenced_results: set[str] = set()
    for index, criterion in enumerate(criteria):
        if not isinstance(criterion, dict):
            continue
        statement = criterion.get("statement")
        refs = criterion.get("measurement_refs")
        result_refs = criterion.get("result_refs")
        endpoint_refs = criterion.get("endpoint_refs")
        if isinstance(refs, list):
            referenced_measurements.update(
                ref for ref in refs if isinstance(ref, str)
            )
        if isinstance(result_refs, list):
            referenced_results.update(
                ref for ref in result_refs if isinstance(ref, str)
            )
        if (
            isinstance(refs, list)
            and isinstance(result_refs, list)
            and isinstance(endpoint_refs, list)
            and not refs
            and not result_refs
            and not endpoint_refs
        ):
            issues.append(
                {
                    "field_path": f"design.criteria[{index}]",
                    "message": "criterion must cite at least one planned measurement, typed result, or endpoint",
                    "suggestion": "把判据连接到本次真正产生的测量、有类型结果或科研端点；仅描述文件存在或代码完成不能作为科研判据。",
                }
            )
        basis_kind = criterion.get("basis_kind")
        basis_text = criterion.get("basis_text")
        source_refs = criterion.get("source_refs")
        if (
            isinstance(statement, str)
            and HARD_NUMERIC_CUTOFF.search(statement) is not None
        ):
            if basis_kind == "qualitative_no_fixed_threshold":
                issues.append(
                    {
                        "field_path": f"design.criteria[{index}].statement",
                        "message": "criterion contains a fixed numeric cutoff without a grounded basis",
                        "suggestion": "删除硬阈值并改用方向或幅度描述；若阈值确有依据，补充来源和可复算口径。",
                    }
                )
            elif basis_kind == "method_standard":
                issues.append(
                    {
                        "field_path": f"design.criteria[{index}].basis_kind",
                        "message": "a method name cannot justify a fixed numeric decision threshold",
                        "suggestion": "删除无来源阈值并采用方向性判据；只有用户要求、已提供资料或可复算数据明确支持时才能保留。",
                    }
                )
            elif (
                basis_kind in {"located_source", "data_derived"}
                and (
                    not isinstance(source_refs, list)
                    or not any(isinstance(ref, str) and ref for ref in source_refs)
                )
            ):
                issues.append(
                    {
                        "field_path": f"design.criteria[{index}].source_refs",
                        "message": "numeric cutoff lacks a traceable supplied source",
                        "suggestion": "用 source_refs 指向阈值来源或推导所用数据；若没有可追溯来源，删除该阈值。",
                    }
                )
            elif basis_kind == "data_derived" and isinstance(basis_text, str) and re.search(
                r"(?:近似|大约|约为|粗略|approximately|roughly)",
                basis_text,
                re.IGNORECASE,
            ):
                issues.append(
                    {
                        "field_path": f"design.criteria[{index}].basis_text",
                        "message": "a fixed data-derived cutoff needs a reproducible derivation, not an approximate rationale",
                        "suggestion": "写出可由已提供数据复算的确定推导口径；否则删除该阈值，直接报告方向、两侧估计量与差值。",
                    }
                )
        if (
            not isinstance(statement, str)
            or not isinstance(refs, list)
            or not all(isinstance(ref, str) for ref in refs)
        ):
            continue
        condition_refs, delta_refs = _sensitivity_criterion_roles(
            refs,
            measurement_plan,
        )
        condition_refs, delta_refs = _linked_sensitivity_roles(
            condition_refs,
            delta_refs,
            all_criterion_measurement_refs,
            [
                audit
                for audit in audits
                if isinstance(audit, dict)
                and all(
                    field in audit
                    for field in (
                        "baseline_measurement",
                        "candidate_measurement",
                        "delta_measurement",
                    )
                )
            ],
        )
        criterion_semantics = " ".join(
            (
                str(criterion.get("id", "")),
                statement,
                str(basis_text or ""),
            )
        )
        if (
            SENSITIVITY_CONTEXT.search(criterion_semantics)
            and (len(condition_refs) < 2 or not delta_refs)
        ):
            issues.append(
                {
                    "field_path": f"design.criteria[{index}].measurement_refs",
                    "message": "sensitivity criterion must cite both condition estimates and their difference",
                    "suggestion": "敏感性判据同时引用条件 A、条件 B 和二者差值，并保持同一单位与统计口径。",
                }
            )
        elif SENSITIVITY_CONTEXT.search(criterion_semantics) and not any(
            isinstance(audit, dict)
            and audit.get("comparison_kind") == "candidate_vs_candidate"
            and {
                audit.get("baseline_measurement"),
                audit.get("candidate_measurement"),
            }.issubset(set(condition_refs))
            for audit in audits
        ):
            issues.append(
                {
                    "field_path": "design.paired_comparison_audits",
                    "message": "fitted-condition sensitivity lacks a same-row candidate comparison",
                    "suggestion": "为保留与排除两种拟合条件声明 candidate_vs_candidate 成对比较，并在同一批评价观测上核对两侧估计量。",
                }
            )
        elif (
            CONTRAST_PROMISE.search(statement)
            and len(refs) >= 2
            and not delta_refs
        ):
            issues.append(
                {
                    "field_path": f"design.criteria[{index}].measurement_refs",
                    "message": "criterion promises a difference but cites no difference measurement",
                    "suggestion": "声明并引用条件差值测量；若不计算差值，则删去判据中的差值承诺。",
                }
            )
    orphan_measurements = sorted(
        name for name in measurement_plan if name not in referenced_measurements
    )
    if orphan_measurements:
        issues.append(
            {
                "field_path": "design.measurement_plan",
                "message": (
                    "planned measurements are not used by any scientific criterion: "
                    + ", ".join(orphan_measurements)
                ),
                "suggestion": "删除无关或重复指标，或把确实用于判断的指标连接到相应判据；不要为了保留指标而新增无关分析。",
            }
        )
    audit_measurements = {
        str(audit.get(field))
        for audit in audits
        if isinstance(audit, dict)
        for field in (
            "baseline_measurement",
            "candidate_measurement",
            "delta_measurement",
        )
        if audit.get(field) is not None
    }
    unaudited_pairs = sorted(
        _paired_measurements_requiring_audit(
            referenced_measurements,
            measurement_plan,
        )
        - audit_measurements
    )
    if unaudited_pairs:
        issues.append(
            {
                "field_path": "design.paired_comparison_audits",
                "message": (
                    "paired calibration measurements lack row-level recomputation: "
                    + ", ".join(unaudited_pairs)
                ),
                "suggestion": "若质量标记只改变拟合样本且评价观测不变，删除重复的 clean/raw 未校准指标并复用共享基准；只有评价观测确实不同，才为各自原始值、校正值和差值补充成对证据。",
            }
        )

    raw_result_plan = design_payload.get("result_plan", [])
    result_plan = raw_result_plan if isinstance(raw_result_plan, list) else []
    orphan_numeric_results: list[str] = []
    for index, result in enumerate(result_plan):
        if (
            isinstance(result, dict)
            and result.get("value_kind") in {"number", "count"}
            and result.get("role") in {"primary", "secondary"}
        ):
            issues.append(
                {
                    "field_path": f"design.result_plan[{index}]",
                    "message": "answer-bearing numeric results must use measurement_plan",
                    "suggestion": "把该数值移入 measurement_plan 并由科研判据引用；result_plan 只保留定性、离散或诊断信息。",
                }
            )
        if isinstance(result, dict) and CODE_LIKE_READER_IDENTIFIER.search(
            " ".join(
                (
                    str(result.get("display_name", "")),
                    str(result.get("scientific_meaning", "")),
                )
            )
        ):
            issues.append(
                {
                    "field_path": f"design.result_plan[{index}]",
                    "message": "reader-facing typed-result text exposes a raw field or category name",
                    "suggestion": "把原始列名或类别代码改写成自然语言科研名称。",
                }
            )
        result_reader_text = " ".join(
            (
                str(result.get("display_name", "")),
                str(result.get("scientific_meaning", "")),
            )
        ) if isinstance(result, dict) else ""
        if result_reader_text and AMBIGUOUS_FLAGGED_RETENTION_LANGUAGE.search(
            result_reader_text
        ):
            issues.append(
                {
                    "field_path": f"design.result_plan[{index}]",
                    "message": "quality-flag inclusion language is ambiguous",
                    "suggestion": "用“包含被标记观测”或“排除被标记观测”明确两种拟合条件。",
                }
            )
        if (
            isinstance(result, dict)
            and result.get("value_kind") in {"number", "count"}
            and isinstance(result.get("id"), str)
            and result["id"] not in referenced_results
        ):
            orphan_numeric_results.append(result["id"])
    fit_quality_refs = {
        str(row.get("name"))
        for row in measurement_rows
        if isinstance(row, dict)
        and R_SQUARED_PLAN.search(
            " ".join(
                str(row.get(field, ""))
                for field in ("name", "display_name", "scientific_meaning")
            )
        )
    } | {
        str(row.get("id"))
        for row in result_plan
        if isinstance(row, dict)
        and R_SQUARED_PLAN.search(
            " ".join(
                str(row.get(field, ""))
                for field in ("id", "display_name", "scientific_meaning")
            )
        )
    }
    request_text = str((request_payload or {}).get("task", ""))
    if fit_quality_refs and R_SQUARED_PLAN.search(request_text) is None:
        located_refs = {
            str(ref)
            for criterion in criteria
            if isinstance(criterion, dict)
            and criterion.get("basis_kind") == "located_source"
            and criterion.get("source_refs")
            for ref in [
                *criterion.get("measurement_refs", []),
                *criterion.get("result_refs", []),
            ]
        }
        unrequested_fit_quality = sorted(fit_quality_refs - located_refs)
        if unrequested_fit_quality:
            issues.append(
                {
                    "field_path": "design.result_plan",
                    "message": "design adds an unrequested coefficient-of-determination route: "
                    + ", ".join(unrequested_fit_quality),
                    "suggestion": "删除未被研究问题或已定位来源要求的决定系数、拟合优度及其判据；保留直接回答问题的误差、参数和条件差值。",
                }
            )
    if orphan_numeric_results:
        issues.append(
            {
                "field_path": "design.result_plan",
                "message": (
                    "numeric diagnostic results are not used by any scientific criterion: "
                    + ", ".join(sorted(orphan_numeric_results))
                ),
                "suggestion": "删除未参与研究判断的数值或计数诊断项；不要为保留它们新增无关判据。若确实回答研究问题，移入 measurement_plan 并由相应判据引用。",
            }
        )

    raw_stages = design_payload.get("experiment_stages", [])
    stages = raw_stages if isinstance(raw_stages, list) else []
    for index, stage in enumerate(stages):
        if (
            isinstance(stage, dict)
            and isinstance(stage.get("method_outline"), str)
            and CODE_LIKE_READER_IDENTIFIER.search(stage["method_outline"])
        ):
            issues.append(
                {
                    "field_path": f"design.experiment_stages[{index}].method_outline",
                    "message": "reader-facing method text exposes a raw field or category name",
                    "suggestion": "方法说明使用自然语言科研名称；原始字段仅在实验代码中使用。",
                }
            )
        if isinstance(stage, dict) and AMBIGUOUS_FLAGGED_RETENTION_LANGUAGE.search(
            " ".join(
                (
                    str(stage.get("objective", "")),
                    str(stage.get("method_outline", "")),
                )
            )
        ):
            issues.append(
                {
                    "field_path": f"design.experiment_stages[{index}].method_outline",
                    "message": "quality-flag inclusion language is ambiguous",
                    "suggestion": "明确写出拟合时包含或排除被标记观测，并把评价范围另写为同一批固定留出观测。",
                }
            )

    raw_method_decisions = design_payload.get("method_decisions", [])
    method_decisions = (
        raw_method_decisions if isinstance(raw_method_decisions, list) else []
    )
    for index, decision in enumerate(method_decisions):
        if not isinstance(decision, dict):
            continue
        if AMBIGUOUS_FLAGGED_RETENTION_LANGUAGE.search(
            " ".join(
                str(decision.get(field, ""))
                for field in ("decision", "rationale", "claim_limit")
            )
        ):
            issues.append(
                {
                    "field_path": f"design.method_decisions[{index}]",
                    "message": "quality-flag inclusion language is ambiguous",
                    "suggestion": "明确区分包含被标记观测的拟合与排除被标记观测的拟合。",
                }
            )
        decision_text = decision.get("decision")
        source_refs = decision.get("source_refs")
        if (
            decision.get("basis_kind") in {"located_source", "data_derived"}
            and isinstance(source_refs, list)
            and not source_refs
        ):
            issues.append(
                {
                    "field_path": f"design.method_decisions[{index}].source_refs",
                    "message": "the declared method basis requires a supplied source reference",
                    "suggestion": "引用实际支持该选择的输入；若只是有界务实选择，改用 bounded_pragmatic_choice 并列出替代方案。",
                }
            )
        if (
            decision.get("basis_kind") == "method_standard"
            and isinstance(decision_text, str)
            and NUMBER_TOKEN.search(decision_text) is not None
            and isinstance(source_refs, list)
            and not source_refs
        ):
            issues.append(
                {
                    "field_path": f"design.method_decisions[{index}].basis_kind",
                    "message": "an unsourced fixed numerical method choice is not a method standard",
                    "suggestion": "若数字来自用户或数据，改用对应依据并指向来源；若只是有界务实选择，明确标为 bounded_pragmatic_choice、列出替代方案与结论边界；不必要时删除固定数值约束。",
                }
            )
        if (
            decision.get("basis_kind") == "bounded_pragmatic_choice"
            and isinstance(decision.get("alternatives"), list)
            and not decision["alternatives"]
        ):
            issues.append(
                {
                    "field_path": f"design.method_decisions[{index}].alternatives",
                    "message": "bounded pragmatic choices require at least one alternative",
                    "suggestion": "列出至少一种合理替代方案并说明当前选择的结论边界。",
                }
            )
    measurement_names = set(measurement_plan)
    result_ids = {
        str(row.get("id"))
        for row in result_plan
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    criterion_ids = {
        str(row.get("id"))
        for row in criteria
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    artifact_rows = (
        design_payload.get("artifact_plan", [])
        if isinstance(design_payload.get("artifact_plan", []), list)
        else []
    )
    artifact_ids = {
        str(row.get("id"))
        for row in artifact_rows
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    artifact_paths = {
        str(row.get("path"))
        for row in artifact_rows
        if isinstance(row, dict) and isinstance(row.get("path"), str)
    }
    stage_ids = {
        str(row.get("id"))
        for row in stages
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    endpoint_ids = {
        str(endpoint)
        for stage in stages
        if isinstance(stage, dict) and isinstance(stage.get("endpoint_ids"), list)
        for endpoint in stage["endpoint_ids"]
        if isinstance(endpoint, str)
    }

    reserved_artifact_names = {
        "audit.md",
        "design.json",
        "entry_result.json",
        "record.json",
        "report.md",
        "request.json",
        "response.json",
        "result.json",
        "state.json",
    }
    for index, artifact in enumerate(artifact_rows):
        if not isinstance(artifact, dict):
            continue
        path = artifact.get("path")
        if (
            isinstance(path, str)
            and path.rstrip("/").rsplit("/", 1)[-1].casefold()
            in reserved_artifact_names
        ):
            issues.append(
                {
                    "field_path": f"design.artifact_plan[{index}].path",
                    "message": "experiment stages cannot create a reserved runtime or final-report artifact",
                    "suggestion": "删除该产物及沙箱内的报告步骤；实验阶段只生成数据、图表或科研中间产物，正式报告由现有汇报器生成。",
                }
            )
        producer = artifact.get("producer_stage_id")
        if isinstance(producer, str) and producer not in stage_ids:
            issues.append(
                {
                    "field_path": f"design.artifact_plan[{index}].producer_stage_id",
                    "message": f"unknown producer stage: {producer}",
                    "suggestion": "将产物连接到实际存在的唯一生产阶段。",
                }
            )

    for index, criterion in enumerate(criteria):
        if not isinstance(criterion, dict):
            continue
        for field, known, label in (
            ("measurement_refs", measurement_names, "measurement"),
            ("result_refs", result_ids, "typed result"),
            ("endpoint_refs", endpoint_ids, "endpoint"),
            ("artifact_refs", artifact_paths, "artifact"),
        ):
            refs = criterion.get(field)
            if not isinstance(refs, list):
                continue
            unknown = sorted(
                str(ref)
                for ref in refs
                if isinstance(ref, str) and ref not in known
            )
            if unknown:
                issues.append(
                    {
                        "field_path": f"design.criteria[{index}].{field}",
                        "message": f"unknown {label} references: {', '.join(unknown)}",
                        "suggestion": "删除失效引用，或先在相应计划中声明真正需要的科研结果。",
                    }
                )

    measurement_owners: dict[str, list[str]] = {}
    result_owners: dict[str, list[str]] = {}
    endpoint_owners: dict[str, list[str]] = {}
    for index, stage in enumerate(stages):
        if not isinstance(stage, dict):
            continue
        stage_id = str(stage.get("id") or f"stage[{index}]")
        for field, known, owners, label in (
            ("measurement_refs", measurement_names, measurement_owners, "measurement"),
            ("result_refs", result_ids, result_owners, "typed result"),
            ("endpoint_ids", endpoint_ids, endpoint_owners, "endpoint"),
            ("criterion_refs", criterion_ids, None, "criterion"),
            ("consumes_artifact_ids", artifact_ids, None, "artifact"),
            ("produces_artifact_ids", artifact_ids, None, "artifact"),
        ):
            refs = stage.get(field)
            if not isinstance(refs, list):
                continue
            unknown = sorted(
                str(ref)
                for ref in refs
                if isinstance(ref, str) and ref not in known
            )
            if unknown:
                issues.append(
                    {
                        "field_path": f"design.experiment_stages[{index}].{field}",
                        "message": f"unknown {label} references: {', '.join(unknown)}",
                        "suggestion": "删除失效引用，或先声明并连接真正由该阶段产生或消费的对象。",
                    }
                )
            if owners is not None:
                for ref in refs:
                    if isinstance(ref, str):
                        owners.setdefault(ref, []).append(stage_id)
    for field, owners, label in (
        ("measurement_refs", measurement_owners, "measurement"),
        ("result_refs", result_owners, "typed result"),
        ("endpoint_ids", endpoint_owners, "endpoint"),
    ):
        for ref, owner_ids in sorted(owners.items()):
            if len(owner_ids) > 1:
                issues.append(
                    {
                        "field_path": f"design.experiment_stages[*].{field}",
                        "message": f"{label} has multiple producing stages: {ref} ({', '.join(owner_ids)})",
                        "suggestion": "每项结果只保留一个生产阶段；需要把数值传给后续阶段时，改用只读 Artifact，或者合并重复阶段。",
                    }
                )

    for index, audit in enumerate(audits):
        if not isinstance(audit, dict):
            continue
        label = f"design.paired_comparison_audits[{index}]"
        baseline_name = audit.get("baseline_measurement")
        baseline_plan = measurement_plan.get(baseline_name, {})
        baseline_reader_text = " ".join(
            (
                str(baseline_name or ""),
                str(baseline_plan.get("display_name", "")),
                str(baseline_plan.get("scientific_meaning", "")),
            )
        )
        if (
            audit.get("comparison_kind") == "candidate_vs_candidate"
            and UNCALIBRATED_BASELINE_LANGUAGE.search(baseline_reader_text)
        ):
            issues.append(
                {
                    "field_path": f"{label}.comparison_kind",
                    "message": "an uncalibrated source baseline cannot be treated as a fitted candidate",
                    "suggestion": "原始或未校准读数与拟合后预测应使用 source_baseline_vs_candidate，并把基准模型输入设为空数组、基准目标与拟合条件设为 null；candidate_vs_candidate 只用于两套拟合模型。",
                }
            )
        if (
            audit.get("comparison_kind") == "candidate_vs_candidate"
            and isinstance(audit.get("baseline_model_input_columns"), list)
            and not audit["baseline_model_input_columns"]
        ):
            issues.append(
                {
                    "field_path": f"{label}.baseline_model_input_columns",
                    "message": "candidate-versus-candidate comparison must declare the baseline model inputs",
                    "suggestion": "两种重拟合条件都要声明预测时使用的输入列；两侧均不得读取评价目标。",
                }
            )
        delta_name = audit.get("delta_measurement")
        delta_formula = audit.get("delta_formula")
        metric = audit.get("metric")
        delta_plan = measurement_plan.get(delta_name, {})
        delta_text = " ".join(
            (
                str(delta_plan.get("display_name", "")),
                str(delta_plan.get("scientific_meaning", "")),
            )
        )
        if (
            metric in {"mae", "rmse"}
            and isinstance(delta_formula, str)
            and _loss_delta_direction_conflicts(delta_text, delta_formula)
        ):
            issues.append(
                {
                    "field_path": f"design.measurement_plan[{delta_name}]",
                    "message": "difference sign interpretation contradicts the declared subtraction direction",
                    "suggestion": "按 delta_formula 核对正负号；若无需解释正负，只陈述相减顺序。",
                }
            )
    unique: dict[tuple[str, str], dict[str, str]] = {}
    for issue in issues:
        unique[(issue["field_path"], issue["message"])] = issue
    issues = list(unique.values())
    issues.sort(key=lambda row: (row["field_path"], row["message"]))
    return issues


def bind_request(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {"request", "request_input"}
    if not set(payload).issubset(allowed) or (
        ("request" in payload) == ("request_input" in payload)
    ):
        raise ServiceError("bind request accepts either request or request_input")
    request = _request_from_payload(payload)
    request_fingerprint = _request_fingerprint(request)
    input_fingerprint = fingerprint_input_references(request)["input_fingerprint"]
    run_id, root, state = create_run(
        request,
        request_fingerprint=request_fingerprint,
    )
    state["input_fingerprint"] = input_fingerprint
    save_state(root, state)
    return {
        "schema_version": "automatic-experiment-brief-v1",
        "status": "request_bound",
        "run_id": run_id,
        "request": request,
        "request_sha256": state["request_sha256"],
        "request_fingerprint": request_fingerprint,
        "input_fingerprint": input_fingerprint,
        "lineage": state["lineage"],
        "public_contracts": [
            REQUEST_VERSION,
            RESPONSE_VERSION,
            RECORD_VERSION,
            ENTRY_RESULT_VERSION,
        ],
        "response_kinds": ["experiment_ready", "clarification_required", "execution_blocked"],
        "terminal_outcomes": sorted(OUTCOMES),
        "design_contract": DESIGN_VERSION,
        "authoring_guide": _authoring_guide(),
    }


def _attempt_code_files(root: Path, attempt_id: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    attempt_root = root / "attempts" / attempt_id
    attempt = read_json(attempt_root / "attempt.json")
    verify_attempt_immutable(attempt_root, attempt)
    code_files: list[dict[str, str]] = []
    for file_row in attempt.get("files", []):
        relative = str(file_row.get("path", ""))
        if not relative.startswith("code/") or relative == "code/worker_request.json":
            continue
        path = attempt_root / Path(*relative.split("/"))
        code_files.append(
            {
                "path": relative.removeprefix("code/"),
                "content": path.read_text(encoding="utf-8"),
                "sha256": file_sha256(path),
            }
        )
    if not code_files:
        raise ServiceError("replay source has no immutable experiment code")
    return attempt, code_files


def _validate_stage_code(
    design: dict[str, Any],
    stage_id: str,
    code_files: list[dict[str, str]],
) -> None:
    stage = experiment_stage(design, stage_id)
    stage_result_ids = set(stage["result_refs"])
    validate_code_files(
        [{"path": row["path"], "content": row["content"]} for row in code_files],
        stage_execution(design, stage_id)["dependencies"],
        required_measurements=set(stage["measurement_refs"]),
        required_results=stage_result_ids,
        required_result_contracts={
            row["id"]: {
                field: row[field]
                for field in ("display_name", "value_kind", "unit", "role")
            }
            for row in design["result_plan"]
            if row["id"] in stage_result_ids
        },
        required_endpoints=set(stage["endpoint_ids"]),
        expected_artifacts=set(stage_execution(design, stage_id)["expected_artifacts"]),
        required_consumed_artifacts=set(stage["consumes_artifact_ids"]),
        primary_estimand=design["interpretation_policy"]["primary_estimand"],
    )


def _validate_replay_source(source_run_id: str) -> dict[str, Any]:
    root, state = load_state(source_run_id)
    if state.get("phase") != "report_finalized":
        raise ServiceError("replay source must be a finalized run")
    for name in (
        "request.json",
        "response.json",
        "design.json",
        "input_snapshot.json",
        "record.json",
        "report.md",
        "entry_result.json",
    ):
        if not (root / name).is_file():
            raise ServiceError(f"replay source is incomplete: {name}")
    request = validate_request(read_json(root / "request.json"))
    response = validate_response(read_json(root / "response.json"), request)
    design = validate_design(read_json(root / "design.json"), request, response)
    record = read_json(root / "record.json")
    record_payload = dict(record)
    stored_record_sha = record_payload.pop("record_sha256", None)
    if (
        not isinstance(stored_record_sha, str)
        or stored_record_sha != canonical_sha256(record_payload)
        or stored_record_sha != state.get("verified_record_sha256")
    ):
        raise ServiceError("replay source record hash is invalid")
    entry = read_json(root / "entry_result.json")
    entry_payload = dict(entry)
    stored_entry_sha = entry_payload.pop("entry_sha256", None)
    if (
        not isinstance(stored_entry_sha, str)
        or stored_entry_sha != canonical_sha256(entry_payload)
    ):
        raise ServiceError("replay source entry hash is invalid")
    if entry.get("record_sha256") != file_sha256(root / "record.json"):
        raise ServiceError("replay source record file changed after finalization")
    if (
        entry.get("report_sha256") != file_sha256(root / "report.md")
        or state.get("report_sha256") != entry.get("report_sha256")
    ):
        raise ServiceError("replay source report changed after finalization")
    if isinstance(entry.get("audit_path"), str):
        audit_path = root / entry["audit_path"]
        if (
            not audit_path.is_file()
            or entry.get("audit_sha256") != file_sha256(audit_path)
        ):
            raise ServiceError("replay source audit changed after finalization")
    for asset in entry.get("report_assets", []):
        if not isinstance(asset, dict) or not isinstance(asset.get("path"), str):
            raise ServiceError("replay source report asset metadata is invalid")
        asset_path = root / Path(*asset["path"].split("/"))
        if not asset_path.is_file() or file_sha256(asset_path) != asset.get("sha256"):
            raise ServiceError("replay source report asset changed after finalization")

    manifest = read_json(root / "input_snapshot.json")
    for input_row in manifest.get("inputs", []):
        for file_row in input_row.get("files", []):
            source = root / "inputs" / Path(*file_row["path"].split("/"))
            if (
                not source.is_file()
                or source.stat().st_size != file_row["size_bytes"]
                or file_sha256(source) != file_row["sha256"]
            ):
                raise ServiceError(
                    f"replay source input snapshot changed: {file_row['path']}"
                )
    request_fingerprint, input_fingerprint = _snapshot_fingerprints(root, request)
    stage_history = record.get("stage_history")
    if not isinstance(stage_history, list) or not stage_history:
        raise ServiceError("replay source has no verified experiment stage")
    stage_code_files: dict[str, list[dict[str, str]]] = {}
    stage_attempt_ids: dict[str, str] = {}
    for row in stage_history:
        if not isinstance(row, dict):
            raise ServiceError("replay source stage history is invalid")
        stage_id = row.get("stage_id")
        attempt_id = row.get("attempt_id")
        if not isinstance(stage_id, str) or not isinstance(attempt_id, str):
            raise ServiceError("replay source stage history lacks an attempt identity")
        if stage_id in stage_code_files:
            raise ServiceError("replay source contains more than one terminal attempt for a stage")
        attempt, code_files = _attempt_code_files(root, attempt_id)
        if (
            attempt.get("design_sha256") != canonical_sha256(design)
            or attempt.get("stage_id") != stage_id
        ):
            raise ServiceError("replay source attempt design or stage hash is invalid")
        _validate_stage_code(design, stage_id, code_files)
        stage_code_files[stage_id] = code_files
        stage_attempt_ids[stage_id] = attempt_id
    first_stage_id = design["experiment_stages"][0]["id"]
    if first_stage_id not in stage_code_files:
        raise ServiceError("replay source does not contain code for its first stage")
    source_environment = (record.get("execution_facts") or {}).get(
        "runtime_environment"
    )
    if not isinstance(source_environment, dict):
        raise ServiceError("replay source lacks a verified runtime environment")
    current_environment = runtime_environment_snapshot()
    if not current_environment.get("ready"):
        raise ServiceError("current locked runtime environment is not ready for replay")
    source_environment_sha = canonical_sha256(source_environment)
    current_environment_sha = canonical_sha256(current_environment)
    if source_environment_sha != current_environment_sha:
        raise ServiceError(
            "current runtime environment differs from the replay source; exact replay is blocked"
        )
    return {
        "root": root,
        "state": state,
        "request": request,
        "response": response,
        "design": design,
        "manifest": manifest,
        "record": record,
        "stage_attempt_ids": stage_attempt_ids,
        "stage_code_files": stage_code_files,
        "request_fingerprint": request_fingerprint,
        "input_fingerprint": input_fingerprint,
        "source_environment_sha256": source_environment_sha,
        "current_environment_sha256": current_environment_sha,
    }


def prepare_replay(source_run_id: str) -> dict[str, Any]:
    """Create a new run from a fully verified immutable source and prepare its code."""

    source = _validate_replay_source(source_run_id)
    source_request = source["request"]
    replay_request = dict(source_request)
    replay_request["replay_of"] = source_run_id
    source_parameters_sha = canonical_sha256(
        {
            "resource_budget": source_request["resource_budget"],
            "seed_policy": source_request["seed_policy"],
            "experiment_stages": source["design"]["experiment_stages"],
        }
    )
    lineage = {
        "mode": "exact_replay",
        "source_run_id": source_run_id,
        "matching_run_ids": [source_run_id],
        "source_request_sha256": canonical_sha256(source_request),
        "source_request_fingerprint": source["request_fingerprint"],
        "source_input_fingerprint": source["input_fingerprint"],
        "source_input_snapshot_sha256": canonical_sha256(source["manifest"]),
        "source_design_sha256": canonical_sha256(source["design"]),
        "source_code_sha256": {
            stage_id: {row["path"]: row["sha256"] for row in code_files}
            for stage_id, code_files in source["stage_code_files"].items()
        },
        "source_parameters_sha256": source_parameters_sha,
        "source_environment_sha256": source["source_environment_sha256"],
        "current_environment_sha256": source["current_environment_sha256"],
        "environment_match": True,
        "numeric_cross_run_comparison_permitted": True,
        "comparison_reason": "输入快照、设计、实验代码、参数、种子与锁定环境均一致。",
    }
    run_id, root, state = create_run(
        replay_request,
        request_fingerprint=source["request_fingerprint"],
        lineage=lineage,
    )
    destination_inputs = root / "inputs"
    for input_row in source["manifest"].get("inputs", []):
        for file_row in input_row.get("files", []):
            source_path = source["root"] / "inputs" / Path(
                *file_row["path"].split("/")
            )
            target_path = destination_inputs / Path(*file_row["path"].split("/"))
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, target_path)
            if file_sha256(target_path) != file_row["sha256"]:
                raise ServiceError("replay input copy hash mismatch")
    atomic_write_json(root / "input_snapshot.json", source["manifest"])
    state["input_manifest_path"] = "input_snapshot.json"
    state["input_fingerprint"] = source["input_fingerprint"]
    checkpoint(
        root,
        state,
        "inputs_snapshotted",
        {
            "input_fingerprint": source["input_fingerprint"],
            "replay_source_run_id": source_run_id,
        },
    )
    atomic_write_json(root / "response.json", source["response"])
    atomic_write_json(root / "design.json", source["design"])
    state["response_path"] = "response.json"
    state["design_path"] = "design.json"
    first_stage_id = source["design"]["experiment_stages"][0]["id"]
    state["current_stage_id"] = first_stage_id
    state["stage_attempt_counts"] = {
        stage["id"]: 0 for stage in source["design"]["experiment_stages"]
    }
    checkpoint(
        root,
        state,
        "design_validated",
        {
            "design_sha256": canonical_sha256(source["design"]),
            "replay_source_run_id": source_run_id,
        },
    )
    prepared = prepare(
        run_id,
        [
            {"path": row["path"], "content": row["content"]}
            for row in source["stage_code_files"][first_stage_id]
        ],
        None,
        f"真实重放源运行 {source_run_id} 的已核验不可变实验代码。",
    )
    replay_code_sha = {
        row["path"].removeprefix("code/"): row["sha256"]
        for row in prepared["attempt"]["files"]
        if row["path"].startswith("code/")
        and row["path"] != "code/worker_request.json"
    }
    if replay_code_sha != lineage["source_code_sha256"][first_stage_id]:
        raise ServiceError("prepared replay code does not match the source code hashes")
    return {
        "schema_version": "automatic-experiment-replay-prepared-v1",
        "status": "replay_prepared",
        "run_id": run_id,
        "attempt_id": prepared["attempt_id"],
        "source_run_id": source_run_id,
        "lineage": lineage,
        "next_action": "execute_prepared_attempt",
    }


def _prepare_next_exact_replay_stage(
    run_id: str,
    state: dict[str, Any],
    stage_id: str,
) -> dict[str, Any] | None:
    lineage = state.get("lineage")
    if not isinstance(lineage, dict) or lineage.get("mode") != "exact_replay":
        return None
    source_run_id = lineage.get("source_run_id")
    if not isinstance(source_run_id, str):
        raise ServiceError("exact replay lineage has no source run")
    source = _validate_replay_source(source_run_id)
    code_files = source["stage_code_files"].get(stage_id)
    expected_hashes = lineage.get("source_code_sha256", {}).get(stage_id)
    if not isinstance(code_files, list) or not isinstance(expected_hashes, dict):
        raise ServiceError(
            "真实重放到达了源运行未执行的条件阶段；无法把它伪装成精确重放。"
        )
    observed_hashes = {row["path"]: row["sha256"] for row in code_files}
    if observed_hashes != expected_hashes:
        raise ServiceError("exact replay source code hashes changed before the next stage")
    return prepare(
        run_id,
        [{"path": row["path"], "content": row["content"]} for row in code_files],
        None,
        f"真实重放源运行 {source_run_id} 的阶段 {stage_id} 已核验不可变代码。",
    )


def inspect_inputs(run_id: str) -> dict[str, Any]:
    root, state = load_state(run_id)
    if state["phase"] != "request_bound":
        if (root / "input_snapshot.json").is_file():
            manifest = read_json(root / "input_snapshot.json")
            return {
                "status": "already_snapshotted",
                "run_id": run_id,
                "input_snapshot": manifest,
                "input_previews": snapshot_input_previews(root, manifest),
            }
        raise ServiceError("inputs can only be snapshotted after request binding")
    request = _load_request(root)
    try:
        manifest = snapshot_inputs(root, request)
    except PathPolicyError as exc:
        state["outcome"] = "boundary_blocked"
        state["last_error"] = f"input_policy: {exc}"
        save_state(root, state)
        return {
            "schema_version": "automatic-experiment-input-inspection-v1",
            "status": "terminal",
            "run_id": run_id,
            "outcome": "boundary_blocked",
            "blockers": [str(exc)],
        }
    observed_input_fingerprint = fingerprint_input_snapshot(
        request,
        manifest,
    )["input_fingerprint"]
    expected_input_fingerprint = state.get("input_fingerprint")
    if (
        isinstance(expected_input_fingerprint, str)
        and expected_input_fingerprint != observed_input_fingerprint
    ):
        reason = (
            "输入在重复请求查询/绑定与不可变快照之间发生变化；请重新提交任务，"
            "避免把不同输入误判为同一实验。"
        )
        state["outcome"] = "boundary_blocked"
        state["last_error"] = reason
        save_state(root, state)
        return {
            "schema_version": "automatic-experiment-input-inspection-v1",
            "status": "terminal",
            "run_id": run_id,
            "outcome": "boundary_blocked",
            "blockers": [reason],
        }
    state["input_fingerprint"] = observed_input_fingerprint
    state["input_manifest_path"] = "input_snapshot.json"
    checkpoint(
        root,
        state,
        "inputs_snapshotted",
        {
            "missing_required_ids": manifest["missing_required_ids"],
            "input_fingerprint": observed_input_fingerprint,
        },
    )
    return {
        "schema_version": "automatic-experiment-input-inspection-v1",
        "status": "inputs_snapshotted",
        "run_id": run_id,
        "input_snapshot": manifest,
        "input_previews": snapshot_input_previews(root, manifest),
    }


def _kb_grounding_warnings(design: dict[str, Any]) -> list[dict[str, Any]]:
    """知识库引用门禁（方案 §5.4 #3，warning 模式）。

    design 的 research_frame.literature_basis / design_summary 中至少引用一个
    真实存在的 kb_ 条目 id，或显式声明 knowledge_gap / 知识缺口；不满足则列入
    ``kb_grounding_missing``。知识库不可用时静默降级为空列表。
    """

    try:
        from knowledge_base import service as kb_service
        from knowledge_base.store import KnowledgeStore
    except Exception:  # noqa: BLE001
        return []
    try:
        frame = design.get("research_frame") or {}
        corpus = " ".join(
            str(part)
            for part in (
                frame.get("literature_basis", ""),
                design.get("design_summary", ""),
            )
        )
        evidence_ids = re.findall(r"kb_[A-Za-z0-9][A-Za-z0-9_-]*", corpus)
        gap = re.search(r"knowledge_gap|知识缺口", corpus, re.IGNORECASE) is not None
        store = KnowledgeStore()
        try:
            return kb_service.grounding_warnings(
                store,
                [
                    {
                        "id": "design",
                        "evidence_ids": evidence_ids,
                        "knowledge_gap": gap,
                    }
                ],
            )
        finally:
            store.close()
    except Exception:  # noqa: BLE001
        return []


def validate_and_store_design(
    run_id: str,
    response_payload: dict[str, Any],
    design_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    root, state = load_state(run_id)
    if state["phase"] not in {"request_bound", "inputs_snapshotted"}:
        raise ServiceError("design can only be checked before attempts are prepared")
    request = _load_request(root)
    response_candidate = dict(response_payload)
    response_candidate["schema_version"] = RESPONSE_VERSION
    response_candidate["task_name"] = request["task_name"]
    response_candidate["task"] = request["task"]
    if not str(response_candidate.get("normalized_task") or "").strip():
        response_candidate["normalized_task"] = request["task"]
    try:
        response = validate_response(response_candidate, request)
    except ContractError as exc:
        return {
            "schema_version": "automatic-experiment-design-check-v1",
            "status": "design_invalid",
            "run_id": run_id,
            "issues": [
                {
                    "field_path": exc.field_path or "response",
                    "message": str(exc),
                    "suggestion": (
                        exc.suggestion
                        or "按当前任务补齐响应中的科学判断；固定版本号、任务名和原始请求由宿主补齐。"
                    ),
                }
            ],
            "message": "实验响应仍有一项需要修正；请按说明调整后重新提交。",
            "authoring_guide": _authoring_guide()["response"],
        }
    snapshot = (
        read_json(root / "input_snapshot.json")
        if (root / "input_snapshot.json").is_file()
        else {
            "schema_version": "automatic-experiment-input-snapshot-v1",
            "created_at": None,
            "total_bytes": 0,
            "missing_required_ids": [row["id"] for row in request["input_refs"] if row["required"]],
            "inputs": [],
        }
    )
    atomic_write_json(root / "response.json", response)
    state["response_path"] = "response.json"
    if (
        response["response_kind"] == "experiment_ready"
        and not request["input_refs"]
        and _requires_declared_input(request)
    ):
        reason = (
            "该实验明确依赖现有数据或文件，但绑定请求没有 input_refs。"
            "请把输入放到 inputs/ 或引用 runs/<run_id>/public/，然后重新绑定；"
            "系统不会再用 0 字节输入快照执行数据实验。"
        )
        state["outcome"] = "input_missing"
        state["last_error"] = reason
        save_state(root, state)
        return {
            "schema_version": "automatic-experiment-design-check-v1",
            "status": "terminal",
            "run_id": run_id,
            "outcome": "input_missing",
            "blockers": [reason],
        }
    if request["resource_budget"]["gpu_count"] != 0 or request["resource_budget"]["gpu_memory_mb"] != 0:
        reason = (
            "GPU execution is boundary-blocked in V1 because per-run device and "
            "memory isolation is not proven."
        )
        state["outcome"] = "boundary_blocked"
        state["last_error"] = reason
        save_state(root, state)
        return {
            "status": "terminal",
            "run_id": run_id,
            "outcome": "boundary_blocked",
            "blockers": [reason],
        }
    if state.get("outcome") == "boundary_blocked" and isinstance(state.get("last_error"), str):
        reason = state["last_error"].removeprefix("input_policy: ").strip()
        save_state(root, state)
        return {
            "status": "terminal",
            "run_id": run_id,
            "outcome": "boundary_blocked",
            "blockers": [reason],
        }
    if response["response_kind"] == "clarification_required":
        state["outcome"] = "clarification_required"
        state["last_error"] = "; ".join(response["clarifications"])
        save_state(root, state)
        return {
            "status": "terminal",
            "run_id": run_id,
            "outcome": "clarification_required",
            "questions": response["clarifications"],
        }
    if response["response_kind"] == "execution_blocked":
        if snapshot["missing_required_ids"]:
            outcome = "input_missing"
        elif _blockers_describe_missing_input(response["blockers"]):
            if _snapshot_has_verified_files(snapshot):
                raise ServiceError(
                    "input-missing blocker contradicts the verified input snapshot"
                )
            outcome = "input_missing"
        elif response["method_fit"] == "incompatible":
            outcome = "method_mismatch"
        else:
            outcome = "boundary_blocked"
        state["outcome"] = outcome
        state["last_error"] = "; ".join(response["blockers"])
        save_state(root, state)
        return {
            "status": "terminal",
            "run_id": run_id,
            "outcome": outcome,
            "blockers": response["blockers"],
        }
    if snapshot["missing_required_ids"]:
        raise ServiceError(
            f"experiment_ready is invalid while required inputs are missing: {snapshot['missing_required_ids']}"
        )
    if design_payload is None:
        raise ServiceError("experiment_ready requires a design object")
    design_candidate = dict(design_payload)
    design_candidate["schema_version"] = DESIGN_VERSION
    design_candidate["task_name"] = request["task_name"]
    design_candidate["normalized_task"] = response["normalized_task"]
    shape_issues = _design_schema_issues(design_candidate, request)
    if shape_issues:
        return {
            "schema_version": "automatic-experiment-design-check-v1",
            "status": "design_invalid",
            "run_id": run_id,
            "issues": shape_issues,
            "message": "实验设计有多项结构问题；请按 issues 一次性修正后重新提交。",
            "authoring_guide": _design_repair_guide(shape_issues),
        }
    try:
        design = validate_design(design_candidate, request, response)
    except ContractError as exc:
        semantic_issues = [
            {
                "field_path": exc.field_path or "design",
                "message": str(exc),
                "suggestion": (
                    exc.suggestion
                    or "按当前问题的科研需要修正该设计关系后重新提交。"
                ),
            }
        ]
        return {
            "schema_version": "automatic-experiment-design-check-v1",
            "status": "design_invalid",
            "run_id": run_id,
            "issues": semantic_issues,
            "message": "实验设计仍有一项科研关系需要修正；请按说明调整后重新提交。",
            "authoring_guide": _design_repair_guide(semantic_issues),
        }
    for stage in design["experiment_stages"]:
        verify_dependencies(stage["execution"]["dependencies"])
    atomic_write_json(root / "design.json", design)
    state["design_path"] = "design.json"
    state["current_stage_id"] = design["experiment_stages"][0]["id"]
    state["stage_attempt_counts"] = {
        stage["id"]: 0 for stage in design["experiment_stages"]
    }
    checkpoint(
        root,
        state,
        "design_validated",
        {"design_sha256": canonical_sha256(design)},
    )
    return {
        "schema_version": "automatic-experiment-design-check-v1",
        "status": "design_validated",
        "run_id": run_id,
        "design_sha256": canonical_sha256(design),
        "warnings": {"kb_grounding_missing": _kb_grounding_warnings(design)},
        "remaining_attempts": state["remaining_attempts"],
        "current_stage_id": state["current_stage_id"],
        "remaining_run_seconds": _remaining_run_seconds(state, request),
        "current_stage": experiment_stage(design, state["current_stage_id"]),
        "required_worker_outputs": _stage_worker_output_guide(
            design,
            state["current_stage_id"],
        ),
        "stage_authoring_guide": {
            "files": [{"path": "experiment.py", "content": "<complete Python source>"}],
            "entrypoint": "run_experiment(context)",
            "input_paths": {
                "single_file": (
                    "context['input_path_by_id'][input_id] is already the exact "
                    "file Path; read it directly and never append the filename"
                ),
                "multiple_files": (
                    "context['input_files'][input_id] is the verified list of "
                    "file Paths"
                ),
            },
            "prior_artifact_paths": "context['artifact_path_by_id'] (read only)",
            "output_path": (
                "Use context['output_dir'] / relative_name; both are pathlib.Path "
                "operations. Do not import os or stringify trusted paths."
            ),
            "paired_evidence_precision": (
                "Do not round row-level targets or predictions before writing paired "
                "evidence; preserve full precision or at least 8 significant digits. "
                "The report renderer handles display rounding."
            ),
            "static_worker_shape": (
                "Declare measurements, result_items, artifacts, and endpoint_results "
                "as explicit lists of dictionary literals, not comprehensions or "
                "runtime-built contract structures."
            ),
            "worker_result": _authoring_guide()["worker_result"],
        },
    }


def prepare(
    run_id: str,
    files: object,
    parent_attempt: str | None,
    change_reason: str,
) -> dict[str, Any]:
    root, state = load_state(run_id)
    if state["phase"] not in {
        "design_validated",
        "stage_transitioned",
        "verification_finished",
    }:
        raise ServiceError("attempts require a validated design or a verified technical failure")
    if state["phase"] == "verification_finished" and state["outcome"] != "technical_failure":
        raise ServiceError("only technical_failure permits a repair attempt")
    request = _load_request(root)
    # A design validated in an earlier (design-phase) session carries a wall budget
    # measured from bind time; resuming that frozen design for execution must start a
    # fresh execution budget, otherwise the design-phase clock makes execution
    # impossible. Reset once, at the first attempt of a validated design with no
    # prior attempts. Attempt-count limits are untouched (no attempts have run yet).
    if (
        state["phase"] == "design_validated"
        and int(state.get("attempt_count", 0)) == 0
        and not state.get("execution_budget_reset_at")
    ):
        state["created_at"] = utc_now()
        state["execution_budget_reset_at"] = state["created_at"]
        save_state(root, state)
    _require_run_budget(state, request)
    design = read_json(root / "design.json")
    stage_id = state.get("current_stage_id")
    if not isinstance(stage_id, str):
        raise ServiceError("the validated run has no active experiment stage")
    stage_attempt_count = int(state["stage_attempt_counts"].get(stage_id, 0))
    if stage_attempt_count >= request["resource_budget"]["max_attempts"]:
        raise ServiceError(
            "当前阶段的技术修复次数已达到上限；请保留现有结果并进入该阶段的预算终态。"
        )
    if parent_attempt is not None:
        parent_metadata = read_json(
            root / "attempts" / parent_attempt / "attempt.json"
        )
        if parent_metadata.get("stage_id") != stage_id:
            raise ServiceError("a repair parent must belong to the current stage")
    attempt_id, metadata = prepare_attempt(
        root,
        state,
        request,
        design,
        files,
        stage_id=stage_id,
        parent_attempt=parent_attempt,
        change_reason=change_reason,
    )
    state["current_attempt"] = attempt_id
    state["attempt_count"] += 1
    state["remaining_attempts"] -= 1
    state["stage_attempt_counts"][stage_id] = stage_attempt_count + 1
    state["budget_usage"]["attempts_used"] = state["attempt_count"]
    if stage_attempt_count == 0:
        state["budget_usage"]["stages_started"] += 1
    state["outcome"] = None
    state["last_error"] = None
    checkpoint(
        root,
        state,
        "attempt_prepared",
        {"attempt_id": attempt_id, "stage_id": stage_id},
    )
    return {
        "schema_version": "automatic-experiment-attempt-prepared-v1",
        "status": "attempt_prepared",
        "run_id": run_id,
        "attempt_id": attempt_id,
        "stage_id": stage_id,
        "remaining_attempts": state["remaining_attempts"],
        "attempt": metadata,
    }


def execute(run_id: str, attempt_id: str) -> dict[str, Any]:
    root, state = load_state(run_id)
    if state["phase"] != "attempt_prepared" or state["current_attempt"] != attempt_id:
        raise ServiceError("only the current prepared attempt may execute")
    request = _load_request(root)
    # ``prepare`` allocates and charges the attempt before it becomes
    # immutable.  A one-attempt run therefore legitimately has zero
    # *unallocated* attempts left here; execution must only enforce the time
    # budget for that already-allocated current attempt.
    remaining = _require_run_budget(
        state,
        request,
        require_unallocated_attempt=False,
    )
    bounded_request = dict(request)
    bounded_request["resource_budget"] = dict(request["resource_budget"])
    bounded_request["resource_budget"]["wall_seconds"] = min(
        request["resource_budget"]["wall_seconds"],
        remaining,
    )
    checkpoint(root, state, "execution_started", {"attempt_id": attempt_id})
    try:
        facts = execute_in_sandbox(root, state, bounded_request, attempt_id)
    except Exception as exc:
        state["last_error"] = str(exc)
        state["outcome"] = "technical_failure"
        save_state(root, state)
        raise
    checkpoint(
        root,
        state,
        "execution_finished",
        {"attempt_id": attempt_id, "stop_reason": facts["stop_reason"]},
    )
    state["budget_usage"]["total_wall_seconds"] = round(
        float(state["budget_usage"].get("total_wall_seconds", 0.0))
        + float(facts["wall_seconds"]),
        6,
    )
    save_state(root, state)
    return {
        "schema_version": "automatic-experiment-execution-result-v1",
        "status": "execution_finished",
        "run_id": run_id,
        "attempt_id": attempt_id,
        "stage_id": state["current_stage_id"],
        "execution_facts": facts,
        "diagnostic": {
            "stderr_excerpt": _diagnostic_excerpt(root, facts["stderr"]["path"]),
            "output_inventory_error": facts["output_inventory_error"],
        },
    }


def _freeze_stage_artifacts(
    root: Path,
    design: dict[str, Any],
    stage_id: str,
    attempt_id: str,
    record: dict[str, Any],
) -> list[dict[str, Any]]:
    if record.get("outcome") not in {
        "completed_interpretable",
        "partial_result",
        "scientific_null",
        "high_uncertainty",
    } or not isinstance(record.get("worker_result"), dict):
        return []
    stage = experiment_stage(design, stage_id)
    artifact_by_id = {row["id"]: row for row in design["artifact_plan"]}
    verified_by_suffix = {
        str(row["path"]).split(f"/stages/{stage_id}/", 1)[-1]: row
        for row in record.get("public_artifacts", [])
        if f"/stages/{stage_id}/" in str(row.get("path", ""))
    }
    rows: list[dict[str, Any]] = []
    for artifact_id in stage["produces_artifact_ids"]:
        planned = artifact_by_id[artifact_id]
        relative = planned["path"]
        verified = verified_by_suffix.get(relative)
        source = root / "attempts" / attempt_id / "output" / Path(
            *relative.split("/")
        )
        if verified is None or not source.is_file():
            raise ServiceError(
                f"verified stage artifact is unavailable for read-only handoff: {artifact_id}"
            )
        if file_sha256(source) != verified["sha256"]:
            raise ServiceError(
                f"verified stage artifact changed before handoff: {artifact_id}"
            )
        target = root / "stage_artifacts" / Path(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ServiceError(
                f"stage artifact handoff would overwrite an existing artifact: {artifact_id}"
            )
        shutil.copyfile(source, target)
        rows.append(
            {
                "artifact_id": artifact_id,
                "source_stage_id": stage_id,
                "source_attempt_id": attempt_id,
                "path": target.relative_to(root).as_posix(),
                "kind": planned["kind"],
                "size_bytes": target.stat().st_size,
                "sha256": file_sha256(target),
                "read_only_handoff": True,
            }
        )
    return rows


def _aggregate_stage_record(
    root: Path,
    state: dict[str, Any],
    request: dict[str, Any],
    response: dict[str, Any],
    design: dict[str, Any],
    final_record: dict[str, Any],
    terminal_outcome: str,
) -> dict[str, Any]:
    stage_records: list[dict[str, Any]] = []
    for row in state["stage_history"]:
        path = root / Path(*row["record_path"].split("/"))
        if path.is_file():
            stage_records.append(read_json(path))
    public_artifacts = [
        artifact
        for record in stage_records
        for artifact in record.get("public_artifacts", [])
    ]
    attempt_history: list[dict[str, Any]] = []
    attempt_positions: dict[str, int] = {}
    for stage_record in stage_records:
        for attempt in stage_record.get("attempt_history", []):
            attempt_id = str(attempt.get("attempt_id", ""))
            if attempt_id in attempt_positions:
                attempt_history[attempt_positions[attempt_id]] = attempt
            else:
                attempt_positions[attempt_id] = len(attempt_history)
                attempt_history.append(attempt)
    aggregate = dict(final_record)
    aggregate.update(
        {
            "outcome": terminal_outcome,
            "outcome_reason": (
                f"{final_record['outcome_reason']} "
                f"规划路线据此进入终态：{terminal_outcome}。"
            ),
            "request_sha256": canonical_sha256(request),
            "response_sha256": canonical_sha256(response),
            "design_sha256": canonical_sha256(design),
            "final_stage_id": state["current_stage_id"],
            "stage_history": state["stage_history"],
            "artifact_lineage": state["artifact_lineage"],
            "budget_usage": {
                **state["budget_usage"],
                "elapsed_wall_seconds": round(_elapsed_run_seconds(state), 3),
                "total_wall_limit_seconds": request["run_budget"][
                    "total_wall_seconds"
                ],
                "attempt_limit": request["run_budget"]["max_total_attempts"],
            },
            "attempt_history": attempt_history,
            "public_artifacts": public_artifacts,
        }
    )
    aggregate.pop("record_sha256", None)
    aggregate["record_sha256"] = canonical_sha256(aggregate)
    atomic_write_json(root / "record.json", aggregate)
    return aggregate


def verify(
    run_id: str,
    attempt_id: str,
    scientific_assessment: dict[str, Any] | None,
) -> dict[str, Any]:
    root, state = load_state(run_id)
    if state["phase"] != "execution_finished" or state["current_attempt"] != attempt_id:
        raise ServiceError("verification requires the current finished attempt")
    request = _load_request(root)
    response = read_json(root / "response.json")
    design = read_json(root / "design.json")
    stage_id = state.get("current_stage_id")
    if not isinstance(stage_id, str):
        raise ServiceError("verification requires an active experiment stage")
    try:
        record = verify_attempt(
            root,
            state,
            request,
            response,
            design,
            attempt_id,
            scientific_assessment,
            stage_id=stage_id,
            persist_run_record=False,
        )
    except AssessmentRequired as required:
        measurement_plan = design.get("measurement_plan") or [
            {
                "name": row["name"],
                "display_name": row["name"],
                "role": row["role"],
                "unit": row["unit"],
                "scientific_meaning": "旧设计未保存中文测量释义；只按已核验数值解释。",
            }
            for row in required.preview["worker_result"]["measurements"]
        ]
        analysis_mode = str(design["research_frame"]["analysis_mode"])
        interval = required.preview["worker_result"]["scientific_payload"].get(
            "interval"
        )
        evidence_type = (
            "inferential_interval"
            if analysis_mode == "inferential" and interval is not None
            else "descriptive_holdout"
            if "holdout" in analysis_mode.lower()
            else "descriptive_or_task_bounded"
        )
        completed_target = experiment_stage(design, stage_id)["transitions"][
            "completed"
        ]
        stage_ids = {row["id"] for row in design["experiment_stages"]}
        return {
            "schema_version": "automatic-experiment-verification-preview-v1",
            "status": "assessment_required",
            "run_id": run_id,
            "attempt_id": attempt_id,
            "stage_id": stage_id,
            "trusted_worker_result": required.preview["worker_result"],
            "verified_artifacts": required.preview["verified_artifacts"],
            "verified_artifact_evidence": required.preview[
                "verified_artifact_evidence"
            ],
            "criterion_evidence": required.preview["criterion_evidence"],
            "paired_comparison_evidence": required.preview[
                "paired_comparison_evidence"
            ],
            "execution_summary": required.preview["execution_summary"],
            "assessment_authoring_guide": {
                "evidence_type": evidence_type,
                "scientific_assessment_contract": {
                    "exact_fields": [
                        "proposed_outcome",
                        "stage_outcome",
                        "rationale",
                        "criterion_results",
                        "uncertainty_reasons",
                        "null_assessment",
                        "report_narrative",
                    ],
                    "allowed_proposed_outcomes": [
                        "completed_interpretable",
                        "partial_result",
                        "scientific_null",
                        "high_uncertainty",
                    ],
                    "allowed_stage_outcomes": [
                        "completed",
                        "inconclusive",
                        "input_missing",
                        "evidence_conflict",
                        "method_invalid",
                    ],
                    "completed_stage_outcome_rule": (
                        "This completed stage transitions to another experiment stage, "
                        "so proposed_outcome must be partial_result until that later stage "
                        "is verified."
                        if completed_target in stage_ids
                        else "This completed stage is terminal, so proposed_outcome must be "
                        "completed_interpretable or scientific_null."
                    ),
                    "criterion_result_exact_fields": [
                        "criterion_id",
                        "status",
                        "explanation",
                    ],
                    "criterion_status_values": [
                        "met",
                        "not_met",
                        "uncertain",
                        "not_evaluated",
                    ],
                    "criterion_id_rule": (
                        "criterion_id must be copied exactly from criterion_evidence; "
                        "use explanation, never evidence_summary"
                    ),
                    "report_narrative_exact_fields": [
                        "title",
                        "objective",
                        "data_scope",
                        "method",
                        "interpretation",
                        "evidence_strength",
                        "claim_boundary",
                        "limitations",
                        "next_steps",
                    ],
                    "report_narrative_types": {
                        "title": "string",
                        "objective": "string",
                        "data_scope": "string",
                        "method": "string",
                        "interpretation": "string",
                        "evidence_strength": "string",
                        "claim_boundary": "string",
                        "limitations": "array of 0 to 8 strings; empty is valid when no material limitation remains beyond claim_boundary",
                        "next_steps": "array of 0 to 8 strings; empty is valid when the user already received a complete answer",
                    },
                    "report_style": (
                        "Write publication-style research prose. Lead with the substantive finding; "
                        "the abstract interpretation should use verified primary measurements, but "
                        "omit a number rather than approximate or invent it when uncertain because "
                        "the deterministic renderer will insert the verified primary comparison. "
                        "describe data, method, result, interpretation, and limitations without "
                        "narrating code success, stage completion, file checks, internal status, or replay commands. "
                        "Describe observations, samples, and scientific variables rather than rows, columns, or fields. "
                        "Do not claim that parameter changes and error changes point in the same scientific direction. "
                        "Without a grounded decision basis, report the magnitude but do not call an effect non-negligible. "
                        "Each limitation must state what is limited and which interpretation it affects; avoid fragments. "
                        "A next step must directly resolve a stated limitation and must not reopen a deferred question."
                    ),
                    "null_assessment_rule": (
                        "must be null unless proposed_outcome is scientific_null; "
                        "scientific_null additionally requires an estimand, interval, "
                        "equivalence bounds, and power or sensitivity basis"
                    ),
                    "unknown_fields": "forbidden at every level",
                },
                "allowed_measurements": [
                    row
                    for row in measurement_plan
                    if row["name"]
                    in set(experiment_stage(design, stage_id)["measurement_refs"])
                ],
                "allowed_typed_results": [
                    row
                    for row in design["result_plan"]
                    if row["id"]
                    in set(experiment_stage(design, stage_id)["result_refs"])
                ],
                "verified_pointwise_facts": [
                    {
                        "evaluation_scope": row["evaluation_scope"],
                        "row_count": row["row_count"],
                        "all_candidate_absolute_errors_lower": row.get(
                            "all_candidate_absolute_errors_lower"
                        ),
                        "candidate_better_absolute_error_count": row.get(
                            "candidate_better_absolute_error_count"
                        ),
                        "candidate_tied_absolute_error_count": row.get(
                            "candidate_tied_absolute_error_count"
                        ),
                        "candidate_worse_absolute_error_count": row.get(
                            "candidate_worse_absolute_error_count"
                        ),
                    }
                    for row in required.preview["paired_comparison_evidence"]
                ],
                "preferred_phrasing": [
                    "当前留出段观测到的误差变化",
                    "N 条留出观测的平均有符号误差为正或为负",
                    "全部 N 条留出观测中，候选方案的绝对误差均低于基线"
                    "（仅当 verified_pointwise_facts 明确支持）",
                    "对排除这一标记观测的方向性结论一致",
                    "两种条件的估计量分别为已核验数值，二者差值为已核验数值",
                ],
                "forbidden_or_conditioned_phrasing": {
                    "显著": "仅在存在推断性区间/检验依据时允许",
                    "泛化能力": "当前非预测性小留出证据不允许",
                    "系统正偏差或系统性高估": "改写为当前留出段平均有符号误差的方向与数值",
                    "接近比较坐标或接近零": "没有预设接近阈值时不允许",
                    "不可忽视": "没有预设且有依据的判定标准时只报告差值和适用样本",
                    "保持稳定或量级较小": "没有等效界限或有依据阈值时只报告两侧估计值、差值与方向，并说明实际重要性尚不能判断",
                    "整体稳健": "单一扰动只能表述为该扰动下方向性结论一致",
                    "全面稳健性分析": "后续研究也要写成针对具体标记条件的敏感性检查",
                    "消除系统偏差": "不允许由描述性小样本结果推出",
                    "基本消除偏差": "同样不允许；只报告当前留出段平均有符号误差的数值变化",
                    "代码执行成功或阶段已完成": "这是内部过程，不是科研结论",
                    "科研含义或主张边界": "不要把字段标签写进正文，直接陈述统计口径和适用范围",
                    "positive或negative": (
                        "不是合法终态；只能使用 allowed_proposed_outcomes 中的值"
                    ),
                    "evidence_summary": (
                        "不是 criterion_results 的合法字段；使用 explanation"
                    ),
                },
            },
            "next_action": (
                "根据已核验结果形成 scientific_assessment 和 report_narrative，"
                "然后再次调用 automatic_experiment_verify_result。"
            ),
        }
    request_remaining = _remaining_run_seconds(state, request)
    stage_attempts = int(state["stage_attempt_counts"].get(stage_id, 0))
    repair_allowed = (
        record["outcome"] == "technical_failure"
        and state["remaining_attempts"] > 0
        and stage_attempts < request["resource_budget"]["max_attempts"]
        and request_remaining > 0
    )
    if repair_allowed:
        state["outcome"] = "technical_failure"
        state["last_error"] = record["outcome_reason"]
        checkpoint(
            root,
            state,
            "verification_finished",
            {
                "stage_id": stage_id,
                "outcome": "technical_failure",
                "repair_allowed": True,
            },
        )
        return {
            "schema_version": "automatic-experiment-verification-result-v1",
            "status": "repair_required",
            "run_id": run_id,
            "attempt_id": attempt_id,
            "stage_id": stage_id,
            "outcome": "technical_failure",
            "outcome_reason": record["outcome_reason"],
            "repair_allowed": True,
            "remaining_attempts": state["remaining_attempts"],
            "remaining_run_seconds": request_remaining,
            "record_sha256": record["record_sha256"],
        }

    direct_terminal: str | None = None
    if record["outcome"] == "budget_stopped":
        stage_outcome = "budget_reached"
    elif record["outcome"] in {"cancelled_by_user", "boundary_blocked"}:
        stage_outcome = "technical_failure"
        direct_terminal = record["outcome"]
    elif record["outcome"] == "technical_failure":
        stage_outcome = (
            "budget_reached"
            if state["remaining_attempts"] <= 0
            or request_remaining <= 0
            or stage_attempts >= request["resource_budget"]["max_attempts"]
            else "technical_failure"
        )
    else:
        assessment = record.get("scientific_assessment") or {}
        stage_outcome = assessment.get("stage_outcome", "inconclusive")
    frozen = _freeze_stage_artifacts(
        root,
        design,
        stage_id,
        attempt_id,
        record,
    )
    state["artifact_lineage"].extend(frozen)
    target = direct_terminal or experiment_stage(design, stage_id)["transitions"][
        stage_outcome
    ]
    stage_row = {
        "stage_id": stage_id,
        "attempt_id": attempt_id,
        "stage_outcome": stage_outcome,
        "transition_target": target,
        "record_path": f"stages/{stage_id}/record.json",
        "record_sha256": record["record_sha256"],
        "verified_at": record["verified_at"],
        "artifacts": [row["artifact_id"] for row in frozen],
        "result_summary": {
            "measurements": (record.get("worker_result") or {}).get(
                "measurements", []
            ),
            "result_items": (record.get("worker_result") or {}).get(
                "result_items", []
            ),
            "endpoint_results": (record.get("worker_result") or {}).get(
                "endpoint_results", []
            ),
        },
    }
    state["stage_history"].append(stage_row)
    state["budget_usage"]["stages_completed"] += 1
    stage_ids = {row["id"] for row in design["experiment_stages"]}
    if target in stage_ids:
        state["current_stage_id"] = target
        state["current_attempt"] = None
        state["outcome"] = None
        state["verified_record_sha256"] = None
        state["last_error"] = None
        checkpoint(
            root,
            state,
            "stage_transitioned",
            {
                "completed_stage_id": stage_id,
                "stage_outcome": stage_outcome,
                "next_stage_id": target,
            },
        )
        replay_prepared = _prepare_next_exact_replay_stage(run_id, state, target)
        return {
            "schema_version": "automatic-experiment-verification-result-v1",
            "status": (
                "next_stage_prepared"
                if replay_prepared is not None
                else "next_stage_required"
            ),
            "run_id": run_id,
            "attempt_id": attempt_id,
            "completed_stage_id": stage_id,
            "stage_outcome": stage_outcome,
            "next_stage_id": target,
            "next_stage": experiment_stage(design, target),
            "prepared_attempt_id": (
                replay_prepared["attempt_id"] if replay_prepared is not None else None
            ),
            "remaining_attempts": (
                replay_prepared["remaining_attempts"]
                if replay_prepared is not None
                else state["remaining_attempts"]
            ),
            "remaining_run_seconds": _remaining_run_seconds(state, request),
        }

    aggregate = _aggregate_stage_record(
        root,
        state,
        request,
        response,
        design,
        record,
        target,
    )
    state["outcome"] = aggregate["outcome"]
    state["verified_record_sha256"] = aggregate["record_sha256"]
    state["last_error"] = (
        aggregate["outcome_reason"]
        if aggregate["outcome"] == "technical_failure"
        else None
    )
    checkpoint(
        root,
        state,
        "verification_finished",
        {
            "outcome": aggregate["outcome"],
            "record_sha256": aggregate["record_sha256"],
            "final_stage_id": stage_id,
        },
    )
    return {
        "schema_version": "automatic-experiment-verification-result-v1",
        "status": "verified",
        "run_id": run_id,
        "attempt_id": attempt_id,
        "stage_id": stage_id,
        "outcome": aggregate["outcome"],
        "outcome_reason": aggregate["outcome_reason"],
        "repair_allowed": False,
        "remaining_attempts": state["remaining_attempts"],
        "record_sha256": aggregate["record_sha256"],
    }


_KB_WRITEBACK_TYPES = {
    "completed_interpretable": "finding",
    "scientific_null": "counterexample",
    "technical_failure": "finding",
}


def _knowledge_writeback(root: Path, state: dict[str, Any]) -> dict[str, Any]:
    """方案 §5.4 #4：finalize 后把 findings/反例/失败经验写回知识库候选。

    候选以 ``source_type=historical_run``、``source_ref=run_id``、
    ``confidence=low`` 走正常 ``kb_propose`` 流程（status=candidate，R2）。
    知识库不可用时静默降级，绝不影响 finalize 主流程。
    """

    outcome = state.get("outcome")
    entry_type = _KB_WRITEBACK_TYPES.get(outcome)
    if entry_type is None:
        return {
            "status": "skipped",
            "entry_ids": [],
            "reason": f"outcome {outcome!r} 无写回映射",
        }
    try:
        from knowledge_base import service as kb_service
        from knowledge_base.store import KnowledgeStore

        run_id = str(state["run_id"])
        record = (
            read_json(root / "record.json")
            if (root / "record.json").is_file()
            else {}
        )
        assessment = record.get("scientific_assessment") or {}
        narrative = assessment.get("report_narrative") or {}
        statement = str(
            narrative.get("interpretation")
            or assessment.get("rationale")
            or record.get("outcome_reason")
            or state.get("last_error")
            or ""
        ).strip()
        if not statement:
            statement = f"运行 {run_id} 以 {outcome} 结束；未留下可用的科学陈述。"
        task_text = str(record.get("task") or "")
        title = (f"[{outcome}] {task_text[:80]}").strip() or f"run {run_id} {outcome}"
        content: dict[str, Any] = {
            "statement": statement[:2_000],
            "run_id": run_id,
        }
        limitations = narrative.get("limitations")
        if isinstance(limitations, list) and limitations:
            content["uncertainty"] = "; ".join(str(item) for item in limitations[:3])[:1_000]
        store = KnowledgeStore()
        try:
            existing = store.find_entry_by_source(run_id, title)
            if existing is not None:
                # finalize 可重入：同一 run 重复写回不得重复建条。
                return {"status": "ok", "entry_ids": [existing["id"]]}
            result = kb_service.propose(
                store,
                entry_type=entry_type,
                title=title,
                content=content,
                source_type="historical_run",
                source_ref=run_id,
                confidence="low",
                agent="automatic_experiment",
                run_id=run_id,
            )
            return {"status": "ok", "entry_ids": [result["entry"]["id"]]}
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "entry_ids": [],
            "reason": "knowledge_base_writeback_failed",
            "error_type": type(exc).__name__,
        }


def finalize(run_id: str) -> dict[str, Any]:
    root, state = load_state(run_id)
    if (root / "entry_result.json").is_file():
        if state["phase"] == "report_finalized":
            entry = _validated_finalized_entry(root, state)
            entry["knowledge_writeback"] = _knowledge_writeback(root, state)
            return entry
        if state["phase"] != "verification_finished":
            raise ServiceError("entry_result.json exists before finalization was eligible")
        recovered = _validated_finalized_entry(
            root,
            state,
            require_state_hashes=False,
        )
        state["outcome"] = recovered["outcome"]
        state["report_sha256"] = recovered["report_sha256"]
        state["audit_sha256"] = recovered["audit_sha256"]
        state["report_assets"] = recovered["report_assets"]
        checkpoint(
            root,
            state,
            "report_finalized",
            {
                "report_sha256": recovered["report_sha256"],
                "audit_sha256": recovered["audit_sha256"],
                "report_assets": recovered["report_assets"],
                "recovered_after_entry_write": True,
            },
        )
        entry = _validated_finalized_entry(root, state)
        entry["knowledge_writeback"] = _knowledge_writeback(root, state)
        return entry
    request = _load_request(root)
    if (root / "record.json").is_file():
        record = _validated_current_record(root, state)
    else:
        if state["outcome"] is None or not (root / "response.json").is_file():
            raise ServiceError(
                "the current attempt has not been verified and cannot be finalized"
            )
        response = read_json(root / "response.json")
        reason = state["last_error"] or "任务在执行前进入正式终态。"
        record = create_early_record(
            root,
            state,
            request,
            response,
            state["outcome"],
            reason,
        )
        state["verified_record_sha256"] = record["record_sha256"]
        checkpoint(
            root,
            state,
            "verification_finished",
            {"outcome": record["outcome"], "record_sha256": record["record_sha256"]},
        )
    entry = finalize_report(root, record)
    state["outcome"] = record["outcome"]
    state["report_sha256"] = entry["report_sha256"]
    state["audit_sha256"] = entry["audit_sha256"]
    state["report_assets"] = entry["report_assets"]
    checkpoint(
        root,
        state,
        "report_finalized",
        {
            "report_sha256": entry["report_sha256"],
            "audit_sha256": entry["audit_sha256"],
            "report_assets": entry["report_assets"],
        },
    )
    finalized = _validated_finalized_entry(root, state)
    finalized["knowledge_writeback"] = _knowledge_writeback(root, state)
    return finalized


def finalize_interrupted(
    run_id: str,
    reason: str,
    outcome: str | None = None,
) -> dict[str, Any]:
    """Produce an honest terminal bundle from every persisted lifecycle phase."""

    root, state = load_state(run_id)
    if state["phase"] == "report_finalized":
        return _validated_finalized_entry(root, state)
    if (
        state["phase"] == "verification_finished"
        and (root / "record.json").is_file()
    ):
        return finalize(run_id)
    request = _load_request(root)
    response = (
        read_json(root / "response.json")
        if (root / "response.json").is_file()
        else None
    )
    normalized_reason = reason.strip() or "本次运行在完成全部核验前结束。"
    selected_outcome = outcome
    if selected_outcome is None:
        if state.get("cancel_requested"):
            selected_outcome = "cancelled_by_user"
        elif _remaining_run_seconds(state, request) <= 0:
            selected_outcome = "budget_stopped"
        else:
            selected_outcome = "high_uncertainty"
    if selected_outcome not in OUTCOMES:
        raise ServiceError("interrupted finalization outcome is unsupported")
    record = create_early_record(
        root,
        state,
        request,
        response,
        selected_outcome,
        normalized_reason,
    )
    state["outcome"] = selected_outcome
    state["last_error"] = normalized_reason
    state["verified_record_sha256"] = record["record_sha256"]
    checkpoint(
        root,
        state,
        "verification_finished",
        {
            "outcome": selected_outcome,
            "record_sha256": record["record_sha256"],
            "interrupted_from_phase": record["execution_state"],
        },
    )
    return finalize(run_id)


def prepare_continuation(source_run_id: str) -> dict[str, Any]:
    """Resume an unfinished run or create a child run from a finished interruption."""

    source_root, source_state = load_state(source_run_id)
    if source_state["phase"] != "report_finalized":
        return {
            "schema_version": "automatic-experiment-continuation-v1",
            "status": "resume_existing",
            "run_id": source_run_id,
            "phase": source_state["phase"],
            "current_stage_id": source_state.get("current_stage_id"),
            "current_attempt": source_state.get("current_attempt"),
            "remaining_attempts": source_state.get("remaining_attempts"),
            "report_exists": (source_root / "report.md").is_file(),
        }
    source_request = _load_request(source_root)
    source_outcome = source_state.get("outcome")
    if source_outcome in {"completed_interpretable", "scientific_null"}:
        raise ServiceError(
            "该运行已经完整结束；如需复现请使用“重放”，如需换方法请提交新任务。"
        )
    continuation_request = dict(source_request)
    continuation_request["replay_of"] = source_run_id
    lineage = {
        "mode": "workflow_continuation",
        "source_run_id": source_run_id,
        "matching_run_ids": [],
        "source_outcome": source_outcome,
        "source_record_sha256": source_state.get("verified_record_sha256"),
        "numeric_cross_run_comparison_permitted": False,
        "comparison_reason": "继续运行可补充输入或重新设计，不假定与历史运行数值可直接比较。",
    }
    run_id, _, state = create_run(
        continuation_request,
        request_fingerprint=_request_fingerprint(continuation_request),
        lineage=lineage,
    )
    return {
        "schema_version": "automatic-experiment-continuation-v1",
        "status": "continuation_created",
        "run_id": run_id,
        "source_run_id": source_run_id,
        "phase": state["phase"],
    }


def _validated_finalized_entry(
    root: Path,
    state: dict[str, Any],
    *,
    require_state_hashes: bool = True,
) -> dict[str, Any]:
    entry_path = root / "entry_result.json"
    if not entry_path.is_file():
        raise ServiceError("finalized run is missing entry_result.json")
    entry = read_json(entry_path)
    payload = dict(entry)
    stored_entry_sha = payload.pop("entry_sha256", None)
    if (
        not isinstance(stored_entry_sha, str)
        or stored_entry_sha != canonical_sha256(payload)
    ):
        raise ServiceError("finalized entry result hash is invalid")
    if (
        entry.get("schema_version") != ENTRY_RESULT_VERSION
        or entry.get("status") != "finalized"
        or entry.get("run_id") != state["run_id"]
        or entry.get("outcome") != state.get("outcome")
        or entry.get("record_path") != "record.json"
        or entry.get("report_path") != "report.md"
    ):
        raise ServiceError("finalized entry result identity is invalid")
    record_path = root / "record.json"
    report_path = root / "report.md"
    if (
        not record_path.is_file()
        or entry.get("record_sha256") != file_sha256(record_path)
    ):
        raise ServiceError("finalized record is missing or changed")
    if (
        not report_path.is_file()
        or entry.get("report_sha256") != file_sha256(report_path)
        or (
            require_state_hashes
            and state.get("report_sha256") != entry.get("report_sha256")
        )
    ):
        raise ServiceError("finalized report is missing or changed")
    try:
        report = report_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ServiceError("finalized report is not readable as UTF-8") from exc
    if entry.get("user_display_markdown") != report:
        raise ServiceError("Pi display Markdown differs from report.md")

    requires_complete_bundle = "audit_sha256" in state
    if requires_complete_bundle:
        if (
            entry.get("audit_path") != "audit.md"
            or not isinstance(entry.get("audit_sha256"), str)
            or not isinstance(entry.get("report_assets"), list)
        ):
            raise ServiceError("new finalized run lacks its audit bundle")
        audit_path = root / "audit.md"
        if (
            not audit_path.is_file()
            or file_sha256(audit_path) != entry["audit_sha256"]
            or (
                require_state_hashes
                and state.get("audit_sha256") != entry["audit_sha256"]
            )
        ):
            raise ServiceError("finalized audit attachment is missing or changed")
        if (
            require_state_hashes
            and state.get("report_assets") != entry["report_assets"]
        ):
            raise ServiceError("finalized report asset metadata differs from state")
    for asset in entry.get("report_assets", []):
        if (
            not isinstance(asset, dict)
            or not isinstance(asset.get("path"), str)
            or not asset["path"].startswith("report_assets/")
            or "\\" in asset["path"]
            or ".." in Path(asset["path"]).parts
        ):
            raise ServiceError("finalized report asset metadata is invalid")
        asset_path = root / Path(*asset["path"].split("/"))
        if (
            not asset_path.is_file()
            or file_sha256(asset_path) != asset.get("sha256")
            or asset_path.stat().st_size != asset.get("size_bytes")
        ):
            raise ServiceError("finalized report asset is missing or changed")
    return entry


def status(run_id: str | None) -> dict[str, Any]:
    selected = run_id or latest_run_id()
    if selected is None:
        return {
            "schema_version": "automatic-experiment-status-v1",
            "status": "idle",
            "active_run": None,
        }
    root, state = load_state(selected)
    current_stage_objective = None
    if (root / "design.json").is_file() and isinstance(
        state.get("current_stage_id"), str
    ):
        design = read_json(root / "design.json")
        current_stage_objective = next(
            (
                stage.get("objective")
                for stage in design.get("experiment_stages", [])
                if stage.get("id") == state["current_stage_id"]
            ),
            None,
        )
    return {
        "schema_version": "automatic-experiment-status-v1",
        "status": "run_loaded",
        "active_run": state,
        "current_stage_objective": current_stage_objective,
        "run_root": str(root),
        "record_exists": (root / "record.json").is_file(),
        "report_exists": (root / "report.md").is_file(),
    }


def stop(run_id: str | None) -> dict[str, Any]:
    selected = run_id or latest_run_id()
    if selected is None:
        return {"status": "idle", "message": "没有可停止的实验运行。"}
    root, state = load_state(selected)
    result = request_stop(root)
    state["cancel_requested"] = True
    state["last_error"] = "用户请求停止当前执行。"
    save_state(root, state)
    return result


def doctor() -> dict[str, Any]:
    return executor_doctor()
