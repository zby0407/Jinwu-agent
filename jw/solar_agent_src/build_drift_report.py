from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = ROOT / "data" / "processed"
OUTPUT_PATH = PROCESSED_DIR / "drift_report.json"


def _rolling_baseline(
    series: pd.Series, window: int = 4
) -> tuple[pd.Series, pd.Series]:
    """Return rolling mean/std of previous `window` cycles for each index value."""
    means = {}
    stds = {}
    for idx in series.index:
        prev = series[series.index < idx].tail(window)
        means[idx] = prev.mean() if len(prev) else np.nan
        stds[idx] = prev.std(ddof=1) if len(prev) > 1 else np.nan
    return pd.Series(means), pd.Series(stds)


def _drift_flag(value: float, baseline_mean: float, baseline_std: float) -> str:
    if (
        pd.isna(value)
        or pd.isna(baseline_mean)
        or pd.isna(baseline_std)
        or baseline_std == 0
    ):
        return "insufficient_data"
    z = abs(float((value - baseline_mean) / baseline_std))
    if z < 1.0:
        return "stable"
    if z < 2.0:
        return "mild_drift"
    return "significant_drift"


def _linear_regression_slope(x: pd.Series, y: pd.Series) -> float:
    valid = x.notna() & y.notna()
    if valid.sum() < 2:
        return np.nan
    slope, _ = np.polyfit(x[valid].astype(float), y[valid].astype(float), 1)
    return float(slope)


def _build_cycle_metric_series(
    master: pd.DataFrame, x_col: str, y_col: str
) -> pd.DataFrame:
    """Compute per-cycle linear relationship metrics between two monthly columns."""
    rows = []
    for cycle_no, group in master.groupby("cycle_no"):
        group = group.dropna(subset=[x_col, y_col])
        if len(group) < 6:
            continue
        x = group[x_col].astype(float)
        y = group[y_col].astype(float)
        corr = x.corr(y)
        if x.nunique() < 2:
            slope = np.nan
            residual_std = np.nan
        else:
            coef = np.polyfit(x, y, 1)
            slope = float(coef[0])
            pred = slope * x + coef[1]
            residual_std = float((y - pred).std())
        rows.append(
            {
                "cycle_no": cycle_no,
                f"{x_col}_{y_col}_corr": corr,
                f"{x_col}_{y_col}_slope": slope,
                f"{x_col}_{y_col}_residual_std": residual_std,
            }
        )
    if not rows:
        return pd.DataFrame(columns=["cycle_no"]).set_index("cycle_no")
    return pd.DataFrame(rows).set_index("cycle_no")


def _indicator_from_metrics(
    metrics: pd.DataFrame,
    metric_cols: list[str],
    indicator_id: str,
    name: str,
    description: str,
    recommended_action: str,
    window: int = 4,
) -> dict[str, Any]:
    cycles_out = []
    flag_counts = {
        "stable": 0,
        "mild_drift": 0,
        "significant_drift": 0,
        "insufficient_data": 0,
    }
    for cycle in metrics.index:
        cycle_flags = {}
        values = {}
        z_scores = {}
        for col in metric_cols:
            value = metrics.loc[cycle, col]
            baseline_mean, baseline_std = _rolling_baseline(metrics[col], window)
            mean = baseline_mean.loc[cycle]
            std = baseline_std.loc[cycle]
            flag = _drift_flag(value, mean, std)
            cycle_flags[col] = flag
            values[col] = None if pd.isna(value) else round(float(value), 4)
            z_scores[col] = (
                None
                if pd.isna(value) or pd.isna(mean) or pd.isna(std) or std == 0
                else round(float((value - mean) / std), 4)
            )
            flag_counts[flag] = flag_counts.get(flag, 0) + 1
        # overall flag for the cycle: worst among metrics
        if "significant_drift" in cycle_flags.values():
            overall = "significant_drift"
        elif "mild_drift" in cycle_flags.values():
            overall = "mild_drift"
        elif "stable" in cycle_flags.values():
            overall = "stable"
        else:
            overall = "insufficient_data"
        cycles_out.append(
            {
                "cycle_no": int(cycle),
                "values": values,
                "z_scores": z_scores,
                "metric_flags": cycle_flags,
                "drift_flag": overall,
            }
        )

    overall_trend = {}
    for col in metric_cols:
        valid = metrics[col].dropna()
        if len(valid) >= 3:
            slope = _linear_regression_slope(
                pd.Series(valid.index, index=valid.index), valid
            )
            overall_trend[col] = {
                "trend_slope_over_cycle": round(float(slope), 6)
                if not pd.isna(slope)
                else None,
                "interpretation": (
                    "increasing"
                    if slope > 0.01
                    else "decreasing"
                    if slope < -0.01
                    else "flat"
                ),
            }

    return {
        "id": indicator_id,
        "name": name,
        "description": description,
        "window_cycles": window,
        "metric_columns": metric_cols,
        "cycles": cycles_out,
        "flag_counts": flag_counts,
        "overall_trend": overall_trend,
        "recommended_action": recommended_action,
    }


def _residual_drift_indicator(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    indicator_id: str,
    name: str,
    description: str,
    recommended_action: str,
    window: int = 4,
) -> dict[str, Any]:
    """Compute residual drift for a y ~ x relationship using previous cycles as baseline."""
    residuals = {}
    for cycle in df.index:
        prev = df.loc[df.index < cycle]
        valid = prev[[x_col, y_col]].dropna()
        if len(valid) < 3:
            residuals[cycle] = np.nan
            continue
        x = valid[x_col].astype(float)
        y = valid[y_col].astype(float)
        if x.nunique() < 2:
            residuals[cycle] = np.nan
            continue
        slope, intercept = np.polyfit(x, y, 1)
        current_x = df.loc[cycle, x_col]
        current_y = df.loc[cycle, y_col]
        if pd.isna(current_x) or pd.isna(current_y):
            residuals[cycle] = np.nan
            continue
        pred = slope * float(current_x) + intercept
        residuals[cycle] = float(current_y - pred)

    residual_series = pd.Series(residuals)
    cycles_out = []
    flag_counts = {
        "stable": 0,
        "mild_drift": 0,
        "significant_drift": 0,
        "insufficient_data": 0,
    }
    for cycle in residual_series.index:
        value = residual_series.loc[cycle]
        baseline_mean, baseline_std = _rolling_baseline(residual_series, window)
        mean = baseline_mean.loc[cycle]
        std = baseline_std.loc[cycle]
        flag = _drift_flag(value, mean, std)
        flag_counts[flag] = flag_counts.get(flag, 0) + 1
        cycles_out.append(
            {
                "cycle_no": int(cycle),
                "x_value": None
                if pd.isna(df.loc[cycle, x_col])
                else round(float(df.loc[cycle, x_col]), 4),
                "y_value": None
                if pd.isna(df.loc[cycle, y_col])
                else round(float(df.loc[cycle, y_col]), 4),
                "residual": None if pd.isna(value) else round(float(value), 4),
                "z_score": None
                if pd.isna(value) or pd.isna(mean) or pd.isna(std) or std == 0
                else round(float((value - mean) / std), 4),
                "drift_flag": flag,
            }
        )

    return {
        "id": indicator_id,
        "name": name,
        "description": description,
        "window_cycles": window,
        "x_col": x_col,
        "y_col": y_col,
        "cycles": cycles_out,
        "flag_counts": flag_counts,
        "recommended_action": recommended_action,
    }


def build_drift_report(
    master_path: Path | None = None,
    cycle_features_path: Path | None = None,
    output_path: Path | None = None,
    window: int = 4,
) -> dict[str, Any]:
    master_path = master_path or PROCESSED_DIR / "clean_monthly_timeseries.csv"
    cycle_features_path = cycle_features_path or PROCESSED_DIR / "cycle_features.csv"
    output_path = output_path or OUTPUT_PATH

    master = pd.read_csv(master_path, parse_dates=["date_month"])
    cycles = pd.read_csv(cycle_features_path)
    cycles = cycles.set_index("cycle_no")

    indicators = []

    # 1. F10.7 vs sunspot relation
    f107_metrics = _build_cycle_metric_series(
        master, "sunspot_number", "f107_monthly_mean"
    )
    if not f107_metrics.empty:
        indicators.append(
            _indicator_from_metrics(
                f107_metrics,
                [
                    "sunspot_number_f107_monthly_mean_corr",
                    "sunspot_number_f107_monthly_mean_slope",
                    "sunspot_number_f107_monthly_mean_residual_std",
                ],
                indicator_id="f107_sunspot_relation",
                name="F10.7-太阳黑子数关系",
                description="检测每个太阳活动周内 F10.7 与太阳黑子数的相关性、斜率和残差是否偏离此前周期滑动基线。",
                recommended_action="如果某些周期显著漂移，建议降低单纯依赖 F10.7 作为代理指标的模型置信度，并补充黑子数主证据。",
                window=window,
            )
        )

    # 2. Rise slope vs peak (Waldmeier effect)
    if "rise_slope" in cycles.columns and "peak_sunspot_number" in cycles.columns:
        indicators.append(
            _residual_drift_indicator(
                cycles,
                x_col="rise_slope",
                y_col="peak_sunspot_number",
                indicator_id="rise_slope_vs_peak",
                name="上升斜率-周期峰值关系（Waldmeier 效应）",
                description="用历史周期的上升斜率预测当前周期峰值，检测 Waldmeier 效应是否稳定。",
                recommended_action="若残差显著漂移，说明 Waldmeier 关系在当前周期不成立，应降低基于上升斜率的峰值预测置信度。",
                window=window,
            )
        )

    # 3. Previous cycle length / peak vs next cycle peak
    if "next_cycle_peak_sunspot" in cycles.columns:
        prev_cycles = cycles.shift(1).copy()
        prev_cycles.index = cycles.index
        pair = cycles[["next_cycle_peak_sunspot"]].copy()
        pair["prev_cycle_length_months"] = prev_cycles["cycle_length_months"]
        pair["prev_peak_sunspot_number"] = prev_cycles["peak_sunspot_number"]
        pair = pair.dropna(subset=["next_cycle_peak_sunspot"])
        if not pair.empty:
            # length -> next peak
            length_ind = _residual_drift_indicator(
                pair,
                x_col="prev_cycle_length_months",
                y_col="next_cycle_peak_sunspot",
                indicator_id="previous_cycle_length_vs_next_peak",
                name="前一周长度与下一周峰值关系",
                description="检测前一周长度对下一周峰值的预测关系是否稳定。",
                recommended_action="若该关系漂移，说明周期长度不是稳定的下一周期强度预测器。",
                window=window,
            )
            indicators.append(length_ind)
            # previous peak -> next peak
            peak_ind = _residual_drift_indicator(
                pair,
                x_col="prev_peak_sunspot_number",
                y_col="next_cycle_peak_sunspot",
                indicator_id="previous_peak_vs_next_peak",
                name="前一周峰值与下一周峰值关系",
                description="检测前一周峰值强度对下一周峰值强度的预测关系是否稳定。",
                recommended_action="若该关系漂移，说明相邻周期强度之间的记忆效应不稳定。",
                window=window,
            )
            indicators.append(peak_ind)

    # 4. Polar precursor vs next cycle peak
    if (
        "polar_precursor_mean" in cycles.columns
        and "next_cycle_peak_sunspot" in cycles.columns
    ):
        indicators.append(
            _residual_drift_indicator(
                cycles,
                x_col="polar_precursor_mean",
                y_col="next_cycle_peak_sunspot",
                indicator_id="polar_precursor_vs_next_peak",
                name="极区前兆与下一周峰值关系",
                description="检测周期开始前极区磁场对下一周峰值的预测关系是否稳定。",
                recommended_action="若关系漂移，说明极区前兆假设在当前周期证据不足，应降低基于极区前兆的预测置信度。",
                window=window,
            )
        )

    # 5. Hemispheric asymmetry vs peak
    if (
        "hemispheric_asymmetry_mean" in cycles.columns
        and "peak_sunspot_number" in cycles.columns
    ):
        indicators.append(
            _residual_drift_indicator(
                cycles,
                x_col="hemispheric_asymmetry_mean",
                y_col="peak_sunspot_number",
                indicator_id="hemispheric_asymmetry_vs_peak",
                name="半球不对称与周期峰值关系",
                description="检测周期内南北半球不对称与周期峰值强度的关系是否稳定。",
                recommended_action="若关系漂移，说明半球不对称对周期强度的约束不稳定，应谨慎解释。",
                window=window,
            )
        )

    # Confidence recommendations: aggregate from indicators
    recommendations = []
    significant_ids = [
        ind["id"]
        for ind in indicators
        if any(c["drift_flag"] == "significant_drift" for c in ind.get("cycles", []))
    ]
    if "f107_sunspot_relation" in significant_ids:
        recommendations.append("降低依赖 F10.7 作为单一输入的模型置信度。")
    if "rise_slope_vs_peak" in significant_ids:
        recommendations.append("降低基于 Waldmeier 效应的峰值预测置信度。")
    if (
        "previous_cycle_length_vs_next_peak" in significant_ids
        or "previous_peak_vs_next_peak" in significant_ids
    ):
        recommendations.append("降低基于相邻周期记忆效应的预测置信度。")
    if "polar_precursor_vs_next_peak" in significant_ids:
        recommendations.append("降低基于极区前兆假设的预测置信度。")
    if "hemispheric_asymmetry_vs_peak" in significant_ids:
        recommendations.append("谨慎解释半球不对称与周期峰值的关系。")
    if not recommendations:
        recommendations.append(
            "当前周期关系未检测到显著漂移，但仍需关注数据覆盖和辅助代理局限。"
        )

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "window_cycles": window,
        "drift_indicators": indicators,
        "confidence_recommendations": recommendations,
        "data_sources": {
            "master": str(master_path.relative_to(ROOT)).replace("\\", "/"),
            "cycle_features": str(cycle_features_path.relative_to(ROOT)).replace(
                "\\", "/"
            ),
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"saved {output_path}")
    return report


def main() -> None:
    build_drift_report()


if __name__ == "__main__":
    main()
