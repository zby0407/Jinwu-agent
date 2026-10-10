"""Bounded retrospective D associations. No forecast, p-value or causal verdict."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import platform
import shutil
import sys

if __package__:
    from . import cycle_metrics as core, cycle_sensitivity as sensitivity
else:
    import cycle_metrics as core
    import cycle_sensitivity as sensitivity

PROTOCOL = "silso_cycle_associations_v1"
INPUT_KEYS = {
    "monthly",
    "smoothed",
    "boundaries",
    "a_metrics",
    "a_parameters",
    "c2a_metrics",
    "c2b_metrics",
    "c2a_parameters",
    "c2b_parameters",
}
SCENARIOS = [
    name
    for name, family, _ in sensitivity.scenario_definitions()
    if family in {"baseline", "smoothing", "boundary_global", "boundary_local"}
]
SPECS = {
    "D1": [
        (
            "Q4_previous_length_next_peak",
            "months",
            "smoothed_peak",
            1,
            "primary_exploratory",
        ),
        (
            "previous_length_next_mean",
            "months",
            "monthly_mean",
            1,
            "secondary_exploratory",
        ),
        (
            "same_length_peak_control",
            "months",
            "smoothed_peak",
            0,
            "same_cycle_not_prediction",
        ),
    ],
    "D2": [
        (
            "WE1_rise_peak",
            "rise_months",
            "smoothed_peak",
            0,
            "paper_inspired_exploratory",
        ),
        (
            "epsilon_peak",
            "rise_decline_ratio",
            "smoothed_peak",
            0,
            "paper_inspired_exploratory",
        ),
        (
            "same_length_peak",
            "months",
            "smoothed_peak",
            0,
            "paper_inspired_exploratory",
        ),
        (
            "ratio_rate_peak",
            "peak_over_rise",
            "smoothed_peak",
            0,
            "mathematically_coupled_diagnostic",
        ),
        (
            "fixed_month_12_24_slope_peak",
            "fixed_12_24_slope",
            "smoothed_peak",
            0,
            "fixed_window_retrospective_proxy",
        ),
    ],
}


def parameters(batch):
    return {
        "schema": "cycle-association-execution-v1",
        "protocol": PROTOCOL,
        "batch": batch,
        "cycles": list(range(1, 25)),
        "targets": [3, 4, 5, 6],
        "scenario_ids": SCENARIOS,
        "late_period_first_y_cycle": 13,
        "minimum_pairs": 4,
        "direction_tolerance": 1e-12,
        "early_slope_month_offsets": [12, 24],
        "spearman_ties": "average_ranks",
        "leave_out": ["pair", "physical_cycle_both_sides"],
        "p_values": False,
        "confidence_intervals": False,
        "forecast": False,
        "coupling_diagnostic": "all_23_nonzero_circular_shifts_of_rise_time_no_null_p_value",
        "study_status": "exploratory_after_ABC_and_paper_not_independent_preregistration",
    }


def validate_parameters(p):
    if p.get("batch") not in SPECS or p != parameters(p["batch"]):
        raise ValueError("D parameters, sample or analysis policy changed")


def ranks(values):
    ordered = sorted(set(values))
    result = {}
    position = 1
    for value in ordered:
        count = values.count(value)
        result[value] = position + (count - 1) / 2
        position += count
    return [result[value] for value in values]


def effect(xs, ys, minimum=4):
    if len(xs) != len(ys) or any(not math.isfinite(v) for v in [*xs, *ys]):
        raise ValueError("invalid paired numeric sample")
    n = len(xs)
    result = {
        "n": n,
        "pearson_r": None,
        "spearman_rho": None,
        "ols_slope": None,
        "ols_intercept": None,
        "status": "insufficient_pairs",
    }
    if n < minimum:
        return result
    xm, ym = math.fsum(xs) / n, math.fsum(ys) / n
    dx, dy = [v - xm for v in xs], [v - ym for v in ys]
    xx, yy, xy = (
        math.fsum(v * v for v in dx),
        math.fsum(v * v for v in dy),
        math.fsum(a * b for a, b in zip(dx, dy)),
    )
    if xx == 0 or yy == 0:
        result["status"] = "constant_variable"
        return result
    rx, ry = ranks(xs), ranks(ys)
    mid = (n + 1) / 2
    rho = math.fsum((a - mid) * (b - mid) for a, b in zip(rx, ry)) / math.sqrt(
        math.fsum((a - mid) ** 2 for a in rx) * math.fsum((b - mid) ** 2 for b in ry)
    )
    return {
        **result,
        "pearson_r": max(-1.0, min(1.0, xy / math.sqrt(xx * yy))),
        "spearman_rho": max(-1.0, min(1.0, rho)),
        "ols_slope": xy / xx,
        "ols_intercept": ym - xy / xx * xm,
        "status": "descriptive_only",
    }


def sign(value):
    return (
        "undefined"
        if value is None
        else "positive"
        if value > 1e-12
        else "negative"
        if value < -1e-12
        else "numerically_zero"
    )


def paired(rows, spec):
    name, xkey, ykey, lag, role = spec
    by_cycle = {int(row["cycle"]): row for row in rows}
    result = []
    for c in sorted(by_cycle):
        if c + lag not in by_cycle:
            continue
        x, y = by_cycle[c][xkey], by_cycle[c + lag][ykey]
        result.append(
            {
                "relation": name,
                "role": role,
                "lag_cycles": lag,
                "x_cycle": c,
                "y_cycle": c + lag,
                "x_metric": xkey,
                "y_metric": ykey,
                "x": x,
                "y": y,
                "touches_SC3_6": c in [3, 4, 5, 6] or c + lag in [3, 4, 5, 6],
                "included": x is not None and y is not None,
                "exclusion": ""
                if x is not None and y is not None
                else "undefined_ratio_or_window",
            }
        )
    return result


def summaries(pairs, p):
    valid = [row for row in pairs if row["included"]]
    groups = [
        ("all", valid),
        ("early_y_SC1_12", [r for r in valid if r["y_cycle"] < 13]),
        ("late_y_SC13_24", [r for r in valid if r["y_cycle"] >= 13]),
        ("exclude_physical_SC3_6", [r for r in valid if not r["touches_SC3_6"]]),
    ]
    groups += [
        (f"leave_pair_{r['x_cycle']}_{r['y_cycle']}", [k for k in valid if k is not r])
        for r in valid
    ]
    groups += [
        (
            f"leave_cycle_{c}",
            [r for r in valid if c not in (r["x_cycle"], r["y_cycle"])],
        )
        for c in p["cycles"]
    ]
    result = []
    for name, group in groups:
        stats = effect(
            [r["x"] for r in group], [r["y"] for r in group], p["minimum_pairs"]
        )
        result.append(
            {
                "subset": name,
                "included_pairs": "|".join(
                    f"{r['x_cycle']}:{r['y_cycle']}" for r in group
                ),
                **stats,
                "pearson_direction": sign(stats["pearson_r"]),
                "spearman_direction": sign(stats["spearman_rho"]),
            }
        )
    return result


def load_inputs(inputs, p):
    validate_parameters(p)
    inputs = Path(inputs).resolve()
    records = json.loads((inputs / "input_manifest.json").read_text("utf-8-sig"))[
        "files"
    ]
    if len(records) != len(INPUT_KEYS) or {r["key"] for r in records} != INPUT_KEYS:
        raise ValueError("D requires exactly nine frozen raw/baseline/C inputs")
    paths = {}
    for record in records:
        path = (inputs / record["path"]).resolve()
        if (
            not path.is_relative_to(inputs)
            or not path.is_file()
            or core.digest(path) != record["sha256"]
            or path.stat().st_size != record["bytes"]
        ):
            raise ValueError("D input identity mismatch: " + record["key"])
        paths[record["key"]] = path
    if len(set(paths.values())) != len(paths):
        raise ValueError("duplicate D input paths")
    monthly = core.load_monthly(paths["monthly"])
    smooth = core.load_monthly(paths["smoothed"], ";")
    bounds = core.load_boundaries(paths["boundaries"])
    ap = json.loads(paths["a_parameters"].read_text("utf-8-sig"))
    core.validate_parameters(ap)
    actual_a = core.calculate(monthly, smooth, bounds, ap["cycles"], ap["thresholds"])
    expected_a = sensitivity.table(paths["a_metrics"])
    canonical = [
        {k: "" if v is None else str(v) for k, v in row.items()} for row in actual_a
    ]
    if canonical != expected_a:
        raise ValueError("frozen A baseline does not reproduce")
    stored, plans = {}, {}
    for batch in ["c2a", "c2b"]:
        cp = json.loads(paths[batch + "_parameters"].read_text("utf-8-sig"))
        selected = sensitivity.validate_parameters(cp)
        if cp["batch"].lower() != batch:
            raise ValueError("C batch identity changed")
        table = sensitivity.table(paths[batch + "_metrics"])
        if len(table) != len(selected) * 24 or len(
            {(r["scenario"], r["cycle"]) for r in table}
        ) != len(table):
            raise ValueError("incomplete or duplicate C metrics")
        if {(r["scenario"], int(r["cycle"])) for r in table} != {
            (s["id"], c) for s in selected for c in p["cycles"]
        }:
            raise ValueError("C metric scenario/cycle coverage mismatch")
        for row in table:
            if row["scenario"] not in SCENARIOS:
                continue
            key = row["scenario"], int(row["cycle"])
            if key in stored and stored[key] != row:
                raise ValueError("repeated C baseline differs")
            stored[key] = row
        plans.update({s["id"]: s for s in selected if s["id"] in SCENARIOS})
    if set(plans) != set(SCENARIOS):
        raise ValueError("missing D sensitivity scenario")
    readiness = {
        "status": "inputs_ready_no_D_results",
        "input_files": records,
        "cycles": p["cycles"],
        "scenario_count": len(plans),
        "planned_D1_forward_pairs_per_scenario": 23,
        "planned_D2_cycles_per_scenario": 24,
        "baseline_A_reproduced": True,
        "C_variant_recomputation": "formal_execution_only",
        "scientific_review": "not_reviewed",
    }
    return monthly, smooth, bounds, stored, plans, readiness


def calculate(monthly, published, bounds, stored, plans, p):
    validate_parameters(p)
    all_features, pairs, stats, coupling = [], [], [], []
    for scenario in SCENARIOS:
        plan = plans[scenario]
        ep = plan["effective_parameters"]
        starts = {
            c: b["start"] + ep["boundary_shifts_months"].get(str(c), 0)
            for c, b in bounds.items()
        }
        smooth = (
            published
            if ep["smoothing"] == "published_13_month"
            else sensitivity.centered(
                monthly, starts[1], starts[25], int(ep["smoothing"].split("_")[-2])
            )
        )
        rows = sensitivity.cycle_rows(
            monthly, smooth, starts, ep["low_monthly_threshold"]
        )
        for row in rows:
            old = stored[scenario, row["cycle"]]
            if any(old.get(k) != ("" if v is None else str(v)) for k, v in row.items()):
                raise ValueError("C metrics do not reproduce: " + scenario)
            c, rise, decline = row["cycle"], row["rise_months"], row["decline_months"]
            offsets = list(range(12, 25))
            values = [smooth[starts[c] + i]["value"] for i in offsets]
            # Fixed calendar window; it is not selected using the future peak.
            slope = math.fsum(
                (i - 18) * v for i, v in zip(offsets, values)
            ) / math.fsum((i - 18) ** 2 for i in offsets)
            row.update(
                rise_decline_ratio=rise / decline if decline else None,
                peak_over_rise=row["smoothed_peak"] / rise if rise else None,
                fixed_12_24_slope=slope,
                fixed_window_before_peak=rise >= 24,
                scenario=scenario,
            )
            all_features.append(row)
        for spec in SPECS[p["batch"]]:
            sample = paired(rows, spec)
            pairs += [{"scenario": scenario, **r} for r in sample]
            stats += [
                {"scenario": scenario, "relation": spec[0], "role": spec[-1], **r}
                for r in summaries(sample, p)
            ]
        if p["batch"] == "D2":
            amplitudes = [r["smoothed_peak"] for r in rows]
            rises = [r["rise_months"] for r in rows]
            for shift in range(1, 24):
                rates = [
                    a / rises[(j + shift) % 24] if rises[(j + shift) % 24] else None
                    for j, a in enumerate(amplitudes)
                ]
                indices = [i for i, v in enumerate(rates) if v is not None]
                coupling.append(
                    {
                        "scenario": scenario,
                        "rise_time_circular_shift": shift,
                        "status_note": "shared_numerator_diagnostic_not_null_distribution_or_p_value",
                        **effect(
                            [rates[i] for i in indices],
                            [amplitudes[i] for i in indices],
                        ),
                    }
                )
    tables = {
        "cycle_features.csv": all_features,
        "paired_samples.csv": pairs,
        "association_statistics.csv": stats,
    }
    if coupling:
        tables["coupling_diagnostic.csv"] = coupling
    return tables


def hypothesis_cards(tables, p):
    baseline = [
        r for r in tables["association_statistics.csv"] if r["scenario"] == "baseline"
    ]
    cards = []
    for spec in SPECS[p["batch"]]:
        rows = [r for r in baseline if r["relation"] == spec[0]]
        allrow = next(r for r in rows if r["subset"] == "all")
        loo = [
            r["pearson_r"]
            for r in rows
            if r["subset"].startswith("leave_cycle_") and r["pearson_r"] is not None
        ]
        cards.append(
            {
                "relation": spec[0],
                "role": spec[-1],
                "baseline": allrow,
                "leave_physical_cycle_r_range": [min(loo), max(loo)] if loo else None,
                "status": "exploratory_descriptive_not_reviewed",
                "falsification": "Inspect all signs, periods, influential cycles and sensitivity choices; inconsistent directions weaken any general relation.",
                "limitations": [
                    "adjacent pairs share cycles",
                    "retrospective extrema and centered smoothing are not real-time predictors",
                    "no calibrated uncertainty or causal identification",
                ],
            }
        )
    return {
        "protocol": PROTOCOL,
        "batch": p["batch"],
        "cards": cards,
        "scientific_review": "not_reviewed",
    }


def render(output, tables, p):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    names = [spec[0] for spec in SPECS[p["batch"]]]
    with plt.rc_context(plt.rcParamsDefault):
        fig, axes = plt.subplots(
            len(names),
            1,
            figsize=(8, 3.1 * len(names)),
            layout="constrained",
            squeeze=False,
        )
        for ax, name in zip(axes[:, 0], names):
            sample = [
                r
                for r in tables["paired_samples.csv"]
                if r["scenario"] == "baseline"
                and r["relation"] == name
                and r["included"]
            ]
            for row in sample:
                ax.scatter(
                    row["x"],
                    row["y"],
                    c="#af4f36" if row["touches_SC3_6"] else "#315e82",
                    s=26,
                )
                ax.annotate(
                    f"{row['x_cycle']}:{row['y_cycle']}",
                    (row["x"], row["y"]),
                    fontsize=7,
                )
            if sample:
                ax.set(xlabel=sample[0]["x_metric"], ylabel=sample[0]["y_metric"])
            ax.set_title(
                name + " (retrospective; no causal/forecast claim)", fontsize=10
            )
            ax.grid(alpha=0.2)
        fig.savefig(
            output / "D_association_scatter.png",
            dpi=160,
            metadata={"Software": "D deterministic association analysis"},
        )
        plt.close(fig)


def run(inputs, parameters_path, output, *, check_only=False, execution_context=None):
    inputs, parameters_path, output = (
        Path(inputs).resolve(),
        Path(parameters_path).resolve(),
        Path(output).resolve(),
    )
    if (
        output.exists()
        or output == inputs
        or output.is_relative_to(inputs)
        or inputs.is_relative_to(output)
    ):
        raise ValueError("D output must be a fresh directory separate from inputs")
    output.mkdir(parents=True)
    info = {
        "protocol": PROTOCOL,
        "check_only": check_only,
        "executor": "operator_script",
        "agent_run": False,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "command": sys.argv,
        "seed": None,
        "review_status": "not_reviewed",
        **(execution_context or {}),
    }
    try:
        info["parameter_sha256"] = core.digest(parameters_path)
        manifest_sha = core.digest(inputs / "input_manifest.json")
        p = json.loads(parameters_path.read_text("utf-8-sig"))
        loaded = load_inputs(inputs, p)
        core.save_json(output / "readiness.json", loaded[-1])
        if not check_only:
            tables = calculate(*loaded[:-1], p)
            for name, rows in tables.items():
                core.write_csv(output / name, rows)
            cards = hypothesis_cards(tables, p)
            core.save_json(output / "hypotheses_D.json", cards)
            render(output, tables, p)
            lines = [
                "# " + p["batch"] + " retrospective associations",
                "",
                "Computed, not scientifically reviewed. No p-values, confidence intervals, prediction or causal inference.",
                "",
                "|relation|n|Pearson r|Spearman rho|OLS slope|",
                "|---|---:|---:|---:|---:|",
            ]
            for card in cards["cards"]:
                r = card["baseline"]
                lines.append(
                    f"|{card['relation']}|{r['n']}|{r['pearson_r']}|{r['spearman_rho']}|{r['ols_slope']}|"
                )
            lines += [
                "",
                "All scenario, era, leave-pair and leave-physical-cycle statistics are retained. Ranges are sensitivity ranges, not confidence intervals.",
                "The P/rise-time rate shares its numerator with amplitude. Circular denominator shifts only diagnose this coupling; they are not a calibrated null test.",
                "The month 12–24 slope uses a fixed window and may extend past some cycle peaks (flagged in cycle_features.csv). It is a retrospective proxy, not a claim to reproduce a literature WE2 estimator.",
                "SILSO activity is not magnetic-field amplitude; no estimate of turbulent correlation time or validation of a dynamo mechanism is made.",
            ]
            (output / "D_report.md").write_text("\n".join(lines) + "\n", "utf-8")
        shutil.copytree(inputs, output / "inputs")
        shutil.copy2(parameters_path, output / "D_parameters.json")
        (output / "code").mkdir()
        for name in [
            "cycle_associations.py",
            "cycle_association_verify.py",
            "cycle_metrics.py",
            "cycle_sensitivity.py",
            "cycle_phase.py",
        ]:
            shutil.copy2(Path(__file__).with_name(name), output / "code" / name)
        if (
            core.digest(parameters_path) != info["parameter_sha256"]
            or core.digest(inputs / "input_manifest.json") != manifest_sha
        ):
            raise ValueError("D parameters/manifest changed during execution")
        for row in loaded[-1]["input_files"]:
            if core.digest(inputs / row["path"]) != row["sha256"]:
                raise ValueError("D input changed during execution")
        info["status"] = (
            "inputs_ready_no_D_results"
            if check_only
            else "computed_not_scientifically_reviewed"
        )
        info["artifacts"] = [
            {
                "path": q.relative_to(output).as_posix(),
                "sha256": core.digest(q),
                "bytes": q.stat().st_size,
            }
            for q in sorted(output.rglob("*"))
            if q.is_file()
        ]
    except Exception as exc:
        info.update(status="failed", error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        info["finished_utc"] = datetime.now(timezone.utc).isoformat()
        core.save_json(output / "run_manifest.json", info)
    return info


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["inputs", "parameters", "output"]:
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    run(args.inputs, args.parameters, args.output, check_only=args.check_only)
