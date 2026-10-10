#!/usr/bin/env python3
"""Auditable descriptive cycle metrics. Local computation only; no model calls.

Inputs are SILSO monthly totals, published smoothed values and official minima.
The output directory must not exist. No interpolation or uncertainty imputation.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import shutil
import statistics
import sys


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_json(path, value):
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def month_index(year, month):
    if not 1 <= month <= 12:
        raise ValueError("invalid month")
    return year * 12 + month - 1


def month_label(index):
    year, month = divmod(index, 12)
    return f"{year:04d}-{month + 1:02d}"


def nullable(value):
    number = float(value)
    if not math.isfinite(number) or (number < 0 and number != -1):
        raise ValueError("nonfinite or unexpected negative value")
    return None if number == -1 else number


def load_monthly(path, delimiter=None):
    rows = {}
    previous = None
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8-sig").splitlines(), 1
    ):
        if not line.strip():
            continue
        fields = line.split(delimiter)
        if len(fields) not in (6, 7):
            raise ValueError(f"invalid monthly fields at line {line_number}")
        year, month = int(fields[0]), int(fields[1])
        index = month_index(year, month)
        if previous is not None and index <= previous:
            raise ValueError("duplicate or nonmonotonic month")
        decimal_year = float(fields[2])
        if not math.isfinite(decimal_year) or not year <= decimal_year < year + 1:
            raise ValueError("invalid decimal year")
        rows[index] = {
            "date": month_label(index),
            "value": nullable(fields[3]),
            "reported_dispersion": nullable(fields[4]),
            "observations": nullable(fields[5]),
            "publication_flag_raw": fields[6].strip() if len(fields) == 7 else "",
        }
        previous = index
    if not rows:
        raise ValueError("empty monthly input")
    return rows


def load_boundaries(path):
    rows = {}
    previous = None
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        fields = line.split()
        if not fields or not fields[0].isdigit():
            continue
        if len(fields) not in (4, 9):
            raise ValueError("invalid extrema row")
        number = int(fields[0])
        start = month_index(int(fields[1]), int(fields[2]))
        if number in rows or (previous is not None and start <= previous):
            raise ValueError("duplicate or nonmonotonic boundary")
        row = {"start": start, "minimum": nullable(fields[3])}
        if len(fields) == 9:
            row.update(
                peak_date=month_index(int(fields[4]), int(fields[5])),
                peak=nullable(fields[6]),
                duration=int(fields[7]) * 12 + int(fields[8]),
            )
        rows[number] = row
        previous = start
    if not rows:
        raise ValueError("empty boundary input")
    return rows


def quality(rows):
    indices = list(rows)
    return {
        "rows": len(rows),
        "first": month_label(min(indices)),
        "last": month_label(max(indices)),
        "duplicate_months": 0,
        "monotonic": True,
        "missing_dates": [
            month_label(i)
            for i in range(min(indices), max(indices) + 1)
            if i not in rows
        ],
        "missing_values": sum(r["value"] is None for r in rows.values()),
        "true_zero_values": sum(r["value"] == 0 for r in rows.values()),
        "missing_dispersion": sum(
            r["reported_dispersion"] is None for r in rows.values()
        ),
        "missing_observation_counts": sum(
            r["observations"] is None for r in rows.values()
        ),
    }


def longest_low(values, threshold):
    longest = current = 0
    for value in values:
        current = current + 1 if value is not None and value < threshold else 0
        longest = max(longest, current)
    return longest


def calculate(monthly, smoothed, boundaries, cycles, thresholds):
    result = []
    for cycle in cycles:
        if cycle not in boundaries or cycle + 1 not in boundaries:
            raise ValueError(f"missing next minimum for cycle {cycle}")
        official = boundaries[cycle]
        start, end = official["start"], boundaries[cycle + 1]["start"]
        indices = list(range(start, end))
        if not indices or official.get("duration") != len(indices):
            raise ValueError("official duration does not match boundaries")
        if not start <= official["peak_date"] < end:
            raise ValueError("official peak outside cycle")
        # A complete-cycle statistic requires every monthly value. Metadata
        # missingness is deliberately not a reason to discard an observation.
        for label, source in [("monthly", monthly), ("smoothed", smoothed)]:
            missing = [
                month_label(i)
                for i in indices
                if i not in source or source[i]["value"] is None
            ]
            if missing:
                raise ValueError(f"incomplete {label} cycle {cycle}: {missing}")
        values = [monthly[i]["value"] for i in indices]
        peak = max(smoothed[i]["value"] for i in indices)
        ties = [i for i in indices if smoothed[i]["value"] == peak]
        peak_date = ties[0]
        total = math.fsum(values)
        row = {
            "cycle": cycle,
            "minimum_date": month_label(start),
            "next_minimum_date_exclusive": month_label(end),
            "months": len(indices),
            "monthly_mean": total / len(indices),
            "activity_sn_month": total,
            "smoothed_peak": peak,
            "peak_date": month_label(peak_date),
            "peak_ties": "|".join(map(month_label, ties)),
            "official_peak": official["peak"],
            "official_peak_date": month_label(official["peak_date"]),
            "series_at_official_peak": smoothed[official["peak_date"]]["value"],
            "peak_minus_official": peak - official["peak"],
            "rise_months": peak_date - start,
            "decline_months": end - peak_date,
            "peak_phase": (peak_date - start) / len(indices),
            "zero_months": sum(v == 0 for v in values),
            "missing_dispersion_months": sum(
                monthly[i]["reported_dispersion"] is None for i in indices
            ),
            "missing_observation_count_months": sum(
                monthly[i]["observations"] is None for i in indices
            ),
        }
        for threshold in thresholds:
            suffix = f"{threshold:g}"
            count = sum(v < threshold for v in values)
            row[f"low_lt_{suffix}_months"] = count
            row[f"low_lt_{suffix}_fraction"] = count / len(values)
            row[f"low_lt_{suffix}_longest_months"] = longest_low(values, threshold)
        result.append(row)
    for key in ("smoothed_peak", "monthly_mean", "activity_sn_month"):
        for row in result:
            row[f"{key}_rank_desc"] = 1 + sum(other[key] > row[key] for other in result)
    return result


def contrasts(rows, params):
    by_cycle = {r["cycle"]: r for r in rows}
    comparisons = []
    primary_threshold = params["primary_threshold"]
    metrics = [
        "smoothed_peak",
        "monthly_mean",
        "activity_sn_month",
        f"low_lt_{primary_threshold:g}_fraction",
        f"low_lt_{primary_threshold:g}_longest_months",
    ]
    groups = [
        (name, members, params["targets"])
        for name, members in params["comparison_groups"].items()
    ]
    groups += [
        (p["name"], p["reference_cycles"], p["targets"])
        for p in params.get("paired_comparisons", [])
    ]
    for name, members, targets in groups:
        for target in targets:
            for metric in metrics:
                reference = statistics.mean(by_cycle[i][metric] for i in members)
                value = by_cycle[target][metric]
                comparisons.append(
                    {
                        "target": target,
                        "reference": name,
                        "reference_cycles": "|".join(map(str, members)),
                        "metric": metric,
                        "target_value": value,
                        "reference_mean": reference,
                        "difference": value - reference,
                        "ratio": value / reference if reference != 0 else None,
                    }
                )
    return comparisons


def write_csv(path, rows):
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def make_report(rows, comparisons, params, dq, execution_context=None):
    execution = (
        "执行入口：JW 确定性工具调用；实际 thread/run 与操作者预检区分见运行审计。"
        if execution_context
        else "执行者：操作者直接运行确定性 Python 脚本；不是 JW Agent 实跑。"
    )
    lines = [
        "# 周期定量研究：A 阶段描述性结果",
        "",
        execution + " 工具不裁定科研通过；产物生成时审查状态：`not_reviewed`。",
        "本报告仅描述冻结数据，不检验唯一物理成因，不给出显著性或预测。",
        "",
        "## 输入与方法",
        "",
        "SILSO V2.0 月均及官方发布的 13 月平滑序列、官方周期表，原始字节见 inputs/。",
        "周期采用相邻官方极小月 [start,end)，每月等权。累计量为月均值之和（黑子数·月），不是能量或磁通。",
        "平滑峰值在各周期内重新搜索，并列取最早月，保留所有并列日期；上升/下降时间以此峰值计算。",
        "-1 转缺失，真实零保留。周期任一月数值缺失则停止计算，不插值；误差/观测数缺失不删有效黑子数。",
        f"主阈值为原始月均 < {params['primary_threshold']:g}，其他阈值均完整输出；最长连续时长限制在单周期内，不能当跨边界低谷总时长。",
        "阈值为操作性定义，无特定物理临界含义。比较组均为周期等权均值，完整定义见 analysis_parameters.json。",
        f"月均 {dq['monthly']['rows']} 行：{dq['monthly']['first']}—{dq['monthly']['last']}，数值缺失 {dq['monthly']['missing_values']}，真实零 {dq['monthly']['true_zero_values']}。",
        f"平滑数值缺失 {dq['smoothed']['missing_values']}；只分析有下一极小边界且月值齐全的已指定完整周期。",
        "",
        "## 周期指标",
        "",
        "|周期|月数|平滑峰值|峰值日期|月均|累计 SN·月|上升/月|下降/月|",
        "|---|---:|---:|---|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"|SC{r['cycle']}|{r['months']}|{r['smoothed_peak']:.1f}|{r['peak_date']}|{r['monthly_mean']:.3f}|{r['activity_sn_month']:.1f}|{r['rise_months']}|{r['decline_months']}|"
        )
    lines += ["", "## 计算得到的描述性事实", ""]
    for r in rows:
        if r["cycle"] in params["targets"]:
            t = f"{params['primary_threshold']:g}"
            lines.append(
                f"- SC{r['cycle']}：峰值、月均、累计量在 {len(rows)} 个周期中的降序秩分别为 "
                f"{r['smoothed_peak_rank_desc']}、{r['monthly_mean_rank_desc']}、{r['activity_sn_month_rank_desc']}；"
                f"月均 <{t} 共 {r[f'low_lt_{t}_months']} 月，占 {r[f'low_lt_{t}_fraction']:.2%}，"
                f"周期内最长连续 {r[f'low_lt_{t}_longest_months']} 月。"
            )
    for c in comparisons:
        if c["reference"] == "SC4" and c["ratio"] is not None:
            relation = (
                "低于"
                if c["difference"] < 0
                else "高于"
                if c["difference"] > 0
                else "等于"
            )
            lines.append(
                f"- SC{c['target']} 的 {c['metric']} {relation} SC4，差值 {c['difference']:.4f}，比值 {c['ratio']:.4f}。"
            )
    lines += [
        "",
        "## 目标周期与比较组",
        "",
        "所有比值和差值均由 contrast 表生成；低活动占比的差为比例之差，不是百分比变化。",
        "",
        "|目标|参照组|指标|目标值|参照均值|差值|比值|",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for c in comparisons:
        ratio = "NA" if c["ratio"] is None else f"{c['ratio']:.4f}"
        lines.append(
            f"|SC{c['target']}|{c['reference']}|{c['metric']}|{c['target_value']:.4f}|{c['reference_mean']:.4f}|{c['difference']:.4f}|{ratio}|"
        )
    lines += ["", "## 产品差异（保留，不改写）", ""]
    differences = [
        r
        for r in rows
        if abs(r["peak_minus_official"]) > 1e-9
        or r["peak_date"] != r["official_peak_date"]
    ]
    for r in differences:
        lines.append(
            f"- SC{r['cycle']}：序列峰 {r['smoothed_peak']:.1f} / {r['peak_date']}；官方表 {r['official_peak']:.1f} / {r['official_peak_date']}；官方峰月的序列值 {r['series_at_official_peak']:.1f}。"
        )
    if not differences:
        lines.append("所选周期的峰值和日期一致。")
    lines += [
        "",
        "## 候选假设与范围",
        "",
        "Q1 拆成指标层面的描述性方向核对，状态与每项证据见 hypothesis_cards.json。对 SC4 和历史背景分别比较，不要求结论一致。",
        "Q2（形态缩放）、Q3（GNbb2）、Q4（跨周期关联）未在本阶段检验。B 方法已另行冻结；C/D 待主结果验收后开展。",
        "早期缺失误差列不是零误差。多指标结果为探索性描述，不把月份当独立样本；无 bootstrap、p 值或观测误差区间。",
        "边界、阈值与产品重建可影响结果。均值、累计量与峰值相关，不能当成互相独立的证据。",
        "低活动月份指月均低于阈值，与文献中的逐日活跃比例不等价。没有同期磁场/流场观测，不能识别唯一因果机制。",
        "",
        "## 文件与复算",
        "",
        "cycle_metrics.csv、contrasts.csv、data/clean_monthly.csv、data_quality.json、figures/ 和 run_manifest.json 构成本次产物。",
        "复制的代码在 code/analyze_cycle_metrics.py。使用 Python 及 matplotlib，命令：",
        "",
        "```powershell",
        "python code/analyze_cycle_metrics.py --inputs inputs --parameters analysis_parameters.json --output ../A_rerun",
        "```",
        "",
        "输出目录必须不存在。该命令仅本地计算，不访问网络、不调用模型。版本见 run_manifest.json；环境锁文件见 code/uv.lock。",
        "",
        "来源：[WDC-SILSO, Royal Observatory of Belgium](https://www.sidc.be/SILSO/home)，数据 DOI 10.24414/qnza-ac80。",
        "原获取日期、来源 URL、许可、SHA-256 见 inputs/metadata/silso_snapshot_manifest.json；本次不重新下载或改变版本。",
    ]
    return "\n".join(lines) + "\n"


def hypothesis_cards(comparisons):
    cards = []
    for c in comparisons:
        direction = "higher" if c["metric"].startswith("low_") else "lower"
        consistent = (
            c["difference"] > 0 if direction == "higher" else c["difference"] < 0
        )
        exploratory = c.get("target") not in (5, 6)
        cards.append(
            {
                "hypothesis": "exploratory_context" if exploratory else "Q1",
                **c,
                "expected_direction": "exploratory" if exploratory else direction,
                "status": "descriptive_only"
                if exploratory
                else (
                    "direction_consistent_descriptively"
                    if consistent
                    else "direction_not_supported"
                ),
                "test": "deterministic signed difference; no inferential significance test",
                "refutation": "opposite or zero signed difference under the declared definition",
                "evidence": "contrasts.csv",
                "uncertainty": "historical systematic errors unquantified; sensitivity pending",
                "alternative": "boundary choice, observation calibration, duration and monthly variability",
            }
        )
    return cards


def figures(output, rows, monthly, smoothed, boundaries, params):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    folder = output / "figures"
    folder.mkdir()
    background = [r for r in rows if r["cycle"] in params["plot_cycles"]]
    fig, axes = plt.subplots(2, 2, figsize=(11, 7), layout="constrained")
    colors = [
        "#be6c3b" if r["cycle"] in params["targets"] else "#315c76" for r in background
    ]
    for ax, key, label in zip(
        axes.flat,
        ["smoothed_peak", "monthly_mean", "activity_sn_month", "months"],
        [
            "13-month smoothed peak [SN]",
            "Cycle monthly mean [SN]",
            "Activity proxy [SN month]",
            "Cycle duration [month]",
        ],
    ):
        ax.bar(
            [r["cycle"] for r in background], [r[key] for r in background], color=colors
        )
        ax.set(
            xlabel="Solar cycle", ylabel=label, xticks=[r["cycle"] for r in background]
        )
        ax.grid(axis="y", alpha=0.2)
    fig.suptitle(
        "SILSO V2.0 frozen snapshot | official [minimum, next minimum) boundaries"
    )
    fig.savefig(folder / "cycle_metrics.png", dpi=160)
    plt.close(fig)
    start = boundaries[min(params["plot_cycles"])]["start"]
    end = boundaries[max(params["plot_cycles"]) + 1]["start"]
    indices = list(range(start, end))
    fig, ax = plt.subplots(figsize=(12, 4), layout="constrained")
    x = [i / 12 for i in indices]
    ax.plot(
        x,
        [monthly[i]["value"] for i in indices],
        color="#b7c5ce",
        lw=0.8,
        label="Monthly mean",
    )
    ax.plot(
        x,
        [smoothed[i]["value"] for i in indices],
        color="#25485c",
        lw=1.5,
        label="Published 13-month smoothed",
    )
    for c in params["plot_cycles"]:
        ax.axvline(boundaries[c]["start"] / 12, color="gray", lw=0.6)
        ax.text(
            (boundaries[c]["start"] + boundaries[c + 1]["start"]) / 24,
            ax.get_ylim()[1] * 0.95,
            f"SC{c}",
            ha="center",
            fontsize=8,
        )
    ax.set(
        xlabel="Year (equal monthly spacing)",
        ylabel="Sunspot number [SN]",
        title=f"SILSO V2.0 | SC{min(params['plot_cycles'])}-{max(params['plot_cycles'])} historical context",
    )
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=2, frameon=False)
    fig.savefig(folder / "monthly_history.png", dpi=160)
    plt.close(fig)


def validate_parameters(params):
    cycles = params["cycles"]
    if (
        not cycles
        or sorted(set(cycles)) != cycles
        or any(type(c) is not int or c < 1 for c in cycles)
    ):
        raise ValueError("cycles must be unique increasing positive integers")
    thresholds = params["thresholds"]
    if (
        not thresholds
        or len(set(thresholds)) != len(thresholds)
        or any(not math.isfinite(t) or t <= 0 for t in thresholds)
    ):
        raise ValueError("thresholds must be finite unique positive numbers")
    if params["primary_threshold"] not in thresholds:
        raise ValueError("primary threshold missing")
    if (
        params["boundary"] != "left_closed_right_open"
        or params["peak_tie"] != "earliest"
        or params["missing_policy"] != "fail_incomplete_cycle"
    ):
        raise ValueError("unsupported scientific policy")
    for selected in [
        params["targets"],
        params["plot_cycles"],
        *params["comparison_groups"].values(),
    ]:
        if (
            not selected
            or len(set(selected)) != len(selected)
            or not set(selected) <= set(cycles)
        ):
            raise ValueError("invalid comparison/target/plot cycle set")
    if any(
        set(params["targets"]) & set(group)
        for group in params["comparison_groups"].values()
    ):
        raise ValueError("target in comparison group")
    for pair in params.get("paired_comparisons", []):
        ref, targets = pair["reference_cycles"], pair["targets"]
        if (
            not ref
            or not targets
            or set(ref) & set(targets)
            or not set(ref + targets) <= set(cycles)
        ):
            raise ValueError("invalid paired comparison")


def run(inputs, parameters, output, execution_context=None):
    inputs, parameters, output = (
        Path(inputs).resolve(),
        Path(parameters).resolve(),
        Path(output).resolve(),
    )
    if (
        output.exists()
        or output.is_relative_to(inputs)
        or inputs.is_relative_to(output)
    ):
        raise ValueError("output must be a new directory separate from inputs")
    output.mkdir(parents=True)
    started = datetime.now(timezone.utc).isoformat()
    manifest = {
        "schema": "cycle-metrics-run-v1",
        "started_utc": started,
        "executor": "local_deterministic_script",
        "agent_run": False,
        "thread_id": None,
        "run_id": None,
        "review_status": "not_reviewed",
        "python": sys.version,
        "platform": platform.platform(),
        "command": sys.argv,
        "seed": None,
        "dependencies": {
            p: importlib.metadata.version(p) for p in ["numpy", "matplotlib"]
        },
    }
    if execution_context:
        manifest.update(execution_context)
    try:
        params = json.loads(parameters.read_text(encoding="utf-8-sig"))
        validate_parameters(params)
        snapshot = json.loads(
            (inputs / "metadata/silso_snapshot_manifest.json").read_text(
                encoding="utf-8-sig"
            )
        )
        paths = {}
        input_records = []
        for record in snapshot["files"]:
            path = (inputs / record["package_path"]).resolve()
            if not path.is_relative_to(inputs):
                raise ValueError("input escapes root")
            actual = digest(path)
            if actual != record["sha256"] or path.stat().st_size != record["bytes"]:
                raise ValueError(f"input identity mismatch: {record['dataset_id']}")
            paths[record["dataset_id"]] = path
            input_records.append(
                {**record, "bound_path": str(path), "verified_sha256": actual}
            )
        manifest["inputs"] = input_records
        manifest["parameter_sha256"] = digest(parameters)
        monthly = load_monthly(paths["silso-monthly-total-v2"])
        smoothed = load_monthly(paths["silso-monthly-smoothed-v2"], ";")
        boundaries = load_boundaries(paths["silso-cycle-extrema-v2"])
        dq = {
            "monthly": quality(monthly),
            "smoothed": quality(smoothed),
            "units": "dimensionless sunspot index; cumulative index-month",
            "missing_marker": -1,
            "metadata_missing_is_not_zero_uncertainty": True,
        }
        save_json(output / "data_quality.json", dq)
        rows = calculate(
            monthly, smoothed, boundaries, params["cycles"], params["thresholds"]
        )
        comparisons = contrasts(rows, params)
        shutil.copytree(inputs, output / "inputs")
        (output / "code").mkdir()
        shutil.copy2(__file__, output / "code/analyze_cycle_metrics.py")
        for candidate in [
            Path(__file__).parent / "uv.lock",
            Path(__file__).parent.parent / "uv.lock",
        ]:
            if candidate.is_file():
                shutil.copy2(candidate, output / "code/uv.lock")
                break
        save_json(output / "analysis_parameters.json", params)
        write_csv(output / "cycle_metrics.csv", rows)
        write_csv(output / "contrasts.csv", comparisons)
        (output / "data").mkdir()
        clean = [
            {"month_index": i, **r, "smoothed_value": smoothed.get(i, {}).get("value")}
            for i, r in monthly.items()
        ]
        write_csv(output / "data/clean_monthly.csv", clean)
        save_json(output / "hypothesis_cards.json", hypothesis_cards(comparisons))
        (output / "sc3_sc6_quantitative_report.md").write_text(
            make_report(rows, comparisons, params, dq, execution_context),
            encoding="utf-8",
        )
        figures(output, rows, monthly, smoothed, boundaries, params)
        if any(
            digest(Path(r["bound_path"])) != r["verified_sha256"] for r in input_records
        ):
            raise ValueError("input changed during analysis")
        manifest["status"] = "computed_not_scientifically_reviewed"
        manifest["artifacts"] = [
            {
                "path": p.relative_to(output).as_posix(),
                "sha256": digest(p),
                "bytes": p.stat().st_size,
            }
            for p in sorted(output.rglob("*"))
            if p.is_file()
        ]
    except Exception as exc:
        manifest.update(
            status="failed", failure_type=type(exc).__name__, failure_message=str(exc)
        )
        raise
    finally:
        manifest["finished_utc"] = datetime.now(timezone.utc).isoformat()
        save_json(output / "run_manifest.json", manifest)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--parameters", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    run(args.inputs, args.parameters, args.output)


if __name__ == "__main__":
    main()
