"""Controlled upgrades for the H2 polar-precursor forecast.

The module keeps the registered mean polar-field model as the primary skill
gate and adds predeclared, physically interpretable sensitivity models.  A
provisional next-cycle check is reported separately and can never enter the
historical skill verdict or its bootstrap interval.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np

from .contracts import classify_forecast_skill

DEFAULT_SEED = 20260828
DEFAULT_BOOTSTRAP_RESAMPLES = 10_000
INITIAL_TRAINING_CYCLES = 5


def _as_float(row: Mapping[str, Any], key: str, index: int) -> float:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"rows[{index}].{key} must be numeric")
    result = float(value)
    if not np.isfinite(result):
        raise ValueError(f"rows[{index}].{key} must be finite")
    return result


def _validate_rows(
    rows: Sequence[Mapping[str, Any]], label: str
) -> list[dict[str, Any]]:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ValueError(f"{label} must be a sequence")
    required = {
        "target_cycle_id",
        "polar_mean_abs_gauss",
        "weakest_hemisphere_abs_gauss",
        "target",
        "target_dispersion",
        "measurement_regime",
    }
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise ValueError(f"{label}[{index}] must be a mapping")
        missing = sorted(required.difference(raw))
        if missing:
            raise ValueError(f"{label}[{index}] missing: {', '.join(missing)}")
        cycle = raw["target_cycle_id"]
        if isinstance(cycle, bool) or not isinstance(cycle, int):
            raise ValueError(f"{label}[{index}].target_cycle_id must be an integer")
        mean = _as_float(raw, "polar_mean_abs_gauss", index)
        weak = _as_float(raw, "weakest_hemisphere_abs_gauss", index)
        dispersion = _as_float(raw, "target_dispersion", index)
        if mean < 0 or weak < 0:
            raise ValueError(f"{label}[{index}] polar fields must be non-negative")
        if dispersion <= 0:
            raise ValueError(f"{label}[{index}].target_dispersion must be positive")
        regime = raw["measurement_regime"]
        if not isinstance(regime, str) or not regime.strip():
            raise ValueError(f"{label}[{index}].measurement_regime must be non-empty")
        normalized.append(
            {
                **dict(raw),
                "target_cycle_id": cycle,
                "polar_mean_abs_gauss": mean,
                "weakest_hemisphere_abs_gauss": weak,
                "target": _as_float(raw, "target", index),
                "target_dispersion": dispersion,
                "measurement_regime": regime.strip(),
            }
        )
    normalized.sort(key=lambda row: int(row["target_cycle_id"]))
    cycles = [int(row["target_cycle_id"]) for row in normalized]
    if len(normalized) < INITIAL_TRAINING_CYCLES + 2:
        raise ValueError("H2 upgrade requires at least seven finalized cycles")
    if cycles != list(range(cycles[0], cycles[-1] + 1)):
        raise ValueError("finalized H2 cycles must be consecutive")
    return normalized


def _fit_linear(
    x: np.ndarray,
    y: np.ndarray,
    xt: float,
    transform: Callable[[np.ndarray], np.ndarray] | None = None,
    weights: np.ndarray | None = None,
) -> float:
    transform = transform or (lambda values: values)
    tx = transform(np.asarray(x, dtype=float))
    ttest = float(transform(np.asarray([xt], dtype=float))[0])
    design = np.column_stack([np.ones(len(tx)), tx])
    if weights is None:
        beta = np.linalg.lstsq(design, y, rcond=None)[0]
    else:
        diagonal = np.asarray(weights, dtype=float)
        beta = np.linalg.solve(
            design.T @ (diagonal[:, None] * design),
            design.T @ (diagonal * y),
        )
    return float(np.array([1.0, ttest]) @ beta)


def _folds(rows: Sequence[Mapping[str, Any]], model: str) -> list[dict[str, Any]]:
    folds: list[dict[str, Any]] = []
    for test_index in range(INITIAL_TRAINING_CYCLES, len(rows)):
        train = rows[:test_index]
        test = rows[test_index]
        y = np.asarray([float(row["target"]) for row in train], dtype=float)
        if model == "mean_polar_linear":
            x = np.asarray([float(row["polar_mean_abs_gauss"]) for row in train])
            xt = float(test["polar_mean_abs_gauss"])
            prediction = _fit_linear(x, y, xt)
        elif model == "sqrt_mean_polar_linear":
            x = np.asarray([float(row["polar_mean_abs_gauss"]) for row in train])
            prediction = _fit_linear(x, y, float(test["polar_mean_abs_gauss"]), np.sqrt)
        elif model == "target_dispersion_weighted_linear":
            x = np.asarray([float(row["polar_mean_abs_gauss"]) for row in train])
            weights = np.asarray(
                [1.0 / float(row["target_dispersion"]) ** 2 for row in train],
                dtype=float,
            )
            prediction = _fit_linear(
                x, y, float(test["polar_mean_abs_gauss"]), weights=weights
            )
        elif model == "weakest_hemisphere_linear":
            x = np.asarray(
                [float(row["weakest_hemisphere_abs_gauss"]) for row in train]
            )
            prediction = _fit_linear(x, y, float(test["weakest_hemisphere_abs_gauss"]))
        else:
            raise ValueError(f"unknown H2 model: {model}")
        training_mean = float(np.mean(y))
        persistence = float(y[-1])
        folds.append(
            {
                "training_cycles": [int(row["target_cycle_id"]) for row in train],
                "test_cycle": int(test["target_cycle_id"]),
                "observed": float(test["target"]),
                "candidate_prediction": prediction,
                "training_mean_prediction": training_mean,
                "persistence_prediction": persistence,
                "measurement_regime": str(test["measurement_regime"]),
            }
        )
    return folds


def _summarize(
    folds: Sequence[Mapping[str, Any]], seed: int, resamples: int
) -> dict[str, Any]:
    observed = np.asarray([float(fold["observed"]) for fold in folds])
    candidate = np.asarray([float(fold["candidate_prediction"]) for fold in folds])
    baseline = np.asarray([float(fold["training_mean_prediction"]) for fold in folds])
    persistence = np.asarray([float(fold["persistence_prediction"]) for fold in folds])
    candidate_error = np.abs(observed - candidate)
    baseline_error = np.abs(observed - baseline)
    persistence_error = np.abs(observed - persistence)
    improvement_by_fold = baseline_error - candidate_error
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(folds), size=(resamples, len(folds)))
    interval = np.quantile(improvement_by_fold[indices].mean(axis=1), [0.025, 0.975])
    regimes: dict[str, dict[str, Any]] = {}
    for regime in sorted({str(fold["measurement_regime"]) for fold in folds}):
        idx = np.asarray(
            [
                i
                for i, fold in enumerate(folds)
                if str(fold["measurement_regime"]) == regime
            ],
            dtype=int,
        )
        regimes[regime] = {
            "fold_count": len(idx),
            "mae_improvement": float(np.mean(improvement_by_fold[idx])),
            "eligible_for_consistency": bool(len(idx) >= 2),
        }
    eligible = [item for item in regimes.values() if item["eligible_for_consistency"]]
    sign = np.sign(float(np.mean(improvement_by_fold)))
    regime_consistent = bool(eligible) and all(
        np.sign(float(item["mae_improvement"])) == sign for item in eligible
    )
    return {
        "candidate_mae": float(np.mean(candidate_error)),
        "candidate_rmse": float(np.sqrt(np.mean((observed - candidate) ** 2))),
        "training_mean_mae": float(np.mean(baseline_error)),
        "training_mean_rmse": float(np.sqrt(np.mean((observed - baseline) ** 2))),
        "persistence_mae": float(np.mean(persistence_error)),
        "persistence_rmse": float(np.sqrt(np.mean((observed - persistence) ** 2))),
        "mae_improvement": float(np.mean(improvement_by_fold)),
        "mae_improvement_interval": [float(interval[0]), float(interval[1])],
        "improvement_by_fold": [float(value) for value in improvement_by_fold],
        "regimes": regimes,
        "regime_consistent": regime_consistent,
        "bootstrap": {"seed": seed, "resamples": resamples},
    }


def _provisional_check(
    rows: Sequence[Mapping[str, Any]], model: str, provisional: Mapping[str, Any]
) -> dict[str, Any]:
    cycle = provisional["target_cycle_id"]
    x = np.asarray([float(row["polar_mean_abs_gauss"]) for row in rows], dtype=float)
    y = np.asarray([float(row["target"]) for row in rows], dtype=float)
    if model == "mean_polar_linear":
        prediction = _fit_linear(x, y, float(provisional["polar_mean_abs_gauss"]))
    elif model == "sqrt_mean_polar_linear":
        prediction = _fit_linear(
            x, y, float(provisional["polar_mean_abs_gauss"]), np.sqrt
        )
    elif model == "target_dispersion_weighted_linear":
        weights = np.asarray(
            [1.0 / float(row["target_dispersion"]) ** 2 for row in rows]
        )
        prediction = _fit_linear(
            x, y, float(provisional["polar_mean_abs_gauss"]), weights=weights
        )
    elif model == "weakest_hemisphere_linear":
        x = np.asarray([float(row["weakest_hemisphere_abs_gauss"]) for row in rows])
        prediction = _fit_linear(
            x, y, float(provisional["weakest_hemisphere_abs_gauss"])
        )
    else:
        raise ValueError(f"unknown H2 model: {model}")
    target = float(provisional["target"])
    baseline = float(np.mean(y))
    return {
        "target_cycle_id": int(cycle),
        "target_status": str(provisional.get("target_status", "provisional")),
        "training_cycles": [int(row["target_cycle_id"]) for row in rows],
        "candidate_prediction": prediction,
        "observed_provisional_target": target,
        "absolute_error": abs(target - prediction),
        "training_mean_prediction": baseline,
        "baseline_absolute_error": abs(target - baseline),
        "excluded_from_skill_gate": True,
    }


def run_h2_upgrade(
    rows: Sequence[Mapping[str, Any]],
    *,
    provisional_row: Mapping[str, Any] | None = None,
    seed: int = DEFAULT_SEED,
    bootstrap_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
) -> dict[str, Any]:
    """Run the predeclared H2 tournament and optional provisional check."""

    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if (
        isinstance(bootstrap_resamples, bool)
        or not isinstance(bootstrap_resamples, int)
        or bootstrap_resamples <= 0
    ):
        raise ValueError("bootstrap_resamples must be positive")
    finalized = _validate_rows(rows, "rows")
    models: dict[str, dict[str, Any]] = {}
    model_names = (
        "mean_polar_linear",
        "sqrt_mean_polar_linear",
        "target_dispersion_weighted_linear",
        "weakest_hemisphere_linear",
    )
    for model in model_names:
        folds = _folds(finalized, model)
        models[model] = {
            "folds": folds,
            "test_cycles": [int(fold["test_cycle"]) for fold in folds],
            "metrics": _summarize(folds, seed, bootstrap_resamples),
        }
    primary_metrics = models["mean_polar_linear"]["metrics"]
    status = classify_forecast_skill(
        execution_completed=True,
        data_available=True,
        mae_improvement=float(primary_metrics["mae_improvement"]),
        ci_low=float(primary_metrics["mae_improvement_interval"][0]),
        ci_high=float(primary_metrics["mae_improvement_interval"][1]),
        regime_consistent=bool(primary_metrics["regime_consistent"]),
    )
    result: dict[str, Any] = {
        "schema_version": "solar-h2-upgrade-receipt-v1",
        "status": status,
        "skill_gate_model": "mean_polar_linear",
        "models": models,
        "challenger_policy": "exploratory_not_promoted",
        "selected_challenger": None,
        "finalized_cycles": [int(row["target_cycle_id"]) for row in finalized],
        "bootstrap": {"seed": seed, "resamples": bootstrap_resamples},
    }
    if provisional_row is not None:
        provisional = dict(provisional_row)
        cycle = provisional.get("target_cycle_id")
        if cycle != int(finalized[-1]["target_cycle_id"]) + 1:
            raise ValueError("provisional cycle must follow finalized history")
        result["provisional_check"] = _provisional_check(
            finalized, "mean_polar_linear", provisional
        )
    return result


__all__ = ["run_h2_upgrade"]
