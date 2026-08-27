#!/usr/bin/env python3
"""Leakage-controlled historical backtest and formal Solar Cycle 26 forecast.

The primary target is the SILSO v2.0 13-month-smoothed peak.  Two transparent
models are evaluated: (1) same-cycle early-rise slope and (2) next-cycle
amplitude from the preceding cycle's peak.  All historical predictions use an
expanding time-ordered training set; Cycle 26 is forecast only after the
historical audit is complete.  This script is intentionally standalone so its
JSON, CSV, Markdown and PNG outputs can be independently re-computed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager, rcParams
import numpy as np
import pandas as pd

from build_solar_cycle_asof_features import (
    load_monthly_total,
    load_official_cycles,
    load_smoothed_total,
)

SEED = 20260827
BOOTSTRAP_REPS = 10_000

_CJK_FONT = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
if _CJK_FONT.exists():
    font_manager.fontManager.addfont(str(_CJK_FONT))
rcParams["font.family"] = "Noto Sans CJK JP"
rcParams["axes.unicode_minus"] = False


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def month_offset(start: pd.Timestamp, date: pd.Timestamp) -> int:
    return (date.year - start.year) * 12 + date.month - start.month


def slope(x: np.ndarray, y: np.ndarray) -> float:
    return float(np.polyfit(x, y, 1)[0])


def fit_predict(x: np.ndarray, y: np.ndarray, xt: float) -> float:
    if len(x) < 2 or np.allclose(x, x[0]):
        return float(np.mean(y))
    return float(np.polyval(np.polyfit(x, y, 1), xt))


def fit_predict_multi(x: np.ndarray, y: np.ndarray, xt: np.ndarray) -> float:
    design = np.column_stack([np.ones(len(x)), x])
    beta = np.linalg.lstsq(design, y, rcond=None)[0]
    return float(np.r_[1.0, xt] @ beta)


def percentile_ci(values: np.ndarray) -> list[float]:
    return [float(v) for v in np.quantile(values, [0.025, 0.975])]


def bootstrap_difference(
    candidate_error: np.ndarray,
    baseline_error: np.ndarray,
    rng: np.random.Generator,
) -> tuple[float, list[float]]:
    n = len(candidate_error)
    draws = np.empty(BOOTSTRAP_REPS)
    for i in range(BOOTSTRAP_REPS):
        idx = rng.integers(0, n, n)
        draws[i] = np.mean(baseline_error[idx]) - np.mean(candidate_error[idx])
    return float(np.mean(baseline_error) - np.mean(candidate_error)), percentile_ci(draws)


def build_cycle_table(monthly: pd.DataFrame, smoothed: pd.DataFrame, extrema_path: Path) -> pd.DataFrame:
    official = load_official_cycles(extrema_path)
    raw = monthly.set_index("date")["sn"]
    smooth = smoothed.set_index("date")["sn"]
    rows: list[dict[str, object]] = []
    for cycle in range(1, 25):
        record = official.get(cycle)
        if record is None or record.maximum is None:
            raise ValueError(f"official extrema missing completed cycle {cycle}")
        offsets = np.arange(7, 25)
        dates = [record.minimum.date + pd.DateOffset(months=int(m)) for m in offsets]
        if any(d not in raw.index for d in dates):
            raise ValueError(f"raw monthly input missing early-rise months for cycle {cycle}")
        rows.append(
            {
                "cycle": cycle,
                "minimum_date": record.minimum.date,
                "maximum_date": record.maximum.date,
                "peak": float(record.maximum.sunspot_number),
                "rise_slope": slope(offsets.astype(float), raw.loc[dates].to_numpy(float)),
                "rise_months": int(month_offset(record.minimum.date, record.maximum.date)),
            }
        )
    # Cycle 25 is a predictor for C26. Its maximum is obtained from the
    # official smoothed series (the official extrema table has no C25 maximum).
    c25 = official.get(25)
    if c25 is None:
        raise ValueError("official extrema table has no Cycle 25 minimum")
    c25_offsets = np.arange(7, 25)
    c25_dates = [c25.minimum.date + pd.DateOffset(months=int(m)) for m in c25_offsets]
    c25_peak_window = smoothed[(smoothed["date"] >= c25.minimum.date) & (smoothed["date"] <= smoothed["date"].max())]
    c25_peak_row = c25_peak_window.loc[c25_peak_window["sn"].idxmax()]
    rows.append(
        {
            "cycle": 25,
            "minimum_date": c25.minimum.date,
            "maximum_date": pd.Timestamp(c25_peak_row["date"]),
            "peak": float(c25_peak_row["sn"]),
            "rise_slope": slope(c25_offsets.astype(float), raw.loc[c25_dates].to_numpy(float)),
            "rise_months": int(month_offset(c25.minimum.date, pd.Timestamp(c25_peak_row["date"]))),
        }
    )
    return pd.DataFrame(rows)


def same_cycle_backtest(cycles: pd.DataFrame, rng: np.random.Generator) -> tuple[pd.DataFrame, dict[str, object]]:
    train_end = 12
    rows = []
    for idx in range(train_end, 24):
        train = cycles.iloc[:idx]
        test = cycles.iloc[idx]
        pred = fit_predict(train["rise_slope"].to_numpy(float), train["peak"].to_numpy(float), float(test["rise_slope"]))
        baseline = float(train["peak"].mean())
        rows.append({"cycle": int(test["cycle"]), "observed_peak": float(test["peak"]), "candidate_prediction": pred, "baseline_prediction": baseline})
    frame = pd.DataFrame(rows)
    ce = np.abs(frame.observed_peak - frame.candidate_prediction).to_numpy()
    be = np.abs(frame.observed_peak - frame.baseline_prediction).to_numpy()
    improvement, ci = bootstrap_difference(ce, be, rng)
    return frame, {"test_cycles": len(frame), "candidate_mae": float(ce.mean()), "baseline_mae": float(be.mean()), "mae_improvement": improvement, "mae_improvement_ci95": ci, "candidate_wins": int((ce < be).sum())}


def next_cycle_backtest(cycles: pd.DataFrame, rng: np.random.Generator, model: str) -> tuple[pd.DataFrame, dict[str, object]]:
    # target t uses predecessor t-1; test starts at t=10 so each fit has >=8 pairs.
    rows = []
    for target in range(10, 25):
        train_targets = np.arange(2, target)
        train_prev = cycles[cycles.cycle.isin(train_targets - 1)].sort_values("cycle")
        train_y = cycles[cycles.cycle.isin(train_targets)].sort_values("cycle")["peak"].to_numpy(float)
        test_prev = cycles[cycles.cycle == target - 1].iloc[0]
        if model == "lag_peak":
            x = train_prev["peak"].to_numpy(float)
            xt = float(test_prev["peak"])
            pred = fit_predict(x, train_y, xt)
        elif model == "lag_peak_rise":
            x = train_prev[["peak", "rise_slope"]].to_numpy(float)
            xt = test_prev[["peak", "rise_slope"]].to_numpy(float)
            pred = fit_predict_multi(x, train_y, xt)
        else:
            raise ValueError(model)
        baseline = float(train_y.mean())
        observed = float(cycles[cycles.cycle == target]["peak"].iloc[0])
        rows.append({"cycle": target, "observed_peak": observed, "candidate_prediction": pred, "baseline_prediction": baseline})
    frame = pd.DataFrame(rows)
    ce = np.abs(frame.observed_peak - frame.candidate_prediction).to_numpy()
    be = np.abs(frame.observed_peak - frame.baseline_prediction).to_numpy()
    improvement, ci = bootstrap_difference(ce, be, rng)
    return frame, {"test_cycles": len(frame), "candidate_mae": float(ce.mean()), "baseline_mae": float(be.mean()), "candidate_rmse": float(np.sqrt(np.mean((frame.observed_peak-frame.candidate_prediction)**2))), "baseline_rmse": float(np.sqrt(np.mean((frame.observed_peak-frame.baseline_prediction)**2))), "mae_improvement": improvement, "mae_improvement_ci95": ci, "candidate_wins": int((ce < be).sum())}


def formal_forecast(cycles: pd.DataFrame, rng: np.random.Generator) -> dict[str, object]:
    train = cycles[cycles.cycle <= 24].copy()
    prev = cycles[cycles.cycle == 25].iloc[0]
    targets = train[train.cycle >= 2]
    prev_rows = train[train.cycle <= 23]
    y = targets.sort_values("cycle")["peak"].to_numpy(float)
    x_peak = prev_rows.sort_values("cycle")["peak"].to_numpy(float)
    x_rise = prev_rows.sort_values("cycle")["rise_slope"].to_numpy(float)
    pred_peak = fit_predict(x_peak, y, float(prev["peak"]))
    pred_both = fit_predict_multi(np.column_stack([x_peak, x_rise]), y, np.array([prev["peak"], prev["rise_slope"]]))
    residual = y - np.polyval(np.polyfit(x_peak, y, 1), x_peak)
    draws = np.empty(BOOTSTRAP_REPS)
    for i in range(BOOTSTRAP_REPS):
        idx = rng.integers(0, len(y), len(y))
        beta = np.polyfit(x_peak[idx], y[idx], 1)
        draws[i] = max(0.0, float(np.polyval(beta, prev["peak"]) + rng.choice(residual[idx])))
    return {"target": "Cycle 26 peak 13-month-smoothed SILSO v2 sunspot number", "as_of": "2026-08-27", "primary_model": "expanding-history linear regression: predecessor peak -> next-cycle peak", "cycle_25_peak_used": float(prev["peak"]), "cycle_25_rise_slope_used": float(prev["rise_slope"]), "point_estimate": pred_peak, "predictive_interval_95": percentile_ci(draws), "sensitivity_lag_peak_rise": pred_both, "climatology_mean_cycles_1_24": float(train.peak.mean()), "bootstrap_seed": SEED, "bootstrap_repetitions": BOOTSTRAP_REPS, "confidence": "low", "interpretation": "Historical backtests do not establish positive skill over the mean baseline; the interval is deliberately wide and the estimate is a conditional statistical forecast, not a physical certainty."}


def make_figure(cycles: pd.DataFrame, same: pd.DataFrame, lag: pd.DataFrame, lag_both: pd.DataFrame, forecast: dict[str, object], path: Path) -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    fig, axes = plt.subplots(1, 3, figsize=(18, 5.6), constrained_layout=True)
    colors = {"actual": "#1b4f72", "candidate": "#d35400", "baseline": "#7f8c8d", "forecast": "#7d3c98"}
    ax = axes[0]
    ax.plot(lag.cycle, lag.observed_peak, "o-", color=colors["actual"], label="Observed peak")
    ax.plot(lag.cycle, lag.candidate_prediction, "s--", color=colors["candidate"], label="Lag-peak model")
    ax.plot(lag.cycle, lag.baseline_prediction, ":", color=colors["baseline"], label="Training-mean baseline")
    for _, r in lag.iterrows(): ax.annotate(f"SC{int(r.cycle)}", (r.cycle, r.observed_peak), xytext=(0, 6), textcoords="offset points", fontsize=7, ha="center")
    ax.set_title("Time-ordered backtest (SC10–24)"); ax.set_xlabel("Target cycle"); ax.set_ylabel("Peak (13-month smoothed SN)"); ax.legend(fontsize=8)
    ax = axes[1]
    ax.scatter(same.candidate_prediction, same.observed_peak, color=colors["candidate"], label="Same-cycle rise model")
    ax.scatter(lag.candidate_prediction, lag.observed_peak, color=colors["actual"], marker="D", label="Lag-peak model")
    lim = [0, max(cycles.peak.max(), same.observed_peak.max()) * 1.08]
    ax.plot(lim, lim, "k--", lw=1, label="Perfect prediction")
    ax.set_title("Predicted vs observed (closer to dashed line is better)"); ax.set_xlabel("Predicted peak"); ax.set_ylabel("Observed peak"); ax.legend(fontsize=8)
    ax = axes[2]
    labels = ["Lag peak", "Lag peak + rise", "Historical mean"]
    vals = [float(forecast["point_estimate"]), float(forecast["sensitivity_lag_peak_rise"]), float(forecast["climatology_mean_cycles_1_24"])]
    ax.bar(labels, vals, color=[colors["forecast"], "#9b59b6", colors["baseline"]])
    lo, hi = forecast["predictive_interval_95"]
    ax.errorbar([0], [vals[0]], yerr=[[vals[0]-lo], [hi-vals[0]]], fmt="none", ecolor="black", capsize=6, lw=2)
    ax.axhline(float(cycles[cycles.cycle == 25].peak.iloc[0]), color="#16a085", ls="--", label="SC25 official smoothed peak")
    ax.set_title("Formal Cycle 26 forecast (as of 2026-08-27)"); ax.set_ylabel("Peak (13-month smoothed SN)"); ax.tick_params(axis="x", labelrotation=18); ax.legend(fontsize=8)
    fig.suptitle("JW Solar Cycle Peak Strength: Backtest and Cycle 26 Forecast", fontsize=15, fontweight="bold")
    fig.savefig(path, dpi=300, bbox_inches="tight"); plt.close(fig)


def write_report(out: Path, cycles: pd.DataFrame, same_stats: dict[str, object], lag_stats: dict[str, object], both_stats: dict[str, object], forecast: dict[str, object], sources: dict[str, object]) -> None:
    table = cycles.to_markdown(index=False)
    report = f"""# 第26太阳活动周：历史回测与正式统计预测

## 结论先行

截至 2026-08-27，主模型给出的第26周 13个月平滑太阳黑子数峰值点估计为 **{forecast['point_estimate']:.1f}**，95%预测区间为 **[{forecast['predictive_interval_95'][0]:.1f}, {forecast['predictive_interval_95'][1]:.1f}]**。置信度为**低**：SC10–24 时间顺序回测中，前一周峰值模型 MAE 为 {lag_stats['candidate_mae']:.1f}，训练均值基线为 {lag_stats['baseline_mae']:.1f}，改进 {lag_stats['mae_improvement']:.1f}，bootstrap 95%区间 [{lag_stats['mae_improvement_ci95'][0]:.1f}, {lag_stats['mae_improvement_ci95'][1]:.1f}]；区间跨过零，不能声称稳定预测技能。

## 数据与可复核性

使用在线取得并保存的 WDC-SILSO Version 2.0 月度总太阳黑子数、13个月平滑序列和官方活动周极值表。输入文件、SHA-256、下载日期、脚本命令和标准输出见同目录 `session_log.md` 与 `data_manifest.json`。SC1–24 是完整历史样本；SC25 只作为第26周预测的已知前驱，峰值从官方平滑序列中重建，未把SC25当完整回测目标。

来源： [SILSO monthly total](https://www.sidc.be/SILSO/DATA/SN_m_tot_V2.0.txt)、[SILSO smoothed monthly](https://www.sidc.be/SILSO/DATA/SN_ms_tot_V2.0.csv)、[SILSO official cycle extrema](https://www.sidc.be/SILSO/DATA/Cycles/TableCyclesMiMa.txt)。

## 方法

1. **同周早期上升率**：官方谷值后第7–24个月原始月均值的 OLS 斜率；SC13–24 为扩展窗口时间顺序回测。
2. **跨周主模型**：用前一活动周峰值预测下一活动周峰值；SC10–24 为扩展窗口回测，训练均值为预先固定基线。正式SC26预测只在历史回测完成后，用SC25峰值拟合全SC2–24目标。
3. **不确定性**：固定随机种子 `{SEED}`，以活动周为重采样单位进行 {BOOTSTRAP_REPS:,} 次bootstrap。预测区间还叠加了历史残差，避免把回归均值误当作未来确定值。

## 逐周期数据

{table}

## 回测结果

| 关系/模型 | 测试周数 | 候选 MAE | 基线 MAE | MAE 改进 | 改进95%区间 | 胜出次数 |
|---|---:|---:|---:|---:|---|---:|
| 同周早期上升率 → 峰值 | {same_stats['test_cycles']} | {same_stats['candidate_mae']:.1f} | {same_stats['baseline_mae']:.1f} | {same_stats['mae_improvement']:.1f} | [{same_stats['mae_improvement_ci95'][0]:.1f}, {same_stats['mae_improvement_ci95'][1]:.1f}] | {same_stats['candidate_wins']} |
| 前一周峰值 → 下一周峰值 | {lag_stats['test_cycles']} | {lag_stats['candidate_mae']:.1f} | {lag_stats['baseline_mae']:.1f} | {lag_stats['mae_improvement']:.1f} | [{lag_stats['mae_improvement_ci95'][0]:.1f}, {lag_stats['mae_improvement_ci95'][1]:.1f}] | {lag_stats['candidate_wins']} |
| 前一周峰值+上升率 → 下一周峰值 | {both_stats['test_cycles']} | {both_stats['candidate_mae']:.1f} | {both_stats['baseline_mae']:.1f} | {both_stats['mae_improvement']:.1f} | [{both_stats['mae_improvement_ci95'][0]:.1f}, {both_stats['mae_improvement_ci95'][1]:.1f}] | {both_stats['candidate_wins']} |

同周早期上升率关系可作为历史预测能力检验，但不能把相关性表述为发电机因果机制。跨周模型的回测优势若不稳定，就只能作为条件统计基线。

## 第26周正式预测

- 主模型：`前一周峰值 → 下一周峰值`；SC25峰值输入 **{forecast['cycle_25_peak_used']:.1f}**，SC25早期上升斜率输入 **{forecast['cycle_25_rise_slope_used']:.3f} SN/月**。
- 点估计：**{forecast['point_estimate']:.1f}**。
- 95%预测区间：**[{forecast['predictive_interval_95'][0]:.1f}, {forecast['predictive_interval_95'][1]:.1f}]**。
- 敏感性模型（前一周峰值+上升率）：**{forecast['sensitivity_lag_peak_rise']:.1f}**；SC1–24历史均值：**{forecast['climatology_mean_cycles_1_24']:.1f}**。

该结果是截至日期冻结数据下的正式、可复核统计预测。它不是对SC26真实峰值的已验证陈述；真实评估必须等待SC26完成并获得官方极值标签。

## 图表与产物

![历史回测与第26周预测](sc26_forecast_visualization.png)

- `sc26_forecast_predictions.csv`：逐周预测、实际值和基线；
- `sc26_formal_forecast.json`：点估计、区间、方法和置信度；
- `data_manifest.json`：来源、版本、哈希和覆盖范围；
- `session_log.md`：本次会话输入、命令、输出、审计和截图记录。

## 限制

样本只有24个完整活动周，回测测试折仅15个；活动周不是独立同分布样本，早期历史观测质量也不完全一致。未使用极区磁场、F10.7或未经登记的替代数据，也没有把第25周当作完整周期样本。预测区间反映模型与历史误差，不等同于物理机制的不确定性；本实验不能证明太阳发电机因果机制。
"""
    (out / "sc26_historical_backtest_report.md").write_text(report, encoding="utf-8")
    (out / "sc26_formal_forecast_report.md").write_text(report, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--monthly", required=True)
    ap.add_argument("--smoothed", required=True)
    ap.add_argument("--extrema", required=True)
    ap.add_argument("--output-dir", required=True)
    args = ap.parse_args()
    monthly_path, smoothed_path, extrema_path = map(lambda x: Path(x).resolve(), (args.monthly, args.smoothed, args.extrema))
    out = Path(args.output_dir).resolve(); out.mkdir(parents=True, exist_ok=True)
    monthly, smoothed = load_monthly_total(monthly_path), load_smoothed_total(smoothed_path)
    cycles = build_cycle_table(monthly, smoothed, extrema_path)
    rng = np.random.default_rng(SEED)
    same, same_stats = same_cycle_backtest(cycles, rng)
    lag, lag_stats = next_cycle_backtest(cycles, rng, "lag_peak")
    lag_both, both_stats = next_cycle_backtest(cycles, rng, "lag_peak_rise")
    forecast = formal_forecast(cycles, rng)
    cycles.to_csv(out / "sc26_cycle_features.csv", index=False, date_format="%Y-%m-%d")
    pd.concat([lag.assign(model="lag_peak"), lag_both.assign(model="lag_peak_rise"), same.assign(model="same_cycle_rise")], ignore_index=True).to_csv(out / "sc26_forecast_predictions.csv", index=False)
    (out / "sc26_formal_forecast.json").write_text(json.dumps(forecast, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sources = {"retrieved": "2026-08-27", "monthly": {"path": str(monthly_path), "sha256": sha256(monthly_path)}, "smoothed": {"path": str(smoothed_path), "sha256": sha256(smoothed_path)}, "extrema": {"path": str(extrema_path), "sha256": sha256(extrema_path)}}
    (out / "data_manifest.json").write_text(json.dumps(sources, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    make_figure(cycles, same, lag, lag_both, forecast, out / "sc26_forecast_visualization.png")
    write_report(out, cycles, same_stats, lag_stats, both_stats, forecast, sources)
    summary = {"output_dir": str(out), "cycles": len(cycles), "same_cycle": same_stats, "lag_peak": lag_stats, "lag_peak_rise": both_stats, "forecast": forecast}
    (out / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
