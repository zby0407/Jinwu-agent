"""Deterministic researcher-facing view for a persisted hypothesis draft.

The working draft intentionally keeps the full scientific contract and audit
trace. This module renders a smaller conversational view from that persisted
state without asking a model to summarize it, so reader-facing prose cannot
invent evidence, rankings, or completion claims.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_CONFIDENCE_LABELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
}
_OPERATOR_LABELS = {
    "measurement_null": "测量与数据处理解释",
    "modal_baseline": "常规物理机制",
    "residual_anomaly": "随机波动或残差解释",
    "causal_edge_change": "因果联系变化",
    "regime_boundary": "深层状态变化",
    "causal_reversal": "因果方向反转",
    "latent_driver": "潜在驱动因素",
    "symmetry_break": "不对称性解释",
    "nonlinear_interaction": "非线性交互",
    "premise_reversal": "关键前提反转",
}
_INTERNAL_TERM_REPLACEMENTS = {
    "null_control": "测量或零假设",
    "modal_baseline": "常规机制",
    "positive_tail": "一类稀疏机制",
    "negative_tail": "另一类稀疏机制",
    "tail_candidate_unverified": "新颖性尚未核验",
    "exploratory_hypothesis": "探索性假设",
    "evidence_constrained_hypothesis": "受证据约束的假设",
    "supported_inference": "有依据的机制推断",
    "exploratory_inference": "探索性机制推断",
    "empirical_support": "实证支持",
    "零假设哨兵": "零假设",
    "Wiki 条目": "知识库材料",
    "Wiki条目": "知识库材料",
    "下一活动周": "下一太阳活动周期",
    "长尾搜索区域": "证据稀疏的探索区域",
    "长尾变化": "较少见的变化",
}
_LEVELS = {"low": 1, "medium": 2, "high": 3}
_WARNING_LABELS = {
    "ungrounded_numeric_threshold": "没有依据的数值门槛",
    "unoperationalized_decision_rule": "尚未操作化的判断词",
    "scope_conditions_missing": "缺失的适用边界",
    "scope_conditions_incomplete": "不完整的适用边界",
    "epistemic_status_missing": "缺失的证据状态",
    "uncertainty_incomplete": "不完整的不确定性说明",
    "candidate_incomplete": "不完整的候选",
    "unbound_evidence": "未绑定的证据",
    "evidence_role_mismatch": "证据角色不一致",
}


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _items(value: object) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return list(value)


def _text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())


def _join_text(values: object, *, limit: int = 2) -> str:
    texts = [_text(value) for value in _items(values)]
    return "；".join(text for text in texts[:limit] if text)


def _candidate_labels(candidates: list[Mapping[str, Any]]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for index, candidate in enumerate(candidates, start=1):
        candidate_id = candidate.get("id")
        if isinstance(candidate_id, str):
            labels[candidate_id] = f"候选 {index}"
            short_id = re.match(r"^(H\d+)(?:_|$)", candidate_id)
            if short_id is not None:
                labels[short_id.group(1)] = f"候选 {index}"
    return labels


def _clean_reader_text(value: object, labels: Mapping[str, str]) -> str:
    text = _text(value)
    if not text:
        return ""
    for source, replacement in sorted(
        labels.items(), key=lambda item: len(item[0]), reverse=True
    ):
        text = re.sub(
            rf"(?<![A-Za-z0-9_]){re.escape(source)}(?![A-Za-z0-9_])",
            replacement,
            text,
        )
    for source, replacement in _INTERNAL_TERM_REPLACEMENTS.items():
        text = text.replace(source, replacement)
    text = text.replace("有 Wiki 中", "已有背景材料对")
    text = text.replace("基于 Wiki 中", "基于已有背景材料所述的")
    text = text.replace("Wiki 中", "已有背景材料中")
    text = text.replace("Wiki", "背景材料")
    text = text.replace("绑定为直接证据", "能够作为直接证据")
    text = text.replace("已绑定的", "当前掌握的")
    text = text.replace("已绑定", "已有")
    text = re.sub(
        r"(?<![A-Za-z0-9_])H\d+(?:_[A-Za-z0-9_]+)?(?![A-Za-z0-9_])",
        "其他候选",
        text,
    )
    for raw, readable in _CONFIDENCE_LABELS.items():
        text = re.sub(
            rf"置信度(?:为|是)\s*{raw}(?![A-Za-z])",
            f"当前把握为{readable}",
            text,
            flags=re.IGNORECASE,
        )
    text = re.sub(
        r"(?:[。；]\s*)?置信度(?:为|是)?(?:中等|中|较低|低|较高|高)"
        r"[^。；]*$",
        "",
        text.rstrip("。；; "),
    ).rstrip("。；; ")
    text = re.sub(r"([。！？])；", r"\1", text)
    text = re.sub(r"。{2,}", "。", text)
    return text


def _research_premise(value: object, labels: Mapping[str, str]) -> str:
    text = _clean_reader_text(value, labels)
    if not text:
        return ""
    premise = re.split(
        r"(?=请(?:形成|提出|生成|给出|构建|搜索|比较|更新))",
        text,
        maxsplit=1,
    )[0].strip()
    if len(premise) < 12:
        premise = text
    premise = re.sub(
        r"^(?:把|将)?(?:下面|以下)?(?:这句|这句话|内容)?"
        r"(?:仅|只)?作为待核验的研究前提(?:，|、)?"
        r"(?:而不是|不是|并非)已证实(?:的)?事实[：:，,]?",
        "",
        premise,
    )
    premise = re.sub(r"^假设(?=在|本|该)", "", premise)
    return premise.rstrip("。；; ")


def _review_rows(snapshot: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    review = _mapping(snapshot.get("tail_review"))
    rows: dict[str, Mapping[str, Any]] = {}
    for value in _items(review.get("candidates")):
        row = _mapping(value)
        candidate_id = row.get("candidate_id")
        if isinstance(candidate_id, str):
            rows[candidate_id] = row
    return rows


def _candidate_heading(
    candidate: Mapping[str, Any],
    *,
    index: int,
    review_rows: Mapping[str, Mapping[str, Any]],
) -> str:
    row = review_rows.get(str(candidate.get("id")), {})
    operator = row.get("generation_operator")
    statement = _text(candidate.get("statement"))
    if "测量" in statement and (
        "代理" in statement or "数据" in statement or "平滑" in statement
    ):
        label = "测量与处理口径"
    else:
        label = ""
        for pattern in (
            r"主要(?:由|源于)(.+?)(?:造成|驱动|导致|产生|，|；|。|：|而非)",
            r"源于(.+?)(?:造成|驱动|导致|产生|，|；|。|：)",
            r"反映(?:了)?(.+?)(?:，|；|。|：)",
        ):
            mechanism_match = re.search(pattern, statement)
            if mechanism_match is not None:
                label = mechanism_match.group(1).strip("：: ")
                break
        label = re.sub(r"[（(][A-Za-z][^）)]*[）)]", "", label)
        label = re.sub(r"^两个周期极小期附近", "", label)
        label = re.sub(r"^(?:两个周期|周期24与周期25)", "", label)
        label = re.sub(r"^上升期期间", "", label)
        if not label or len(label) > 32:
            label = _OPERATOR_LABELS.get(str(operator), "竞争性解释")
    return f"## 候选 {index}：{label}"


def _candidate_evidence_line(
    candidate: Mapping[str, Any],
    labels: Mapping[str, str],
) -> str:
    supporting = _items(candidate.get("supporting_evidence"))
    opposing = _items(candidate.get("opposing_evidence"))
    epistemic = _mapping(candidate.get("epistemic_status"))
    empirical_support = epistemic.get("empirical_support")
    gaps = _join_text(candidate.get("evidence_gaps"), limit=1)
    if empirical_support == "none" or not supporting:
        opening = "目前没有直接实证支持"
    else:
        opening = f"目前有 {len(supporting)} 项材料提供支持或约束"
    if opposing:
        opening += f"，另有 {len(opposing)} 项反对材料"
    if gaps:
        opening += "；最关键的证据缺口是：" + _clean_reader_text(gaps, labels)
    return opening


def _priority_tests(
    candidates: list[Mapping[str, Any]],
    review_rows: Mapping[str, Mapping[str, Any]],
    labels: Mapping[str, str],
) -> list[tuple[str, str]]:
    def priority(candidate: Mapping[str, Any]) -> tuple[int, int, int, int]:
        row = review_rows.get(str(candidate.get("id")), {})
        metrics = _mapping(row.get("tail_metrics"))
        return (
            -_LEVELS.get(str(metrics.get("expected_information_gain")), 0),
            -_LEVELS.get(str(metrics.get("falsifiability")), 0),
            _LEVELS.get(str(metrics.get("test_cost")), 4),
            _LEVELS.get(str(metrics.get("evidence_risk")), 4),
        )

    ranked = sorted(candidates, key=priority)
    selected: list[tuple[str, str]] = []
    seen_objectives: set[str] = set()
    for candidate in ranked:
        next_test = _mapping(candidate.get("next_test"))
        objective = _clean_reader_text(next_test.get("objective"), labels)
        power = _clean_reader_text(next_test.get("discriminating_power"), labels)
        normalized = "".join(objective.casefold().split())
        if not objective or normalized in seen_objectives:
            continue
        seen_objectives.add(normalized)
        selected.append((objective, power))
        if len(selected) == 2:
            break
    return selected


def render_hypothesis_reader_markdown(
    snapshot: Mapping[str, Any],
    *,
    partial_reason: str | None = None,
) -> str:
    """Render a concise Chinese reader view from a persisted draft snapshot."""

    draft = _mapping(snapshot.get("draft"))
    candidates = [
        candidate
        for value in _items(draft.get("candidates"))
        if (candidate := _mapping(value))
    ]
    labels = _candidate_labels(candidates)
    review_rows = _review_rows(snapshot)
    research_question = _research_premise(draft.get("research_question"), labels)

    lines = ["# 科学假设组合", "", "## 先说结论", ""]
    if partial_reason:
        lines.extend(
            [
                "本次生成提前停止，下面展示的是已经保存的草稿；"
                "仍有部分内容尚未完成，因此不能把下面内容当作最终结论。",
                "",
            ]
        )
    warning_count = snapshot.get("soft_warning_count")
    if isinstance(warning_count, int) and warning_count > 0:
        warning_labels = []
        for warning in _items(snapshot.get("soft_warnings")):
            code = _mapping(warning).get("code")
            label = _WARNING_LABELS.get(str(code))
            if label and label not in warning_labels:
                warning_labels.append(label)
        detail = "、".join(warning_labels[:3]) or "尚未说明清楚的科学问题"
        lines.extend(
            [
                f"当前内容还有 {warning_count} 处需要补充或改写，主要涉及{detail}；"
                "处理完成前不能把它当作最终结论。",
                "",
            ]
        )
    if research_question:
        lines.append(
            f"这里把“{research_question}”作为待核验的研究前提，而不是已经证实的事实。"
        )
    lines.append(
        "下面的候选是相互竞争的解释，不代表其中任何一个已经成立，"
        "也不按新奇程度决定优先级。"
    )
    lines.extend(["", "### 最先做的检验", ""])
    tests = _priority_tests(candidates, review_rows, labels)
    if tests:
        for index, (objective, power) in enumerate(tests, start=1):
            suffix = f"。{power}" if power else ""
            lines.append(f"{index}. {objective}{suffix}")
    else:
        lines.append("当前草稿还没有形成足以区分候选的下一项检验。")

    lines.extend(
        [
            "",
            f"本轮保留 {len(candidates)} 个候选。以下顺序用于阅读，不表示优先级。",
            "",
        ]
    )

    for index, candidate in enumerate(candidates, start=1):
        mechanism = _mapping(candidate.get("mechanism"))
        scope = _mapping(candidate.get("scope_conditions"))
        predictions = [
            prediction
            for value in _items(candidate.get("predictions"))
            if (prediction := _mapping(value))
        ]
        prediction = predictions[0] if predictions else {}
        uncertainty = _mapping(candidate.get("uncertainty"))
        next_test = _mapping(candidate.get("next_test"))
        confidence = _mapping(candidate.get("confidence"))

        applicability = _clean_reader_text(candidate.get("applicability"), labels)
        does_not_apply = _clean_reader_text(
            _join_text(scope.get("does_not_apply_when"), limit=1),
            labels,
        )
        generalization = _clean_reader_text(
            _join_text(scope.get("generalization_limits"), limit=1),
            labels,
        )
        boundary_parts = [part.rstrip("。；; ") for part in (applicability,) if part]
        if does_not_apply:
            boundary_parts.append("出现以下情况时不适用：" + does_not_apply)
        if generalization:
            generalization = re.sub(
                r"^(?:不能|不应)外推(?:到|为)?[：:]?",
                "",
                generalization,
            ).strip()
            boundary_parts.append("外推限制：" + generalization)

        premises = _clean_reader_text(
            _join_text(mechanism.get("required_premises"), limit=2),
            labels,
        )
        prediction_text = _clean_reader_text(prediction.get("statement"), labels)
        prediction_text = prediction_text.rstrip("。；; ")
        weaken_text = _clean_reader_text(prediction.get("would_weaken_if"), labels)
        if not weaken_text:
            weaken_text = _clean_reader_text(
                _join_text(candidate.get("falsification_conditions"), limit=1),
                labels,
            )
        weaken_text = re.sub(r"^如果", "", weaken_text).rstrip("。；; ")
        alternatives = _clean_reader_text(
            _join_text(candidate.get("alternative_explanations"), limit=1),
            labels,
        )
        confounders = _clean_reader_text(
            _join_text(candidate.get("confounders"), limit=1),
            labels,
        )
        uncertainty_source = _clean_reader_text(
            _join_text(uncertainty.get("sources"), limit=1),
            labels,
        )
        uncertainty_impact = _clean_reader_text(
            uncertainty.get("implications"),
            labels,
        )
        objective = _clean_reader_text(next_test.get("objective"), labels)
        power = _clean_reader_text(next_test.get("discriminating_power"), labels)
        confidence_level = _CONFIDENCE_LABELS.get(str(confidence.get("level")), "未定")
        confidence_basis = _clean_reader_text(confidence.get("basis"), labels)
        confidence_basis = confidence_basis.rstrip("。；; ")
        confidence_basis = re.sub(
            r"(?:[，；。]\s*)?(?:故|因此)?当前把握为[高中低]$",
            "",
            confidence_basis,
        ).rstrip("。；; ")
        statement = _clean_reader_text(candidate.get("statement"), labels)

        lines.extend(
            [
                _candidate_heading(
                    candidate,
                    index=index,
                    review_rows=review_rows,
                ),
                "",
                f"- **主张：** {statement}",
                f"- **适用边界：** {'；'.join(boundary_parts) or '当前草稿未写清。'}",
                f"- **成立需要：** {premises or '当前草稿未写清必要前提。'}",
                (
                    f"- **怎样与其他解释区分：** "
                    f"{prediction_text or '尚未形成可观测预测。'}"
                    + (f"；如果{weaken_text}，该候选会被削弱。" if weaken_text else "")
                ),
                (
                    f"- **其他可能性与混杂：** "
                    f"{alternatives or '尚未列出替代解释'}；"
                    f"{confounders or '尚未列出主要混杂因素'}。"
                ),
                (
                    f"- **证据与不确定性：** "
                    f"{_candidate_evidence_line(candidate, labels)}"
                    + (
                        f"；主要不确定性来自：{uncertainty_source}"
                        if uncertainty_source
                        else ""
                    )
                    + (
                        f"；它可能导致：{uncertainty_impact}"
                        if uncertainty_impact
                        else ""
                    )
                ),
                (
                    f"- **最值得做的下一步：** {objective or '当前草稿未写清。'}"
                    + (f"。{power}" if power else "")
                ),
                (
                    f"- **当前把握：** {confidence_level}"
                    + (f"。{confidence_basis}" if confidence_basis else "")
                ),
                "",
            ]
        )

    lines.extend(
        [
            "以上内容只说明目前有哪些可检验的解释，以及怎样区分它们；"
            "不代表任何候选已经得到证实。",
            "",
        ]
    )
    return "\n".join(lines)


__all__ = ["render_hypothesis_reader_markdown"]
