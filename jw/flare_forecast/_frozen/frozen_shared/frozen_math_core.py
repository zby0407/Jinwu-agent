"""Frozen daily full-disk backtest: no network, no live forecast, no source edits.

Production fits and figures require an independently recorded JW execute receipt.
This script cannot certify the identity of its caller or issue a scientific verdict.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import traceback
from pathlib import Path, PurePosixPath

# Set before importing numerical libraries, including in the bounded worker.
for _thread_variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_thread_variable] = "4"

import joblib
import numpy as np
import pandas as pd
import scipy
import sklearn
from scipy.optimize import minimize_scalar
from scipy.special import softmax
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_info, threadpool_limits

SEED = 20261011
MAGNETICS = ["USFLUX", "TOTUSJH", "TOTUSJZ", "ABSNJZH", "SAVNCPP", "MEANALP", "R_VALUE", "TOTPOT", "MEANSHR", "AREA_ACR"]
FEATURES = [f"{m}_{a}" for m in MAGNETICS for a in ("max", "median")] + ["patch_count"]
HISTORY_FEATURES = [f"history_{h}h_{n}" for h in (24, 72, 168) for n in ("c_count", "m_count", "x_count", "max_peak_w_m2")]
FEATURES += HISTORY_FEATURES
SPLITS = {
    "train": ("2011-01-08", "2013-12-23"),
    "selection": ("2014-01-08", "2014-12-23"),
    "calibration": ("2015-01-08", "2015-06-23"),
    "test": ("2015-07-08", "2016-12-31"),
}
TARGETS = ("C1.0+", "M1.0+")
THRESHOLD_GRID = np.arange(1, 20, dtype=float) / 20
EPSILON = 1e-12
COVERAGE_AMENDMENT_SHA256 = "dfc6d07b21247fb09a9c2d2592c78a5958e78407a1986cbf65a3cf88dd701307"
SCIENCE_COVERAGE_AMENDMENT_SHA256 = "95ce9339915ee8c7b4617412fb7b4bbc4dfee6cef1ec1f1574674b3816590343"
BASE_V1_SCRIPT_SHA256 = "42aa1b7b87338686e9db6c00fbc8a08d0ff3548e49a9d40821ee2a877711d30b"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def classify_dates(values: pd.Series) -> pd.Series:
    if not values.astype(str).str.fullmatch(r"\d{4}-\d{2}-\d{2}T00:00:00Z").all():
        raise ValueError("Issues require explicit canonical UTC midnight ISO Z timestamps")
    parsed = pd.to_datetime(values, utc=True, errors="raise")
    if ((parsed.dt.hour != 0) | (parsed.dt.minute != 0) | (parsed.dt.second != 0) | (parsed.dt.microsecond != 0)).any():
        raise ValueError("Issues must be exactly daily UTC midnight")
    result = pd.Series("excluded", index=values.index, dtype="str")
    for name, (start, end) in SPLITS.items():
        result.loc[(parsed >= pd.Timestamp(start, tz="UTC")) & (parsed < pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1))] = name
    return result


def validate_contract(contract: dict) -> None:
    if contract.get("schema_version") != "solar-flare-forecast-task-v1":
        raise ValueError("Unknown forecast contract schema")
    if contract.get("forecast_mode") != "research_backtest" or contract.get("spatial_unit") != "full_disk":
        raise ValueError("Only registered retrospective full-disk task is permitted")
    if contract.get("target_thresholds") != list(TARGETS):
        raise ValueError("Forecast target thresholds differ from registration")
    if any(tuple(contract.get("splits", {}).get(k, [])) != v for k, v in SPLITS.items()):
        raise ValueError("Registered chronological split changed")
    fs = contract.get("feature_schema", {})
    if fs.get("magnetic_metrics") != MAGNETICS or fs.get("expected_feature_count") != 33:
        raise ValueError("Unexpected feature contract")
    if contract.get("models", {}).get("seed") != SEED:
        raise ValueError("Model seed changed")
    expected_candidates = {"logistic_regression": {"C": [.01, .1, 1, 10], "standard_scaler": "fit train only", "max_iter": 2000}, "random_forest": {"n_estimators": 200, "min_samples_leaf": [5, 15, 30], "max_depth": 8, "n_jobs": 4}, "hist_gradient_boosting": {"learning_rate": .05, "max_iter": 200, "max_leaf_nodes": [7, 15], "l2_regularization": 1}}
    if contract.get("models", {}).get("candidates") != expected_candidates or contract["models"].get("refit_after_selection") is not False or contract["models"].get("class_weights") is not None:
        raise ValueError("Registered model grid/refit/weights changed")
    if contract.get("resource_limits", {}).get("training_cpu_wall_seconds") != 900 or contract["resource_limits"].get("workers") != 4:
        raise ValueError("Registered runtime bounds changed")


def safe_child(root: Path, name: str) -> Path:
    p = PurePosixPath(name)
    if not name or p.is_absolute() or "\\" in name or ":" in name or any(v in {"", ".", ".."} for v in name.split("/")):
        raise ValueError("Unsafe prepared artifact path")
    result = (root / Path(*p.parts)).resolve()
    if not result.is_relative_to(root):
        raise ValueError("Prepared artifact escapes input root")
    return result


def checked_inputs(input_dir: Path, contract_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict, dict]:
    contract = json.loads(contract_path.read_text(encoding="utf-8-sig"))
    validate_contract(contract)
    manifest_path = input_dir / "prepared_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("schema_version") != "prepared-forecast-artifacts-v1" or manifest.get("contract_sha256") != digest(contract_path):
        raise ValueError("Prepared manifest/contract integrity mismatch")
    if manifest.get("coverage_amendment_sha256") != COVERAGE_AMENDMENT_SHA256:
        raise ValueError("Prepared data lacks registered independent coverage amendment")
    if manifest.get("science_coverage_amendment_sha256") != SCIENCE_COVERAGE_AMENDMENT_SHA256:
        raise ValueError("Prepared data lacks the frozen amendment03 science coverage source plan")
    schema = manifest.get("schema", {})
    if schema.get("features") != FEATURES:
        raise ValueError("Manifest does not contain exact registered 33-feature whitelist")
    expected_columns = {"issue_time_column": "issue_utc", "target_class_column": "y_class", "status_column": "status", "exclusion_reason_column": "exclusion_reasons"}
    if any(schema.get(k) != v for k, v in expected_columns.items()):
        raise ValueError("Prepared column interface changed")
    before, paths = {"prepared_manifest.json": digest(manifest_path)}, {}
    for record in manifest.get("files", []):
        name = record["path"]
        if name in paths or name == "prepared_manifest.json":
            raise ValueError("Duplicate/circular prepared artifact entry")
        path = safe_child(input_dir, name)
        if not path.is_file() or path.stat().st_size != record["bytes"] or digest(path) != record["sha256"]:
            raise ValueError(f"Prepared file integrity mismatch: {name}")
        paths[name], before[name] = path, record["sha256"]
    required = {"daily_instances.csv", "all_daily_windows.csv", "validation.json", "summary.json"}
    if required - paths.keys():
        raise ValueError("Prepared essential artifacts missing")
    prep_validation = json.loads(paths["validation.json"].read_text(encoding="utf-8-sig"))
    if prep_validation.get("status") != "passed" or prep_validation.get("is_acquisition_probe") or prep_validation.get("models_fit"):
        raise ValueError("Preparation is failed, only a probe, or already fitted")
    daily = pd.read_csv(paths["daily_instances.csv"])
    all_days = pd.read_csv(paths["all_daily_windows.csv"])
    validation = validate_frames(daily, all_days)
    prepared_summary = json.loads(paths["summary.json"].read_text(encoding="utf-8-sig"))
    return daily, all_days, contract, {"input_hashes_before": before, "prepared_manifest": manifest, "prepared_summary": prepared_summary, "validation": validation}


def validate_frames(daily: pd.DataFrame, all_days: pd.DataFrame) -> dict:
    required = {"issue_utc", "status", "y_class", "label_c1", "label_m1", *FEATURES,
                "past_168h_coverage_fraction", "future_coverage_fraction", "past_168h_max_gap_minutes", "future_max_gap_minutes", "feature_max_observed_utc", "hmi_latest_support_end_utc", "history_latest_event_end_utc", "independent_XRS_coverage_qualified"}
    if required - set(daily.columns):
        raise ValueError(f"Prepared daily columns missing: {sorted(required - set(daily.columns))}")
    if {"issue_utc", "status", "exclusion_reasons"} - set(all_days.columns):
        raise ValueError("All-day calendar interface incomplete")
    for frame in (daily, all_days):
        if frame["issue_utc"].duplicated().any():
            raise ValueError("Duplicate daily issue keys")
        classify_dates(frame["issue_utc"])
    expected_calendar = pd.date_range("2011-01-01", "2016-12-31", freq="D", tz="UTC")
    actual_calendar = pd.DatetimeIndex(pd.to_datetime(all_days["issue_utc"], utc=True))
    if len(actual_calendar) != len(expected_calendar) or not actual_calendar.sort_values().equals(expected_calendar):
        raise ValueError("All-day calendar must retain all 2192 dates, including gaps")
    if not daily["status"].eq("eligible").all():
        raise ValueError("Ineligible input row cannot be imputed into training")
    if not all_days["status"].isin(("eligible", "excluded")).all():
        raise ValueError("Unknown all-day qualification status")
    if not daily["independent_XRS_coverage_qualified"].eq(True).all():
        raise ValueError("Independent XRS qualification is not explicitly true")
    if set(daily["issue_utc"]) != set(all_days.loc[all_days["status"].eq("eligible"), "issue_utc"]):
        raise ValueError("Eligible/all-day rows disagree")
    projection = all_days.set_index("issue_utc").loc[daily["issue_utc"], ["status", "y_class", "label_c1", "label_m1", *FEATURES]].reset_index(drop=True)
    expected = daily[["status", "y_class", "label_c1", "label_m1", *FEATURES]].reset_index(drop=True)
    for col in expected.columns:
        if col == "status":
            equal = np.array_equal(projection[col].to_numpy(), expected[col].to_numpy())
        else:
            equal = np.array_equal(pd.to_numeric(projection[col]).to_numpy(), pd.to_numeric(expected[col]).to_numpy(), equal_nan=True)
        if not equal:
            raise ValueError(f"Eligible/all-day scientific projection differs: {col}")
    x = daily[FEATURES].to_numpy(dtype=float)
    if not np.isfinite(x).all():
        raise ValueError("Nonfinite scientific input; no zero padding/imputation permitted")
    if not (daily["patch_count"] >= 1).all() or not (daily["patch_count"] % 1 == 0).all():
        raise ValueError("Invalid patch count")
    for h in (24, 72, 168):
        c, m, z = [daily[f"history_{h}h_{s}_count"].to_numpy(float) for s in ("c", "m", "x")]
        if (z < 0).any() or (m < z).any() or (c < m).any() or any((a % 1 != 0).any() for a in (c, m, z)):
            raise ValueError("Historical threshold counts must be integer and nested C>=M>=X")
    y = daily["y_class"].to_numpy(float)
    if not np.isin(y, (0, 1, 2)).all() or not np.array_equal(daily["label_c1"].to_numpy(), (y >= 1).astype(int)) or not np.array_equal(daily["label_m1"].to_numpy(), (y == 2).astype(int)):
        raise ValueError("Nested labels/class encoding disagree")
    for col in ("past_168h_coverage_fraction", "future_coverage_fraction"):
        v = pd.to_numeric(daily[col]).to_numpy(float)
        if not np.isfinite(v).all() or ((v < .99) | (v > 1)).any():
            raise ValueError("Unknown/insufficient XRS coverage cannot become a class-zero row")
    for col in ("past_168h_max_gap_minutes", "future_max_gap_minutes"):
        v = pd.to_numeric(daily[col]).to_numpy(float)
        if not np.isfinite(v).all() or ((v < 0) | (v > 5) | (v % 1 != 0)).any():
            raise ValueError("XRS maximum-gap qualification failed")
    issued = pd.to_datetime(daily["issue_utc"], utc=True)
    observed = pd.to_datetime(daily["feature_max_observed_utc"], utc=True, errors="raise")
    if observed.isna().any() or not (observed < issued).all():
        raise ValueError("Feature time is missing or reaches/follows issue")
    latest_history = pd.to_datetime(daily["history_latest_event_end_utc"], utc=True, errors="raise")
    has_history = daily[[f"history_{h}h_c_count" for h in (24, 72, 168)]].max(axis=1).gt(0)
    if latest_history[has_history].isna().any() or (latest_history.notna() & (latest_history > issued - pd.Timedelta(hours=3))).any() or (latest_history.notna() & (latest_history > observed)).any():
        raise ValueError("Historical completed event information violates conservative 3h availability")
    hmi_support = pd.to_datetime(daily["hmi_latest_support_end_utc"], utc=True, errors="raise")
    if hmi_support.isna().any() or not (hmi_support < issued).all():
        raise ValueError("HMI support tail is missing or reaches/follows issue")
    expected_max = pd.concat([hmi_support, latest_history], axis=1).max(axis=1)
    if not observed.eq(expected_max).all():
        raise ValueError("Maximum feature time must include HMI support tail and history event end")
    daily["split"] = classify_dates(daily["issue_utc"])
    counts = {k: int(daily["split"].eq(k).sum()) for k in SPLITS}
    if any(n == 0 for n in counts.values()):
        raise ValueError("One or more registered roles have no eligible data")
    train_classes = sorted(daily.loc[daily["split"].eq("train"), "y_class"].unique().tolist())
    if train_classes != [0, 1, 2]:
        raise ValueError("Three-class training requires all registered classes; do not fabricate missing labels")
    return {"status": "passed", "feature_count": len(FEATURES), "split_counts": counts, "all_daily_calendar_count": len(all_days), "metadata_excluded_from_model": True, "coverage_future_flags_excluded_from_model": True, "processing_DATE_is_retrospective": True, "feature_observation_max_strictly_before_issue": True}


def probability_matrix(model: object, x: np.ndarray) -> np.ndarray:
    p = np.asarray(model.predict_proba(x), dtype=float)
    classes = np.asarray(model.classes_)
    if set(classes.tolist()) != {0, 1, 2}:
        raise ValueError("Fitted model class interface is incomplete")
    result = p[:, [int(np.where(classes == i)[0][0]) for i in (0, 1, 2)]]
    validate_probabilities(result)
    return result


def validate_probabilities(p: np.ndarray) -> None:
    if p.ndim != 2 or p.shape[1] != 3 or not np.isfinite(p).all() or (p < 0).any() or (p > 1).any() or not np.allclose(p.sum(axis=1), 1, rtol=0, atol=1e-10):
        raise ValueError("Invalid three-class probabilities")


def target_probabilities(p: np.ndarray) -> dict[str, np.ndarray]:
    validate_probabilities(p)
    return {TARGETS[0]: p[:, 1] + p[:, 2], TARGETS[1]: p[:, 2]}


def binary_brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((np.asarray(y, dtype=float) - np.asarray(p, dtype=float)) ** 2))


def dual_brier(y_class: np.ndarray, probabilities: np.ndarray) -> float:
    p = target_probabilities(probabilities)
    return (binary_brier(y_class >= 1, p[TARGETS[0]]) + binary_brier(y_class == 2, p[TARGETS[1]])) / 2


def apply_temperature(p: np.ndarray, temperature: float) -> np.ndarray:
    validate_probabilities(p)
    if not .25 <= temperature <= 4:
        raise ValueError("Temperature outside registered bounds")
    if temperature == 1:
        return p.copy()
    result = softmax(np.log(np.maximum(p, EPSILON)) / temperature, axis=1)
    validate_probabilities(result)
    return result


def negative_log_likelihood(y: np.ndarray, p: np.ndarray) -> float:
    return float(-np.log(np.maximum(p[np.arange(len(y)), y.astype(int)], EPSILON)).mean())


def fit_temperature(y: np.ndarray, p: np.ndarray) -> dict:
    validate_probabilities(p)
    counts = np.bincount(y.astype(int), minlength=3).tolist()
    before = negative_log_likelihood(y, p)
    if min(counts) < 20:
        return {"temperature": 1.0, "status": "identity_limited", "class_counts": counts, "limitation": "At least one calibration class has fewer than 20 observations", "nll_before": before, "nll_after": before, "fit_role": "calibration_only", "log_floor_epsilon": EPSILON}
    objective = lambda t: negative_log_likelihood(y, apply_temperature(p, float(t)))
    fitted = minimize_scalar(objective, bounds=(.25, 4), method="bounded", options={"xatol": 1e-8})
    if not fitted.success or not np.isfinite(fitted.fun):
        raise ValueError("Bounded calibration optimization failed")
    # Identity is within the registered domain; never retain a numerically worse fit.
    choices = [(1.0, before), (.25, objective(.25)), (4.0, objective(4.0)), (float(fitted.x), float(fitted.fun))]
    temperature, after = min(choices, key=lambda v: v[1])
    return {"temperature": temperature, "status": "fitted_on_calibration_only", "class_counts": counts, "nll_before": before, "nll_after": after, "fit_role": "calibration_only", "log_floor_epsilon": EPSILON, "optimizer_success": True}


def binary_metrics(y: np.ndarray, p: np.ndarray, threshold: float, climatology: float) -> dict:
    y, p = np.asarray(y), np.asarray(p, dtype=float)
    if y.ndim != 1 or p.shape != y.shape or not len(y) or not np.isin(y, (0, 1)).all() or not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError("Invalid binary metric inputs")
    y = y.astype(int)
    if not 0 <= threshold <= 1 or not 0 <= climatology <= 1:
        raise ValueError("Invalid threshold/reference probability")
    pred = p >= threshold
    tp, fp = int((pred & (y == 1)).sum()), int((pred & (y == 0)).sum())
    tn, fn = int((~pred & (y == 0)).sum()), int((~pred & (y == 1)).sum())
    bs, reference = binary_brier(y, p), binary_brier(y, np.full(len(y), climatology))
    q, reasons = float(y.mean()), {}
    result = {"n": len(y), "positives": int(y.sum()), "negatives": int((y == 0).sum()), "positive_rate": q,
              "threshold": float(threshold), "tp": tp, "fp": fp, "tn": tn, "fn": fn,
              "MAE_probability": float(np.abs(p - y).mean()), "Accuracy": float((pred == y).mean()), "Brier": bs,
              "Brier_reference_train_climatology": reference}
    for name, num, den in (("precision", tp, tp + fp), ("recall", tp, tp + fn), ("false_alarm_ratio", fp, tp + fp), ("false_positive_rate", fp, fp + tn)):
        result[name] = float(num / den) if den else None
        if not den:
            reasons[name] = "zero_denominator"
    result["TSS"] = float(tp / (tp + fn) - fp / (fp + tn)) if tp + fn and fp + tn else None
    result["Brier_skill_vs_train_climatology"] = float(1 - bs / reference) if reference > 0 else None
    if reference == 0:
        reasons["Brier_skill_vs_train_climatology"] = "zero_reference_brier"
    if 0 < q < 1:
        result["R2_binary_probability_auxiliary"] = float(1 - bs / (q * (1 - q)))
        result["ROC_AUC"] = float(roc_auc_score(y, p))
        result["average_precision"] = float(average_precision_score(y, p))
    else:
        for key in ("R2_binary_probability_auxiliary", "ROC_AUC", "average_precision", "TSS"):
            result[key] = None
            reasons[key] = "single_class_outcomes"
    result["undefined_reasons_json"] = json.dumps(reasons, sort_keys=True)
    return result


def choose_threshold(y: np.ndarray, p: np.ndarray) -> tuple[float, str]:
    if len(np.unique(y)) < 2:
        return .5, "identity_0.5_selection_single_class"
    choices = [(float(t), binary_metrics(y, p, float(t), float(np.mean(y)))["TSS"]) for t in THRESHOLD_GRID]
    selected = max(choices, key=lambda v: (v[1], v[0]))
    return selected[0], "selection_max_TSS_ties_higher_threshold"


def candidate_models() -> list[tuple[str, object, dict]]:
    candidates = []
    for c in (.01, .1, 1, 10):
        candidates.append((f"logistic_C{c:g}", make_pipeline(StandardScaler(), LogisticRegression(C=c, max_iter=2000, random_state=SEED)), {"family": "logistic", "C": c, "scaler_fit_role": "train_only"}))
    for leaf in (5, 15, 30):
        candidates.append((f"random_forest_leaf{leaf}", RandomForestClassifier(n_estimators=200, min_samples_leaf=leaf, max_depth=8, n_jobs=4, random_state=SEED), {"family": "random_forest", "n_estimators": 200, "min_samples_leaf": leaf, "max_depth": 8}))
    for leaves in (7, 15):
        candidates.append((f"hist_gradient_leaves{leaves}", HistGradientBoostingClassifier(learning_rate=.05, max_iter=200, max_leaf_nodes=leaves, l2_regularization=1, random_state=SEED), {"family": "hist_gradient_boosting", "max_leaf_nodes": leaves, "max_iter": 200, "learning_rate": .05, "l2_regularization": 1}))
    return candidates


def fit_select(x_train: np.ndarray, y_train: np.ndarray, x_selection: np.ndarray, y_selection: np.ndarray, candidates: list | None = None) -> tuple[str, object, list]:
    scores, best = [], None
    for order, (name, model, parameters) in enumerate(candidate_models() if candidates is None else candidates):
        start = time.monotonic()
        model.fit(x_train, y_train)
        score = dual_brier(y_selection, probability_matrix(model, x_selection))
        row = {"order": order, "model_id": name, "selection_mean_binary_Brier": score, "fit_rows": len(y_train), "selection_rows": len(y_selection), "fit_wall_seconds": time.monotonic() - start, "parameters_json": json.dumps(parameters, sort_keys=True), "fit_role": "train_only", "selection_role": "selection_only"}
        scores.append(row)
        if best is None or score < best[0]:
            best = (score, name, model)
    if best is None:
        raise ValueError("No candidate models")
    for row in scores:
        row["selected"] = row["model_id"] == best[1]
    return best[1], best[2], scores


def permutation_scores(model: object, x: np.ndarray, y: np.ndarray, temperature: float, repeats: int = 10, seed: int = SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    base = target_probabilities(apply_temperature(probability_matrix(model, x), temperature))
    baseline = [binary_brier(y >= 1, base[TARGETS[0]]), binary_brier(y == 2, base[TARGETS[1]])]
    records = []
    for j, feature in enumerate(FEATURES):
        differences = []
        for _ in range(repeats):
            corrupted = x.copy()
            corrupted[:, j] = corrupted[rng.permutation(len(x)), j]
            p = target_probabilities(apply_temperature(probability_matrix(model, corrupted), temperature))
            differences.append([binary_brier(y >= 1, p[TARGETS[0]]) - baseline[0], binary_brier(y == 2, p[TARGETS[1]]) - baseline[1]])
        a = np.asarray(differences)
        records.append({"feature": feature, "C_Brier_increase_mean": float(a[:, 0].mean()), "C_Brier_increase_std": float(a[:, 0].std(ddof=0)), "M_Brier_increase_mean": float(a[:, 1].mean()), "M_Brier_increase_std": float(a[:, 1].std(ddof=0)), "mean_Brier_increase": float(a.mean(axis=1).mean()), "mean_Brier_increase_std": float(a.mean(axis=1).std(ddof=0)), "repeats": repeats, "role": "selection_reused_exploratory", "repeat_values_json": json.dumps(a.tolist())})
    return pd.DataFrame(records)


def reliability_bins(y: np.ndarray, p: np.ndarray, model_id: str, target: str) -> list[dict]:
    indices = np.minimum((p * 5).astype(int), 4)
    rows = []
    for b in range(5):
        mask = indices == b
        rows.append({"model_id": model_id, "target": target, "bin": b, "left": b / 5, "right": (b + 1) / 5, "right_inclusive": b == 4, "n": int(mask.sum()), "mean_predicted_probability": float(p[mask].mean()) if mask.any() else None, "observed_fraction": float(y[mask].mean()) if mask.any() else None})
    return rows


def block_bootstrap(issues: pd.Series, outcomes: dict, predictions: dict, references: dict, draws: int = 200, seed: int = SEED) -> pd.DataFrame:
    months = pd.to_datetime(issues, utc=True).dt.strftime("%Y-%m").to_numpy()
    groups = [np.where(months == m)[0] for m in sorted(set(months))]
    rng, values = np.random.default_rng(seed), {}
    keys = ("MAE_probability", "Accuracy", "R2_binary_probability_auxiliary", "Brier", "Brier_skill_vs_train_climatology", "ROC_AUC", "average_precision", "TSS", "precision", "recall", "false_alarm_ratio")
    for _ in range(draws):
        idx = np.concatenate([groups[j] for j in rng.integers(0, len(groups), len(groups))])
        metrics_this_draw = {}
        for model_id, model_p in predictions.items():
            for target in TARGETS:
                metrics = binary_metrics(outcomes[target][idx], model_p[target][idx], .5, references[target])
                metrics_this_draw[(model_id, target)] = metrics
                for key in keys:
                    value = metrics[key]
                    if value is not None:
                        values.setdefault((model_id, target, key), []).append(value)
        for baseline in predictions:
            if baseline == "selected_calibrated":
                continue
            for target in TARGETS:
                for metric in ("Brier", "ROC_AUC", "average_precision", "TSS"):
                    a = metrics_this_draw[("selected_calibrated", target)][metric]
                    b = metrics_this_draw[(baseline, target)][metric]
                    if a is not None and b is not None:
                        values.setdefault((f"selected_minus_{baseline}", target, metric), []).append(a - b)
    rows = []
    for model_id in [*predictions, *[f"selected_minus_{k}" for k in predictions if k != "selected_calibrated"]]:
        compare = model_id.startswith("selected_minus_")
        for target in TARGETS:
            for metric in (("Brier", "ROC_AUC", "average_precision", "TSS") if compare else keys):
                samples = values.get((model_id, target, metric), [])
                lo, hi = np.percentile(samples, (2.5, 97.5)).tolist() if samples else (None, None)
                rows.append({"model_id": model_id, "target": target, "metric": metric, "ci_lower": lo, "ci_upper": hi, "bootstrap_valid_draws": len(samples), "bootstrap_total_draws": draws, "blocks": len(groups), "block_unit": "eligible_test_month", "comparison_sign": "negative Brier difference favors selected; positive ROC/AP/TSS difference favors selected" if compare else "", "method": "paired_month_block_percentile_95", "interpretation": "sampling interval under monthly-block assumption; not physical-hypothesis confidence"})
    return pd.DataFrame(rows)


def create_figures(output: Path, full_test: pd.DataFrame, importance: pd.DataFrame, bins: list, metrics: pd.DataFrame) -> list[dict]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    font = Path("C:/Windows/Fonts/msyh.ttc")
    if font.is_file():
        font_manager.fontManager.addfont(str(font))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(font)).get_name()
    plt.rcParams["axes.unicode_minus"] = False
    figure_dir = output / "figures"
    figure_dir.mkdir()
    products = []

    def save(fig, name, question, limitation):
        for extension in ("png", "svg"):
            path = figure_dir / f"{name}.{extension}"
            fig.savefig(path, dpi=220, bbox_inches="tight", metadata={"Description": limitation} if extension == "svg" else {"Description": limitation})
            products.append({"path": path.relative_to(output).as_posix(), "sha256": digest(path), "bytes": path.stat().st_size, "question": question, "limitation": limitation})
        plt.close(fig)

    months = sorted(pd.to_datetime(full_test["issue_utc"], utc=True).dt.strftime("%Y-%m").unique())
    fig, axes = plt.subplots(2, 1, figsize=(14, 13), constrained_layout=True)
    for ax, target, field, label in zip(axes, TARGETS, ("p_C_selected_calibrated", "p_M_selected_calibrated"), ("label_c1", "label_m1")):
        matrix = np.full((len(months), 31), np.nan)
        event_marks = []
        for row in full_test.itertuples(index=False):
            stamp = pd.Timestamp(row.issue_utc)
            month, day = months.index(stamp.strftime("%Y-%m")), stamp.day - 1
            value = getattr(row, field)
            if pd.notna(value):
                matrix[month, day] = value
                if getattr(row, label) == 1:
                    event_marks.append((day, month))
        cmap = plt.get_cmap("YlOrRd").with_extremes(bad="#d7dce0")
        im = ax.imshow(np.ma.masked_invalid(matrix), vmin=0, vmax=1, cmap=cmap, aspect="auto")
        if event_marks:
            xy = np.asarray(event_marks)
            ax.scatter(xy[:, 0], xy[:, 1], s=15, marker="o", facecolors="black", edgecolors="white", linewidths=.45)
        ax.set_xticks(np.arange(31), labels=np.arange(1, 32))
        ax.set_yticks(np.arange(len(months)), labels=months)
        ax.set_title(f"{target}：每日00:00 UTC发出、下一24小时新耀斑概率（独立测试时段）")
        ax.set_xlabel("UTC日期（日）；黑点为后来观测到的合格新事件，未输入预测模型")
        fig.colorbar(im, ax=ax, label="模型概率（0—1）", shrink=.8)
    fig.suptitle("全日面太阳历史预测回测：精修SHARP输入，非实时运行验证", fontsize=15)
    save(fig, "01_heldout_probability_heatmap", "在未用于训练/选择/校准的测试日期，模型给出了什么概率？", "灰色为无合格预测/不在测试范围/不存在日期；目录阴性不保证绝对无耀斑；精修产品未来处理，不能作为实时业务验证。")

    table = importance.sort_values("mean_Brier_increase", kind="stable")
    fig, ax = plt.subplots(figsize=(13, 14), constrained_layout=True)
    positions = np.arange(len(table))
    ax.barh(positions, table["mean_Brier_increase"], xerr=table["mean_Brier_increase_std"], color="#227c9d", error_kw={"ecolor": "#30343b", "capsize": 2})
    ax.axvline(0, color="black", linewidth=.8)
    ax.set_yticks(positions, labels=table["feature"], fontsize=9)
    ax.set_xlabel("打乱该特征后，两阈值平均Brier增加（越大，当前模型越依赖）\n误差线为10次打乱的标准差；选择集也用于选模型，排序可能乐观。\n相关特征共享信息；这是模型依赖排序，不能解释为物理因果贡献。", fontsize=10)
    ax.set_title("特征重要性：选择时段的10次置换；探索性模型依赖排序")
    save(fig, "02_permutation_importance", "这个已训练模型依赖哪些输入信息？", "选择集重复使用，探索性；误差线为置换标准差而非统计置信区间；不证明磁场机制或因果贡献。")

    fig, axes = plt.subplots(2, 2, figsize=(14, 11), constrained_layout=True)
    colors = {"selected_calibrated": "#d1495b", "train_climatology": "#777777", "past24_persistence": "#e4ad38", "history_only": "#00798c"}
    for j, target in enumerate(TARGETS):
        ax = axes[0, j]
        ax.plot((0, 1), (0, 1), "--", color="black", linewidth=1, label="理想校准")
        for model_id, color in colors.items():
            valid = [r for r in bins if r["target"] == target and r["model_id"] == model_id and r["n"]]
            ax.plot([r["mean_predicted_probability"] for r in valid], [r["observed_fraction"] for r in valid], marker="o", color=color, label=model_id)
        ax.set(xlim=(0, 1), ylim=(0, 1), xlabel="平均预测概率（固定5箱）", ylabel="实际新事件比例", title=f"{target}：独立测试校准图")
        ax.legend(fontsize=8)
        bar = axes[1, j]
        subset = metrics[(metrics["target"] == target) & (metrics["threshold_kind"] == "main_0.5")]
        ids = list(colors)
        briers = [float(subset.loc[subset["model_id"] == k, "Brier"].iloc[0]) for k in ids]
        bar.bar(np.arange(4), briers, color=[colors[k] for k in ids])
        bar.set_xticks(np.arange(4), labels=["选定模型", "训练基率", "24h持续性", "仅历史模型"])
        bar.set(ylabel="Brier（越低越好）", title="同一测试日期、自然事件率下的基线比较")
        for x, value in enumerate(briers):
            bar.text(x, value, f"{value:.4f}", ha="center", va="bottom", fontsize=9)
    fig.suptitle("测试概率是否可信、是否优于简单基线？结果可为正、零或负", fontsize=15)
    save(fig, "03_reliability_baselines", "模型概率和真实事件率是否一致，是否优于固定基线？", "固定分箱可能稀疏；Brier同时反映区分与校准，不能单独视为校准证明；历史回测与实时预报不同。")
    return products


def run_experiment(input_dir: Path, contract_path: Path, output: Path) -> dict:
    started = time.monotonic()
    if output.exists() or output.is_relative_to(input_dir):
        raise ValueError("Refuse output overwrite or output inside inputs")
    daily, all_days, contract, audit = checked_inputs(input_dir, contract_path)
    output.mkdir(parents=True)
    roles = {name: daily.loc[daily["split"].eq(name)].copy().sort_values("issue_utc") for name in SPLITS}
    x = {name: role[FEATURES].to_numpy(float) for name, role in roles.items()}
    y = {name: role["y_class"].to_numpy(int) for name, role in roles.items()}
    with threadpool_limits(limits=4):
        selected_id, selected, scores = fit_select(x["train"], y["train"], x["selection"], y["selection"])
        pd.DataFrame(scores).to_csv(output / "model_selection.csv", index=False, encoding="utf-8")
        history_indices = [FEATURES.index(c) for c in HISTORY_FEATURES]
        history = make_pipeline(StandardScaler(), LogisticRegression(C=1, max_iter=2000, random_state=SEED))
        history.fit(x["train"][:, history_indices], y["train"])
        selected_cal = fit_temperature(y["calibration"], probability_matrix(selected, x["calibration"]))
        history_cal = fit_temperature(y["calibration"], probability_matrix(history, x["calibration"][:, history_indices]))
        temperatures = {"selected_calibrated": selected_cal, "history_only": history_cal}
        q_class = np.bincount(y["train"], minlength=3) / len(y["train"])
        references = {TARGETS[0]: float(q_class[1] + q_class[2]), TARGETS[1]: float(q_class[2])}
        probabilities = {}
        for role, frame in roles.items():
            n = len(frame)
            persistence_class = np.where(frame["history_24h_m_count"].to_numpy() > 0, 2, np.where(frame["history_24h_c_count"].to_numpy() > 0, 1, 0))
            probabilities[role] = {
                "selected_calibrated": target_probabilities(apply_temperature(probability_matrix(selected, x[role]), selected_cal["temperature"])),
                "train_climatology": target_probabilities(np.tile(q_class, (n, 1))),
                "past24_persistence": target_probabilities(np.eye(3)[persistence_class]),
                "history_only": target_probabilities(apply_temperature(probability_matrix(history, x[role][:, history_indices]), history_cal["temperature"])),
            }
        thresholds = {}
        for model_id, model_p in probabilities["selection"].items():
            thresholds[model_id] = {}
            for target in TARGETS:
                label = y["selection"] >= 1 if target == TARGETS[0] else y["selection"] == 2
                value, rule = choose_threshold(label, model_p[target])
                thresholds[model_id][target] = {"value": value, "rule": rule, "role": "selection_only", "probability_model": "temperature fitted on separate calibration role, then applied to selection; no test tuning"}
        metric_rows, bins = [], []
        test_outcomes = {TARGETS[0]: y["test"] >= 1, TARGETS[1]: y["test"] == 2}
        for model_id, model_p in probabilities["test"].items():
            for target in TARGETS:
                for kind, threshold in (("main_0.5", .5), ("secondary_selection_TSS", thresholds[model_id][target]["value"])):
                    metric_rows.append({"model_id": model_id, "target": target, "role": "test_only", "threshold_kind": kind, **binary_metrics(test_outcomes[target], model_p[target], threshold, references[target])})
                bins += reliability_bins(test_outcomes[target].astype(int), model_p[target], model_id, target)
        metrics = pd.DataFrame(metric_rows)
        metrics.to_csv(output / "test_metrics.csv", index=False, encoding="utf-8")
        ci = block_bootstrap(roles["test"]["issue_utc"], test_outcomes, probabilities["test"], references)
        ci.to_csv(output / "bootstrap_ci.csv", index=False, encoding="utf-8")
        importance = permutation_scores(selected, x["selection"], y["selection"], selected_cal["temperature"])
        importance.to_csv(output / "permutation_importance.csv", index=False, encoding="utf-8")
        prediction_frames = []
        for role, frame in roles.items():
            issued = frame[["issue_utc", "y_class", "label_c1", "label_m1"]].copy()
            issued["role"] = role
            for model_id, model_p in probabilities[role].items():
                issued[f"p_C_{model_id}"] = model_p[TARGETS[0]]
                issued[f"p_M_{model_id}"] = model_p[TARGETS[1]]
            prediction_frames.append(issued)
        all_predictions = pd.concat(prediction_frames, ignore_index=True).sort_values("issue_utc")
        all_predictions.to_csv(output / "all_issued_predictions.csv", index=False, encoding="utf-8")
        test_predictions = all_predictions.loc[all_predictions["role"].eq("test")]
        test_predictions.to_csv(output / "heldout_predictions.csv", index=False, encoding="utf-8")
        all_test = all_days.loc[classify_dates(all_days["issue_utc"]).eq("test"), ["issue_utc", "status", "exclusion_reasons"]].merge(test_predictions.drop(columns="role"), on="issue_utc", how="left", validate="one_to_one")
        all_test.to_csv(output / "all_test_calendar.csv", index=False, encoding="utf-8")
        calibration = {"schema_version": "forecast-calibration-v1", "selected_model_id": selected_id, "models": temperatures, "thresholds": thresholds, "reliability_bins": bins, "train_climatology_class_probabilities": q_class.tolist(), "calibration_fit_role": "calibration_only", "threshold_selection_role": "selection_only", "test_used_to_fit_anything": False}
        write_json(output / "calibration.json", calibration)
        joblib.dump({"selected_model_id": selected_id, "selected_model": selected, "history_only_model": history, "selected_temperature": selected_cal["temperature"], "history_temperature": history_cal["temperature"], "feature_columns": FEATURES, "history_columns": HISTORY_FEATURES, "class_encoding": [0, 1, 2], "train_climatology": q_class, "thresholds": thresholds, "contract_sha256": digest(contract_path), "forecast_mode": "research_backtest"}, output / "models.joblib")
        figures = create_figures(output, all_test, importance, bins, metrics)
        write_json(output / "figure_index.json", {"schema_version": "forecast-figure-index-v1", "selected_model_id": selected_id, "data_origin": "heldout predictions and selection-period permutation results from this deterministic invocation", "caller_origin_requires_external_receipt": True, "products": figures})
        pools = threadpool_info()
        if any(p.get("num_threads", 1) > 4 for p in pools):
            raise ValueError("Native numerical thread limit exceeded")
    after = {name: digest(safe_child(input_dir, name)) for name in audit["input_hashes_before"]}
    if after != audit["input_hashes_before"]:
        raise ValueError("Read-only prepared inputs changed")
    validation = {**audit["validation"], "scope": "artifact/schema/registered_splits/nested_probability/formula/runtime_checks", "scientific_acceptance": "not_issued_by_this_tool", "caller_origin": "requires_actual_JW_execute_receipt", "input_hashes_before": audit["input_hashes_before"], "input_hashes_after": after, "input_hashes_unchanged": True, "contract_sha256": digest(contract_path), "prepared_manifest_sha256": digest(input_dir / "prepared_manifest.json"), "source_groups": audit["prepared_summary"].get("source_groups", []), "full_raw_sources_locator": "input prepared_manifest.json source_versions_terms; this tool verifies prepared file hashes, not every remote raw file", "original_source_manifest_sha256": audit["prepared_summary"].get("source_manifest_sha256"), "prepared_coverage_amendment_sha256": audit["prepared_manifest"].get("coverage_amendment_sha256"), "prepared_science_coverage_amendment_sha256": audit["prepared_manifest"]["science_coverage_amendment_sha256"], "base_v1_script_sha256": BASE_V1_SCRIPT_SHA256, "workers": 4, "native_thread_pools_within_limit": pools, "test_refit_or_tuning": False, "model_nesting_passed": True, "no_metadata_features": True, "no_missing_input_imputation": True}
    write_json(output / "validation.json", validation)
    summary = {"schema_version": "daily-full-disk-forecast-result-v1", "status": "completed", "forecast_mode": "research_backtest", "spatial_unit": "full_disk", "selected_model_id": selected_id, "selected_by": "minimum selection mean binary Brier, ties first preregistered candidate", "selection_scores": scores, "feature_columns": FEATURES, "feature_count": 33, "class_encoding": {"0": "no catalogued C+ new start", "1": "C+ but no M+ new start", "2": "M+ new start, including X"}, "split_counts": audit["validation"]["split_counts"], "split_class_counts": {k: np.bincount(v, minlength=3).tolist() for k, v in y.items()}, "calibration": temperatures, "test_metrics": metric_rows, "test_calendar_dates": len(all_test), "test_eligible_dates": len(test_predictions), "test_excluded_dates": len(all_test) - len(test_predictions), "bootstrap_draws": 200, "bootstrap_block": "test_month", "seed": SEED, "wall_seconds": time.monotonic() - started, "environment": {"python": sys.version, "platform": platform.platform(), "numpy": np.__version__, "pandas": pd.__version__, "scipy": scipy.__version__, "sklearn": sklearn.__version__, "joblib": joblib.__version__}, "script_sha256": digest(Path(__file__)), "contract_sha256": digest(contract_path), "limitations": ["Definitive SHARP DATE/geometry can use later processing; observation-time retrospective backtest, never historical operational availability.", "Full-disk outcomes with quality-selected visible SHARP summaries; no active-region localization or unseen-AR generalization claim.", "GOES catalog-negative is not perfect physical absence; independent minute coverage qualifies windows but does not fix event catalog completeness.", "MAE probability is not a proper probability scoring rule; binary probability R2 is auxiliary Brier normalization, not physical variance explained.", "Selection-period permutation reuses model selection data: exploratory model dependence with correlated features, not causal or mechanism importance.", "Monthly-block confidence intervals assume this dependence scale and do not certify calibration or physical hypotheses.", "Results may have zero/negative skill; no performance-driven data changes or test-based model choice are allowed."], "JW_actual_execute_status": "pending_external_driver_receipt", "scientific_review_status": "not_declared_by_computation_tool"}
    summary["test_metrics"] = [r for r in metric_rows if r["threshold_kind"] == "main_0.5"]
    summary["selection_scores"] = [{k: r[k] for k in ("order", "model_id", "selection_mean_binary_Brier", "selected")} for r in scores]
    summary["test_metrics_scope"] = "Four frozen selected/baseline models, both targets, main 0.5; all secondary thresholds remain in test_metrics.csv. Unselected candidates are evaluated on selection only."
    summary["selected_test_metric_intervals"] = [{"target": r.target, "metric": r.metric, "lower": None if pd.isna(r.ci_lower) else float(r.ci_lower), "upper": None if pd.isna(r.ci_upper) else float(r.ci_upper), "valid_draws": int(r.bootstrap_valid_draws)} for r in ci.loc[ci["model_id"].eq("selected_calibrated")].itertuples(index=False)]
    summary["bootstrap_ci_locator"] = {"path": "bootstrap_ci.csv", "sha256": digest(output / "bootstrap_ci.csv"), "scope": "All 0.5 metrics and paired selected-minus-baseline Brier/ROC/AP/TSS intervals; 200 draws, monthly-block assumption"}
    summary["science_coverage_amendment_sha256"] = SCIENCE_COVERAGE_AMENDMENT_SHA256
    summary["base_v1_script_sha256"] = BASE_V1_SCRIPT_SHA256
    write_json(output / "summary.json", summary)
    if (output / "summary.json").stat().st_size > 20000 or (output / "validation.json").stat().st_size > 15000:
        raise ValueError("Compact independent review artifacts exceed registered review-size limits")
    products = [{"path": p.relative_to(output).as_posix(), "bytes": p.stat().st_size, "sha256": digest(p)} for p in sorted(output.rglob("*")) if p.is_file()]
    write_json(output / "proof.json", {"schema_version": "forecast-computation-proof-v1", "science_coverage_amendment_sha256": SCIENCE_COVERAGE_AMENDMENT_SHA256, "base_v1_script_sha256": BASE_V1_SCRIPT_SHA256, "contract_sha256": digest(contract_path), "prepared_manifest_sha256": digest(input_dir / "prepared_manifest.json"), "script_sha256": digest(Path(__file__)), "products": products, "JW_identity_not_self_asserted": True, "input_hashes_unchanged": True})
    return {"status": "completed", "selected_model_id": selected_id, "output_directory": str(output), "proof_sha256": digest(output / "proof.json"), "summary_sha256": digest(output / "summary.json"), "wall_seconds": summary["wall_seconds"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--bounded-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    input_dir, contract_path, output = Path(args.input_dir).resolve(), Path(args.contract).resolve(), Path(args.output_dir).resolve()
    if output.exists() or output.is_relative_to(input_dir) or output == contract_path.parent:
        raise ValueError("Fresh separate output directory required")
    if not args.bounded_worker:
        command = [sys.executable, str(Path(__file__).resolve()), "--input-dir", str(input_dir), "--contract", str(contract_path), "--output-dir", str(output), "--bounded-worker"]
        process = subprocess.Popen(command)
        try:
            return process.wait(timeout=900)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            output.mkdir(parents=True, exist_ok=True)
            write_json(output / "failure_timeout.json", {"status": "failed", "reason": "registered_900_second_hard_wall_limit", "worker_pid": process.pid, "partial_outputs_retained": True, "no_scientific_acceptance": True})
            return 2
    try:
        result = run_experiment(input_dir, contract_path, output)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
        return 0
    except Exception as exc:
        if not output.exists():
            output.mkdir(parents=True)
        write_json(output / "failure.json", {"status": "failed", "error_type": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc(), "partial_outputs_retained": True, "no_scientific_acceptance": True})
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}, ensure_ascii=False), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
