#!/usr/bin/env python3
"""Render the H2 upgrade diagnostics from its deterministic receipt."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager, rcParams

COLORS = {
    "primary": "#0072B2",
    "challenger": "#D55E00",
    "weak": "#009E73",
    "actual": "#262626",
    "mean": "#7A7A7A",
    "provisional": "#CC79A7",
}


def style() -> None:
    cjk = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    if cjk.exists():
        font_manager.fontManager.addfont(str(cjk))
        rcParams["font.family"] = "Noto Sans CJK JP"
    rcParams["axes.unicode_minus"] = False
    rcParams["font.size"] = 10


def save(fig: plt.Figure, out: Path, name: str) -> None:
    fig.savefig(out / f"{name}.png", dpi=300, bbox_inches="tight")
    fig.savefig(out / f"{name}.pdf", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--figures", type=Path, required=True)
    args = parser.parse_args()
    args.figures.mkdir(parents=True, exist_ok=True)
    style()
    receipt = json.loads(
        (args.results / "h2_upgrade_receipt.json").read_text(encoding="utf-8")
    )
    metrics = []
    for model, payload in receipt["models"].items():
        m = payload["metrics"]
        metrics.append((model, m["candidate_mae"], m["mae_improvement_interval"]))
    labels = {
        "mean_polar_linear": "均值极区场（主模型）",
        "sqrt_mean_polar_linear": "√均值极区场",
        "target_dispersion_weighted_linear": "按峰值离散度加权",
        "weakest_hemisphere_linear": "较弱半球极区场",
    }
    fig, ax = plt.subplots(figsize=(9, 5.4), constrained_layout=True)
    y = np.arange(len(metrics))
    ax.barh(
        y,
        [m[1] for m in metrics],
        color=[
            COLORS["primary"] if m[0] == "mean_polar_linear" else COLORS["challenger"]
            for m in metrics
        ],
        alpha=0.85,
    )
    for i, (_model, mae, interval) in enumerate(metrics):
        ax.text(
            mae + 0.6,
            i,
            f"MAE {mae:.1f}; 改善区间 [{interval[0]:.1f}, {interval[1]:.1f}]",
            va="center",
            fontsize=8,
        )
    ax.set_yticks(y, [labels[m[0]] for m in metrics])
    ax.invert_yaxis()
    ax.set_xlabel("滚动测试 MAE（13 个月平滑太阳黑子数）")
    ax.set_title("H2 预先登记模型的历史滚动比较（SC20–24）")
    ax.grid(axis="x", alpha=0.25)
    save(fig, args.figures, "h2_model_comparison")

    rows = list(
        csv.DictReader(
            (args.results / "h2_input_rows.csv").open(encoding="utf-8", newline="")
        )
    )
    preds = list(
        csv.DictReader(
            (args.results / "h2_rolling_predictions.csv").open(
                encoding="utf-8", newline=""
            )
        )
    )
    primary = [r for r in preds if r["model"] == "mean_polar_linear"]
    fig, axes = plt.subplots(
        2, 1, figsize=(10, 7), constrained_layout=True, sharex=False
    )
    ax = axes[0]
    cycles = [int(r["target_cycle_id"]) for r in rows]
    north = [float(r["north_abs_gauss"]) for r in rows]
    south = [float(r["south_abs_gauss"]) for r in rows]
    ax.plot(cycles, north, "o-", color="#56B4E9", label="北半球 |B|（MWO/WSO）")
    ax.plot(cycles, south, "s-", color="#009E73", label="南半球 |B|（MWO/WSO）")
    ax.plot(
        cycles,
        [(a + b) / 2 for a, b in zip(north, south, strict=True)],
        "^-",
        color=COLORS["primary"],
        label="两半球均值（H2 主特征）",
    )
    ax.set_ylabel("极小期极区场（G）")
    ax.set_title("H2 特征升级：显式保留两半球信息")
    ax.legend(frameon=False, ncol=3, fontsize=8)
    ax.grid(alpha=0.25)
    ax = axes[1]
    t = [int(r["test_cycle"]) for r in primary]
    obs = [float(r["observed"]) for r in primary]
    predv = [float(r["candidate_prediction"]) for r in primary]
    base = [float(r["training_mean_prediction"]) for r in primary]
    ax.plot(t, obs, "o-", color=COLORS["actual"], label="实际峰值")
    ax.plot(t, predv, "s--", color=COLORS["primary"], label="H2 主模型预测")
    ax.plot(t, base, "^:", color=COLORS["mean"], label="训练均值基线")
    p = receipt.get("provisional_check")
    if p:
        ax.plot(
            [p["target_cycle_id"]],
            [p["candidate_prediction"]],
            marker="D",
            ms=8,
            mfc="white",
            color=COLORS["provisional"],
            label="SC25 暂定前瞻检查（不入技能门）",
        )
        ax.plot(
            [p["target_cycle_id"]],
            [p["observed_provisional_target"]],
            marker="x",
            ms=8,
            color=COLORS["provisional"],
        )
    ax.set_xlabel("目标活动周")
    ax.set_ylabel("峰值（13 个月平滑 SN）")
    ax.set_title("H2 严格滚动回测与 SC25 暂定前瞻检查")
    ax.legend(frameon=False, ncol=2, fontsize=8)
    ax.grid(alpha=0.25)
    ax.set_xticks(t + ([25] if p else []))
    save(fig, args.figures, "h2_polar_upgrade_diagnostics")


if __name__ == "__main__":
    main()
