"""假设组合的确定性排序核验。

模型负责给出 rubric 七维等级与成对比较判断；代码负责封闭字段校验、
锚点闭合（每条名次必须附可追溯理由与关键证据锚点，不允许只输出
排名序号）、以及排序与各维度理由之间的一致性核验。

七维 rubric 取自本系统 co-scientist 设计文稿：数据支持度、模型一致性、
物理合理性、不确定性、反例周期、消融敏感性、漂移风险。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .contracts import ContractError, _array, _enum, _exact_fields, _id, _object, _text
from .upstream import KNOWN_DATA_COVERAGES

if TYPE_CHECKING:
    from .harness import EvidenceRegister

RANKING_VERSION = "scientific-hypothesis-ranking-v1"

RUBRIC_DIMENSIONS: tuple[dict[str, str], ...] = (
    {"key": "data_support", "label": "数据支持度"},
    {"key": "model_consistency", "label": "模型一致性"},
    {"key": "physical_plausibility", "label": "物理合理性"},
    {"key": "uncertainty", "label": "不确定性"},
    {"key": "counterexample_coverage", "label": "反例周期"},
    {"key": "ablation_sensitivity", "label": "消融敏感性"},
    {"key": "drift_risk", "label": "漂移风险"},
)
RUBRIC_KEYS = tuple(spec["key"] for spec in RUBRIC_DIMENSIONS)

GRADE_STRENGTH = {"strong": 3, "moderate": 2, "weak": 1}
GRADE_LABELS = {"strong": "强", "moderate": "中", "weak": "弱"}


def validate_ranking_request(
    payload: object,
    candidates: list[dict[str, Any]],
    register: EvidenceRegister,
) -> dict[str, Any]:
    """校验排序请求；candidates 须为已通过响应合同校验的候选列表。"""

    request = _object(payload, "ranking request")
    _exact_fields(
        request,
        {"schema_version", "rubric", "weights", "ranked", "pairwise_judgments"},
        "ranking request",
    )
    if request["schema_version"] != RANKING_VERSION:
        raise ContractError(f"schema_version 必须为 {RANKING_VERSION}")

    candidate_ids = [candidate["id"] for candidate in candidates]
    candidate_set = set(candidate_ids)

    # rubric 块必须原样声明七维度（防模型自创维度）。
    rubric = _array(request["rubric"], "ranking request.rubric")
    rubric_keys = []
    for index, item in enumerate(rubric):
        label = f"ranking request.rubric[{index}]"
        row = _object(item, label)
        _exact_fields(row, {"key", "label"}, label)
        rubric_keys.append(_text(row["key"], f"{label}.key", max_length=64))
        _text(row["label"], f"{label}.label", max_length=100)
    if sorted(rubric_keys) != sorted(RUBRIC_KEYS):
        raise ContractError(
            "ranking request.rubric 必须恰好包含七个维度：" + "、".join(RUBRIC_KEYS)
        )

    weights_raw = _object(request["weights"], "ranking request.weights")
    if set(weights_raw) - set(RUBRIC_KEYS):
        raise ContractError("ranking request.weights 含未定义维度")
    weights: dict[str, float] = {}
    for key in RUBRIC_KEYS:
        value = weights_raw.get(key, 1)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= 3:
            raise ContractError(f"ranking request.weights.{key} 必须是 1 到 3 的数")
        weights[key] = float(value)

    # 每条名次：可追溯理由 + 关键证据锚点（强制）。
    ranked_raw = _array(
        request["ranked"], "ranking request.ranked", min_items=1, max_items=8
    )
    ranked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(ranked_raw):
        label = f"ranking request.ranked[{index}]"
        row = _object(item, label)
        _exact_fields(
            row,
            {
                "candidate_id",
                "rank",
                "rationale",
                "key_evidence_ids",
                "dimension_grades",
                "weakest_dimensions",
                "confidence_note",
            },
            label,
        )
        candidate_id = _id(row["candidate_id"], f"{label}.candidate_id")
        if candidate_id not in candidate_set:
            raise ContractError(f"{label}.candidate_id 未指向任何候选：{candidate_id}")
        if candidate_id in seen:
            raise ContractError(f"{label}.candidate_id 重复：{candidate_id}")
        seen.add(candidate_id)
        rank = row["rank"]
        if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
            raise ContractError(f"{label}.rank 必须是不小于 1 的整数")

        key_evidence_ids = [
            _id(value, f"{label}.key_evidence_ids[{i}]")
            for i, value in enumerate(
                _array(row["key_evidence_ids"], f"{label}.key_evidence_ids", max_items=12)
            )
        ]
        for evidence_id in key_evidence_ids:
            entry = register.get(evidence_id)
            if entry is None:
                raise ContractError(
                    f"{label}.key_evidence_ids 引用了未绑定的证据：{evidence_id}"
                )
            if not entry["verified_support"]:
                raise ContractError(
                    f"{label}.key_evidence_ids 引用了未核验证据 {evidence_id}；"
                    "排序锚点只允许使用已核验证据"
                )

        grades_raw = _object(row["dimension_grades"], f"{label}.dimension_grades")
        _exact_fields(grades_raw, set(RUBRIC_KEYS), f"{label}.dimension_grades")
        grades = {
            key: _enum(grades_raw[key], set(GRADE_STRENGTH), f"{label}.dimension_grades.{key}")
            for key in RUBRIC_KEYS
        }
        anchor_entries = [register.get(evidence_id) for evidence_id in key_evidence_ids]
        empirical_anchors = [
            entry
            for entry in anchor_entries
            if (
                entry is not None
                and entry["role"] == "supports"
                and not entry["material_id"].startswith("kb_")
            )
        ]
        if grades["data_support"] == "strong" and not empirical_anchors:
            raise ContractError(
                f"{label}.dimension_grades.data_support 不得评为 strong："
                "Wiki 机制条目或限制性证据不能替代已核验的观测/实验支持"
            )

        weakest = [
            _enum(value, set(RUBRIC_KEYS), f"{label}.weakest_dimensions[{i}]")
            for i, value in enumerate(
                _array(
                    row["weakest_dimensions"],
                    f"{label}.weakest_dimensions",
                    max_items=len(RUBRIC_KEYS),
                )
            )
        ]
        if len(weakest) != len(set(weakest)):
            raise ContractError(f"{label}.weakest_dimensions 中的维度必须互不相同")

        ranked.append(
            {
                "candidate_id": candidate_id,
                "rank": rank,
                "rationale": _text(row["rationale"], f"{label}.rationale", max_length=2_000),
                "key_evidence_ids": key_evidence_ids,
                "dimension_grades": grades,
                "weakest_dimensions": weakest,
                "confidence_note": _text(
                    row["confidence_note"], f"{label}.confidence_note", max_length=1_000
                ),
            }
        )

    if seen != candidate_set:
        missing = sorted(candidate_set - seen)
        raise ContractError(
            "ranking request.ranked 必须覆盖全部候选；缺失：{}".format("、".join(missing))
        )
    ranks = sorted(row["rank"] for row in ranked)
    if ranks != list(range(1, len(ranked) + 1)):
        raise ContractError("ranking request.ranked 的名次必须是从 1 开始的连续整数")

    # 成对比较（可选）；判断依据必须非空且引用候选真实差异。
    judgments: list[dict[str, Any]] = []
    pair_keys: set[tuple[str, str]] = set()
    for index, item in enumerate(
        _array(
            request["pairwise_judgments"],
            "ranking request.pairwise_judgments",
            max_items=28,
        )
    ):
        label = f"ranking request.pairwise_judgments[{index}]"
        row = _object(item, label)
        _exact_fields(row, {"left_id", "right_id", "preferred_id", "basis"}, label)
        left = _id(row["left_id"], f"{label}.left_id")
        right = _id(row["right_id"], f"{label}.right_id")
        preferred = _id(row["preferred_id"], f"{label}.preferred_id")
        if left == right:
            raise ContractError(f"{label} 的左右两侧不能是同一候选")
        for side, side_label in ((left, "left_id"), (right, "right_id")):
            if side not in candidate_set:
                raise ContractError(f"{label}.{side_label} 未指向任何候选：{side}")
        if preferred not in {left, right}:
            raise ContractError(f"{label}.preferred_id 必须是左右两侧之一")
        pair_key = tuple(sorted((left, right)))
        if pair_key in pair_keys:
            raise ContractError(f"{label} 与之前的条目比较了同一对候选")
        pair_keys.add(pair_key)
        judgments.append(
            {
                "left_id": left,
                "right_id": right,
                "preferred_id": preferred,
                "basis": _text(row["basis"], f"{label}.basis", max_length=1_000),
            }
        )

    return {
        "schema_version": RANKING_VERSION,
        "weights": weights,
        "ranked": ranked,
        "pairwise_judgments": judgments,
    }


def check_ranking_consistency(
    ranking: dict[str, Any], candidates: list[dict[str, Any]]
) -> list[str]:
    """核验名次与维度等级、成对判断之间的一致性（代码重算，不采信模型结论）。"""

    candidate_by_id = {candidate["id"]: candidate for candidate in candidates}
    errors: list[str] = []

    for row in ranking["ranked"]:
        cid = row["candidate_id"]
        label = f"候选 {cid}"
        candidate = candidate_by_id[cid]
        supporting_ids = {link["evidence_id"] for link in candidate["supporting_evidence"]}
        anchored_outside = [
            evidence_id
            for evidence_id in row["key_evidence_ids"]
            if evidence_id not in supporting_ids
        ]
        if anchored_outside:
            errors.append(
                f"{label} 的排序锚点 {', '.join(anchored_outside)} "
                "未出现在该候选的支持证据中；关键证据锚点必须来自该候选自身的已核验支持证据"
            )
        if not row["key_evidence_ids"] and candidate["supporting_evidence"]:
            errors.append(
                f"{label} 有已核验支持证据但排序未附任何关键证据锚点；"
                "每条名次必须给出可追溯锚点"
            )
        # 领域约束核验规则：可泛化表述超出数据覆盖范围的候选，其"数据支持度"
        # 维度不得评 strong（与置信度门同源的写死规则）。
        applicability = candidate.get("applicability", "")
        statement_scope = candidate.get("statement", "") + " " + applicability
        for spec in KNOWN_DATA_COVERAGES:
            coverage_mentions = re.search(spec["pattern"].pattern, statement_scope, re.IGNORECASE)
            if coverage_mentions and spec["scope_pattern"].search(statement_scope):
                if row["dimension_grades"]["data_support"] == "strong":
                    errors.append(
                        f"{label} 的可泛化表述超出 {spec['product']} 的覆盖范围"
                        f"（{spec['coverage']}），数据支持度不得评为 strong"
                    )

    pairwise_preference: dict[tuple[str, str], str] = {}
    for judgment in ranking["pairwise_judgments"]:
        pair_key = tuple(sorted((judgment["left_id"], judgment["right_id"])))
        pairwise_preference[pair_key] = judgment["preferred_id"]

    rank_by_id = {row["candidate_id"]: row["rank"] for row in ranking["ranked"]}
    for pair_key, preferred in pairwise_preference.items():
        other = pair_key[0] if pair_key[1] == preferred else pair_key[1]
        if rank_by_id[preferred] > rank_by_id[other]:
            errors.append(
                f"成对判断倾向 {preferred} 优于 {other}，但名次相反；"
                "请修正名次或补充说明为何推翻成对判断"
            )
    return errors


def compute_dimension_scores(ranking: dict[str, Any]) -> dict[str, dict[str, float]]:
    """按权重把定性等级折算为加权总分（确定性重算，供报告展示）。"""

    weights = ranking["weights"]
    total_weight = sum(weights.values())
    scores: dict[str, dict[str, float]] = {}
    for row in ranking["ranked"]:
        weighted = sum(
            GRADE_STRENGTH[row["dimension_grades"][key]] * weights[key]
            for key in RUBRIC_KEYS
        )
        scores[row["candidate_id"]] = {
            "weighted_total": round(weighted, 4),
            "weighted_average": round(weighted / total_weight, 4),
        }
    return scores


__all__ = [
    "GRADE_LABELS",
    "GRADE_STRENGTH",
    "RANKING_VERSION",
    "RUBRIC_DIMENSIONS",
    "RUBRIC_KEYS",
    "check_ranking_consistency",
    "compute_dimension_scores",
    "validate_ranking_request",
]
