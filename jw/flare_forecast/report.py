"""Reader-facing reports from issued bytes and the frozen full test cohort."""

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .assets import sha
from .model import FEATURES, load_forest
from .workflow import verify_selected_asset


def local_time(stamp):
    return (
        datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        .astimezone(timezone(timedelta(hours=8)))
        .isoformat(sep=" ")
    )


def history_summary(assets: Path):
    source = verify_selected_asset(assets, "prepared/daily_physical.csv")
    with source.open(encoding="utf-8-sig", newline="") as f:
        all_rows = list(csv.DictReader(f))
    rows = [r for r in all_rows if r["split"] == "test"]
    if (
        len(all_rows) != 2325
        or len(rows) != 841
        or any(r["status"] != "eligible" for r in rows)
    ):
        raise ValueError(
            "Frozen report cohort differs from the registered 2325/841 profile"
        )
    probabilities = load_forest(assets).predict(
        [[float(r[k]) for k in FEATURES] for r in rows]
    )
    summary = {
        "total_eligible": len(all_rows),
        "final_fit": 1201,
        "test_windows": len(rows),
        "test_years": [2023, 2024, 2025],
        "dataset_sha256": sha(source),
        "targets": {},
    }
    for target, label in (("C1.0+", "label_c1"), ("M1.0+", "label_m1")):
        y = np.array([int(r[label]) for r in rows])
        p = np.array([r[target] for r in probabilities])
        if not np.isin(y, [0, 1]).all():
            raise ValueError("Unknown label in qualified test cohort")
        predicted = p >= 0.5
        tp, fn = int(((y == 1) & predicted).sum()), int(((y == 1) & ~predicted).sum())
        fp, tn = int(((y == 0) & predicted).sum()), int(((y == 0) & ~predicted).sum())
        summary["targets"][target] = {
            "positive": int(y.sum()),
            "negative": int((y == 0).sum()),
            "accuracy": float((predicted == y).mean()),
            "brier": float(((p - y) ** 2).mean()),
            "always_yes_accuracy": float(y.mean()),
            "always_yes_brier": float((1 - y).mean()),
            "always_no_brier": float(y.mean()),
            "recall": tp / (tp + fn),
            "tss": tp / (tp + fn) - fp / (fp + tn),
            "tp": tp,
            "fn": fn,
            "fp": fp,
            "tn": tn,
        }
    return summary, rows, probabilities


def build_report(forecast: dict, assets: Path, output: Path, live_dir=None) -> str:
    """Build the full report inside the native JW forecasting tool."""
    from .report_full import build_full_report

    return build_full_report(forecast, assets, output, live_dir)
