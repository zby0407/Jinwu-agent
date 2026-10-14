"""Extended frozen-model mathematics; production outputs omit probability MAE.

The unchanged earlier math core supplies model factories and probability checks.
This wrapper owns final refit, output metrics, test descriptive permutation and CI.
Importing it does not fit, predict or draw a model.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, clone

CORE_SHA = "0582ae6f2085e18029b640c22a95b7be634c4e593560283c004b91ebec8c93d3"
SEED = 20261011
FEATURES = [f"history_{h}h_{a}" for h in (24, 72, 168) for a in ("c_count", "m_count", "x_count", "max_peak_w_m2")]
TARGETS = ("C1.0+", "M1.0+")
METRIC_NAMES = ("Accuracy", "R2_binary_probability_auxiliary", "Brier",
                "Brier_skill_vs_train_climatology", "ROC_AUC", "average_precision",
                "TSS", "precision", "recall", "false_alarm_ratio")


def _load_core():
    path = Path(__file__).with_name("frozen_math_core.py")
    if hashlib.sha256(path.read_bytes()).hexdigest() != CORE_SHA:
        raise ValueError("Unchanged frozen math core differs")
    spec = importlib.util.spec_from_file_location("extended_unchanged_math_core", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = _load_core()
candidate_models = core.candidate_models
probability_matrix = core.probability_matrix
target_probabilities = core.target_probabilities
apply_temperature = core.apply_temperature
fit_temperature = core.fit_temperature
reliability_bins = core.reliability_bins
dual_brier = core.dual_brier


def binary_metrics(y, p, threshold, climatology):
    result = core.binary_metrics(y, p, threshold, climatology)
    result.pop("MAE_probability")
    return result


def parameter_signature(value):
    """JSON-safe signatures include scaler/model configuration, never fitted data."""
    if isinstance(value, BaseEstimator):
        return {"estimator": type(value).__module__ + "." + type(value).__name__,
                "parameters": parameter_signature(value.get_params(deep=False))}
    if isinstance(value, dict):
        return {str(k): parameter_signature(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [parameter_signature(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("Unexpected estimator configuration type")


def fit_selection_then_refit(x_train, y_train, x_selection, y_selection, candidates=None, on_selected=None):
    selected_id, candidate_selected, scores = core.fit_select(
        x_train, y_train, x_selection, y_selection, candidates=candidates)
    locked = parameter_signature(candidate_selected)
    if on_selected is not None:
        on_selected({"selected_model_id": selected_id, "locked_configuration_signature": locked,
                     "selection_scores": scores, "selection_role": "selection_2021_only",
                     "test_accessed_for_choice": False, "calibration_accessed_for_choice": False,
                     "final_refit_started": False})
    final = clone(candidate_selected)
    if parameter_signature(final) != locked:
        raise ValueError("Refit clone changed locked configuration")
    x_final = np.concatenate((x_train, x_selection), axis=0)
    y_final = np.concatenate((y_train, y_selection), axis=0)
    started = time.monotonic()
    final.fit(x_final, y_final)
    if parameter_signature(final) != locked:
        raise ValueError("Final fit changed registered configuration")
    return selected_id, final, scores, {
        "selected_model_id": selected_id,
        "candidate_fit_rows": len(y_train), "selection_rows": len(y_selection),
        "final_refit_rows": len(y_final), "final_refit_calls": 1,
        "final_refit_roles": ["train", "selection"],
        "excluded_fit_roles": ["calibration", "test", "live"],
        "new_gap_rows_added": False, "locked_configuration_signature": locked,
        "clone_configuration_unchanged": True,
        "final_fit_wall_seconds": time.monotonic()-started,
        "selection_score_role": "selection_2021_only_before_final_refit",
        "selection_scores_recomputed_after_final_refit": False}


def permutation_scores(model, x, y, temperature, repeats=10, seed=SEED):
    rng = np.random.default_rng(seed)
    base = target_probabilities(apply_temperature(probability_matrix(model, x), temperature))
    baseline = [float(np.mean((base[TARGETS[0]]-(y >= 1))**2)),
                float(np.mean((base[TARGETS[1]]-(y == 2))**2))]
    records = []
    for j, feature in enumerate(FEATURES):
        differences = []
        for _ in range(repeats):
            perturbed = x.copy()
            perturbed[:, j] = perturbed[rng.permutation(len(x)), j]
            p = target_probabilities(apply_temperature(probability_matrix(model, perturbed), temperature))
            differences.append([float(np.mean((p[TARGETS[0]]-(y >= 1))**2))-baseline[0],
                                float(np.mean((p[TARGETS[1]]-(y == 2))**2))-baseline[1]])
        a = np.asarray(differences)
        records.append({"feature": feature, "C_Brier_increase_mean": float(a[:, 0].mean()),
                        "C_Brier_increase_std": float(a[:, 0].std(ddof=0)),
                        "M_Brier_increase_mean": float(a[:, 1].mean()),
                        "M_Brier_increase_std": float(a[:, 1].std(ddof=0)),
                        "mean_Brier_increase": float(a.mean(axis=1).mean()),
                        "mean_Brier_increase_std": float(a.mean(axis=1).std(ddof=0)),
                        "repeats": repeats, "role": "heldout_test_descriptive_permutation_no_feature_selection",
                        "use_for_model_feature_selection": False,
                        "repeat_values_json": json.dumps(a.tolist())})
    return pd.DataFrame(records)


def block_bootstrap(issues, outcomes, predictions, references, draws=200, seed=SEED):
    months = pd.to_datetime(issues, utc=True).dt.strftime("%Y-%m").to_numpy()
    groups = [np.where(months == m)[0] for m in sorted(set(months))]
    if not groups:
        raise ValueError("No heldout month blocks")
    rng, values = np.random.default_rng(seed), {}
    for _ in range(draws):
        idx = np.concatenate([groups[j] for j in rng.integers(0, len(groups), len(groups))])
        per_draw = {}
        for model_id, probabilities in predictions.items():
            for target in TARGETS:
                metrics = binary_metrics(outcomes[target][idx], probabilities[target][idx], .5, references[target])
                per_draw[(model_id, target)] = metrics
                for key in METRIC_NAMES:
                    if metrics[key] is not None:
                        values.setdefault((model_id, target, key), []).append(metrics[key])
        for baseline in predictions:
            if baseline == "selected_calibrated":
                continue
            for target in TARGETS:
                for metric in ("Brier", "ROC_AUC", "average_precision", "TSS"):
                    a, b = per_draw[("selected_calibrated", target)][metric], per_draw[(baseline, target)][metric]
                    if a is not None and b is not None:
                        values.setdefault(("selected_minus_"+baseline, target, metric), []).append(a-b)
    rows = []
    for model_id in [*predictions, *["selected_minus_"+k for k in predictions if k != "selected_calibrated"]]:
        paired = model_id.startswith("selected_minus_")
        for target in TARGETS:
            for metric in (("Brier", "ROC_AUC", "average_precision", "TSS") if paired else METRIC_NAMES):
                samples = values.get((model_id, target, metric), [])
                lo, hi = np.percentile(samples, (2.5, 97.5)).tolist() if samples else (None, None)
                rows.append({"model_id": model_id, "target": target, "metric": metric,
                             "ci_lower": lo, "ci_upper": hi,
                             "bootstrap_valid_draws": len(samples), "bootstrap_total_draws": draws,
                             "blocks": len(groups), "block_unit": "eligible_test_month",
                             "comparison_sign": "negative Brier favors selected; positive ROC/AP/TSS favors selected" if paired else "",
                             "method": "paired_month_block_percentile_95",
                             "interpretation": "historical sampling interval, not tomorrow correctness or physical confidence"})
    return pd.DataFrame(rows)
