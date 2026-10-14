"""Reader-facing reports from issued bytes and the frozen full test cohort."""

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .assets import sha
from .model import FEATURES, load_forest
from .native_data import write
from .workflow import verify_selected_asset


def local_time(stamp):
    return (
        datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        .astimezone(timezone(timedelta(hours=8)))
        .strftime("%Y-%m-%d %H:%M:%S")
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


def build_report(forecast: dict, assets: Path, output: Path) -> str:
    """Does not issue, fetch, fit, change probabilities or assert future truth."""
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    output.mkdir(parents=True, exist_ok=False)
    summary, rows, predictions = history_summary(assets)
    write(output / "historical_metrics.json", summary)
    dates = [
        datetime.fromisoformat(r["target_start_utc"].replace("Z", "+00:00"))
        for r in rows
    ]
    fig = Figure(figsize=(11, 5.5), layout="constrained")
    axes = fig.subplots(2, 1, sharex=True)
    for ax, target, label in zip(
        axes, ("C1.0+", "M1.0+"), ("label_c1", "label_m1"), strict=True
    ):
        line_dates, line_values = [], []
        for i, stamp in enumerate(dates):
            if i and (stamp - dates[i - 1]).total_seconds() > 48 * 3600:
                line_dates.append(stamp)
                line_values.append(float("nan"))
            line_dates.append(stamp)
            line_values.append(predictions[i][target])
        ax.plot(
            line_dates,
            line_values,
            linewidth=0.7,
            label="Frozen forecast probability",
        )
        ax.scatter(
            dates,
            [int(r[label]) for r in rows],
            s=5,
            alpha=0.22,
            label="Observed binary label",
        )
        ax.axhline(0.5, color="gray", linestyle="--", linewidth=0.6)
        ax.set(
            ylim=(-0.06, 1.06),
            ylabel=target,
            title=f"Historical independent test: all {len(rows)} windows",
        )
        ax.legend(fontsize=8, loc="lower left")
    axes[-1].set_xlabel(
        "Historical target start (UTC), 2023-2025; not a future hourly trajectory"
    )
    fig.savefig(output / "history.png", dpi=140)
    start, end = (
        forecast["prediction_window"]["start"],
        forecast["prediction_window"]["end"],
    )
    lines = [
        "# 未来24小时太阳耀斑实验预报",
        "",
        f"北京时间窗口：**{local_time(start)} 至 {local_time(end)}**（起点包含、终点排除）。",
        "",
        f"UTC窗口：{start} 至 {end}。签发时间为窗口起点，不回填为用户发消息时刻。",
        "",
        "## 本次预测",
        "",
        "| 目标 | 24小时内至少一次新起始事件的概率 | 固定0.5阈值判定 |",
        "| --- | ---: | --- |",
    ]
    for target, p in forecast["probabilities"].items():
        lines.append(
            f"| {target} | {p:.2%} | {'达到阳性阈值' if p >= 0.5 else '未达阳性阈值'} |"
        )
    lines += [
        "",
        "M1.0+包含X级，C1.0+包含M/X级。概率不等于正确率；未达阈值也不等于不会爆发。预测不提供爆发地点、精确时刻、逐小时强度或次数。",
        "",
        f"磁场观测UTC：{forecast['input_observation_utc']}；签发时最旧输入年龄：{forecast['input_age_seconds'] / 60:.1f}分钟。输入为23项中央SHARP标量，目标为全太阳GOES事件。",
        "",
        "## 历史性能与用途",
        "",
        "历史主队列2325个合格窗口，最终拟合1201个；独立测试为2023—2025年全部841个窗口。2011—2022为发展范围，但实际合格数据始于2014年。",
        "",
        "| 目标 | 正/负样本 | Accuracy | Brier（越低越好） | 恒报有Accuracy | 恒报有Brier | 召回率 | TSS |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for target, m in summary["targets"].items():
        lines.append(
            f"| {target} | {m['positive']}/{m['negative']} | {m['accuracy']:.2%} | {m['brier']:.6f} | {m['always_yes_accuracy']:.2%} | {m['always_yes_brier']:.6f} | {m['recall']:.2%} | {m['tss']:.4f} |"
        )
    lines += [
        "",
        "C级历史技能未获支持，不能用高Accuracy掩盖正样本占比高或优于基线的证据不足。M级仅有受限历史技能支持，仍存在漏报，不能保证本次窗口正确。当前用途是可审计的实验性概率参考，尚无稳定业务验证、同队列成熟模型比较或新因果结论。",
        "",
        "![全部841个独立历史测试窗口](history.png)",
        "",
        "图中曲线为全部历史测试概率，点为二元真实标签；超过两天的样本间隔断开曲线，不为缺失窗口补值。不是未来24小时的逐时耀斑走势。",
        "",
        "## 资格与限制",
        "",
        "本次实时输入由新原生JW Data实际读取并独立审阅后签发。完整QUALITY=1024仍为警告；中央patch与全太阳标签有空间差异。来源版本/使用政策未完全核定。没有重训、调阈值或读取未来真值；正式科学审查链与业务验证仍未完成。",
        "",
        "未来真实爆发情况目前未知。须在目标窗口结束且官方目录更新后才能核验，本报告没有安排自动核验或自动重发。",
        "",
        f"原模型checkpoint SHA256：`{forecast['model_origin_checkpoint_sha256']}`。",
        f"本次实时manifest SHA256：`{forecast['live_manifest_sha256']}`。",
        f"原生Data审阅SHA256：`{forecast['native_Data_review_sha256']}`。",
        "",
    ]
    text = "\n".join(lines)
    (output / "report.md").write_text(text, encoding="utf-8")
    import base64

    from markdown_it import MarkdownIt

    body = MarkdownIt("commonmark").enable("table").render(text)
    encoded = base64.b64encode((output / "history.png").read_bytes()).decode("ascii")
    body = body.replace('src="history.png"', f'src="data:image/png;base64,{encoded}"')
    page = (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>未来24小时耀斑实验预报</title><style>body{font-family:system-ui,sans-serif;max-width:1100px;margin:40px auto;padding:0 24px;line-height:1.7;color:#16263a}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #cad5e2;padding:9px}th{background:#edf3fa}img{width:100%;height:auto}code{overflow-wrap:anywhere}h1,h2{color:#18476c}@media print{body{margin:0}h2{break-after:avoid}img,table{break-inside:avoid}}</style><body>'
        + body
        + "</body></html>"
    )
    (output / "report.html").write_text(page, encoding="utf-8")
    return text
