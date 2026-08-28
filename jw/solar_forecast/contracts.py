"""Typed boundaries for solar precursor features and forecast experiments.

These validators sit where deterministic host-produced data first enters the
research loop.  They intentionally distinguish physical observables and keep
the scientific verdict derivable from numerical receipts rather than model
prose.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

FEATURE_VERSION = "solar-precursor-feature-record-v1"
EXPERIMENT_VERSION = "solar-forecast-experiment-receipt-v1"

OBSERVABLE_KINDS = {
    "sunspot_rise_metric",
    "polar_aperture_field",
    "hemispheric_polar_flux",
    "axial_dipole_moment",
}
AXIAL_ALLOWED_SOURCE_KINDS = {
    "registered_axial_dipole",
    "synoptic_map_harmonic",
}
FEATURE_STATUSES = {"available", "blocked_by_data"}
FORECAST_STATUSES = {
    "skill_supported",
    "mixed_evidence",
    "tested_no_skill",
    "blocked_by_data",
    "execution_failed",
}

_FEATURE_FIELDS = {
    "schema_version",
    "feature_id",
    "hypothesis_id",
    "forecast_origin",
    "observable_kind",
    "physical_quantity",
    "unit",
    "source_dataset_ids",
    "source_artifact_ids",
    "observation_start",
    "observation_end",
    "available_at",
    "cycle_id",
    "target_cycle_id",
    "value",
    "uncertainty",
    "measurement_regime",
    "derivation_method",
    "source_kind",
    "status",
}

_EXPERIMENT_FIELDS = {
    "schema_version",
    "experiment_id",
    "status",
    "forecast_origin",
    "hypothesis_ids",
    "feature_ids",
    "baseline_names",
    "candidate_name",
    "training_cycles",
    "test_cycles",
    "folds",
    "metrics",
    "bootstrap",
    "sensitivity",
    "leakage_audit",
}


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def _require_fields(value: Mapping[str, Any], fields: set[str], label: str) -> None:
    missing = sorted(fields.difference(value))
    if missing:
        raise ValueError(f"{label} missing required fields: {', '.join(missing)}")


def _require_nonempty_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value.strip()


def _require_string_list(
    value: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> list[str]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError(f"{field} must be a list of non-empty strings")
    result = [item.strip() for item in value]
    if not allow_empty and not result:
        raise ValueError(f"{field} must not be empty")
    return result


def _require_cycle(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer cycle number")
    return value


def _require_finite(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def validate_precursor_feature_record(value: object) -> dict[str, object]:
    """Validate one as-of precursor record and its physical-variable lineage."""

    record = _require_mapping(value, "precursor feature record")
    _require_fields(record, _FEATURE_FIELDS, "precursor feature record")
    if record["schema_version"] != FEATURE_VERSION:
        raise ValueError(f"schema_version must be {FEATURE_VERSION}")

    for field in (
        "feature_id",
        "hypothesis_id",
        "forecast_origin",
        "physical_quantity",
        "unit",
        "measurement_regime",
        "derivation_method",
        "source_kind",
    ):
        _require_nonempty_text(record[field], field)
    for field in ("observation_start", "observation_end", "available_at"):
        if not isinstance(record[field], (str, int, float)) or isinstance(record[field], bool):
            raise ValueError(f"{field} must identify an observation or availability time")

    observable_kind = record["observable_kind"]
    if observable_kind not in OBSERVABLE_KINDS:
        raise ValueError(f"observable_kind must be one of {sorted(OBSERVABLE_KINDS)}")
    status = record["status"]
    if status not in FEATURE_STATUSES:
        raise ValueError(f"status must be one of {sorted(FEATURE_STATUSES)}")

    _require_cycle(record["cycle_id"], "cycle_id")
    _require_cycle(record["target_cycle_id"], "target_cycle_id")
    allow_empty_sources = status == "blocked_by_data"
    _require_string_list(
        record["source_dataset_ids"],
        "source_dataset_ids",
        allow_empty=allow_empty_sources,
    )
    _require_string_list(
        record["source_artifact_ids"],
        "source_artifact_ids",
        allow_empty=allow_empty_sources,
    )

    if status == "blocked_by_data":
        if record["value"] is not None:
            raise ValueError("blocked_by_data feature value must be None")
        _require_nonempty_text(record.get("data_gap"), "data_gap")
    else:
        _require_finite(record["value"], "value")
        if "data_gap" in record:
            raise ValueError("data_gap is forbidden unless status is blocked_by_data")

    uncertainty = record["uncertainty"]
    if uncertainty is not None and not isinstance(uncertainty, Mapping):
        if _require_finite(uncertainty, "uncertainty") < 0:
            raise ValueError("uncertainty must be non-negative")

    if (
        observable_kind == "axial_dipole_moment"
        and status == "available"
        and record["source_kind"] not in AXIAL_ALLOWED_SOURCE_KINDS
    ):
        raise ValueError(
            "axial dipole values require a registered axial dipole product or "
            "a harmonic derived from registered synoptic maps"
        )

    return deepcopy(dict(record))


def classify_forecast_skill(
    *,
    execution_completed: bool,
    data_available: bool,
    mae_improvement: float | None = None,
    ci_low: float | None = None,
    ci_high: float | None = None,
    regime_consistent: bool | None = None,
) -> str:
    """Classify forecast skill from the pre-registered deterministic gates."""

    if not execution_completed:
        return "execution_failed"
    if not data_available:
        return "blocked_by_data"
    if regime_consistent is None or any(
        value is None or not math.isfinite(float(value))
        for value in (mae_improvement, ci_low, ci_high)
    ):
        raise ValueError("completed forecast classification requires finite metrics")
    if not isinstance(regime_consistent, bool):
        raise ValueError("completed forecast classification requires regime_consistent")
    assert mae_improvement is not None
    assert ci_low is not None
    if mae_improvement <= 0:
        return "tested_no_skill"
    if ci_low > 0 and regime_consistent:
        return "skill_supported"
    return "mixed_evidence"


def _require_cycle_list(value: object, field: str, *, allow_empty: bool = False) -> list[int]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be a list of cycle numbers")
    cycles = [_require_cycle(item, field) for item in value]
    if not allow_empty and not cycles:
        raise ValueError(f"{field} must not be empty")
    if cycles != sorted(cycles) or len(cycles) != len(set(cycles)):
        raise ValueError(f"{field} must be ordered and unique")
    return cycles


def validate_forecast_experiment_receipt(value: object) -> dict[str, object]:
    """Validate a deterministic rolling-origin forecast experiment receipt."""

    receipt = _require_mapping(value, "forecast experiment receipt")
    _require_fields(receipt, _EXPERIMENT_FIELDS, "forecast experiment receipt")
    if receipt["schema_version"] != EXPERIMENT_VERSION:
        raise ValueError(f"schema_version must be {EXPERIMENT_VERSION}")
    for field in ("experiment_id", "forecast_origin", "candidate_name"):
        _require_nonempty_text(receipt[field], field)
    status = receipt["status"]
    if status not in FORECAST_STATUSES:
        raise ValueError(f"status must be one of {sorted(FORECAST_STATUSES)}")

    allow_empty = status in {"blocked_by_data", "execution_failed"}
    _require_string_list(receipt["hypothesis_ids"], "hypothesis_ids")
    _require_string_list(receipt["feature_ids"], "feature_ids", allow_empty=allow_empty)
    baselines = _require_string_list(receipt["baseline_names"], "baseline_names")
    if "training_mean" not in baselines or "persistence" not in baselines:
        raise ValueError("baseline_names must include training_mean and persistence")
    _require_cycle_list(receipt["training_cycles"], "training_cycles", allow_empty=allow_empty)
    _require_cycle_list(receipt["test_cycles"], "test_cycles", allow_empty=allow_empty)

    folds = receipt["folds"]
    if not isinstance(folds, Sequence) or isinstance(folds, (str, bytes)):
        raise ValueError("folds must be a list")
    if not allow_empty and not folds:
        raise ValueError("completed forecast receipt requires folds")
    for index, raw_fold in enumerate(folds):
        fold = _require_mapping(raw_fold, f"folds[{index}]")
        required = {
            "training_cycles",
            "test_cycle",
            "observed",
            "candidate_prediction",
            "training_mean_prediction",
            "persistence_prediction",
            "measurement_regime",
        }
        _require_fields(fold, required, f"folds[{index}]")
        train = _require_cycle_list(fold["training_cycles"], f"folds[{index}].training_cycles")
        test_cycle = _require_cycle(fold["test_cycle"], f"folds[{index}].test_cycle")
        if train and max(train) >= test_cycle:
            raise ValueError("every training cycle must precede its test cycle")
        for field in (
            "observed",
            "candidate_prediction",
            "training_mean_prediction",
            "persistence_prediction",
        ):
            _require_finite(fold[field], f"folds[{index}].{field}")
        _require_nonempty_text(fold["measurement_regime"], f"folds[{index}].measurement_regime")

    metrics = _require_mapping(receipt["metrics"], "metrics")
    metric_fields = {
        "candidate_mae",
        "candidate_rmse",
        "training_mean_mae",
        "training_mean_rmse",
        "persistence_mae",
        "persistence_rmse",
        "mae_improvement",
        "mae_improvement_interval",
    }
    _require_fields(metrics, metric_fields, "metrics")
    for field in metric_fields.difference({"mae_improvement_interval"}):
        _require_finite(metrics[field], f"metrics.{field}")
    interval = metrics["mae_improvement_interval"]
    if not isinstance(interval, Sequence) or isinstance(interval, (str, bytes)) or len(interval) != 2:
        raise ValueError("metrics.mae_improvement_interval must contain two bounds")
    low = _require_finite(interval[0], "metrics.mae_improvement_interval[0]")
    high = _require_finite(interval[1], "metrics.mae_improvement_interval[1]")
    if low > high:
        raise ValueError("metrics.mae_improvement_interval bounds are reversed")

    bootstrap = _require_mapping(receipt["bootstrap"], "bootstrap")
    _require_fields(bootstrap, {"seed", "resamples"}, "bootstrap")
    if not isinstance(bootstrap["seed"], int) or isinstance(bootstrap["seed"], bool):
        raise ValueError("bootstrap.seed must be an integer")
    if (
        not isinstance(bootstrap["resamples"], int)
        or isinstance(bootstrap["resamples"], bool)
        or bootstrap["resamples"] <= 0
    ):
        raise ValueError("bootstrap.resamples must be a positive integer")

    sensitivity = _require_mapping(receipt["sensitivity"], "sensitivity")
    _require_fields(
        sensitivity,
        {"measurement_regimes", "regime_consistent", "leave_one_fold"},
        "sensitivity",
    )
    if not isinstance(sensitivity["measurement_regimes"], Mapping):
        raise ValueError("sensitivity.measurement_regimes must be a mapping")
    if not isinstance(sensitivity["regime_consistent"], bool):
        raise ValueError("sensitivity.regime_consistent must be boolean")
    if not isinstance(sensitivity["leave_one_fold"], Sequence) or isinstance(
        sensitivity["leave_one_fold"], (str, bytes)
    ):
        raise ValueError("sensitivity.leave_one_fold must be a list")

    leakage = _require_mapping(receipt["leakage_audit"], "leakage_audit")
    _require_fields(leakage, {"passed", "rule"}, "leakage_audit")
    if leakage["passed"] is not True and not allow_empty:
        raise ValueError("completed forecast receipt requires a passed leakage audit")
    _require_nonempty_text(leakage["rule"], "leakage_audit.rule")

    return deepcopy(dict(receipt))
