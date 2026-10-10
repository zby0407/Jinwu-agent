"""Auditable B-stage phase comparison; --check-only never computes B outcomes."""

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
    from . import cycle_metrics as core
else:
    import cycle_metrics as core

PROTOCOL = "silso_cycle_phase_v1"
INPUT_KEYS = {"monthly", "smoothed", "boundaries", "a_metrics", "a_parameters"}


def validate_parameters(p):
    expected = {
        "schema",
        "cycles",
        "targets",
        "references",
        "plot_cycles",
        "grid_intervals",
        "interpolation",
        "endpoint",
        "normalization",
        "reference_summary",
        "distance",
        "background",
        "decision",
        "missing_policy",
        "comparison_tolerance",
    }
    if set(p) != expected or p["schema"] != "cycle-phase-parameters-v1":
        raise ValueError("unsupported or incomplete phase parameters")
    cycles = p["cycles"]
    if (
        not cycles
        or any(type(c) is not int or c < 1 for c in cycles)
        or cycles != sorted(set(cycles))
    ):
        raise ValueError("cycles must be ordered unique positive integers")
    for key in ("targets", "references", "plot_cycles"):
        values = p[key]
        if (
            not values
            or len(set(values)) != len(values)
            or not set(values) <= set(cycles)
        ):
            raise ValueError("invalid cycle selection")
    if len(p["references"]) < 2 or set(p["targets"]) & set(p["references"]):
        raise ValueError("at least two references, disjoint from targets, required")
    if type(p["grid_intervals"]) is not int or not 2 <= p["grid_intervals"] <= 10000:
        raise ValueError("invalid grid size")
    if p["comparison_tolerance"] != {"absolute": 1e-12, "relative": 1e-12}:
        raise ValueError("unsupported numerical comparison tolerance")
    policies = {
        "interpolation": "piecewise_linear",
        "endpoint": "next_minimum_smoothed",
        "normalization": "A_within_cycle_peak_no_baseline_subtraction",
        "reference_summary": "equal_cycle_mean",
        "distance": "equal_grid_rms_inclusive_endpoints",
        "background": "reference_leave_one_out",
        "decision": "strictly_above_all_background",
        "missing_policy": "fail_no_gap_interpolation_no_extrapolation",
    }
    if any(p[k] != v for k, v in policies.items()):
        raise ValueError("unsupported scientific policy")


def inspect_inputs(inputs, parameters):
    inputs = Path(inputs).resolve()
    p = json.loads(Path(parameters).read_text(encoding="utf-8-sig"))
    validate_parameters(p)
    manifest = json.loads(
        (inputs / "input_manifest.json").read_text(encoding="utf-8-sig")
    )
    records = manifest["files"]
    if len(records) != len(INPUT_KEYS) or {r["key"] for r in records} != INPUT_KEYS:
        raise ValueError("five distinct phase inputs are required")
    paths = {}
    for rec in records:
        path = (inputs / rec["path"]).resolve()
        if not path.is_relative_to(inputs) or not path.is_file():
            raise ValueError("input missing or escapes root")
        if core.digest(path) != rec["sha256"] or path.stat().st_size != rec["bytes"]:
            raise ValueError(f"input identity mismatch: {rec['key']}")
        paths[rec["key"]] = path
    monthly = core.load_monthly(paths["monthly"])
    smoothed = core.load_monthly(paths["smoothed"], ";")
    boundaries = core.load_boundaries(paths["boundaries"])
    a_params = json.loads(paths["a_parameters"].read_text(encoding="utf-8-sig"))
    core.validate_parameters(a_params)
    recalculated = core.calculate(
        monthly, smoothed, boundaries, a_params["cycles"], a_params["thresholds"]
    )
    with paths["a_metrics"].open(encoding="utf-8", newline="") as stream:
        supplied = list(csv.DictReader(stream))
    canonical = [
        {k: "" if v is None else str(v) for k, v in row.items()} for row in recalculated
    ]
    if canonical != supplied:
        raise ValueError(
            "A baseline differs from deterministic recalculation of bound raw inputs"
        )
    by_cycle = {row["cycle"]: row for row in recalculated}
    if not set(p["cycles"]) <= by_cycle.keys():
        raise ValueError("B cycle missing from A baseline")
    endpoints = []
    for cycle in p["cycles"]:
        end = boundaries[cycle + 1]["start"]
        if end not in smoothed or smoothed[end]["value"] is None:
            raise ValueError(f"missing next-minimum endpoint for cycle {cycle}")
        if by_cycle[cycle]["smoothed_peak"] <= 0:
            raise ValueError(f"zero peak prevents normalization for cycle {cycle}")
        endpoints.append(
            {"cycle": cycle, "endpoint_date": core.month_label(end), "available": True}
        )
    readiness = {
        "status": "inputs_ready_no_B_results",
        "input_files": records,
        "a_baseline_recomputed_and_equal": True,
        "cycles": p["cycles"],
        "grid_points_per_cycle": p["grid_intervals"] + 1,
        "endpoints": endpoints,
        "monthly_quality": core.quality(monthly),
        "smoothed_quality": core.quality(smoothed),
    }
    return p, smoothed, boundaries, by_cycle, readiness


def align_curve(smoothed, start, end, peak, grid):
    if end <= start or not math.isfinite(peak) or peak <= 0:
        raise ValueError("invalid cycle length or peak")
    grid = np.asarray(grid, dtype=float)
    if (
        grid.ndim != 1
        or not len(grid)
        or not np.isfinite(grid).all()
        or np.any(np.diff(grid) <= 0)
    ):
        raise ValueError("grid must be finite and strictly increasing")
    if grid[0] < 0 or grid[-1] > 1:
        raise ValueError("extrapolation is forbidden")
    indices = list(range(start, end + 1))
    if any(i not in smoothed or smoothed[i]["value"] is None for i in indices):
        raise ValueError("missing month/endpoint; gap interpolation is forbidden")
    values = np.asarray([smoothed[i]["value"] for i in indices], dtype=float)
    if not np.isfinite(values).all() or np.any(values < 0):
        raise ValueError("invalid smoothed value")
    x = np.asarray([(i - start) / (end - start) for i in indices])
    absolute = np.interp(grid, x, values)
    return absolute, absolute / peak


def rms(a, b):
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if (
        a.shape != b.shape
        or a.ndim != 1
        or not len(a)
        or not np.isfinite(a).all()
        or not np.isfinite(b).all()
    ):
        raise ValueError("incompatible or invalid distance vectors")
    return float(np.sqrt(np.mean((a - b) ** 2)))


def compare(curves, p):
    references = p["references"]
    mean = np.mean([curves[c][1] for c in references], axis=0)
    background = []
    for c in references:
        others = [k for k in references if k != c]
        other_mean = np.mean([curves[k][1] for k in others], axis=0)
        background.append(
            {
                "cycle": c,
                "reference_cycles": "|".join(map(str, others)),
                "normalized_rms": rms(curves[c][1], other_mean),
            }
        )
    maximum = max(row["normalized_rms"] for row in background)
    targets = []
    for c in p["targets"]:
        distance = rms(curves[c][1], mean)
        tolerance = p["comparison_tolerance"]["absolute"] + p["comparison_tolerance"][
            "relative"
        ] * abs(maximum)
        targets.append(
            {
                "cycle": c,
                "normalized_rms": distance,
                "background_max_rms": maximum,
                "margin_above_background": distance - maximum,
                "comparison_tolerance": tolerance,
                "status": "outside_reference_background"
                if distance - maximum > tolerance
                else "not_outside_reference_background",
            }
        )
    pairs = []
    for i, c in enumerate(p["cycles"]):
        for other in p["cycles"][i + 1 :]:
            pairs.append(
                {
                    "cycle_a": c,
                    "cycle_b": other,
                    "absolute_rms_sn": rms(curves[c][0], curves[other][0]),
                    "normalized_rms": rms(curves[c][1], curves[other][1]),
                }
            )
    return mean, background, targets, pairs


def render_figures(output, grid, curves, mean, p):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    for index, name, label in [
        (0, "phase_absolute.png", "Smoothed sunspot number"),
        (1, "phase_normalized.png", "Smoothed SN / within-cycle peak"),
    ]:
        fig, ax = plt.subplots(figsize=(10, 5), layout="constrained")
        for cycle in p["plot_cycles"]:
            ax.plot(
                grid,
                curves[cycle][index],
                label=f"SC{cycle}",
                linewidth=2 if cycle in p["targets"] else 1,
                alpha=1 if cycle in p["targets"] else 0.5,
            )
        if index == 1:
            ax.plot(
                grid,
                mean,
                color="black",
                linestyle="--",
                label="Reference mean",
                linewidth=2,
            )
        ax.set(xlabel="Cycle phase (next minimum is an endpoint only)", ylabel=label)
        ax.legend(ncol=4, fontsize=8)
        ax.grid(alpha=0.2)
        fig.savefig(
            output / name,
            dpi=160,
            metadata={"Software": "JW deterministic phase comparison"},
        )
        plt.close(fig)


def run(inputs, parameters, output, *, check_only=False, execution_context=None):
    inputs, parameters, output = map(
        lambda x: Path(x).resolve(), (inputs, parameters, output)
    )
    if (
        output.exists()
        or output.is_relative_to(inputs)
        or inputs.is_relative_to(output)
        or parameters.is_relative_to(output)
    ):
        raise ValueError("output must be a new directory separate from inputs")
    output.mkdir(parents=True)
    run_info = {
        "schema": "cycle-phase-run-v1",
        "protocol": PROTOCOL,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "executor": "operator_deterministic_script",
        "agent_run": False,
        "check_only": check_only,
        "review_status": "not_reviewed",
        "python": sys.version,
        "platform": platform.platform(),
        "command": sys.argv,
        "seed": None,
        "dependencies": {
            k: importlib.metadata.version(k) for k in ("numpy", "matplotlib")
        },
    }
    if execution_context:
        run_info.update(execution_context)
    try:
        param_hash = core.digest(parameters)
        p, smooth, bounds, baseline, readiness = inspect_inputs(inputs, parameters)
        run_info["parameter_sha256"] = param_hash
        run_info["input_files"] = readiness["input_files"]
        core.save_json(output / "readiness.json", readiness)
        if not check_only:
            grid = np.linspace(0, 1, p["grid_intervals"] + 1)
            curves = {
                c: align_curve(
                    smooth,
                    bounds[c]["start"],
                    bounds[c + 1]["start"],
                    baseline[c]["smoothed_peak"],
                    grid,
                )
                for c in p["cycles"]
            }
            mean, background, targets, pairs = compare(curves, p)
            core.write_csv(
                output / "phase_curves.csv",
                [
                    {
                        "cycle": c,
                        "phase": float(phi),
                        "absolute_smoothed_sn": float(curves[c][0][i]),
                        "peak_normalized": float(curves[c][1][i]),
                    }
                    for c in p["cycles"]
                    for i, phi in enumerate(grid)
                ],
            )
            core.write_csv(
                output / "reference_curve.csv",
                [
                    {"phase": float(phi), "normalized_mean": float(mean[i])}
                    for i, phi in enumerate(grid)
                ],
            )
            core.write_csv(output / "background_loo.csv", background)
            core.write_csv(output / "target_comparisons.csv", targets)
            core.write_csv(output / "pairwise_distances.csv", pairs)
            shape = []
            for c in p["cycles"]:
                row = baseline[c]
                shape.append(
                    {
                        "cycle": c,
                        "peak_phase": row["peak_phase"],
                        "A_peak": row["smoothed_peak"],
                        "A_peak_date": row["peak_date"],
                        "rise_months": row["rise_months"],
                        "decline_months": row["decline_months"],
                        "decline_rise_ratio": row["decline_months"] / row["rise_months"]
                        if row["rise_months"]
                        else None,
                        "ratio_status": "defined"
                        if row["rise_months"]
                        else "zero_rise_undefined",
                        "grid_normalized_max": float(max(curves[c][1])),
                        "endpoint_above_A_peak": bool(curves[c][1][-1] > 1),
                    }
                )
            core.write_csv(output / "cycle_shape_metrics.csv", shape)
            core.save_json(
                output / "hypothesis_Q2.json",
                {
                    "scope": "exploratory_descriptive_only",
                    "results": targets,
                    "falsification": "target distance does not exceed all reference leave-one-out distances",
                    "limitations": [
                        "small reference group",
                        "target centroid has R members; LOO centroid R-1",
                        "shared smoothed source",
                        "no p value or causal identification",
                        "sensitivity untested",
                    ],
                },
            )
            report = [
                "# B 阶段：相位与幅度归一化",
                "",
                "生成时科研审查：not_reviewed。执行身份见 run_manifest.json。",
                "相位 0—1，线性插值只用于比较，不创造观测；下一极小月只作为曲线端点，不改变 A 的累计量或峰值。",
                "保幅与峰归一化曲线分别保存；不减极小值，不按网格最大值二次归一化。峰值相位取 A 的实际峰月。",
                "形态距离是包含两个端点、各网格点等权的 RMS；参考曲线为参照周期等权均值。",
                "目标与全部参照的均值比较，背景为逐个参照到其余参照均值；两者均值所含周期数不同，不能当作同分布检验或 p 值。",
                "",
                "严格超过背景的数值实现要求差值大于 1e-12 + 1e-12×背景最大距离；这只防止浮点舍入误判，不是观测误差、统计区间或科学效应阈值。",
                "| 周期 | 归一化 RMS | 背景最大 RMS | 探索性判定 |",
                "|---|---:|---:|---|",
            ]
            for row in targets:
                label = (
                    "超出该参照组背景范围"
                    if row["status"] == "outside_reference_background"
                    else "当前参照下不支持异常形态"
                )
                report.append(
                    f"| SC{row['cycle']} | {row['normalized_rms']:.8g} | {row['background_max_rms']:.8g} | {label} |"
                )
            report += [
                "",
                "结论只对应冻结参照和口径，未做 C 敏感性或 D 关联研究。归一化丢失幅度，不能据此否认低活动。",
                "没有显著性、独立月份重采样或物理因果结论；周期编号与边界采用官方口径，丢失周期分期尚未检验。",
            ]
            (output / "B_phase_report.md").write_text(
                "\n".join(report) + "\n", encoding="utf-8"
            )
            render_figures(output, grid, curves, mean, p)
        shutil.copytree(inputs, output / "inputs")
        shutil.copy2(parameters, output / "phase_parameters.json")
        (output / "code").mkdir()
        shutil.copy2(__file__, output / "code/analyze_cycle_phase.py")
        shutil.copy2(core.__file__, output / "code/cycle_metrics.py")
        for lock in (
            Path(__file__).parent / "uv.lock",
            Path(__file__).parent.parent / "uv.lock",
        ):
            if lock.is_file():
                shutil.copy2(lock, output / "code/uv.lock")
                break
        if core.digest(parameters) != param_hash or any(
            core.digest(inputs / rec["path"]) != rec["sha256"]
            for rec in readiness["input_files"]
        ):
            raise ValueError("input changed during execution")
        run_info["status"] = (
            "inputs_ready_no_B_results"
            if check_only
            else "computed_not_scientifically_reviewed"
        )
        run_info["artifacts"] = [
            {
                "path": path.relative_to(output).as_posix(),
                "sha256": core.digest(path),
                "bytes": path.stat().st_size,
            }
            for path in sorted(output.rglob("*"))
            if path.is_file()
        ]
    except Exception as exc:
        run_info.update(status="failed", error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        run_info["finished_utc"] = datetime.now(timezone.utc).isoformat()
        core.save_json(output / "run_manifest.json", run_info)
    return run_info


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
