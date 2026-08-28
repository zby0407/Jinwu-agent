#!/usr/bin/env python3
"""Create reader-facing plots for JW historical backtests and Cycle 26.

The script consumes the deterministic CSV/JSON receipts already produced by
the SILSO Cycle-26 run and the polar-precursor run.  It does not refit a model
or change any scientific result; it only renders the recorded predictions and
metrics into publication-friendly PNG/PDF figures.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager, rcParams
import numpy as np
import pandas as pd


def configure_style() -> None:
    cjk = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    if cjk.exists():
        font_manager.fontManager.addfont(str(cjk))
        rcParams["font.family"] = "Noto Sans CJK JP"
    rcParams["axes.unicode_minus"] = False
    rcParams["font.size"] = 10
    rcParams["axes.titlesize"] = 12
    rcParams["axes.labelsize"] = 10
    rcParams["xtick.labelsize"] = 9
    rcParams["ytick.labelsize"] = 9


COLORS = {
    "actual": "#0072B2",
    "lag": "#D55E00",
    "lag_rise": "#CC79A7",
    "same": "#009E73",
    "mean": "#6C757D",
    "persistence": "#E69F00",
    "forecast": "#7B3294",
    "interval": "#C2A5CF",
    "mwo": "#56B4E9",
    "wso": "#009E73",
}


def save(fig: plt.Figure, out: Path, stem: str) -> None:
    fig.savefig(out / f"{stem}.png", dpi=300, bbox_inches="tight")
    fig.savefig(out / f"{stem}.pdf", bbox_inches="tight")
    plt.close(fig)


def load_inputs(legacy_dir: Path, polar_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict, pd.DataFrame, dict]:
    cycles = pd.read_csv(legacy_dir / "sc26_cycle_features.csv")
    pred = pd.read_csv(legacy_dir / "sc26_forecast_predictions.csv")
    summary = json.loads((legacy_dir / "run_summary.json").read_text(encoding="utf-8"))
    polar_pred = pd.read_csv(polar_dir / "public/stages/analysis_stage/rolling_predictions.csv")
    polar_receipt = json.loads(
        (polar_dir / "public/stages/analysis_stage/forecast_experiment_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    return cycles, pred, summary, polar_pred, polar_receipt


def plot_historical_curves(out: Path, cycles: pd.DataFrame, pred: pd.DataFrame) -> None:
    lag = pred[pred["model"] == "lag_peak"].sort_values("cycle")
    lag_rise = pred[pred["model"] == "lag_peak_rise"].sort_values("cycle")
    same = pred[pred["model"] == "same_cycle_rise"].sort_values("cycle")

    fig, axes = plt.subplots(2, 1, figsize=(10, 7.2), sharex=True, constrained_layout=True)
    ax = axes[0]
    ax.plot(lag.cycle, lag.observed_peak, "o-", color=COLORS["actual"], lw=2.2, label="实际峰值")
    ax.plot(lag.cycle, lag.candidate_prediction, "s--", color=COLORS["lag"], lw=1.8, label="前一周峰值模型")
    ax.plot(lag_rise.cycle, lag_rise.candidate_prediction, "^--", color=COLORS["lag_rise"], lw=1.8, label="前一周峰值 + 上升率")
    ax.plot(same.cycle, same.candidate_prediction, "D-.", color=COLORS["same"], lw=1.6, label="同周早期上升率")
    ax.set_ylabel("峰值（13个月平滑 SN）")
    ax.set_title("历史时间顺序回测：实际峰值与逐周预测")
    ax.legend(ncol=2, frameon=False, loc="upper left")
    ax.axvline(12.5, color="#999999", lw=0.8, ls=":")
    ax.text(12.65, 0.04, "同周模型测试起点", transform=ax.get_xaxis_transform(), color="#666666", fontsize=8, va="bottom")

    ax = axes[1]
    for frame, key, label, marker in [
        (lag, "lag", "前一周峰值", "s"),
        (lag_rise, "lag_rise", "峰值 + 上升率", "^"),
        (same, "same", "同周早期上升率", "D"),
    ]:
        err = np.abs(frame["observed_peak"] - frame["candidate_prediction"])
        ax.plot(frame.cycle, err, marker=marker, lw=1.7, color=COLORS[key], label=label)
    ax.axhline(float(np.abs(lag.observed_peak - lag.baseline_prediction).mean()), color=COLORS["mean"], ls=":", lw=1.5, label="前一周峰值模型的均值基线 MAE")
    ax.set_xlabel("目标活动周")
    ax.set_ylabel("绝对误差（SN）")
    ax.set_title("逐周绝对误差（越低越好）")
    ax.legend(ncol=2, frameon=False, loc="upper left")
    ax.set_xticks(range(10, 25))
    ax.grid(True, alpha=0.25)
    save(fig, out, "jw_historical_backtest_curves")


def plot_predicted_scatter(out: Path, pred: pd.DataFrame, polar_pred: pd.DataFrame) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(9, 8), constrained_layout=True)
    panels = [
        (axes[0, 0], pred[pred.model == "lag_peak"], "lag", "前一周峰值"),
        (axes[0, 1], pred[pred.model == "lag_peak_rise"], "lag_rise", "前一周峰值 + 上升率"),
        (axes[1, 0], pred[pred.model == "same_cycle_rise"], "same", "同周早期上升率"),
        (axes[1, 1], polar_pred.rename(columns={"observed": "observed_peak", "candidate_prediction": "candidate_prediction"}), "same", "极小期极区场（H2）"),
    ]
    for ax, frame, key, title in panels:
        x = frame["candidate_prediction"].to_numpy(float)
        y = frame["observed_peak"].to_numpy(float)
        ax.scatter(x, y, s=42, color=COLORS[key], edgecolor="white", linewidth=0.5)
        lim = [0, max(float(np.nanmax(x)), float(np.nanmax(y))) * 1.08]
        ax.plot(lim, lim, "k--", lw=1, label="完美预测")
        mae = float(np.mean(np.abs(x - y)))
        ax.text(0.04, 0.94, f"MAE = {mae:.1f}", transform=ax.transAxes, va="top", fontsize=9)
        ax.set_title(title)
        ax.set_xlabel("预测峰值（SN）")
        ax.set_ylabel("实际峰值（SN）")
        ax.set_xlim(lim)
        ax.set_ylim(lim)
        ax.grid(True, alpha=0.25)
    fig.suptitle("预测值与实际值：历史模型和极区场补充回测", fontsize=14, fontweight="bold")
    save(fig, out, "jw_predicted_vs_observed")


def plot_cycle26_context(out: Path, cycles: pd.DataFrame, summary: dict) -> None:
    forecast = summary["forecast"]
    complete = cycles[cycles.cycle <= 24]
    c25 = cycles[cycles.cycle == 25]
    fig, ax = plt.subplots(figsize=(10, 5.8), constrained_layout=True)
    ax.plot(complete.cycle, complete.peak, "o-", color=COLORS["actual"], lw=2.2, label="SC1–24 官方峰值")
    if not c25.empty:
        ax.plot(c25.cycle, c25.peak, "o--", color="#555555", lw=1.8, label="SC25 截至日期的暂定峰值")
    ax.errorbar(
        [26],
        [forecast["point_estimate"]],
        yerr=[[forecast["point_estimate"] - forecast["predictive_interval_95"][0]], [forecast["predictive_interval_95"][1] - forecast["point_estimate"]]],
        fmt="o",
        color=COLORS["forecast"],
        ecolor=COLORS["forecast"],
        elinewidth=2.2,
        capsize=7,
        markersize=8,
        label="SC26 主模型点估计与 95% 预测区间",
    )
    ax.axhline(forecast["climatology_mean_cycles_1_24"], color=COLORS["mean"], ls=":", lw=1.7, label=f"SC1–24 均值 = {forecast['climatology_mean_cycles_1_24']:.1f}")
    ax.axhline(forecast["sensitivity_lag_peak_rise"], color=COLORS["lag_rise"], ls="--", lw=1.3, label=f"峰值 + 上升率敏感性 = {forecast['sensitivity_lag_peak_rise']:.1f}")
    ax.set_xlabel("太阳活动周")
    ax.set_ylabel("峰值（13个月平滑 SN）")
    ax.set_title("SC26 条件统计预测的历史背景（数据冻结：2026-08-27）")
    ax.set_xticks(range(1, 27, 2))
    ax.set_xlim(0.5, 26.7)
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    ax.grid(True, alpha=0.25)
    ax.text(26, forecast["predictive_interval_95"][1] + 7, "175.0", color=COLORS["forecast"], ha="center", fontweight="bold")
    save(fig, out, "jw_cycle26_forecast_context")


def plot_polar_backtest(out: Path, polar_pred: pd.DataFrame, receipt: dict) -> None:
    frame = polar_pred.sort_values("test_cycle")
    fig, ax = plt.subplots(figsize=(9.5, 5.2), constrained_layout=True)
    ax.plot(frame.test_cycle, frame.observed, "o-", color=COLORS["actual"], lw=2.2, label="实际峰值")
    ax.plot(frame.test_cycle, frame.candidate_prediction, "s--", color=COLORS["same"], lw=1.9, label="极区场候选模型")
    ax.plot(frame.test_cycle, frame.training_mean_prediction, "^:", color=COLORS["mean"], lw=1.6, label="训练均值基线")
    ax.plot(frame.test_cycle, frame.persistence_prediction, "D-.", color=COLORS["persistence"], lw=1.5, label="持续性基线")
    for _, row in frame.iterrows():
        color = COLORS["mwo"] if row.measurement_regime == "MWO" else COLORS["wso"]
        ax.axvspan(row.test_cycle - 0.45, row.test_cycle + 0.45, color=color, alpha=0.08)
        ax.text(row.test_cycle, ax.get_ylim()[0] if ax.get_ylim()[0] > 0 else 0, row.measurement_regime, ha="center", va="bottom", fontsize=8, color=color)
    metrics = receipt["metrics"]
    ax.text(0.02, 0.96, f"MAE {metrics['candidate_mae']:.1f} vs 均值基线 {metrics['training_mean_mae']:.1f}\n改善 13.1；95%区间 [−7.0, 31.3]", transform=ax.transAxes, va="top", fontsize=9, bbox={"facecolor": "white", "alpha": 0.85, "edgecolor": "none"})
    ax.set_xlabel("测试目标活动周")
    ax.set_ylabel("峰值（13个月平滑 SN）")
    ax.set_title("极小期极区场 H2：第20–24周严格滚动回测")
    ax.set_xticks(frame.test_cycle)
    ax.legend(frameon=False, ncol=2, loc="lower left")
    ax.grid(True, alpha=0.25)
    save(fig, out, "jw_polar_precursor_backtest")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-dir", type=Path, required=True)
    parser.add_argument("--polar-run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    configure_style()
    cycles, pred, summary, polar_pred, polar_receipt = load_inputs(args.legacy_dir, args.polar_run_dir)
    plot_historical_curves(args.output_dir, cycles, pred)
    plot_predicted_scatter(args.output_dir, pred, polar_pred)
    plot_cycle26_context(args.output_dir, cycles, summary)
    plot_polar_backtest(args.output_dir, polar_pred, polar_receipt)
    print(json.dumps({"output_dir": str(args.output_dir.resolve()), "figures": sorted(p.name for p in args.output_dir.glob("*.png"))}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
