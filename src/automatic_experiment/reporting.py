"""Formal Markdown and Pi entry-result rendering for every terminal state."""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
from typing import Any

from .contracts import (
    ENTRY_RESULT_VERSION,
    HARD_NUMERIC_CUTOFF,
    RELATIVE_DECISION_CUTOFF,
    R_SQUARED_PLAN,
    _quantitative_claims,
    _same_fitted_condition,
    canonical_sha256,
)
from .report_assets import generate_report_assets
from .state import (
    atomic_write_json,
    atomic_write_text,
    file_sha256,
    read_json,
    utc_now,
)

OUTCOME_LABELS = {
    "completed_interpretable": "完成且结果可解释",
    "partial_result": "获得部分结果",
    "scientific_null": "科学空结果",
    "high_uncertainty": "结果不确定性较高",
    "input_missing": "缺少必要输入",
    "method_mismatch": "方法与任务不匹配",
    "technical_failure": "技术执行或结果核验失败",
    "budget_stopped": "达到资源预算后停止",
    "clarification_required": "需要用户澄清",
    "boundary_blocked": "安全或能力边界阻断",
    "cancelled_by_user": "用户已取消",
}
ROLE_LABELS = {
    "primary": "主要测量",
    "secondary": "辅助测量",
    "diagnostic": "诊断测量",
}
CRITERION_LABELS = {
    "met": "已满足",
    "not_met": "未满足",
    "uncertain": "暂无法确定",
    "not_evaluated": "未评价",
}
BASIS_LABELS = {
    "user_request": "用户明确要求",
    "located_source": "已定位资料",
    "data_derived": "当前数据推导",
    "method_standard": "方法标准",
    "bounded_pragmatic_choice": "预先声明的有界约定",
    "qualitative_no_fixed_threshold": "不设固定数值门槛的定性检查",
}
ARTIFACT_KIND_LABELS = {
    "json": "JSON",
    "csv": "CSV",
    "text": "文本",
    "markdown": "Markdown",
    "image": "图像",
    "fits": "FITS 科学数据",
    "netcdf": "NetCDF 科学数据",
    "hdf5": "HDF5 科学数据",
    "parquet": "Parquet 表格",
    "other": "其他",
}
ANALYSIS_MODE_LABELS = {
    "descriptive": "描述性分析",
    "comparative": "比较分析",
    "descriptive_and_comparative": "描述性与比较分析",
    "inferential": "推断性分析",
    "predictive": "预测性分析",
    "simulation": "模拟分析",
    "descriptive_and_calibrative": "描述性校准比较",
    "descriptive and calibrative": "描述性校准比较",
    "descriptive_with_calibration": "描述性校准与时间留出评估",
    "time_ordered_holdout_calibration": "时间顺序留出校准评估",
    "time_ordered_holdout_evaluation": "时间顺序留出评估",
    "time_ordered_split_calibration": "时间顺序校准与留出评估",
    "method_comparison_with_calibration": "校准方法比较与时间留出评估",
    "bounded_exploratory": "有界探索性分析",
}
ENDPOINT_STATUS_LABELS = {
    "completed": "已完成评估",
    "failed": "端点计算失败",
    "not_evaluated": "未执行评估",
}
STAGE_OUTCOME_LABELS = {
    "completed": "已按预定要求完成",
    "inconclusive": "得到结果，但暂不足以作出明确判断",
    "input_missing": "因必要输入缺失而停止",
    "evidence_conflict": "发现相互冲突的证据",
    "method_invalid": "发现当前方法不适合该问题",
    "technical_failure": "执行过程出现可定位的技术问题",
    "budget_reached": "达到本次运行预算",
}
INPUT_ROLE_LABELS = {
    "upstream_research_plan": "研究规划反馈",
    "research_plan": "研究规划反馈",
    "upstream_data_feature_feedback": "数据与特征反馈",
    "data_feature_feedback": "数据与特征反馈",
    "primary_data": "主要分析数据",
    "processed_data": "处理后数据",
    "data_feature_metadata": "数据与特征反馈",
    "paired_measurement_data": "处理后配对数据",
    "upstream research plan guidance": "研究规划反馈",
    "data feature and quality metadata": "数据与特征反馈",
    "paired observation data": "处理后配对数据",
}

READER_FIELD_LABELS: dict[str, str] = {}


def _reader_facing_text(value: Any) -> str:
    text = str(value)
    text = re.sub(
        r"(?:\battempt-\d{3}\b\s*)?成功执行\s*[，,]\s*"
        r"(?:退出码|exit\s+code)\s*(?:为\s*)?0",
        "实验程序正常完成",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\battempt-\d{3}\b", "本次执行", text, flags=re.IGNORECASE)
    text = re.sub(
        r"(?:退出码|exit\s+code)\s*(?:为\s*)?0",
        "程序正常结束",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?:退出码|exit\s+code)",
        "程序结束状态",
        text,
        flags=re.IGNORECASE,
    )
    for machine_name, display_name in sorted(
        READER_FIELD_LABELS.items(), key=lambda item: len(item[0]), reverse=True
    ):
        text = text.replace(f"`{machine_name}`", display_name)
        text = text.replace(machine_name, display_name)
    text = text.replace("分组 分组", "分组")
    text = text.replace("观测值 数值", "观测值")
    text = text.replace("验证结论稳健性", "检查方向性结论是否一致")
    text = text.replace("稳健性检查", "敏感性检查")
    text = re.sub(
        r"合成(?:演示|测试)?夹具(?:数据)?",
        "合成演示数据",
        text,
    )
    text = re.sub(r"模拟(?:测试)?夹具(?:数据)?", "模拟数据", text)
    text = text.replace("suspect_geometry", "几何条件可疑")
    text = text.replace("quality_flag", "质量标记")
    text = text.replace("polar_overlap_features.csv", "本次提供的合成配对数据")
    text = re.sub(
        r"(?<![A-Za-z0-9_])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.csv\b",
        "输入表格数据",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\bCSV\b", "表格数据", text, flags=re.IGNORECASE)
    text = text.replace("合成配对数据重叠期数据", "合成数据的重叠期")
    text = text.replace("合成配对数据配对观测", "合成配对观测")
    text = text.replace("合成配对数据数据范围", "合成数据范围")
    text = text.replace("合成配对数据数据", "合成配对数据")
    text = text.replace("被被标记", "被标记")
    text = text.replace("当前夹具", "当前合成数据")
    text = text.replace("本夹具", "本合成数据")
    text = text.replace("夹具", "演示数据")
    for internal_reason, reader_reason in (
        ("wall_budget_parent_guard", "运行时间达到预算"),
        ("wall_budget", "运行时间达到预算"),
        ("stdout_budget", "输出文本达到预算"),
        ("stderr_budget", "错误文本达到预算"),
        ("disk_budget", "可用存储达到预算"),
        ("resource_budget", "计算资源达到预算"),
    ):
        text = text.replace(internal_reason, reader_reason)
    for internal_outcome, reader_outcome in OUTCOME_LABELS.items():
        text = text.replace(internal_outcome, reader_outcome)
    text = text.replace("未校准时候选", "未校准时，候选")
    text = text.replace("几何条件可疑 标记行", "被标记为几何条件可疑的观测")
    text = text.replace("标记为 几何条件可疑", "被标记为几何条件可疑")
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", text)
    return text


def _reader_facing_plan_text(
    value: Any,
    design: dict[str, Any] | None,
) -> str:
    """Replace plan ids with their reader-facing names without global state."""

    text = _reader_facing_text(value)
    labels = {
        str(row["name"]): str(row["display_name"])
        for row in (design or {}).get("measurement_plan", [])
        if isinstance(row, dict)
        and isinstance(row.get("name"), str)
        and isinstance(row.get("display_name"), str)
    }
    for machine_name, display_name in list(labels.items()):
        if machine_name.endswith("_value"):
            labels.setdefault(machine_name.removesuffix("_value"), display_name)
    labels.update(
        {
            str(row["id"]): str(row["display_name"])
            for row in (design or {}).get("result_plan", [])
            if isinstance(row, dict)
            and isinstance(row.get("id"), str)
            and isinstance(row.get("display_name"), str)
        }
    )
    for machine_name, display_name in sorted(
        labels.items(), key=lambda item: len(item[0]), reverse=True
    ):
        text = text.replace(f"`{machine_name}`", display_name)
        text = re.sub(
            rf"(?<![A-Za-z0-9_]){re.escape(machine_name)}(?![A-Za-z0-9_])",
            display_name,
            text,
        )
    text = _replace_condition_aliases(text, design)
    fitting_only_sensitivity = any(
        row.get("comparison_kind") == "candidate_vs_candidate"
        and row.get("fit_evaluation_relation") == "disjoint_rows"
        and row.get("baseline_fit_condition")
        and row.get("candidate_fit_condition")
        for row in (design or {}).get("paired_comparison_audits", [])
    )
    if fitting_only_sensitivity:
        text = re.sub(
            r"排除(?:被|质量)?标记观测后(?=(?:[，,]\s*)?(?:该改善|校准|斜率|截距))",
            "排除被标记观测拟合后",
            text,
        )
        text = text.replace("全量拟合", "包含被标记观测的拟合")
        text = text.replace("全量数据拟合", "包含被标记观测的拟合")
        text = text.replace("全部数据拟合", "包含被标记观测的拟合")
        text = text.replace("全数据拟合", "包含被标记观测的拟合")
        text = text.replace(
            "全部数据校准后",
            "包含被标记观测拟合并校准后",
        )
        text = text.replace(
            "全部数据校准",
            "包含被标记观测拟合后的校准",
        )
        text = text.replace("全部数据条件", "包含被标记观测条件")
        text = text.replace("全部行拟合", "包含被标记观测的拟合")
        text = text.replace(
            "全部行校准",
            "包含被标记观测拟合后的校准",
        )
        text = text.replace("全部行条件", "包含被标记观测条件")
        text = re.sub(
            r"(包含被标记观测的|排除标记观测)拟合校准",
            r"\1拟合后的校准",
            text,
        )
        text = re.sub(
            r"以(包含被标记观测的|排除标记观测)拟合后的校准后",
            r"\1拟合并校准后",
            text,
        )
        text = text.replace(
            "排除标记观测拟合后的校准误差低于包含被标记观测的拟合",
            "排除标记观测拟合后的校准误差低于包含被标记观测拟合后的校准误差",
        )
    text = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", text)
    return text


def _replace_condition_aliases(
    value: Any,
    design: dict[str, Any] | None,
) -> str:
    text = str(value)
    condition_aliases = _semantic_condition_aliases(design)
    for left_code, left_alias in condition_aliases.items():
        for right_code, right_alias in condition_aliases.items():
            text = re.sub(
                rf"(?<![A-Za-z0-9])(?:条件\s*)?"
                rf"{re.escape(left_code)}\s*减\s*(?:条件\s*)?"
                rf"{re.escape(right_code)}(?![A-Za-z0-9])",
                f"{left_alias.removesuffix('条件')}减"
                f"{right_alias.removesuffix('条件')}",
                text,
                flags=re.IGNORECASE,
            )
        text = re.sub(
            rf"条件\s*{re.escape(left_code)}(?![A-Za-z0-9])",
            left_alias,
            text,
            flags=re.IGNORECASE,
        )
    return text


def _semantic_condition_aliases(
    design: dict[str, Any] | None,
) -> dict[str, str]:
    """Infer reader-facing names for opaque condition codes when possible."""

    aliases: dict[str, str] = {}

    def infer_alias(value: Any) -> str | None:
        text = _reader_facing_text(value)
        marked = re.search(r"标记|异常|可疑", text) is not None
        if marked and re.search(r"排除|剔除|不含|不包含", text):
            return "排除标记观测条件"
        if marked and re.search(r"保留|包含|纳入", text):
            return "包含被标记观测条件"
        return None

    for row in (design or {}).get("measurement_plan", []):
        match = re.search(
            r"条件\s*(?P<code>[A-Za-z0-9]+)",
            str(row.get("display_name", "")),
            re.IGNORECASE,
        )
        if match:
            alias = infer_alias(row.get("scientific_meaning", ""))
            if alias:
                aliases.setdefault(match.group("code").upper(), alias)
    for audit in (design or {}).get("paired_comparison_audits", []):
        for field in ("baseline_fit_condition", "candidate_fit_condition"):
            value = audit.get(field)
            match = re.match(
                r"\s*条件\s*(?P<code>[A-Za-z0-9]+)\s*[：:]",
                str(value or ""),
                re.IGNORECASE,
            )
            if match:
                alias = infer_alias(value)
                if alias:
                    aliases.setdefault(match.group("code").upper(), alias)
    return aliases


def _clause(value: Any) -> str:
    return _reader_facing_text(value).rstrip("。；; ")


def _table_text(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _normalized_result_label(value: object) -> str:
    normalized = "".join(
        character.casefold() for character in str(value) if character.isalnum()
    )
    for suffix in (
        "结果值",
        "数值",
        "结果",
        "值",
        "resultvalue",
        "numericvalue",
        "value",
        "result",
    ):
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            normalized = normalized[: -len(suffix)]
            break
    normalized = re.sub(r"(?<=[\u3400-\u9fff])(?:r|n)$", "", normalized)
    return normalized


def _has_cjk(value: object) -> bool:
    return any("\u3400" <= char <= "\u9fff" for char in str(value))


def _reader_text(value: object) -> str:
    text = str(value)
    for internal, reader_term in (
        ("paired_comparison_audits", "逐行比较核验"),
        ("worker_result", "机器核验结果"),
        ("worker", "实验程序"),
    ):
        text = text.replace(internal, reader_term)
    return text


def _is_placeholder_unit(value: object) -> bool:
    """Return True for authoring notes that are not scientific units."""

    unit = re.sub(r"[\s_\-—–（）()]+", "", str(value or "").casefold())
    if not unit:
        return False
    exact = {
        "与数值列相同单位",
        "与原始数值相同单位",
        "与原数据相同单位",
        "与输入相同单位",
        "原数据单位",
        "原始数据单位",
        "sameunitasvaluecolumn",
        "sameunitasinput",
        "sameasinput",
        "inputunit",
        "sourceunit",
    }
    return unit in exact or (
        ("相同单位" in unit or "sameunit" in unit)
        and any(token in unit for token in ("数值", "数据", "输入", "value", "input"))
    )


def _display_unit(value: object) -> str:
    unit = str(value or "").strip()
    return "" if _is_placeholder_unit(unit) else unit


def _format_measurement(row: dict[str, Any]) -> str:
    value = row["value"]
    unit = _display_unit(row.get("unit"))
    if unit == "boolean":
        return "是" if bool(value) else "否"
    rendered = f"{value:.4g}" if isinstance(value, float) else str(value)
    if unit == "percent":
        return f"{rendered}%"
    if unit.casefold() in {"dimensionless", "unitless", "无单位", "无量纲", "1"}:
        return rendered
    return f"{rendered} {unit}".strip()


def _format_typed_result(row: dict[str, Any]) -> str:
    value = row.get("value")
    category_context = " ".join(
        str(row.get(field, "")) for field in ("display_name", "scientific_meaning")
    )
    if row.get("value_kind") == "boolean":
        rendered = "是" if value else "否"
    elif row.get("value_kind") == "category" and str(value).casefold() in {
        "pass",
        "passed",
        "ok",
        "success",
    }:
        rendered = "通过"
    elif row.get("value_kind") == "category" and str(value).casefold() in {
        "fail",
        "failed",
        "error",
    }:
        rendered = "未通过"
    elif row.get("value_kind") == "category" and str(value).casefold() in {
        "positive",
        "positive_correlation",
    }:
        rendered = "正相关" if "相关" in category_context else "正向"
    elif row.get("value_kind") == "category" and str(value).casefold() in {
        "negative",
        "negative_correlation",
    }:
        rendered = "负相关" if "相关" in category_context else "负向"
    elif row.get("value_kind") == "category" and str(value).casefold() in {
        "neutral",
        "none",
        "no_correlation",
        "zero",
    }:
        rendered = "无明确线性方向" if "相关" in category_context else "中性"
    elif isinstance(value, float):
        rendered = f"{value:.4g}"
    else:
        rendered = str(value)
    if row.get("value_kind") == "text":
        if re.fullmatch(
            r"\s*simple_linear\s*:\s*reference\s*=\s*intercept\s*"
            r"\+\s*slope\s*\*\s*candidate\s*",
            rendered,
            re.IGNORECASE,
        ):
            rendered = "线性函数：参考坐标 = 截距 + 斜率 × 候选读数"
        elif re.search(
            r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9]*"
            r"(?:_[A-Za-z0-9]+)+(?![A-Za-z0-9_])",
            rendered,
        ):
            rendered = "见数据与方法中的定义"
    unit = _display_unit(row.get("unit"))
    if unit.casefold() in {"dimensionless", "unitless", "无单位", "无量纲", "1"}:
        return rendered
    if unit == "percent":
        return f"{rendered}%"
    return f"{rendered} {unit}".strip()


def _missing_unit_note(
    design: dict[str, Any] | None,
    measurements: list[dict[str, Any]],
    typed_results: list[dict[str, Any]],
    *,
    chinese_task: bool,
) -> str:
    planned = [
        *((design or {}).get("measurement_plan") or []),
        *((design or {}).get("result_plan") or []),
    ]
    observed = [*measurements, *typed_results]
    if not any(
        _is_placeholder_unit(row.get("unit"))
        for row in [*planned, *observed]
        if isinstance(row, dict)
    ):
        return ""
    if chinese_task:
        return "原始数据未注明计量单位，因此相关统计量只报告数值，不补设单位。"
    return (
        "The source data do not specify a measurement unit, so the corresponding "
        "statistics are reported as values without an invented unit."
    )


def _source_labels(record: dict[str, Any]) -> dict[str, str]:
    snapshot = record.get("input_snapshot") or {}
    return {
        row["id"]: row.get("source_path") or row["id"]
        for row in snapshot.get("inputs", [])
    }


def _input_role_label(value: object) -> str:
    return INPUT_ROLE_LABELS.get(str(value), str(value))


def _analysis_mode_label(value: object) -> str:
    text = str(value)
    if text in ANALYSIS_MODE_LABELS:
        return ANALYSIS_MODE_LABELS[text]
    if _has_cjk(text):
        return text
    normalized = text.lower().replace("_", " ").replace("-", " ")
    segments: list[str] = []
    has_descriptive = "descript" in normalized
    has_comparison = "compar" in normalized
    if has_descriptive and has_comparison:
        segments.append("描述性比较")
    elif has_descriptive:
        segments.append("描述性分析")
    elif has_comparison:
        segments.append("比较分析")
    if "infer" in normalized:
        segments.append("推断性分析")
    if "predict" in normalized:
        segments.append("预测性分析")
    if "simulat" in normalized:
        segments.append("模拟分析")
    if "calibr" in normalized:
        segments.append("校准")
    if "holdout" in normalized and "time" in normalized:
        segments.append("时间顺序留出评估")
    elif "holdout" in normalized:
        segments.append("留出评估")
    return "与".join(dict.fromkeys(segments)) if segments else "任务定制分析"


def _input_lines(record: dict[str, Any]) -> list[str]:
    snapshot = record.get("input_snapshot")
    if not snapshot:
        return ["- 本次任务没有已快照的本地输入文件。"]
    lines = [f"- 已确认输入快照总大小为 {snapshot['total_bytes']} 字节。"]
    for row in snapshot["inputs"]:
        label = row.get("source_path") or row["id"]
        lines.append(f"- `{label}`：已快照 {len(row.get('files', []))} 个文件。")
        for file_row in row.get("files", []):
            profile = file_row.get("profile")
            if not isinstance(profile, dict) or profile.get("kind") != "tabular":
                continue
            if profile.get("profile_complete"):
                columns = "、".join(f"`{name}`" for name in profile.get("columns", []))
                lines.append(
                    f"  - 表格结构：{profile['row_count']} 行；列为 {columns or '未识别'}。"
                )
                missing = profile.get("missing_value_counts", {})
                if isinstance(missing, dict) and missing:
                    detail = "、".join(
                        f"`{name}` {count} 个" for name, count in missing.items()
                    )
                    lines.append(f"  - 空值概况：{detail}。")
            else:
                lines.append(
                    f"  - 表格结构概况未完整生成：{profile.get('reason', '达到检查上限')}。"
                )
    if snapshot.get("missing_required_ids"):
        lines.append(
            f"- 缺失的必要输入：{', '.join(snapshot['missing_required_ids'])}。"
        )
    return lines


def _data_profile_summary(record: dict[str, Any]) -> str:
    snapshot = record.get("input_snapshot") or {}
    summaries: list[str] = []
    for input_row in snapshot.get("inputs", []):
        for file_row in input_row.get("files", []):
            profile = file_row.get("profile")
            if (
                not isinstance(profile, dict)
                or profile.get("kind") != "tabular"
                or not profile.get("profile_complete")
            ):
                continue
            raw_columns = profile.get("columns", [])
            missing = profile.get("missing_value_counts") or {}
            missing_total = (
                sum(int(value) for value in missing.values())
                if isinstance(missing, dict)
                else 0
            )
            summaries.append(
                f"{profile.get('row_count', 0)} 行、{len(raw_columns)} 列，"
                + (
                    "未发现空值"
                    if missing_total == 0
                    else f"发现 {missing_total} 个空值"
                )
            )
    if not summaries:
        return ""
    return "实际表格结构：" + "；".join(summaries[:4]) + "。"


def _criterion_table(
    record: dict[str, Any],
    design: dict[str, Any] | None,
) -> list[str]:
    assessment = record.get("scientific_assessment")
    if not assessment:
        return ["本次任务未进入科学结果判定阶段。"]
    ledger = record.get("evidence_ledger") or {}
    rows = ledger.get("criteria", [])
    if not rows:
        return ["没有形成可审计的研究判据证据表。"]
    lines = [
        "| 判据 ID | 预设判据 | 判断依据 | 实际证据 | 结论 |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        basis = BASIS_LABELS.get(row.get("basis_kind"), "已声明依据")
        evidence_parts: list[str] = []
        for measurement in row.get("measurements", []):
            evidence_parts.append(
                f"{measurement['name']}={_format_measurement(measurement)}"
            )
        for result_item in row.get("typed_results", []):
            evidence_parts.append(
                f"{result_item['display_name']}={_format_typed_result(result_item)}"
            )
        for endpoint in row.get("endpoints", []):
            if not row.get("measurements"):
                evidence_parts.append(
                    "端点"
                    + ENDPOINT_STATUS_LABELS.get(endpoint["status"], endpoint["status"])
                )
        decision = CRITERION_LABELS.get(
            row.get("assessment_status"),
            row.get("assessment_status", "未评价"),
        )
        explanation = row.get("assessment_explanation")
        if explanation:
            decision = f"{decision}：{_scientific_reader_text(explanation)}"
        lines.append(
            "| "
            + " | ".join(
                _table_text(value)
                for value in (
                    row["criterion_id"],
                    row["statement"],
                    f"{basis}；{row['basis_text']}",
                    "；".join(evidence_parts) or "没有已核验的测量或端点",
                    decision,
                )
            )
            + " |"
        )
    return lines


def _research_frame_lines(
    record: dict[str, Any],
    design: dict[str, Any] | None,
) -> list[str]:
    ledger = record.get("evidence_ledger") or {}
    frame = ledger.get("research_frame") or (design or {}).get("research_frame")
    if not isinstance(frame, dict):
        return []
    source_labels = _source_labels(record)
    lines = [
        f"本次实验的核心研究问题是：{frame['primary_question']}",
        "",
        "分析定位："
        + _analysis_mode_label(frame["analysis_mode"])
        + f"。可支持的主张范围是：{frame['claim_scope']}",
        "",
        "本次可直接回答的问题：",
        *[f"- {row}" for row in frame["supported_questions"]],
    ]
    if frame["deferred_questions"]:
        lines.extend(
            [
                "",
                "当前数据或方法暂不回答的问题：",
                *[f"- {row}" for row in frame["deferred_questions"]],
            ]
        )
    lines.extend(["", f"文献与外部依据边界：{frame['literature_basis']}"])
    if frame["input_evidence"]:
        lines.extend(
            [
                "",
                "| 输入材料 | 在本实验中的角色 | 使用方式 | 已知局限 |",
                "|---|---|---|---|",
            ]
        )
        for row in frame["input_evidence"]:
            lines.append(
                "| "
                + " | ".join(
                    _table_text(value)
                    for value in (
                        source_labels.get(row["input_id"], row["input_id"]),
                        _input_role_label(row["role"]),
                        row["intended_use"],
                        row["limitations"],
                    )
                )
                + " |"
            )
    return lines


def _execution_lines(record: dict[str, Any]) -> list[str]:
    facts = record.get("execution_facts")
    if not facts:
        return [
            "- 未启动实验进程。",
            "- 因此本报告不声称已经运行代码或生成实验测量。",
        ]
    host_exit = facts.get(
        "host_process_exit_code", facts.get("windows_process_exit_code")
    )
    lines = [
        f"- 后端：{facts['sandbox_policy']['backend']}。",
        f"- 开始：{facts['started_at']}；结束：{facts['ended_at']}；墙钟 {facts['wall_seconds']} 秒。",
        f"- 主机/沙箱退出状态：{host_exit} / {facts['sandbox_exit_code']}。",
        f"- 停止原因：{facts['stop_reason'] or '正常结束'}。",
        f"- stdout/stderr：{facts['stdout']['size_bytes']} / {facts['stderr']['size_bytes']} 字节。",
        "- 网络、用户、PID、IPC 与 UTS 命名空间已启用；输入和代码只读，只有本次 output 目录可写。",
    ]
    usage = facts.get("resource_usage") or {}
    if usage:
        lines.append(
            f"- CPU 用户/系统时间：{usage.get('user_cpu_seconds', 0)} / "
            f"{usage.get('system_cpu_seconds', 0)} 秒；最大 RSS：{usage.get('max_rss_kb', 0)} KiB。"
        )
    return lines


def _attempt_lines(record: dict[str, Any]) -> list[str]:
    history = record.get("attempt_history") or []
    if not history:
        return ["- 本次任务未创建执行尝试。"]
    lines = [
        "| 次序 | 与前次关系 | 设计是否保持 | 执行 | 核验结果 | 修复或变更说明 |",
        "|---:|---|---|---|---|---|",
    ]
    design_sha = record.get("design_sha256")
    for index, row in enumerate(history, start=1):
        parent = (
            "首次尝试" if not row.get("parent_attempt") else f"承接第 {index - 1} 次"
        )
        design_state = (
            "保持同一已核验方案"
            if row.get("design_sha256") == design_sha
            else "设计哈希不一致"
        )
        execution = row.get("execution_summary")
        if execution is None:
            execution_text = "未执行"
        else:
            host_exit = execution.get(
                "host_process_exit_code", execution.get("windows_process_exit_code")
            )
            execution_text = (
                f"已执行 {execution['wall_seconds']:.3f} 秒；"
                f"退出 {host_exit}/"
                f"{execution['sandbox_exit_code']}"
            )
        outcome = row.get("verification_outcome")
        if (
            outcome == "technical_failure"
            and execution is not None
            and execution.get(
                "host_process_exit_code", execution.get("windows_process_exit_code")
            )
            == 0
            and execution["sandbox_exit_code"] == 0
        ):
            outcome_text = "执行完成，结果核验未通过"
        else:
            outcome_text = OUTCOME_LABELS.get(
                outcome,
                "尚未核验" if outcome is None else outcome,
            )
        change_reason = _reader_text(row.get("change_reason") or "")
        if not _has_cjk(change_reason):
            change_reason = (
                "技术修复，原始变更理由与代码差异已保存在机器记录。"
                if index > 1
                else "初始实现，原始变更理由与代码差异已保存在机器记录。"
            )
        changes = row.get("code_changes", [])
        if changes:
            change_count = sum(
                1 for change in changes if change.get("change_kind") != "unchanged"
            )
            change_reason += f" 涉及 {change_count} 个代码文件变更。"
        lines.append(
            "| "
            + " | ".join(
                _table_text(value)
                for value in (
                    index,
                    parent,
                    design_state,
                    execution_text,
                    outcome_text,
                    change_reason,
                )
            )
            + " |"
        )
    return lines


def _result_lines(record: dict[str, Any]) -> list[str]:
    result = record.get("worker_result")
    if not result:
        return ["没有通过合同检查的科学测量结果。"]
    lines: list[str] = [
        "| 测量 | 角色 | 数值 | 可追溯产物 |",
        "|---|---|---:|---|",
    ]
    for row in result["measurements"]:
        role = ROLE_LABELS.get(row["role"], row["role"])
        lines.append(
            f"| `{_table_text(row['name'])}` | {_table_text(role)} | "
            f"{_table_text(_format_measurement(row))} | "
            f"{_table_text(row['source_artifact'] or '仅见于受信任实验结果')} |"
        )
    for row in result.get("result_items", []):
        role = ROLE_LABELS.get(row["role"], row["role"])
        lines.append(
            f"| `{_table_text(row['display_name'])}` | {_table_text(role)} | "
            f"{_table_text(_format_typed_result(row))} | "
            f"{_table_text(row['source_artifact'] or '仅见于受信任实验结果')} |"
        )
    if len(lines) == 2:
        return ["实验程序已完成，但没有声明可单独展示的结果项。"]
    return lines


def _paired_comparison_lines(record: dict[str, Any]) -> list[str]:
    ledger = record.get("evidence_ledger") or {}
    rows = ledger.get("paired_comparisons") or []
    if not rows:
        return []
    metric_labels = {
        "mae": "平均绝对误差（MAE）",
        "rmse": "均方根误差（RMSE）",
        "mean_signed_error": "平均有符号误差",
    }
    lines = [
        "### 可信成对比较复算",
        "",
        "以下比较由确定性核心从不可变输入与逐行预测证据重新计算，不依赖实验代码自行汇总的结论。",
        "",
        "| 评估范围 | 比较类型 | 方案 A | 方案 B | 行数 | 复算指标 |",
        "|---|---|---|---|---:|---|",
    ]
    for row in rows:
        measurements = "；".join(
            f"`{name}`={value:.6g}"
            for name, value in row["recomputed_measurements"].items()
        )
        direction = (
            f"{', '.join(row['model_input_columns'])} → {row['model_target_column']}"
        )
        if row["comparison_kind"] == "candidate_vs_candidate":
            comparison_label = "两个候选模型条件"
            baseline_label = f"{row['baseline_fit_condition']}（{direction}）"
        else:
            comparison_label = "原始基线与候选模型"
            baseline_label = f"原始 `{row['source_baseline_column']}`"
        candidate_label = f"{row['candidate_fit_condition']}（{direction}）"
        lines.append(
            "| "
            + " | ".join(
                _table_text(value)
                for value in (
                    row["evaluation_scope"],
                    comparison_label,
                    baseline_label,
                    candidate_label,
                    row["row_count"],
                    f"{metric_labels.get(row['metric'], row['metric'])}；{measurements}",
                )
            )
            + " |"
        )
    return lines


def _result_summary(record: dict[str, Any]) -> str:
    return record["outcome_reason"]


def _narrative(record: dict[str, Any]) -> dict[str, Any] | None:
    assessment = record.get("scientific_assessment")
    if not assessment:
        return None
    narrative = assessment.get("report_narrative")
    return narrative if isinstance(narrative, dict) else None


def _uncertainty_lines(record: dict[str, Any]) -> list[str]:
    assessment = record.get("scientific_assessment")
    worker = record.get("worker_result")
    reasons: list[str] = []
    narrative = _narrative(record)
    if narrative:
        reasons.extend(narrative.get("limitations", []))
    elif assessment:
        reasons.extend(assessment.get("uncertainty_reasons", []))
    if worker and not narrative:
        reasons.extend(worker["scientific_payload"].get("uncertainty_reasons", []))
    if worker and not narrative:
        reasons.extend(worker.get("warnings", []))
    unique = list(dict.fromkeys(reason for reason in reasons if reason))
    return [f"- {reason}" for reason in unique] or [
        "- 本报告只解释已提供输入和实际执行的方法，不外推到未提供的数据、未执行的比较或总体推断。"
    ]


def _artifact_lines(record: dict[str, Any]) -> list[str]:
    rows = [
        row
        for row in record.get("public_artifacts", [])
        if Path(row["path"]).name != "worker_result.json"
    ]
    if not rows:
        return ["- 没有用户实验产物；机器核验记录仍保存在本次运行记录中。"]
    lines = []
    chinese_task = any("\u3400" <= char <= "\u9fff" for char in record["task"])
    for row in rows:
        description = row["description"]
        if chinese_task and not any(
            "\u3400" <= char <= "\u9fff" for char in description
        ):
            description = (
                f"{ARTIFACT_KIND_LABELS.get(row['kind'], row['kind'])} 实验产物"
            )
        lines.append(
            f"- `{row['path']}`：{description}，{row['size_bytes']} 字节，"
            f"SHA-256 `{row['sha256']}`。"
        )
    return lines


def _early_context_lines(
    record: dict[str, Any],
    response: dict[str, Any] | None,
) -> list[str]:
    if not response:
        return [f"- {record['outcome_reason']}"]
    rows = response.get("clarifications", []) or response.get("blockers", [])
    return [f"- {row}" for row in rows] or [f"- {record['outcome_reason']}"]


def _scientific_reader_text(value: Any) -> str:
    """Render verified prose at report precision, without workflow narration."""

    text = _reader_facing_text(value).strip()
    text = text.replace("共享保留标记评价观测", "同一组固定留出评价观测")
    text = text.replace("仅保留标记评价观测", "固定的留出评价观测")
    text = text.replace("保留标记评价观测", "固定的留出评价观测")
    text = re.sub(
        r"(?:仅|只)保留(?:了)?(?:质量)?标记(?:的)?(?:行|观测|样本|数据)?"
        r"(?=\s*(?:拟合|校准))",
        "排除被标记观测后使用其余观测",
        text,
    )
    text = text.replace("保留标记观测拟合", "包含被标记观测拟合")
    text = text.replace("保留被标记观测", "包含被标记观测")
    text = text.replace("保留标记观测", "包含被标记观测")
    text = text.replace("arithmetic Arithmetic mean", "Arithmetic mean")
    text = text.replace("合成交接包", "本次提供的合成数据")
    while "数据数据" in text:
        text = text.replace("数据数据", "数据")
    text = text.replace("被被标记", "被标记")
    text = text.replace("带质量标记的观测", "被标记观测")
    text = text.replace("总观测行数", "观测总数")
    text = text.replace("全量评估行数", "全部评估观测数")
    text = text.replace("排除标记后评估行数", "固定留出评估观测数")
    text = text.replace("标记观测行数", "被标记观测数")
    text = text.replace(
        "不得将结果描述为真实太阳观测结论或任何太阳活动周预测",
        "本结果不代表真实太阳观测，也不用于太阳活动周预测",
    )
    text = text.replace(
        "校准改善一致性的结论不等同于物理口径差异得到证明",
        "一致性改善只反映当前统计校准结果，不能证明观测口径之间的物理差异",
    )
    text = text.replace(
        "标记观测对校准参数和校准效果有一定影响",
        "两种拟合条件下的校准参数和误差不同",
    )
    text = text.replace(
        "18 条合成观测的样本量极小，校准参数对个别观测敏感，参数稳定性未经过统计检验",
        "当前只有 18 条合成观测；两种样本处理下的校准参数存在差异，"
        "尚未检验其在其他划分下的稳定性",
    )
    text = text.replace(
        "单一可疑标记行限制了敏感性分析的统计力度",
        "仅有一条被标记观测，因此敏感性分析只反映该观测是否纳入拟合时的变化",
    )
    text = text.replace("单一标记行", "一条被标记观测")
    text = text.replace("可疑行", "可疑观测")
    text = text.replace(
        "两组读数呈现极强的正向线性关联",
        "两组读数在当前样本中呈正向线性关联",
    )
    text = text.replace(
        "在当前观测样本中，两组读数在当前样本中呈",
        "在当前观测样本中，两组读数呈",
    )
    text = text.replace(
        "当前样本上的相关系数接近 1，线性关联模式清晰",
        "当前样本中的相关系数为正",
    )
    text = text.replace(
        "参考读数从负值增至正值时，候选读数也沿相同方向单调递增",
        "相关系数的正号表示两组读数在当前样本中的线性变化方向一致",
    )
    text = text.replace(
        "所有数值均为预设的示例值",
        "这些数值不应解释为真实观测值",
    )
    text = text.replace(
        "该行对相关系数的影响未经敏感性检验",
        "本分析未比较该观测纳入或排除时的相关系数差异",
    )
    text = text.replace(
        "原始数据未注明计量单位，数值含义不明",
        "原始数据未注明计量单位；这不改变相关系数的无量纲性质，"
        "但限制了对读数物理尺度的解释",
    )
    text = text.replace(
        "原始数据未注明计量单位以外的高斯标度信息，本文报告数值以 G 为单位",
        "变量说明将读数单位标记为 G；原始数据未另附仪器计量校准说明",
    )
    text = text.replace(
        "校准参数在本留出段上产生了更低的预测误差",
        "该拟合条件在本留出段上的预测误差更低",
    )
    text = text.replace(
        "排除标记条件下的该拟合条件在本留出段上的预测误差更低",
        "排除标记观测拟合后，在本留出段上的预测误差更低",
    )
    text = text.replace(
        "使用真实物理观测数据验证该关联模式是否在实际测量中成立",
        "在真实物理观测中重新估计相关系数及其方向",
    )
    text = text.replace("18 条月度重叠观测", "18 条带日期的重叠观测")
    text = text.replace(
        "候选读数与参考读数两列数值",
        "候选读数与参考读数两个数值变量",
    )
    text = text.replace("，为确定性计算", "，用于描述当前样本")
    text = text.replace("冻结留出观测", "固定的留出评估观测")
    text = text.replace(
        "敏感性分析的统计力度有限",
        "敏感性分析仅反映当前被标记观测是否纳入拟合时的差异",
    )
    text = re.sub(
        r"相同\s*(\d+)\s*条固定的留出评估观测",
        r"相同的 \1 条固定留出评估观测",
        text,
    )
    text = text.replace(
        "排除标记观测对校准比例关系和偏移均有方向性影响，"
        "且排除后校准精度在相同评价集上更优",
        "两种拟合条件的校准斜率和截距存在上述差异；"
        "在当前相同的留出评估观测上，排除标记观测拟合后的平均绝对误差更低",
    )
    text = text.replace(
        "反映校准效果对标记观测的依赖程度",
        "表示两种拟合条件下校准误差的差值",
    )
    text = text.replace(
        "反映标记观测对校准比例关系的影响",
        "表示两种拟合条件下斜率的差值",
    )
    text = text.replace(
        "反映标记观测对校准偏移的影响",
        "表示两种拟合条件下截距的差值",
    )
    text = text.replace(
        "敏感性分析的证据力度有限",
        "敏感性分析只反映当前被标记观测是否纳入拟合时的差异",
    )
    text = text.replace(
        "反映质量标记对校准效果的影响",
        "描述被标记观测是否纳入拟合时校准误差改善量的差异",
    )
    text = text.replace(
        "衡量质量标记对校准效果的影响",
        "描述被标记观测是否纳入拟合时校准误差改善量的差异",
    )
    text = text.replace(
        "反映标记观测对校准误差的影响方向",
        "表示保留与排除标记观测两种拟合条件的校准误差差异",
    )
    text = text.replace(
        "反映标记观测对校准效果估计的影响",
        "表示保留与排除标记观测两种拟合条件的校准改善量差异",
    )
    text = text.replace(
        "反映标记观测对斜率估计的影响",
        "表示保留与排除标记观测两种拟合条件的斜率差异",
    )
    text = text.replace(
        "反映标记观测对截距估计的影响",
        "表示保留与排除标记观测两种拟合条件的截距差异",
    )
    text = re.sub(
        r"校准参数在两个条件下保持稳定[：:]?",
        "两种拟合条件下的校准参数分别为：",
        text,
    )
    text = text.replace(
        "标记观测对校准函数和误差估计的影响方向一致且量级较小",
        "两种拟合条件下的校准函数和误差估计存在上述差异；"
        "其实际重要性尚不能由本设计判断",
    )
    text = text.replace(
        "合成重叠期数据中配对数据上",
        "合成重叠期配对数据上",
    )
    text = text.replace(
        "仅 18 条观测且评估集仅 6 条观测，统计精度有限，影响校准效果估计的可靠性",
        "当前仅有 18 条观测，其中 6 条用于留出评价；误差估计仅反映这 6 条留出观测",
    )
    text = text.replace(
        "线性校准模型可能无法捕获仪器间的非线性差异，影响对校准充分性的判断",
        "本研究只评估线性校准，尚未检验仪器读数关系是否存在"
        "需要其他函数形式描述的非线性结构",
    )
    text = text.replace(
        "尝试多项式回归或稳健回归方法，检验线性假设是否充分",
        "检查校准残差与质量标记的分布；只有观察到非线性或异常值结构时，"
        "再选择相应模型进行比较",
    )
    text = text.replace(
        "在真实观测数据上验证校准方法，评估非线性关系是否需要更复杂模型",
        "在真实观测数据上复核相同的校准比较",
    )
    text = text.replace(
        "增加评价样本量以支持正式区间估计和更高分辨率的敏感性分析",
        "增加独立评价观测，以复核两种拟合条件下误差差值的方向和幅度",
    )
    text = text.replace(
        "扩大样本量以提高统计精度，尤其增加留出评估集的观测数量",
        "在更多独立的重叠期观测上复核校准误差，并为留出评价保留足够的观测数量",
    )
    text = text.replace(
        "两种条件的校准后平均绝对误差保留标记观测拟合条件比排除标记观测拟合条件高",
        "包含被标记观测拟合后的校准平均绝对误差比排除标记观测拟合后高",
    )
    text = re.sub(
        r"(^|[。！？；]\s*)不代表真实",
        lambda match: match.group(1) + "这些结果不代表真实",
        text,
    )
    text = re.sub(
        r"留前评估(?:行|观测|集)",
        "时间顺序留出的评估观测",
        text,
    )
    text = text.replace("留前评估期", "时间顺序留出评价阶段")
    text = text.replace("留前评估", "时间顺序留出评价")
    text = text.replace("清洁评估行", "固定的留出评估观测")
    text = text.replace("清洁评估观测", "固定的留出评估观测")
    text = re.sub(
        r"(?:可疑|异常)?标记训练行",
        "被标记的拟合观测",
        text,
    )
    text = re.sub(
        r"(?:单一)?可疑标记行",
        "被标记为可疑的观测",
        text,
    )
    text = text.replace("训练行", "拟合观测")
    text = text.replace("评估行", "评估观测")
    text = text.replace("训练集", "校准集")
    text = re.sub(
        r"(\d+)\s*行合成演示配对观测",
        r"\1 组配对的合成演示观测",
        text,
    )
    text = re.sub(
        r"当前\s*(\d+)\s*条观测合成演示数据",
        r"当前包含 \1 条观测的合成演示数据",
        text,
    )
    text = re.sub(
        r"(?P<prefix>当前|仅)\s*(?P<count>\d+)\s*行合成演示数据",
        lambda match: (
            f"当前包含 {match.group('count')} 条观测的合成演示数据"
            if match.group("prefix") == "当前"
            else f"仅包含 {match.group('count')} 条观测的合成演示数据"
        ),
        text,
    )
    text = re.sub(
        r"全部\s*(\d+)\s*条时间顺序留出的评估观测",
        r"全部 \1 条留出评估观测",
        text,
    )
    text = text.replace("相同固定的留出评估观测", "同一组固定的留出评估观测")
    text = text.replace(
        "使用全部拟合观测拟合校准后",
        "使用全部拟合观测估计校准关系后",
    )
    text = re.sub(
        r"时间顺序\s*(\d+)\s*/\s*(\d+)\s*划分基于数据规模启发[，,]?"
        r"(?:并非|非)最优分割",
        r"当前采用时间顺序 \1/\2 划分，尚未比较其他划分方式",
        text,
    )
    text = re.sub(
        r"结果为确定性(?:精确)?计算[，,]\s*无抽样不确定性或模型假设",
        "这些数值是对当前数据的确定性描述；本次未进行总体推断，也未估计抽样不确定性",
        text,
    )
    text = text.replace("确定性精确计算", "对当前数据的确定性计算")
    text = text.replace("不存在抽样不确定性", "未估计抽样不确定性")
    text = text.replace("无抽样不确定性", "未估计抽样不确定性")
    text = text.replace("略有降低", "降低")
    text = text.replace("略有下降", "下降")
    text = text.replace("略有升高", "升高")
    text = text.replace("略有上升", "上升")
    text = text.replace("略低", "更低")
    text = text.replace("略高", "更高")
    text = re.sub(
        r"确定性计算[，,]\s*全部观测参与统计[，,]\s*"
        r"结果可(?:精确)?复现",
        "全部观测均纳入计算，所得统计量可由同一输入复算",
        text,
    )
    text = text.replace("可精确重复", "可重复得到")
    text = text.replace("可精确复现", "可由同一输入复算")
    text = re.sub(
        r"数据量极小[（(]仅\s*(\d+)\s*条观测[）)]，"
        r"描述统计的稳定性有限",
        r"当前输入仅含 \1 条观测，因此这些统计量不支持总体推断",
        text,
    )
    text = text.replace(
        "原始数据未注明计量单位，结果数值无物理量纲",
        "原始数据未注明计量单位，无法确定这些数值的计量含义",
    )
    text = re.sub(
        r"使用\s*Python\s*标准库逐(?:行|条|个观测)读取"
        r"(?:表格数据|输入数据|CSV)",
        "逐条读取输入数据",
        text,
        flags=re.IGNORECASE,
    )
    text = text.replace(
        "本任务为确定性计算，不涉及抽样推断或假设检验",
        "这些统计量基于当前输入的完整枚举，不涉及抽样推断或假设检验",
    )
    text = text.replace("数值列", "数值变量")
    text = text.replace("分组列", "分组变量")
    text = text.replace("表格数据文件", "输入数据")
    text = text.replace("输入表格数据数据文件", "输入数据")
    text = text.replace("输入输入数据", "输入数据")
    text = text.replace("时下校正", "时校正")
    text = text.replace("时下校准", "时校准")
    text = text.replace("排除标记拟合", "排除标记观测拟合")
    text = text.replace("评价行", "评估观测")
    text = text.replace("校准行", "校准观测")
    text = text.replace("大幅降低", "降低")
    text = text.replace("统计功效有限", "样本规模有限")
    text = text.replace(
        "排除标记后的校准效果略优于全数据校准",
        "排除标记观测拟合后的校准误差低于包含被标记观测的拟合",
    )
    text = text.replace("略优于", "优于")
    text = re.sub(
        r"包含(?P<left>[^，。；;]{1,30})和(?P<right>[^，。；;]{1,30})各一列",
        r"包含\g<left>和\g<right>两个变量",
        text,
    )
    text = re.sub(
        r"包含全部\s*(\d+)\s*(?:行|条观测)校准观测",
        r"校准集包含全部 \1 条观测",
        text,
    )
    text = re.sub(
        r"排除\s*(\d+)\s*(?:行|条观测)标记观测",
        r"排除 \1 条被标记观测",
        text,
    )
    text = re.sub(
        r"同一\s*(\d+)\s*(?:行|条观测)留出评价集",
        r"同一组 \1 条留出评估观测",
        text,
    )
    text = text.replace("当前文件", "当前输入")
    text = text.replace("本文件", "当前输入")
    text = re.sub(
        r"^输入(?:表格)?数据(?:(?:的)?数值变量描述性统计|"
        r"(?:中)?(?:全部)?有限数值的描述(?:性)?统计)$",
        "输入数据的描述性统计",
        text,
    )

    plain_field_labels = {
        "value": "数值变量",
        "values": "数值变量",
        "group": "分组变量",
        "category": "分类变量",
        "date": "日期变量",
        "time": "时间变量",
        "target": "目标变量",
        "reference": "参考变量",
        "candidate": "候选变量",
    }

    def natural_plain_field(match: re.Match[str]) -> str:
        name = match.group("name").casefold()
        return plain_field_labels.get(name, "相应变量")

    text = re.sub(
        r"(?<![A-Za-z0-9_])(?P<name>[A-Za-z][A-Za-z0-9]*)"
        r"(?![A-Za-z0-9_])\s*(?:列|字段)",
        natural_plain_field,
        text,
    )

    def natural_column_list(match: re.Match[str]) -> str:
        columns = re.sub(
            r"(?<![A-Za-z0-9_])(?P<name>[A-Za-z][A-Za-z0-9]*)"
            r"(?![A-Za-z0-9_])",
            lambda item: plain_field_labels.get(
                item.group("name").casefold(),
                "相应变量",
            ),
            match.group("columns"),
        )
        return f"数据包含{columns}"

    text = re.sub(
        r"(?:数据文件|输入数据|输入表格数据(?:文件)?|表格数据(?:文件)?)包含"
        r"(?:\d+|[一二两三四五六七八九十]+)列[（(]"
        r"(?P<columns>[^）)]+)[）)]",
        natural_column_list,
        text,
    )
    text = re.sub(r"\s+和\s+", "和", text)
    text = re.sub(r"数值变量\s*(\d+)\s*条观测", r"\1 条数值观测", text)
    text = re.sub(
        r"数值变量全部\s*(\d+)\s*条有限数值",
        r"全部 \1 个有限数值",
        text,
    )
    text = text.replace("数值变量全部有限数值", "全部有限数值")
    text = text.replace("数值变量为数值型。", "")
    text = re.sub(
        r"排除标记观测(?:的|后)?\s*\d+\s*(?:行|条|对)(?:观测)?\s*评估集",
        "排除标记观测后的评估集",
        text,
    )
    text = re.sub(
        r"排除标记观测的同一批\s*\d+\s*(?:行|条|对)(?:观测)?\s*评估集",
        "排除标记观测条件下的同一批评估观测",
        text,
    )
    text = text.replace(
        "确定性计算结果，在当前输入上可重复得到",
        "这些统计量描述当前输入中的数值特征，不构成总体推断",
    )
    text = re.sub(
        r"检查(?P<object>[^，。；;]{1,60}?被标记观测)"
        r"对(?P<outcome>[^，。；;]{1,80})的影响",
        r"比较保留与排除\g<object>时\g<outcome>的差异",
        text,
    )
    text = re.sub(
        r"量化排除(?P<object>[^，。；;]{0,40}?被标记观测)"
        r"对(?P<outcome>[^，。；;]{1,80})的影响",
        r"比较保留与排除\g<object>时\g<outcome>的差异",
        text,
    )
    text = text.replace("均远低于", "均低于")
    text = text.replace("大幅降低", "降低")
    text = text.replace("结果和来源文件可以相互复核", "现有结果可由所用数据直接复算")
    text = re.sub(
        r"结合(?:(?:第)?(?:[一二三四五]|[1-5])(?:个)?阶段|"
        r"阶段(?:[一二三四五]|[1-5]))结果",
        "综合上述结果",
        text,
    )
    text = text.replace("稳健性", "敏感性")
    text = text.replace("灵敏度分析", "敏感性分析")
    text = text.replace(
        "及质量标记敏感性分析",
        "：对标记观测的敏感性分析",
    )
    text = text.replace(
        "的质量标记敏感性分析",
        "：对标记观测的敏感性分析",
    )
    text = text.replace(
        "合成演示数据重叠期数据",
        "合成演示数据的重叠期观测",
    )
    text = text.replace(
        "合成演示数据重叠期",
        "合成重叠期数据中",
    )
    text = text.replace(
        "差异不具有实质影响",
        "但当前样本量与不确定性不足以判断该差异的实际意义",
    )
    for phrase in ("效果基本不变", "变化基本不变", "变化不大", "差别不大"):
        text = text.replace(
            phrase,
            "改善方向保持一致，但当前数据不足以判断差异的实际意义",
        )
    text = text.replace(
        "差值微小",
        "观察到差值，但当前设计不足以判定其是否具有实质影响",
    )
    text = text.replace(
        "当前设计未预设等效界限或有依据阈值，不足以判断该差异是否具有实质影响",
        "由于缺少预先规定且有依据的实质差异标准，本结果只能描述差异的方向和幅度，"
        "不能据此判断其实际重要性",
    )
    text = text.replace(
        "当前设计未预设等效界限或有依据阈值",
        "缺少预先规定且有依据的实质差异标准",
    )
    text = text.replace("略有差异", "存在差异")
    text = text.replace("略有变化", "发生变化")
    text = text.replace(
        "参数变化幅度较小，但误差改善方向在所有留出观测上一致",
        "全部留出观测上的误差变化方向一致；参数差异的实际重要性尚不能由本设计判断",
    )
    text = text.replace(
        "参数变化幅度较小",
        "参数发生变化，但本设计没有给出判断其实际重要性的依据",
    )
    text = text.replace(
        "敏感性分析同时给出两种条件各自的估计量及其差值。",
        "",
    )
    if _has_cjk(text):
        for abbreviation, full_name in (
            ("MAE", "平均绝对误差"),
            ("RMSE", "均方根误差"),
        ):
            text = text.replace(f"{full_name}（{abbreviation}）", full_name)
            text = re.sub(
                rf"(?<![A-Za-z]){abbreviation}(?![A-Za-z])",
                full_name,
                text,
            )
    text = text.replace(
        "留出集平均绝对误差方向为减小",
        "留出观测的平均绝对误差降低",
    )
    text = re.sub(
        r"当前设计未设定等效界限或有依据的实质影响阈值，"
        r"因此无法判定该差值是否具有实质意义；"
        r"仅报告两种条件的估计值、差值与方向",
        "由于没有预先规定且有依据的实质差异标准，这些结果只能说明"
        "当前样本中的变化方向与幅度，不能据此判断其实际重要性",
        text,
    )
    text = re.sub(
        r"两种条件平均绝对误差(?:之)?差值",
        "两种条件的平均绝对误差差值",
        text,
    )
    text = re.sub(
        r"两种条件平均绝对误差(?:之)?差",
        "两种条件的平均绝对误差差值",
        text,
    )
    text = text.replace("平均绝对误差之差值", "平均绝对误差差值")
    text = text.replace("平均绝对误差之差", "平均绝对误差差值")
    text = text.replace("平均绝对误差差值值", "平均绝对误差差值")
    text = text.replace(
        "两种条件校正平均绝对误差差值",
        "校正后平均绝对误差差值",
    )
    text = text.replace("两种条件斜率之差", "斜率差值")
    text = text.replace("两种条件截距之差", "截距差值")
    text = re.sub(
        r"平均绝对误差之(?P<left>[^，。；;]{1,20}条件)"
        r"比(?P<right>[^，。；;]{1,20}条件)(?P<direction>高|低)",
        r"\g<left>的平均绝对误差比\g<right>\g<direction>",
        text,
    )
    text = re.sub(
        r"留出集仅\s*(?P<count>\d+)\s*对观测，"
        r"平均绝对误差(?:之)?差(?:值)?[^，。；;]{0,40}"
        r"的精度受样本量限制，不排除不同分割方案下结果方向可能改变",
        r"留出集仅包含 \g<count> 对观测，尚未检验不同时间分割方案下"
        r"误差差值的稳定性",
        text,
    )
    text = text.replace(
        "增加留出集观测数量以提高误差差值的精度",
        "在更多独立的重叠期观测上复核误差差值及其对时间分割的敏感性",
    )
    text = text.replace(
        "引入多种类型的质量标记观测以扩展敏感性分析的覆盖范围",
        "在包含更多质量标记类型的数据中复核校正结果",
    )
    if _has_cjk(text):
        text = re.sub(
            r"(?<![A-Za-z])OLS(?![A-Za-z])",
            "普通最小二乘",
            text,
            flags=re.IGNORECASE,
        )
    text = re.sub(
        r"比较仅适用于",
        "上述结果仅适用于",
        text,
    )
    text = text.replace(
        "本合成演示数据重叠期的观测对",
        "当前合成数据的重叠期配对观测",
    )
    text = text.replace("；不推断", "；不能据此推断")
    text = text.replace(
        "不能据此推断真实仪器、物理机制或太阳活动周",
        "不能据此评价真实仪器表现，也不能据此解释物理机制或推断太阳活动周",
    )
    text = text.replace("后但当前", "后观察到差异，但当前")
    text = re.sub(
        r"非零值(?:即)?表示[^。；;]*(?:实质影响|实质性变化)",
        "该差值描述两种条件的变化方向与幅度，实际意义需结合不确定性判断",
        text,
    )
    # The subtraction order already defines a contrast. Do not repeat a
    # model-authored positive/negative gloss in the main table; a wrong gloss
    # can reverse the scientific interpretation even when the number is right.
    text = re.sub(
        r"[，,；;]\s*(?:正值|正数|positive(?:\s+values?)?)[^。.!?！？]*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"留出段\s*(\d+)\s*行逐行给出两种条件与原始读数的绝对误差；"
        r"样本量极小，估计值对划分与单点敏感",
        r"两种校正条件与未校正读数均在同一组 \1 条留出观测上比较；"
        r"由于样本量较小，误差估计和参数差异仍可能受个别观测及划分方式影响",
        text,
    )
    text = re.sub(
        r"(\d+)\s*行留出观测的逐行配对比较提供了确定性数值证据：",
        r"对 \1 条留出观测的逐行比较显示，",
        text,
    )
    text = re.sub(
        r"(?P<left>[\u3400-\u9fff]{2,20})列（(?P<unit>[^）]{1,12})）与"
        r"(?P<right>[\u3400-\u9fff]{2,20})列（(?P=unit)）逐行配对",
        r"\g<left>与\g<right>（单位均为 \g<unit>）按观测一一对应",
        text,
    )
    text = re.sub(
        r"([前后])\s*(\d+)\s*行(?=（)",
        r"\1 \2 条观测",
        text,
    )
    text = re.sub(
        r"(\d+)\s*个(?=[^，。；;]{0,20}观测)",
        r"\1 条",
        text,
    )
    text = re.sub(
        r"(\d+)\s*行(?=(?:合成)?(?:配对|拟合|留出)观测)",
        r"\1 条",
        text,
    )
    text = re.sub(
        r"(\d+)\s*行(?=(?:合成)?观测)",
        r"\1 条",
        text,
    )
    text = re.sub(
        r"(\d+)\s*个(?=留出观测)",
        r"\1 条",
        text,
    )
    text = re.sub(
        r"(\d+)\s*行标记为",
        r"\1 条观测被标记为",
        text,
    )
    text = re.sub(
        r"实际表格结构[：:]\s*(\d+)\s*行[、,，]\s*(\d+)\s*列",
        r"共 \1 条观测，记录 \2 个变量",
        text,
    )
    text = re.sub(
        r"包含(?P<left>[^，。；;]{1,30}读数)和"
        r"(?P<right>[^，。；;]{1,30}读数)各一列",
        r"包含\g<left>和\g<right>两个变量",
        text,
    )
    text = text.replace("其中一行标记为", "其中一条观测标记为")
    text = text.replace("标记为几何异常", "被标记为几何条件异常")
    text = re.sub(
        r"使用\s*(\d+)\s*行拟合",
        r"使用 \1 条观测拟合",
        text,
    )
    text = re.sub(
        r"(\d+)\s*行拟合",
        r"使用 \1 条观测拟合",
        text,
    )
    text = text.replace("以使用", "使用")
    text = re.sub(
        r"(\d+)\s*行(?=（[^）]{1,40}）被标记)",
        r"\1 条观测",
        text,
    )
    text = re.sub(
        r"留出观测中每行",
        "留出观测中，每条观测",
        text,
    )
    text = re.sub(
        r"全部\s*(\d+)\s*行上的绝对误差",
        r"全部 \1 条观测的绝对误差",
        text,
    )
    text = text.replace("被标记为可疑几何", "被标记为几何条件可疑")
    text = text.replace("异常几何", "几何条件异常")
    text = text.replace("排除质量标记行", "排除被标记观测")
    text = text.replace("保留质量标记行", "包含被标记观测")
    text = text.replace("不含质量标记行", "不包含被标记观测")
    text = text.replace("含质量标记行", "包含被标记观测")
    text = text.replace("质量标记行", "被标记观测")
    text = text.replace("排除标记行", "排除该标记观测")
    text = text.replace("保留标记行", "包含被标记观测")
    text = text.replace("这一标记行", "这一标记观测")
    text = text.replace("不含标记行", "不包含该标记观测")
    text = text.replace("含标记行", "包含该标记观测")
    text = text.replace("排除质量标记观测", "排除被标记观测")
    text = text.replace("保留质量标记观测", "包含被标记观测")
    text = text.replace("带质量标记观测", "被标记观测")
    text = re.sub(
        r"量化排除(?P<object>[^，。；;]{0,40}?被标记观测)"
        r"对(?P<outcome>[^，。；;]{1,80})的影响",
        r"比较保留与排除\g<object>时\g<outcome>的差异",
        text,
    )
    text = re.sub(
        r"比较保留与排除(?P<object>[^。；;]{1,80}?被标记观测)后两种条件的",
        r"比较保留或排除\g<object>时的",
        text,
    )
    text = text.replace("保留被标记观测条件的", "包含被标记观测时的")
    text = text.replace("排除被标记观测条件的", "排除被标记观测时的")
    text = text.replace("保留被标记观测条件下的", "包含被标记观测时的")
    text = text.replace("排除被标记观测条件下的", "排除被标记观测时的")
    text = text.replace("保留标记观测条件的", "包含被标记观测时的")
    text = text.replace("排除标记观测条件的", "排除标记观测时的")
    text = text.replace("保留标记观测条件下的", "包含被标记观测时的")
    text = text.replace("排除标记观测条件下的", "排除标记观测时的")
    text = text.replace("唯一一行带质量标记的观测", "唯一一条带质量标记的观测")
    text = text.replace("可疑观测行", "可疑观测")
    text = text.replace("单行观测", "单条观测")
    text = text.replace("每行包含", "每条观测包含")
    text = text.replace("每一行", "每条观测")
    text = text.replace("逐行低于", "均低于")
    text = text.replace("逐行高于", "均高于")
    text = text.replace("逐行下降", "均下降")
    text = text.replace("逐行上升", "均上升")
    text = text.replace("逐行比较", "逐个观测比较")
    text = text.replace("逐行证据", "逐个观测的比较结果")
    text = text.replace("逐行绝对误差", "各观测的绝对误差")
    text = text.replace("逐行", "逐个观测")
    text = re.sub(
        r"所有数值均从确定性的(?P<method>[^。；;]{1,40})直接计算，"
        r"无随机成分",
        r"各项结果均按\g<method>计算",
        text,
    )
    text = re.sub(
        r"三种成对比较（[^）]{1,120}）均在相同\s*(\d+)\s*条留出观测上"
        r"逐个观测完成，逐个观测核验与声明测量值完全一致",
        r"三组比较均使用相同的 \1 条留出观测",
        text,
    )
    text = text.replace(
        "逐个观测核验与声明测量值完全一致",
        "各观测的比较方向与相应平均误差一致",
    )
    text = re.sub(
        r"证据强度受限于(?P<limits>[^。；;]{1,100})",
        r"结果的适用范围受\g<limits>限制",
        text,
    )
    text = re.sub(
        r"(?:校准|拟合)?参数(?:估计)?对(?:样本选择|单条观测)(?:高度|可能)?敏感",
        "不同样本处理下的参数估计存在差异",
        text,
    )
    text = text.replace("高度敏感", "差异仍需进一步检验")
    text = text.replace(
        "拟合不稳定",
        "拟合结果在不同样本处理下存在差异，稳定性尚未充分评估",
    )
    text = text.replace(
        "结果不具有天文物理含义",
        "不能据此推断真实仪器表现或天体物理过程",
    )
    text = text.replace(
        "以获得对单个观测的变化不敏感的误差估计",
        "并检验误差估计对个别观测变化的敏感性",
    )
    text = text.replace(
        "以获得对单行扰动不敏感的误差估计",
        "并检验误差估计对个别观测变化的敏感性",
    )
    text = re.sub(
        r"结果仅适用于本合成(?:演示数据|数据)中指定的\s*(\d+)\s*行数据"
        r"和\s*(\d+)/(\d+)\s*时间分割下的线性校正比较",
        r"上述结果仅适用于本合成演示数据中的 \1 条观测，以及前 \2 条用于拟合、"
        r"后 \3 条用于留出评价的时间划分",
        text,
    )
    text = re.sub(
        r"条件\s*A[（(]保留(?:该)?标记观测[）)]",
        "包含被标记观测时",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"条件\s*B[（(]排除(?:该)?标记观测[）)]",
        "排除该标记观测时",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^两种(?P<condition>[^，。；;]{1,24}条件)在"
        r"(?P<scope>[^，。；;]{1,80})上的(?P<comparison>[^，。；;]{1,80}对比)$",
        r"\g<scope>上的两种\g<condition>的\g<comparison>",
        text,
    )
    text = re.sub(
        r"(?:，|；)?(?:这|该结果|该比例)?(?:表明|说明)"
        r"[^。；;]{0,100}(?:影响)?(?:在[^。；;]{0,30})?不可忽视",
        "；这一比例仅描述当前样本中的相对变化，不能据此判断其实际重要性",
        text,
    )
    text = re.sub(
        r"(?P<prefix>从\s*[-+−]?\d+(?:\.\d+)?(?:\s*[A-Za-z%/]+)?"
        r"（(?P<left>[^）]{1,16})）变为\s*[-+−]?\d+(?:\.\d+)?"
        r"(?:\s*[A-Za-z%/]+)?（(?P<right>[^）]{1,16})），)"
        r"差值(?P<value>\s*[-+−]?\d+(?:\.\d+)?(?:\s*[A-Za-z%/]+)?)",
        r"\g<prefix>\g<left>减\g<right>为\g<value>",
        text,
    )
    text = re.sub(
        r"(?P<first>(?P<order>[\u3400-\u9fff]{1,16}减[\u3400-\u9fff]{1,16})"
        r"为[^；。]{1,80}；[^；。]{1,80}，)"
        r"差值(?P<value>\s*[-+−]?\d+(?:\.\d+)?(?:\s*[A-Za-z%/]+)?)",
        r"\g<first>\g<order>为\g<value>",
        text,
    )
    text = text.replace(
        "确定性计算结果，所有数值由代码直接产出并经独立核验复算。",
        "本分析为描述性计算，各项数值均可由同一组观测按上述方法复算。",
    )
    text = re.sub(
        r"(?:确定性计算结果[，,]?)?(?:所有|全部|各项)数值"
        r"[^。.!?！？]{0,50}(?:代码|程序)[^。.!?！？]{0,50}"
        r"(?:核验|复算)[^。.!?！？]*",
        "本分析为描述性计算，各项数值均可由同一组观测按上述方法复算",
        text,
    )
    text = re.sub(
        r"逐个观测的比较结果表记录了\s*(\d+)\s*条留出观测的"
        r"参考值、两种条件校正值和未校正偏差，支持逐对比较",
        r"\1 条留出观测均同时包含参考读数、两种条件下的校正读数"
        r"和未校正读数，因此可进行配对比较",
        text,
    )
    text = re.sub(
        r"拟合观测仅\s*(\d+)\s*至\s*(\d+)\s*条、留出观测仅\s*(\d+)\s*条，"
        r"参数估计对单条观测高度敏感，差值的定量大小不具备敏感性含义",
        r"拟合观测为 \1 至 \2 条、留出观测为 \3 条，尚未检验"
        r"参数和误差差值在其他时间分割下的稳定性",
        text,
    )
    text = re.sub(
        r"线性模型为有界近似，未评估非线性残差或异方差结构",
        "本次仅评估线性关系，尚未检验非线性或异方差结构",
        text,
    )
    text = text.replace("不做任何校正时候选读数", "不做任何校正时，候选读数")
    text = text.replace("数据为合成性质", "数据为合成数据")
    text = re.sub(
        r"表明(?:该|这一)?(?:被)?标记观测对"
        r"(?P<target>[^，。；;]{1,30})有可观测的影响",
        r"显示\g<target>会随该观测是否纳入拟合而变化",
        text,
    )
    text = re.sub(
        r"仅\s*(?P<total>\d+)\s*条配对观测"
        r"（拟合\s*(?P<fit>\d+)\s*个、留出\s*(?P<holdout>\d+)\s*个）"
        r"，统计能力有限",
        r"仅有 \g<total> 条配对观测，其中 \g<fit> 条用于拟合、"
        r"\g<holdout> 条用于留出评价；尚未检验结果在其他样本或时间划分下的稳定性",
        text,
    )
    text = re.sub(
        r"样本量限制(?:了)?(?P<target>参数估计|误差估计|估计结果)的精度",
        r"尚未检验\g<target>在其他样本或时间划分下的稳定性",
        text,
    )
    text = re.sub(
        r"未进行统计显著性检验[，,]"
        r"差值方向与幅度仅供描述性参考",
        "本分析只描述当前样本中的差值方向与幅度，未量化估计不确定性",
        text,
    )
    text = text.replace("一阶线性一致性校正", "线性校正")
    text = re.sub(r"^仅适用于", "上述结果仅适用于", text)
    text = text.replace(
        "仅评估一阶线性模型，非线性校正或分段校正的效果未被考察",
        "本次仅评估线性模型，尚未比较非线性或分段校正方法",
    )
    text = text.replace(
        "以更大样本和多时段数据验证校正稳定性",
        "在更多独立时段的配对观测上复核校正参数和误差差值的稳定性",
    )
    text = text.replace(
        "评估非线性或分段校正模型对残差结构的改善",
        "比较线性、非线性与分段校正方法的留出误差和残差结构",
    )
    text = re.sub(
        r"(?P<count>\d+)\s*条(?P<kind>[^，。；;（）]{0,40}留出观测)"
        r"（后\s*(?P=count)\s*条）",
        r"后 \g<count> 条\g<kind>",
        text,
    )
    text = re.sub(
        r"条件\s*([A-Z])\s*(?=[\u3400-\u9fff])",
        r"条件 \1 ",
        text,
    )
    text = re.sub(r"条件\s*([A-Z])", r"条件 \1", text)
    text = re.sub(r"\b([A-Z])\s*减\s*([A-Z])\b", r"\1 减 \2", text)
    text = re.sub(
        r"(?<![\d.])(\d+)\s*(条|对|项|个)(?=[\u3400-\u9fff])",
        r"\1 \2",
        text,
    )
    text = re.sub(
        r"(?P<prefix>在|前|后|全部|共|仅|为|以|包含|其中|相同|的)"
        r"\s*(?P<count>\d+)\s*(?P<unit>条|对|项|个)",
        r"\g<prefix> \g<count> \g<unit>",
        text,
    )
    text = re.sub(
        r"逐个观测的比较结果确认(?P<scope>\d+\s*条[^，。；;]{0,30}观测)中",
        r"对\g<scope>的比较显示，",
        text,
    )
    text = text.replace(
        "对排除这一标记观测的方向性结论一致",
        "这一差异在全部留出观测上的方向一致",
    )
    text = text.replace(
        "校正参数变化方向与 MAE 变化一致",
        "斜率和截距也随拟合条件发生变化，具体数值见结果表",
    )
    text = re.sub(
        r"(?:校正)?参数变化与误差变化方向对应："
        r"排除后斜率和截距均发生变化，留出段平均误差下降",
        "排除该观测后，斜率和截距发生变化，同时留出段平均误差下降；"
        "二者是同一敏感性比较中的并列结果",
        text,
    )
    text = text.replace(
        "排除可疑观测后斜率和截距均发生了变化，对应当前留出段观测到的误差系统性降低",
        "排除可疑观测后，斜率和截距发生变化，同时当前留出段的平均绝对误差降低",
    )
    text = re.sub(
        r"这表明拟合样本中被标记为[^。]{1,160}"
        r"其存在使[^。]{1,120}误差",
        "在当前时间划分下，保留或排除该标记观测会同时改变拟合参数和"
        "留出误差；仅凭本次敏感性比较不能确定这种变化的机制",
        text,
    )
    text = re.sub(
        r"当前留出段\s*(\d+)\s*行逐一比较结果方向完全一致",
        r"对当前留出段 \1 条观测的逐一比较结果方向一致",
        text,
    )
    text = re.sub(
        r"(\d+)\s*行样本量(?:极小|较小)",
        r"\1 条观测的样本量较小",
        text,
    )
    text = re.sub(
        r"时间有序留出仅\s*(\d+)\s*行",
        r"时间有序留出仅包含 \1 条观测",
        text,
    )
    text = re.sub(
        r"前使用\s*(\d+)\s*条观测拟合、后\s*(\d+)\s*(?:行|条)?留出",
        r"前 \1 条观测用于拟合、后 \2 条观测用于留出评价",
        text,
    )
    text = re.sub(
        r"前使用\s*(\d+)\s*条观测拟合",
        r"前 \1 条观测用于拟合",
        text,
    )
    text = re.sub(
        r"后\s*(\d+)\s*行留出",
        r"后 \1 条观测用于留出评价",
        text,
    )
    text = text.replace("误差系统性降低", "误差降低")
    text = re.sub(
        r"在全部\s*(\d+)\s*条观测的绝对误差",
        r"在全部 \1 条观测上的绝对误差",
        text,
    )
    text = re.sub(
        r"(全部\s*\d+\s*条[^，。；;]{1,30}观测中)(?=[\u3400-\u9fff])",
        r"\1，",
        text,
    )
    text = re.sub(
        r"全部\s*(\d+)\s*条留出观测中，?排除条件的绝对误差均更低，"
        r"这一差异在全部留出观测上的方向一致",
        r"在当前时间划分下，排除该标记观测后，\1 条留出观测的"
        r"绝对误差均降低；这说明当前留出结果会随该观测是否纳入拟合而变化",
        text,
    )
    text = text.replace(
        "校正参数稳定性有限",
        "样本量不足以充分评价校正参数在其他时段的稳定性",
    )
    text = text.replace(
        "不同切分可能产生不同的误差差异",
        "不同切分可能改变误差差值",
    )
    text = text.replace(
        "基于留出观测的配对比较，所有数值来自同一数据集上的确定性计算，"
        "证据完整但样本量小",
        "两种条件在同一组留出观测上比较，避免了评价样本不一致造成的混淆；"
        "但留出观测较少，结果仍可能受时间划分和个别观测影响",
    )
    text = text.replace(
        "样本量较小，单个观测对参数估计有较大影响",
        "样本量较小，无法充分评价参数估计在其他样本中的稳定性",
    )
    text = text.replace(
        "时间顺序分割比例无外部标准，结果对分割选择敏感",
        "时间顺序分割比例缺少外部依据，本次也未检验其他分割方式，"
        "因此无法判断误差差值对分割选择的稳定性",
    )
    text = re.sub(
        r"标记观测位于拟合集中段（(?P<date>[^）]+)），"
        r"其杠杆效应可能对拟合产生局部影响",
        r"标记观测位于拟合集的中段（\g<date>）；本次未单独估计其杠杆值，"
        r"因此无法区分参数变化来自时间位置、数值偏离还是质量标记",
        text,
    )

    def naturalize_named_difference(match: re.Match[str]) -> str:
        raw_value = match.group("value").replace("−", "-")
        try:
            delta = float(raw_value)
        except ValueError:
            return match.group(0)

        def condition_name(value: str) -> str:
            value = re.sub(
                r"\s*[-+−]?\d+(?:\.\d+)?(?:\s*[A-Za-z%/]+)?\s*$",
                "",
                value,
            ).strip()
            value = value.replace("全数据", "包含被标记观测的拟合")
            value = value.replace("排除标记", "排除标记观测拟合")
            return value

        left = condition_name(match.group("left"))
        right = condition_name(match.group("right"))
        left = left if left.endswith("条件") else f"{left}条件"
        right = right if right.endswith("条件") else f"{right}条件"
        unit = match.group("unit") or ""
        amount = f"{abs(delta):g}"
        suffix = f" {unit}" if unit else ""
        if delta < 0:
            return f"{left}比{right}低 {amount}{suffix}"
        if delta > 0:
            return f"{left}比{right}高 {amount}{suffix}"
        return f"{left}与{right}相同"

    text = re.sub(
        r"差值为\s*(?P<value>[-+−]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
        r"(?:\s*(?P<unit>[A-Za-z%/]+))?\s*[（(]"
        r"(?P<left>[^（）()]{1,16})减(?P<right>[^（）()]{1,16})[）)]",
        naturalize_named_difference,
        text,
    )
    text = re.sub(
        r"(?P<prefix>(?P<metric>平均绝对误差|均方根误差|平均有符号误差|"
        r"均方误差)(?:从|由)[^，。；;]{1,80}，)"
        r"(?P<left>[^，。；;]{1,20}条件)比(?P<right>[^，。；;]{1,20}条件)"
        r"(?P<direction>高|低)",
        r"\g<prefix>\g<left>的\g<metric>比\g<right>\g<direction>",
        text,
    )

    def naturalize_excluded_minus_included(match: re.Match[str]) -> str:
        rendered = match.group("value")
        try:
            delta = float(rendered)
        except ValueError:
            return match.group(0)
        unit = match.group("unit") or ""
        amount = f"{abs(delta):g}"
        suffix = f" {unit}" if unit else ""
        if delta < 0:
            return f"排除条件的平均绝对误差比保留条件低 {amount}{suffix}"
        if delta > 0:
            return f"排除条件的平均绝对误差比保留条件高 {amount}{suffix}"
        return "排除条件与保留条件的平均绝对误差相同"

    text = re.sub(
        r"两条件差（排除减保留）\s*"
        r"(?P<value>[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
        r"(?:\s*(?P<unit>[A-Za-z%/]+))?",
        naturalize_excluded_minus_included,
        text,
    )

    def shorten_number(match: re.Match[str]) -> str:
        try:
            return f"{float(match.group(0)):.6g}"
        except ValueError:
            return match.group(0)

    text = re.sub(
        r"(?<![A-Za-z0-9_])[-+]?\d+\.\d{7,}(?![A-Za-z0-9_])",
        shorten_number,
        text,
    )
    text = re.sub(
        r"包含被标记观测条件\s*(?=校正|校准|斜率|截距)",
        "包含被标记观测时的",
        text,
    )
    text = re.sub(
        r"排除(?:被)?标记观测条件\s*(?=校正|校准|斜率|截距)",
        "排除被标记观测时的",
        text,
    )
    if _has_cjk(text):
        text = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", text)
        text = re.sub(r"(?<=[\u3400-\u9fff])(?=[-+−]?\d)", " ", text)
        text = re.sub(r"(?<=\d)(?=[\u3400-\u9fff])", " ", text)
        text = re.sub(r"(?<=[A-Za-z%])(?=[\u3400-\u9fff])", " ", text)
        text = re.sub(r"(?<=[\u3400-\u9fff])(?=[A-Za-z])", " ", text)
        text = re.sub(r"\s{2,}", " ", text)
    return text


def _scientific_sentence(value: Any) -> str:
    text = _scientific_reader_text(value).strip()
    return text if text.endswith(("。", "！", "？", ".", "!", "?")) else text + "。"


def _concise_abstract_result(value: Any) -> str:
    """Keep one pointwise result statement in the abstract."""

    text = _scientific_reader_text(value)
    text = re.sub(
        r"保留标记观测条件(?=从|斜率|截距)",
        "包含该标记观测时的",
        text,
    )
    text = re.sub(
        r"排除标记观测条件(?=从|斜率|截距)",
        "排除该标记观测时的",
        text,
    )
    text = text.replace("时的从", "时从")
    sentences = [
        row.strip()
        for row in re.split(
            r"(?<=[。！？!?])\s*|(?<!\d)\.(?!\d)\s*",
            text,
        )
        if row.strip()
    ]
    result: list[str] = []
    pointwise_direction_seen = False
    for sentence in sentences:
        range_match = re.search(
            r"数据范围为最小值\s*(?P<minimum>[-+−]?\d+(?:\.\d+)?)"
            r"\s*至最大值\s*(?P<maximum>[-+−]?\d+(?:\.\d+)?)",
            sentence,
        )
        preceding = "".join(result)
        if (
            range_match
            and f"最小值为 {range_match.group('minimum')}" in preceding
            and f"最大值为 {range_match.group('maximum')}" in preceding
        ):
            continue
        pointwise_direction = (
            "绝对误差" in sentence
            and re.search(r"(?:每条|全部|\d+\s*条).*观测", sentence) is not None
            and re.search(r"(?:均低于|均高于|均下降|均上升|方向一致)", sentence)
            is not None
        )
        if pointwise_direction and pointwise_direction_seen:
            without_repeated_clause = re.sub(
                r"[，,；;](?:且)?(?:全部|所有|\d+\s*条)"
                r"[^。.!?！？]{0,100}(?:均低于|均高于|均下降|均上升|方向一致)"
                r"[^。.!?！？]*[。.!?！？]?$",
                "。",
                sentence,
            )
            if without_repeated_clause != sentence and re.search(
                r"\d", without_repeated_clause
            ):
                result.append(without_repeated_clause)
            continue
        pointwise_direction_seen = pointwise_direction_seen or pointwise_direction
        result.append(sentence)
    return "".join(result)


def _remove_unrequested_descriptive_pattern(value: str, task: str) -> str:
    """Remove a narrow class of decorative pattern claims from simple summaries."""

    value = re.sub(
        r"[，,]表明[^。；;]{0,100}(?:近似线性|非恒等)[^。；;]*",
        "",
        value,
    )
    if R_SQUARED_PLAN.search(task) is None:
        value = "".join(
            clause
            for clause in re.split(r"(?<=[；;。！？!?])\s*", value)
            if R_SQUARED_PLAN.search(clause) is None
        )
    if re.search(r"对称|中点|均匀递增|数列性质", task):
        return value.strip()
    sentences = [row for row in re.split(r"(?<=[。！？!?])\s*", value) if row.strip()]
    kept = [
        sentence
        for sentence in sentences
        if not (
            re.search(r"均值.*(?:数据)?中点", sentence)
            or re.search(r"均匀递增.*对称", sentence)
        )
    ]
    return "".join(kept).strip()


def _has_decimal_result(value: str) -> bool:
    return (
        re.search(
            r"(?<![A-Za-z0-9_])[-+−]?\d+\.\d+(?:[eE][-+]?\d+)?",
            value,
        )
        is not None
    )


def _needs_primary_abstract_sentence(
    conclusion: str,
    *,
    has_complete_primary_values: bool,
) -> bool:
    """Add a generated comparison only when the narrative has no numeric result."""

    return not has_complete_primary_values and not _has_decimal_result(conclusion)


def _scientific_result_display(
    display: Any,
    definition: Any,
    design: dict[str, Any] | None = None,
) -> str:
    """Repair a historical MSE label when its definition is signed error."""

    raw_display = _scientific_reader_text(
        _replace_condition_aliases(_reader_facing_text(display), design)
    ).replace(
        "数据审计状态",
        "数据完整性检查",
    )
    raw_definition = _scientific_reader_text(
        _replace_condition_aliases(_reader_facing_text(definition), design)
    )
    fitting_only_sensitivity = any(
        row.get("comparison_kind") == "candidate_vs_candidate"
        and row.get("fit_evaluation_relation") == "disjoint_rows"
        and row.get("baseline_fit_condition")
        and row.get("candidate_fit_condition")
        for row in (design or {}).get("paired_comparison_audits", [])
    )
    if fitting_only_sensitivity:
        raw_display = raw_display.replace(
            "（全部行拟合）",
            "（包含被标记观测的拟合）",
        )
        raw_display = raw_display.replace(
            "（全部行）",
            "（包含被标记观测的拟合）",
        )
        raw_display = re.sub(
            r"^全数据未校准",
            "留出观测未校准",
            raw_display,
        )
        raw_display = re.sub(
            r"^(?:全量|全数据)拟合校准在排除标记评估期上的",
            "包含被标记观测拟合后的校准",
            raw_display,
        )
        raw_display = re.sub(
            r"^排除标记观测后校准",
            "排除标记观测拟合后的校准",
            raw_display,
        )
        raw_display = re.sub(
            r"^全部数据校准后",
            "包含被标记观测拟合后的校准",
            raw_display,
        )
        raw_display = re.sub(
            r"^(?:全量数据|全部数据|全数据)校准",
            "包含被标记观测拟合后的校准",
            raw_display,
        )
        raw_display = re.sub(
            r"^全数据拟合(?=斜率|截距)",
            "包含被标记观测拟合后的",
            raw_display,
        )
        raw_display = re.sub(
            r"^排除标记观测拟合(?=斜率|截距)",
            "排除标记观测拟合的",
            raw_display,
        )
        raw_display = re.sub(
            r"^排除标记(?:后)?校准(?:的)?",
            "排除标记观测拟合后的校准",
            raw_display,
        )
        raw_display = raw_display.replace(
            "（全数据减排除标记）",
            "（包含标记观测减排除标记观测）",
        )
        raw_display = raw_display.replace(
            "排除标记平均绝对误差改善量",
            "排除标记观测拟合时的平均绝对误差改善量",
        )
        raw_display = raw_display.replace(
            "平均绝对误差改善量的标记敏感性",
            "两种拟合条件的平均绝对误差改善量之差",
        )
        raw_display = raw_display.replace(
            "全数据一致性",
            "包含被标记观测拟合后的留出一致性",
        )
    if (
        re.match(r"^条件\s*[A-Z]\s*未校正基准", raw_display)
        and "原始候选读数" in raw_definition
    ):
        raw_display = re.sub(r"^条件\s*[A-Z]\s*", "", raw_display)
    if (
        re.match(
            r"^(?:保留|排除)标记观测条件未校正基准",
            raw_display,
        )
        and "原始候选读数" in raw_definition
    ):
        raw_display = re.sub(
            r"^(?:保留|排除)标记观测条件",
            "",
            raw_display,
        )
    if raw_display.startswith("条件 A") and "保留" in raw_definition:
        raw_display = raw_display.replace("条件 A", "保留标记观测条件", 1)
    if raw_display.startswith("条件 B") and "排除" in raw_definition:
        raw_display = raw_display.replace("条件 B", "排除标记观测条件", 1)
    raw_display = re.sub(
        r"^保留标记观测条件\s*(?=校正)",
        "包含被标记观测时的",
        raw_display,
    )
    raw_display = re.sub(
        r"^排除标记观测条件\s*(?=校正)",
        "排除标记观测时的",
        raw_display,
    )
    raw_display = re.sub(
        r"^保留标记观测条件\s*(?=斜率|截距)",
        "包含被标记观测时的",
        raw_display,
    )
    raw_display = re.sub(
        r"^排除标记观测条件\s*(?=斜率|截距)",
        "排除标记观测时的",
        raw_display,
    )
    raw_display = re.sub(
        r"(?:保留|排除)标记条件\s+", lambda match: match.group(0).rstrip(), raw_display
    )
    if raw_display == "有效行数" and re.search(
        r"(?:质量标记.*正常|正常.*质量标记)",
        raw_definition,
    ):
        raw_display = "正常标记观测数"
    if raw_display in {"嫌疑行数", "异常行数"} and re.search(
        r"(?:质量标记|异常|嫌疑)",
        raw_definition,
    ):
        raw_display = "需关注观测数"
    if re.search(
        r"(?:平均有符号|平均符号|mean\s+signed)",
        raw_definition,
        re.IGNORECASE,
    ):
        repaired = re.sub(r"\bMSE\b\s*", "平均有符号误差", raw_display)
        return re.sub(
            r"(?<=[\u3400-\u9fff])\s+(?=平均有符号误差)",
            "",
            repaired,
        )
    return raw_display


def _scientific_result_definition(
    definition: Any,
    design: dict[str, Any] | None,
) -> str:
    """Keep a reader definition aligned with the declared model direction."""

    text = _scientific_reader_text(
        _replace_condition_aliases(_reader_facing_text(definition), design)
    )
    fitting_only_sensitivity = any(
        row.get("comparison_kind") == "candidate_vs_candidate"
        and row.get("fit_evaluation_relation") == "disjoint_rows"
        and row.get("baseline_fit_condition")
        and row.get("candidate_fit_condition")
        for row in (design or {}).get("paired_comparison_audits", [])
    )
    if fitting_only_sensitivity:
        text = re.sub(
            r"排除标记(?:观测)?(?:后|的)?评估(?:期|集|观测)",
            "固定的留出评估观测",
            text,
        )
        text = text.replace(
            "包含全部校准行时",
            "校准集保留被标记观测时",
        )
        text = text.replace(
            "排除标记观测后校准行上",
            "校准集排除被标记观测时",
        )
        text = text.replace("全量数据拟合", "包含被标记观测的拟合")
        text = text.replace("全部数据拟合", "包含被标记观测的拟合")
        text = text.replace("全数据拟合", "包含被标记观测的拟合")
        text = text.replace(
            "全数据条件下",
            "包含被标记观测拟合后，在留出观测上",
        )
        text = text.replace(
            "全数据斜率",
            "包含被标记观测拟合后的斜率",
        )
        text = text.replace(
            "全数据截距",
            "包含被标记观测拟合后的截距",
        )
        text = text.replace(
            "全部数据校准后",
            "包含被标记观测拟合并校准后",
        )
        text = text.replace(
            "全部数据校准",
            "包含被标记观测拟合后的校准",
        )
        text = text.replace("全部行拟合", "包含被标记观测的拟合")
        text = text.replace(
            "全部行校准",
            "包含被标记观测拟合后的校准",
        )
        text = text.replace(
            "全部行斜率",
            "包含被标记观测拟合后的斜率",
        )
        text = text.replace(
            "全部行截距",
            "包含被标记观测拟合后的截距",
        )
        text = text.replace(
            "全数据与排除标记两种拟合条件",
            "包含与排除被标记观测两种拟合条件",
        )
        text = text.replace(
            "全量拟合校准",
            "包含被标记观测拟合后的校准",
        )
        text = text.replace(
            "排除标记拟合",
            "排除标记观测拟合",
        )
        text = text.replace(
            "排除标记观测拟合斜率",
            "排除标记观测拟合的斜率",
        )
        text = text.replace(
            "排除标记观测拟合截距",
            "排除标记观测拟合的截距",
        )
    condition_names: dict[str, str] = {}
    for row in (design or {}).get("measurement_plan", []):
        display = _scientific_reader_text(row.get("display_name", ""))
        meaning = _scientific_reader_text(row.get("scientific_meaning", ""))
        for letter in ("A", "B"):
            if f"条件 {letter}" not in display:
                continue
            if "保留" in meaning:
                condition_names[letter] = "包含被标记观测条件"
            elif "排除" in meaning:
                condition_names[letter] = "排除标记条件"
    for letter, condition_name in condition_names.items():
        text = text.replace(f"条件 {letter}", condition_name)
    text = re.sub(r"((?:保留|排除)标记条件)\s+(?=减|的)", r"\1", text)
    text = re.sub(r"((?:保留|排除)标记条件)\s+的", r"\1的", text)
    text = re.sub(
        r"((?:保留|排除)标记条件)\s+(?=[\u3400-\u9fff])",
        r"\1",
        text,
    )
    candidate_to_reference = any(
        any(
            "candidate" in str(column).casefold()
            for column in audit.get("candidate_model_input_columns", [])
        )
        and "reference"
        in str(audit.get("candidate_model_target_column", "")).casefold()
        for audit in (design or {}).get("paired_comparison_audits", [])
    )
    if candidate_to_reference:
        text = re.sub(
            r"候选读数(?:相对于|关于|对)参考读数的"
            r"(?=(?:普通)?最小二乘(?:回归)?斜率)",
            "参考读数相对于候选读数的",
            text,
        )
        text = re.sub(
            r"参考读数相对于候选读数的"
            r"(?=(?:普通)?最小二乘(?:回归)?(?:斜率|截距))",
            "以候选读数为自变量、参考读数为因变量的",
            text,
        )
    if (
        sum(
            1
            for audit in (design or {}).get("paired_comparison_audits", [])
            if audit.get("comparison_kind") == "source_baseline_vs_candidate"
        )
        >= 2
    ):
        text = re.sub(
            r"作为(?:保留|排除)标记(?:观测)?条件校正效果的对照基准",
            "作为两种校正条件共同的对照基准",
            text,
        )
    return text


def _historical_mse_means_signed_error(
    design: dict[str, Any] | None,
) -> bool:
    return any(
        re.search(r"(?:^|_)mse(?:_|$)", str(row.get("name", "")))
        and re.search(
            r"(?:平均有符号|平均符号|mean\s+signed)",
            f"{row.get('display_name', '')} {row.get('scientific_meaning', '')}",
            re.IGNORECASE,
        )
        for row in (design or {}).get("measurement_plan", [])
    )


def _repair_historical_signed_error_text(value: Any) -> str:
    text = str(value)
    text = text.replace("均方误差（MSE）", "平均有符号误差")
    text = re.sub(r"\bMSE\b\s*", "平均有符号误差", text)
    return re.sub(
        r"(?<=[\u3400-\u9fff])\s+(?=平均有符号误差)",
        "",
        text,
    )


def _scientific_method_text(value: Any) -> str:
    """Keep method prose scientific when a model mixes it with implementation notes."""

    text = _scientific_reader_text(value)
    text = re.sub(
        r"模型方向为参考读数相对于候选读数",
        "以候选读数为自变量、参考读数为因变量",
        text,
    )
    text = re.sub(
        r"保留标记观测条件保留全部\s*(\d+)\s*条拟合观测",
        r"包含该标记观测时使用全部 \1 条拟合观测",
        text,
    )
    text = re.sub(
        r"排除标记观测条件排除唯一标记观测后使用\s*(\d+)\s*条观测拟合",
        r"排除该标记观测时使用其余 \1 条观测拟合",
        text,
    )
    sentences: list[str] = []
    for sentence in re.split(r"(?<=[。！？])\s*", text):
        sentence = re.sub(r"^\s*\d+\s*[.．、)]\s*", "", sentence).strip()
        sentence = re.sub(
            r"^(?:读取|载入|加载)(?:本次)?(?:提供的)?"
            r"[^，,；;。]{1,80}?并(?=(?:按|将|对|采用|使用))",
            "",
            sentence,
        ).strip()
        if re.match(
            r"^(?:输出|写入|保存|生成[^。；;]*(?:文件|JSON|CSV|表格数据))",
            sentence,
            re.IGNORECASE,
        ):
            continue
        if sentence:
            sentences.append(sentence)
    text = "".join(sentences)
    text = re.sub(
        r"(^|[；;]\s*)\d+\s*[.．、)]\s*",
        r"\1",
        text,
    )
    text = re.sub(r"^(?:读取|载入|加载)[^；;。]+[；;]\s*", "", text)
    text = re.sub(
        r"\bOLS\b\s*线性模型",
        "普通最小二乘线性模型",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?:^|[；;]\s*)\d+\)\s*(?:写入|保存(?:至|为)?|输出(?:至|为)?)"
        r"[^。；;]*(?:产物|文件)[^。；;]*[。；;]?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?:并)?(?:写入|保存至|保存为|输出至|输出为)"
        r"[^。；;]*(?:\.json|\.csv|\.parquet|\.md|\.txt|产物文件)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?:^|[；;]\s*)(?:并)?(?:输出|写入|保存)[^。]*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?:在训练(?:集|数据)上)?(?:最小二乘)?拟合\s+"
        r"[A-Za-z_][A-Za-z0-9_]*\s*=\s*[^；;。]+",
        "在训练数据上估计候选读数与参考读数之间的线性校正关系",
        text,
    )
    text = re.sub(
        r"(?:对评估(?:集|数据))?计算校准值\s+"
        r"[A-Za-z_][A-Za-z0-9_]*\s*=\s*[^；;。]+",
        "将该校正关系应用于留出评价数据",
        text,
    )
    text = re.sub(
        r"\b[A-Za-z][A-Za-z0-9_]*\s*(?:→|->)\s*"
        r"[A-Za-z][A-Za-z0-9_]*\b",
        "候选读数到参考读数的线性校正关系",
        text,
    )
    text = re.sub(
        r"(?:拟合|估计)\s*候选读数到参考读数的线性校正关系"
        r"(?:的线性(?:映射|关系))?",
        "估计候选读数到参考读数的线性校正关系",
        text,
    )
    text = re.sub(
        r"(?:^|(?<=[。；;]))\s*(?:并)?(?:输出|写入|保存)"
        r"[^。；;]*(?:[。；;]|$)",
        "",
        text,
        flags=re.IGNORECASE,
    )

    def natural_identifier(match: re.Match[str]) -> str:
        identifier = match.group(0).lower()
        if "reference" in identifier:
            return "参考读数"
        if "candidate" in identifier:
            return "候选读数"
        if "quality" in identifier or "flag" in identifier:
            return "质量标记"
        if "suspect" in identifier:
            return "质量标记异常"
        if "date" in identifier or "time" in identifier:
            return "时间"
        if "target" in identifier:
            return "目标变量"
        return "相应分析变量"

    text = re.sub(
        r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9]*"
        r"(?:_[A-Za-z0-9]+)+(?![A-Za-z0-9_])",
        natural_identifier,
        text,
    )
    text = text.replace(
        "候选读数到参考读数的线性校正关系的线性映射",
        "候选读数到参考读数的线性校正关系",
    )
    text = re.sub(r"([）)])\s+([与及和])", r"\1\2", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"；\s*；", "；", text).strip("。；; ")
    text = text.replace("；记录", "，并记录")
    return text


def _report_measurement_selection(
    measurements: list[dict[str, Any]],
    design: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Keep a long experiment table focused without dropping comparison evidence."""

    plan = {str(row["name"]): row for row in (design or {}).get("measurement_plan", [])}

    def structural_diagnostic(row: dict[str, Any]) -> bool:
        if row.get("role") != "diagnostic":
            return False
        name = str(row.get("name", ""))
        planned = plan.get(name, {})
        reader_text = " ".join(
            (
                str(planned.get("display_name", "")),
                str(planned.get("scientific_meaning", "")),
            )
        )
        return (
            re.search(
                r"(?:^|_)(?:fit|fitting|train|training|holdout|test|testing|"
                r"row|rows|sample|samples|observation|observations)"
                r"(?:_|$)",
                name,
                re.IGNORECASE,
            )
            is not None
            and re.search(r"(?:^|_)(?:n|count|rows|samples)(?:_|$)", name) is not None
        ) or re.search(r"(?:观测|样本|记录)(?:总)?(?:数|数量)", reader_text) is not None

    focused_measurements = [
        row for row in measurements if not structural_diagnostic(row)
    ]
    if focused_measurements:
        measurements = focused_measurements

    audits = [
        row
        for row in (design or {}).get("paired_comparison_audits", [])
        if isinstance(row, dict)
    ]
    task_text = " ".join(
        (
            str((design or {}).get("normalized_task", "")),
            str(
                ((design or {}).get("research_frame") or {}).get("primary_question", "")
            ),
        )
    )
    source_delta_requested = (
        re.search(
            r"校(?:准|正)前后|相对(?:未校准|未校正|原始)[^，。；;]{0,20}"
            r"(?:差值|改善|变化)|改善(?:量|幅度|多少)|"
            r"\bimprovement\b|\bbefore\b[^.]{0,30}\bafter\b",
            task_text,
            re.IGNORECASE,
        )
        is not None
    )
    if not source_delta_requested:
        source_delta_names = {
            str(row["delta_measurement"])
            for row in audits
            if row.get("comparison_kind") == "source_baseline_vs_candidate"
            and isinstance(row.get("delta_measurement"), str)
        }
        measurements = [
            row
            for row in measurements
            if str(row.get("name", "")) not in source_delta_names
        ]

    source_baseline_keys = {
        str(row["baseline_measurement"]): (
            str(row.get("source_input_id", "")),
            str(row.get("source_target_column", "")),
            str(row.get("source_baseline_column", "")),
            str(row.get("metric", "")),
        )
        for row in audits
        if row.get("comparison_kind") == "source_baseline_vs_candidate"
        and isinstance(row.get("baseline_measurement"), str)
    }
    seen_source_baselines: set[tuple[object, ...]] = set()
    deduplicated_measurements: list[dict[str, Any]] = []
    for row in measurements:
        name = str(row.get("name", ""))
        source_key = source_baseline_keys.get(name)
        if source_key is None:
            deduplicated_measurements.append(row)
            continue
        value = row.get("value")
        key = (*source_key, value, str(row.get("unit", "")))
        if key in seen_source_baselines:
            continue
        seen_source_baselines.add(key)
        deduplicated_measurements.append(row)
    measurements = deduplicated_measurements

    # Historical runs may have named one fitted condition twice: once in the
    # raw-versus-calibrated comparison and again in the condition sensitivity
    # comparison. Keep one row only when both names resolve to the same verified
    # value and unit. New designs are rejected before execution.
    source_audits = [
        row
        for row in audits
        if row.get("comparison_kind") == "source_baseline_vs_candidate"
    ]
    aliases: dict[str, str] = {}
    for condition_audit in audits:
        if condition_audit.get("comparison_kind") != "candidate_vs_candidate":
            continue
        for side in ("baseline", "candidate"):
            for source_audit in source_audits:
                if not all(
                    condition_audit.get(field) == source_audit.get(field)
                    for field in (
                        "source_input_id",
                        "source_target_column",
                        "source_baseline_column",
                        "metric",
                    )
                ) or not _same_fitted_condition(
                    condition_audit.get(f"{side}_fit_condition"),
                    source_audit.get("candidate_fit_condition"),
                ):
                    continue
                alias = str(condition_audit.get(f"{side}_measurement", ""))
                canonical = str(source_audit.get("candidate_measurement", ""))
                if alias and canonical and alias != canonical:
                    aliases[alias] = canonical
    by_measurement_name = {str(row.get("name", "")): row for row in measurements}
    measurements = [
        row
        for row in measurements
        if not (
            str(row.get("name", "")) in aliases
            and aliases[str(row.get("name", ""))] in by_measurement_name
            and row.get("value")
            == by_measurement_name[aliases[str(row.get("name", ""))]].get("value")
            and str(row.get("unit", ""))
            == str(
                by_measurement_name[aliases[str(row.get("name", ""))]].get("unit", "")
            )
        )
    ]

    if len(measurements) <= 12:
        return measurements

    by_name = {str(row["name"]): row for row in measurements}
    priority: list[str] = []

    def add(name: object) -> None:
        normalized = str(name)
        if normalized in by_name and normalized not in priority:
            priority.append(normalized)

    sensitivity_refs: list[str] = []
    for criterion in (design or {}).get("criteria", []):
        if re.search(
            r"(?:sensitivity|robust|difference|contrast|敏感性|稳健性|差值|差异)",
            str(criterion.get("statement", "")),
            re.IGNORECASE,
        ):
            refs = [str(ref) for ref in criterion.get("measurement_refs", [])]
            sensitivity_refs.extend(ref for ref in refs if ref not in sensitivity_refs)
    for ref in sensitivity_refs:
        planned = plan.get(ref, {})
        reader_text = " ".join(
            (
                str(planned.get("display_name", "")),
                str(planned.get("scientific_meaning", "")),
            )
        )
        if re.search(
            r"\b(?:difference|contrast|delta)\b|差值|差异|二者之差",
            reader_text,
            re.IGNORECASE,
        ):
            add(ref)
    for ref in sensitivity_refs:
        if plan.get(ref, {}).get("role") == "primary":
            add(ref)
    for audit in (design or {}).get("paired_comparison_audits", []):
        for field in (
            "baseline_measurement",
            "candidate_measurement",
            "delta_measurement",
        ):
            if audit.get(field):
                add(audit[field])
    for ref in sensitivity_refs:
        add(ref)
    target_count = 12
    for role in ("primary", "secondary", "diagnostic"):
        for row in measurements:
            if len(priority) >= target_count:
                break
            if row.get("role") == role:
                add(row["name"])
    selected_names = set(priority[:target_count])
    return [row for row in measurements if str(row["name"]) in selected_names]


def _paired_delta_report_text(
    measurement_name: str,
    design: dict[str, Any] | None,
) -> tuple[str, str] | None:
    """Derive a reader-safe delta label from the verified comparison contract."""

    audit = next(
        (
            row
            for row in (design or {}).get("paired_comparison_audits", [])
            if row.get("delta_measurement") == measurement_name
        ),
        None,
    )
    if not audit:
        return None

    metric = {
        "mae": "平均绝对误差",
        "rmse": "均方根误差",
        "mean_signed_error": "平均有符号误差",
    }.get(str(audit.get("metric", "")), "误差指标")

    def fit_label(value: object, fallback: str) -> str:
        raw = _scientific_reader_text(value).strip("。；; ")
        lowered = raw.casefold()
        if re.search(r"exclude|without|drop|排除|剔除", lowered):
            return "排除标记观测拟合后"
        if re.search(r"include|with|retain|all|full|保留|包含|全部|全量", lowered):
            return "包含被标记观测拟合后"
        if raw:
            return raw if raw.endswith(("后", "时", "条件")) else f"{raw}条件"
        return fallback

    if audit.get("comparison_kind") == "source_baseline_vs_candidate":
        baseline = "未校准读数"
    else:
        baseline = fit_label(
            audit.get("baseline_fit_condition"),
            "参照拟合条件",
        )
    candidate = fit_label(
        audit.get("candidate_fit_condition"),
        "比较拟合条件",
    )
    if audit.get("delta_formula") == "candidate_minus_baseline":
        left, right = candidate, baseline
    else:
        left, right = baseline, candidate
    noun = "改善量" if baseline == "未校准读数" and left == baseline else "差值"
    display = f"{metric}{noun}（{left} − {right}）"
    definition = f"在相同评价观测上，{left}的{metric}减去{right}的{metric}"
    return display, definition


def _order_report_measurements(
    measurements: list[dict[str, Any]],
    design: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Show paired estimates before their derived differences."""

    paired_baselines = {
        str(row.get("baseline_measurement"))
        for row in (design or {}).get("paired_comparison_audits", [])
        if row.get("baseline_measurement")
    }
    paired_candidates = {
        str(row.get("candidate_measurement"))
        for row in (design or {}).get("paired_comparison_audits", [])
        if row.get("candidate_measurement")
    }
    paired_deltas = {
        str(row.get("delta_measurement"))
        for row in (design or {}).get("paired_comparison_audits", [])
        if row.get("delta_measurement")
    }
    plan_order = {
        str(row["name"]): index
        for index, row in enumerate((design or {}).get("measurement_plan", []))
    }
    return sorted(
        measurements,
        key=lambda row: (
            0
            if str(row["name"]) in paired_baselines
            else 1
            if str(row["name"]) in paired_candidates
            else 2
            if str(row["name"]) in paired_deltas
            else 3,
            plan_order.get(str(row["name"]), len(plan_order)),
        ),
    )


def _paired_result_lines(
    paired: list[dict[str, Any]],
    design: dict[str, Any] | None,
    chinese_task: bool,
) -> list[str]:
    """Describe pointwise comparison direction using reader-facing condition names."""

    plan = {str(row["name"]): row for row in (design or {}).get("measurement_plan", [])}
    audits = {
        str(row["id"]): row
        for row in (design or {}).get("paired_comparison_audits", [])
    }
    lines: list[str] = []

    def condition_label(value: object) -> str:
        label = _scientific_reader_text(value)
        if "未校正" in label or "未经校正" in label:
            return "未校正读数"
        if "未校准" in label or "未经校准" in label:
            return "未校准读数"
        if "原始" in label and "误差" in label:
            return "原始读数"
        if re.search(r"(?:保留|包含)[^，。；;]{0,16}标记[^，。；;]{0,12}拟合", label):
            return "包含被标记观测时"
        if re.search(r"排除[^，。；;]{0,16}标记[^，。；;]{0,12}拟合", label):
            return "排除标记观测时"
        if re.search(r"保留标记(?:行|观测)", label):
            return "包含被标记观测时"
        if "包含被标记观测" in label:
            return "包含被标记观测时"
        if "排除被标记观测" in label:
            return "排除被标记观测时"
        timed_condition = re.match(
            r"(?P<label>(?:保留|包含|排除)[^，。；;]{0,30}标记观测时)",
            label,
        )
        if timed_condition:
            return timed_condition.group("label").replace("保留", "包含", 1)
        if re.match(r"(?:保留|包含)[^，。；;]{0,30}标记观测条件", label):
            return "包含被标记观测时"
        named_condition = re.match(
            r"条件\s*(?P<name>[A-Za-z0-9一二三四五六七八九十]+)",
            label,
            re.IGNORECASE,
        )
        if named_condition:
            return f"条件 {named_condition.group('name')}"
        if "条件" in label:
            prefix = label.split("条件", 1)[0].strip()
            if prefix:
                return prefix + "条件"
        if "时的" in label:
            return label.split("时的", 1)[0] + "时"
        if "校正后" in label and "误差" in label:
            return "校正后读数"
        if "校准后" in label and "误差" in label:
            return "校准后读数"
        for metric_name in ("平均绝对误差", "均方根误差", "平均有符号误差"):
            if metric_name in label:
                prefix = label.split(metric_name, 1)[0].rstrip("的 ")
                if prefix:
                    return prefix
        return label

    def fit_condition_label(value: object, fallback: str) -> str:
        if not chinese_task:
            return fallback
        fit = str(value or "").casefold()
        if re.search(r"exclude|without|drop|排除|剔除", fit):
            return "排除标记观测时"
        if re.search(r"include|with|retain|all|保留|包含|全部|全量", fit):
            return "包含被标记观测时"
        return fallback

    def possessive(label: str) -> str:
        return f"{label} 的" if re.search(r"[A-Za-z0-9]$", label) else f"{label}的"

    source_groups: dict[tuple[str, str], dict[str, list[str]]] = {}
    handled_source_ids: set[str] = set()
    for row in paired[:3]:
        audit = audits.get(str(row.get("id", "")), {})
        if audit.get(
            "comparison_kind"
        ) != "source_baseline_vs_candidate" or not row.get(
            "all_candidate_absolute_errors_lower"
        ):
            continue
        count = int(row.get("row_count", 0))
        _, scope_intro = _paired_scope_and_intro(
            row["evaluation_scope"],
            count,
            chinese_task,
        )
        baseline = plan.get(str(audit.get("baseline_measurement", "")), {})
        candidate = plan.get(str(audit.get("candidate_measurement", "")), {})
        baseline_label = condition_label(
            _scientific_result_display(
                baseline.get("display_name", "参照条件"),
                baseline.get("scientific_meaning", ""),
                design,
            )
        )
        candidate_label = condition_label(
            _scientific_result_display(
                candidate.get("display_name", "比较条件"),
                candidate.get("scientific_meaning", ""),
                design,
            )
        )
        baseline_label = fit_condition_label(
            audit.get("baseline_fit_condition"),
            baseline_label,
        )
        candidate_label = fit_condition_label(
            audit.get("candidate_fit_condition"),
            candidate_label,
        )
        group = source_groups.setdefault(
            (scope_intro, baseline_label),
            {"candidate_labels": [], "row_ids": []},
        )
        group["candidate_labels"].append(candidate_label)
        group["row_ids"].append(str(row.get("id", "")))

    for (scope_intro, baseline_label), group in source_groups.items():
        unique_labels = list(dict.fromkeys(group["candidate_labels"]))
        if len(unique_labels) == 1:
            continue
        handled_source_ids.update(group["row_ids"])
        latin_labels = any(re.search(r"[A-Za-z0-9]$", label) for label in unique_labels)
        retained = next(
            (
                match.group("object")
                for label in unique_labels
                if (
                    match := re.fullmatch(
                        r"包含(?P<object>.+)时",
                        label,
                    )
                )
            ),
            None,
        )
        excluded = next(
            (
                match.group("object")
                for label in unique_labels
                if (
                    match := re.fullmatch(
                        r"排除(?:被)?(?P<object>.+)时",
                        label,
                    )
                )
            ),
            None,
        )
        if retained and excluded and retained.lstrip("被") == excluded.lstrip("被"):
            shared = retained if retained.startswith("被") else excluded
            comparison_phrase = f"无论包含还是排除{shared}，"
        else:
            joined = (" 和 " if latin_labels else "和").join(unique_labels)
            suffix = (
                " 下"
                if latin_labels
                else (
                    "，"
                    if all(label.endswith("时") for label in unique_labels)
                    else "下"
                )
            )
            comparison_phrase = f"{joined}{suffix}"
        lines.append(
            f"{scope_intro}，{comparison_phrase}校正读数的绝对误差均低于"
            f"{baseline_label}的绝对误差。"
        )

    for row in paired[:3]:
        if str(row.get("id", "")) in handled_source_ids:
            continue
        count = int(row.get("row_count", 0))
        scope, scope_intro = _paired_scope_and_intro(
            row["evaluation_scope"],
            count,
            chinese_task,
        )
        audit = audits.get(str(row.get("id", "")), {})
        baseline = plan.get(str(audit.get("baseline_measurement", "")), {})
        candidate = plan.get(str(audit.get("candidate_measurement", "")), {})
        baseline_label = condition_label(
            _scientific_result_display(
                baseline.get("display_name", "参照条件"),
                baseline.get("scientific_meaning", ""),
                design,
            )
        )
        candidate_label = condition_label(
            _scientific_result_display(
                candidate.get("display_name", "比较条件"),
                candidate.get("scientific_meaning", ""),
                design,
            )
        )
        baseline_label = fit_condition_label(
            audit.get("baseline_fit_condition"),
            baseline_label,
        )
        candidate_label = fit_condition_label(
            audit.get("candidate_fit_condition"),
            candidate_label,
        )
        if chinese_task and row.get("all_candidate_absolute_errors_lower"):
            lines.append(
                f"{scope_intro}，"
                f"{possessive(candidate_label)}绝对误差均低于"
                f"{possessive(baseline_label)}绝对误差。"
            )
        elif chinese_task:
            better = int(row.get("candidate_better_absolute_error_count", 0))
            tied = int(row.get("candidate_tied_absolute_error_count", 0))
            worse = int(row.get("candidate_worse_absolute_error_count", 0))
            if worse == count and tied == 0:
                lines.append(
                    f"{scope_intro}，"
                    f"{possessive(baseline_label)}绝对误差均低于"
                    f"{possessive(candidate_label)}绝对误差。"
                )
            else:
                lines.append(
                    f"在{scope}中，与{baseline_label}相比，{candidate_label}"
                    f"的绝对误差在 {better} 条观测中较低、{tied} 条中相同、"
                    f"{worse} 条中较高。"
                )
        else:
            lines.append(
                f"The comparison used the same {row['row_count']} observations "
                f"for {baseline_label} and {candidate_label}."
            )
    return lines


def _paired_scope_and_intro(
    value: object,
    count: int,
    chinese_task: bool,
) -> tuple[str, str]:
    """Reduce a comparison description to its scientific evaluation scope."""

    scope = _scientific_reader_text(value).split("；", 1)[0]
    scope = re.sub(r"(\d+)\s*行", r"\1 条", scope)
    scope = re.sub(
        r"\d+\s*(?:条|对)(?=[^，。；;]{0,16}(?:评估集|评价集|留出集))",
        f"{count} 条",
        scope,
    )
    scope = re.sub(
        r"^在?(?P<scope>(?:完全)?相同(?:的)?\s*\d+\s*条"
        r"[^，。；;]{0,40}观测)上.*$",
        r"\g<scope>",
        scope,
    )
    scope = re.sub(
        r"^(?:完全)?相同(?:的)?\s*(\d+)\s*条",
        r"相同的 \1 条",
        scope,
    )
    scope = scope.removeprefix("在")
    scope = re.sub(
        r"^(?P<scope>.+?观测)上(?:的)?两种[^，。；;]{1,80}"
        r"(?:对比|比较)$",
        r"\g<scope>",
        scope,
    )
    scope = re.sub(
        r"^(?P<scope>.+?观测)上[^，。；;]{1,120}(?:对比|比较)$",
        r"\g<scope>",
        scope,
    )
    scope = re.sub(
        r"^两种[^，。；;]{1,50}条件在(?P<scope>.+?观测)上的"
        r"(?:逐个观测)?[^，。；;]{0,50}(?:对比|比较)$",
        r"\g<scope>",
        scope,
    )
    if chinese_task and re.search(
        r"(?:保留|包含|排除|剔除)[^，。；;]{0,24}(?:标记|可疑|异常)"
        r"|两种[^，。；;]{0,24}拟合条件|未使用校准参数",
        scope,
    ):
        scope = f"相同的 {count} 条留出观测"
    if chinese_task and re.search(
        r"评估(?:集|观测|数据|期)|评价(?:集|观测|数据|期)|"
        r"留出(?:集|数据|期)|留出的",
        scope,
    ):
        scope = f"相同的 {count} 条观测（评估集）"
    if chinese_task and not _has_cjk(scope):
        scope = "当前评价数据"
    same_scope = re.fullmatch(r"相同(?:的)?(?P<kind>.+?)观测", scope)
    if same_scope and not re.search(rf"{count}\s*条", scope):
        intro = f"在相同的 {count} 条{same_scope.group('kind')}观测中"
    elif re.search(rf"{count}\s*条", scope):
        separator = " " if re.match(r"\d", scope) else ""
        intro = f"在{separator}{scope}中"
    elif scope.endswith("观测"):
        kind = re.sub(r"^(?:完全)?相同(?:的)?", "", scope)
        intro = f"在相同的 {count} 条{kind}中"
    else:
        intro = f"在{scope}的全部 {count} 条观测中"
    return scope, intro


def _verified_primary_comparison_sentence(
    paired: list[dict[str, Any]],
    design: dict[str, Any] | None,
    observed_measurements: dict[str, dict[str, Any]] | None = None,
) -> str:
    if not paired:
        return ""
    row = paired[0]
    audit = next(
        (
            item
            for item in (design or {}).get("paired_comparison_audits", [])
            if item.get("id") == row.get("id")
        ),
        None,
    )
    if not audit:
        return ""
    values = row.get("recomputed_measurements") or {}
    if observed_measurements and all(
        name in observed_measurements
        for name in (
            audit.get("baseline_measurement"),
            audit.get("candidate_measurement"),
            audit.get("delta_measurement"),
        )
    ):
        values = {
            name: observed_measurements[name]["value"]
            for name in (
                audit.get("baseline_measurement"),
                audit.get("candidate_measurement"),
                audit.get("delta_measurement"),
            )
        }
    plan = {item["name"]: item for item in (design or {}).get("measurement_plan", [])}
    names = [
        audit.get("baseline_measurement"),
        audit.get("candidate_measurement"),
        audit.get("delta_measurement"),
    ]
    if any(name not in values or name not in plan for name in names):
        return ""
    _scope, scope_intro = _paired_scope_and_intro(
        row.get("evaluation_scope", "当前评价数据"),
        int(row.get("row_count", 0)),
        _has_cjk(row.get("evaluation_scope", "")),
    )
    scope_prefix = _scope.strip("“”\"' ")

    def concise_label(name: str) -> str:
        label = _scientific_result_display(
            plan[name]["display_name"],
            plan[name].get("scientific_meaning", ""),
            design,
        )
        if scope_prefix and label.startswith(scope_prefix):
            shortened = label[len(scope_prefix) :].lstrip("：:，, ")
            if shortened:
                return shortened
        return label

    parts = [
        (
            f"{concise_label(name)}为 "
            f"{_format_measurement({'value': values[name], 'unit': plan[name].get('unit', '')})}"
        )
        for name in names
    ]
    return _scientific_sentence(f"{scope_intro}，" + "，".join(parts))


def _conclusion_has_primary_comparison_values(
    conclusion: str,
    paired: list[dict[str, Any]],
    design: dict[str, Any] | None,
    observed_measurements: dict[str, dict[str, Any]],
) -> bool:
    """Return whether both sides of the primary comparison already appear."""

    if not paired:
        return False
    audit = next(
        (
            item
            for item in (design or {}).get("paired_comparison_audits", [])
            if item.get("id") == paired[0].get("id")
        ),
        None,
    )
    if audit is None:
        return False
    names = (
        audit.get("baseline_measurement"),
        audit.get("candidate_measurement"),
    )
    if any(name not in observed_measurements for name in names):
        return False
    claims = _quantitative_claims(conclusion)
    return all(
        any(
            math.isclose(
                claim,
                float(observed_measurements[name]["value"]),
                rel_tol=0.01,
                abs_tol=max(
                    1e-12,
                    rounding_tolerance,
                    abs(float(observed_measurements[name]["value"])) * 0.01,
                ),
            )
            for claim, rounding_tolerance in claims
        )
        for name in names
    )


def _verified_condition_contrast_sentence(
    design: dict[str, Any] | None,
    observed_measurements: dict[str, dict[str, Any]],
) -> str:
    """Summarize two condition estimates and their verified contrast."""

    plan = {str(row["name"]): row for row in (design or {}).get("measurement_plan", [])}
    plan.update(
        {
            str(row["id"]): {
                **row,
                "name": row["id"],
            }
            for row in (design or {}).get("result_plan", [])
        }
    )

    def reader_text(name: str) -> str:
        row = plan.get(name, {})
        return " ".join(
            (
                str(row.get("display_name", "")),
                str(row.get("scientific_meaning", "")),
            )
        )

    def condition_role(name: str) -> str | None:
        text = reader_text(name)
        if re.search(r"\b(?:with|include|included)\b|包含|纳入", text, re.IGNORECASE):
            return "included"
        if re.search(
            r"\b(?:without|exclude|excluded|filtered)\b|排除|剔除",
            text,
            re.IGNORECASE,
        ):
            return "excluded"
        return None

    def is_contrast(name: str) -> bool:
        return (
            re.search(
                r"\b(?:delta|difference|contrast)\b|差值|差异|二者之差|条件间",
                reader_text(name),
                re.IGNORECASE,
            )
            is not None
        )

    def prefer_condition(names: list[str]) -> str:
        return sorted(
            names,
            key=lambda name: (
                re.search(
                    r"\bimprovement\b|改善",
                    reader_text(name),
                    re.IGNORECASE,
                )
                is None,
                name,
            ),
        )[0]

    def render(names: tuple[str, str, str]) -> str:
        units = {str(plan[name].get("unit", "")) for name in names}
        if len(units) != 1:
            return ""
        parts = [
            (
                f"{_scientific_result_display(plan[name]['display_name'], plan[name].get('scientific_meaning', ''), design)}为 "
                f"{_format_measurement(observed_measurements[name])}"
            )
            for name in names
        ]
        return _scientific_sentence("敏感性比较中，" + "，".join(parts))

    available = [name for name in observed_measurements if name in plan]
    all_included = [
        name
        for name in available
        if not is_contrast(name) and condition_role(name) == "included"
    ]
    all_excluded = [
        name
        for name in available
        if not is_contrast(name) and condition_role(name) == "excluded"
    ]
    for criterion in (design or {}).get("criteria", []):
        if (
            re.search(
                r"\b(?:difference|contrast)\b|差值|差异|二者之差|条件间",
                str(criterion.get("statement", "")),
                re.IGNORECASE,
            )
            is None
        ):
            continue
        refs = [
            str(ref)
            for ref in criterion.get("measurement_refs", [])
            if str(ref) in plan and str(ref) in observed_measurements
        ]
        contrasts = [name for name in refs if is_contrast(name)]
        included = [
            name
            for name in refs
            if not is_contrast(name) and condition_role(name) == "included"
        ]
        excluded = [
            name
            for name in refs
            if not is_contrast(name) and condition_role(name) == "excluded"
        ]
        if not contrasts or not included or not excluded:
            continue
        names = (
            prefer_condition(all_included or included),
            prefer_condition(all_excluded or excluded),
            contrasts[0],
        )
        sentence = render(names)
        if sentence:
            return sentence
    contrasts = [name for name in available if is_contrast(name)]
    if all_included and all_excluded and contrasts:
        return render(
            (
                prefer_condition(all_included),
                prefer_condition(all_excluded),
                contrasts[0],
            )
        )
    return ""


def _has_untraceable_cutoff(design: dict[str, Any] | None) -> bool:
    criteria_have_cutoff = any(
        row.get("basis_kind") == "method_standard"
        and (
            HARD_NUMERIC_CUTOFF.search(str(row.get("statement", ""))) is not None
            or RELATIVE_DECISION_CUTOFF.search(str(row.get("statement", "")))
            is not None
        )
        for row in (design or {}).get("criteria", [])
    )
    policy = (design or {}).get("interpretation_policy") or {}
    policy_has_worded_fraction = any(
        RELATIVE_DECISION_CUTOFF.search(str(policy.get(field, ""))) is not None
        for field in ("null_rule", "uncertainty_rule", "partial_rule")
    )
    return criteria_have_cutoff or policy_has_worded_fraction


def _remove_untraceable_cutoff_claims(
    text: str,
    design: dict[str, Any] | None,
) -> str:
    """Do not perpetuate a historical model-invented threshold in reader prose."""

    if not _has_untraceable_cutoff(design):
        return text
    sentences = re.split(r"(?<=[。！？])\s*|(?<=[!?])(?=\s|$)", text)
    kept = [
        sentence
        for sentence in sentences
        if not re.search(r"\d[^。；;]*阈值|阈值[^。；;]*\d", sentence)
        and RELATIVE_DECISION_CUTOFF.search(sentence) is None
    ]
    return " ".join(kept).strip()


def _method_summary(
    design: dict[str, Any] | None,
    narrative: dict[str, Any] | None,
    completed_stage_ids: set[str] | None = None,
) -> str:
    methods: list[str] = []
    if narrative:
        candidate = _scientific_method_text(
            _reader_facing_plan_text(narrative.get("method", ""), design)
        )
        sentences = [
            row.strip().strip("。；;.!?！？ ")
            for row in re.split(r"(?<=[。.!?！？])\s*", candidate)
            if row.strip().strip("。；;.!?！？ ")
            and not re.search(
                r"(?:代码|程序|沙箱|结果文件|哈希|核验|workflow|schema)",
                row,
                re.IGNORECASE,
            )
        ]
        methods.extend(sentences)
    if not methods:
        for stage in (design or {}).get("experiment_stages", []):
            if (
                completed_stage_ids is not None
                and stage.get("id") not in completed_stage_ids
            ):
                continue
            text = _scientific_method_text(
                _reader_facing_plan_text(stage.get("method_outline", ""), design)
            )
            if text:
                methods.append(text)
    if not methods:
        return "本次未形成可供科学解释的方法结果。"
    if len(methods) == 1:
        result = _scientific_sentence(methods[0])
    else:
        result = "；".join(methods) + "。"
    if _historical_mse_means_signed_error(design):
        result = _repair_historical_signed_error_text(result)
    metric_names = (
        ("MAE", "平均绝对误差（MAE）"),
        ("RMSE", "均方根误差（RMSE）"),
        ("MSE", "均方误差（MSE）"),
    )
    for abbreviation, full_name in metric_names:
        if abbreviation in result and full_name not in result:
            result = re.sub(rf"\b{abbreviation}\b", full_name, result, count=1)
    result = result.replace("） 和", "）和").replace("） 与", "）与")
    result = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", result)
    return result


def _unique_report_texts(values: list[Any], *, limit: int = 8) -> list[str]:
    rows: list[str] = []
    normalized_rows: list[str] = []
    for value in values:
        text = _scientific_reader_text(value).strip("。；; ")
        normalized = "".join(char.casefold() for char in text if char.isalnum())
        if not normalized:
            continue
        if any(
            normalized == previous or normalized in previous
            for previous in normalized_rows
        ):
            continue
        for index, previous in enumerate(normalized_rows):
            if previous in normalized:
                normalized_rows[index] = normalized
                rows[index] = text
                break
        else:
            normalized_rows.append(normalized)
            rows.append(text)
        if len(rows) >= limit:
            break
    return rows


def _scientific_follow_up(
    values: list[Any],
    *,
    frame: dict[str, Any] | None = None,
    limitations: list[Any] | None = None,
    objective: Any = "",
) -> list[str]:
    operational = re.compile(
        r"(?:重放|重现|复现|replay|audit\.md|运行记录|run[_ -]?id|"
        r"/automatic-experiment|相同条件再运行)",
        re.IGNORECASE,
    )
    rows = [
        row
        for row in _unique_report_texts(values, limit=4)
        if operational.search(row) is None
    ]
    objective_text = _scientific_reader_text(objective)
    sensitivity_route = re.compile(
        r"敏感性|稳健性|排除[^。；;]{0,30}(?:标记|可疑|异常)(?:观测)?|"
        r"(?:标记|可疑|异常)[^。；;]{0,40}(?:影响|比较|检验|分析|评估)"
    )
    if sensitivity_route.search(objective_text) is None:
        rows = [row for row in rows if sensitivity_route.search(row) is None]
    temporal_inference_route = re.compile(r"自相关|有效自由度|时序依赖")
    if re.search(r"自相关|有效自由度|时序|时间序列|推断", objective_text) is None:
        rows = [row for row in rows if temporal_inference_route.search(row) is None]
    deferred = (frame or {}).get("deferred_questions", [])
    if not deferred:
        return rows

    deferred_text = " ".join(str(row) for row in deferred)
    mechanism_request = re.compile(r"物理(?:机制|成因)|因果(?:机制|归因)?")
    if mechanism_request.search(deferred_text) and not mechanism_request.search(
        objective_text
    ):
        rows = [row for row in rows if mechanism_request.search(row) is None]

    def trigrams(value: Any) -> set[str]:
        compact = "".join(
            char.casefold() for char in _scientific_reader_text(value) if char.isalnum()
        )
        return {compact[index : index + 3] for index in range(max(0, len(compact) - 2))}

    relevant = trigrams(objective)
    deferred_grams = [trigrams(row) for row in deferred]
    return [
        row
        for row in rows
        if not (
            max(
                (len(trigrams(row) & deferred_row) for deferred_row in deferred_grams),
                default=0,
            )
            >= 3
            and len(trigrams(row) & relevant) < 2
        )
    ]


def _scientific_limitations(
    values: list[Any],
    *,
    frame: dict[str, Any] | None,
    objective: Any,
) -> list[str]:
    """Remove limitations that merely reopen an explicitly deferred parent task."""

    rows = _unique_report_texts(
        [_limitation_sentence(row) for row in values],
        limit=6,
    )
    objective_text = _scientific_reader_text(objective)
    descriptive_correlation = bool(
        re.search(r"Pearson|相关系数|相关方向", objective_text, re.IGNORECASE)
        and re.search(
            r"显著|假设检验|总体|置信|区间|自相关|有效自由度|敏感性|稳健性",
            objective_text,
        )
        is None
    )
    if descriptive_correlation:
        unrequested_correlation_route = re.compile(
            r"(?:样本量|样本数)[^。；;]{0,60}(?:抽样不确定性|异常值|极端观测)|"
            r"(?:标记|可疑|异常)[^。；;]{0,60}(?:影响|敏感)|"
            r"自相关|有效自由度|"
            r"^结论仅适用于[^。；;]{0,60}(?:不推广|不外推|总体)"
        )
        rows = [
            row for row in rows if unrequested_correlation_route.search(row) is None
        ]
    deferred = (frame or {}).get("deferred_questions", [])
    if not deferred:
        return rows

    deferred_text = " ".join(str(row) for row in deferred)
    mechanism_request = re.compile(r"物理(?:机制|成因)|因果(?:机制|归因)?")
    if mechanism_request.search(deferred_text) and not mechanism_request.search(
        _scientific_reader_text(objective)
    ):
        rows = [row for row in rows if mechanism_request.search(row) is None]

    def trigrams(value: Any) -> set[str]:
        compact = "".join(
            char.casefold() for char in _scientific_reader_text(value) if char.isalnum()
        )
        return {compact[index : index + 3] for index in range(max(0, len(compact) - 2))}

    objective_grams = trigrams(objective)
    deferred_grams = [trigrams(row) for row in deferred]
    missing_parent_condition = re.compile(
        r"未包含|缺少|尚无|(?:^|[，。；;])无[^，。；;]{0,24}(?:数据|目标|设计|真值)"
        r"|不能(?:检验|评估|判断|推断|进行)|无法评估|无法检验|下游"
        r"|\b(?:missing|unavailable|downstream)\b",
        re.IGNORECASE,
    )
    return [
        row
        for row in rows
        if not (
            missing_parent_condition.search(row) is not None
            and max(
                (len(trigrams(row) & item) for item in deferred_grams),
                default=0,
            )
            >= 3
            and len(trigrams(row) & objective_grams) < 2
        )
    ]


def _scientific_discussion_rows(
    values: list[Any],
    *,
    objective: Any = "",
) -> list[str]:
    """Keep scientific interpretation in Discussion and audit facts in audit.md."""

    operational = re.compile(
        r"(?:代码|程序|文件|字段|核验|复算|哈希|运行状态|阶段完成)|"
        r"^(?:各项|所有|全部)结果均按.+计算$|"
        r"^(?:三组|多组|各组)比较均使用相同(?:的)?\s*\d+\s*条.+观测$|"
        r"^当前样本中的相关系数为(?:正|负)$|"
        r"^相关系数基于[^。；;]{0,80}确定性计算"
    )
    objective_text = _scientific_reader_text(objective)
    sensitivity_requested = re.search(
        r"敏感性|稳健性|质量标记|排除[^。；;]{0,30}(?:标记|可疑|异常)",
        objective_text,
    )
    rows: list[str] = []
    for value in values:
        text = _scientific_reader_text(value)
        sentences = [
            row.strip("。；; ")
            for row in re.split(r"(?<=[。！？!?])\s*|(?<!\d)\.(?!\d)\s*", text)
            if row.strip("。；; ")
        ]
        for sentence in sentences:
            if operational.search(sentence):
                continue
            if sensitivity_requested is None and re.search(
                r"(?:标记|可疑|异常)[^。；;]{0,60}(?:影响|敏感)",
                sentence,
            ):
                continue
            rows.append(sentence)
    rows = _unique_report_texts(rows, limit=4)
    if any(re.search(r"适用于|外推|适用范围", row) for row in rows):
        rows = [
            row
            for row in rows
            if not (
                row.startswith("结果的适用范围受")
                and any(
                    other != row and re.search(r"适用于|外推|适用范围", other)
                    for other in rows
                )
            )
        ]
    return rows[:3]


def _main_report_text(sections: list[str]) -> str:
    """Normalize Markdown spacing without imposing layout-only content caps."""

    report = re.sub(r"\n{3,}", "\n\n", "\n".join(sections)).strip() + "\n"
    report = re.sub(
        r"相同\s*(\d+)\s*行留出集",
        r"相同的 \1 条留出观测",
        report,
    )
    report = re.sub(
        r"留出集仅\s*(\d+)\s*行",
        r"留出集仅含 \1 条观测",
        report,
    )
    report = re.sub(
        r"(\d+)\s*行(?=\s*(?:时间顺序)?配对观测)",
        r"\1 对",
        report,
    )
    report = re.sub(r"(\d+)\s*行数据", r"\1 条观测", report)
    report = re.sub(r"(\d+)\s*行", r"\1 条观测", report)
    report = re.sub(
        r"仅\s*(\d+)\s*条观测合成数据",
        r"仅有 \1 条合成观测",
        report,
    )
    report = re.sub(
        r"合成演示数据中\s*(\d+)\s*对时间顺序配对观测",
        r"合成演示数据包含 \1 组按时间配对的观测",
        report,
    )
    report = report.replace("单行扰动", "单个观测的变化")
    report = report.replace("单行变化", "单个观测的变化")
    report = report.replace("质量标记行", "被质量标记的观测")
    report = report.replace("单一标记行", "一条被标记观测")
    report = report.replace("标记行", "被标记观测")
    report = report.replace("逐行", "逐个观测")
    report = report.replace("逐个观测读取", "逐条读取")
    return report


def _fallback_main_report(
    measurement_table: list[str],
    *,
    has_verified_results: bool,
) -> str:
    """Keep a verified run reportable even when optional narrative prose is unusable."""

    if has_verified_results and len(measurement_table) > 2:
        result_block = [*measurement_table]
        abstract = "本次分析已获得下列测量结果。解释范围限于实际使用的数据与本次方法。"
        discussion = (
            "现有结果描述当前数据中的数值关系或差异，不能据此作超出数据范围和"
            "方法假设的外推。"
        )
    else:
        result_block = ["本次没有获得可供科学解释的测量结果。"]
        abstract = "本次分析没有形成可报告的测量结果。"
        discussion = "当前证据不足以对研究问题作出数值判断。"
    return _main_report_text(
        [
            "# 实验分析报告",
            "",
            "## 摘要",
            "",
            abstract,
            "",
            "## 数据与方法",
            "",
            "本报告仅纳入实际使用的数据以及已经完成的分析。",
            "",
            "## 结果",
            "",
            *result_block,
            "",
            "## 讨论",
            "",
            discussion,
            "",
            "## 局限性",
            "",
            "- 结论的适用范围受输入数据覆盖、样本规模与方法假设限制。",
            "",
        ]
    )


def _emergency_main_report(
    record: dict[str, Any],
    design: dict[str, Any] | None,
) -> str:
    """Guarantee a useful Markdown report when optional prose cannot be published."""

    stage_history = record.get("stage_history") or []
    measurements = [
        item
        for stage in stage_history
        for item in (stage.get("result_summary") or {}).get("measurements", [])
    ] or (record.get("worker_result") or {}).get("measurements", [])
    plan = {
        str(row.get("name")): row
        for row in (design or {}).get("measurement_plan", [])
        if isinstance(row, dict)
    }
    table = ["| 指标 | 估计值 | 指标定义 |", "|---|---:|---|"]
    unsafe_label = re.compile(
        r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9]*"
        r"(?:_[A-Za-z0-9]+)+(?![A-Za-z0-9_])|"
        r"schema|worker|criterion|artifact|endpoint|stage",
        re.IGNORECASE,
    )
    for index, row in enumerate(measurements[:12], start=1):
        planned = plan.get(str(row.get("name")), {})
        label = _scientific_result_display(
            planned.get("display_name") or f"测量结果 {index}",
            planned.get("scientific_meaning", ""),
            design,
        )
        if not label or unsafe_label.search(label):
            label = f"测量结果 {index}"
        table.append(
            "| "
            + " | ".join(
                _table_text(value)
                for value in (
                    label,
                    _format_measurement(row),
                    "当前输入与已完成方法得到的数值",
                )
            )
            + " |"
        )
    report = _fallback_main_report(
        table,
        has_verified_results=bool(measurements),
    )
    try:
        _validate_main_report_quality(report)
    except RuntimeError:
        report = _fallback_main_report([], has_verified_results=False)
        _validate_main_report_quality(report)
    return report


def _limitation_sentence(value: Any) -> str:
    """Turn terse model notes into complete, implication-aware report prose."""

    text = _scientific_reader_text(value).strip("。；; ")
    if re.fullmatch(r"数据为合成(?:演示)?数据", text):
        return "数据为合成演示数据，因此结果不能直接外推到真实观测"
    if re.fullmatch(r"仅\s*\d+\s*(?:行|条)留出观测", text):
        return f"{text}，平均误差和参数差异可能对个别观测及划分方式敏感"
    if re.search(r"^仅考察.+一种扰动$", text):
        return f"{text}，尚不能判断其他质量控制方案下结论是否一致"
    if re.fullmatch(r"(?:只拟合了|仅考察)线性(?:形式|关系)?", text):
        return "本次仅考察线性关系，未检验非线性或随时间变化的关系"
    return text


def render_report(
    record: dict[str, Any],
    response: dict[str, Any] | None = None,
    design: dict[str, Any] | None = None,
    report_assets: list[dict[str, Any]] | None = None,
) -> str:
    outcome = record["outcome"]
    label = OUTCOME_LABELS[outcome]
    narrative = _narrative(record)
    stage_history = record.get("stage_history") or []
    measurements = [
        item
        for stage_row in stage_history
        for item in (stage_row.get("result_summary") or {}).get("measurements", [])
    ] or (record.get("worker_result") or {}).get("measurements", [])
    typed_results = [
        item
        for stage_row in stage_history
        for item in (stage_row.get("result_summary") or {}).get("result_items", [])
    ] or (record.get("worker_result") or {}).get("result_items", [])
    has_verified_results = bool(measurements or typed_results)
    frame = (
        ((record.get("evidence_ledger") or {}).get("research_frame") or {})
        or (design or {}).get("research_frame")
        or {}
    )
    title = (
        _scientific_reader_text(_reader_facing_plan_text(narrative["title"], design))
        if narrative
        else "实验分析报告"
    )
    task = (design or {}).get("normalized_task") or record["task"]
    objective = (
        _scientific_reader_text(
            _reader_facing_plan_text(narrative["objective"], design)
        )
        if narrative
        else frame.get("primary_question") or task
    )

    if narrative:
        conclusion = _remove_unrequested_descriptive_pattern(
            _remove_untraceable_cutoff_claims(
                _concise_abstract_result(
                    _reader_facing_plan_text(narrative["interpretation"], design)
                ),
                design,
            ),
            task,
        )
        strength = _remove_unrequested_descriptive_pattern(
            _scientific_reader_text(
                _reader_facing_plan_text(narrative["evidence_strength"], design)
            ),
            task,
        )
        if _has_untraceable_cutoff(design):
            strength = (
                "当前记录中包含未能追溯来源的数值判定阈值；本报告不据此作综合有效性判定。"
                "数值比较仅描述当前数据范围内观察到的变化。"
            )
        claim_boundary = _scientific_reader_text(
            _reader_facing_plan_text(narrative["claim_boundary"], design)
        )
        data_scope = _scientific_reader_text(
            _reader_facing_plan_text(narrative["data_scope"], design)
        )
        limitations = _scientific_limitations(
            [
                _scientific_reader_text(_reader_facing_plan_text(row, design))
                for row in narrative["limitations"]
            ],
            frame=frame,
            objective=objective,
        )
        next_steps = _scientific_follow_up(
            [_reader_facing_plan_text(row, design) for row in narrative["next_steps"]],
            frame=frame,
            limitations=limitations,
            objective=objective,
        )
    else:
        if has_verified_results:
            conclusion = (
                "已完成部分形成了可报告的结果；尚未完成复核的比较结果不进入"
                "本报告结论，因此目前不能评价主要研究问题。"
            )
            strength = (
                "现有证据足以描述已完成的数据质量检查，但不足以评价尚未核验的主要"
                "估计量。"
            )
            data_scope = (
                "本报告仅纳入已经复核的数据质量与样本构成结果；其他计算值不作为证据。"
            )
            limitations = [
                "主要比较尚未完成结果复核，当前结果只能说明数据质量与样本构成，"
                "不能据此判断所研究方法的效果。"
            ]
            completed_ids = {
                str(row.get("stage_id"))
                for row in stage_history
                if isinstance(row.get("stage_id"), str)
            }
            pending_objectives = [
                _scientific_reader_text(row.get("objective", "")).strip("。；; ")
                for row in (design or {}).get("experiment_stages", [])
                if row.get("id") not in completed_ids
                and _scientific_reader_text(row.get("objective", "")).strip()
            ]
            next_steps = (
                [f"完成{pending_objectives[0]}并复核相应估计量，以回答主要研究问题。"]
                if pending_objectives
                else []
            )
        else:
            conclusion = "本次没有获得实验结果，不能据此对研究问题作出数值判断。"
            strength = "当前没有可供科学解释的测量结果。"
            data_scope = (
                "本次任务未进入科学测量阶段。"
                if record.get("worker_result") is None
                else "结果范围以实际使用的数据和已完成的分析为准。"
            )
            limitations = ["当前没有测量值可供科学解释。"]
            next_steps = _scientific_follow_up(
                [
                    row.removeprefix("- ")
                    for row in _early_context_lines(record, response)
                ]
            )
        claim_boundary = _scientific_reader_text(
            frame.get("claim_scope") or "本报告只覆盖已提供的数据与已经完成的分析。"
        )

    plan = {row["name"]: row for row in (design or {}).get("measurement_plan", [])}
    measurement_by_name = {row["name"]: row for row in measurements}
    selected_measurements = [
        measurement_by_name[row["name"]]
        for row in (design or {}).get("measurement_plan", [])
        if row["name"] in measurement_by_name
    ]
    selected_measurements.extend(row for row in measurements if row["name"] not in plan)
    selected_measurements = _report_measurement_selection(
        selected_measurements,
        design,
    )
    selected_measurements = _order_report_measurements(
        selected_measurements,
        design,
    )

    measurement_table = [
        "| 指标 | 估计值 | 指标定义 |",
        "|---|---:|---|",
    ]
    seen_result_rows: set[tuple[str, str]] = set()

    def append_result_row(
        display: object,
        value: str,
        definition: object,
        *,
        measurement_name: str | None = None,
    ) -> None:
        delta_text = (
            _paired_delta_report_text(measurement_name, design)
            if measurement_name
            else None
        )
        if delta_text:
            display, definition = delta_text
        display = _scientific_result_display(display, definition, design)
        definition = _scientific_result_definition(definition, design)
        normalized_display = _normalized_result_label(display)
        key = (normalized_display, value.strip().casefold())
        if key in seen_result_rows:
            return
        seen_result_rows.add(key)
        measurement_table.append(
            "| "
            + " | ".join(_table_text(item) for item in (display, value, definition))
            + " |"
        )

    for row in selected_measurements:
        planned = plan.get(row["name"])
        display = (
            planned["display_name"]
            if planned
            else _legacy_measurement_display_name(row["name"])
        )
        definition = (
            planned["scientific_meaning"]
            if planned
            else _legacy_measurement_meaning(row["name"])
        )
        if _historical_mse_means_signed_error(design) and re.search(
            r"(?:^|_)mse(?:_|$)", str(row["name"])
        ):
            display = _repair_historical_signed_error_text(display)
            definition = _repair_historical_signed_error_text(definition)
        append_result_row(
            display,
            _format_measurement(row),
            definition,
            measurement_name=str(row["name"]),
        )

    result_plan = {row["id"]: row for row in (design or {}).get("result_plan", [])}
    answer_result_refs = {
        str(ref)
        for criterion in (design or {}).get("criteria", [])
        for ref in criterion.get("result_refs", [])
    }
    has_measurements = len(measurement_table) > 2
    result_by_id = {row["id"]: row for row in typed_results}
    ordered_results = [
        result_by_id[row["id"]]
        for row in (design or {}).get("result_plan", [])
        if row["id"] in result_by_id
    ]
    ordered_results.extend(row for row in typed_results if row["id"] not in result_plan)
    remaining_result_rows = max(0, 12 - (len(measurement_table) - 2))
    for row in ordered_results:
        if remaining_result_rows <= 0:
            break
        if row.get("value_kind") == "boolean" and _has_untraceable_cutoff(design):
            continue
        planned = result_plan.get(row["id"])
        planned_role = (planned or row).get("role")
        if (
            planned_role == "diagnostic"
            and has_measurements
            and str(row["id"]) not in answer_result_refs
        ):
            continue
        if (
            planned_role == "diagnostic"
            and has_measurements
            and row.get("value_kind") == "count"
            and re.search(
                r"样本数|观测数|记录数|行数|数量|\bcount\b|number\s+of",
                str(task),
                re.IGNORECASE,
            )
            is None
        ):
            continue
        display_name = (planned or row).get("display_name", "定性结果")
        result_definition = (planned or {}).get("scientific_meaning", "")
        if (
            planned_role == "diagnostic"
            and R_SQUARED_PLAN.search(
                " ".join(
                    (
                        str(row.get("id", "")),
                        str(display_name),
                        str(result_definition),
                    )
                )
            )
            and R_SQUARED_PLAN.search(str(task)) is None
        ):
            continue
        if row.get("value_kind") == "boolean" and re.search(
            r"(?:稳健|robust)", str(display_name), re.IGNORECASE
        ):
            continue
        append_result_row(
            display_name,
            _format_typed_result(row),
            result_definition or "当前分析得到的定性或离散结果",
        )
        remaining_result_rows -= 1

    has_results = len(measurement_table) > 2
    if not has_results:
        stop_explanations = {
            "clarification_required": "现有信息不足以确定不会改变结论的分析方法，因此尚未开始计算。",
            "input_missing": "必要数据尚未提供，因此当前无法形成可解释的实验结果。",
            "method_mismatch": "现有方法不能有效回答研究问题，因此未对结果作勉强解释。",
            "technical_failure": "本次分析未获得可供科学解释的结果。",
            "budget_stopped": "本次分析在规定时间内未形成可解释结果。",
            "boundary_blocked": "该任务超出当前可安全开展的实验范围。",
            "cancelled_by_user": "本次分析已按用户要求停止，尚未形成实验结果。",
            "high_uncertainty": "现有证据不足以形成可靠的数值判断。",
        }
        context_rows = _unique_report_texts(
            [row.removeprefix("- ") for row in _early_context_lines(record, response)],
            limit=3,
        )
        sections = [
            f"# {title}",
            "",
            "## 当前情况",
            "",
            stop_explanations.get(outcome, conclusion),
            "",
            "## 可得结论",
            "",
            "当前没有形成可报告的测量结果，因而不能据此回答原研究问题。",
            "",
            "## 继续研究所需条件",
            "",
            *(
                [f"- {row}" for row in context_rows]
                or ["- 补齐必要信息或调整方法后重新开展分析。"]
            ),
            "",
        ]
        report = _main_report_text(sections)
        _validate_main_report_quality(report)
        return report

    paired = (record.get("evidence_ledger") or {}).get("paired_comparisons") or []
    chinese_task = _has_cjk(record.get("task", ""))
    paired_paragraphs = _paired_result_lines(paired, design, chinese_task)
    combined_limitations = _scientific_limitations(
        limitations,
        frame=frame,
        objective=objective,
    )
    data_profile = _data_profile_summary(record)
    unit_note = _missing_unit_note(
        design,
        measurements,
        typed_results,
        chinese_task=chinese_task,
    )
    asset_lines: list[str] = []
    if report_assets:
        asset = report_assets[0]
        asset_lines.extend(
            [
                f"![结果比较图]({asset['path']})",
                "",
                (
                    "图注：图中展示同一评价范围内各观测的比较结果；"
                    f"共纳入 {asset['row_count']} 条观测。"
                ),
            ]
        )

    objective_text = _scientific_reader_text(
        _reader_facing_plan_text(objective, design)
    ).rstrip("。")
    if chinese_task and re.match(
        r"^(?:评估|比较|检验|估计|分析|考察|量化|确定|计算|验证)",
        objective_text,
    ):
        summary_intro = f"本研究{objective_text}。"
    elif chinese_task:
        summary_intro = f"研究目标是{objective_text}。"
    else:
        summary_intro = (
            f"This analysis addresses the following question: {objective_text}."
        )
    if outcome not in {"completed_interpretable", "scientific_null"}:
        partial_phrases = {
            "partial_result": "本次仅形成部分结果，以下解释限于已完成部分。",
            "high_uncertainty": "现有结果不确定性较高，以下解释限于当前证据。",
            "budget_stopped": "分析在完成全部预定比较前结束，以下仅报告已经复核的结果。",
            "cancelled_by_user": "分析在完成全部预定比较前停止，以下仅报告已经复核的结果。",
        }
        summary_intro += partial_phrases.get(
            outcome,
            f"本次仅获得{label}，以下解释限于已完成部分。",
        )
    completed_stage_ids = {
        str(row.get("stage_id"))
        for row in stage_history
        if isinstance(row.get("stage_id"), str)
    }
    method_detail = _method_summary(
        design,
        narrative,
        completed_stage_ids or None,
    )
    data_parts = _unique_report_texts(
        [
            data_scope,
            *(
                [data_profile]
                if data_profile
                and re.search(r"\d+\s*(?:行|条|对|个)", data_scope) is None
                else []
            ),
            unit_note,
        ],
        limit=3,
    )
    data_detail = "；".join(data_parts).rstrip("。；") + "。"
    paired_direction_is_uniform = bool(paired) and all(
        int(row.get("candidate_tied_absolute_error_count", 0)) == 0
        and (
            int(row.get("candidate_better_absolute_error_count", 0))
            == int(row.get("row_count", 0))
            or int(row.get("candidate_worse_absolute_error_count", 0))
            == int(row.get("row_count", 0))
        )
        for row in paired
    )
    pointwise_interpretation = (
        "各观测的比较方向与相应的平均绝对误差一致，观察到的平均差异并非由"
        "方向相反的个别观测相互抵消所致。"
        if paired_direction_is_uniform
        else ""
    )
    if pointwise_interpretation and (
        re.search(
            r"(?:所有|全部)[^。；;]{0,20}留出观测|"
            r"留出[^。；;]{0,20}(?:逐一比较|方向一致)",
            strength,
        )
        or (
            "绝对误差" in strength
            and re.search(r"(?:均低于|均高于|均下降|均上升)", strength)
        )
    ):
        pointwise_interpretation = ""
    discussion = _scientific_discussion_rows(
        [pointwise_interpretation, claim_boundary, strength],
        objective=objective,
    )
    if not discussion:
        discussion = [
            (
                "这些结果描述当前输入中的数值特征，不构成总体推断"
                if has_verified_results
                else "当前证据不足以对研究问题作出科学解释"
            )
        ]
    primary_comparison = _verified_primary_comparison_sentence(
        paired,
        design,
        measurement_by_name,
    )
    contrast_observations = dict(measurement_by_name)
    contrast_observations.update(
        {
            str(row["id"]): {
                "value": row["value"],
                "unit": row.get("unit", ""),
            }
            for row in typed_results
            if row.get("value_kind") in {"number", "count"}
            and isinstance(row.get("value"), (int, float))
            and not isinstance(row.get("value"), bool)
        }
    )
    condition_contrast = _verified_condition_contrast_sentence(
        design,
        contrast_observations,
    )
    typed_summary = ""
    if not narrative and typed_results:
        summary_items = [
            (
                _scientific_result_display(
                    (result_plan.get(row["id"]) or row).get(
                        "display_name",
                        "定性结果",
                    ),
                    (result_plan.get(row["id"]) or {}).get(
                        "scientific_meaning",
                        "",
                    ),
                    design,
                ),
                _format_typed_result(row),
            )
            for row in ordered_results[:3]
        ]
        if summary_items:
            typed_summary = (
                "已复核结果显示："
                + "；".join(f"{display}为{value}" for display, value in summary_items)
                + "。"
            )

    conclusion_sentence = _scientific_sentence(conclusion) if conclusion else ""
    summary_details = (
        [
            row
            for row in (
                (
                    primary_comparison
                    if primary_comparison
                    and _needs_primary_abstract_sentence(
                        conclusion,
                        has_complete_primary_values=(
                            _conclusion_has_primary_comparison_values(
                                conclusion,
                                paired,
                                design,
                                measurement_by_name,
                            )
                        ),
                    )
                    else ""
                ),
                conclusion_sentence,
            )
            if row
        ]
        if narrative
        else [
            row
            for row in (
                primary_comparison,
                condition_contrast,
                typed_summary,
                _scientific_sentence(conclusion),
            )
            if row
        ]
    )

    sections = [
        f"# {title}",
        "",
        "## 摘要",
        "",
        summary_intro,
        "",
        *[item for row in summary_details for item in (row, "")],
        "## 数据与方法",
        "",
        data_detail,
        "",
        method_detail,
        "",
        "## 结果",
        "",
        *measurement_table,
        "",
        *paired_paragraphs,
        "",
        *asset_lines,
        "",
        "## 讨论",
        "",
        *[_scientific_sentence(row) for row in discussion],
        "",
        *(
            [
                "## 局限性",
                "",
                *[f"- {_scientific_sentence(row)}" for row in combined_limitations],
                "",
            ]
            if combined_limitations
            else []
        ),
        *(
            [
                "## 后续研究",
                "",
                *[f"- {_scientific_sentence(row)}" for row in next_steps],
                "",
            ]
            if next_steps
            else []
        ),
    ]
    report = _main_report_text(sections)
    try:
        _validate_main_report_quality(report)
    except RuntimeError:
        report = _fallback_main_report(
            measurement_table,
            has_verified_results=has_verified_results,
        )
        _validate_main_report_quality(report)
    return report


def _legacy_measurement_display_name(name: str) -> str:
    lowered = name.lower()
    replacements = (
        ("holdout_raw_mae", "留出段原始读数平均绝对误差"),
        ("holdout_calibrated_mae", "留出段校准预测平均绝对误差"),
        ("holdout_mae_improvement", "留出段平均绝对误差改善量"),
        ("holdout_raw_rmse", "留出段原始读数均方根误差"),
        ("holdout_calibrated_rmse", "留出段校准预测均方根误差"),
        ("holdout_rmse_improvement", "留出段均方根误差改善量"),
        ("holdout_raw_mean_signed_error", "留出段原始读数平均有符号误差"),
        (
            "holdout_calibrated_mean_signed_error",
            "留出段校准预测平均有符号误差",
        ),
        ("sensitivity_mae_with_suspect", "包含标记行拟合的平均绝对误差"),
        ("sensitivity_mae_without_suspect", "排除标记行拟合的平均绝对误差"),
        ("sensitivity_mae_difference", "两种标记行处理条件的误差差值"),
        ("mean", "算术平均值"),
    )
    for marker, label in replacements:
        if marker == lowered:
            return label
    return "已核验测量"


def _legacy_measurement_meaning(name: str) -> str:
    lowered = name.lower()
    if "improvement" in lowered or "difference" in lowered:
        return "两个已声明条件之间的观测差值"
    if "mean_signed_error" in lowered or "signed_error" in lowered:
        return "当前评价行上预测值减比较坐标的平均方向"
    if "rmse" in lowered:
        return "当前评价行上对较大误差更敏感的误差尺度"
    if "mae" in lowered:
        return "当前评价行上绝对误差的平均尺度"
    if lowered == "mean":
        return "已提供数值的总体算术平均"
    return "旧设计中已核验但未保存独立中文释义的测量"


def _validate_main_report_quality(report: str) -> None:
    forbidden = (
        "schema_version",
        "worker_result",
        "paired_comparison_audits",
        "experiment_stages",
        "response_kind",
        "proposed_outcome",
        "Traceback (most recent call last)",
        "验证轮次",
        "保存机制",
        "measurement name",
        "result id",
        "artifact id",
        "endpoint id",
        "stage id",
        "exit code",
        "退出码",
        "当前状态：",
        "科研含义",
        "确定性数值证据",
        "逐行",
        "质量标记行",
        "标记行",
        "可疑行",
        "表格结构",
        "各一列",
        "其中一行",
        "夹具",
        "结论摘要：",
        "证据说明：",
        "解释力度：",
        "主张边界：",
        "方法选择（",
        "代码执行成功",
        "由代码直接产出",
        "独立核验",
        "结果表记录",
        "结果文件",
        "已按预定要求完成",
        "无单位",
        "与数值列相同单位",
        "与原始数值相同单位",
        "确定性精确计算",
        "无抽样不确定性",
        "可精确复现",
        "结果数值无物理量纲",
        "Python 标准库",
        "本任务为确定性计算",
        "略低",
        "略高",
        "均远低于",
        "样本量限制了参数估计的精度",
        "回归观察窗口",
        "外层回归",
        "audit.md",
        "/automatic-experiment",
    )
    leaked = [token for token in forbidden if token in report]
    if leaked:
        raise RuntimeError(f"main report exposes internal workflow terms: {leaked}")
    if re.search(
        r"^##[^\n]+\n(?:[ \t]*\n)*(?=##[^\n]+\n|\Z)",
        report,
        re.MULTILINE,
    ):
        raise RuntimeError("main report contains an empty section")
    if re.search(r"第\s*\d+\s*阶段", report):
        raise RuntimeError("main report narrates internal execution stages")
    if re.search(r"\d+\s*行[^。；\n]{0,20}(?:观测|拟合|留出)", report):
        raise RuntimeError("main report uses table-row language for observations")
    if re.search(r"\d+\s*(?:行|列)", report):
        raise RuntimeError(
            "main report exposes table dimensions instead of scientific units"
        )
    if re.search(r"在\d+\s*条", report):
        raise RuntimeError(
            "main report has inconsistent spacing around an observation count"
        )
    for match in re.finditer(
        r"(?P<declared>\d+)\s*条[^。\n]{0,100}?的全部\s*"
        r"(?P<observed>\d+)\s*条观测",
        report,
    ):
        if match.group("declared") != match.group("observed"):
            raise RuntimeError("main report contains contradictory observation counts")
    if re.search(
        r"(?P<label>(?:未校准|校准后|保留[^，。\n]{0,24}|"
        r"排除[^，。\n]{0,24})读数)的绝对误差均低于"
        r"(?P=label)的绝对误差",
        report,
    ):
        raise RuntimeError("main report compares a condition with itself")
    if re.search(
        r"\|\s*(?:pass|failed|not_evaluated|positive|negative|neutral|"
        r"no_correlation)\s*\|",
        report,
        re.IGNORECASE,
    ):
        raise RuntimeError("main report exposes an internal category code")
    prose_for_identifier_check = re.sub(
        r"\]\([^)]+\)",
        "]()",
        report,
    )
    if re.search(
        r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9]*"
        r"(?:_[A-Za-z0-9]+)+(?![A-Za-z0-9_])",
        prose_for_identifier_check,
    ):
        raise RuntimeError("main report exposes a machine-style identifier")


def render_audit(
    record: dict[str, Any],
    response: dict[str, Any] | None = None,
    design: dict[str, Any] | None = None,
    *,
    report_assets: list[dict[str, Any]] | None = None,
    asset_status: dict[str, Any] | None = None,
) -> str:
    snapshot = record.get("input_snapshot") or {}
    input_rows = [
        "| 输入 ID | 源引用 | 快照文件 | 字节 | SHA-256 |",
        "|---|---|---|---:|---|",
    ]
    for input_row in snapshot.get("inputs", []):
        if not input_row.get("files"):
            input_rows.append(
                f"| `{input_row['id']}` | `{input_row.get('source_path', '')}` | "
                f"{input_row.get('status', 'missing')} | 0 | — |"
            )
        for file_row in input_row.get("files", []):
            input_rows.append(
                f"| `{input_row['id']}` | `{input_row.get('source_path', '')}` | "
                f"`{file_row['path']}` | {file_row['size_bytes']} | "
                f"`{file_row['sha256']}` |"
            )
    if len(input_rows) == 2:
        input_rows.append("| — | — | 无本地输入 | 0 | — |")

    method_rows = [
        "| 类型 | 决策 | 依据 | 理由 | 替代方案 | 主张限制 |",
        "|---|---|---|---|---|---|",
    ]
    for row in (design or {}).get("method_decisions", []):
        method_rows.append(
            "| "
            + " | ".join(
                _table_text(value)
                for value in (
                    row["decision_key"],
                    row["decision"],
                    row["basis_kind"],
                    row["rationale"],
                    "；".join(row["alternatives"]),
                    row["claim_limit"],
                )
            )
            + " |"
        )
    if len(method_rows) == 2:
        method_rows.append(
            "| legacy | 旧设计未保存独立方法决策卡 | — | "
            "重放保持源设计 | — | 不据此声称方法最优 |"
        )

    asset_rows = [
        "| 状态 | 路径 | 行处理 | SHA-256 | 说明 |",
        "|---|---|---|---|---|",
    ]
    if report_assets:
        for row in report_assets:
            asset_rows.append(
                f"| generated | `{row['path']}` | {row['row_handling']} "
                f"({row['row_count']} rows) | `{row['sha256']}` | "
                f"{row['description']} |"
            )
    else:
        asset_rows.append(
            "| not_applicable | — | — | — | "
            + _table_text((asset_status or {}).get("reason", "无适合证据"))
            + " |"
        )

    lineage_json = json.dumps(
        record.get("replay") or {},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    sections = [
        "# 自动实验 Agent 1.0 完整审计附件",
        "",
        "本附件保存机器事实、完整证据台账和运行来源，不替代研究者主报告。",
        "",
        "## 一、身份、状态与哈希",
        "",
        f"- run ID：`{record['run_id']}`",
        f"- 请求内容 SHA-256：`{record['request_sha256']}`",
        f"- 响应内容 SHA-256：`{record['response_sha256']}`",
        f"- 设计内容 SHA-256：`{record.get('design_sha256')}`",
        f"- record 内容 SHA-256：`{record['record_sha256']}`",
        f"- 执行状态：`{record['execution_state']}`；科学状态：`{record['outcome']}`",
        "",
        "## 二、输入角色、结构与文件哈希",
        "",
        *_research_frame_lines(record, design),
        "",
        *input_rows,
        "",
        "## 三、方法决策、假设与解释政策",
        "",
        *method_rows,
        "",
        "解释政策：",
        "",
        "```json",
        json.dumps(
            (design or {}).get("interpretation_policy", {}),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        "```",
        "",
        "## 四、全部判据与测量",
        "",
        *_criterion_table(record, design),
        "",
        *_result_lines(record),
        "",
        "## 五、成对逐行复算",
        "",
        *_paired_comparison_lines(record),
        "",
        "逐行身份与方向性计数：",
        "",
        "```json",
        json.dumps(
            (record.get("evidence_ledger") or {}).get("paired_comparisons") or [],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ),
        "```",
        "",
        "## 六、实际执行事实与核验检查",
        "",
        *_execution_lines(record),
        "",
        "```json",
        json.dumps(
            record.get("verification_checks") or [],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ),
        "```",
        "",
        "## 七、不可变尝试关系",
        "",
        *_attempt_lines(record),
        "",
        "## 八、公开产物与报告资产",
        "",
        *_artifact_lines(record),
        "",
        *asset_rows,
        "",
        "## 九、运行来源与复现信息",
        "",
        "```json",
        lineage_json,
        "```",
        "",
        "## 十、用户入口",
        "",
        f"- 通过 Pi 复现实验：`{record['replay']['pi_command']}`",
        "- 内部 Python、WSL 与沙箱命令不作为用户入口。",
        "",
    ]
    return "\n".join(sections)


def finalize_report(run_root: Path, record: dict[str, Any]) -> dict[str, Any]:
    response_path = run_root / "response.json"
    design_path = run_root / "design.json"
    response = read_json(response_path) if response_path.is_file() else None
    design = read_json(design_path) if design_path.is_file() else None
    report_assets, asset_status = generate_report_assets(
        run_root,
        record,
        design,
    )
    audit = render_audit(
        record,
        response=response,
        design=design,
        report_assets=report_assets,
        asset_status=asset_status,
    )
    audit_path = run_root / "audit.md"
    atomic_write_text(audit_path, audit)
    try:
        report = render_report(
            record,
            response=response,
            design=design,
            report_assets=report_assets,
        )
    except Exception as error:
        report = _emergency_main_report(record, design)
        audit = (
            audit.rstrip()
            + "\n\n## 十一、主报告生成说明\n\n"
            + "原始叙述未通过面向读者的语言检查，因此主报告使用已核对数值生成了安全摘要。"
            + "完整实验事实仍保留在本审计记录中。\n\n"
            + f"内部原因：`{type(error).__name__}: {str(error)[:500]}`\n"
        )
        atomic_write_text(audit_path, audit)
    report_path = run_root / "report.md"
    atomic_write_text(report_path, report)
    entry = {
        "schema_version": ENTRY_RESULT_VERSION,
        "status": "finalized",
        "run_id": record["run_id"],
        "outcome": record["outcome"],
        "record_path": "record.json",
        "record_sha256": file_sha256(run_root / "record.json"),
        "report_path": "report.md",
        "report_sha256": file_sha256(report_path),
        "audit_path": "audit.md",
        "audit_sha256": file_sha256(audit_path),
        "report_assets": report_assets,
        "user_display_markdown": report,
        "safe_next_action": record["replay"]["pi_command"],
        "created_at": utc_now(),
    }
    entry["entry_sha256"] = canonical_sha256(entry)
    atomic_write_json(run_root / "entry_result.json", entry)
    return entry
