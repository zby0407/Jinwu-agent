"""Independent CSV/SciPy verifier. Does not import JW or the D calculator."""

import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path

from scipy.stats import pearsonr, spearmanr, linregress


def read(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def check(actual, expected):
    if expected is None:
        assert actual == "", (actual, expected)
    else:
        assert math.isclose(float(actual), expected, rel_tol=1e-10, abs_tol=1e-10), (
            actual,
            expected,
        )


def verify(output):
    output = Path(output)
    features = {
        (r["scenario"], int(r["cycle"])): r for r in read(output / "cycle_features.csv")
    }
    assert len(features) == 360
    for r in features.values():
        rise, decline, peak = (
            float(r["rise_months"]),
            float(r["decline_months"]),
            float(r["smoothed_peak"]),
        )
        check(r["rise_decline_ratio"], rise / decline if decline else None)
        check(r["peak_over_rise"], peak / rise if rise else None)
        assert r["fixed_window_before_peak"] == str(rise >= 24)
    pairs = read(output / "paired_samples.csv")
    by = {}
    for r in pairs:
        a, b = int(r["x_cycle"]), int(r["y_cycle"])
        assert b - a == int(r["lag_cycles"])
        x, y = (
            features[r["scenario"], a][r["x_metric"]],
            features[r["scenario"], b][r["y_metric"]],
        )
        assert r["x"] == x and r["y"] == y
        assert r["included"] == str(x != "" and y != "")
        assert r["touches_SC3_6"] == str(a in (3, 4, 5, 6) or b in (3, 4, 5, 6))
        by.setdefault((r["scenario"], r["relation"]), []).append(r)
    stats = read(output / "association_statistics.csv")
    count = 0
    for row in stats:
        sample = [
            r for r in by[row["scenario"], row["relation"]] if r["included"] == "True"
        ]
        subset = row["subset"]
        if subset == "early_y_SC1_12":
            sample = [r for r in sample if int(r["y_cycle"]) <= 12]
        elif subset == "late_y_SC13_24":
            sample = [r for r in sample if int(r["y_cycle"]) >= 13]
        elif subset == "exclude_physical_SC3_6":
            sample = [r for r in sample if r["touches_SC3_6"] == "False"]
        elif subset.startswith("leave_cycle_"):
            excluded = int(subset.removeprefix("leave_cycle_"))
            sample = [
                r
                for r in sample
                if excluded not in [int(r["x_cycle"]), int(r["y_cycle"])]
            ]
        elif subset.startswith("leave_pair_"):
            a, b = subset.removeprefix("leave_pair_").split("_")
            sample = [r for r in sample if (r["x_cycle"], r["y_cycle"]) != (a, b)]
        else:
            assert subset == "all"
        assert row["included_pairs"] == "|".join(
            r["x_cycle"] + ":" + r["y_cycle"] for r in sample
        )
        assert int(row["n"]) == len(sample)
        xs = [float(r["x"]) for r in sample]
        ys = [float(r["y"]) for r in sample]
        defined = len(xs) >= 4 and len(set(xs)) > 1 and len(set(ys)) > 1
        expected = (
            [
                pearsonr(xs, ys).statistic,
                spearmanr(xs, ys).statistic,
                linregress(xs, ys).slope,
                linregress(xs, ys).intercept,
            ]
            if defined
            else [None] * 4
        )
        for key, value in zip(
            ["pearson_r", "spearman_rho", "ols_slope", "ols_intercept"], expected
        ):
            check(row[key], value)
            count += 1
        for key, value in zip(
            ["pearson_direction", "spearman_direction"], expected[:2]
        ):
            want = (
                "undefined"
                if value is None
                else "positive"
                if value > 1e-12
                else "negative"
                if value < -1e-12
                else "numerically_zero"
            )
            assert row[key] == want
    coupling = output / "coupling_diagnostic.csv"
    if coupling.exists():
        for r in read(coupling):
            ordered = [features[r["scenario"], c] for c in range(1, 25)]
            shift = int(r["rise_time_circular_shift"])
            observations = [
                (
                    float(v["smoothed_peak"])
                    / float(ordered[(i + shift) % 24]["rise_months"]),
                    float(v["smoothed_peak"]),
                )
                for i, v in enumerate(ordered)
                if float(ordered[(i + shift) % 24]["rise_months"])
            ]
            xs, ys = map(list, zip(*observations))
            defined = len(xs) >= 4 and len(set(xs)) > 1 and len(set(ys)) > 1
            check(r["pearson_r"], pearsonr(xs, ys).statistic if defined else None)
            check(r["spearman_rho"], spearmanr(xs, ys).statistic if defined else None)
            assert int(r["n"]) == len(xs)
    return {
        "status": "pass",
        "statistics_rows": len(stats),
        "paired_rows": len(pairs),
        "effect_values_checked": count,
        "method": "independent CSV indexing and SciPy statistics; no JW/calculator imports; raw-feature integrity separately enforced by tool review",
        "calibrated_uncertainty": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--audit", required=True)
    args = parser.parse_args()
    record = {"started_utc": datetime.now(timezone.utc).isoformat(), "agent_run": False}
    try:
        record.update(verify(args.output))
    except Exception as exc:
        record.update(status="failed", error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        Path(args.audit).write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", "utf-8"
        )
    print(json.dumps(record, ensure_ascii=False))
