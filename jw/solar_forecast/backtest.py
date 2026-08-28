"""Deterministic rolling-origin tournament for solar polar precursors."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from .contracts import (
    AXIAL_ALLOWED_SOURCE_KINDS,
    EXPERIMENT_VERSION,
    classify_forecast_skill,
    validate_forecast_experiment_receipt,
)

INITIAL_TRAINING_CYCLES = 5


def _finite_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def _normalize_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    label: str,
) -> list[dict[str, object]]:
    if len(rows) < INITIAL_TRAINING_CYCLES + 2:
        raise ValueError(f"{label} requires at least seven ordered cycles")
    normalized: list[dict[str, object]] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise ValueError(f"{label}[{index}] must be a mapping")
        required = {
            "feature_id",
            "hypothesis_id",
            "target_cycle_id",
            "value",
            "target",
            "measurement_regime",
        }
        missing = sorted(required.difference(raw))
        if missing:
            raise ValueError(f"{label}[{index}] missing fields: {', '.join(missing)}")
        cycle = raw["target_cycle_id"]
        if isinstance(cycle, bool) or not isinstance(cycle, int):
            raise ValueError(f"{label}[{index}].target_cycle_id must be an integer")
        feature_id = raw["feature_id"]
        hypothesis_id = raw["hypothesis_id"]
        regime = raw["measurement_regime"]
        if not isinstance(feature_id, str) or not feature_id.strip():
            raise ValueError(f"{label}[{index}].feature_id must be non-empty")
        if not isinstance(hypothesis_id, str) or not hypothesis_id.strip():
            raise ValueError(f"{label}[{index}].hypothesis_id must be non-empty")
        if not isinstance(regime, str) or not regime.strip():
            raise ValueError(f"{label}[{index}].measurement_regime must be non-empty")
        normalized.append(
            {
                **dict(raw),
                "feature_id": feature_id.strip(),
                "hypothesis_id": hypothesis_id.strip(),
                "target_cycle_id": cycle,
                "value": _finite_number(raw["value"], f"{label}[{index}].value"),
                "target": _finite_number(raw["target"], f"{label}[{index}].target"),
                "measurement_regime": regime.strip(),
            }
        )
    normalized.sort(key=lambda row: int(row["target_cycle_id"]))
    cycles = [int(row["target_cycle_id"]) for row in normalized]
    if len(cycles) != len(set(cycles)):
        raise ValueError(f"{label} target cycles must be unique")
    return normalized


def _fit_line(train_x: np.ndarray, train_y: np.ndarray, test_x: float) -> float:
    design = np.column_stack([np.ones(len(train_x)), train_x])
    intercept, slope = np.linalg.lstsq(design, train_y, rcond=None)[0]
    return float(intercept + slope * test_x)


def _rolling_folds(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    folds: list[dict[str, object]] = []
    for test_index in range(INITIAL_TRAINING_CYCLES, len(rows)):
        train = rows[:test_index]
        test = rows[test_index]
        train_x = np.asarray([float(row["value"]) for row in train], dtype=float)
        train_y = np.asarray([float(row["target"]) for row in train], dtype=float)
        prediction = _fit_line(train_x, train_y, float(test["value"]))
        folds.append(
            {
                "training_cycles": [int(row["target_cycle_id"]) for row in train],
                "test_cycle": int(test["target_cycle_id"]),
                "feature_id": str(test["feature_id"]),
                "observed": float(test["target"]),
                "candidate_prediction": prediction,
                "training_mean_prediction": float(np.mean(train_y)),
                "persistence_prediction": float(train_y[-1]),
                "measurement_regime": str(test["measurement_regime"]),
            }
        )
    return folds


def _error_summary(
    observed: np.ndarray,
    predicted: np.ndarray,
) -> tuple[float, float]:
    residual = observed - predicted
    return float(np.mean(np.abs(residual))), float(np.sqrt(np.mean(residual**2)))


def _paired_interval(
    improvement_by_fold: np.ndarray,
    *,
    seed: int,
    resamples: int,
) -> list[float]:
    if resamples <= 0:
        raise ValueError("bootstrap_resamples must be positive")
    rng = np.random.default_rng(seed)
    indices = rng.integers(
        0,
        len(improvement_by_fold),
        size=(resamples, len(improvement_by_fold)),
    )
    bootstrapped = improvement_by_fold[indices].mean(axis=1)
    low, high = np.quantile(bootstrapped, [0.025, 0.975])
    return [float(low), float(high)]


def _summarize(
    folds: Sequence[Mapping[str, object]],
    *,
    seed: int,
    bootstrap_resamples: int,
) -> tuple[dict[str, object], dict[str, object]]:
    observed = np.asarray([float(fold["observed"]) for fold in folds], dtype=float)
    candidate = np.asarray(
        [float(fold["candidate_prediction"]) for fold in folds], dtype=float
    )
    training_mean = np.asarray(
        [float(fold["training_mean_prediction"]) for fold in folds], dtype=float
    )
    persistence = np.asarray(
        [float(fold["persistence_prediction"]) for fold in folds], dtype=float
    )
    candidate_mae, candidate_rmse = _error_summary(observed, candidate)
    mean_mae, mean_rmse = _error_summary(observed, training_mean)
    persistence_mae, persistence_rmse = _error_summary(observed, persistence)
    improvement_by_fold = np.abs(observed - training_mean) - np.abs(
        observed - candidate
    )
    interval = _paired_interval(
        improvement_by_fold,
        seed=seed,
        resamples=bootstrap_resamples,
    )
    metrics: dict[str, object] = {
        "candidate_mae": candidate_mae,
        "candidate_rmse": candidate_rmse,
        "training_mean_mae": mean_mae,
        "training_mean_rmse": mean_rmse,
        "persistence_mae": persistence_mae,
        "persistence_rmse": persistence_rmse,
        "mae_improvement": float(np.mean(improvement_by_fold)),
        "mae_improvement_interval": interval,
    }

    regimes: dict[str, dict[str, object]] = {}
    for regime in sorted({str(fold["measurement_regime"]) for fold in folds}):
        regime_indices = np.asarray(
            [
                index
                for index, fold in enumerate(folds)
                if str(fold["measurement_regime"]) == regime
            ],
            dtype=int,
        )
        regimes[regime] = {
            "fold_count": len(regime_indices),
            "mae_improvement": float(np.mean(improvement_by_fold[regime_indices])),
            "eligible_for_consistency": bool(len(regime_indices) >= 2),
        }
    overall_sign = np.sign(float(metrics["mae_improvement"]))
    eligible_regimes = [
        item for item in regimes.values() if item["eligible_for_consistency"]
    ]
    regime_consistent = bool(eligible_regimes) and all(
        np.sign(float(item["mae_improvement"])) == overall_sign
        for item in eligible_regimes
    )

    leave_one_fold = []
    for omitted, fold in enumerate(folds):
        retained = np.delete(improvement_by_fold, omitted)
        leave_one_fold.append(
            {
                "omitted_test_cycle": int(fold["test_cycle"]),
                "mae_improvement": float(np.mean(retained)),
            }
        )
    sensitivity: dict[str, object] = {
        "measurement_regimes": regimes,
        "regime_consistent": regime_consistent,
        "leave_one_fold": leave_one_fold,
    }
    return metrics, sensitivity


def _validate_axial_rows(
    rows: Sequence[Mapping[str, object]],
    expected_cycles: Sequence[int],
) -> list[dict[str, object]]:
    normalized = _normalize_rows(rows, label="discriminator_rows")
    cycles = [int(row["target_cycle_id"]) for row in normalized]
    if cycles != list(expected_cycles):
        raise ValueError("axial_dipole_moment comparison requires identical cycles")
    if any(
        row.get("observable_kind") != "axial_dipole_moment"
        or row.get("source_kind") not in AXIAL_ALLOWED_SOURCE_KINDS
        for row in normalized
    ):
        raise ValueError(
            "axial_dipole_moment comparison requires registered axial-dipole "
            "values or registered synoptic-map harmonics"
        )
    return normalized


def _discriminator_comparison(
    polar_folds: Sequence[Mapping[str, object]],
    axial_rows: Sequence[Mapping[str, object]],
    *,
    seed: int,
    bootstrap_resamples: int,
) -> dict[str, object]:
    axial_folds = _rolling_folds(axial_rows)
    polar_error = np.asarray(
        [
            abs(float(fold["observed"]) - float(fold["candidate_prediction"]))
            for fold in polar_folds
        ],
        dtype=float,
    )
    axial_error = np.asarray(
        [
            abs(float(fold["observed"]) - float(fold["candidate_prediction"]))
            for fold in axial_folds
        ],
        dtype=float,
    )
    improvement = polar_error - axial_error
    interval = _paired_interval(
        improvement,
        seed=seed,
        resamples=bootstrap_resamples,
    )
    point = float(np.mean(improvement))
    status = classify_forecast_skill(
        execution_completed=True,
        data_available=True,
        mae_improvement=point,
        ci_low=interval[0],
        ci_high=interval[1],
        regime_consistent=True,
    )
    return {
        "hypothesis_id": "h3_axial_dipole_discriminator",
        "observable_kind": "axial_dipole_moment",
        "reference_observable_kind": "polar_aperture_field",
        "complexity_matched": True,
        "test_cycles": [int(fold["test_cycle"]) for fold in axial_folds],
        "feature_ids": [str(fold["feature_id"]) for fold in axial_folds],
        "axial_mae": float(np.mean(axial_error)),
        "polar_aperture_mae": float(np.mean(polar_error)),
        "mae_improvement_over_polar": point,
        "mae_improvement_interval": interval,
        "status": status,
    }


def run_precursor_backtest(
    rows: Sequence[Mapping[str, object]],
    *,
    discriminator_rows: Sequence[Mapping[str, object]] | None = None,
    seed: int = 20260828,
    bootstrap_resamples: int = 10_000,
) -> dict[str, object]:
    """Run the frozen five-cycle expanding-window precursor tournament."""

    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    ordered = _normalize_rows(rows, label="rows")
    folds = _rolling_folds(ordered)
    metrics, sensitivity = _summarize(
        folds,
        seed=seed,
        bootstrap_resamples=bootstrap_resamples,
    )
    interval = metrics["mae_improvement_interval"]
    assert isinstance(interval, list)
    status = classify_forecast_skill(
        execution_completed=True,
        data_available=True,
        mae_improvement=float(metrics["mae_improvement"]),
        ci_low=float(interval[0]),
        ci_high=float(interval[1]),
        regime_consistent=bool(sensitivity["regime_consistent"]),
    )
    receipt: dict[str, Any] = {
        "schema_version": EXPERIMENT_VERSION,
        "experiment_id": "polar-precursor-rolling-origin-v1",
        "status": status,
        "forecast_origin": "cycle_minimum",
        "hypothesis_ids": sorted(
            {str(row["hypothesis_id"]) for row in ordered}
        ),
        "feature_ids": [str(row["feature_id"]) for row in ordered],
        "baseline_names": ["training_mean", "persistence"],
        "candidate_name": "linear_polar_precursor",
        "training_cycles": [
            int(row["target_cycle_id"])
            for row in ordered[:INITIAL_TRAINING_CYCLES]
        ],
        "test_cycles": [int(fold["test_cycle"]) for fold in folds],
        "folds": folds,
        "metrics": metrics,
        "bootstrap": {"seed": seed, "resamples": bootstrap_resamples},
        "sensitivity": sensitivity,
        "leakage_audit": {
            "passed": all(
                max(fold["training_cycles"]) < int(fold["test_cycle"])
                for fold in folds
            ),
            "rule": "every training cycle precedes its held-out test cycle",
        },
    }
    if discriminator_rows is not None:
        axial = _validate_axial_rows(
            discriminator_rows,
            [int(row["target_cycle_id"]) for row in ordered],
        )
        receipt["discriminator_comparison"] = _discriminator_comparison(
            folds,
            axial,
            seed=seed,
            bootstrap_resamples=bootstrap_resamples,
        )
    return validate_forecast_experiment_receipt(receipt)
