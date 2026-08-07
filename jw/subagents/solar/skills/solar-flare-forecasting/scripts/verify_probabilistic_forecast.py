#!/usr/bin/env python3
"""Compute compact probability and threshold verification for flare forecasts."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def _rounded(value: float | None) -> float | None:
    return round(value, 8) if value is not None else None


def verify(
    rows: list[dict[str, str]],
    *,
    probability_column: str = "probability",
    outcome_column: str = "outcome",
    baseline_column: str = "baseline_probability",
    threshold: float = 0.5,
    bins: int = 10,
) -> dict[str, Any]:
    issues: list[str] = []
    parsed: list[tuple[float, int, float]] = []
    required = {probability_column, outcome_column, baseline_column}
    if not rows:
        return {"status": "error", "issues": ["forecast result table is empty"]}
    missing = required - set(rows[0])
    if missing:
        return {"status": "error", "issues": [f"missing columns: {sorted(missing)}"]}
    if not 0 <= threshold <= 1:
        return {"status": "error", "issues": ["threshold must be within [0, 1]"]}
    if bins < 1:
        return {"status": "error", "issues": ["bins must be positive"]}

    for index, row in enumerate(rows, start=2):
        try:
            probability = float(row[probability_column])
            baseline = float(row[baseline_column])
            outcome = int(row[outcome_column])
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(f"row {index}: cannot parse forecast values: {exc}")
            continue
        if not 0 <= probability <= 1 or not 0 <= baseline <= 1:
            issues.append(f"row {index}: probabilities must be within [0, 1]")
            continue
        if outcome not in {0, 1}:
            issues.append(f"row {index}: outcome must be 0 or 1")
            continue
        parsed.append((probability, outcome, baseline))

    if issues:
        return {"status": "error", "issues": issues}

    n = len(parsed)
    event_count = sum(outcome for _, outcome, _ in parsed)
    brier = sum((probability - outcome) ** 2 for probability, outcome, _ in parsed) / n
    baseline_brier = (
        sum((baseline - outcome) ** 2 for _, outcome, baseline in parsed) / n
    )
    brier_skill = 1 - brier / baseline_brier if baseline_brier else None
    epsilon = 1e-15
    log_loss = (
        -sum(
            outcome * math.log(min(max(probability, epsilon), 1 - epsilon))
            + (1 - outcome) * math.log(min(max(1 - probability, epsilon), 1 - epsilon))
            for probability, outcome, _ in parsed
        )
        / n
    )

    tp = tn = fp = fn = 0
    for probability, outcome, _ in parsed:
        predicted = int(probability >= threshold)
        if predicted == 1 and outcome == 1:
            tp += 1
        elif predicted == 1:
            fp += 1
        elif outcome == 1:
            fn += 1
        else:
            tn += 1

    pod = _ratio(tp, tp + fn)
    far = _ratio(fp, tp + fp)
    csi = _ratio(tp, tp + fp + fn)
    false_positive_rate = _ratio(fp, fp + tn)
    tss = (
        pod - false_positive_rate
        if pod is not None and false_positive_rate is not None
        else None
    )
    hss_denominator = (tp + fn) * (fn + tn) + (tp + fp) * (fp + tn)
    hss = _ratio(2 * (tp * tn - fp * fn), hss_denominator)
    precision = _ratio(tp, tp + fp)

    calibration: list[dict[str, Any]] = []
    for index in range(bins):
        lower = index / bins
        upper = (index + 1) / bins
        members = [
            (probability, outcome)
            for probability, outcome, _ in parsed
            if lower <= probability <= upper
            and (index == bins - 1 or probability < upper)
        ]
        if not members:
            continue
        calibration.append(
            {
                "lower": lower,
                "upper": upper,
                "count": len(members),
                "mean_probability": _rounded(
                    sum(item[0] for item in members) / len(members)
                ),
                "observed_rate": _rounded(
                    sum(item[1] for item in members) / len(members)
                ),
            }
        )

    return {
        "status": "ok",
        "issues": [],
        "sample": {
            "count": n,
            "event_count": event_count,
            "event_rate": _rounded(event_count / n),
        },
        "probability_metrics": {
            "brier_score": _rounded(brier),
            "baseline_brier_score": _rounded(baseline_brier),
            "brier_skill_score": _rounded(brier_skill),
            "log_loss": _rounded(log_loss),
        },
        "threshold_metrics": {
            "threshold": threshold,
            "tp": tp,
            "tn": tn,
            "fp": fp,
            "fn": fn,
            "pod": _rounded(pod),
            "far": _rounded(far),
            "csi": _rounded(csi),
            "tss": _rounded(tss),
            "hss": _rounded(hss),
            "precision": _rounded(precision),
        },
        "calibration_bins": calibration,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("table", type=Path)
    parser.add_argument("--probability-column", default="probability")
    parser.add_argument("--outcome-column", default="outcome")
    parser.add_argument("--baseline-column", default="baseline_probability")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--bins", type=int, default=10)
    args = parser.parse_args()
    with args.table.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    result = verify(
        rows,
        probability_column=args.probability_column,
        outcome_column=args.outcome_column,
        baseline_column=args.baseline_column,
        threshold=args.threshold,
        bins=args.bins,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
