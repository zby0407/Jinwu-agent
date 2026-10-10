"""Frozen single-factor C2 batches; real-input check-only does not run scenarios."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import shutil
import sys

import numpy as np

if __package__:
    from . import cycle_metrics as core, cycle_phase as phase
else:
    import cycle_metrics as core
    import cycle_phase as phase

PROTOCOL = "silso_cycle_sensitivity_v1"
INPUT_KEYS = phase.INPUT_KEYS | {"b_parameters", "b_targets"}
BASE = {
    "smoothing": "published_13_month",
    "low_monthly_threshold": 20,
    "boundary_shifts_months": {},
    "references": [1, 2, 7, 8, 9, 10],
}
BATCHES = {
    "C2a": {"baseline", "smoothing", "threshold"},
    "C2b": {"baseline", "boundary_global", "boundary_local"},
    "C2c": {"baseline", "references"},
}


def scenario_definitions():
    specs = [("baseline", "baseline", {})]
    specs += [
        (
            f"smooth_boxcar_{w}",
            "smoothing",
            {"smoothing": f"centered_equal_weight_{w}_months"},
        )
        for w in (9, 25)
    ]
    specs += [
        (
            f"boundary_all_{d:+d}",
            "boundary_global",
            {"boundary_shifts_months": {str(c): d for c in range(1, 26)}},
        )
        for d in (-6, 6)
    ]
    specs += [
        (
            f"boundary_SC{c}_{d:+d}",
            "boundary_local",
            {"boundary_shifts_months": {str(c): d}},
        )
        for c in range(3, 8)
        for d in (-6, 6)
    ]
    specs += [
        (
            f"reference_omit_SC{c}",
            "references",
            {"references": [k for k in BASE["references"] if k != c]},
        )
        for c in BASE["references"]
    ]
    specs += [
        ("reference_expand_SC14", "references", {"references": [1, 2, *range(7, 15)]})
    ]
    specs += [
        (f"threshold_{t}", "threshold", {"low_monthly_threshold": t}) for t in (10, 30)
    ]
    return specs


def validate_parameters(p):
    if (
        set(p) != {"schema", "protocol", "batch", "plan"}
        or p["schema"] != "cycle-sensitivity-execution-v1"
        or p["protocol"] != PROTOCOL
        or p["batch"] not in BATCHES
    ):
        raise ValueError("invalid bounded C2 execution parameters")
    plan = p["plan"]
    for key, expected in {
        "schema": "C2-sensitivity-plan-v1",
        "baseline": BASE,
        "cycles": list(range(1, 25)),
        "targets": [3, 4, 5, 6],
        "one_factor_at_a_time": True,
        "interactions_tested": False,
        "phase_grid_intervals": 100,
        "normalization": "scenario_specific_within_cycle_peak_no_baseline_subtraction",
        "published_baseline_is_primary": True,
        "decision_tolerance": {"absolute": 1e-12, "relative": 1e-12},
    }.items():
        if plan.get(key) != expected:
            raise ValueError("C2 scientific policy changed: " + key)
    expected = []
    for name, family, changes in scenario_definitions():
        row = {"id": name, "family": family, "changes": changes}
        if family == "threshold":
            row["existing_A_result"] = True
        row["effective_parameters"] = {**BASE, **changes}
        expected.append(row)
    if plan.get("scenarios") != expected:
        raise ValueError("C2 scenario selection or effective parameters changed")
    return [row for row in expected if row["family"] in BATCHES[p["batch"]]]


def table(path):
    with Path(path).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def load_inputs(inputs, p):
    inputs = Path(inputs).resolve()
    records = json.loads(
        (inputs / "input_manifest.json").read_text(encoding="utf-8-sig")
    )["files"]
    if len(records) != len(INPUT_KEYS) or {r["key"] for r in records} != INPUT_KEYS:
        raise ValueError("C2 requires exactly seven scientific/baseline inputs")
    paths = {}
    for rec in records:
        path = (inputs / rec["path"]).resolve()
        if (
            not path.is_relative_to(inputs)
            or not path.is_file()
            or core.digest(path) != rec["sha256"]
            or path.stat().st_size != rec["bytes"]
        ):
            raise ValueError("invalid C2 bound input: " + rec["key"])
        paths[rec["key"]] = path
    monthly, smooth = (
        core.load_monthly(paths["monthly"]),
        core.load_monthly(paths["smoothed"], ";"),
    )
    bounds = core.load_boundaries(paths["boundaries"])
    ap = json.loads(paths["a_parameters"].read_text(encoding="utf-8-sig"))
    bp = json.loads(paths["b_parameters"].read_text(encoding="utf-8-sig"))
    core.validate_parameters(ap)
    phase.validate_parameters(bp)
    if (
        ap["cycles"] != list(range(1, 25))
        or bp["cycles"] != ap["cycles"]
        or bp["targets"] != [3, 4, 5, 6]
        or bp["references"] != BASE["references"]
        or bp["grid_intervals"] != 100
    ):
        raise ValueError("A/B scope inconsistent with C2")
    recalculated = core.calculate(
        monthly, smooth, bounds, ap["cycles"], ap["thresholds"]
    )
    if [
        {k: "" if v is None else str(v) for k, v in row.items()} for row in recalculated
    ] != table(paths["a_metrics"]):
        raise ValueError("A baseline does not equal raw-input recomputation")
    selected = validate_parameters(p)
    coverage = []
    for scenario in selected:
        ep = scenario["effective_parameters"]
        shifted = {
            c: row["start"] + ep["boundary_shifts_months"].get(str(c), 0)
            for c, row in bounds.items()
        }
        if list(shifted) != list(range(1, 26)) or list(shifted.values()) != sorted(
            set(shifted.values())
        ):
            raise ValueError("invalid shifted boundaries")
        start, end = shifted[1], shifted[25]
        halo = (
            0
            if ep["smoothing"] == "published_13_month"
            else int(ep["smoothing"].split("_")[-2]) // 2
        )
        for i in range(start - halo, end + halo + 1):
            if i not in monthly or monthly[i]["value"] is None:
                raise ValueError("missing monthly value or smoothing halo")
        if any(
            i not in smooth or smooth[i]["value"] is None for i in range(start, end + 1)
        ):
            raise ValueError("missing published smooth value or curve endpoint")
        coverage.append(
            {
                "scenario": scenario["id"],
                "start": core.month_label(start),
                "end_endpoint": core.month_label(end),
                "halo": halo,
                "covered": True,
            }
        )
    # The diagnostic also requires six monthly neighbors; check before any outcome.
    if any(
        i not in monthly or monthly[i]["value"] is None
        for i in range(bounds[1]["start"] - 6, bounds[25]["start"] + 7)
    ):
        raise ValueError("missing diagnostic 13-month halo")
    readiness = {
        "status": "inputs_ready_no_C2_results",
        "batch": p["batch"],
        "scenarios": coverage,
        "a_baseline_recomputed_and_equal": True,
        "B_baseline_numeric_check": "on_formal_execution_only",
        "input_files": records,
        "source_interpolation": ["1824-02"],
    }
    return (
        monthly,
        smooth,
        bounds,
        recalculated,
        bp,
        table(paths["b_targets"]),
        readiness,
    )


def centered(monthly, start, end, width, *, silso_weights=False):
    if width not in (9, 13, 25) or silso_weights and width != 13:
        raise ValueError("unsupported smoother")
    half = width // 2
    result = {}
    for i in range(start, end + 1):
        indices = list(range(i - half, i + half + 1))
        if any(k not in monthly or monthly[k]["value"] is None for k in indices):
            raise ValueError("no gap filling or edge padding")
        values = [monthly[k]["value"] for k in indices]
        value = (
            (values[0] + values[-1]) / 24 + math.fsum(values[1:-1]) / 12
            if silso_weights
            else math.fsum(values) / width
        )
        result[i] = {"value": value}
    return result


def cycle_rows(monthly, smooth, starts, threshold):
    result = []
    for c in range(1, 25):
        start, end = starts[c], starts[c + 1]
        indices = list(range(start, end))
        if not indices or any(
            i not in monthly
            or monthly[i]["value"] is None
            or i not in smooth
            or smooth[i]["value"] is None
            for i in indices
        ):
            raise ValueError("incomplete sensitivity cycle")
        peak = max(smooth[i]["value"] for i in indices)
        if not math.isfinite(peak) or peak <= 0:
            raise ValueError("zero or invalid peak cannot normalize")
        ties = [i for i in indices if smooth[i]["value"] == peak]
        peak_month = ties[0]
        values = [monthly[i]["value"] for i in indices]
        total = math.fsum(values)
        rise, decline = peak_month - start, end - peak_month
        result.append(
            {
                "cycle": c,
                "minimum_date": core.month_label(start),
                "next_minimum_date_exclusive": core.month_label(end),
                "months": end - start,
                "monthly_mean": total / (end - start),
                "activity_sn_month": total,
                "smoothed_peak": peak,
                "peak_date": core.month_label(peak_month),
                "peak_ties": "|".join(map(core.month_label, ties)),
                "rise_months": rise,
                "decline_months": decline,
                "peak_phase": rise / (end - start),
                "decline_rise_ratio": decline / rise if rise else None,
                "low_threshold": threshold,
                "low_fraction": sum(v < threshold for v in values) / len(values),
                "longest_low_months": core.longest_low(values, threshold),
            }
        )
    for key in ("smoothed_peak", "monthly_mean", "activity_sn_month"):
        for row in result:
            row[key + "_rank_desc"] = 1 + sum(other[key] > row[key] for other in result)
    return result


def calculate(monthly, published, bounds, baseline_a, bp, baseline_b, p):
    selected = validate_parameters(p)
    metrics, curves_table, target_rows, backgrounds, q1, deltas = [], [], [], [], [], []
    grid = np.linspace(0, 1, 101)
    base_targets = {}
    base_metrics = {}
    for scenario in selected:
        name, ep = scenario["id"], scenario["effective_parameters"]
        starts = {
            c: row["start"] + ep["boundary_shifts_months"].get(str(c), 0)
            for c, row in bounds.items()
        }
        smooth = (
            published
            if ep["smoothing"] == "published_13_month"
            else centered(
                monthly, starts[1], starts[25], int(ep["smoothing"].split("_")[-2])
            )
        )
        rows = cycle_rows(monthly, smooth, starts, ep["low_monthly_threshold"])
        curves = {
            r["cycle"]: phase.align_curve(
                smooth,
                starts[r["cycle"]],
                starts[r["cycle"] + 1],
                r["smoothed_peak"],
                grid,
            )
            for r in rows
        }
        sp = {**bp, "references": ep["references"]}
        _, background, targets, _ = phase.compare(curves, sp)
        if name == "baseline":
            base_metrics = {row["cycle"]: row for row in rows}
            base_targets = {row["cycle"]: row for row in targets}
            for original, row in zip(baseline_a, rows):
                for key in (
                    "cycle",
                    "months",
                    "monthly_mean",
                    "activity_sn_month",
                    "smoothed_peak",
                    "peak_date",
                    "rise_months",
                    "decline_months",
                    "peak_phase",
                ):
                    if original[key] != row[key]:
                        raise ValueError("C2 baseline failed A replication: " + key)
                t = f"{ep['low_monthly_threshold']:g}"
                if (
                    original[f"low_lt_{t}_fraction"] != row["low_fraction"]
                    or original[f"low_lt_{t}_longest_months"]
                    != row["longest_low_months"]
                ):
                    raise ValueError("C2 baseline low-activity mismatch")
            expected = [{k: str(v) for k, v in row.items()} for row in targets]
            if expected != baseline_b:
                raise ValueError("C2 official baseline failed frozen B replication")
        for row in rows:
            metrics.append({"scenario": name, "family": scenario["family"], **row})
            c = row["cycle"]
            for j, x in enumerate(grid):
                curves_table.append(
                    {
                        "scenario": name,
                        "cycle": c,
                        "phase": float(x),
                        "absolute_smoothed_sn": float(curves[c][0][j]),
                        "peak_normalized": float(curves[c][1][j]),
                    }
                )
            for key in (
                "months",
                "monthly_mean",
                "activity_sn_month",
                "smoothed_peak",
                "rise_months",
                "decline_months",
                "peak_phase",
                "low_fraction",
                "longest_low_months",
            ):
                deltas.append(
                    {
                        "scenario": name,
                        "cycle": c,
                        "metric": key,
                        "value": row[key],
                        "baseline": base_metrics[c][key],
                        "difference": row[key] - base_metrics[c][key],
                    }
                )
        target_rows += [
            {
                "scenario": name,
                "family": scenario["family"],
                **row,
                "baseline_status": base_targets[row["cycle"]]["status"],
                "status_changed": row["status"] != base_targets[row["cycle"]]["status"],
            }
            for row in targets
        ]
        backgrounds += [{"scenario": name, **row} for row in background]
        by_cycle = {row["cycle"]: row for row in rows}
        groups = [
            (c, "reference_mean", ep["references"]) for c in p["plan"]["targets"]
        ] + [(5, "SC4", [4]), (6, "SC4", [4])]
        for c, label, refs in groups:
            for metric in (
                "smoothed_peak",
                "monthly_mean",
                "activity_sn_month",
                "low_fraction",
                "longest_low_months",
            ):
                value, reference = (
                    by_cycle[c][metric],
                    math.fsum(by_cycle[k][metric] for k in refs) / len(refs),
                )
                diff = value - reference
                direction = "higher" if diff > 0 else "lower" if diff < 0 else "equal"
                desired = (
                    "higher"
                    if metric.startswith("low_") or metric == "longest_low_months"
                    else "lower"
                )
                q1.append(
                    {
                        "scenario": name,
                        "cycle": c,
                        "reference": label,
                        "metric": metric,
                        "target": value,
                        "reference_mean": reference,
                        "difference": diff,
                        "ratio": value / reference if reference else None,
                        "direction": direction,
                        "status": "exploratory_context"
                        if c in (3, 4)
                        else "direction_consistent_descriptively"
                        if direction == desired
                        else "direction_not_supported",
                    }
                )
    reconstructed = centered(
        monthly, bounds[1]["start"], bounds[25]["start"], 13, silso_weights=True
    )
    diagnostic = [
        {
            "month": core.month_label(i),
            "published": published[i]["value"],
            "reconstructed_13_month": row["value"],
            "difference": row["value"] - published[i]["value"],
        }
        for i, row in reconstructed.items()
    ]
    return {
        "scenario_metrics.csv": metrics,
        "scenario_phase_curves.csv": curves_table,
        "scenario_targets.csv": target_rows,
        "scenario_background.csv": backgrounds,
        "scenario_Q1.csv": q1,
        "scenario_deltas.csv": deltas,
        "smoothing_diagnostic.csv": diagnostic,
    }


def run(inputs, parameters, output, *, check_only=False, execution_context=None):
    inputs, parameters, output = [
        Path(x).resolve() for x in (inputs, parameters, output)
    ]
    if (
        output.exists()
        or output.is_relative_to(inputs)
        or inputs.is_relative_to(output)
        or parameters.is_relative_to(output)
    ):
        raise ValueError("new separate output directory required")
    output.mkdir(parents=True)
    info = {
        "protocol": PROTOCOL,
        "executor": "operator_deterministic_script",
        "agent_run": False,
        "review_status": "not_reviewed",
        "check_only": check_only,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "command": sys.argv,
        "seed": None,
        "dependencies": {
            k: importlib.metadata.version(k) for k in ("numpy", "matplotlib")
        },
    }
    if execution_context:
        info.update(execution_context)
    try:
        phash, ihash = (
            core.digest(parameters),
            core.digest(inputs / "input_manifest.json"),
        )
        p = json.loads(parameters.read_text(encoding="utf-8-sig"))
        validate_parameters(p)
        monthly, smooth, bounds, a, bp, b, ready = load_inputs(inputs, p)
        info.update(parameter_sha256=phash, batch=p["batch"])
        core.save_json(output / "readiness.json", ready)
        if not check_only:
            tables = calculate(monthly, smooth, bounds, a, bp, b, p)
            for name, rows in tables.items():
                core.write_csv(output / name, rows)
            core.save_json(
                output / "hypotheses_C2.json",
                {
                    "scope": "exploratory_sensitivity_only",
                    "Q2": tables["scenario_targets.csv"],
                    "Q1": tables["scenario_Q1.csv"],
                    "limits": [
                        "not observational error intervals",
                        "no p values or cause identification",
                        "single-factor scenarios omit interactions",
                        "lost-cycle partition not tested",
                    ],
                },
            )
            lines = [
                "# C2 单因素敏感性",
                "",
                f"批次 {p['batch']}；生成时科研审查 not_reviewed。官方基线为主分析，其他情景不替代主结果。",
                "原有 A/B 基线从原始输入重算并匹配。边界±6月是压力检查，不是观测误差区间。没有因果或显著性结论。",
                "|情景|目标|RMS|背景最大|判定|相对基线改变|",
                "|---|---|---:|---:|---|---|",
            ]
            for row in tables["scenario_targets.csv"]:
                lines.append(
                    f"|{row['scenario']}|SC{row['cycle']}|{row['normalized_rms']:.8g}|{row['background_max_rms']:.8g}|{row['status']}|{row['status_changed']}|"
                )
            (output / "C2_report.md").write_text(
                "\n".join(lines) + "\n", encoding="utf-8"
            )
            if __package__:
                from .c_stage_figures import render_c2
            else:
                from c_stage_figures import render_c2
            render_c2(output, tables)
        shutil.copytree(inputs, output / "inputs")
        shutil.copy2(parameters, output / "C2_parameters.json")
        (output / "code").mkdir()
        for path, name in [
            (Path(__file__), "analyze_cycle_sensitivity.py"),
            (Path(core.__file__), "cycle_metrics.py"),
            (Path(phase.__file__), "cycle_phase.py"),
            (Path(__file__).with_name("c_stage_figures.py"), "c_stage_figures.py"),
        ]:
            shutil.copy2(path, output / "code" / name)
        if (
            core.digest(parameters) != phash
            or core.digest(inputs / "input_manifest.json") != ihash
            or any(
                core.digest(inputs / r["path"]) != r["sha256"]
                for r in ready["input_files"]
            )
        ):
            raise ValueError("input changed during computation")
        info["status"] = (
            "inputs_ready_no_C2_results"
            if check_only
            else "computed_not_scientifically_reviewed"
        )
        info["artifacts"] = [
            {
                "path": path.relative_to(output).as_posix(),
                "sha256": core.digest(path),
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
        core.save_json(output / "run_manifest.json", info)
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("inputs", "parameters", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    run(args.inputs, args.parameters, args.output, check_only=args.check_only)


if __name__ == "__main__":
    main()
