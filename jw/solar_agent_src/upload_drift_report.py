from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from build_drift_report import (
    _build_cycle_metric_series,
    _indicator_from_metrics,
    _residual_drift_indicator,
)


ROOT = Path(__file__).resolve().parents[1]


def _build_drift_report_from_dataframes(
    master: pd.DataFrame,
    cycle_features: pd.DataFrame,
    semantic_map: dict[str, str],
    window: int = 4,
) -> dict[str, Any]:
    master = master.copy()
    master["date_month"] = pd.to_datetime(master["date_month"], errors="coerce")
    master = master.dropna(subset=["date_month"])

    if "cycle_no" not in master.columns and "cycle_number" not in master.columns:
        from build_interim_monthly import add_cycle_columns, read_cycles

        cycles_meta = read_cycles()
        master = add_cycle_columns(master, cycles_meta)
    if "cycle_no" not in master.columns and "cycle_number" in master.columns:
        master = master.rename(columns={"cycle_number": "cycle_no"})

    cycle_features = cycle_features.copy()
    if not cycle_features.empty and "cycle_no" in cycle_features.columns:
        cycle_features = cycle_features.set_index("cycle_no")

    indicators: list[dict[str, Any]] = []

    sunspot_col = next((c for c, s in semantic_map.items() if s == "sunspot"), None)
    f107_col = next((c for c, s in semantic_map.items() if s == "f107"), None)
    polar_cols = [c for c, s in semantic_map.items() if s == "polar"]
    hemisphere_cols = [c for c, s in semantic_map.items() if s == "hemisphere"]

    # 1. F10.7 vs sunspot relation
    if sunspot_col and f107_col and not master.empty:
        if sunspot_col in master.columns and f107_col in master.columns:
            f107_metrics = _build_cycle_metric_series(master, sunspot_col, f107_col)
            if not f107_metrics.empty:
                metric_cols = [
                    f"{sunspot_col}_{f107_col}_corr",
                    f"{sunspot_col}_{f107_col}_slope",
                    f"{sunspot_col}_{f107_col}_residual_std",
                ]
                indicators.append(
                    _indicator_from_metrics(
                        f107_metrics,
                        metric_cols,
                        indicator_id="f107_sunspot_relation",
                        name="F10.7-太阳黑子数关系",
                        description="检测上传数据中 F10.7 与太阳黑子数的相关性、斜率和残差是否跨周期漂移。",
                        recommended_action="若关系显著漂移，建议降低依赖 F10.7 的模型置信度。",
                        window=window,
                    )
                )

    # 2. Waldmeier: rise slope vs peak
    if (
        not cycle_features.empty
        and "rise_slope" in cycle_features.columns
        and "peak_sunspot_number" in cycle_features.columns
    ):
        indicators.append(
            _residual_drift_indicator(
                cycle_features,
                x_col="rise_slope",
                y_col="peak_sunspot_number",
                indicator_id="rise_slope_vs_peak",
                name="上升斜率-周期峰值关系（Waldmeier 效应）",
                description="检测上传数据中的 Waldmeier 效应是否稳定。",
                recommended_action="若残差显著漂移，降低基于 Waldmeier 效应的峰值预测置信度。",
                window=window,
            )
        )

    # 3. Previous cycle length / peak vs next cycle peak
    if not cycle_features.empty and "next_cycle_peak_sunspot" in cycle_features.columns:
        prev = cycle_features.shift(1)
        pair = cycle_features[["next_cycle_peak_sunspot"]].copy()
        if "cycle_length_months" in cycle_features.columns:
            pair["prev_cycle_length_months"] = prev["cycle_length_months"]
            pair = pair.dropna(
                subset=["prev_cycle_length_months", "next_cycle_peak_sunspot"]
            )
            if not pair.empty:
                indicators.append(
                    _residual_drift_indicator(
                        pair,
                        x_col="prev_cycle_length_months",
                        y_col="next_cycle_peak_sunspot",
                        indicator_id="previous_cycle_length_vs_next_peak",
                        name="前一周长度与下一周峰值关系",
                        description="检测上传数据中前一周长度对下一周峰值的预测关系是否稳定。",
                        recommended_action="若关系漂移，说明周期长度不是稳定的下一周期强度预测器。",
                        window=window,
                    )
                )
        if "peak_sunspot_number" in cycle_features.columns:
            pair2 = cycle_features[["next_cycle_peak_sunspot"]].copy()
            pair2["prev_peak_sunspot_number"] = prev["peak_sunspot_number"]
            pair2 = pair2.dropna(
                subset=["prev_peak_sunspot_number", "next_cycle_peak_sunspot"]
            )
            if not pair2.empty:
                indicators.append(
                    _residual_drift_indicator(
                        pair2,
                        x_col="prev_peak_sunspot_number",
                        y_col="next_cycle_peak_sunspot",
                        indicator_id="previous_peak_vs_next_peak",
                        name="前一周峰值与下一周峰值关系",
                        description="检测上传数据中前一周峰值强度对下一周峰值强度的预测关系是否稳定。",
                        recommended_action="若关系漂移，说明相邻周期强度之间的记忆效应不稳定。",
                        window=window,
                    )
                )

    # 4. Polar precursor vs next cycle peak
    if (
        polar_cols
        and not cycle_features.empty
        and "next_cycle_peak_sunspot" in cycle_features.columns
    ):
        for polar_col in polar_cols:
            precursor_col = f"{polar_col}_precursor_mean"
            if precursor_col in cycle_features.columns:
                sub = cycle_features[
                    ["next_cycle_peak_sunspot", precursor_col]
                ].dropna()
                if not sub.empty:
                    indicators.append(
                        _residual_drift_indicator(
                            sub,
                            x_col=precursor_col,
                            y_col="next_cycle_peak_sunspot",
                            indicator_id=f"polar_precursor_{polar_col}_vs_next_peak",
                            name=f"极区前兆（{polar_col}）与下一周峰值关系",
                            description="检测上传数据中的极区前兆对下一周峰值的预测关系是否稳定。",
                            recommended_action="若关系漂移，降低基于极区前兆假设的预测置信度。",
                            window=window,
                        )
                    )

    # 5. Hemispheric asymmetry vs peak
    if (
        hemisphere_cols
        and sunspot_col
        and not cycle_features.empty
        and "peak_sunspot_number" in cycle_features.columns
    ):
        for hem_col in hemisphere_cols:
            asym_col = f"{hem_col}_mean"
            if asym_col in cycle_features.columns:
                sub = cycle_features[["peak_sunspot_number", asym_col]].dropna()
                if not sub.empty:
                    indicators.append(
                        _residual_drift_indicator(
                            sub,
                            x_col=asym_col,
                            y_col="peak_sunspot_number",
                            indicator_id=f"hemispheric_asymmetry_{hem_col}_vs_peak",
                            name=f"半球不对称（{hem_col}）与周期峰值关系",
                            description="检测上传数据中的半球不对称与周期峰值强度的关系是否稳定。",
                            recommended_action="若关系漂移，谨慎解释半球不对称与周期强度的关系。",
                            window=window,
                        )
                    )

    # Confidence recommendations
    recommendations: list[str] = []
    significant_ids = [
        ind["id"]
        for ind in indicators
        if any(
            c.get("drift_flag") == "significant_drift" for c in ind.get("cycles", [])
        )
    ]
    if "f107_sunspot_relation" in significant_ids:
        recommendations.append("降低依赖 F10.7 作为单一输入的模型置信度。")
    if "rise_slope_vs_peak" in significant_ids:
        recommendations.append("降低基于 Waldmeier 效应的峰值预测置信度。")
    if any(
        i in significant_ids
        for i in ["previous_cycle_length_vs_next_peak", "previous_peak_vs_next_peak"]
    ):
        recommendations.append("降低基于相邻周期记忆效应的预测置信度。")
    if any(i.startswith("polar_precursor_") for i in significant_ids):
        recommendations.append("降低基于极区前兆假设的预测置信度。")
    if any(i.startswith("hemispheric_asymmetry_") for i in significant_ids):
        recommendations.append("谨慎解释半球不对称与周期峰值的关系。")
    if not recommendations:
        recommendations.append(
            "当前上传数据未检测到显著漂移，但仍需关注数据覆盖和辅助代理局限。"
        )

    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "window_cycles": window,
        "drift_indicators": indicators,
        "confidence_recommendations": recommendations,
    }


def run(
    master: pd.DataFrame,
    cycle_features: pd.DataFrame,
    semantic_map: dict[str, str],
    output_path: Path | None = None,
    window: int = 4,
) -> dict[str, Any]:
    """Build and save drift report for uploaded data."""
    report = _build_drift_report_from_dataframes(
        master, cycle_features, semantic_map, window=window
    )
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        report["path"] = str(output_path.relative_to(ROOT)).replace("\\", "/")
    return report
