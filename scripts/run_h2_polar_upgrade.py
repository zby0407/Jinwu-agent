#!/usr/bin/env python3
"""Reproduce the controlled H2 polar-precursor upgrade from registered inputs."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

from jw.solar_forecast.h2_upgrade import run_h2_upgrade


def _number(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except ValueError:
        return None


def _decimal_month(value: str) -> float:
    year, month = value[:7].split("-")
    return int(year) + (int(month) - 0.5) / 12.0


def _polar_observations(path: Path) -> dict[str, list[tuple[float, float, str]]]:
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    observations = {"north": [], "south": []}
    for row in csv.DictReader(lines):
        for hemisphere, prefix in (("north", "N"), ("south", "S")):
            for source in ("MWO", "WSO"):
                date = _number(row.get(f"{prefix} {source} Date"))
                field = _number(row.get(f"{prefix} {source} PField"))
                if date is not None and field is not None:
                    observations[hemisphere].append((date, field, source))
    for values in observations.values():
        values.sort()
    return observations


def _hemisphere_value(
    observations: dict[str, list[tuple[float, float, str]]],
    hemisphere: str,
    center: float,
) -> tuple[float, str]:
    for source in ("WSO", "MWO"):
        eligible = [
            item
            for item in observations[hemisphere]
            if item[2] == source and center - 0.5 <= item[0] <= center + 0.5
        ]
        if eligible:
            return sum(abs(item[1]) for item in eligible) / len(eligible), source
    for source in ("WSO", "MWO"):
        prior = [
            item
            for item in observations[hemisphere]
            if item[2] == source and item[0] <= center and center - item[0] <= 1.5
        ]
        if prior:
            return abs(prior[-1][1]), source
    raise ValueError(f"no polar observation for {hemisphere} near {center:.4f}")


def build_rows(
    feature_table: Path,
    feature_receipt: Path,
    polar_path: Path,
    provisional_table: Path | None,
) -> tuple[list[dict[str, object]], dict[str, object] | None]:
    with feature_table.open(encoding="utf-8", newline="") as handle:
        features = {
            int(row["cycle_number"]): row
            for row in csv.DictReader(handle)
            if row.get("row_role") == "analysis"
        }
    receipt = json.loads(feature_receipt.read_text(encoding="utf-8"))
    records = {
        int(item["target_cycle_id"]): item for item in receipt["feature_records"]
    }
    observations = _polar_observations(polar_path)
    rows: list[dict[str, object]] = []
    for cycle in sorted(features):
        row = features[cycle]
        center = _decimal_month(row["minimum_date"])
        north, north_source = _hemisphere_value(observations, "north", center)
        south, south_source = _hemisphere_value(observations, "south", center)
        record = records[cycle]
        rows.append(
            {
                "target_cycle_id": cycle,
                "polar_mean_abs_gauss": float(record["value"]),
                "weakest_hemisphere_abs_gauss": min(north, south),
                "north_abs_gauss": north,
                "south_abs_gauss": south,
                "target": float(row["peak_smoothed_sunspot_number"]),
                "target_dispersion": float(row["peak_smoothed_sunspot_number_sigma"]),
                "measurement_regime": str(record["measurement_regime"]),
                "north_source": north_source,
                "south_source": south_source,
            }
        )
    provisional: dict[str, object] | None = None
    if provisional_table is not None:
        with provisional_table.open(encoding="utf-8", newline="") as handle:
            p25 = next(row for row in csv.DictReader(handle) if int(row["cycle"]) == 25)
        center = _decimal_month(p25["minimum_date"])
        north, north_source = _hemisphere_value(observations, "north", center)
        south, south_source = _hemisphere_value(observations, "south", center)
        provisional = {
            "target_cycle_id": 25,
            "polar_mean_abs_gauss": (north + south) / 2.0,
            "weakest_hemisphere_abs_gauss": min(north, south),
            "target": float(p25["peak"]),
            # SC25 peak is provisional; this positive placeholder is required
            # only for the WLS challenger and is never used in the skill gate.
            "target_dispersion": 8.0,
            "measurement_regime": "WSO",
            "target_status": "provisional_as_of_2026-08-27",
            "north_source": north_source,
            "south_source": south_source,
        }
    return rows, provisional


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-table", type=Path, required=True)
    parser.add_argument("--feature-receipt", type=Path, required=True)
    parser.add_argument("--polar-field", type=Path, required=True)
    parser.add_argument("--provisional-table", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-resamples", type=int, default=10_000)
    args = parser.parse_args()
    rows, provisional = build_rows(
        args.feature_table,
        args.feature_receipt,
        args.polar_field,
        args.provisional_table,
    )
    result = run_h2_upgrade(
        rows, provisional_row=provisional, bootstrap_resamples=args.bootstrap_resamples
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "h2_upgrade_receipt.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest = {
        "schema_version": "h2-upgrade-run-manifest-v1",
        "feature_table": str(args.feature_table.resolve()),
        "feature_receipt": str(args.feature_receipt.resolve()),
        "polar_field": str(args.polar_field.resolve()),
        "provisional_table": (
            str(args.provisional_table.resolve())
            if args.provisional_table is not None
            else None
        ),
        "bootstrap_seed": result["bootstrap"]["seed"],
        "bootstrap_resamples": result["bootstrap"]["resamples"],
        "skill_gate_model": result["skill_gate_model"],
        "provisional_excluded_from_skill_gate": provisional is not None,
    }
    (args.output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (args.output_dir / "h2_input_rows.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with (args.output_dir / "h2_model_metrics.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "model",
                "candidate_mae",
                "candidate_rmse",
                "mae_improvement",
                "ci_low",
                "ci_high",
            ],
        )
        writer.writeheader()
        for model, payload in result["models"].items():
            metrics = payload["metrics"]
            writer.writerow(
                {
                    "model": model,
                    "candidate_mae": metrics["candidate_mae"],
                    "candidate_rmse": metrics["candidate_rmse"],
                    "mae_improvement": metrics["mae_improvement"],
                    "ci_low": metrics["mae_improvement_interval"][0],
                    "ci_high": metrics["mae_improvement_interval"][1],
                }
            )
    with (args.output_dir / "h2_rolling_predictions.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "model",
                "test_cycle",
                "observed",
                "candidate_prediction",
                "training_mean_prediction",
                "persistence_prediction",
                "measurement_regime",
            ],
        )
        writer.writeheader()
        for model, payload in result["models"].items():
            for fold in payload["folds"]:
                writer.writerow(
                    {
                        "model": model,
                        **{
                            key: fold[key]
                            for key in writer.fieldnames
                            if key != "model"
                        },
                    }
                )
    if provisional is not None:
        (args.output_dir / "h2_provisional_check.json").write_text(
            json.dumps(result["provisional_check"], ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "status": result["status"],
                "primary_mae": result["models"]["mean_polar_linear"]["metrics"][
                    "candidate_mae"
                ],
                "output_dir": str(args.output_dir),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
