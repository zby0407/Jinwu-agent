"""Bounded annual SILSO/GNbb2 comparison. Standard-library numeric core; matplotlib figures; no JW approval."""

from __future__ import annotations

import argparse
import calendar
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import shutil
import sys

PROTOCOL = "silso_gnbb2_annual_v1"
METRICS = (
    "annual_mean",
    "annual_peak",
    "annual_sum",
    "low_fraction",
    "longest_low_years",
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_csv(path, rows):
    if not rows:
        raise ValueError("empty required table")
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def validate_parameters(p):
    expected = {
        "schema": "cross-series-parameters-v1",
        "protocol": PROTOCOL,
        "cycles": list(range(1, 24)),
        "targets": [3, 4, 5, 6],
        "references": [1, 2, 7, 8, 9, 10],
        "annual_years": [1749, 2015],
        "weightings": ["calendar_days", "equal_months"],
        "memberships": ["whole_calendar_year", "midyear"],
        "primary": {"weighting": "calendar_days", "membership": "whole_calendar_year"},
        "required_months": 12,
        "low_fraction_of_reference": 0.25,
        "trough_boundaries": [5, 6, 7],
        "trough_half_width_years": 2,
        "tie_rule": "earliest_plus_all",
        "rank": "competition_descending",
        "tolerance": {"absolute": 1e-12, "relative": 1e-12},
        "missing": "fail_required_scientific_values_keep_missing_error_metadata",
        "uncertainty": "Eg_retained_not_propagated_no_p_value",
        "known_source_interpolation": ["1824-02"],
        "scientific_status": "not_reviewed",
    }
    if p != expected:
        raise ValueError(
            "parameters differ from bounded C1 protocol; define and test a new version"
        )


def finite(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite input")
    return result


def parse_monthly(path):
    rows = {}
    previous = None
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        v = line.split()
        if not v:
            continue
        if len(v) not in (6, 7):
            raise ValueError("unexpected SILSO monthly columns")
        year, month = int(v[0]), int(v[1])
        if not 1 <= month <= 12:
            raise ValueError("invalid month")
        key = year * 12 + month - 1
        if previous is not None and key != previous + 1:
            raise ValueError("duplicate, unordered or missing month")
        value, sigma, count = finite(v[3]), finite(v[4]), int(v[5])
        if value < 0 and value != -1 or sigma < 0 and sigma != -1 or count < -1:
            raise ValueError("unexpected negative monthly field")
        rows[key] = {
            "value": None if value == -1 else value,
            "sigma": None if sigma == -1 else sigma,
            "observations": None if count == -1 else count,
        }
        previous = key
    if not rows:
        raise ValueError("empty monthly file")
    return rows


def parse_gn(path):
    rows = {}
    previous = None
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        v = line.split()
        if not v:
            continue
        if len(v) != 3:
            raise ValueError("GNbb2 requires exactly year/Gn/Eg")
        stamp, value, error = map(finite, v)
        year = int(stamp)
        if stamp != year + 0.5:
            raise ValueError("GNbb2 year must be a calendar midyear label")
        if previous is not None and year != previous + 1:
            raise ValueError("duplicate, unordered or missing GN year")
        if value < 0 and value != -1 or error < 0 and error != -1:
            raise ValueError("unexpected negative GNbb2 field")
        rows[year] = {
            "value": None if value == -1 else value,
            "Eg": None if error == -1 else error,
        }
        previous = year
    if not rows:
        raise ValueError("empty GNbb2 file")
    return rows


def parse_boundaries(path):
    rows = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        v = line.split()
        if not v or not v[0].isdigit():
            continue
        c, year, month = map(int, v[:3])
        if c in rows or not 1 <= month <= 12:
            raise ValueError("duplicate cycle or invalid boundary month")
        rows[c] = year * 12 + month - 1
    if list(rows) != list(range(1, 26)) or list(rows.values()) != sorted(
        set(rows.values())
    ):
        raise ValueError("expected ordered SC1-25 boundaries")
    return rows


def members(years, start, end, rule):
    if start >= end:
        raise ValueError("nonpositive cycle length")
    if rule == "whole_calendar_year":
        return [y for y in years if start <= y * 12 and (y + 1) * 12 <= end]
    if rule == "midyear":
        return [y for y in years if start <= y * 12 + 6 < end]
    raise ValueError("unknown calendar membership rule")


def annualize(monthly, year, weighting):
    values = [monthly[year * 12 + month - 1]["value"] for month in range(1, 13)]
    if any(v is None for v in values):
        raise ValueError("12 valid months are required")
    if weighting not in ("calendar_days", "equal_months"):
        raise ValueError("unknown annual weighting")
    weights = [
        calendar.monthrange(year, month)[1] if weighting == "calendar_days" else 1
        for month in range(1, 13)
    ]
    return math.fsum(v * w for v, w in zip(values, weights)) / sum(weights)


def sign(a, b, tolerance):
    difference = a - b
    margin = tolerance["absolute"] + tolerance["relative"] * max(abs(a), abs(b))
    return (
        "higher"
        if difference > margin
        else "lower"
        if difference < -margin
        else "numerically_tied"
    )


def longest_low(year_values, threshold):
    longest = current = 0
    previous = None
    for year, value in year_values:
        if previous is not None and year != previous + 1:
            current = 0
        current = current + 1 if value < threshold else 0
        longest = max(longest, current)
        previous = year
    return longest


def summary(values, low_threshold):
    if not values:
        raise ValueError("empty annual cycle subset")
    nums = [v for _, v in values]
    peak = max(nums)
    peaks = [y for y, v in values if v == peak]
    return {
        "year_count": len(nums),
        "annual_mean": math.fsum(nums) / len(nums),
        "annual_peak": peak,
        "annual_peak_year": peaks[0],
        "all_peak_years": "|".join(map(str, peaks)),
        "annual_sum": math.fsum(nums),
        "low_threshold": low_threshold,
        "low_fraction": sum(v < low_threshold for v in nums) / len(nums),
        "longest_low_years": longest_low(values, low_threshold),
    }


def load_inputs(inputs, p):
    inputs = Path(inputs).resolve()
    manifest = json.loads((inputs / "input_manifest.json").read_text(encoding="utf-8"))
    records = manifest["files"]
    if manifest.get("schema") != "cross-series-inputs-v1" or len(records) != 6:
        raise ValueError("expected six frozen C input identities")
    paths = {}
    for record in records:
        path = (inputs / record["path"]).resolve()
        if not path.is_relative_to(inputs) or record["key"] in paths:
            raise ValueError("escaped or duplicate input")
        if digest(path) != record["sha256"] or path.stat().st_size != record["bytes"]:
            raise ValueError("input hash/size mismatch: " + record["key"])
        paths[record["key"]] = path
    if set(paths) != {
        "monthly",
        "smoothed",
        "boundaries",
        "a_metrics",
        "a_parameters",
        "gnbb2",
    }:
        raise ValueError("unexpected input keys")
    monthly, gn, boundaries = (
        parse_monthly(paths["monthly"]),
        parse_gn(paths["gnbb2"]),
        parse_boundaries(paths["boundaries"]),
    )
    years = list(range(p["annual_years"][0], p["annual_years"][1] + 1))
    for y in years:
        if y not in gn or gn[y]["value"] is None:
            raise ValueError("required GN year unavailable")
        if any(
            y * 12 + m not in monthly or monthly[y * 12 + m]["value"] is None
            for m in range(12)
        ):
            raise ValueError("required year lacks 12 valid SILSO months")
    assignments = []
    for rule in p["memberships"]:
        used = set()
        for c in p["cycles"]:
            # Do not conceal truncated cycles by dropping unmatched final years.
            if (
                boundaries[c] < years[0] * 12
                or boundaries[c + 1] > (years[-1] + 1) * 12
            ):
                raise ValueError("cycle outside common observation coverage")
            selected = members(years, boundaries[c], boundaries[c + 1], rule)
            if not selected or used.intersection(selected):
                raise ValueError("empty or overlapping annual cycle membership")
            used.update(selected)
            assignments.append(
                {
                    "membership": rule,
                    "cycle": c,
                    "first_year": selected[0],
                    "last_year": selected[-1],
                    "n_years": len(selected),
                    "years": "|".join(map(str, selected)),
                }
            )
    readiness = {
        "status": "inputs_ready_no_C_results",
        "common_years": [years[0], years[-1]],
        "matched_year_count": len(years),
        "gn_file_years": [min(gn), max(gn)],
        "gn_rows": len(gn),
        "monthly_rows": len(monthly),
        "cycles": p["cycles"],
        "SC24_excluded": "GN ends before official SC24 end; no truncated-cycle substitution",
        "scientific_values_valid": True,
        "missing_monthly_sigma_in_common_years": sum(
            monthly[y * 12 + m]["sigma"] is None for y in years for m in range(12)
        ),
        "missing_GN_Eg_in_common_years": sum(gn[y]["Eg"] is None for y in years),
        "zero_GN_values_in_common_years": sum(gn[y]["value"] == 0 for y in years),
        "known_source_interpolation": p["known_source_interpolation"],
        "input_files": records,
        "no_GN_interpolation": True,
        "smoothed_and_A_files": "hash-checked provenance/context for C2; not operands of C1",
    }
    return monthly, gn, boundaries, years, assignments, readiness


def calculate(monthly, gn, boundaries, years, p):
    annual = [
        {
            "year": y,
            "silso_calendar_days": annualize(monthly, y, "calendar_days"),
            "silso_equal_months": annualize(monthly, y, "equal_months"),
            "gnbb2": gn[y]["value"],
            "gnbb2_Eg": gn[y]["Eg"],
            "valid_months": 12,
            "source_interpolated_month": "1824-02" if y == 1824 else "",
        }
        for y in years
    ]
    metrics, contrasts, agreements, troughs = [], [], [], []
    for weighting in p["weightings"]:
        products = {
            "SILSO": {r["year"]: r["silso_" + weighting] for r in annual},
            "GNbb2": {y: gn[y]["value"] for y in years},
        }
        for rule in p["memberships"]:
            scenario = weighting + "__" + rule
            comparison_signs = {}
            for dataset, data in products.items():
                slices = {
                    c: [
                        (y, data[y])
                        for y in members(years, boundaries[c], boundaries[c + 1], rule)
                    ]
                    for c in p["cycles"]
                }
                reference_mean = math.fsum(
                    math.fsum(v for _, v in slices[c]) / len(slices[c])
                    for c in p["references"]
                ) / len(p["references"])
                if reference_mean <= 0:
                    raise ValueError("nonpositive reference annual mean")
                group = {
                    c: summary(
                        slices[c], p["low_fraction_of_reference"] * reference_mean
                    )
                    for c in p["cycles"]
                }
                for c, row in group.items():
                    extra = {
                        metric + "_rank": 1
                        + sum(other[metric] > row[metric] for other in group.values())
                        for metric in METRICS[:3]
                    }
                    metrics.append(
                        {
                            "scenario": scenario,
                            "dataset": dataset,
                            "cycle": c,
                            "reference_cycle_mean": reference_mean,
                            "scaled_annual_mean": row["annual_mean"] / reference_mean,
                            **row,
                            **extra,
                        }
                    )
                comparisons = [(c, "reference_mean") for c in p["targets"]] + [
                    (5, "SC4"),
                    (6, "SC4"),
                ]
                for c, comparator in comparisons:
                    for metric in METRICS:
                        target = group[c][metric]
                        reference = (
                            group[4][metric]
                            if comparator == "SC4"
                            else math.fsum(group[k][metric] for k in p["references"])
                            / len(p["references"])
                        )
                        direction = sign(target, reference, p["tolerance"])
                        contrasts.append(
                            {
                                "scenario": scenario,
                                "dataset": dataset,
                                "cycle": c,
                                "comparator": comparator,
                                "metric": metric,
                                "target": target,
                                "reference": reference,
                                "difference_within_product": target - reference,
                                "ratio_within_product": target / reference
                                if reference
                                else "",
                                "ratio_status": "defined"
                                if reference
                                else "zero_denominator",
                                "direction": direction,
                            }
                        )
                        comparison_signs[dataset, c, comparator, metric] = direction
            for c, comparator in comparisons:
                for metric in METRICS:
                    a, b = [
                        comparison_signs[ds, c, comparator, metric]
                        for ds in ("SILSO", "GNbb2")
                    ]
                    status = (
                        "same_direction"
                        if a == b and a != "numerically_tied"
                        else "both_tied"
                        if a == b
                        else "discordant"
                    )
                    agreements.append(
                        {
                            "scenario": scenario,
                            "cycle": c,
                            "comparator": comparator,
                            "metric": metric,
                            "SILSO_direction": a,
                            "GNbb2_direction": b,
                            "status": status,
                        }
                    )
        for c in p["trough_boundaries"]:
            center = boundaries[c] // 12
            selected = list(
                range(
                    center - p["trough_half_width_years"],
                    center + p["trough_half_width_years"] + 1,
                )
            )
            for dataset, data in products.items():
                if not all(y in data for y in selected):
                    raise ValueError("incomplete trough window")
                minimum = min(data[y] for y in selected)
                ties = [y for y in selected if data[y] == minimum]
                troughs.append(
                    {
                        "weighting": weighting,
                        "dataset": dataset,
                        "boundary_cycle": c,
                        "window_start": selected[0],
                        "window_end": selected[-1],
                        "minimum": minimum,
                        "earliest_minimum_year": ties[0],
                        "all_minimum_years": "|".join(map(str, ties)),
                    }
                )
    return {
        "annual_matched.csv": annual,
        "cycle_annual_metrics.csv": metrics,
        "within_product_contrasts.csv": contrasts,
        "Q3_direction_agreement.csv": agreements,
        "trough_windows.csv": troughs,
    }


def run(inputs, parameters, output, *, check_only=False, execution_context=None):
    inputs, parameters, output = (
        Path(inputs).resolve(),
        Path(parameters).resolve(),
        Path(output).resolve(),
    )
    if (
        output == inputs
        or output.is_relative_to(inputs)
        or inputs.is_relative_to(output)
        or parameters.is_relative_to(output)
    ):
        raise ValueError("output must be separate from input and parameter locations")
    output.mkdir(parents=True, exist_ok=False)
    info = {
        "protocol": PROTOCOL,
        "executor": "operator_deterministic_script",
        "agent_run": False,
        "check_only": check_only,
        "review_status": "not_reviewed",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "dependencies": {
            "calculation": "Python standard library",
            "matplotlib": importlib.metadata.version("matplotlib"),
        },
        "seed": None,
        "command": sys.argv,
        "status": "started",
    }
    if execution_context:
        info.update(execution_context)
    try:
        parameter_hash = digest(parameters)
        input_manifest_hash = digest(inputs / "input_manifest.json")
        p = json.loads(parameters.read_text(encoding="utf-8-sig"))
        validate_parameters(p)
        monthly, gn, boundaries, years, assignments, readiness = load_inputs(inputs, p)
        info["parameter_sha256"] = parameter_hash
        save(output / "readiness.json", readiness)
        write_csv(output / "calendar_membership_plan.csv", assignments)
        if not check_only:
            tables = calculate(monthly, gn, boundaries, years, p)
            for name, rows in tables.items():
                write_csv(output / name, rows)
            save(
                output / "hypothesis_Q3.json",
                {
                    "scope": "exploratory_descriptive_only",
                    "status": "computed_not_scientifically_reviewed",
                    "primary_scenario": "calendar_days__whole_calendar_year",
                    "claims": tables["Q3_direction_agreement.csv"],
                    "limits": [
                        "shared historical observations",
                        "no observational error propagation or p values",
                        "whole-year subset is not exact month-boundary cycle integral",
                        "annual peaks differ from 13-month peaks",
                        "no complete SC24 comparison",
                        "direction agreement does not prove a physical mechanism",
                    ],
                },
            )
            lines = [
                "# C1 年尺度跨序列对照",
                "",
                "生成时科研审查：not_reviewed。原始单位只在各产品内部比较。",
                "主口径：日历日数加权月均、完全位于官方周期内的完整日历年。两个权重×两个分组口径均完整输出。",
                "SC1—23 为共同完整周期；年指标不等于 A 月度指标。Eg 保留但未作误差传播；不作显著性/物理因果结论。",
                "",
                "| 目标 | 对照 | 指标 | SILSO 方向 | GNbb2 方向 | 跨序列状态 |",
                "|---|---|---|---|---|---|",
            ]
            for row in tables["Q3_direction_agreement.csv"]:
                if row["scenario"] == "calendar_days__whole_calendar_year":
                    lines.append(
                        f"| SC{row['cycle']} | {row['comparator']} | {row['metric']} | {row['SILSO_direction']} | {row['GNbb2_direction']} | {row['status']} |"
                    )
            (output / "C1_report.md").write_text(
                "\n".join(lines) + "\n", encoding="utf-8"
            )
            if __package__:
                from .c_stage_figures import render_c1
            else:
                from c_stage_figures import render_c1
            render_c1(output, tables)
        shutil.copytree(inputs, output / "inputs")
        shutil.copy2(parameters, output / "C1_parameters.json")
        (output / "code").mkdir()
        shutil.copy2(__file__, output / "code/analyze_cross_series.py")
        shutil.copy2(
            Path(__file__).with_name("c_stage_figures.py"),
            output / "code/c_stage_figures.py",
        )
        if (
            digest(parameters) != parameter_hash
            or digest(inputs / "input_manifest.json") != input_manifest_hash
        ):
            raise ValueError("parameters or input manifest changed during execution")
        for rec in readiness["input_files"]:
            if digest(inputs / rec["path"]) != rec["sha256"]:
                raise ValueError("input changed during execution")
        info["status"] = (
            "inputs_ready_no_C_results"
            if check_only
            else "computed_not_scientifically_reviewed"
        )
        info["artifacts"] = [
            {
                "path": path.relative_to(output).as_posix(),
                "sha256": digest(path),
                "bytes": path.stat().st_size,
            }
            for path in sorted(output.rglob("*"))
            if path.is_file()
        ]
    except Exception as exc:
        info.update(status="failed", error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        info["finished_utc"] = datetime.now(timezone.utc).isoformat()
        save(output / "run_manifest.json", info)
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--parameters", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    run(args.inputs, args.parameters, args.output, check_only=args.check_only)


if __name__ == "__main__":
    main()
