from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / "data" / "feature_meaning_seed.json"


def _load_seed() -> dict[str, Any]:
    if SEED_PATH.exists():
        return json.loads(SEED_PATH.read_text(encoding="utf-8"))
    return {}


def _pattern_meaning(field: str) -> dict[str, Any]:
    """Infer physical meaning for engineered or unknown fields by naming convention."""
    if "f107_sunspot_zscore" in field:
        return {
            "physical_meaning": "F10.7-黑子数关系偏离历史基线的标准化程度（z-score）",
            "mechanism_link": ["proxy_indicator_relation"],
            "note": "工程特征/漂移指标",
        }
    if "f107_sunspot_residual" in field:
        return {
            "physical_meaning": "F10.7 相对黑子数历史关系的残差",
            "mechanism_link": ["proxy_indicator_relation"],
            "note": "工程特征/漂移指标",
        }
    if "f107_sunspot_expected" in field or "f107_sunspot_ratio" in field:
        return {
            "physical_meaning": "F10.7 实际值与基于黑子数历史关系预期值的比值",
            "mechanism_link": ["proxy_indicator_relation"],
            "note": "工程特征/漂移指标",
        }
    if "f107_sunspot_corr" in field:
        return {
            "physical_meaning": "F10.7 与太阳黑子数滚动相关性",
            "mechanism_link": ["proxy_indicator_relation"],
            "note": "工程特征/漂移指标",
        }
    if "waldmeier_residual" in field or "rise_slope_peak_residual" in field:
        return {
            "physical_meaning": "周期峰值相对上升斜率历史关系的残差",
            "mechanism_link": ["waldmeier_effect"],
            "note": "工程特征/漂移指标",
        }
    if "_lag_" in field:
        return {
            "physical_meaning": "滞后特征，用于捕捉历史观测对当前状态的预测信息",
            "mechanism_link": ["solar_activity_cycle"],
            "note": "工程特征",
        }
    if "_mean_" in field or "_std_" in field:
        return {
            "physical_meaning": "滚动统计特征，用于平滑短期波动并捕捉趋势",
            "mechanism_link": ["solar_activity_cycle"],
            "note": "工程特征",
        }
    if "_diff_" in field or "_roc_" in field:
        return {
            "physical_meaning": "差分或变化率特征，反映变化速度",
            "mechanism_link": ["solar_activity_cycle"],
            "note": "工程特征",
        }
    if field.startswith("is_") and field.endswith("_available"):
        return {
            "physical_meaning": "数据源可用性标记",
            "mechanism_link": ["data_quality"],
            "note": "工程特征",
        }
    return {
        "physical_meaning": "未验证字段",
        "mechanism_link": [],
        "note": "语义未标注",
    }


def lookup_physical_meaning(field: str) -> dict[str, Any]:
    """Return physical meaning and mechanism link for a field.

    Priority:
    1. Exact match in the local seed JSON.
    2. Pattern-based fallback for engineered features.
    3. Generic 'unverified' placeholder.
    """
    seed = _load_seed()
    if field in seed:
        return seed[field]
    return _pattern_meaning(field)
