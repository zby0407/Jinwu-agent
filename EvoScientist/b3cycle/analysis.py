from __future__ import annotations

import json
import hashlib
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import numpy as np

from .data import (
    output_root,
    processed_root,
    raw_root,
    read_noaa_observed,
    read_noaa_predicted,
    read_silso_hemispheric,
    read_silso_extended_hemispheric,
    read_silso_monthly,
    read_wso_polar,
)


@dataclass
class CycleFeature:
    cycle: int
    start_year: float
    end_year: float
    peak_year: float
    length_years: float
    peak_ssn: float
    rise_years: float
    decay_years: float
    rise_rate: float
    integrated_activity: float
    previous_peak_ssn: float | None = None
    next_peak_ssn: float | None = None


def _valid_xy(rows: list[dict[str, Any]], x_key: str, y_key: str) -> tuple[np.ndarray, np.ndarray]:
    x: list[float] = []
    y: list[float] = []
    for row in rows:
        xv = float(row.get(x_key, float("nan")))
        yv = float(row.get(y_key, float("nan")))
        if math.isfinite(xv) and math.isfinite(yv) and xv >= 0 and yv >= 0:
            x.append(xv)
            y.append(yv)
    return np.array(x, dtype=float), np.array(y, dtype=float)


def pearson(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 3 or len(y) < 3:
        return float("nan")
    if np.std(x) == 0 or np.std(y) == 0:
        return float("nan")
    return round(float(np.corrcoef(x, y)[0, 1]), 4)


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 3:
        return float("nan")
    xr = np.argsort(np.argsort(x)).astype(float)
    yr = np.argsort(np.argsort(y)).astype(float)
    return pearson(xr, yr)


def _safe_round(value: float, digits: int = 4) -> float | None:
    return round(float(value), digits) if math.isfinite(float(value)) else None


def _linear_fit(x: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    if len(x) < 3 or len(y) < 3 or np.std(x) == 0:
        return {
            "n": int(min(len(x), len(y))),
            "slope": None,
            "intercept": None,
            "rmse": None,
            "mae": None,
            "r2": None,
            "pearson": None,
            "spearman": None,
        }
    slope, intercept = np.polyfit(x, y, 1)
    predicted = slope * x + intercept
    residual = y - predicted
    total = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - float(np.sum(residual**2)) / total if total > 0 else float("nan")
    return {
        "n": int(len(x)),
        "slope": _safe_round(float(slope), 6),
        "intercept": _safe_round(float(intercept), 4),
        "rmse": _safe_round(float(np.sqrt(np.mean(residual**2))), 4),
        "mae": _safe_round(float(np.mean(np.abs(residual))), 4),
        "r2": _safe_round(r2, 4),
        "pearson": _safe_round(pearson(x, y), 4),
        "spearman": _safe_round(spearman(x, y), 4),
    }


def _empirical_two_sided_p(observed: float, controls: np.ndarray) -> float | None:
    finite = controls[np.isfinite(controls)]
    if not math.isfinite(observed) or len(finite) == 0:
        return None
    return round(float((1 + np.sum(np.abs(finite) >= abs(observed))) / (len(finite) + 1)), 5)


def find_cycle_minima(smoothed: list[dict[str, Any]]) -> list[int]:
    values = np.array([row["ssn"] for row in smoothed], dtype=float)
    years = np.array([row["decimal_year"] for row in smoothed], dtype=float)
    candidates: list[int] = []
    half_window = 48
    for idx in range(half_window, len(values) - half_window):
        if values[idx] < 0:
            continue
        window = values[idx - half_window : idx + half_window + 1]
        window = window[window >= 0]
        if len(window) < half_window:
            continue
        if values[idx] <= float(np.min(window)):
            if not candidates or years[idx] - years[candidates[-1]] >= 7.5:
                candidates.append(idx)
            elif values[idx] < values[candidates[-1]]:
                candidates[-1] = idx
    return candidates


def build_cycle_features(smoothed: list[dict[str, Any]]) -> list[CycleFeature]:
    minima = find_cycle_minima(smoothed)
    features: list[CycleFeature] = []
    cycle_number = 1
    for left, right in zip(minima, minima[1:]):
        segment = smoothed[left : right + 1]
        valid = [row for row in segment if row["valid"]]
        if len(valid) < 24:
            continue
        peak = max(valid, key=lambda row: row["ssn"])
        start_year = float(valid[0]["decimal_year"])
        end_year = float(valid[-1]["decimal_year"])
        peak_year = float(peak["decimal_year"])
        peak_ssn = float(peak["ssn"])
        rise_years = max(peak_year - start_year, 1e-6)
        decay_years = max(end_year - peak_year, 1e-6)
        integrated_activity = float(sum(row["ssn"] for row in valid) / 12.0)
        features.append(
            CycleFeature(
                cycle=cycle_number,
                start_year=round(start_year, 3),
                end_year=round(end_year, 3),
                peak_year=round(peak_year, 3),
                length_years=round(end_year - start_year, 3),
                peak_ssn=round(peak_ssn, 3),
                rise_years=round(rise_years, 3),
                decay_years=round(decay_years, 3),
                rise_rate=round(peak_ssn / rise_years, 3),
                integrated_activity=round(integrated_activity, 3),
            )
        )
        cycle_number += 1
    for idx, feature in enumerate(features):
        if idx > 0:
            feature.previous_peak_ssn = features[idx - 1].peak_ssn
        if idx < len(features) - 1:
            feature.next_peak_ssn = features[idx + 1].peak_ssn
    return features


def cycle26_proxy_forecast(cycles: list[CycleFeature], predicted: list[dict[str, Any]]) -> dict[str, Any]:
    recent_complete = cycles[-4:]
    peaks = np.array([cycle.peak_ssn for cycle in cycles if cycle.cycle >= 12], dtype=float)
    predicted_values = np.array(
        [row["predicted_ssn"] for row in predicted if row["predicted_ssn"] >= 0],
        dtype=float,
    )
    swpc_peak = float(np.max(predicted_values)) if len(predicted_values) else float("nan")
    current_peak = cycles[-1].peak_ssn if cycles else float("nan")
    climatology_median = float(np.median(peaks)) if len(peaks) else float("nan")
    recent_median = float(np.median([cycle.peak_ssn for cycle in recent_complete])) if recent_complete else float("nan")
    if math.isfinite(swpc_peak) and swpc_peak < 0.75 * climatology_median:
        class_label = "weak-to-moderate"
    elif math.isfinite(swpc_peak) and swpc_peak > 1.15 * climatology_median:
        class_label = "strong"
    else:
        class_label = "moderate"
    return {
        "target": "Solar Cycle 26 proxy forecast",
        "claim_boundary": "NOAA prediction series currently extends to late Cycle 25 / minimum approach; this is not an official Cycle 26 amplitude forecast.",
        "current_observed_peak_ssn": round(float(current_peak), 3),
        "historical_median_peak_ssn_cycle12_plus": round(climatology_median, 3),
        "recent_four_cycle_median_peak_ssn": round(recent_median, 3),
        "swpc_prediction_window_peak_ssn": round(swpc_peak, 3) if math.isfinite(swpc_peak) else None,
        "strength_class": class_label,
        "interpretation": "Use this as a proxy-only prior; historical WSO polar-field evidence constrains the mechanism, but Cycle-26 amplitude still needs a mature minimum-time polar precursor.",
    }


def waldmeier_analysis(cycles: list[CycleFeature]) -> dict[str, Any]:
    rows = [cycle for cycle in cycles if cycle.cycle >= 8 and cycle.peak_ssn > 0 and cycle.rise_years > 0]
    peaks = np.array([cycle.peak_ssn for cycle in rows], dtype=float)
    rise = np.array([cycle.rise_years for cycle in rows], dtype=float)
    rate = np.array([cycle.rise_rate for cycle in rows], dtype=float)
    return {
        "sample_cycles": [cycle.cycle for cycle in rows],
        "n": len(rows),
        "spearman_peak_vs_rise_time": spearman(peaks, rise),
        "pearson_peak_vs_rise_time": pearson(peaks, rise),
        "spearman_peak_vs_rise_rate": spearman(peaks, rate),
        "interpretation": "Negative peak-vs-rise-time and positive peak-vs-rise-rate support a Waldmeier-like constraint, not a causal proof.",
    }


def f107_drift_analysis(noaa: list[dict[str, Any]]) -> dict[str, Any]:
    windows = [
        ("all_available", 1947.0, 2100.0),
        ("cycle23_24", 1996.0, 2019.9),
        ("cycle25_to_date", 2019.9, 2100.0),
    ]
    result = []
    for name, start, end in windows:
        rows = [
            row
            for row in noaa
            if start <= row["decimal_year"] <= end
            and row.get("ssn", -1) >= 0
            and row.get("f10_7", -1) >= 0
        ]
        x, y = _valid_xy(rows, "ssn", "f10_7")
        if len(x) >= 3:
            slope, intercept = np.polyfit(x, y, 1)
            residual = y - (slope * x + intercept)
            rmse = float(np.sqrt(np.mean(residual * residual)))
        else:
            slope, intercept, rmse = float("nan"), float("nan"), float("nan")
        result.append(
            {
                "window": name,
                "n_months": int(len(x)),
                "pearson_ssn_f107": pearson(x, y),
                "linear_slope": round(float(slope), 5) if math.isfinite(slope) else None,
                "linear_intercept": round(float(intercept), 3) if math.isfinite(intercept) else None,
                "rmse": round(rmse, 3) if math.isfinite(rmse) else None,
            }
        )
    drift_flag = False
    if len(result) >= 3 and result[1]["linear_slope"] and result[2]["linear_slope"]:
        drift_flag = abs(result[2]["linear_slope"] - result[1]["linear_slope"]) > 0.08
    return {
        "windows": result,
        "drift_flag": drift_flag,
        "interpretation": "Slope changes across windows indicate possible proxy-relation drift and should lower confidence for single-proxy hypotheses.",
    }


def hemispheric_asymmetry(hemispheric: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for row in hemispheric if row["north"] >= 0 and row["south"] >= 0 and row["total"] > 0]
    asym = np.array([(row["north"] - row["south"]) / max(row["north"] + row["south"], 1e-6) for row in rows])
    years = np.array([row["decimal_year"] for row in rows])
    strong = rows[int(np.argmax(np.abs(asym)))] if len(rows) else {}
    return {
        "n_months": len(rows),
        "mean_abs_asymmetry": round(float(np.mean(np.abs(asym))), 4) if len(rows) else None,
        "max_abs_asymmetry": round(float(np.max(np.abs(asym))), 4) if len(rows) else None,
        "max_abs_asymmetry_year": round(float(strong.get("decimal_year", float("nan"))), 3) if strong else None,
        "coverage": {
            "start_year": round(float(np.min(years)), 3) if len(years) else None,
            "end_year": round(float(np.max(years)), 3) if len(years) else None,
        },
        "interpretation": "Hemispheric asymmetry challenges purely axisymmetric explanations and supports stochastic/coupled-dynamo hypotheses.",
    }


def _nearest_row(rows: list[dict[str, Any]], year: float, max_distance: float = 0.35) -> dict[str, Any] | None:
    valid = [
        row
        for row in rows
        if math.isfinite(float(row.get("decimal_year", float("nan"))))
    ]
    if not valid:
        return None
    closest = min(valid, key=lambda row: abs(float(row["decimal_year"]) - year))
    if abs(float(closest["decimal_year"]) - year) > max_distance:
        return None
    return closest


def polar_precursor_analysis(cycles: list[CycleFeature], wso_rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid_wso = [
        row
        for row in wso_rows
        if math.isfinite(row["north_filtered"])
        and math.isfinite(row["south_filtered"])
        and math.isfinite(row["avg_filtered"])
    ]
    pairs: list[dict[str, Any]] = []
    for cycle in cycles:
        if cycle.next_peak_ssn is None:
            continue
        wso = _nearest_row(valid_wso, cycle.end_year)
        if not wso:
            continue
        signed_average = (wso["north_filtered"] - wso["south_filtered"]) / 2.0
        polar_strength = abs(signed_average)
        pairs.append(
            {
                "cycle": cycle.cycle,
                "minimum_year": cycle.end_year,
                "wso_year": round(float(wso["decimal_year"]), 3),
                "north_filtered": wso["north_filtered"],
                "south_filtered": wso["south_filtered"],
                "signed_average_filtered": round(float(signed_average), 3),
                "polar_strength_proxy": round(float(polar_strength), 3),
                "next_cycle_peak_ssn": cycle.next_peak_ssn,
            }
        )
    x = np.array([row["polar_strength_proxy"] for row in pairs], dtype=float)
    y = np.array([row["next_cycle_peak_ssn"] for row in pairs], dtype=float)
    latest = valid_wso[-1] if valid_wso else {}
    return {
        "source": "WSO Polar Field Observations - 1976-Present",
        "coverage": {
            "start_year": round(float(valid_wso[0]["decimal_year"]), 3) if valid_wso else None,
            "end_year": round(float(valid_wso[-1]["decimal_year"]), 3) if valid_wso else None,
            "n_valid_rows": len(valid_wso),
        },
        "cycle_minimum_pairs": pairs,
        "n_pairs": len(pairs),
        "pearson_polar_strength_vs_next_peak": pearson(x, y) if len(pairs) >= 3 else None,
        "spearman_polar_strength_vs_next_peak": spearman(x, y) if len(pairs) >= 3 else None,
        "latest_filtered": {
            "decimal_year": round(float(latest.get("decimal_year", float("nan"))), 3) if latest else None,
            "north_filtered": latest.get("north_filtered"),
            "south_filtered": latest.get("south_filtered"),
            "avg_filtered": latest.get("avg_filtered"),
            "signed_average_filtered": round(
                float((latest.get("north_filtered", 0.0) - latest.get("south_filtered", 0.0)) / 2.0),
                3,
            )
            if latest
            else None,
        },
        "interpretation": "WSO polar-field data convert H1 from a missing-data warning into a testable polar-precursor constraint, but only a few complete cycles are available.",
    }


def low_order_dynamo_toy_model(cycles: list[CycleFeature], polar: dict[str, Any]) -> dict[str, Any]:
    cycle_by_number = {cycle.cycle: cycle for cycle in cycles}
    rows: list[dict[str, Any]] = []
    for pair in polar.get("cycle_minimum_pairs", []):
        cycle = cycle_by_number.get(pair.get("cycle"))
        polar_strength = float(pair.get("polar_strength_proxy", float("nan")))
        next_peak = float(pair.get("next_cycle_peak_ssn", float("nan")))
        if not cycle or not math.isfinite(polar_strength) or not math.isfinite(next_peak):
            continue
        if polar_strength <= 0 or next_peak <= 0:
            continue
        rows.append(
            {
                "cycle": pair["cycle"],
                "minimum_year": pair["minimum_year"],
                "polar_strength_proxy": polar_strength,
                "current_cycle_peak_ssn": float(cycle.peak_ssn),
                "next_cycle_peak_ssn": next_peak,
            }
        )
    if len(rows) < 3:
        return {
            "model_name": "low_order_babcock_leighton_map",
            "status": "insufficient_data",
            "sample_size": len(rows),
            "claim_boundary": "The toy model requires at least three polar-field precursor pairs.",
        }

    p = np.array([row["polar_strength_proxy"] for row in rows], dtype=float)
    y = np.array([row["next_cycle_peak_ssn"] for row in rows], dtype=float)
    current_peak = np.array([row["current_cycle_peak_ssn"] for row in rows], dtype=float)
    p_ref = float(np.median(p))
    y_ref = float(np.median(y))
    p_norm = p / max(p_ref, 1e-6)
    y_norm = y / max(y_ref, 1e-6)
    gamma_grid = np.linspace(0.0, 2.5, 101)

    def fit_response(px: np.ndarray, yy: np.ndarray) -> tuple[float, float, float, np.ndarray]:
        best_gamma = 0.0
        best_gain = 0.0
        best_rmse = float("inf")
        best_pred = np.zeros_like(yy)
        for gamma in gamma_grid:
            basis = px / (1.0 + gamma * px * px)
            denom = float(np.dot(basis, basis))
            if denom <= 0:
                continue
            gain = float(np.dot(basis, yy) / denom)
            pred = gain * basis
            rmse = float(np.sqrt(np.mean((pred - yy) ** 2)))
            if rmse < best_rmse:
                best_gamma = float(gamma)
                best_gain = gain
                best_rmse = rmse
                best_pred = pred
        return best_gamma, best_gain, best_rmse, best_pred

    gamma, gain, rmse_norm, pred_norm = fit_response(p_norm, y_norm)
    pred = pred_norm * y_ref
    residual = pred - y
    mae = float(np.mean(np.abs(residual)))
    rmse = float(np.sqrt(np.mean(residual * residual)))
    mape = float(np.mean(np.abs(residual) / np.maximum(y, 1e-6)))
    median_baseline = np.full_like(y, y_ref)
    median_rmse = float(np.sqrt(np.mean((median_baseline - y) ** 2)))
    persistence_rmse = float(np.sqrt(np.mean((current_peak - y) ** 2)))

    loo_rows: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        mask = np.ones(len(rows), dtype=bool)
        mask[idx] = False
        loo_gamma, loo_gain, _, _ = fit_response(p_norm[mask], y_norm[mask])
        basis = p_norm[idx] / (1.0 + loo_gamma * p_norm[idx] * p_norm[idx])
        loo_pred = float(loo_gain * basis * y_ref)
        loo_rows.append(
            {
                "cycle": row["cycle"],
                "observed_next_peak_ssn": round(float(y[idx]), 3),
                "predicted_next_peak_ssn": round(loo_pred, 3),
                "abs_error_ssn": round(abs(loo_pred - float(y[idx])), 3),
                "fit_without_cycle_gamma": round(float(loo_gamma), 3),
            }
        )
    loo_errors = np.array([row["abs_error_ssn"] for row in loo_rows], dtype=float)

    predictions = []
    for idx, row in enumerate(rows):
        predictions.append(
            {
                "cycle": row["cycle"],
                "minimum_year": row["minimum_year"],
                "polar_strength_proxy": round(float(p[idx]), 3),
                "observed_next_peak_ssn": round(float(y[idx]), 3),
                "toy_model_peak_ssn": round(float(pred[idx]), 3),
                "residual_ssn": round(float(residual[idx]), 3),
            }
        )

    improvement = 1.0 - rmse / median_rmse if median_rmse > 0 else float("nan")
    return {
        "model_name": "low_order_babcock_leighton_map",
        "status": "executed",
        "equation": "T_{n+1}/T_ref = g * (P_n/P_ref) / (1 + gamma * (P_n/P_ref)^2)",
        "state_variables": {
            "P_n": "solar-minimum polar-field strength proxy from WSO filtered polar fields",
            "T_{n+1}": "next-cycle toroidal activity proxy, represented by smoothed sunspot-number peak",
            "gamma": "nonlinear quenching parameter that weakens response at large seed fields",
            "g": "poloidal-to-toroidal gain fitted by least squares for each gamma",
        },
        "assumptions": [
            "The map is a mechanism-facing surrogate, not a full magnetohydrodynamic dynamo simulation.",
            "WSO polar-field pairs are sparse, so the fitted parameters are diagnostic only.",
            "The response is constrained to pass through the origin after normalization because no polar seed should imply no next-cycle toroidal response in this toy closure.",
        ],
        "sample_size": len(rows),
        "normalization": {
            "polar_strength_reference": round(p_ref, 3),
            "next_peak_reference": round(y_ref, 3),
        },
        "fit": {
            "gain": round(float(gain), 4),
            "quenching_gamma": round(float(gamma), 4),
            "rmse_ssn": round(rmse, 3),
            "mae_ssn": round(mae, 3),
            "mape": round(mape, 4),
            "median_baseline_rmse_ssn": round(median_rmse, 3),
            "persistence_baseline_rmse_ssn": round(persistence_rmse, 3),
            "improvement_vs_median_baseline": round(float(improvement), 4) if math.isfinite(improvement) else None,
            "normalized_rmse": round(float(rmse_norm), 4),
        },
        "predictions_by_pair": predictions,
        "leave_one_out": {
            "mae_ssn": round(float(np.mean(loo_errors)), 3),
            "rmse_ssn": round(float(np.sqrt(np.mean(loo_errors * loo_errors))), 3),
            "rows": loo_rows,
        },
        "interpretation": "The toy map makes the Babcock-Leighton precursor chain explicit: polar seed field is converted into next-cycle toroidal activity with a fitted nonlinear quenching term. Its value is explanatory and comparative; four WSO pairs are not enough for a definitive forecast.",
        "claim_boundary": "Use this as a mechanism plausibility check and ablation baseline, not as an operational Cycle-26 prediction.",
    }


def waldmeier_leave_one_cycle_out(
    cycles: list[CycleFeature], seed: int = 0
) -> dict[str, Any]:
    """Test whether the Waldmeier sign survives omission of each cycle."""

    rows = [
        cycle
        for cycle in cycles
        if cycle.cycle >= 8 and cycle.peak_ssn > 0 and cycle.rise_years > 0
    ]
    peaks = np.array([row.peak_ssn for row in rows], dtype=float)
    rise = np.array([row.rise_years for row in rows], dtype=float)
    rate = np.array([row.rise_rate for row in rows], dtype=float)
    folds: list[dict[str, Any]] = []
    predictions: list[float] = []
    observed: list[float] = []
    for held_index, held in enumerate(rows):
        keep = np.ones(len(rows), dtype=bool)
        keep[held_index] = False
        train_fit = _linear_fit(rise[keep], peaks[keep])
        prediction = None
        if train_fit["slope"] is not None and train_fit["intercept"] is not None:
            prediction = float(train_fit["slope"]) * held.rise_years + float(
                train_fit["intercept"]
            )
            predictions.append(prediction)
            observed.append(held.peak_ssn)
        folds.append(
            {
                "held_out_cycle": held.cycle,
                "train_n": int(np.sum(keep)),
                "train_spearman_peak_vs_rise_time": _safe_round(
                    spearman(peaks[keep], rise[keep])
                ),
                "train_spearman_peak_vs_rise_rate": _safe_round(
                    spearman(peaks[keep], rate[keep])
                ),
                "held_out_peak_ssn": held.peak_ssn,
                "predicted_peak_from_rise_time": _safe_round(
                    prediction, 3
                )
                if prediction is not None
                else None,
                "absolute_error_ssn": _safe_round(
                    abs(prediction - held.peak_ssn), 3
                )
                if prediction is not None
                else None,
            }
        )

    loo_time = np.array(
        [
            fold["train_spearman_peak_vs_rise_time"]
            for fold in folds
            if fold["train_spearman_peak_vs_rise_time"] is not None
        ],
        dtype=float,
    )
    loo_rate = np.array(
        [
            fold["train_spearman_peak_vs_rise_rate"]
            for fold in folds
            if fold["train_spearman_peak_vs_rise_rate"] is not None
        ],
        dtype=float,
    )

    rng = np.random.default_rng(seed)
    bootstrap: list[float] = []
    if len(rows) >= 8:
        for _ in range(1000):
            indices = rng.integers(0, len(rows), len(rows))
            value = spearman(peaks[indices], rise[indices])
            if math.isfinite(value):
                bootstrap.append(value)
    bootstrap_array = np.array(bootstrap, dtype=float)
    prediction_errors = (
        np.array(predictions, dtype=float) - np.array(observed, dtype=float)
        if predictions
        else np.array([], dtype=float)
    )
    return {
        "status": "executed" if len(rows) >= 8 else "insufficient_data",
        "method": "leave-one-complete-cycle-out; no random row split",
        "seed": seed,
        "sample_cycles": [row.cycle for row in rows],
        "n_cycles": len(rows),
        "full_sample": {
            "spearman_peak_vs_rise_time": _safe_round(spearman(peaks, rise)),
            "pearson_peak_vs_rise_time": _safe_round(pearson(peaks, rise)),
            "spearman_peak_vs_rise_rate": _safe_round(spearman(peaks, rate)),
        },
        "leave_one_cycle_out": {
            "negative_time_effect_fraction": _safe_round(
                float(np.mean(loo_time < 0)) if len(loo_time) else float("nan")
            ),
            "positive_rate_effect_fraction": _safe_round(
                float(np.mean(loo_rate > 0)) if len(loo_rate) else float("nan")
            ),
            "time_effect_range": [
                _safe_round(float(np.min(loo_time))) if len(loo_time) else None,
                _safe_round(float(np.max(loo_time))) if len(loo_time) else None,
            ],
            "prediction_mae_ssn": _safe_round(
                float(np.mean(np.abs(prediction_errors))), 3
            )
            if len(prediction_errors)
            else None,
            "prediction_rmse_ssn": _safe_round(
                float(np.sqrt(np.mean(prediction_errors**2))), 3
            )
            if len(prediction_errors)
            else None,
            "folds": folds,
        },
        "bootstrap_95_interval_spearman_peak_vs_rise_time": [
            _safe_round(float(np.percentile(bootstrap_array, 2.5)))
            if len(bootstrap_array)
            else None,
            _safe_round(float(np.percentile(bootstrap_array, 97.5)))
            if len(bootstrap_array)
            else None,
        ],
        "interpretation": "符号在逐周留一后若保持稳定，可支持 Waldmeier 形态约束；它仍是现象学相关，不是发电机因果证明。",
        "claim_boundary": "Retrospective complete-cycle morphology only; Cycle 25 is not treated as complete.",
    }


def _cycle_phase_rows(
    noaa: list[dict[str, Any]], cycles: list[CycleFeature]
) -> list[dict[str, Any]]:
    descriptors = [
        {
            "cycle": row.cycle,
            "start": row.start_year,
            "peak": row.peak_year,
            "end": row.end_year,
            "complete": True,
        }
        for row in cycles
        if row.end_year >= 1947.0
    ]
    latest = [
        row
        for row in noaa
        if row["decimal_year"] >= 2019.95
        and row.get("ssn", -1) >= 0
        and row.get("f10_7", -1) >= 0
    ]
    if latest:
        peak_row = max(latest, key=lambda row: row.get("smoothed_ssn", -1))
        if peak_row.get("smoothed_ssn", -1) < 0:
            peak_row = max(latest, key=lambda row: row["ssn"])
        descriptors.append(
            {
                "cycle": 25,
                "start": 2019.958,
                "peak": float(peak_row["decimal_year"]),
                "end": float(latest[-1]["decimal_year"]),
                "complete": False,
            }
        )

    output: list[dict[str, Any]] = []
    for row in noaa:
        if row.get("ssn", -1) < 0 or row.get("f10_7", -1) < 0:
            continue
        year = float(row["decimal_year"])
        descriptor = next(
            (
                candidate
                for candidate in descriptors
                if candidate["start"] <= year <= candidate["end"]
            ),
            None,
        )
        if descriptor is None:
            continue
        if abs(year - float(descriptor["peak"])) <= 1.0:
            phase = "peak_band"
        elif year < float(descriptor["peak"]):
            phase = "rising"
        else:
            phase = "declining"
        output.append(
            {
                **row,
                "cycle": int(descriptor["cycle"]),
                "cycle_complete": bool(descriptor["complete"]),
                "phase": phase,
            }
        )
    return output


def f107_phase_stratified_drift(
    noaa: list[dict[str, Any]], cycles: list[CycleFeature], seed: int = 0
) -> dict[str, Any]:
    """Estimate proxy drift by cycle and activity phase with causal grouping."""

    rows = _cycle_phase_rows(noaa, cycles)
    strata: list[dict[str, Any]] = []
    for cycle in sorted({int(row["cycle"]) for row in rows}):
        for phase in ("rising", "peak_band", "declining"):
            group = [
                row
                for row in rows
                if row["cycle"] == cycle and row["phase"] == phase
            ]
            x = np.array([row["ssn"] for row in group], dtype=float)
            y = np.array([row["f10_7"] for row in group], dtype=float)
            if len(x) < 12:
                continue
            fit = _linear_fit(x, y)
            rng = np.random.default_rng(seed + cycle * 17 + len(phase))
            block_slopes: list[float] = []
            block = max(6, min(18, len(x) // 4))
            for _ in range(400):
                starts = rng.integers(0, max(1, len(x) - block + 1), 4)
                indices = np.concatenate(
                    [np.arange(start, min(start + block, len(x))) for start in starts]
                )
                if len(indices) >= 3 and np.std(x[indices]) > 0:
                    slope, _ = np.polyfit(x[indices], y[indices], 1)
                    if math.isfinite(float(slope)):
                        block_slopes.append(float(slope))
            slope_interval = [
                _safe_round(float(np.percentile(block_slopes, 2.5)), 6)
                if block_slopes
                else None,
                _safe_round(float(np.percentile(block_slopes, 97.5)), 6)
                if block_slopes
                else None,
            ]
            strata.append(
                {
                    "cycle": cycle,
                    "cycle_complete": bool(group[0]["cycle_complete"]),
                    "phase": phase,
                    "start_year": _safe_round(group[0]["decimal_year"], 3),
                    "end_year": _safe_round(group[-1]["decimal_year"], 3),
                    "fit": fit,
                    "block_bootstrap_95_interval_slope": slope_interval,
                }
            )
    slopes = np.array(
        [row["fit"]["slope"] for row in strata if row["fit"]["slope"] is not None],
        dtype=float,
    )
    return {
        "status": "executed" if len(strata) >= 3 else "insufficient_data",
        "method": "cycle-and-phase stratification with contiguous-block bootstrap",
        "seed": seed,
        "n_valid_months": len(rows),
        "strata": strata,
        "slope_range": [
            _safe_round(float(np.min(slopes)), 6) if len(slopes) else None,
            _safe_round(float(np.max(slopes)), 6) if len(slopes) else None,
        ],
        "max_absolute_slope_difference": _safe_round(
            float(np.max(slopes) - np.min(slopes)), 6
        )
        if len(slopes)
        else None,
        "cycle25_provisional": any(
            row["cycle"] == 25 and not row["cycle_complete"] for row in strata
        ),
        "interpretation": "跨周期或跨相位斜率变化会降低单一代理量外推置信度；连续块 bootstrap 保留部分时间相关性。",
        "claim_boundary": "The analysis diagnoses proxy calibration drift; it does not measure the internal solar magnetic field.",
    }


def _component_calibration(
    extended: list[dict[str, Any]],
    direct_by_month: dict[tuple[int, int], dict[str, Any]],
    north_field: str,
    south_field: str,
) -> dict[str, Any]:
    rows: list[tuple[float, float, float, float]] = []
    for row in extended:
        direct = direct_by_month.get((row["year"], row["month"]))
        if direct is None:
            continue
        north = float(row[north_field])
        south = float(row[south_field])
        if north < 0 or south < 0 or direct["north"] < 0 or direct["south"] < 0:
            continue
        rows.append((north, south, float(direct["north"]), float(direct["south"])))

    if len(rows) < 12:
        return {
            "n_overlap_months": len(rows),
            "north": _linear_fit(np.array([]), np.array([])),
            "south": _linear_fit(np.array([]), np.array([])),
            "error_aggregation": "pooled north/south component errors",
            "north_rmse": None,
            "south_rmse": None,
            "hemisphere_pooled_rmse": None,
            "hemisphere_pooled_mae": None,
            "combined_rmse": None,
            "combined_mae": None,
            "total_activity_rmse": None,
        }
    values = np.array(rows, dtype=float)
    north_residual = values[:, 0] - values[:, 2]
    south_residual = values[:, 1] - values[:, 3]
    pooled_residual = np.concatenate((north_residual, south_residual))
    total_activity_residual = north_residual + south_residual
    pooled_rmse = float(np.sqrt(np.mean(pooled_residual**2)))
    pooled_mae = float(np.mean(np.abs(pooled_residual)))
    return {
        "n_overlap_months": len(rows),
        "north": _linear_fit(values[:, 0], values[:, 2]),
        "south": _linear_fit(values[:, 1], values[:, 3]),
        "error_aggregation": "pooled north/south component errors",
        "north_rmse": _safe_round(
            float(np.sqrt(np.mean(north_residual**2))), 4
        ),
        "south_rmse": _safe_round(
            float(np.sqrt(np.mean(south_residual**2))), 4
        ),
        "hemisphere_pooled_rmse": _safe_round(pooled_rmse, 4),
        "hemisphere_pooled_mae": _safe_round(pooled_mae, 4),
        # Compatibility aliases now use pooled component errors, so opposing
        # north/south errors cannot cancel before squaring or taking abs().
        "combined_rmse": _safe_round(pooled_rmse, 4),
        "combined_mae": _safe_round(pooled_mae, 4),
        "combined_bias": _safe_round(float(np.mean(pooled_residual)), 4),
        "combined_residual_95_interval": [
            _safe_round(float(np.percentile(pooled_residual, 2.5)), 4),
            _safe_round(float(np.percentile(pooled_residual, 97.5)), 4),
        ],
        "total_activity_rmse": _safe_round(
            float(np.sqrt(np.mean(total_activity_residual**2))), 4
        ),
    }


def extended_hemispheric_calibration(
    extended: list[dict[str, Any]], direct: list[dict[str, Any]]
) -> dict[str, Any]:
    """Audit reconstructed and direct hemispheric layers over their overlap."""

    direct_by_month = {
        (int(row["year"]), int(row["month"])): row
        for row in direct
        if row["north"] >= 0 and row["south"] >= 0
    }
    component_pairs = (
        ("catalogue_composite", "north", "south"),
        ("area_reconstruction", "north_area", "south_area"),
        ("temmer2006_recalibration", "north_temmer2006", "south_temmer2006"),
        ("catalogue_embedded_silso", "north_silso", "south_silso"),
    )
    calibration = {
        name: _component_calibration(
            extended, direct_by_month, north_field, south_field
        )
        for name, north_field, south_field in component_pairs
    }
    reconstructed = [
        row
        for row in extended
        if row["year"] < 1992 and row["north"] >= 0 and row["south"] >= 0
    ]
    direct_layer = [
        row
        for row in extended
        if row["year"] >= 1992
        and row["north_silso"] >= 0
        and row["south_silso"] >= 0
    ]
    composite = calibration["catalogue_composite"]
    return {
        "status": (
            "executed"
            if composite["n_overlap_months"] >= 24 and reconstructed
            else "insufficient_data"
        ),
        "source": "SILSO/Veronig Catalogue B plus current SILSO monthly hemispheric series",
        "coverage": {
            "catalogue_start": extended[0]["date"] if extended else None,
            "catalogue_end": extended[-1]["date"] if extended else None,
            "reconstructed_pre_1992_months": len(reconstructed),
            "embedded_direct_1992_plus_months": len(direct_layer),
            "current_direct_months": len(direct_by_month),
        },
        "overlap_calibration": calibration,
        "evidence_layers": [
            {
                "layer": "reconstructed_pre_1992",
                "semantic_status": "reconstruction",
                "claim_weight": "lower_than_direct_observation",
            },
            {
                "layer": "direct_1992_plus",
                "semantic_status": "observation",
                "claim_weight": "primary_for_overlap_validation",
            },
        ],
        "interpretation": "扩展序列增加了跨周期覆盖，但重建层必须通过重叠期残差与缩放误差进入不确定性，而不能与直接观测混为一层。",
        "claim_boundary": "Pre-1992 hemispheric values are reconstructed evidence, not direct SILSO observations.",
    }


def polar_precursor_robustness(
    polar: dict[str, Any], seed: int = 0
) -> dict[str, Any]:
    pairs = [
        row
        for row in polar.get("cycle_minimum_pairs", [])
        if row.get("polar_strength_proxy") is not None
        and row.get("next_cycle_peak_ssn") is not None
    ]
    x = np.array([row["polar_strength_proxy"] for row in pairs], dtype=float)
    y = np.array([row["next_cycle_peak_ssn"] for row in pairs], dtype=float)
    loo: list[dict[str, Any]] = []
    for index, row in enumerate(pairs):
        keep = np.ones(len(pairs), dtype=bool)
        keep[index] = False
        loo.append(
            {
                "held_out_cycle": row["cycle"],
                "n_train": int(np.sum(keep)),
                "pearson": _safe_round(pearson(x[keep], y[keep])),
                "spearman": _safe_round(spearman(x[keep], y[keep])),
            }
        )
    loo_spearman = np.array(
        [row["spearman"] for row in loo if row["spearman"] is not None], dtype=float
    )
    rng = np.random.default_rng(seed)
    bootstrap: list[float] = []
    if len(pairs) >= 3:
        for _ in range(1000):
            indices = rng.integers(0, len(pairs), len(pairs))
            value = spearman(x[indices], y[indices])
            if math.isfinite(value):
                bootstrap.append(value)
    return {
        "status": "executed" if len(pairs) >= 3 else "insufficient_data",
        "seed": seed,
        "n_complete_pairs": len(pairs),
        "full_sample": {
            "pearson": _safe_round(pearson(x, y)),
            "spearman": _safe_round(spearman(x, y)),
        },
        "leave_one_cycle_out": {
            "folds": loo,
            "positive_spearman_fraction": _safe_round(
                float(np.mean(loo_spearman > 0))
                if len(loo_spearman)
                else float("nan")
            ),
            "spearman_range": [
                _safe_round(float(np.min(loo_spearman)))
                if len(loo_spearman)
                else None,
                _safe_round(float(np.max(loo_spearman)))
                if len(loo_spearman)
                else None,
            ],
        },
        "bootstrap_95_interval_spearman": [
            _safe_round(float(np.percentile(bootstrap, 2.5)))
            if bootstrap
            else None,
            _safe_round(float(np.percentile(bootstrap, 97.5)))
            if bootstrap
            else None,
        ],
        "small_sample_warning": len(pairs) < 8,
        "interpretation": "留一结果若对单个周期间极敏感，应降低极区前兆的置信度；当前完整配对数量只足以形成机制约束。",
        "claim_boundary": "Sparse retrospective precursor evidence; no operational Cycle-26 amplitude forecast.",
    }


def _loo_family_predictions(
    rows: list[dict[str, Any]], family: str
) -> list[dict[str, Any]]:
    predictions: list[dict[str, Any]] = []
    for held_index, held in enumerate(rows):
        train = [row for index, row in enumerate(rows) if index != held_index]
        target = float(held["next_cycle_peak_ssn"])
        if family == "median_baseline":
            prediction = float(np.median([row["next_cycle_peak_ssn"] for row in train]))
        elif family == "persistence_baseline":
            prediction = float(held["current_cycle_peak_ssn"])
        elif family == "linear_polar":
            x = np.array([row["polar_strength_proxy"] for row in train], dtype=float)
            y = np.array([row["next_cycle_peak_ssn"] for row in train], dtype=float)
            if len(x) < 2 or np.std(x) == 0:
                prediction = float(np.median(y))
            else:
                slope, intercept = np.polyfit(x, y, 1)
                prediction = float(slope * held["polar_strength_proxy"] + intercept)
        elif family == "nonlinear_quenching":
            p = np.array([row["polar_strength_proxy"] for row in train], dtype=float)
            y = np.array([row["next_cycle_peak_ssn"] for row in train], dtype=float)
            p_ref = max(float(np.median(p)), 1e-6)
            y_ref = max(float(np.median(y)), 1e-6)
            px = p / p_ref
            yy = y / y_ref
            best = (float("inf"), 0.0, 0.0)
            for gamma in np.linspace(0.0, 2.5, 101):
                basis = px / (1.0 + gamma * px * px)
                denominator = float(np.dot(basis, basis))
                if denominator <= 0:
                    continue
                gain = float(np.dot(basis, yy) / denominator)
                rmse = float(np.sqrt(np.mean((gain * basis - yy) ** 2)))
                if rmse < best[0]:
                    best = (rmse, float(gamma), gain)
            _, gamma, gain = best
            held_p = float(held["polar_strength_proxy"]) / p_ref
            prediction = float(gain * held_p / (1.0 + gamma * held_p**2) * y_ref)
        else:
            raise ValueError(f"unknown model family: {family}")
        predictions.append(
            {
                "held_out_cycle": int(held["cycle"]),
                "observed_next_peak_ssn": round(target, 3),
                "predicted_next_peak_ssn": round(prediction, 3),
                "absolute_error_ssn": round(abs(prediction - target), 3),
            }
        )
    return predictions


def low_order_dynamo_family_ablation(
    cycles: list[CycleFeature], polar: dict[str, Any]
) -> dict[str, Any]:
    cycle_by_number = {row.cycle: row for row in cycles}
    rows: list[dict[str, Any]] = []
    for pair in polar.get("cycle_minimum_pairs", []):
        cycle = cycle_by_number.get(pair.get("cycle"))
        if cycle is None:
            continue
        rows.append(
            {
                "cycle": int(pair["cycle"]),
                "polar_strength_proxy": float(pair["polar_strength_proxy"]),
                "current_cycle_peak_ssn": float(cycle.peak_ssn),
                "next_cycle_peak_ssn": float(pair["next_cycle_peak_ssn"]),
            }
        )
    families: list[dict[str, Any]] = []
    if len(rows) >= 3:
        for family in (
            "median_baseline",
            "persistence_baseline",
            "linear_polar",
            "nonlinear_quenching",
        ):
            predictions = _loo_family_predictions(rows, family)
            errors = np.array(
                [row["absolute_error_ssn"] for row in predictions], dtype=float
            )
            families.append(
                {
                    "family": family,
                    "loo_mae_ssn": _safe_round(float(np.mean(errors)), 3),
                    "loo_rmse_ssn": _safe_round(
                        float(np.sqrt(np.mean(errors**2))), 3
                    ),
                    "folds": predictions,
                }
            )
    ranked = sorted(
        families,
        key=lambda row: (
            float(row["loo_rmse_ssn"])
            if row["loo_rmse_ssn"] is not None
            else float("inf"),
            row["family"],
        ),
    )
    return {
        "status": "executed" if len(rows) >= 3 else "insufficient_data",
        "evaluation": "leave-one-complete-precursor-pair-out",
        "n_pairs": len(rows),
        "families": families,
        "ranking_by_loo_rmse": [row["family"] for row in ranked],
        "best_family": ranked[0]["family"] if ranked else None,
        "best_is_better_than_median": bool(
            ranked
            and next(
                row for row in families if row["family"] == "median_baseline"
            )["loo_rmse_ssn"]
            > ranked[0]["loo_rmse_ssn"]
        ),
        "small_sample_warning": len(rows) < 8,
        "interpretation": "模型族必须同时与中位数和持续性基线比较；小样本下即使低阶闭合最优，也只能说明相对拟合而非真实发电机方程。",
        "claim_boundary": "Mechanism-facing surrogate ablation only; not a full MHD model and not an operational forecast.",
    }


def negative_controls_and_placebos(
    cycles: list[CycleFeature], polar: dict[str, Any], seed: int = 0
) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    morphology_rows = [
        row for row in cycles if row.cycle >= 8 and row.peak_ssn > 0 and row.rise_years > 0
    ]
    peaks = np.array([row.peak_ssn for row in morphology_rows], dtype=float)
    rise = np.array([row.rise_years for row in morphology_rows], dtype=float)
    observed_waldmeier = spearman(peaks, rise)
    morphology_controls = np.array(
        [spearman(rng.permutation(peaks), rise) for _ in range(2000)], dtype=float
    )

    pairs = polar.get("cycle_minimum_pairs", [])
    polar_x = np.array(
        [row["polar_strength_proxy"] for row in pairs], dtype=float
    )
    polar_y = np.array([row["next_cycle_peak_ssn"] for row in pairs], dtype=float)
    observed_polar = spearman(polar_x, polar_y) if len(pairs) >= 3 else float("nan")
    polar_controls = (
        np.array(
            [spearman(polar_x, rng.permutation(polar_y)) for _ in range(2000)],
            dtype=float,
        )
        if len(pairs) >= 3
        else np.array([], dtype=float)
    )

    lagged = spearman(peaks[1:], rise[:-1]) if len(peaks) >= 4 else float("nan")
    return {
        "status": "executed" if len(morphology_rows) >= 8 else "insufficient_data",
        "seed": seed,
        "permutation_count": 2000,
        "controls": [
            {
                "id": "NC1_waldmeier_peak_permutation",
                "observed_spearman": _safe_round(observed_waldmeier),
                "control_mean": _safe_round(float(np.nanmean(morphology_controls))),
                "control_95_interval": [
                    _safe_round(float(np.nanpercentile(morphology_controls, 2.5))),
                    _safe_round(float(np.nanpercentile(morphology_controls, 97.5))),
                ],
                "empirical_two_sided_p": _empirical_two_sided_p(
                    observed_waldmeier, morphology_controls
                ),
                "operation": "fixed-seed permutation of cycle-level peak labels",
            },
            {
                "id": "NC2_polar_next_peak_permutation",
                "observed_spearman": _safe_round(observed_polar),
                "control_mean": _safe_round(float(np.nanmean(polar_controls)))
                if len(polar_controls)
                else None,
                "control_95_interval": [
                    _safe_round(float(np.nanpercentile(polar_controls, 2.5)))
                    if len(polar_controls)
                    else None,
                    _safe_round(float(np.nanpercentile(polar_controls, 97.5)))
                    if len(polar_controls)
                    else None,
                ],
                "empirical_two_sided_p": _empirical_two_sided_p(
                    observed_polar, polar_controls
                ),
                "operation": "fixed-seed permutation of next-cycle outcomes",
                "small_sample_warning": len(pairs) < 8,
            },
            {
                "id": "NC3_one_cycle_lag_placebo",
                "placebo_spearman_previous_rise_vs_next_peak": _safe_round(lagged),
                "operation": "one-cycle temporal misalignment",
            },
        ],
        "interpretation": "负对照用于发现偶然配对和时间错位；p 值只描述本次预注册安慰剂分布，不把相关性升级为因果。",
        "claim_boundary": "Negative controls can falsify an apparent association but cannot by themselves prove a solar-dynamo mechanism.",
    }


def hypothesis_cards(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    wald = analysis["waldmeier"]
    drift = analysis["f10_7_drift"]
    hemi = analysis["hemispheric_asymmetry"]
    polar = analysis.get("polar_precursor", {})
    toy = analysis.get("dynamo_toy_model", {})
    polar_n = polar.get("n_pairs", 0) or 0
    polar_corr = polar.get("spearman_polar_strength_vs_next_peak")
    cards = [
        {
            "id": "H1_poloidal_precursor_needed",
            "hypothesis": "Cycle-26 confidence should be governed by polar-field precursor evidence, not by sunspot/F10.7 proxies alone.",
            "mechanism": "Babcock-Leighton flux-transport dynamo",
            "supporting_evidence": [
                f"WSO polar precursor pairs available = {polar_n}",
                f"Spearman polar-strength-vs-next-peak = {polar_corr}",
            ],
            "counter_evidence": [
                "Only a small number of complete WSO-to-next-cycle pairs are available, so the result is a constraint rather than a definitive forecast."
            ],
            "score": 0.82 if polar_n >= 4 and polar_corr is not None and polar_corr > 0.3 else 0.72,
            "next_test": "Add NSO/SOLIS or polar faculae proxies to extend polar-field evidence before 1976.",
        },
        {
            "id": "H2_waldmeier_constraint",
            "hypothesis": "The observed cycle morphology supports a Waldmeier-like nonlinear dynamo constraint.",
            "mechanism": "Nonlinear amplification/saturation in the dynamo loop",
            "supporting_evidence": [
                f"Spearman peak-vs-rise-time = {wald['spearman_peak_vs_rise_time']}",
                f"Spearman peak-vs-rise-rate = {wald['spearman_peak_vs_rise_rate']}",
            ],
            "counter_evidence": ["Correlation is not causation; individual cycles can deviate from the trend."],
            "score": 0.7 if wald["spearman_peak_vs_rise_time"] < -0.3 else 0.55,
            "next_test": "Run leave-one-cycle-out robustness and compare with a low-order dynamo toy model.",
        },
        {
            "id": "H3_proxy_relation_drift",
            "hypothesis": "F10.7-to-sunspot relation drift should trigger lower confidence in single-proxy forecasts.",
            "mechanism": "Proxy-layer response and cycle-dependent activity morphology",
            "supporting_evidence": [
                "Windowed linear fits compare all available months, cycles 23-24, and cycle 25-to-date.",
                f"Drift flag = {drift['drift_flag']}",
            ],
            "counter_evidence": ["NOAA recent months can be preliminary; relationship should be rechecked after finalized data."],
            "score": 0.68 if drift["drift_flag"] else 0.52,
            "next_test": "Repeat with smoothed F10.7 and activity-phase stratification.",
        },
        {
            "id": "H4_hemispheric_asymmetry",
            "hypothesis": "North-south asymmetry is material evidence against a purely axisymmetric explanation.",
            "mechanism": "Stochastic emergence, cross-equatorial coupling, and non-axisymmetric dynamo components",
            "supporting_evidence": [
                f"Mean absolute hemispheric asymmetry = {hemi['mean_abs_asymmetry']}",
                f"Maximum absolute hemispheric asymmetry = {hemi['max_abs_asymmetry']}",
            ],
            "counter_evidence": ["SILSO hemispheric coverage begins in 1992, much shorter than total sunspot-number coverage."],
            "score": 0.66,
            "next_test": "Estimate cycle-phase dependence and compare north/south peak timing.",
        },
        {
            "id": "H5_low_order_dynamo_closure",
            "hypothesis": "A low-order Babcock-Leighton closure can explain how polar-field precursor evidence becomes next-cycle amplitude evidence, but it remains diagnostic under sparse WSO pairs.",
            "mechanism": "Poloidal-to-toroidal conversion with nonlinear quenching",
            "supporting_evidence": [
                f"Toy model status = {toy.get('status')}",
                f"Toy model RMSE = {toy.get('fit', {}).get('rmse_ssn')}; median baseline RMSE = {toy.get('fit', {}).get('median_baseline_rmse_ssn')}",
            ],
            "counter_evidence": [
                "The fitted quenching parameter is underdetermined with four WSO precursor pairs and should not be interpreted as a physical constant."
            ],
            "score": 0.64 if toy.get("status") == "executed" else 0.5,
            "next_test": "Extend the precursor series and compare the toy closure with a low-order ODE or flux-transport surrogate.",
        },
    ]
    return sorted(cards, key=lambda item: item["score"], reverse=True)


def tournament_ranking(cards: list[dict[str, Any]]) -> dict[str, Any]:
    weights = {
        "base_score": 0.42,
        "has_quantitative_evidence": 0.18,
        "has_counter_evidence": 0.14,
        "has_next_test": 0.14,
        "mechanism_specificity": 0.12,
    }

    def strength(card: dict[str, Any]) -> float:
        supporting = " ".join(card.get("supporting_evidence", []))
        mechanism = card.get("mechanism", "")
        quantitative = any(char.isdigit() for char in supporting)
        return (
            weights["base_score"] * float(card.get("score", 0.0))
            + weights["has_quantitative_evidence"] * (1.0 if quantitative else 0.0)
            + weights["has_counter_evidence"] * (1.0 if card.get("counter_evidence") else 0.0)
            + weights["has_next_test"] * (1.0 if card.get("next_test") else 0.0)
            + weights["mechanism_specificity"] * (1.0 if len(mechanism.split()) >= 3 else 0.65)
        )

    ratings = {card["id"]: 1000.0 for card in cards}
    comparisons: list[dict[str, Any]] = []
    k = 32.0
    for i, left in enumerate(cards):
        for right in cards[i + 1 :]:
            left_strength = strength(left)
            right_strength = strength(right)
            if left_strength == right_strength:
                left_score = 0.5
                winner = "draw"
            elif left_strength > right_strength:
                left_score = 1.0
                winner = left["id"]
            else:
                left_score = 0.0
                winner = right["id"]
            expected_left = 1.0 / (1.0 + 10.0 ** ((ratings[right["id"]] - ratings[left["id"]]) / 400.0))
            ratings[left["id"]] += k * (left_score - expected_left)
            ratings[right["id"]] += k * ((1.0 - left_score) - (1.0 - expected_left))
            comparisons.append(
                {
                    "left": left["id"],
                    "right": right["id"],
                    "winner": winner,
                    "left_strength": round(left_strength, 4),
                    "right_strength": round(right_strength, 4),
                }
            )
    ranking = sorted(
        [{"id": key, "elo": round(value, 1)} for key, value in ratings.items()],
        key=lambda row: row["elo"],
        reverse=True,
    )
    return {
        "method": "deterministic pairwise tournament inspired by Co-Scientist idea ranking",
        "weights": weights,
        "comparisons": comparisons,
        "ranking": ranking,
        "top_hypothesis": ranking[0]["id"] if ranking else None,
    }


REGISTERED_ANALYSIS_IDS = (
    "E0_data_vintage_audit",
    "E1_cycle_segmentation_baseline",
    "E2_waldmeier_leave_one_cycle_out",
    "E3_f107_phase_stratified_drift",
    "E4_extended_hemispheric_calibration",
    "E5_polar_precursor_robustness",
    "E6_low_order_dynamo_family_ablation",
    "E7_negative_controls_and_placebos",
    "E8_clean_reproduction",
)


def _load_analysis_inputs() -> dict[str, Any]:
    root = raw_root()
    monthly = read_silso_monthly(root / "SN_m_tot_V2.0.csv")
    smoothed = read_silso_monthly(root / "SN_ms_tot_V2.0.csv")
    hemispheric = read_silso_hemispheric(root / "SN_m_hem_V2.0.csv")
    extended = read_silso_extended_hemispheric(root / "Catalogue_B.csv")
    noaa = read_noaa_observed(root / "observed-solar-cycle-indices.json")
    predicted = read_noaa_predicted(root / "predicted-solar-cycle.json")
    wso = read_wso_polar(root / "wso_polar_field_observations.html")
    manifest = json.loads((root / "source_manifest.json").read_text(encoding="utf-8"))
    cycles = build_cycle_features(smoothed)
    polar = polar_precursor_analysis(cycles, wso)
    return {
        "monthly": monthly,
        "smoothed": smoothed,
        "hemispheric": hemispheric,
        "extended": extended,
        "noaa": noaa,
        "predicted": predicted,
        "wso": wso,
        "manifest": manifest,
        "cycles": cycles,
        "polar": polar,
    }


def _centered_feature_availability(
    smoothed: list[dict[str, Any]], forecast_origin: str
) -> list[dict[str, Any]]:
    valid = [row for row in smoothed if row["valid"]]
    if not valid:
        return []
    latest = valid[-1]
    month_index = int(latest["year"]) * 12 + int(latest["month"]) - 1 + 7
    available_year, month_zero = divmod(month_index, 12)
    return [
        {
            "feature": "ssn_smoothed_13m_centered",
            "operation": "smoothing",
            "smoothing_alignment": "centered",
            "smoothing_window_months": 13,
            "observed_at": f"{int(latest['year']):04d}-{int(latest['month']):02d}-01T00:00:00Z",
            "available_at": f"{available_year:04d}-{month_zero + 1:02d}-01T00:00:00Z",
            "forecast_origin": forecast_origin,
            "role": "retrospective_cycle_segmentation_only",
        }
    ]


def data_vintage_audit(manifest: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for source in manifest:
        relative = source.get("file")
        path = raw_root().parents[2] / str(relative) if isinstance(relative, str) else None
        if path is None or not path.is_file():
            rows.append(
                {
                    "id": source.get("id"),
                    "file_present": False,
                    "hash_matches": False,
                    "bytes_match": False,
                    "license_recorded": bool(source.get("license")),
                    "causal_timestamp_recorded": bool(source.get("available_at")),
                }
            )
            continue
        computed_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(
            {
                "id": source.get("id"),
                "file_present": True,
                "hash_matches": computed_hash == source.get("sha256"),
                "bytes_match": path.stat().st_size == source.get("bytes"),
                "license_recorded": bool(source.get("license")),
                "retrieved_at": source.get("retrieved_at"),
                "available_at": source.get("available_at"),
                "causal_timestamp_recorded": bool(source.get("available_at")),
            }
        )
    passed = bool(rows) and all(
        row["file_present"]
        and row["hash_matches"]
        and row["bytes_match"]
        and row["license_recorded"]
        and row["causal_timestamp_recorded"]
        for row in rows
    )
    return {
        "status": "passed" if passed else "warning",
        "source_count": len(rows),
        "sources": rows,
        "all_hashes_match": passed,
        "claim_boundary": "This gate verifies the local snapshot, not the upstream source's future immutability.",
    }


def _base_analysis(inputs: dict[str, Any]) -> dict[str, Any]:
    manifest = inputs["manifest"]
    forecast_origin = max(
        (
            str(row.get("retrieved_at"))
            for row in manifest
            if isinstance(row, dict) and row.get("retrieved_at")
        ),
        default="2026-07-13T00:00:00Z",
    )
    return {
        "project": "太阳活动周 AI 科学家",
        "claim_boundary": "结论限于回顾性观测约束、反证和假设优先级，因果机制仍待独立验证；产物仅供研究。",
        "data_manifest": manifest,
        "forecast_origin": forecast_origin,
        "feature_availability": _centered_feature_availability(
            inputs["smoothed"], forecast_origin
        ),
        "series_coverage": {
            "silso_monthly_start": inputs["monthly"][0]["decimal_year"],
            "silso_monthly_end": inputs["monthly"][-1]["decimal_year"],
            "silso_extended_hemispheric_start": inputs["extended"][0]["decimal_year"],
            "silso_extended_hemispheric_end": inputs["extended"][-1]["decimal_year"],
            "noaa_observed_end": inputs["noaa"][-1]["decimal_year"],
            "noaa_predicted_end": inputs["predicted"][-1]["decimal_year"],
            "wso_polar_start": inputs["wso"][0]["decimal_year"] if inputs["wso"] else None,
            "wso_polar_end": inputs["wso"][-1]["decimal_year"] if inputs["wso"] else None,
        },
    }


def _full_analysis_payload(inputs: dict[str, Any], seed: int) -> dict[str, Any]:
    cycles = inputs["cycles"]
    polar = inputs["polar"]
    analysis = {
        **_base_analysis(inputs),
        "data_vintage_audit": data_vintage_audit(inputs["manifest"]),
        "cycle_features": [asdict(cycle) for cycle in cycles],
        "cycle_segmentation_baseline": {
            "status": "executed",
            "n_complete_cycles": len(cycles),
            "split_strategy": "leave-one-complete-cycle-out_or_not-applicable",
            "centered_smoothing_role": "retrospective_only",
        },
        "cycle26_proxy_forecast": cycle26_proxy_forecast(cycles, inputs["predicted"]),
        "waldmeier": waldmeier_analysis(cycles),
        "waldmeier_leave_one_cycle_out": waldmeier_leave_one_cycle_out(cycles, seed),
        "f10_7_drift": f107_drift_analysis(inputs["noaa"]),
        "f107_phase_stratified_drift": f107_phase_stratified_drift(
            inputs["noaa"], cycles, seed
        ),
        "hemispheric_asymmetry": hemispheric_asymmetry(inputs["hemispheric"]),
        "extended_hemispheric_calibration": extended_hemispheric_calibration(
            inputs["extended"], inputs["hemispheric"]
        ),
        "polar_precursor": polar,
        "polar_precursor_robustness": polar_precursor_robustness(polar, seed),
    }
    analysis["dynamo_toy_model"] = low_order_dynamo_toy_model(cycles, polar)
    analysis["low_order_dynamo_family_ablation"] = low_order_dynamo_family_ablation(
        cycles, polar
    )
    analysis["negative_controls_and_placebos"] = negative_controls_and_placebos(
        cycles, polar, seed
    )
    analysis["hypothesis_cards"] = hypothesis_cards(analysis)
    analysis["tournament_ranking"] = tournament_ranking(analysis["hypothesis_cards"])
    analysis["clean_reproduction"] = {
        "status": "executed",
        "registered_outputs": [
            "cycle_features",
            "waldmeier_leave_one_cycle_out",
            "f107_phase_stratified_drift",
            "extended_hemispheric_calibration",
            "polar_precursor_robustness",
            "low_order_dynamo_family_ablation",
            "negative_controls_and_placebos",
        ],
        "seed": seed,
        "claim_boundary": "The registered worker uses a temporary data root; OS-level container isolation is reported separately by the manifest.",
    }
    return analysis


def run_registered_analysis(experiment_id: str, seed: int = 0) -> dict[str, Any]:
    """Dispatch exactly one reviewed experiment and return its bounded facts."""

    if experiment_id not in REGISTERED_ANALYSIS_IDS:
        raise ValueError(f"unregistered experiment id: {experiment_id}")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a non-negative integer")
    inputs = _load_analysis_inputs()
    base = _base_analysis(inputs)
    cycles = inputs["cycles"]
    polar = inputs["polar"]
    if experiment_id == "E0_data_vintage_audit":
        return {**base, "data_vintage_audit": data_vintage_audit(inputs["manifest"])}
    if experiment_id == "E1_cycle_segmentation_baseline":
        return {
            **base,
            "cycle_features": [asdict(cycle) for cycle in cycles],
            "cycle_segmentation_baseline": {
                "status": "executed",
                "n_complete_cycles": len(cycles),
                "split_strategy": "leave-one-complete-cycle-out_or_not-applicable",
                "centered_smoothing_role": "retrospective_only",
            },
        }
    if experiment_id == "E2_waldmeier_leave_one_cycle_out":
        return {
            **base,
            "waldmeier": waldmeier_analysis(cycles),
            "waldmeier_leave_one_cycle_out": waldmeier_leave_one_cycle_out(
                cycles, seed
            ),
        }
    if experiment_id == "E3_f107_phase_stratified_drift":
        return {
            **base,
            "f10_7_drift": f107_drift_analysis(inputs["noaa"]),
            "f107_phase_stratified_drift": f107_phase_stratified_drift(
                inputs["noaa"], cycles, seed
            ),
        }
    if experiment_id == "E4_extended_hemispheric_calibration":
        return {
            **base,
            "hemispheric_asymmetry": hemispheric_asymmetry(inputs["hemispheric"]),
            "extended_hemispheric_calibration": extended_hemispheric_calibration(
                inputs["extended"], inputs["hemispheric"]
            ),
        }
    if experiment_id == "E5_polar_precursor_robustness":
        return {
            **base,
            "polar_precursor": polar,
            "polar_precursor_robustness": polar_precursor_robustness(polar, seed),
        }
    if experiment_id == "E6_low_order_dynamo_family_ablation":
        return {
            **base,
            "dynamo_toy_model": low_order_dynamo_toy_model(cycles, polar),
            "low_order_dynamo_family_ablation": low_order_dynamo_family_ablation(
                cycles, polar
            ),
        }
    if experiment_id == "E7_negative_controls_and_placebos":
        return {
            **base,
            "negative_controls_and_placebos": negative_controls_and_placebos(
                cycles, polar, seed
            ),
        }
    return _full_analysis_payload(inputs, seed)


def run_b3_analysis(seed: int = 0) -> dict[str, Any]:
    analysis = _full_analysis_payload(_load_analysis_inputs(), seed)
    processed_root().mkdir(parents=True, exist_ok=True)
    output_root().mkdir(parents=True, exist_ok=True)
    (processed_root() / "cycle_features.json").write_text(
        json.dumps(analysis["cycle_features"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_root() / "b3_analysis_report.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return analysis
