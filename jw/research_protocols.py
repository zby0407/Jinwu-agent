"""Deterministic contracts for narrow, high-risk scientific analyses."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

F107_DISCONTINUITY_PROTOCOL = "f107_discontinuity_v1"
GENERIC_DATA_PRODUCT = "generic_data_artifact"
SILSO_CYCLE_REPRODUCTION_PROTOCOL = "silso_cycle_reproduction_v1"
SILSO_CYCLE_EXTREMA_DATA_PRODUCT = "silso_cycle_extrema_v1"
SOLAR_POLAR_PRECURSOR_PROTOCOL = "solar_polar_precursor_v1"
SOLAR_POLAR_PRECURSOR_DATA_PRODUCT = "solar_polar_precursor_table_v1"
SILSO_CYCLE_REPRODUCTION_DATASET_IDS: tuple[str, ...] = (
    "silso-monthly-total-v2",
    "silso-monthly-smoothed-v2",
    "silso-cycle-extrema-v2",
)
F107_DISCONTINUITY_REQUIRED_MEASUREMENTS: tuple[str, ...] = (
    "f107_full_period_relation",
    "f107_pre_1980_relation",
    "f107_post_1980_relation",
    "f107_fixed_1980_chow_f",
    "f107_scan_best_break_year",
    "f107_relative_scale_jump",
    "f107_pre_model_predicts_post_mean_residual",
    "f107_pre_model_predicts_post_positive_fraction",
    "f107_post_model_predicts_pre_mean_residual",
    "f107_post_model_predicts_pre_positive_fraction",
    "f107_low_activity_sensitivity",
    "f107_month_coverage_sensitivity",
)

_F107_PATTERN = re.compile(
    r"(?:f\s*10[.]?7|10[.]7\s*cm|太阳射电流量)",
    re.IGNORECASE,
)
_F107_DISCONTINUITY_PATTERN = re.compile(
    r"(?:1980|1981|漂移|不连续|断点|变点|跨时段|跨周期稳定|"
    r"discontinuity|breakpoint|change[\s-]?point|drift|cross[\s-]?period)",
    re.IGNORECASE,
)
_SILSO_PATTERN = re.compile(r"(?:WDC[- ]?SILSO|SILSO|太阳黑子)", re.IGNORECASE)
_SILSO_CYCLE_REPRODUCTION_PATTERN = re.compile(
    r"(?:太阳活动周|solar\s+cycle|周期).*(?:极小|极大|极值|上升时间|"
    r"minimum|maximum|extrema|rise\s+time)|"
    r"(?:极小|极大|极值|上升时间|minimum|maximum|extrema|rise\s+time).*"
    r"(?:太阳活动周|solar\s+cycle|周期)",
    re.IGNORECASE | re.DOTALL,
)
_SILSO_CYCLE_ENGLISH_PATTERN = re.compile(
    r"(?:solar\s+)?cycles?.*(?:minimum|maximum|extrema|rise\s+time)|"
    r"(?:minimum|maximum|extrema|rise\s+time).*(?:solar\s+)?cycles?",
    re.IGNORECASE | re.DOTALL,
)
_POLAR_FIELD_PATTERN = re.compile(
    r"(?:polar[\s-]?field|polar precursor|MWO|WSO|极区磁场|极地磁场)",
    re.IGNORECASE,
)
_POLAR_PRECURSOR_INTENT_PATTERN = re.compile(
    r"(?:precursor|predictor|following cycle|next cycle|near minimum|"
    r"前兆|预测因子|下一周期|极小附近)",
    re.IGNORECASE,
)
_POLAR_EXCLUSION_PATTERN = re.compile(
    r"(?:do not|without|exclude|不要|不加入|排除).{0,30}"
    r"(?:polar[\s-]?field|polar precursor|MWO|WSO|极区磁场|极地磁场)",
    re.IGNORECASE | re.DOTALL,
)


def detect_analysis_protocol(text: str) -> str:
    """Return the required deterministic analysis protocol for one request."""

    if _F107_PATTERN.search(text) and _F107_DISCONTINUITY_PATTERN.search(text):
        return F107_DISCONTINUITY_PROTOCOL
    if (
        _POLAR_FIELD_PATTERN.search(text)
        and _POLAR_PRECURSOR_INTENT_PATTERN.search(text)
        and not _POLAR_EXCLUSION_PATTERN.search(text)
    ):
        return SOLAR_POLAR_PRECURSOR_PROTOCOL
    if _SILSO_PATTERN.search(text) and (
        _SILSO_CYCLE_REPRODUCTION_PATTERN.search(text)
        or _SILSO_CYCLE_ENGLISH_PATTERN.search(text)
    ):
        return SILSO_CYCLE_REPRODUCTION_PROTOCOL
    return "none"


def required_data_product_for_protocol(protocol: str) -> str:
    """Return the sole specialized Data product required by one protocol."""

    return {
        SILSO_CYCLE_REPRODUCTION_PROTOCOL: SILSO_CYCLE_EXTREMA_DATA_PRODUCT,
        SOLAR_POLAR_PRECURSOR_PROTOCOL: SOLAR_POLAR_PRECURSOR_DATA_PRODUCT,
    }.get(protocol, GENERIC_DATA_PRODUCT)


def render_silso_cycle_reproduction_markdown(payload: Mapping[str, Any]) -> str:
    """Render the accepted SILSO comparison without another model generation."""

    if not (
        payload.get("schema_version") == "silso-cycle-reproduction-v1"
        and payload.get("analysis_protocol") == SILSO_CYCLE_REPRODUCTION_PROTOCOL
        and payload.get("cycles") == [21, 22, 23, 24]
    ):
        raise ValueError("invalid SILSO cycle reproduction payload")
    rows = payload.get("comparison")
    if not isinstance(rows, list) or len(rows) != 4:
        raise ValueError("SILSO comparison must contain cycles 21 through 24")

    def extremum(row: Mapping[str, Any], key: str) -> Mapping[str, Any]:
        value = row.get(key)
        if not isinstance(value, Mapping):
            raise ValueError(f"invalid {key}")
        return value

    table_rows: list[str] = []
    differences: list[str] = []
    strengths: list[tuple[int, float]] = []
    rise_times: list[tuple[int, int]] = []
    for raw_row in rows:
        if not isinstance(raw_row, Mapping):
            raise ValueError("invalid SILSO comparison row")
        cycle = int(raw_row["cycle"])
        official_minimum = extremum(raw_row, "official_minimum")
        official_maximum = extremum(raw_row, "official_maximum")
        rise_months = int(raw_row["official_rise_months"])
        minimum_value = float(official_minimum["sunspot_number"])
        maximum_value = float(official_maximum["sunspot_number"])
        table_rows.append(
            f"| {cycle} | {official_minimum['year_month']} | {minimum_value:.1f} | "
            f"{official_maximum['year_month']} | {maximum_value:.1f} | "
            f"{rise_months} 个月 |"
        )
        strengths.append((cycle, maximum_value))
        rise_times.append((cycle, rise_months))
        if not (
            raw_row.get("minimum_matches_official") is True
            and raw_row.get("maximum_matches_official") is True
        ):
            recomputed_minimum = extremum(raw_row, "recomputed_minimum")
            recomputed_maximum = extremum(raw_row, "recomputed_maximum")
            if (
                raw_row.get("minimum_matches_official") is False
                and float(recomputed_minimum["sunspot_number"]) == minimum_value
            ):
                difference_note = (
                    "重算序列在相同最小平滑值的平台期选择了不同月份；"
                    "两者均保留，主表采用官方年月。"
                )
            elif raw_row.get("maximum_matches_official") is False:
                difference_note = (
                    "官方极大与序列重算极大不一致；两者均保留，主表采用官方值。"
                )
            else:
                difference_note = "官方值与序列重算值不一致；两者均保留。"
            differences.append(
                f"| {cycle} | {official_minimum['year_month']} / "
                f"{minimum_value:.1f} | {recomputed_minimum['year_month']} / "
                f"{float(recomputed_minimum['sunspot_number']):.1f} | "
                f"{official_maximum['year_month']} / {maximum_value:.1f} | "
                f"{recomputed_maximum['year_month']} / "
                f"{float(recomputed_maximum['sunspot_number']):.1f} | "
                f"{rise_months} / {int(raw_row['recomputed_rise_months'])} 个月 | "
                f"{difference_note} |"
            )

    strength_order = " > ".join(
        f"周期 {cycle}"
        for cycle, _value in sorted(strengths, key=lambda item: item[1], reverse=True)
    )
    weakest = min(strengths, key=lambda item: item[1])
    fastest = min(rise_times, key=lambda item: item[1])
    slowest = max(rise_times, key=lambda item: item[1])
    sections = [
        "## 第 21–24 太阳活动周历史复现",
        "",
        "数据源：WDC-SILSO Sunspot Number Version 2.0 的官方 13 个月平滑月均总太阳黑子数和官方周期极值表。",
        "",
        "计算方法：以官方极小和极大年月为周期标记，并按日历月份差计算从极小到极大的上升时间。主表采用官方极值；序列重算结果仅用于一致性检查。",
        "",
        "| 周期 | 官方极小年月 | 极小值 | 官方极大年月 | 峰值 | 上升时间 |",
        "| --- | --- | ---: | --- | ---: | ---: |",
        *table_rows,
    ]
    if differences:
        sections.extend(
            [
                "",
                "### 官方值与序列重算的差异",
                "",
                "| 周期 | 官方极小 | 重算极小 | 官方极大 | 重算极大 | 官方/重算上升时间 | 说明 |",
                "| --- | --- | --- | --- | --- | --- | --- |",
                *differences,
            ]
        )
    sections.extend(
        [
            "",
            "### 直接比较",
            "",
            f"- 强度排序：{strength_order}。",
            f"- 周期 {weakest[0]} 是四个周期中最弱的。",
            f"- 周期 {fastest[0]} 上升最快（{fastest[1]} 个月）。",
            f"- 周期 {slowest[0]} 上升最慢（{slowest[1]} 个月）。",
            "- 以上仅为历史描述，不涉及周期 26 的预测。",
        ]
    )
    return "\n".join(sections)


def f107_discontinuity_directive() -> str:
    """Return the experiment-facing contract for the F10.7 discontinuity task."""

    measurement_ids = ", ".join(F107_DISCONTINUITY_REQUIRED_MEASUREMENTS)
    return (
        "Implement f107_discontinuity_v1 from the verified dataset manifest. "
        "Model F10.7 as the response over the common pre/post sunspot-number "
        "support; never invert an SN-on-F10.7 OLS slope. Compute the relative "
        "F10.7 scale jump at a fixed reference such as SN=100 and store it as "
        "f107_relative_scale_jump, then compare that computed value with the "
        "published approximately 10.5% discontinuity without forcing agreement. "
        "Keep the fixed 1980-1981 comparison confirmatory. For exploratory "
        "breakpoint scans, use an upper-tail survival probability and choose the "
        "maximum F statistic or minimum unrestricted SSR, not the first p-value "
        "that underflows to zero. Define cross-period residuals as "
        "actual-minus-predicted. Include observed/URSI product, low-activity, and "
        "20/25-observed-day monthly-coverage sensitivities. Estimate long-term "
        "trend and instantaneous step in a joint model before comparing them. "
        f"Emit every required measurement id: {measurement_ids}."
    )


def sha256_file(path: Path) -> str:
    """Hash one immutable artifact without loading it fully into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class DatasetSemanticManifest:
    """Hash-bound semantic description of one canonicalized dataset."""

    manifest_id: str
    input_path: str
    input_sha256: str
    adapter_id: str
    adapter_version: str
    product_id: str
    product_version: str
    column_bindings: Mapping[str, str]
    unit: str
    observation_grain: str
    time_column: str
    primary_key: tuple[str, ...]
    duplicate_policy: str
    missing_policy: str
    quality_policy: str
    aggregation_plan: tuple[str, ...]
    coverage_start: str
    coverage_end: str
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
    excluded_inputs: tuple[Mapping[str, str], ...] = ()
    limitations: tuple[str, ...] = ()
    analysis_requirements: tuple[str, ...] = ()
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["column_bindings"] = dict(self.column_bindings)
        value["primary_key"] = list(self.primary_key)
        value["aggregation_plan"] = list(self.aggregation_plan)
        value["diagnostics"] = dict(self.diagnostics)
        value["excluded_inputs"] = [dict(row) for row in self.excluded_inputs]
        value["limitations"] = list(self.limitations)
        value["analysis_requirements"] = list(self.analysis_requirements)
        return value


__all__ = [
    "F107_DISCONTINUITY_PROTOCOL",
    "F107_DISCONTINUITY_REQUIRED_MEASUREMENTS",
    "GENERIC_DATA_PRODUCT",
    "SILSO_CYCLE_EXTREMA_DATA_PRODUCT",
    "SILSO_CYCLE_REPRODUCTION_DATASET_IDS",
    "SILSO_CYCLE_REPRODUCTION_PROTOCOL",
    "SOLAR_POLAR_PRECURSOR_DATA_PRODUCT",
    "SOLAR_POLAR_PRECURSOR_PROTOCOL",
    "DatasetSemanticManifest",
    "detect_analysis_protocol",
    "f107_discontinuity_directive",
    "render_silso_cycle_reproduction_markdown",
    "required_data_product_for_protocol",
    "sha256_file",
]
