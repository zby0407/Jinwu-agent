#!/usr/bin/env python3
"""Run diagnostic experiments for solar-cycle prediction and mechanism analysis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error


def grade(result: dict) -> str:
    """Simple evidence grade based on stability and sanity."""
    warnings = result.get("warnings", [])
    if result.get("error"):
        return "FAIL"
    if warnings:
        return "WARNING"
    return "PASS"


def experiment_baseline(features: pd.DataFrame) -> dict:
    """Persistence baseline: predict next cycle peak = previous cycle peak."""
    if len(features) < 2:
        return {"experiment": "baseline", "error": "Need >=2 cycles"}
    y_true = features["peak_sn"].iloc[1:].values
    y_pred = features["peak_sn"].iloc[:-1].values
    return {
        "experiment": "baseline",
        "description": "Predict next cycle peak equals previous cycle peak",
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "warnings": ["Very small sample"] if len(features) < 10 else [],
    }


def experiment_backtest(features: pd.DataFrame) -> dict:
    """Leave-one-cycle-out regression using cycle features."""
    if len(features) < 4:
        return {"experiment": "backtest", "error": "Need >=4 cycles"}

    feats = ["length_months", "rise_months", "min_sn", "rise_slope"]
    feats = [c for c in feats if c in features.columns]
    y_true, y_pred = [], []
    for i in range(len(features)):
        train = features.drop(index=i)
        test = features.iloc[[i]]
        model = Ridge(alpha=1.0)
        model.fit(train[feats], train["peak_sn"])
        y_true.append(float(test["peak_sn"].iloc[0]))
        y_pred.append(float(model.predict(test[feats])[0]))

    return {
        "experiment": "backtest",
        "description": "Leave-one-cycle-out Ridge regression on cycle morphology",
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "predictions": [
            {"true": t, "predicted": p} for t, p in zip(y_true, y_pred, strict=False)
        ],
        "warnings": ["Small sample; confidence low"] if len(features) < 10 else [],
    }


def experiment_ablation(features: pd.DataFrame) -> dict:
    """Remove one feature at a time and measure RMSE change."""
    if len(features) < 4:
        return {"experiment": "ablation", "error": "Need >=4 cycles"}

    feats = ["length_months", "rise_months", "min_sn", "rise_slope"]
    feats = [c for c in feats if c in features.columns]
    base = experiment_backtest(features)
    base_rmse = base.get("rmse", np.nan)

    deltas = {}
    for f in feats:
        reduced = [c for c in feats if c != f]
        y_true, y_pred = [], []
        for i in range(len(features)):
            train = features.drop(index=i)
            test = features.iloc[[i]]
            model = Ridge(alpha=1.0)
            model.fit(train[reduced], train["peak_sn"])
            y_true.append(float(test["peak_sn"].iloc[0]))
            y_pred.append(float(model.predict(test[reduced])[0]))
        rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
        deltas[f] = {"rmse": rmse, "delta": float(rmse - base_rmse)}

    warnings = [
        f"Model heavily depends on {f}"
        for f, d in deltas.items()
        if d["delta"] > base_rmse * 0.3
    ]
    return {
        "experiment": "ablation",
        "description": "Feature ablation on backtest model",
        "base_rmse": base_rmse,
        "deltas": deltas,
        "warnings": warnings,
    }


def experiment_polar_precursor(features: pd.DataFrame) -> dict:
    """Correlate the prior-cycle polar-field strength proxy with the next cycle peak."""
    proxy_col = "polar_proxy_abs_combined"
    if proxy_col not in features.columns:
        # Fallback for legacy feature tables.
        proxy_col = "polar_proxy_combined"
    if proxy_col not in features.columns:
        return {
            "experiment": "polar_precursor",
            "error": "No polar proxy column in features",
            "note": "Run build_features.py with --polar-monthly to produce this column",
        }

    # The proxy is measured at the minimum of cycle i; it should predict the
    # peak of cycle i+1. Build paired samples from consecutive cycles where the
    # proxy is available.
    df = features.sort_values("cycle").reset_index(drop=True)
    df[proxy_col] = pd.to_numeric(df[proxy_col], errors="coerce")
    pairs = []
    for i in range(1, len(df)):
        prev = df.iloc[i - 1]
        curr = df.iloc[i]
        if pd.notna(prev[proxy_col]) and pd.notna(curr["peak_sn"]):
            pairs.append(
                {
                    "cycle": int(curr["cycle"]),
                    "proxy": float(prev[proxy_col]),
                    "peak_sn": float(curr["peak_sn"]),
                }
            )

    if len(pairs) < 2:
        return {
            "experiment": "polar_precursor",
            "description": "Polar-field precursor vs next-cycle peak sunspot number",
            "n_pairs": len(pairs),
            "pairs": pairs,
            "error": "Need at least 2 cycles with polar precursor data",
            "warnings": ["Very small sample; cannot estimate stable relationship"],
        }

    proxy = np.array([p["proxy"] for p in pairs])
    peak = np.array([p["peak_sn"] for p in pairs])
    corr = float(np.corrcoef(proxy, peak)[0, 1])

    # Leave-one-out regression with an intercept.
    y_true, y_pred = [], []
    for i in range(len(pairs)):
        x_train = np.vstack([np.delete(proxy, i), np.ones(len(proxy) - 1)]).T
        y_train = np.delete(peak, i)
        x_test = np.array([proxy[i], 1.0])
        coef = np.linalg.lstsq(x_train, y_train, rcond=None)[0]
        y_true.append(float(peak[i]))
        y_pred.append(float(x_test @ coef))

    mae = float(mean_absolute_error(y_true, y_pred))
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))

    return {
        "experiment": "polar_precursor",
        "description": "Polar-field precursor vs next-cycle peak sunspot number",
        "n_pairs": len(pairs),
        "pairs": pairs,
        "correlation": corr,
        "mae": mae,
        "rmse": rmse,
        "warnings": [
            "Very small sample; confidence low"
            if len(pairs) < 10
            else "Moderate sample"
        ],
    }


def experiment_drift(features: pd.DataFrame) -> dict:
    """Placeholder: detect index relationship drift."""
    if "mean_f10" not in features.columns:
        return {
            "experiment": "drift",
            "error": "No mean_f10 column; F10.7 data required",
        }
    corr = float(features["peak_sn"].corr(features["mean_f10"]))
    return {
        "experiment": "drift",
        "description": "Correlation between peak sunspot number and mean F10.7 per cycle",
        "correlation": corr,
        "warnings": [] if abs(corr) > 0.7 else ["Weak or unstable correlation"],
    }


EXPERIMENTS = {
    "baseline": experiment_baseline,
    "backtest": experiment_backtest,
    "ablation": experiment_ablation,
    "polar_precursor": experiment_polar_precursor,
    "drift": experiment_drift,
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run solar-cycle diagnostic experiments"
    )
    parser.add_argument("--features", required=True, help="Cycle features CSV")
    parser.add_argument("--output-dir", default="./artifacts", help="Output directory")
    parser.add_argument(
        "--experiments",
        default="baseline,backtest,ablation,drift",
        help="Comma-separated experiment names",
    )
    args = parser.parse_args()

    features = pd.read_csv(args.features)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    names = [n.strip() for n in args.experiments.split(",")]
    results = []
    for name in names:
        fn = EXPERIMENTS.get(name)
        if fn is None:
            results.append({"experiment": name, "error": f"Unknown experiment: {name}"})
            continue
        result = fn(features)
        result["grade"] = grade(result)
        results.append(result)

    summary_path = out_dir / "experiment_summary.json"
    summary_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Wrote experiment summary to {summary_path}")

    # Human-readable log
    log_path = out_dir / "experiment_log.md"
    lines = ["# Solar-Cycle Experiment Log\n"]
    for r in results:
        lines.append(f"## {r['experiment']} — {r.get('grade', 'UNKNOWN')}")
        lines.append(r.get("description", ""))
        lines.append(f"```json\n{json.dumps(r, indent=2)}\n```\n")
    log_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote experiment log to {log_path}")


if __name__ == "__main__":
    main()
