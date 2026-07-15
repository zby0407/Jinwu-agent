"""Versioned science-agent contracts and an immutable JSON run store."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import psutil

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = PROJECT_ROOT / "b3" / "specs"
PRODUCTION_ANALYSIS_WORKER = (
    PROJECT_ROOT / "scripts_b3" / "run_analysis_worker.py"
).resolve()

# Stable wire identifiers retained because they participate in immutable historical hashes.
# Human-facing prompts and documentation call these contracts ResearchPlan 1.0,
# ExperimentManifest 1.0, and HypothesisPortfolio 1.0.
RESEARCH_PLAN_SCHEMA_VERSION = "b3-research-plan-v2"
EXPERIMENT_MANIFEST_SCHEMA_VERSION = "b3-experiment-manifest-v2"
HYPOTHESIS_PORTFOLIO_SCHEMA_VERSION = "b3-hypothesis-portfolio-v2"

EXPERIMENT_STATUSES = frozenset(
    {"passed", "warning", "failed", "quarantined", "inconclusive"}
)
HYPOTHESIS_PORTFOLIO_STATUSES = frozenset(
    {"draft", "needs_revision", "calibrated", "rejected"}
)

REGISTERED_EXPERIMENTS = {
    "E0_data_vintage_audit": "data_vintage_audit",
    "E1_cycle_segmentation_baseline": "cycle_segmentation",
    "E2_waldmeier_leave_one_cycle_out": "waldmeier",
    "E3_f107_phase_stratified_drift": "f107_drift",
    "E4_extended_hemispheric_calibration": "hemispheric_calibration",
    "E5_polar_precursor_robustness": "polar_precursor",
    "E6_low_order_dynamo_family_ablation": "dynamo_ablation",
    "E7_negative_controls_and_placebos": "negative_controls",
    "E8_clean_reproduction": "clean_reproduction",
}

_EXPERIMENT_FACT_FIELDS = {
    "E0_data_vintage_audit": (
        "data_manifest",
        "series_coverage",
        "data_vintage_audit",
    ),
    "E1_cycle_segmentation_baseline": (
        "series_coverage",
        "cycle_features",
        "cycle_segmentation_baseline",
    ),
    "E2_waldmeier_leave_one_cycle_out": (
        "waldmeier_leave_one_cycle_out",
        "waldmeier",
    ),
    "E3_f107_phase_stratified_drift": (
        "f107_phase_stratified_drift",
        "f10_7_drift",
    ),
    "E4_extended_hemispheric_calibration": (
        "extended_hemispheric_calibration",
        "hemispheric_asymmetry",
    ),
    "E5_polar_precursor_robustness": (
        "polar_precursor_robustness",
        "polar_precursor",
    ),
    "E6_low_order_dynamo_family_ablation": (
        "low_order_dynamo_family_ablation",
        "dynamo_toy_model",
    ),
    "E7_negative_controls_and_placebos": ("negative_controls_and_placebos",),
    "E8_clean_reproduction": (
        "project",
        "claim_boundary",
        "series_coverage",
        "data_vintage_audit",
        "cycle_features",
        "cycle_segmentation_baseline",
        "cycle26_proxy_forecast",
        "waldmeier",
        "f10_7_drift",
        "hemispheric_asymmetry",
        "polar_precursor",
        "dynamo_toy_model",
        "hypothesis_cards",
        "tournament_ranking",
        "waldmeier_leave_one_cycle_out",
        "f107_phase_stratified_drift",
        "extended_hemispheric_calibration",
        "polar_precursor_robustness",
        "low_order_dynamo_family_ablation",
        "negative_controls_and_placebos",
        "clean_reproduction",
    ),
}

_EXPERIMENT_SOURCE_IDS = {
    "E0_data_vintage_audit": frozenset(
        {
            "silso_monthly_total",
            "silso_monthly_smoothed_total",
            "silso_monthly_hemispheric",
            "noaa_observed_solar_cycle_indices",
            "noaa_predicted_solar_cycle",
            "wso_polar_field_observations",
            "silso_extended_hemispheric_catalogue_b",
        }
    ),
    "E1_cycle_segmentation_baseline": frozenset(
        {"silso_monthly_total", "silso_monthly_smoothed_total"}
    ),
    "E2_waldmeier_leave_one_cycle_out": frozenset(
        {"silso_monthly_smoothed_total"}
    ),
    "E3_f107_phase_stratified_drift": frozenset(
        {"noaa_observed_solar_cycle_indices"}
    ),
    "E4_extended_hemispheric_calibration": frozenset(
        {
            "silso_monthly_hemispheric",
            "silso_extended_hemispheric_catalogue_b",
        }
    ),
    "E5_polar_precursor_robustness": frozenset(
        {"silso_monthly_smoothed_total", "wso_polar_field_observations"}
    ),
    "E6_low_order_dynamo_family_ablation": frozenset(
        {"silso_monthly_smoothed_total", "wso_polar_field_observations"}
    ),
    "E7_negative_controls_and_placebos": frozenset(
        {"silso_monthly_smoothed_total", "wso_polar_field_observations"}
    ),
    "E8_clean_reproduction": frozenset(
        {
            "silso_monthly_total",
            "silso_monthly_smoothed_total",
            "silso_monthly_hemispheric",
            "silso_extended_hemispheric_catalogue_b",
            "noaa_observed_solar_cycle_indices",
            "noaa_predicted_solar_cycle",
            "wso_polar_field_observations",
        }
    ),
}

_REGISTERED_OUTPUT_REQUIREMENTS = {
    "E0_data_vintage_audit": ("data_manifest", "series_coverage"),
    "E1_cycle_segmentation_baseline": ("cycle_features",),
    "E2_waldmeier_leave_one_cycle_out": ("waldmeier_leave_one_cycle_out",),
    "E3_f107_phase_stratified_drift": ("f107_phase_stratified_drift",),
    "E4_extended_hemispheric_calibration": (
        "extended_hemispheric_calibration",
    ),
    "E5_polar_precursor_robustness": ("polar_precursor_robustness",),
    "E6_low_order_dynamo_family_ablation": (
        "low_order_dynamo_family_ablation",
    ),
    "E7_negative_controls_and_placebos": (
        "negative_controls_and_placebos",
    ),
    "E8_clean_reproduction": (
        "data_vintage_audit",
        "cycle_features",
        "cycle_segmentation_baseline",
        "waldmeier",
        "f10_7_drift",
        "hemispheric_asymmetry",
        "polar_precursor",
        "dynamo_toy_model",
        "waldmeier_leave_one_cycle_out",
        "f107_phase_stratified_drift",
        "extended_hemispheric_calibration",
        "polar_precursor_robustness",
        "low_order_dynamo_family_ablation",
        "negative_controls_and_placebos",
        "clean_reproduction",
    ),
}

_CENTERED_INPUT_EXPERIMENTS = frozenset(
    {
        "E1_cycle_segmentation_baseline",
        "E2_waldmeier_leave_one_cycle_out",
        "E5_polar_precursor_robustness",
        "E6_low_order_dynamo_family_ablation",
        "E8_clean_reproduction",
    }
)

_RESEARCH_PLAN_FIELDS = [
    "schema_version",
    "run_id",
    "created_at",
    "research_question",
    "claim_boundary",
    "data_contracts",
    "task_graph",
    "primary_metrics",
    "counter_evidence_paths",
    "stop_rules",
    "frozen_hash",
    "artifact_sha256",
]
_EXPERIMENT_MANIFEST_FIELDS = [
    "schema_version",
    "run_id",
    "node_id",
    "parent_id",
    "experiment_id",
    "seed",
    "status",
    "started_at",
    "finished_at",
    "data_sources",
    "artifacts",
    "result",
    "error",
    "claim_effect",
    "artifact_sha256",
]
_HYPOTHESIS_PORTFOLIO_FIELDS = [
    "schema_version",
    "run_id",
    "portfolio_id",
    "created_at",
    "status",
    "claim_boundary",
    "source_ids",
    "artifact_paths",
    "experiment_ids",
    "hypotheses",
    "artifact_sha256",
]
_CLAIM_EFFECTS_BY_STATUS = {
    "passed": {"supports_bounded_claim", "keeps_confidence_bounded"},
    "warning": {"keeps_confidence_bounded", "lowers_confidence"},
    "inconclusive": {"keeps_confidence_bounded", "lowers_confidence"},
    "failed": {"blocks_claim"},
    "quarantined": {"blocks_claim"},
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ScienceAgentError(ValueError):
    """Raised when a science-agent contract or immutable-store rule fails."""


def canonical_json_sha256(payload: object) -> str:
    """Return SHA-256 over the canonical UTF-8 JSON representation of payload."""

    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ScienceAgentError(
            "payload must contain only finite JSON values with string object keys"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def require_fields(payload: dict[str, Any], fields: list[str], label: str) -> None:
    """Require every named top-level field in payload."""

    missing = [field for field in fields if field not in payload]
    if missing:
        raise ScienceAgentError(f"{label} missing fields: {', '.join(missing)}")


class _SchemaViolation(ValueError):
    pass


def _load_contract_schema(filename: str) -> dict[str, Any]:
    path = SCHEMA_ROOT / filename
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ScienceAgentError(f"contract schema is unavailable or invalid: {path}") from exc
    if not isinstance(schema, dict):
        raise ScienceAgentError(f"contract schema must be a JSON object: {path}")
    return schema


def _resolve_local_ref(root_schema: dict[str, Any], ref: object, path: str) -> dict[str, Any]:
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise _SchemaViolation(f"{path} uses unsupported schema reference: {ref}")
    target: object = root_schema
    for raw_token in ref[2:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, dict) or token not in target:
            raise _SchemaViolation(f"{path} uses unresolved schema reference: {ref}")
        target = target[token]
    if not isinstance(target, dict):
        raise _SchemaViolation(f"{path} schema reference is not an object: {ref}")
    return target


def _matches_json_type(value: object, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _validate_schema_value(
    value: object,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str,
) -> None:
    if "$ref" in schema:
        referenced = _resolve_local_ref(root_schema, schema["$ref"], path)
        _validate_schema_value(value, referenced, root_schema, path)

    if "oneOf" in schema:
        options = schema["oneOf"]
        if not isinstance(options, list):
            raise _SchemaViolation(f"{path} has invalid oneOf schema")
        match_count = 0
        for option in options:
            if not isinstance(option, dict):
                continue
            try:
                _validate_schema_value(value, option, root_schema, path)
            except _SchemaViolation:
                continue
            match_count += 1
        if match_count != 1:
            raise _SchemaViolation(f"{path} does not match exactly one allowed schema")

    if "const" in schema and value != schema["const"]:
        raise _SchemaViolation(f"{path} must equal {schema['const']!r}")
    if "enum" in schema:
        choices = schema["enum"]
        if not isinstance(choices, list) or value not in choices:
            raise _SchemaViolation(f"{path} must be one of: {choices}")

    expected_types = schema.get("type")
    if expected_types is not None:
        expected = [expected_types] if isinstance(expected_types, str) else expected_types
        if not isinstance(expected, list) or not all(isinstance(item, str) for item in expected):
            raise _SchemaViolation(f"{path} has invalid type schema")
        if not any(_matches_json_type(value, item) for item in expected):
            raise _SchemaViolation(f"{path} must have JSON type {' or '.join(expected)}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        if not isinstance(required, list):
            raise _SchemaViolation(f"{path} has invalid required schema")
        missing = [field for field in required if field not in value]
        if missing:
            raise _SchemaViolation(f"{path} missing fields: {', '.join(missing)}")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise _SchemaViolation(f"{path} has invalid properties schema")
        if schema.get("additionalProperties") is False:
            unknown = [field for field in value if field not in properties]
            if unknown:
                raise _SchemaViolation(f"{path} has unknown fields: {', '.join(unknown)}")
        for field, child in value.items():
            child_schema = properties.get(field)
            if isinstance(child_schema, dict):
                _validate_schema_value(child, child_schema, root_schema, f"{path}.{field}")

    if isinstance(value, list):
        minimum_items = schema.get("minItems")
        if isinstance(minimum_items, int) and len(value) < minimum_items:
            raise _SchemaViolation(f"{path} must contain at least {minimum_items} item(s)")
        if schema.get("uniqueItems") is True:
            signatures: set[str] = set()
            for item in value:
                try:
                    signature = json.dumps(
                        item,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                except (TypeError, ValueError) as exc:
                    raise _SchemaViolation(f"{path} contains a non-JSON item") from exc
                if signature in signatures:
                    raise _SchemaViolation(f"{path} must contain unique items")
                signatures.add(signature)
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, child in enumerate(value):
                _validate_schema_value(child, item_schema, root_schema, f"{path}[{index}]")

    if isinstance(value, str):
        minimum_length = schema.get("minLength")
        if isinstance(minimum_length, int) and len(value) < minimum_length:
            raise _SchemaViolation(f"{path} must contain at least {minimum_length} character(s)")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            raise _SchemaViolation(f"{path} does not match required pattern")
        if schema.get("format") == "date-time":
            try:
                parsed = datetime.fromisoformat(
                    value[:-1] + "+00:00" if value.endswith("Z") else value
                )
            except ValueError as exc:
                raise _SchemaViolation(f"{path} must be an ISO-8601 timestamp") from exc
            if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
                raise _SchemaViolation(f"{path} must be a UTC timestamp")
        if schema.get("format") == "uri":
            parsed_uri = urlparse(value)
            if not parsed_uri.scheme or not (parsed_uri.netloc or parsed_uri.path):
                raise _SchemaViolation(f"{path} must be an absolute URI")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise _SchemaViolation(f"{path} must be a finite JSON number")
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            raise _SchemaViolation(f"{path} must be at least {minimum}")
        if isinstance(maximum, (int, float)) and value > maximum:
            raise _SchemaViolation(f"{path} must be at most {maximum}")


def _validate_contract_schema(payload: object, filename: str, label: str) -> None:
    schema = _load_contract_schema(filename)
    try:
        _validate_schema_value(payload, schema, schema, label)
    except _SchemaViolation as exc:
        raise ScienceAgentError(str(exc)) from exc


def _require_mapping(payload: object, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ScienceAgentError(f"{label} must be a JSON object")
    return payload


def _require_nonempty_string(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ScienceAgentError(f"{field} must be a non-empty string")


def _require_string_list(value: object, field: str, *, allow_empty: bool) -> None:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ScienceAgentError(f"{field} must be a list of non-empty strings")
    if not allow_empty and not value:
        raise ScienceAgentError(f"{field} must not be empty")


def _verify_artifact_hash(payload: dict[str, Any], label: str) -> None:
    if "artifact_sha256" not in payload:
        raise ScienceAgentError(f"{label} missing fields: artifact_sha256")
    unhashed = dict(payload)
    supplied = unhashed.pop("artifact_sha256")
    if not isinstance(supplied, str) or not _SHA256_RE.fullmatch(supplied):
        raise ScienceAgentError(f"{label} artifact_sha256 must be a lowercase SHA-256")
    expected = canonical_json_sha256(unhashed)
    if supplied != expected:
        raise ScienceAgentError(f"{label} artifact_sha256 does not match canonical artifact")


def _reject_random_time_series_split(value: object, path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if "split" in str(key).lower() and isinstance(child, str):
                normalized = re.sub(r"[^a-z]+", " ", child.lower())
                if "random" in normalized or "shuffle" in normalized:
                    raise ScienceAgentError(
                        f"{child_path} rejects random time-series split"
                    )
            _reject_random_time_series_split(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_random_time_series_split(child, f"{path}[{index}]")


def _parse_utc_timestamp(value: object, field: str) -> datetime:
    _require_nonempty_string(value, field)
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise ScienceAgentError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ScienceAgentError(f"{field} must be a UTC timestamp")
    return parsed


_SPLIT_METHOD_FIELDS = frozenset(
    {
        "split",
        "split_method",
        "split_strategy",
        "split_type",
        "row_split",
        "row_split_method",
        "row_split_strategy",
        "data_split",
        "data_split_method",
        "data_split_strategy",
        "train_test_split",
        "validation_split",
    }
)
_SHUFFLE_FIELDS = frozenset({"shuffle", "shuffled", "shuffle_rows"})
_FEATURE_NAME_FIELDS = (
    "feature",
    "feature_name",
    "name",
    "transform",
    "transform_name",
    "transformation",
)
_CENTERED_TOKENS = frozenset({"centered", "centred"})
_SMOOTH_TOKENS = frozenset({"smooth", "smoothed", "smoothing"})


def _method_token(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _declares_forbidden_row_split(field: object, value: object) -> bool:
    key = _method_token(field)
    if key in _SHUFFLE_FIELDS:
        return value is True or (
            isinstance(value, str)
            and _method_token(value) in {"true", "yes", "enabled", "shuffle", "shuffled"}
        )
    if key not in _SPLIT_METHOD_FIELDS:
        return False
    if isinstance(value, dict):
        return any(
            _declares_forbidden_row_split("split_strategy", value.get(nested_field))
            for nested_field in ("strategy", "method", "type", "name")
            if nested_field in value
        )
    if not isinstance(value, str):
        return False
    tokens = set(_method_token(value).split("_"))
    if "not" in tokens:
        return False
    return any(
        token.startswith("random") or token.startswith("shuffl")
        for token in tokens
    )


def _declares_centered_13_month_smoothing(record: dict[str, Any]) -> bool:
    for field in _FEATURE_NAME_FIELDS:
        value = record.get(field)
        if not isinstance(value, str):
            continue
        tokens = set(_method_token(value).split("_"))
        has_window = "13m" in tokens or (
            "13" in tokens and bool(tokens & {"month", "months", "monthly"})
        )
        if has_window and tokens & _CENTERED_TOKENS and tokens & _SMOOTH_TOKENS:
            return True

    alignment = _method_token(
        record.get("smoothing_alignment", record.get("alignment", ""))
    )
    window = record.get("smoothing_window_months", record.get("window_months"))
    operation = _method_token(record.get("operation", record.get("transform", "smoothing")))
    return (
        alignment in _CENTERED_TOKENS
        and isinstance(window, int)
        and not isinstance(window, bool)
        and window == 13
        and operation in _SMOOTH_TOKENS
    )


def _parse_method_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def audit_feature_availability(
    rows: list[dict[str, Any]], origin: str
) -> list[dict[str, Any]]:
    """Return feature rows whose declared availability is after the origin."""

    if not isinstance(rows, list):
        raise ScienceAgentError("feature availability rows must be a list")

    def parse(value: object, field: str) -> datetime:
        if not isinstance(value, str) or not value.strip():
            raise ScienceAgentError(f"{field} must be a non-empty ISO-8601 timestamp")
        text = value.strip()
        try:
            parsed = datetime.fromisoformat(
                text[:-1] + "+00:00" if text.endswith("Z") else text
            )
        except ValueError as exc:
            raise ScienceAgentError(f"{field} must be an ISO-8601 timestamp") from exc
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    origin_dt = parse(origin, "origin")
    violations: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ScienceAgentError(
                f"feature availability row {index} must be a JSON object"
            )
        available_at = parse(row.get("available_at"), f"rows[{index}].available_at")
        if available_at > origin_dt:
            violations.append(row)
    return violations


def _audit_experiment_methodology(manifest: dict[str, Any]) -> list[str]:
    """Return explicit time-series methodology violations without blocking accounting."""

    violations: list[str] = []

    def walk(value: object, path: str, inherited_origin: object = None) -> None:
        if isinstance(value, dict):
            origin_value = value.get("forecast_origin", inherited_origin)
            for field, child in value.items():
                child_path = f"{path}.{field}"
                if _declares_forbidden_row_split(field, child):
                    violations.append(
                        f"{child_path} declares random or shuffled row split"
                    )

            if _declares_centered_13_month_smoothing(value):
                available_value = value.get("available_at")
                available_at = _parse_method_timestamp(available_value)
                forecast_origin = _parse_method_timestamp(origin_value)
                if available_value is None or origin_value is None:
                    violations.append(
                        f"{path} centered 13-month smoothing requires "
                        "forecast_origin and available_at timestamps"
                    )
                elif available_at is None or forecast_origin is None:
                    violations.append(
                        f"{path} centered 13-month smoothing has invalid "
                        "forecast_origin or available_at timestamp"
                    )
                elif available_at > forecast_origin:
                    violations.append(
                        f"{path} centered 13-month smoothing "
                        "available_at exceeds forecast_origin"
                    )

            for field, child in value.items():
                walk(child, f"{path}.{field}", origin_value)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]", inherited_origin)

    walk(manifest["result"], "result")
    if "provenance" in manifest:
        walk(manifest["provenance"], "provenance")
    return violations


def _validate_research_task_graph(plan: dict[str, Any]) -> None:
    nodes = plan["task_graph"]
    node_by_id: dict[str, dict[str, Any]] = {}
    output_producer: dict[str, str] = {}
    execution_target_owner: dict[tuple[str, int], str] = {}
    data_ids = {str(contract["id"]) for contract in plan["data_contracts"]}
    data_contract_by_id = {
        str(contract["id"]): contract for contract in plan["data_contracts"]
    }
    registered_tools = {
        f"registered:{experiment_id}" for experiment_id in REGISTERED_EXPERIMENTS
    }

    for index, node in enumerate(nodes):
        node_id = str(node["id"])
        if node_id in node_by_id:
            raise ScienceAgentError(f"task_graph has duplicate node id: {node_id}")
        node_by_id[node_id] = node
        if node["tool"] not in registered_tools:
            raise ScienceAgentError(
                f"task_graph node {node_id} must use a registered E0-E8 tool"
            )
        experiment_id = str(node["tool"]).removeprefix("registered:")
        execution_target = (experiment_id, int(node["seed"]))
        previous_owner = execution_target_owner.get(execution_target)
        if previous_owner is not None:
            raise ScienceAgentError(
                "task_graph nodes would write the same immutable experiment target: "
                f"{previous_owner} and {node_id}"
            )
        execution_target_owner[execution_target] = node_id
        budget = node["budget"]
        wall_seconds = budget.get("wall_seconds") if isinstance(budget, dict) else None
        if (
            not isinstance(wall_seconds, (int, float))
            or isinstance(wall_seconds, bool)
            or not math.isfinite(float(wall_seconds))
            or float(wall_seconds) <= 0
        ):
            raise ScienceAgentError(
                f"task_graph node {node_id} requires a positive wall_seconds budget"
            )
        cpu_seconds = budget.get("cpu_seconds") if isinstance(budget, dict) else None
        if cpu_seconds is not None and (
            isinstance(cpu_seconds, bool)
            or not isinstance(cpu_seconds, (int, float))
            or not math.isfinite(float(cpu_seconds))
            or float(cpu_seconds) <= 0
        ):
            raise ScienceAgentError(
                f"task_graph node {node_id} CPU budget must be positive when present"
            )
        if budget.get("gpu_seconds", 0) != 0:
            raise ScienceAgentError(
                f"task_graph node {node_id} cannot request GPU time for a CPU-only registered worker"
            )
        if budget.get("tokens", 0) != 0:
            raise ScienceAgentError(
                f"task_graph node {node_id} cannot request model tokens inside a deterministic experiment"
            )
        if "split_strategy" not in node:
            raise ScienceAgentError(
                f"task_graph node {node_id} requires a preregistered split_strategy"
            )
        if node["tool"] == "registered:E4_extended_hemispheric_calibration":
            semantic_layers = {
                data_contract_by_id[input_id]["semantic_layer"]
                for input_id in node["inputs"]
                if input_id in data_contract_by_id
            }
            if not {"reconstruction", "observation"}.issubset(semantic_layers):
                raise ScienceAgentError(
                    "E4 hemispheric calibration requires separate reconstruction "
                    "and observation input layers"
                )
            criteria = " ".join(str(item) for item in node["success_criteria"])
            if not (
                re.search(r"\boverlap\b", criteria, flags=re.IGNORECASE)
                and re.search(r"\bcalibrat", criteria, flags=re.IGNORECASE)
                and re.search(
                    r"\b(?:uncertainty|tolerance|error|interval)\b",
                    criteria,
                    flags=re.IGNORECASE,
                )
                and re.search(
                    r"\b(?:inconclusive|reject|fail|downgrade|block)\b",
                    criteria,
                    flags=re.IGNORECASE,
                )
                and not re.search(
                    r"\b(?:without|skip|omit|no)\b.{0,24}\boverlap\b",
                    criteria,
                    flags=re.IGNORECASE,
                )
            ):
                raise ScienceAgentError(
                    "E4 hemispheric calibration requires an overlap-calibration "
                    "success criterion with uncertainty and an explicit failure outcome"
                )
        for output in node["outputs"]:
            artifact_path = Path(output)
            if (
                artifact_path.is_absolute()
                or bool(artifact_path.drive)
                or ".." in artifact_path.parts
            ):
                raise ScienceAgentError(
                    f"task_graph node {node_id} has an unsafe output path: {output}"
                )
            if output in data_ids or output in output_producer:
                raise ScienceAgentError(
                    f"task_graph output is not uniquely produced: {output}"
                )
            output_producer[output] = node_id

    for node_id, node in node_by_id.items():
        for dependency in node["depends_on"]:
            if dependency not in node_by_id:
                raise ScienceAgentError(
                    f"task_graph node {node_id} has dangling dependency: {dependency}"
                )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise ScienceAgentError(f"task_graph cycle detected at: {node_id}")
        if node_id in visited:
            return
        visiting.add(node_id)
        for dependency in node_by_id[node_id]["depends_on"]:
            visit(str(dependency))
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in node_by_id:
        visit(node_id)

    ancestor_cache: dict[str, set[str]] = {}

    def ancestors(node_id: str) -> set[str]:
        if node_id not in ancestor_cache:
            result: set[str] = set()
            for dependency in node_by_id[node_id]["depends_on"]:
                dependency_id = str(dependency)
                result.add(dependency_id)
                result.update(ancestors(dependency_id))
            ancestor_cache[node_id] = result
        return ancestor_cache[node_id]

    for node_id, node in node_by_id.items():
        allowed_ancestors = ancestors(node_id)
        for input_reference in node["inputs"]:
            if input_reference in data_ids:
                continue
            producer = output_producer.get(input_reference)
            if producer is None:
                raise ScienceAgentError(
                    f"task_graph node {node_id} has unknown input: {input_reference}"
                )
            if producer not in allowed_ancestors:
                raise ScienceAgentError(
                    f"task_graph node {node_id} input is not produced by a dependency: "
                    f"{input_reference}"
                )


def validate_research_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the frozen ResearchPlan 1.0 envelope and its canonical hash."""

    plan = _require_mapping(payload, "research_plan")
    _validate_contract_schema(
        plan,
        "research_plan_v2.schema.json",
        "research_plan",
    )
    require_fields(plan, _RESEARCH_PLAN_FIELDS, "research_plan")
    if plan["schema_version"] != RESEARCH_PLAN_SCHEMA_VERSION:
        raise ScienceAgentError(
            f"research_plan schema_version must be {RESEARCH_PLAN_SCHEMA_VERSION}"
        )
    _require_nonempty_string(plan["run_id"], "run_id")
    _parse_utc_timestamp(plan["created_at"], "created_at")
    _require_nonempty_string(plan["research_question"], "research_question")
    _require_nonempty_string(plan["claim_boundary"], "claim_boundary")
    if not isinstance(plan["data_contracts"], list) or not plan["data_contracts"]:
        raise ScienceAgentError("data_contracts must be a non-empty source reference list")
    if not isinstance(plan["task_graph"], list) or not plan["task_graph"]:
        raise ScienceAgentError("task_graph must be a non-empty artifact-producing list")
    _require_string_list(plan["primary_metrics"], "primary_metrics", allow_empty=False)
    _require_string_list(
        plan["counter_evidence_paths"],
        "counter_evidence_paths",
        allow_empty=False,
    )
    _require_string_list(plan["stop_rules"], "stop_rules", allow_empty=False)
    _reject_embedded_overclaims(plan, "research_plan")
    _validate_research_task_graph(plan)
    _reject_random_time_series_split(plan)

    unhashed = dict(plan)
    unhashed.pop("artifact_sha256", None)
    supplied = unhashed.pop("frozen_hash")
    if not isinstance(supplied, str) or not _SHA256_RE.fullmatch(supplied):
        raise ScienceAgentError("frozen_hash must be a lowercase SHA-256")
    expected = canonical_json_sha256(unhashed)
    if supplied != expected:
        raise ScienceAgentError("frozen_hash does not match canonical plan")
    _verify_artifact_hash(plan, "research_plan")
    return payload


def validate_experiment_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the ExperimentManifest 1.0 envelope and status invariants."""

    manifest = _require_mapping(payload, "experiment_manifest")
    _validate_contract_schema(
        manifest,
        "experiment_manifest_v2.schema.json",
        "experiment_manifest",
    )
    require_fields(manifest, _EXPERIMENT_MANIFEST_FIELDS, "experiment_manifest")
    if manifest["schema_version"] != EXPERIMENT_MANIFEST_SCHEMA_VERSION:
        raise ScienceAgentError(
            "experiment_manifest schema_version must be "
            f"{EXPERIMENT_MANIFEST_SCHEMA_VERSION}"
        )
    for field in ("run_id", "node_id", "experiment_id"):
        _require_nonempty_string(manifest[field], field)
    if manifest["parent_id"] is not None:
        _require_nonempty_string(manifest["parent_id"], "parent_id")
    if isinstance(manifest["seed"], bool) or not isinstance(manifest["seed"], int):
        raise ScienceAgentError("seed must be an integer")
    if manifest["seed"] < 0:
        raise ScienceAgentError("seed must be non-negative")
    status = manifest["status"]
    if status not in EXPERIMENT_STATUSES:
        raise ScienceAgentError(
            f"experiment_manifest status must be one of: {', '.join(sorted(EXPERIMENT_STATUSES))}"
        )
    started_at = _parse_utc_timestamp(manifest["started_at"], "started_at")
    finished_at = _parse_utc_timestamp(manifest["finished_at"], "finished_at")
    if finished_at < started_at:
        raise ScienceAgentError("finished_at must not precede started_at")
    if not isinstance(manifest["result"], dict):
        raise ScienceAgentError("result must be a JSON object")
    if not isinstance(manifest["data_sources"], list):
        raise ScienceAgentError("data_sources must be a source reference list")
    if status != "failed" and not manifest["data_sources"]:
        raise ScienceAgentError(
            "data_sources must be non-empty unless source accounting itself failed"
        )
    if not isinstance(manifest["artifacts"], list) or not manifest["artifacts"]:
        raise ScienceAgentError("artifacts must be a non-empty artifact reference list")
    if manifest["error"] is not None and not isinstance(manifest["error"], dict):
        raise ScienceAgentError("error must be null or a JSON object")
    _reject_embedded_overclaims(manifest, "experiment_manifest")
    claim_effect = manifest["claim_effect"]
    if claim_effect not in _CLAIM_EFFECTS_BY_STATUS[status]:
        raise ScienceAgentError(f"claim_effect is inconsistent with status {status}")
    methodology_violations = _audit_experiment_methodology(manifest)
    if status in {"passed", "warning"} and methodology_violations:
        raise ScienceAgentError("; ".join(methodology_violations))
    _verify_artifact_hash(manifest, "experiment_manifest")
    return payload


_HYPOTHESIS_SCORE_CRITERIA = (
    "data_grounding",
    "mechanism_coherence",
    "testability",
    "falsifiability",
    "novelty",
    "reproducibility",
    "uncertainty",
)

_EVOLUTION_STRATEGIES = frozenset(
    {"grounding", "simplification", "combination", "divergence"}
)
_TOURNAMENT_SENTINELS = frozenset({"tie", "position_sensitive"})
_OVERCLAIM_RULES: tuple[tuple[re.Pattern[str], tuple[re.Pattern[str], ...]], ...] = (
    (
        re.compile(r"\bofficial\b.{0,48}\bforecast\b", flags=re.IGNORECASE),
        (
            re.compile(
                r"\b(?:not|never)\s+(?:an?\s+)?official\b.{0,32}\bforecast\b",
                flags=re.IGNORECASE,
            ),
            re.compile(
                r"\b(?:cannot|can't|must\s+not)\s+be\s+(?:an?\s+)?official\b.{0,32}\bforecast\b",
                flags=re.IGNORECASE,
            ),
            re.compile(
                r"\b(?:does|do|did)\s+not\s+(?:constitute|establish|provide|issue|represent|make)\b"
                r"[^.;。；]{0,24}\b(?:an?\s+)?official\b[^.;。；]{0,32}\bforecast\b",
                flags=re.IGNORECASE,
            ),
        ),
    ),
    (
        re.compile(r"\bforecast\b.{0,48}\bofficial\b", flags=re.IGNORECASE),
        (
            re.compile(
                r"\bforecast\b.{0,24}\b(?:is|are|was|were)\s+(?:not|never)\s+official\b",
                flags=re.IGNORECASE,
            ),
        ),
    ),
    (
        re.compile(
            r"\bprov(?:e|es|ed|en|ing)\b.{0,80}\borigin\b",
            flags=re.IGNORECASE,
        ),
        (
            re.compile(
                r"\b(?:does|do|did|has|have|had)\s+not\s+prov(?:e|es|ed|en|ing)\b.{0,80}\borigin\b",
                flags=re.IGNORECASE,
            ),
            re.compile(
                r"\b(?:cannot|can't|never|must\s+not)\s+prov(?:e|ed|ing)?\b.{0,80}\borigin\b",
                flags=re.IGNORECASE,
            ),
        ),
    ),
    (
        re.compile(
            r"\borigin\b.{0,80}\b(?:prov(?:e|es|ed|en|ing)|proof)\b",
            flags=re.IGNORECASE,
        ),
        (
            re.compile(
                r"\borigin\b.{0,48}\b(?:is|was|has)\s+not\b.{0,24}\b(?:proven|proved|proof)\b",
                flags=re.IGNORECASE,
            ),
            re.compile(
                r"\borigin\b.{0,48}\b(?:cannot|can't|must\s+not)\s+be\b.{0,16}\bproven\b",
                flags=re.IGNORECASE,
            ),
        ),
    ),
    (
        re.compile(
            r"\b(?:f10\.?7|sunspot(?:\s+number)?)\b.{0,80}"
            r"\b(?:direct(?:ly)?\s+measur(?:e|es|ed|ement)|measur(?:e|es|ed)\s+directly)\b"
            r".{0,48}\b(?:internal|dynamo|magnetic\s+field)\b",
            flags=re.IGNORECASE,
        ),
        (
            re.compile(
                r"\b(?:f10\.?7|sunspot(?:\s+number)?)\b.{0,24}"
                r"\b(?:does|do|did)\s+not\s+(?:directly\s+)?measur\w*"
                r"[^,.;，。；]{0,48}\b(?:internal|dynamo|magnetic\s+field)\b",
                flags=re.IGNORECASE,
            ),
            re.compile(
                r"\b(?:f10\.?7|sunspot(?:\s+number)?)\b.{0,24}"
                r"\b(?:cannot|can't|must\s+not)\s+(?:directly\s+)?measur\w*"
                r"[^,.;，。；]{0,48}\b(?:internal|dynamo|magnetic\s+field)\b",
                flags=re.IGNORECASE,
            ),
            re.compile(
                r"\b(?:f10\.?7|sunspot(?:\s+number)?)\b.{0,24}"
                r"\bis\s+not\s+(?:an?\s+)?direct\s+measure\w*"
                r"[^,.;，。；]{0,48}\b(?:internal|dynamo|magnetic\s+field)\b",
                flags=re.IGNORECASE,
            ),
        ),
    ),
    (
        re.compile(
            r"\bcorrelation\b.{0,64}"
            r"\b(?:prov(?:e|es|ed)|establish(?:es|ed)?|demonstrat(?:e|es|ed))\b"
            r".{0,32}\bcaus(?:e|al|ation)\b",
            flags=re.IGNORECASE,
        ),
        (
            re.compile(
                r"\bcorrelation\b.{0,32}\b(?:does|do|did)\s+not\s+"
                r"(?:prov(?:e|es|ed)|establish|demonstrate)\b"
                r"[^,.;，。；]{0,32}\b(?:cause|causal|causation)\b",
                flags=re.IGNORECASE,
            ),
            re.compile(
                r"\bcorrelation\b.{0,32}\b(?:cannot|can't|never)\s+"
                r"(?:prov(?:e|ed)?|establish|demonstrate)\b"
                r"[^,.;，。；]{0,32}\b(?:cause|causal|causation)\b",
                flags=re.IGNORECASE,
            ),
        ),
    ),
    (
        re.compile(
            r"\bNOAA\b.{0,96}\bCycle\s*25\b.{0,96}"
            r"\b(?:official\s+)?Cycle\s*26\s+forecast\b",
            flags=re.IGNORECASE,
        ),
        (
            re.compile(
                r"\bNOAA\b.{0,96}\bCycle\s*25\b.{0,48}"
                r"\b(?:is|are|was|were)\s+not\s+(?:an?\s+)?(?:official\s+)?"
                r"Cycle\s*26\s+forecast\b",
                flags=re.IGNORECASE,
            ),
            re.compile(
                r"\bNOAA\b.{0,96}\bCycle\s*25\b.{0,48}"
                r"\b(?:cannot|can't|must\s+not)\s+be\s+(?:an?\s+)?"
                r"(?:official\s+)?Cycle\s*26\s+forecast\b",
                flags=re.IGNORECASE,
            ),
        ),
    ),
    (
        re.compile(r"官方.{0,24}预测|预测.{0,24}官方"),
        (
            re.compile(
                r"(?:不是(?!没有)|并非(?!没有)|不能视为|不得称为|不可称为)"
                r"[^，。；,.;]{0,16}官方[^，。；,.;]{0,24}预测"
            ),
            re.compile(
                r"官方[^，。；,.;]{0,24}预测[^，。；,.;]{0,12}"
                r"(?:并不存在|并非如此|不成立)"
            ),
        ),
    ),
    (
        re.compile(
            r"(?:已经|已)?证明.{0,36}(?:起源|成因)|"
            r"(?:起源|成因).{0,36}(?:已被|已经|已)?证明"
        ),
        (
            re.compile(
                r"(?:尚未|没有|未能|并未|不能|无法)证明"
                r"[^，。；,.;]{0,36}(?:起源|成因)"
            ),
            re.compile(
                r"(?:起源|成因)[^，。；,.;]{0,24}"
                r"(?:尚未|没有|并未|不能|无法)[^，。；,.;]{0,12}证明"
            ),
        ),
    ),
)


def _hypothesis_lexemes(text: str) -> tuple[str, ...]:
    if not isinstance(text, str) or not text.strip():
        raise ScienceAgentError("hypothesis text must be a non-empty string")
    normalized = unicodedata.normalize("NFKC", text).casefold()
    lexemes = tuple(re.findall(r"\w+", normalized, flags=re.UNICODE))
    if not lexemes:
        raise ScienceAgentError("hypothesis text must contain lexical content")
    return lexemes


def _reject_allowed_overclaims(boundary: object, label: str) -> None:
    if isinstance(boundary, dict):
        statements = boundary.get("allowed", [])
    else:
        statements = [boundary]
    for statement in statements:
        if not isinstance(statement, str):
            continue
        normalized = unicodedata.normalize("NFKC", statement)
        for pattern, safe_patterns in _OVERCLAIM_RULES:
            for match in pattern.finditer(normalized):
                context_start = max(0, match.start() - 96)
                context = normalized[
                    context_start : min(len(normalized), match.end() + 48)
                ]
                explicitly_negated = any(
                    context_start + safe_match.start() <= match.start()
                    and context_start + safe_match.end() >= match.end()
                    for safe in safe_patterns
                    for safe_match in safe.finditer(context)
                )
                if explicitly_negated:
                    continue
                raise ScienceAgentError(
                    f"{label} contains an allowed overclaim: {match.group(0)}"
                )


def _reject_embedded_overclaims(value: object, label: str) -> None:
    """Reject overclaims in every scientific free-text field of an artifact.

    ``claim_boundary.forbidden`` is the sole exception: those entries describe
    claims that the artifact explicitly prohibits, so treating their quoted
    text as an asserted claim would invert the contract's meaning.
    """

    if isinstance(value, dict):
        for key, child in value.items():
            child_label = f"{label}.{key}"
            if key == "forbidden" and label.rsplit(".", 1)[-1] == "claim_boundary":
                continue
            _reject_embedded_overclaims(child, child_label)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_embedded_overclaims(child, f"{label}[{index}]")
        return
    if isinstance(value, str):
        _reject_allowed_overclaims(value, label)


def _mechanism_signature(card: dict[str, Any]) -> str:
    graph = card["mechanism_graph"]
    nodes_by_id: dict[str, dict[str, str]] = {}
    for node in graph["nodes"]:
        nodes_by_id[str(node["id"])] = {
            "label": " ".join(_hypothesis_lexemes(str(node["label"]))),
            "layer": str(node["layer"]),
        }
    nodes = sorted(
        nodes_by_id.values(), key=lambda node: (node["layer"], node["label"])
    )
    edges = sorted(
        (
            {
                "source": nodes_by_id[str(edge["source"])],
                "target": nodes_by_id[str(edge["target"])],
                "relation": " ".join(
                    _hypothesis_lexemes(str(edge["relation"]))
                ),
            }
            for edge in graph["edges"]
        ),
        key=lambda edge: canonical_json_sha256(edge),
    )
    return canonical_json_sha256({"nodes": nodes, "edges": edges})


def normalize_hypothesis_text(text: str) -> set[str]:
    """Return deterministic lexical features for English and CJK hypotheses."""

    lexemes = _hypothesis_lexemes(text)
    normalized = " ".join(lexemes)
    tokens = set(lexemes)
    for segment in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]+", normalized):
        tokens.add(segment)
        if len(segment) > 1:
            tokens.update(segment[index : index + 2] for index in range(len(segment) - 1))
    return tokens


def jaccard_text(left: str, right: str) -> float:
    """Compute Jaccard similarity over normalized lexical features."""

    left_tokens = normalize_hypothesis_text(left)
    right_tokens = normalize_hypothesis_text(right)
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union)


def proximity_clusters(
    cards: list[dict[str, Any]], threshold: float = 0.82
) -> list[list[str]]:
    """Cluster near hypotheses by connected components in stable input order."""

    if not isinstance(cards, list):
        raise ScienceAgentError("hypothesis cards must be a list")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        raise ScienceAgentError("proximity threshold must be numeric")
    threshold_value = float(threshold)
    if not math.isfinite(threshold_value) or not 0.0 <= threshold_value <= 1.0:
        raise ScienceAgentError("proximity threshold must be between 0 and 1")

    ids: list[str] = []
    hypotheses: list[str] = []
    seen: set[str] = set()
    for index, card in enumerate(cards):
        if not isinstance(card, dict):
            raise ScienceAgentError(f"hypothesis cards[{index}] must be an object")
        card_id = card.get("id")
        hypothesis = card.get("hypothesis")
        _require_nonempty_string(card_id, f"hypothesis cards[{index}].id")
        _require_nonempty_string(
            hypothesis, f"hypothesis cards[{index}].hypothesis"
        )
        stable_id = str(card_id)
        if stable_id in seen:
            raise ScienceAgentError(f"duplicate hypothesis id: {stable_id}")
        seen.add(stable_id)
        ids.append(stable_id)
        hypotheses.append(str(hypothesis))

    parents = list(range(len(cards)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left_index: int, right_index: int) -> None:
        left_root, right_root = find(left_index), find(right_index)
        if left_root != right_root:
            parents[right_root] = left_root

    for left_index in range(len(cards)):
        for right_index in range(left_index + 1, len(cards)):
            if (
                jaccard_text(hypotheses[left_index], hypotheses[right_index])
                >= threshold_value
            ):
                union(left_index, right_index)

    grouped: dict[int, list[str]] = {}
    for index, card_id in enumerate(ids):
        grouped.setdefault(find(index), []).append(card_id)
    return list(grouped.values())


def _validated_score_vector(card: dict[str, Any]) -> dict[str, float]:
    if not isinstance(card, dict):
        raise ScienceAgentError("hypothesis card must be an object")
    _require_nonempty_string(card.get("id"), "hypothesis card id")
    scores = card.get("scores")
    if not isinstance(scores, dict):
        raise ScienceAgentError(f"hypothesis {card['id']} has no score vector")
    if set(scores) != set(_HYPOTHESIS_SCORE_CRITERIA):
        raise ScienceAgentError(
            f"hypothesis {card['id']} score vector must contain every criterion"
        )
    validated: dict[str, float] = {}
    for criterion in _HYPOTHESIS_SCORE_CRITERIA:
        value = scores[criterion]
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or not 0.0 <= float(value) <= 1.0
        ):
            raise ScienceAgentError(
                f"hypothesis {card['id']} score {criterion} must be finite in [0, 1]"
            )
        validated[criterion] = float(value)
    return validated


def _is_qualified_hypothesis_evidence(evidence: dict[str, Any]) -> bool:
    return (
        evidence["kind"] == "source" and evidence["status"] == "verified"
    ) or (
        evidence["kind"] == "artifact"
        and evidence["status"] in {"passed", "warning"}
    )


def _is_qualified_hypothesis_counter_evidence(evidence: dict[str, Any]) -> bool:
    return (
        evidence["kind"] == "source" and evidence["status"] == "verified"
    ) or (
        evidence["kind"] == "artifact"
        and evidence["status"] in EXPERIMENT_STATUSES
    )


def score_hypothesis_pair(
    left: dict[str, Any], right: dict[str, Any], tolerance: float = 1e-12
) -> str:
    """Choose by criterion wins, avoiding a single reward-hackable scalar score."""

    if not isinstance(left, dict) or not isinstance(right, dict):
        raise ScienceAgentError("hypothesis cards must be objects")
    left_id, right_id = str(left.get("id", "")), str(right.get("id", ""))
    _require_nonempty_string(left_id, "left hypothesis id")
    _require_nonempty_string(right_id, "right hypothesis id")
    if left_id == right_id:
        raise ScienceAgentError("pairwise hypotheses must have distinct ids")
    if (
        not isinstance(tolerance, (int, float))
        or isinstance(tolerance, bool)
        or not math.isfinite(float(tolerance))
    ):
        raise ScienceAgentError("pairwise tolerance must be finite")
    if float(tolerance) < 0:
        raise ScienceAgentError("pairwise tolerance must not be negative")
    left_scores = _validated_score_vector(left)
    right_scores = _validated_score_vector(right)
    left_wins = sum(
        left_scores[key] > right_scores[key] + float(tolerance)
        for key in _HYPOTHESIS_SCORE_CRITERIA
    )
    right_wins = sum(
        right_scores[key] > left_scores[key] + float(tolerance)
        for key in _HYPOTHESIS_SCORE_CRITERIA
    )
    if left_wins > right_wins:
        return left_id
    if right_wins > left_wins:
        return right_id
    return "tie"


def order_balanced_tournament(
    cards: list[dict[str, Any]],
    pair_judge: Callable[[dict[str, Any], dict[str, Any]], str],
) -> dict[str, Any]:
    """Judge every pair in both orders and quarantine position-sensitive results."""

    if not isinstance(cards, list) or len(cards) < 2:
        raise ScienceAgentError("order-balanced tournament requires at least two cards")
    if not callable(pair_judge):
        raise ScienceAgentError("pair_judge must be callable")
    ids: list[str] = []
    for index, card in enumerate(cards):
        if not isinstance(card, dict):
            raise ScienceAgentError(f"tournament card[{index}] must be an object")
        _require_nonempty_string(card.get("id"), f"tournament card[{index}].id")
        card_id = str(card["id"])
        if card_id in _TOURNAMENT_SENTINELS:
            raise ScienceAgentError(f"reserved tournament hypothesis id: {card_id}")
        if card_id in ids:
            raise ScienceAgentError(f"duplicate hypothesis id: {card_id}")
        ids.append(card_id)

    ordered_cards = sorted(cards, key=lambda card: str(card["id"]))
    ordered_ids = [str(card["id"]) for card in ordered_cards]
    ratings = {card_id: 1200.0 for card_id in ordered_ids}
    matches: list[dict[str, Any]] = []
    position_bias_count = 0
    match_number = 0
    for left_index, left in enumerate(ordered_cards):
        for right in ordered_cards[left_index + 1 :]:
            match_number += 1
            left_id, right_id = str(left["id"]), str(right["id"])
            first = pair_judge(left, right)
            second = pair_judge(right, left)
            allowed = {left_id, right_id, "tie"}
            if first not in allowed or second not in allowed:
                raise ScienceAgentError("pair judge returned an invalid winner id")
            position_bias = first != second
            if position_bias:
                winner = "position_sensitive"
                position_bias_count += 1
            else:
                winner = first

            match_id = f"M{match_number:03d}_{left_id}_{right_id}"
            matches.append(
                {
                    "match_id": match_id,
                    "left": left_id,
                    "right": right_id,
                    "first_order_winner": first,
                    "reverse_order_winner": second,
                    "winner": winner,
                    "position_bias": position_bias,
                }
            )
            if winner in ratings:
                loser = right_id if winner == left_id else left_id
                expected = 1.0 / (
                    1.0 + 10.0 ** ((ratings[loser] - ratings[winner]) / 400.0)
                )
                delta = 32.0 * (1.0 - expected)
                ratings[winner] += delta
                ratings[loser] -= delta

    return {
        "initial_elo": 1200.0,
        "k_factor": 32.0,
        "matches": matches,
        "ratings": {key: round(value, 6) for key, value in ratings.items()},
        "position_bias_count": position_bias_count,
    }


def validate_hypothesis_portfolio(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the versioned hypothesis-portfolio envelope."""

    portfolio = _require_mapping(payload, "hypothesis_portfolio")
    _validate_contract_schema(
        portfolio,
        "hypothesis_portfolio_v2.schema.json",
        "hypothesis_portfolio",
    )
    require_fields(portfolio, _HYPOTHESIS_PORTFOLIO_FIELDS, "hypothesis_portfolio")
    if portfolio["schema_version"] != HYPOTHESIS_PORTFOLIO_SCHEMA_VERSION:
        raise ScienceAgentError(
            "hypothesis_portfolio schema_version must be "
            f"{HYPOTHESIS_PORTFOLIO_SCHEMA_VERSION}"
        )
    _require_nonempty_string(portfolio["run_id"], "run_id")
    _require_nonempty_string(portfolio["portfolio_id"], "portfolio_id")
    _parse_utc_timestamp(portfolio["created_at"], "created_at")
    _require_string_list(portfolio["source_ids"], "source_ids", allow_empty=False)
    _require_string_list(
        portfolio["artifact_paths"], "artifact_paths", allow_empty=False
    )
    _require_string_list(
        portfolio["experiment_ids"], "experiment_ids", allow_empty=False
    )
    unknown_experiments = sorted(
        set(portfolio["experiment_ids"]) - set(REGISTERED_EXPERIMENTS)
    )
    if unknown_experiments:
        raise ScienceAgentError(
            "hypothesis_portfolio references an unregistered experiment: "
            + ", ".join(unknown_experiments)
        )
    _reject_embedded_overclaims(portfolio, "hypothesis_portfolio")
    status = portfolio["status"]
    if status not in HYPOTHESIS_PORTFOLIO_STATUSES:
        raise ScienceAgentError(
            "hypothesis_portfolio status must be one of: "
            f"{', '.join(sorted(HYPOTHESIS_PORTFOLIO_STATUSES))}"
        )
    hypotheses = portfolio["hypotheses"]
    if not isinstance(hypotheses, list) or not hypotheses:
        raise ScienceAgentError("hypotheses must be a non-empty list")
    seen_ids: set[str] = set()
    seen_content: dict[tuple[str, ...], str] = {}
    for index, card in enumerate(hypotheses):
        if not isinstance(card, dict):
            raise ScienceAgentError(f"hypotheses[{index}] must be a JSON object")
        _require_nonempty_string(card.get("id"), f"hypotheses[{index}].id")
        card_id = str(card["id"])
        if card_id in _TOURNAMENT_SENTINELS:
            raise ScienceAgentError(f"reserved tournament hypothesis id: {card_id}")
        if card_id in seen_ids:
            raise ScienceAgentError(f"duplicate hypothesis id: {card_id}")
        seen_ids.add(card_id)
        fingerprint = _hypothesis_lexemes(card["hypothesis"])
        if fingerprint in seen_content:
            raise ScienceAgentError(
                "duplicate hypothesis content: "
                f"{seen_content[fingerprint]} and {card_id}"
            )
        seen_content[fingerprint] = card_id
        if (
            card["generation_strategy"] in _EVOLUTION_STRATEGIES
            and not card["lineage"]
        ):
            raise ScienceAgentError(
                f"hypothesis {card_id} evolution strategy requires lineage"
            )
        if status == "calibrated":
            if "scores" not in card or "tournament" not in card:
                raise ScienceAgentError(
                    "calibrated hypotheses require scores and tournament evidence"
                )
            _validated_score_vector(card)
            if card["tournament"]["position_bias"] and card["decision"] not in {
                "downgrade",
                "discard",
            }:
                raise ScienceAgentError(
                    "calibrated position-sensitive hypotheses must be downgraded "
                    "or discarded"
                )

    lineage_by_id = {
        str(card["id"]): [str(parent_id) for parent_id in card["lineage"]]
        for card in hypotheses
    }
    for card_id, parent_ids in lineage_by_id.items():
        for parent_id in parent_ids:
            if parent_id not in seen_ids:
                raise ScienceAgentError(
                    f"hypothesis {card_id} has dangling lineage reference: {parent_id}"
                )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit_lineage(card_id: str) -> None:
        if card_id in visiting:
            raise ScienceAgentError(f"hypothesis lineage cycle detected at: {card_id}")
        if card_id in visited:
            return
        visiting.add(card_id)
        for parent_id in lineage_by_id[card_id]:
            visit_lineage(parent_id)
        visiting.remove(card_id)
        visited.add(card_id)

    for hypothesis_id in lineage_by_id:
        visit_lineage(hypothesis_id)

    source_ids = set(portfolio["source_ids"])
    artifact_paths = set(portfolio["artifact_paths"])
    experiment_ids = set(portfolio["experiment_ids"])
    for index, card in enumerate(hypotheses):
        graph = card["mechanism_graph"]
        node_ids: set[str] = set()
        for node in graph["nodes"]:
            node_id = node["id"]
            if node_id in node_ids:
                raise ScienceAgentError(
                    f"hypotheses[{index}] has duplicate mechanism node id: {node_id}"
                )
            node_ids.add(node_id)
            node_label = unicodedata.normalize("NFKC", str(node["label"])).casefold()
            if re.search(r"\bf10\.?7\b", node_label) and node["layer"] != "proxy":
                raise ScienceAgentError(
                    f"hypotheses[{index}] F10.7 mechanism node must use the proxy layer"
                )
        for edge in graph["edges"]:
            if edge["source"] not in node_ids:
                raise ScienceAgentError(
                    "hypotheses[{}] has dangling mechanism edge source: {}".format(
                        index, edge["source"]
                    )
                )
            if edge["target"] not in node_ids:
                raise ScienceAgentError(
                    "hypotheses[{}] has dangling mechanism edge target: {}".format(
                        index, edge["target"]
                    )
                )

        for prediction in card["measurable_predictions"]:
            _require_nonempty_string(
                prediction["threshold_or_interval"],
                f"hypotheses[{index}].prediction.threshold_or_interval",
            )
            _require_nonempty_string(
                prediction["time_window"],
                f"hypotheses[{index}].prediction.time_window",
            )
            target = prediction["target_experiment"]
            if target not in experiment_ids:
                raise ScienceAgentError(
                    f"hypotheses[{index}] has dangling target experiment: {target}"
                )
        for evidence_field in ("supporting_evidence", "counter_evidence"):
            qualifier = (
                _is_qualified_hypothesis_evidence
                if evidence_field == "supporting_evidence"
                else _is_qualified_hypothesis_counter_evidence
            )
            qualified = any(
                qualifier(evidence)
                for evidence in card[evidence_field]
            )
            if not qualified:
                requirement = (
                    "one verified source or passed/warning artifact"
                    if evidence_field == "supporting_evidence"
                    else "one verified source or immutable experiment artifact"
                )
                raise ScienceAgentError(
                    f"hypotheses[{index}].{evidence_field} must include at least "
                    f"{requirement}"
                )
            if evidence_field == "supporting_evidence":
                for evidence in card[evidence_field]:
                    if not _is_qualified_hypothesis_evidence(evidence):
                        raise ScienceAgentError(
                            f"hypotheses[{index}].supporting_evidence cannot use "
                            "failed, quarantined, or inconclusive evidence as support"
                        )
            else:
                for evidence in card[evidence_field]:
                    if not _is_qualified_hypothesis_counter_evidence(evidence):
                        raise ScienceAgentError(
                            f"hypotheses[{index}].counter_evidence must use verified "
                            "sources or immutable experiment artifacts"
                        )
            for evidence in card[evidence_field]:
                reference = evidence["ref"]
                if evidence["kind"] == "source" and reference not in source_ids:
                    raise ScienceAgentError(
                        f"hypotheses[{index}] has dangling source reference: {reference}"
                    )
                if evidence["kind"] == "artifact" and reference not in artifact_paths:
                    raise ScienceAgentError(
                        f"hypotheses[{index}] has dangling artifact reference: {reference}"
                    )

    cards_by_id = {str(card["id"]): card for card in hypotheses}
    for cluster in proximity_clusters(hypotheses):
        signatures: dict[str, str] = {}
        for card_id in cluster:
            signature = _mechanism_signature(cards_by_id[card_id])
            if signature in signatures:
                raise ScienceAgentError(
                    "near-duplicate mechanism: "
                    f"{signatures[signature]} and {card_id}"
                )
            signatures[signature] = card_id
    _verify_artifact_hash(portfolio, "hypothesis_portfolio")
    return payload


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_generated_segment(value: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "_", value.strip(), flags=re.UNICODE).strip("._")
    return cleaned or "science_task"


class RunStore:
    """Append-only directory store for one immutable JSON artifact per path."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise ScienceAgentError(f"run store root is not a directory: {self.root}")

    @staticmethod
    def _validate_run_id(run_id: object) -> str:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ScienceAgentError("run_id must be a non-empty string")
        candidate = Path(run_id)
        if (
            candidate.is_absolute()
            or bool(candidate.drive)
            or candidate.name != run_id
            or run_id in {".", ".."}
        ):
            raise ScienceAgentError("run_id must be one safe path segment")
        return run_id

    def create_run(self, task: str, run_id: str | None = None) -> dict[str, Any]:
        _require_nonempty_string(task, "task")
        stable = run_id or (
            f"{_safe_generated_segment(task)}_"
            f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_"
            f"{uuid.uuid4().hex[:8]}"
        )
        stable = self._validate_run_id(stable)
        run_dir = (self.root / stable).resolve()
        if run_dir.parent != self.root:
            raise ScienceAgentError("run_id resolves outside run store")
        try:
            run_dir.mkdir(parents=False, exist_ok=False)
        except FileExistsError as exc:
            raise ScienceAgentError(f"run already exists: {stable}") from exc

        manifest: dict[str, Any] = {
            "schema_version": "b3-science-run-v2",
            "run_id": stable,
            "task": task,
            "task_sha256": canonical_json_sha256(task),
            "research_question_binding": "exact",
            "created_at": _utc_now(),
        }
        manifest["artifact_sha256"] = canonical_json_sha256(manifest)
        try:
            self._write_new(run_dir / "run_manifest.json", manifest)
        except BaseException:
            try:
                run_dir.rmdir()
            except OSError:
                pass
            raise
        return manifest

    def write_artifact(
        self,
        run_id: str,
        relative_path: str,
        payload: dict[str, Any],
    ) -> Path:
        artifact = _require_mapping(payload, "artifact")
        path = self._safe_path(run_id, relative_path)
        unhashed = dict(artifact)
        supplied = unhashed.pop("artifact_sha256", None)
        expected = canonical_json_sha256(unhashed)
        if supplied is not None and supplied != expected:
            raise ScienceAgentError("artifact_sha256 does not match canonical artifact")
        stored = dict(unhashed)
        stored["artifact_sha256"] = expected
        self._write_new(path, stored)
        return path

    def write_artifact_bundle(
        self,
        run_id: str,
        relative_directory: str,
        payloads: dict[str, dict[str, Any]],
    ) -> dict[str, Path]:
        """Publish a new directory of immutable artifacts as one unit."""

        if not isinstance(payloads, dict) or not payloads:
            raise ScienceAgentError("artifact bundle must be a non-empty mapping")
        directory = self._safe_path(run_id, relative_directory)
        run_dir = (self.root / self._validate_run_id(run_id)).resolve()
        if directory.parent == run_dir and directory.name == "run_manifest.json":
            raise ScienceAgentError("artifact bundle must target a directory")
        if directory.exists():
            raise ScienceAgentError(f"artifact bundle is immutable: {directory.name}")
        directory.parent.mkdir(parents=True, exist_ok=True)
        staging = directory.with_name(f".{directory.name}.{uuid.uuid4().hex}.tmp")
        stored_payloads: dict[str, dict[str, Any]] = {}
        for name, payload in payloads.items():
            if not isinstance(name, str) or Path(name).name != name or name in {".", ".."}:
                raise ScienceAgentError("artifact bundle names must be safe file names")
            artifact = _require_mapping(payload, f"artifact bundle {name}")
            unhashed = dict(artifact)
            supplied = unhashed.pop("artifact_sha256", None)
            expected = canonical_json_sha256(unhashed)
            if supplied is not None and supplied != expected:
                raise ScienceAgentError(
                    f"artifact_sha256 does not match canonical artifact: {name}"
                )
            stored_payloads[name] = {**unhashed, "artifact_sha256": expected}
        try:
            staging.mkdir(parents=False, exist_ok=False)
            for name, payload in stored_payloads.items():
                self._write_new(staging / name, payload)
            os.rename(staging, directory)
        except FileExistsError as exc:
            raise ScienceAgentError(
                f"artifact bundle is immutable: {directory.name}"
            ) from exc
        except OSError as exc:
            raise ScienceAgentError(
                f"could not publish artifact bundle: {directory}"
            ) from exc
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        return {name: directory / name for name in stored_payloads}

    def read_artifact(self, run_id: str, relative_path: str) -> dict[str, Any]:
        path = self._safe_path(run_id, relative_path)
        if not path.is_file():
            raise ScienceAgentError(f"artifact not found: {relative_path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ScienceAgentError(f"artifact is not valid UTF-8 JSON: {relative_path}") from exc
        artifact = _require_mapping(payload, "artifact")
        unhashed = dict(artifact)
        supplied = unhashed.pop("artifact_sha256", None)
        if not isinstance(supplied, str) or supplied != canonical_json_sha256(unhashed):
            raise ScienceAgentError(f"artifact hash mismatch: {relative_path}")
        return artifact

    def _safe_path(self, run_id: str, relative_path: str) -> Path:
        stable = self._validate_run_id(run_id)
        run_dir = (self.root / stable).resolve()
        if run_dir.parent != self.root or not run_dir.is_dir():
            raise ScienceAgentError(f"run does not exist: {stable}")
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ScienceAgentError("artifact path must be a non-empty relative path")
        relative = Path(relative_path)
        if relative.is_absolute() or bool(relative.drive):
            raise ScienceAgentError("artifact path resolves outside run directory")
        path = (run_dir / relative).resolve()
        try:
            path.relative_to(run_dir)
        except ValueError as exc:
            raise ScienceAgentError("artifact path resolves outside run directory") from exc
        if path == run_dir:
            raise ScienceAgentError("artifact path must name a file inside run directory")
        return path

    @staticmethod
    def _write_new(path: Path, payload: dict[str, Any]) -> None:
        try:
            serialized = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            ) + "\n"
        except (TypeError, ValueError) as exc:
            raise ScienceAgentError("artifact payload must contain only finite JSON values") from exc
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ScienceAgentError(f"artifact is immutable: {path.name}") from exc
        except OSError as exc:
            raise ScienceAgentError(f"could not write artifact: {path}") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _clone_json_mapping(payload: object, label: str) -> dict[str, Any]:
    """Deep-copy one finite JSON object before deterministic envelope mutation."""

    source = _require_mapping(payload, label)
    try:
        serialized = json.dumps(source, ensure_ascii=False, allow_nan=False)
        cloned = json.loads(serialized)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ScienceAgentError(f"{label} must contain only finite JSON values") from exc
    return _require_mapping(cloned, label)


def _reject_agent_owned_envelope(
    draft: dict[str, Any], fields: tuple[str, ...], label: str
) -> None:
    supplied = sorted(field for field in fields if field in draft)
    if supplied:
        raise ScienceAgentError(
            f"{label} deterministic envelope fields must be omitted: "
            + ", ".join(supplied)
        )


def submit_research_plan_draft(
    store: RunStore,
    run_id: str,
    draft: dict[str, Any],
) -> dict[str, Any]:
    """Validate, freeze, hash, and immutably persist one planner-owned draft."""

    run_manifest = store.read_artifact(run_id, "run_manifest.json")
    if run_manifest.get("run_id") != run_id:
        raise ScienceAgentError("run manifest does not match requested run_id")
    plan = _clone_json_mapping(draft, "research_plan_draft")
    _reject_agent_owned_envelope(
        plan,
        ("run_id", "created_at", "status", "frozen_hash", "artifact_sha256"),
        "research_plan_draft",
    )
    task = run_manifest.get("task")
    if run_manifest.get("research_question_binding") == "exact":
        if not isinstance(task, str) or not task.strip():
            raise ScienceAgentError("run manifest task binding is invalid")
        if run_manifest.get("task_sha256") != canonical_json_sha256(task):
            raise ScienceAgentError("run manifest task hash does not match task")
        if plan.get("research_question") != task:
            raise ScienceAgentError(
                "research_plan_draft research_question must exactly match the initialized task"
            )
    plan["run_id"] = run_id
    plan["created_at"] = _utc_now()
    plan["status"] = "frozen"
    plan["frozen_hash"] = canonical_json_sha256(plan)
    plan["artifact_sha256"] = canonical_json_sha256(plan)
    validate_research_plan(plan)
    store.write_artifact(run_id, "research_plan.json", plan)
    return store.read_artifact(run_id, "research_plan.json")


def _calibrate_hypothesis_cards(cards: object) -> list[dict[str, Any]]:
    if not isinstance(cards, list) or not cards:
        raise ScienceAgentError("HypothesisPortfolio 1.0 requires hypothesis cards")
    calibrated: list[dict[str, Any]] = []
    for index, card_value in enumerate(cards):
        card = _require_mapping(card_value, f"hypotheses[{index}]")
        if "tournament" in card:
            raise ScienceAgentError(
                "HypothesisCard 1.0 must omit deterministic tournament fields"
            )
        _validated_score_vector(card)
        calibrated.append(card)

    if len(calibrated) == 1:
        card = calibrated[0]
        card["tournament"] = {
            "rating": 1200.0,
            "position_bias": False,
            "match_ids": [],
        }
        return calibrated

    tournament = order_balanced_tournament(calibrated, score_hypothesis_pair)
    for card in calibrated:
        card_id = str(card["id"])
        matches = [
            match
            for match in tournament["matches"]
            if card_id in {match["left"], match["right"]}
        ]
        position_bias = any(match["position_bias"] for match in matches)
        if position_bias and card.get("decision") not in {"downgrade", "discard"}:
            card["decision"] = "downgrade"
        card["tournament"] = {
            "rating": tournament["ratings"][card_id],
            "position_bias": position_bias,
            "match_ids": [match["match_id"] for match in matches],
        }
    return calibrated


def validate_hypothesis_portfolio_against_run(
    store: RunStore,
    run_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Cross-check a valid portfolio against immutable sources and run artifacts."""

    portfolio = validate_hypothesis_portfolio(payload)
    if portfolio.get("run_id") != run_id:
        raise ScienceAgentError("hypothesis portfolio run_id mismatch")
    plan = store.read_artifact(run_id, "research_plan.json")
    validate_research_plan(plan)
    plan_source_ids = {str(item["id"]) for item in plan["data_contracts"]}
    unknown_sources = sorted(set(portfolio["source_ids"]) - plan_source_ids)
    if unknown_sources:
        raise ScienceAgentError(
            "hypothesis portfolio references a source absent from the frozen plan: "
            + ", ".join(unknown_sources)
        )

    artifacts: dict[str, dict[str, Any]] = {}
    for relative_path in portfolio["artifact_paths"]:
        if relative_path == "hypothesis_portfolio.json":
            raise ScienceAgentError("hypothesis portfolio cannot cite itself")
        artifact = store.read_artifact(run_id, relative_path)
        if artifact.get("run_id", run_id) != run_id:
            raise ScienceAgentError(
                f"hypothesis evidence artifact run_id mismatch: {relative_path}"
            )
        experiment_id = artifact.get("experiment_id")
        if (
            experiment_id is not None
            and experiment_id not in portfolio["experiment_ids"]
        ):
            raise ScienceAgentError(
                f"hypothesis evidence experiment is absent from catalog: {relative_path}"
            )
        artifacts[relative_path] = artifact

    for card in portfolio["hypotheses"]:
        for field in ("supporting_evidence", "counter_evidence"):
            for evidence in card[field]:
                if evidence["kind"] != "artifact":
                    continue
                artifact = artifacts[evidence["ref"]]
                recorded_status = artifact.get("status")
                if recorded_status is None:
                    raise ScienceAgentError(
                        f"hypothesis evidence artifact has no status: {evidence['ref']}"
                    )
                if evidence["status"] != recorded_status:
                    raise ScienceAgentError(
                        f"hypothesis evidence status mismatch: {evidence['ref']}"
                    )
    return payload


def submit_hypothesis_portfolio_draft(
    store: RunStore,
    run_id: str,
    draft: dict[str, Any],
) -> dict[str, Any]:
    """Calibrate, cross-check, hash, and persist HypothesisPortfolio 1.0."""

    store.read_artifact(run_id, "run_manifest.json")
    portfolio = _clone_json_mapping(draft, "hypothesis_portfolio_submission")
    _reject_agent_owned_envelope(
        portfolio,
        ("run_id", "created_at", "status", "artifact_sha256"),
        "hypothesis_portfolio_submission",
    )
    portfolio["hypotheses"] = _calibrate_hypothesis_cards(
        portfolio.get("hypotheses")
    )
    portfolio["run_id"] = run_id
    portfolio["created_at"] = _utc_now()
    portfolio["status"] = "calibrated"
    portfolio["artifact_sha256"] = canonical_json_sha256(portfolio)
    validate_hypothesis_portfolio(portfolio)
    validate_hypothesis_portfolio_against_run(store, run_id, portfolio)
    store.write_artifact(run_id, "hypothesis_portfolio.json", portfolio)
    return store.read_artifact(run_id, "hypothesis_portfolio.json")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_source_path(value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(PROJECT_ROOT)
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def _fallback_analysis_source_manifest() -> dict[str, Any]:
    path = PROJECT_ROOT / "b3" / "data" / "raw" / "source_manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        payload = []
    return {"data_manifest": payload if isinstance(payload, list) else []}


def _source_hash_records(
    data_manifest: object,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    hard_failures: list[str] = []
    if not isinstance(data_manifest, list):
        return records, ["analysis data_manifest is not a list"], hard_failures

    for index, source in enumerate(data_manifest):
        if not isinstance(source, dict):
            warnings.append(f"data_manifest[{index}] is not an object")
            continue
        source_id = source.get("id")
        if (
            not isinstance(source_id, str)
            or not source_id.strip()
            or "/" in source_id
            or "\\" in source_id
        ):
            warnings.append(f"data_manifest[{index}] has no safe stable id")
            continue

        path = _safe_source_path(source.get("file"))
        computed_hash: str | None = None
        if path is not None:
            try:
                computed_hash = _sha256_file(path)
            except OSError:
                warnings.append(f"source {source_id} could not be hashed")

        recorded_hash = source.get("sha256")
        valid_recorded = (
            isinstance(recorded_hash, str)
            and _SHA256_RE.fullmatch(recorded_hash) is not None
        )
        if recorded_hash is not None and not valid_recorded:
            hard_failures.append(f"source {source_id} has an invalid recorded SHA-256")
        if valid_recorded and computed_hash is not None and recorded_hash != computed_hash:
            hard_failures.append(f"source {source_id} recorded SHA-256 mismatches local file")

        if valid_recorded and computed_hash is not None and recorded_hash != computed_hash:
            selected_hash = computed_hash
            hash_origin = "computed_local_file_after_recorded_mismatch"
        elif valid_recorded:
            selected_hash = str(recorded_hash)
            hash_origin = "source_manifest"
            if computed_hash is None:
                warnings.append(f"source {source_id} recorded SHA-256 could not be rechecked")
        elif computed_hash is not None:
            selected_hash = computed_hash
            hash_origin = "computed_local_file"
            warnings.append(
                f"source {source_id} has no recorded SHA-256; local file hash was computed"
            )
        else:
            selected_hash = None
            hash_origin = "missing"
            warnings.append(f"source {source_id} has no usable source hash")

        if not isinstance(source.get("license"), str) or not str(
            source.get("license", "")
        ).strip():
            warnings.append(f"source {source_id} has no recorded license")
        if not _is_utc_timestamp(source.get("retrieved_at", source.get("downloaded_at"))):
            warnings.append(f"source {source_id} has no recorded UTC retrieval timestamp")
        if not _is_utc_timestamp(source.get("available_at")):
            warnings.append(f"source {source_id} has no recorded causal availability timestamp")

        records.append(
            {
                "id": source_id,
                "file": source.get("file"),
                "url": source.get("url"),
                "sha256": selected_hash,
                "recorded_sha256": str(recorded_hash) if valid_recorded else None,
                "hash_origin": hash_origin,
            }
        )
    return records, warnings, hard_failures


def _is_material(value: object) -> bool:
    if isinstance(value, (dict, list, str)):
        return bool(value)
    return value is not None


def extract_registered_result(
    experiment_id: str,
    analysis: dict[str, Any],
    seed: int,
) -> dict[str, Any]:
    """Select only current analysis facts for one reviewed registry entry."""

    if experiment_id not in REGISTERED_EXPERIMENTS:
        raise ScienceAgentError(f"experiment is not registered: {experiment_id}")
    if not isinstance(analysis, dict):
        raise ScienceAgentError("run_b3_analysis must return a JSON object")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ScienceAgentError("seed must be a non-negative integer")

    facts = {
        field: analysis[field]
        for field in _EXPERIMENT_FACT_FIELDS[experiment_id]
        if field in analysis
    }
    source_hashes, warnings, hard_failures = _source_hash_records(
        analysis.get("data_manifest", [])
    )
    methodology_violations = _audit_experiment_methodology(
        {"result": {"facts": facts}}
    )
    hard_failures.extend(methodology_violations)
    missing_required_inputs: list[str] = []
    required = _REGISTERED_OUTPUT_REQUIREMENTS[experiment_id]
    for field in required:
        if not _is_material(analysis.get(field)):
            missing_required_inputs.append(field)

    required_source_ids = _EXPERIMENT_SOURCE_IDS.get(experiment_id, frozenset())
    available_source_ids = {
        str(record["id"])
        for record in source_hashes
        if isinstance(record.get("sha256"), str)
    }
    for source_id in sorted(required_source_ids - available_source_ids):
        missing_required_inputs.append(f"registered_source:{source_id}")

    if experiment_id == "E4_extended_hemispheric_calibration":
        source_ids = {
            str(row.get("id", "")).lower()
            for row in analysis.get("data_manifest", [])
            if isinstance(row, dict)
        }
        has_extended_source = any(
            "hemispheric" in source_id and "extended" in source_id
            for source_id in source_ids
        )
        if not has_extended_source:
            missing_required_inputs.append("extended_hemispheric_source")
    elif experiment_id == "E7_negative_controls_and_placebos":
        has_control_output = any(
            _is_material(analysis.get(field))
            for field in (
                "negative_controls_and_placebos",
                "negative_controls",
                "placebos",
            )
        )
        if has_control_output:
            missing_required_inputs = [
                item
                for item in missing_required_inputs
                if item != "negative_controls_and_placebos"
            ]

    for record in source_hashes:
        if record.get("sha256") is None:
            missing_required_inputs.append(f"source_hash:{record['id']}")
    missing_required_inputs = list(dict.fromkeys(missing_required_inputs))

    feature_rows = analysis.get("feature_availability")
    forecast_origin = analysis.get("forecast_origin")
    if feature_rows is None:
        if experiment_id in _CENTERED_INPUT_EXPERIMENTS:
            missing_required_inputs.append(
                "feature_availability_for_centered_13m_inputs"
            )
        feature_availability_audit: dict[str, Any] = {
            "status": "not_run",
            "reasons": [
                "The current analysis does not expose row-level feature_availability records."
            ],
            "violations": [],
        }
    elif not isinstance(forecast_origin, str) or not forecast_origin.strip():
        missing_required_inputs.append("forecast_origin_for_feature_availability")
        feature_availability_audit = {
            "status": "not_run",
            "reasons": [
                "feature_availability records were present without a forecast_origin."
            ],
            "violations": [],
        }
    else:
        if experiment_id in _CENTERED_INPUT_EXPERIMENTS and isinstance(
            feature_rows, list
        ):
            centered_rows = [
                row
                for row in feature_rows
                if isinstance(row, dict)
                and _declares_centered_13_month_smoothing(row)
            ]
            if not centered_rows:
                missing_required_inputs.append(
                    "feature_availability_for_centered_13m_inputs"
                )
            centered_ids = [
                next(
                    (
                        _method_token(row[field])
                        for field in _FEATURE_NAME_FIELDS
                        if isinstance(row.get(field), str)
                    ),
                    "",
                )
                for row in centered_rows
            ]
            if len(centered_ids) != len(set(centered_ids)):
                hard_failures.append(
                    "feature availability audit has duplicate centered-input declarations"
                )
        try:
            availability_violations = audit_feature_availability(
                feature_rows, forecast_origin
            )
        except ScienceAgentError as exc:
            hard_failures.append(f"feature availability audit failed: {exc}")
            feature_availability_audit = {
                "status": "failed",
                "reasons": [str(exc)],
                "violations": [],
            }
        else:
            feature_availability_audit = {
                "status": "failed" if availability_violations else "passed",
                "reasons": [
                    (
                        f"{len(availability_violations)} feature row(s) were unavailable at the forecast origin."
                        if availability_violations
                        else "All declared feature rows were available at the forecast origin."
                    )
                ],
                "violations": availability_violations,
            }
            if availability_violations:
                hard_failures.append(
                    "feature availability leakage: one or more features were unavailable at the forecast origin"
                )

    missing_required_inputs = list(dict.fromkeys(missing_required_inputs))

    if hard_failures:
        recommended_status = "quarantined"
    elif missing_required_inputs:
        recommended_status = "inconclusive"
    elif warnings:
        recommended_status = "warning"
    else:
        recommended_status = "passed"
    claim_boundary = analysis.get("claim_boundary")
    if not isinstance(claim_boundary, str) or not claim_boundary.strip():
        claim_boundary = "Retrospective registered evidence only; no operational forecast claim."
        warnings.append("analysis did not expose a claim_boundary")
        if recommended_status == "passed":
            recommended_status = "warning"

    return {
        "experiment_id": experiment_id,
        "registered_operation": REGISTERED_EXPERIMENTS[experiment_id],
        "seed": seed,
        "facts": facts,
        "source_hashes": source_hashes,
        "warnings": warnings,
        "hard_failures": hard_failures,
        "missing_required_inputs": missing_required_inputs,
        "feature_availability_audit": feature_availability_audit,
        "recommended_status": recommended_status,
        "claim_boundary": claim_boundary,
    }


def _is_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(
            text[:-1] + "+00:00" if text.endswith("Z") else text
        )
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def _contract_timestamp(value: object, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(
                text[:-1] + "+00:00" if text.endswith("Z") else text
            )
        except ValueError:
            pass
        else:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
    return fallback


def _build_manifest_data_sources(
    experiment_id: str,
    analysis: dict[str, Any],
    result: dict[str, Any],
    fallback_timestamp: str,
) -> list[dict[str, Any]]:
    manifest_rows = analysis.get("data_manifest", [])
    metadata = {
        row.get("id"): row
        for row in manifest_rows
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    selected_ids = _EXPERIMENT_SOURCE_IDS.get(experiment_id)
    data_sources: list[dict[str, Any]] = []
    for hash_record in result.get("source_hashes", []):
        if not isinstance(hash_record, dict):
            continue
        source_id = hash_record.get("id")
        sha256 = hash_record.get("sha256")
        if not isinstance(source_id, str) or not isinstance(sha256, str):
            continue
        if selected_ids is not None and source_id not in selected_ids:
            continue
        source = metadata.get(source_id, {})
        url = source.get("url") if isinstance(source, dict) else None
        parsed_url = urlparse(url) if isinstance(url, str) else None
        if parsed_url is None or not parsed_url.scheme:
            url = f"urn:b3:data-source:{source_id}"
        license_value = source.get("license") if isinstance(source, dict) else None
        if not isinstance(license_value, str) or not license_value.strip():
            license_value = "not_recorded_in_source_manifest"
        data_status = source.get("data_status") if isinstance(source, dict) else None
        if data_status not in {"provisional", "definitive", "retrospective"}:
            data_status = "retrospective"
        retrieved_at = _contract_timestamp(
            source.get("retrieved_at", source.get("downloaded_at")),
            fallback_timestamp,
        )
        data_source: dict[str, Any] = {
            "id": source_id,
            "url": url,
            "license": license_value,
            "retrieved_at": retrieved_at,
            "available_at": _contract_timestamp(
                source.get("available_at"), retrieved_at
            ),
            "sha256": sha256,
            "data_status": data_status,
        }
        version = source.get("version") if isinstance(source, dict) else None
        if isinstance(version, str) and version.strip():
            data_source["version"] = version
        data_sources.append(data_source)

    if data_sources:
        return data_sources
    source_manifest = PROJECT_ROOT / "b3" / "data" / "raw" / "source_manifest.json"
    if not source_manifest.is_file():
        raise ScienceAgentError("no hashable data source is available for immutable accounting")
    return [
        {
            "id": "b3_source_manifest_accounting_only",
            "url": source_manifest.resolve().as_uri(),
            "license": "workspace_metadata_upstream_licenses_not_recorded",
            "retrieved_at": fallback_timestamp,
            "available_at": fallback_timestamp,
            "sha256": _sha256_file(source_manifest),
            "data_status": "retrospective",
        }
    ]


def preflight_registered_experiment(
    store: RunStore,
    run_id: str,
    experiment_id: str,
    plan_node_id: str,
    seed: int,
) -> dict[str, Any]:
    """Inspect the frozen DAG and immutable artifacts without executing a node."""

    if experiment_id not in REGISTERED_EXPERIMENTS:
        raise ScienceAgentError(f"experiment is not registered: {experiment_id}")
    _require_nonempty_string(plan_node_id, "plan_node_id")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ScienceAgentError("seed must be a non-negative integer")

    reasons: list[str] = []
    plan: dict[str, Any] | None = None
    node: dict[str, Any] | None = None
    try:
        run_manifest = store.read_artifact(run_id, "run_manifest.json")
        if run_manifest.get("run_id") != run_id:
            reasons.append("run_manifest_mismatch")
        plan = store.read_artifact(run_id, "research_plan.json")
        validate_research_plan(plan)
    except ScienceAgentError:
        reasons.append("frozen_plan_missing_or_invalid")
    if plan is not None:
        if plan.get("run_id") != run_id:
            reasons.append("plan_run_id_mismatch")
        if plan.get("status") != "frozen":
            reasons.append("plan_not_frozen")
        matches = [
            candidate
            for candidate in plan.get("task_graph", [])
            if isinstance(candidate, dict) and candidate.get("id") == plan_node_id
        ]
        if len(matches) != 1:
            reasons.append("plan_node_not_unique")
        else:
            node = matches[0]
            if node.get("tool") != f"registered:{experiment_id}":
                reasons.append("registered_tool_mismatch")
            if node.get("status") != "ready":
                reasons.append("plan_node_not_ready")
            if node.get("seed") != seed:
                reasons.append("seed_mismatch")
            budget = node.get("budget")
            if not isinstance(budget, dict) or not isinstance(
                budget.get("wall_seconds"), (int, float)
            ) or isinstance(budget.get("wall_seconds"), bool) or float(
                budget.get("wall_seconds", 0)
            ) <= 0:
                reasons.append("positive_wall_budget_missing")

    run_dir = (store.root / store._validate_run_id(run_id)).resolve()
    target_relative = f"experiments/{experiment_id}_seed{seed}"
    target_exists = (run_dir / target_relative).exists()
    if target_exists:
        reasons.append("immutable_target_already_exists")

    manifest_by_parent: dict[str, list[dict[str, Any]]] = {}
    experiments_dir = run_dir / "experiments"
    if experiments_dir.is_dir():
        for path in sorted(experiments_dir.glob("*/manifest.json")):
            try:
                relative = path.relative_to(run_dir).as_posix()
                manifest = store.read_artifact(run_id, relative)
                validate_experiment_manifest(manifest)
            except (ScienceAgentError, ValueError):
                continue
            parent_id = manifest.get("parent_id")
            if isinstance(parent_id, str):
                manifest_by_parent.setdefault(parent_id, []).append(manifest)

    dependencies: list[dict[str, Any]] = []
    if node is not None:
        for dependency_id in node.get("depends_on", []):
            candidates = manifest_by_parent.get(str(dependency_id), [])
            accepted = [
                manifest
                for manifest in candidates
                if manifest.get("status") in {"passed", "warning"}
            ]
            dependency_status = (
                str(accepted[-1]["status"])
                if len(accepted) == 1
                else (
                    "ambiguous"
                    if len(accepted) > 1
                    else (
                        str(candidates[-1].get("status"))
                        if candidates
                        else "missing"
                    )
                )
            )
            satisfied = len(accepted) == 1
            dependencies.append(
                {
                    "plan_node_id": str(dependency_id),
                    "status": dependency_status,
                    "satisfied": satisfied,
                }
            )
            if not satisfied:
                reasons.append(f"dependency_not_satisfied:{dependency_id}")

    reasons = list(dict.fromkeys(reasons))
    return {
        "schema_version": "b3-experiment-preflight-v1",
        "status": "ready" if not reasons else "blocked",
        "run_id": run_id,
        "plan_node_id": plan_node_id,
        "experiment_id": experiment_id,
        "seed": seed,
        "target_path": target_relative,
        "target_available": not target_exists,
        "dependencies": dependencies,
        "reasons": reasons,
    }


def _load_registered_plan(
    store: RunStore,
    run_id: str,
    experiment_id: str,
    plan_node_id: str,
    seed: int,
) -> dict[str, Any]:
    preflight = preflight_registered_experiment(
        store, run_id, experiment_id, plan_node_id, seed
    )
    if preflight["status"] != "ready":
        raise ScienceAgentError(
            "registered experiment preflight blocked: "
            + ", ".join(preflight["reasons"])
        )
    plan = store.read_artifact(run_id, "research_plan.json")
    validate_research_plan(plan)
    if plan.get("run_id") != run_id:
        raise ScienceAgentError("research_plan run_id does not match the run")
    if plan.get("status") != "frozen":
        raise ScienceAgentError("research_plan is not frozen for execution")
    matches = [
        node
        for node in plan.get("task_graph", [])
        if isinstance(node, dict) and node.get("id") == plan_node_id
    ]
    if len(matches) != 1:
        raise ScienceAgentError(f"plan node is not uniquely registered: {plan_node_id}")
    expected_tool = f"registered:{experiment_id}"
    node = matches[0]
    if node.get("tool") != expected_tool:
        raise ScienceAgentError(
            f"plan node tool must be exactly {expected_tool}"
        )
    if node.get("status") != "ready":
        raise ScienceAgentError("plan node must have ready status for execution")
    if node.get("seed") != seed:
        raise ScienceAgentError("experiment seed does not match the frozen plan node")
    return plan


def _safe_error_message(exc: Exception) -> str:
    return "registered experiment failed; diagnostic text omitted to protect secrets"


def _portable_provenance_path(path: Path) -> str:
    """Return an auditable path label without serializing machine-local roots."""

    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        # External fault-injection workers are identified by their file name and
        # content hash elsewhere in the same provenance record. Their temporary
        # host path is neither portable nor relevant to the scientific claim.
        return f"external_worker/{resolved.name}"


def _execution_record(experiment_id: str, worker_path: Path) -> dict[str, Any]:
    code_paths = (
        Path(__file__).resolve(),
        worker_path.resolve(),
        PROJECT_ROOT / "src" / "b3cycle" / "analysis.py",
        PROJECT_ROOT / "src" / "b3cycle" / "data.py",
    )
    dependency_path = PROJECT_ROOT / "requirements-analysis.lock"
    if not all(path.is_file() for path in code_paths) or not dependency_path.is_file():
        raise ScienceAgentError("execution provenance files are unavailable")
    pins = {
        name.strip().lower(): version.strip()
        for line in dependency_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "==" in line
        for name, version in [line.split("==", 1)]
    }
    for package in ("numpy", "psutil"):
        if package not in pins:
            raise ScienceAgentError(
                f"requirements-analysis.lock has no {package} pin"
            )
        if importlib.metadata.version(package) != pins[package]:
            raise ScienceAgentError(
                f"installed {package} version does not match requirements-analysis.lock"
            )
    code_hashes = {_portable_provenance_path(path): _sha256_file(path) for path in code_paths}
    return {
        "command": f"registered:{experiment_id}",
        "cwd": (
            "temporary_analysis_root"
            if worker_path.resolve() == PRODUCTION_ANALYSIS_WORKER
            else str(PROJECT_ROOT)
        ),
        "code_sha256": canonical_json_sha256(code_hashes),
        "dependency_lock_sha256": _sha256_file(dependency_path),
        "python_version": platform.python_version(),
        "os": platform.platform(),
        "hardware": platform.machine() or "not_reported_by_platform",
    }


def _execution_code_hashes(worker_path: Path) -> dict[str, str]:
    paths = (
        Path(__file__).resolve(),
        worker_path.resolve(),
        PROJECT_ROOT / "src" / "b3cycle" / "analysis.py",
        PROJECT_ROOT / "src" / "b3cycle" / "data.py",
    )
    if not all(path.is_file() for path in paths):
        raise ScienceAgentError("execution provenance files are unavailable")
    return {_portable_provenance_path(path): _sha256_file(path) for path in paths}


def _isolated_analysis_environment(
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    allowed_names = {
        "SYSTEMROOT",
        "WINDIR",
        "PATH",
        "PATHEXT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
    }
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in allowed_names
    }
    environment.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    if overrides:
        if set(overrides) - {"B3_PROJECT_ROOT"}:
            raise ScienceAgentError("unsupported isolated-environment override")
        environment.update(overrides)
    return environment


def _run_worker_process(
    worker: Path,
    wall_seconds: float,
    cwd: Path,
    *,
    cpu_seconds: float | None = None,
    environment_overrides: dict[str, str] | None = None,
    worker_args: list[str] | None = None,
) -> dict[str, Any]:
    """Execute one worker with active wall/CPU/output limits and usage sampling."""

    output_limit = 4 * 1024 * 1024
    worker_args = worker_args or []
    if os.name == "nt" and sys.prefix != sys.base_prefix:
        # A Windows venv python.exe is a redirector that spawns the real
        # interpreter. Launch that interpreter directly so the child-process
        # guard still means "the analysis spawned a process", not "the venv
        # bootstrap worked". Only the current venv's locked packages are added.
        try:
            interpreter = psutil.Process().exe()
        except psutil.Error:
            interpreter = getattr(sys, "_base_executable", sys.executable)
        bootstrap = (
            "import runpy,sys; "
            "sys.path.insert(0, sys.argv[1]); "
            "worker=sys.argv[2]; sys.argv=sys.argv[2:]; "
            "runpy.run_path(worker, run_name='__main__')"
        )
        command = [
            interpreter,
            "-B",
            "-I",
            "-c",
            bootstrap,
            sysconfig.get_paths()["purelib"],
            str(worker),
            *worker_args,
        ]
    else:
        command = [sys.executable, "-B", "-I", str(worker), *worker_args]
    with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(
        mode="w+b"
    ) as stderr_file:
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=_isolated_analysis_environment(environment_overrides),
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
            )
        except OSError as exc:
            raise ScienceAgentError("isolated analysis worker could not start") from exc
        peak_ram_mb = 0.0
        worker_cpu_seconds = 0.0
        try:
            tracked_process = psutil.Process(process.pid)
        except psutil.Error:
            tracked_process = None
        seen_children: dict[int, psutil.Process] = {}

        def current_children() -> list[psutil.Process]:
            if tracked_process is None:
                return []
            try:
                children = tracked_process.children(recursive=True)
            except psutil.Error:
                return []
            for child in children:
                seen_children[child.pid] = child
            return children

        def terminate_process_tree() -> None:
            children = current_children()
            targets = list({child.pid: child for child in [*seen_children.values(), *children]}.values())
            for child in reversed(targets):
                try:
                    child.kill()
                except psutil.Error:
                    pass
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if targets:
                psutil.wait_procs(targets, timeout=5)

        def sample_usage() -> None:
            nonlocal peak_ram_mb, worker_cpu_seconds
            if tracked_process is None:
                return
            try:
                memory = tracked_process.memory_info()
                peak_bytes = getattr(memory, "peak_wset", memory.rss)
                peak_ram_mb = max(peak_ram_mb, peak_bytes / (1024.0 * 1024.0))
                cpu = tracked_process.cpu_times()
                worker_cpu_seconds = max(
                    worker_cpu_seconds, float(cpu.user + cpu.system)
                )
            except psutil.Error:
                pass

        deadline = time.perf_counter() + wall_seconds
        termination: str | None = None
        while process.poll() is None:
            sample_usage()
            if current_children():
                termination = "child_process"
                break
            if stdout_file.tell() > output_limit or stderr_file.tell() > output_limit:
                termination = "output"
                break
            if cpu_seconds is not None and worker_cpu_seconds > cpu_seconds:
                termination = "cpu"
                break
            if time.perf_counter() >= deadline:
                termination = "wall"
                break
            time.sleep(0.01)
        if termination is not None:
            terminate_process_tree()
        else:
            process.wait(timeout=5)
        sample_usage()
        usage = {
            "cpu_seconds": max(0.0, worker_cpu_seconds),
            "peak_ram_mb": max(0.0, peak_ram_mb),
        }
        if termination == "wall":
            error = TimeoutError(
                f"registered analysis exceeded wall budget ({wall_seconds:g}s)"
            )
            error.worker_usage = usage
            raise error
        if termination == "cpu":
            error = TimeoutError(
                f"registered analysis exceeded CPU budget ({cpu_seconds:g}s)"
            )
            error.worker_usage = usage
            raise error
        if termination == "output":
            error = ScienceAgentError(
                "isolated analysis worker output exceeded 4 MiB"
            )
            error.worker_usage = usage
            raise error
        if termination == "child_process":
            error = ScienceAgentError(
                "isolated analysis worker spawned a forbidden child process"
            )
            error.worker_usage = usage
            raise error
        stdout_size = stdout_file.tell()
        stderr_size = stderr_file.tell()
        if stdout_size > output_limit or stderr_size > output_limit:
            error = ScienceAgentError(
                "isolated analysis worker output exceeded 4 MiB"
            )
            error.worker_usage = usage
            raise error
        stdout_file.seek(0)
        try:
            stdout = stdout_file.read().decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            error = ScienceAgentError(
                "isolated analysis worker returned non-UTF-8 output"
            )
            error.worker_usage = usage
            raise error from exc
        if process.returncode != 0:
            error = ScienceAgentError("isolated analysis worker failed")
            error.worker_usage = usage
            raise error
        return {"stdout": stdout, "usage": usage}


def _parse_worker_envelope(stdout: str) -> dict[str, Any]:
    """Parse and validate the finite JSON envelope emitted by the fixed worker."""

    def reject_nonfinite(value: str) -> None:
        raise ValueError(f"non-finite JSON constant: {value}")

    try:
        payload = json.loads(stdout, parse_constant=reject_nonfinite)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ScienceAgentError("isolated analysis worker returned invalid JSON") from exc
    envelope = _require_mapping(payload, "isolated analysis envelope")
    if set(envelope) != {"schema_version", "analysis", "usage"}:
        raise ScienceAgentError("isolated analysis worker returned an invalid envelope")
    if envelope["schema_version"] != "b3-analysis-worker-v1":
        raise ScienceAgentError("isolated analysis worker schema is unsupported")
    analysis = _require_mapping(envelope["analysis"], "isolated analysis")
    usage = _require_mapping(envelope["usage"], "isolated analysis usage")
    if set(usage) != {"cpu_seconds", "peak_ram_mb"}:
        raise ScienceAgentError("isolated analysis worker usage is invalid")
    for field in ("cpu_seconds", "peak_ram_mb"):
        value = usage[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ScienceAgentError(f"isolated analysis worker {field} must be numeric")
        if not math.isfinite(float(value)) or float(value) < 0:
            raise ScienceAgentError(f"isolated analysis worker {field} must be finite")
    return {"analysis": analysis, "usage": usage}


def _default_analysis_worker_path() -> Path:
    return PRODUCTION_ANALYSIS_WORKER


def _run_isolated_analysis_at_path(
    wall_seconds: float,
    worker: Path,
    cpu_seconds: float | None = None,
    experiment_id: str = "E8_clean_reproduction",
    seed: int = 0,
) -> dict[str, Any]:
    """Run one selected worker through the production isolation/parsing chain."""

    if not math.isfinite(wall_seconds) or wall_seconds <= 0:
        raise ScienceAgentError("isolated analysis requires a positive wall budget")
    if not worker.is_file():
        raise ScienceAgentError("isolated analysis worker is unavailable")
    is_production_worker = worker.resolve() == PRODUCTION_ANALYSIS_WORKER
    if experiment_id not in REGISTERED_EXPERIMENTS:
        raise ScienceAgentError(f"experiment is not registered: {experiment_id}")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ScienceAgentError("seed must be a non-negative integer")
    worker_args = (
        ["--experiment-id", experiment_id, "--seed", str(seed)]
        if is_production_worker
        else None
    )
    if is_production_worker:
        with tempfile.TemporaryDirectory(prefix="b3-analysis-sandbox-") as tmp:
            sandbox_root = Path(tmp)
            source_raw = PROJECT_ROOT / "b3" / "data" / "raw"
            sandbox_raw = sandbox_root / "b3" / "data" / "raw"
            shutil.copytree(source_raw, sandbox_raw)
            process_result = _run_worker_process(
                worker,
                wall_seconds,
                sandbox_root,
                cpu_seconds=cpu_seconds,
                environment_overrides={"B3_PROJECT_ROOT": str(sandbox_root)},
                worker_args=worker_args,
            )
            try:
                parsed = _parse_worker_envelope(process_result["stdout"])
            except Exception as exc:
                exc.worker_usage = process_result["usage"]
                raise
    else:
        process_result = _run_worker_process(
            worker,
            wall_seconds,
            PROJECT_ROOT,
            cpu_seconds=cpu_seconds,
            worker_args=worker_args,
        )
        try:
            parsed = _parse_worker_envelope(process_result["stdout"])
        except Exception as exc:
            exc.worker_usage = process_result["usage"]
            raise
    parsed["usage"] = {
        "cpu_seconds": max(
            float(parsed["usage"]["cpu_seconds"]),
            float(process_result["usage"]["cpu_seconds"]),
        ),
        "peak_ram_mb": max(
            float(parsed["usage"]["peak_ram_mb"]),
            float(process_result["usage"]["peak_ram_mb"]),
        ),
    }
    parsed["isolation"] = {
        "temporary_data_root": is_production_worker,
        "os_write_sandbox": False,
        "source_copy": is_production_worker,
        "shared_output_writes": False if is_production_worker else None,
    }
    return parsed


def _run_isolated_analysis(wall_seconds: float) -> dict[str, Any]:
    """Run the fixed production analysis worker bounded by the frozen plan."""

    return _run_isolated_analysis_at_path(
        wall_seconds,
        _default_analysis_worker_path(),
        experiment_id="E8_clean_reproduction",
        seed=0,
    )


def _registered_failure_result(
    experiment_id: str,
    seed: int,
    analysis: dict[str, Any],
    hard_failure: str,
) -> dict[str, Any]:
    """Build the fixed, claim-blocking result used by accounted failures."""

    try:
        source_hashes, source_warnings, source_failures = _source_hash_records(
            analysis.get("data_manifest", [])
        )
    except Exception:
        source_hashes = []
        source_warnings = ["source accounting was unavailable in the failed run"]
        source_failures = ["registered source accounting did not complete"]
    return {
        "experiment_id": experiment_id,
        "registered_operation": REGISTERED_EXPERIMENTS[experiment_id],
        "seed": seed,
        "facts": {},
        "source_hashes": source_hashes,
        "warnings": source_warnings,
        "hard_failures": source_failures + [hard_failure],
        "missing_required_inputs": [],
        "feature_availability_audit": {
            "status": "not_run",
            "reasons": ["The registered experiment did not produce claimable output."],
            "violations": [],
        },
        "recommended_status": "failed",
        "claim_boundary": "No scientific claim may be made from a failed run.",
    }


def run_registered_experiment(
    store: RunStore,
    run_id: str,
    experiment_id: str,
    plan_node_id: str,
    seed: int,
) -> dict[str, Any]:
    """Run one frozen-plan node through the fixed B3 analysis boundary."""

    if experiment_id not in REGISTERED_EXPERIMENTS:
        raise ScienceAgentError(f"experiment is not registered: {experiment_id}")
    _require_nonempty_string(plan_node_id, "plan_node_id")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ScienceAgentError("seed must be a non-negative integer")
    run_manifest = store.read_artifact(run_id, "run_manifest.json")
    if run_manifest.get("run_id") != run_id:
        raise ScienceAgentError("run manifest does not match requested run_id")
    target_directory = store._safe_path(
        run_id, f"experiments/{experiment_id}_seed{seed}"
    )
    if target_directory.exists():
        raise ScienceAgentError(
            "immutable experiment target already exists; replay requires a new seed or run"
        )

    started = datetime.now(timezone.utc)
    wall_started = time.perf_counter()

    analysis = _fallback_analysis_source_manifest()
    plan: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    worker_started = False
    worker_completed = False
    worker_usage = {"cpu_seconds": 0.0, "peak_ram_mb": 0.0}
    worker_isolation: dict[str, Any] | None = None
    worker_path = _default_analysis_worker_path()
    try:
        plan = _load_registered_plan(
            store,
            run_id,
            experiment_id,
            plan_node_id,
            seed,
        )
        node = next(
            node
            for node in plan["task_graph"]
            if isinstance(node, dict) and node.get("id") == plan_node_id
        )
        worker_started = True
        cpu_budget = node["budget"].get("cpu_seconds")
        worker_result = _run_isolated_analysis_at_path(
            float(node["budget"]["wall_seconds"]),
            worker_path,
            float(cpu_budget) if cpu_budget is not None else None,
            experiment_id,
            seed,
        )
        analysis = worker_result["analysis"]
        worker_usage = worker_result["usage"]
        worker_isolation = worker_result["isolation"]
        worker_completed = True
        result = extract_registered_result(experiment_id, analysis, seed)
        # Force finite-JSON validation inside the accounted failure boundary.
        canonical_json_sha256(result)
        status = str(result["recommended_status"])
    except Exception as exc:
        failure_usage = getattr(exc, "worker_usage", None)
        if isinstance(failure_usage, dict):
            worker_usage = {
                "cpu_seconds": float(failure_usage.get("cpu_seconds", 0.0)),
                "peak_ram_mb": float(failure_usage.get("peak_ram_mb", 0.0)),
            }
        if not isinstance(analysis, dict):
            analysis = _fallback_analysis_source_manifest()
        result = _registered_failure_result(
            experiment_id,
            seed,
            analysis,
            "registered analysis did not complete",
        )
        status = "failed"
        error = {
            "type": type(exc).__name__,
            "message": _safe_error_message(exc),
        }

    accounting_failures: list[str] = []
    execution_record: dict[str, Any] | None = None
    code_file_hashes: dict[str, str] = {}
    dependency_versions: dict[str, str] = {}
    try:
        execution_record = _execution_record(experiment_id, worker_path)
    except Exception:
        accounting_failures.append("execution_record")
    try:
        code_file_hashes = _execution_code_hashes(worker_path)
    except Exception:
        accounting_failures.append("code_file_hashes")
    try:
        dependency_versions = {
            "numpy": importlib.metadata.version("numpy"),
            "psutil": importlib.metadata.version("psutil"),
        }
    except Exception:
        accounting_failures.append("dependency_versions")
    accounting_complete = not accounting_failures
    if not accounting_complete:
        result = _registered_failure_result(
            experiment_id,
            seed,
            analysis,
            "registered experiment accounting did not complete",
        )
        status = "failed"
        error = {
            "type": "AccountingError",
            "message": _safe_error_message(ScienceAgentError("accounting failure")),
        }

    finished = datetime.now(timezone.utc)
    result_path = f"experiments/{experiment_id}_seed{seed}/result.json"
    try:
        data_sources = _build_manifest_data_sources(
            experiment_id,
            analysis,
            result,
            started.isoformat(),
        )
    except Exception:
        accounting_failures.append("data_sources")
        accounting_complete = False
        analysis = _fallback_analysis_source_manifest()
        result = _registered_failure_result(
            experiment_id,
            seed,
            analysis,
            "registered data-source accounting did not complete",
        )
        status = "failed"
        error = {
            "type": "AccountingError",
            "message": _safe_error_message(ScienceAgentError("accounting failure")),
        }
        data_sources = []
    result_artifact: dict[str, Any] = {
        "schema_version": "b3-registered-experiment-result-v1",
        "run_id": run_id,
        "node_id": f"{experiment_id}_seed{seed}",
        "experiment_id": experiment_id,
        "seed": seed,
        "status": status,
        "finished_at": finished.isoformat(),
        "result": result,
        "error": error,
    }
    result_artifact["artifact_sha256"] = canonical_json_sha256(result_artifact)
    claim_effect = {
        "passed": "keeps_confidence_bounded",
        "warning": "lowers_confidence",
        "inconclusive": "lowers_confidence",
        "failed": "blocks_claim",
        "quarantined": "blocks_claim",
    }[status]
    gate_status = {
        "passed": "passed",
        "warning": "warning",
        "inconclusive": "warning",
        "failed": "failed",
        "quarantined": "failed",
    }[status]
    availability_audit = result["feature_availability_audit"]
    leakage_gate_status = {
        "failed": "failed",
        "passed": "passed",
        "not_run": "warning",
    }[availability_audit["status"]]
    leakage_reasons = list(availability_audit["reasons"])
    methodology_failures = [
        failure
        for failure in result["hard_failures"]
        if any(
            token in failure.lower()
            for token in ("split", "smoothing", "feature availability")
        )
    ]
    if methodology_failures:
        leakage_gate_status = "failed"
        leakage_reasons = methodology_failures
    manifest: dict[str, Any] = {
        "schema_version": EXPERIMENT_MANIFEST_SCHEMA_VERSION,
        "run_id": run_id,
        "node_id": f"{experiment_id}_seed{seed}",
        "parent_id": plan_node_id,
        "experiment_id": experiment_id,
        "seed": seed,
        "status": status,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "data_sources": data_sources,
        "feature_availability_audit": {
            "status": result["feature_availability_audit"]["status"],
            "reasons": result["feature_availability_audit"]["reasons"],
        },
        "usage": {
            "wall_seconds": max(0.0, time.perf_counter() - wall_started),
            "cpu_seconds": float(worker_usage["cpu_seconds"]),
            "gpu_seconds": 0.0,
            "peak_ram_mb": float(worker_usage["peak_ram_mb"]),
            "tokens": 0,
        },
        "artifacts": [
            {
                "kind": "metrics",
                "path": result_path,
                "sha256": result_artifact["artifact_sha256"],
            }
        ],
        "gates": {
            "baseline": {
                "status": gate_status,
                "reasons": [f"registered outcome: {status}"],
            },
            "stability": {
                "status": "not_run",
                "reasons": ["No extra unregistered stability computation was executed."],
            },
            "leakage": {
                "status": leakage_gate_status,
                "reasons": leakage_reasons,
            },
            "safety": {
                "status": (
                    "passed"
                    if worker_completed and accounting_complete
                    else (
                        "failed"
                        if worker_started or not accounting_complete
                        else "not_run"
                    )
                ),
                "reasons": [
                    (
                        "Execution completed in the fixed isolated Python worker."
                        if worker_completed and accounting_complete
                        else (
                            "Execution accounting was incomplete; no claim is allowed."
                            if not accounting_complete
                            else (
                                "The isolated worker started but did not return a valid result."
                                if worker_started
                                else "The isolated worker was not started."
                            )
                        )
                    )
                ],
            },
            "reproduction": {
                "status": (
                    gate_status
                    if experiment_id == "E8_clean_reproduction"
                    else "not_run"
                ),
                "reasons": [
                    (
                        f"clean reproduction outcome: {status}"
                        if experiment_id == "E8_clean_reproduction"
                        else "This node is not the registered clean-reproduction node."
                    )
                ],
            },
        },
        "result": result,
        "provenance": {
            "execution_boundary": "isolated_python_worker",
            "worker_started": worker_started,
            "worker_completed": worker_completed,
            "accounting_complete": accounting_complete,
            "accounting_failure_stages": accounting_failures,
            "code_files_sha256": code_file_hashes,
            "dependency_lock": "requirements-analysis.lock",
            "dependency_versions": dependency_versions,
            "worker_path": _portable_provenance_path(worker_path),
            "worker_is_default": worker_path.resolve()
            == PRODUCTION_ANALYSIS_WORKER,
            "filesystem_isolation": (
                worker_isolation
                if worker_completed
                else {
                    "temporary_data_root": worker_path.resolve()
                    == PRODUCTION_ANALYSIS_WORKER,
                    "os_write_sandbox": False,
                    "source_copy": worker_path.resolve()
                    == PRODUCTION_ANALYSIS_WORKER,
                    "shared_output_writes": False
                    if worker_path.resolve() == PRODUCTION_ANALYSIS_WORKER
                    else None,
                }
            ),
            "registry_operation": REGISTERED_EXPERIMENTS[experiment_id],
            "plan_artifact": "research_plan.json",
            "plan_artifact_sha256": (
                plan.get("artifact_sha256") if isinstance(plan, dict) else None
            ),
            "source_hash_policy": (
                "preserve a verified recorded SHA-256; otherwise use the computed local-file SHA-256, retain the recorded value for audit, and quarantine mismatches"
            ),
        },
        "error": error,
        "claim_effect": claim_effect,
    }
    if execution_record is not None:
        manifest["execution"] = execution_record
    try:
        manifest["artifact_sha256"] = canonical_json_sha256(manifest)
        validate_experiment_manifest(manifest)
    except Exception:
        fallback_analysis = _fallback_analysis_source_manifest()
        result = _registered_failure_result(
            experiment_id,
            seed,
            fallback_analysis,
            "experiment manifest accounting did not complete",
        )
        result_artifact = {
            "schema_version": "b3-registered-experiment-result-v1",
            "run_id": run_id,
            "node_id": f"{experiment_id}_seed{seed}",
            "experiment_id": experiment_id,
            "seed": seed,
            "status": "failed",
            "finished_at": finished.isoformat(),
            "result": result,
            "error": {
                "type": "ManifestAccountingError",
                "message": _safe_error_message(
                    ScienceAgentError("manifest validation failure")
                ),
            },
        }
        result_artifact["artifact_sha256"] = canonical_json_sha256(result_artifact)
        manifest = {
            "schema_version": EXPERIMENT_MANIFEST_SCHEMA_VERSION,
            "run_id": run_id,
            "node_id": f"{experiment_id}_seed{seed}",
            "parent_id": plan_node_id,
            "experiment_id": experiment_id,
            "seed": seed,
            "status": "failed",
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "data_sources": [],
            "artifacts": [
                {
                    "kind": "metrics",
                    "path": result_path,
                    "sha256": result_artifact["artifact_sha256"],
                }
            ],
            "result": result,
            "error": result_artifact["error"],
            "claim_effect": "blocks_claim",
        }
        manifest["artifact_sha256"] = canonical_json_sha256(manifest)
        validate_experiment_manifest(manifest)
    store.write_artifact_bundle(
        run_id,
        f"experiments/{manifest['node_id']}",
        {"result.json": result_artifact, "manifest.json": manifest},
    )
    return manifest


__all__ = [
    "REGISTERED_EXPERIMENTS",
    "RunStore",
    "ScienceAgentError",
    "audit_feature_availability",
    "canonical_json_sha256",
    "extract_registered_result",
    "jaccard_text",
    "normalize_hypothesis_text",
    "order_balanced_tournament",
    "preflight_registered_experiment",
    "proximity_clusters",
    "run_registered_experiment",
    "score_hypothesis_pair",
    "submit_hypothesis_portfolio_draft",
    "submit_research_plan_draft",
    "validate_experiment_manifest",
    "validate_hypothesis_portfolio",
    "validate_hypothesis_portfolio_against_run",
    "validate_research_plan",
]
