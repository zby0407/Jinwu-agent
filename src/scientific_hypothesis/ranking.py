"""假设组合的确定性排序核验。

模型负责给出 rubric 七维等级与成对比较判断；代码负责封闭字段校验、
锚点闭合（每条名次必须附可追溯理由与关键证据锚点，不允许只输出
排名序号）、以及排序与各维度理由之间的一致性核验。

七维 rubric 取自本系统 co-scientist 设计文稿：数据支持度、模型一致性、
物理合理性、不确定性、反例周期、消融敏感性、漂移风险。
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from .contracts import ContractError, _array, _enum, _exact_fields, _id, _object, _text
from .upstream import KNOWN_DATA_COVERAGES

if TYPE_CHECKING:
    from .harness import EvidenceRegister

RANKING_VERSION = "scientific-hypothesis-ranking-v1"
PORTFOLIO_RANKING_VERSION = "scientific-hypothesis-portfolio-ranking-v2"

CLAIM_TYPES = {
    "descriptive_relationship",
    "predictive",
    "mechanism_candidate",
    "null_hypothesis",
    "measurement_explanation",
}
EVIDENCE_STATUSES = {"supported", "mixed", "unsupported", "insufficient"}
SUPPORT_LEVELS = {"high", "medium", "low"}
PRIORITY_LEVELS = {"high", "medium", "low"}
OUT_OF_SAMPLE_STATUSES = {
    "beats_baseline",
    "skill_supported",
    "mixed_evidence",
    "tested_no_skill",
    "blocked_by_data",
    "execution_failed",
    "not_tested",
    "not_applicable",
}
SENSITIVITY_STATUSES = {"supports", "fragile", "not_tested", "not_applicable"}
FALSIFIABILITY_STATUSES = {"clear", "partial", "unclear"}
FEASIBILITY_STATUSES = {"executable_now", "requires_new_data", "not_currently_feasible"}
PORTFOLIO_ROLES = {
    "empirical_anchor",
    "physical_precursor",
    "physical_discriminator",
    "challenger",
}
PORTFOLIO_STATUSES = {
    "candidate_pending_test",
    "active_top3",
    "challenger_pool",
    "rejected",
    "blocked_by_data",
}
FORECAST_ORIGINS = {"early_cycle", "cycle_minimum", "not_applicable"}
_LIFECYCLE_FIELDS = {
    "portfolio_role",
    "portfolio_status",
    "forecast_origin",
    "forecast_receipt_ref",
}

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
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 1 <= value <= 3
        ):
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
                _array(
                    row["key_evidence_ids"], f"{label}.key_evidence_ids", max_items=12
                )
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
            key: _enum(
                grades_raw[key], set(GRADE_STRENGTH), f"{label}.dimension_grades.{key}"
            )
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
                "rationale": _text(
                    row["rationale"], f"{label}.rationale", max_length=2_000
                ),
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
            "ranking request.ranked 必须覆盖全部候选；缺失：{}".format(
                "、".join(missing)
            )
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
        supporting_ids = {
            link["evidence_id"] for link in candidate["supporting_evidence"]
        }
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
            coverage_mentions = re.search(
                spec["pattern"].pattern, statement_scope, re.IGNORECASE
            )
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


def _rank(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractError(f"{label} 必须是不小于 1 的整数")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError(f"{label} 必须是布尔值")
    return value


def _text_list(
    value: object, label: str, *, min_items: int = 0, max_items: int = 20
) -> list[str]:
    return [
        _text(item, f"{label}[{index}]", max_length=2_000)
        for index, item in enumerate(
            _array(value, label, min_items=min_items, max_items=max_items)
        )
    ]


def _evidence_links(
    value: object,
    label: str,
    register: EvidenceRegister,
    *,
    expected_roles: set[str],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(_array(value, label, max_items=30)):
        row_label = f"{label}[{index}]"
        row = _object(item, row_label)
        _exact_fields(
            row,
            {"evidence_id", "dependency_group_id", "relation"},
            row_label,
        )
        evidence_id = _id(row["evidence_id"], f"{row_label}.evidence_id")
        if evidence_id in seen:
            raise ContractError(f"{label} 不得重复引用证据 {evidence_id}")
        seen.add(evidence_id)
        entry = register.get(evidence_id)
        if entry is None:
            raise ContractError(f"{row_label} 引用了未绑定证据：{evidence_id}")
        if not entry["verified_support"]:
            raise ContractError(f"{row_label} 引用了未核验证据：{evidence_id}")
        if entry["role"] not in expected_roles:
            raise ContractError(
                f"{row_label} 的证据角色 {entry['role']} 与本字段不一致"
            )
        rows.append(
            {
                "evidence_id": evidence_id,
                "dependency_group_id": _id(
                    row["dependency_group_id"],
                    f"{row_label}.dependency_group_id",
                ),
                "relation": _text(
                    row["relation"], f"{row_label}.relation", max_length=1_000
                ),
            }
        )
    return rows


def _forecast_receipt_ref(value: object, label: str) -> str | None:
    if value is None:
        return None
    ref = _text(value, label, max_length=500).replace("\\", "/")
    path = PurePosixPath(ref)
    if path.is_absolute() or ".." in path.parts or not ref.startswith("experiment/runs/"):
        raise ContractError(
            f"{label} 必须是 experiment/runs/ 下的相对 forecast receipt 路径"
        )
    if path.name != "forecast_experiment_receipt.json":
        raise ContractError(f"{label} 必须指向 forecast_experiment_receipt.json")
    return ref


def _receipt_observable_kind(
    receipt_ref: str,
    register: EvidenceRegister,
    label: str,
) -> str:
    matches: list[str] = []
    for entry in register.all():
        if not entry.get("verified_support"):
            continue
        excerpt = entry.get("excerpt")
        if not isinstance(excerpt, str):
            continue
        try:
            payload = json.loads(excerpt)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get("forecast_receipt_ref") != receipt_ref:
            continue
        observable_kind = payload.get("observable_kind")
        if isinstance(observable_kind, str):
            matches.append(observable_kind)
    if not matches:
        raise ContractError(f"{label} 没有对应的已核验 forecast receipt 证据")
    if len(set(matches)) != 1:
        raise ContractError(f"{label} 对应的 forecast receipt 观测量定义冲突")
    return matches[0]


def _normalize_lifecycle(
    row: dict[str, Any],
    label: str,
    register: EvidenceRegister,
) -> dict[str, str | None]:
    supplied = _LIFECYCLE_FIELDS.intersection(row)
    if supplied and supplied != _LIFECYCLE_FIELDS:
        missing = sorted(_LIFECYCLE_FIELDS.difference(row))
        raise ContractError(f"{label} 生命周期字段必须成组提供；缺失：{', '.join(missing)}")
    if not supplied:
        return {
            "portfolio_role": "challenger",
            "portfolio_status": "challenger_pool",
            "forecast_origin": "not_applicable",
            "forecast_receipt_ref": None,
        }

    role = _enum(row["portfolio_role"], PORTFOLIO_ROLES, f"{label}.portfolio_role")
    status = _enum(
        row["portfolio_status"], PORTFOLIO_STATUSES, f"{label}.portfolio_status"
    )
    origin = _enum(
        row["forecast_origin"], FORECAST_ORIGINS, f"{label}.forecast_origin"
    )
    receipt_ref = _forecast_receipt_ref(
        row["forecast_receipt_ref"], f"{label}.forecast_receipt_ref"
    )
    required_origin = {
        "empirical_anchor": "early_cycle",
        "physical_precursor": "cycle_minimum",
        "physical_discriminator": "cycle_minimum",
    }.get(role)
    if required_origin is not None and origin != required_origin:
        raise ContractError(
            f"{label}.forecast_origin 与 portfolio_role={role} 不一致；"
            f"必须为 {required_origin}"
        )
    if role == "challenger" and origin not in FORECAST_ORIGINS:
        raise ContractError(f"{label}.forecast_origin 无效")
    if status == "blocked_by_data" and receipt_ref is not None:
        raise ContractError(f"{label}: blocked_by_data 不得声明可用 forecast receipt")
    if receipt_ref is not None:
        observable_kind = _receipt_observable_kind(receipt_ref, register, label)
        allowed_observables = {
            "empirical_anchor": {"sunspot_rise_metric"},
            "physical_precursor": {
                "polar_aperture_field",
                "hemispheric_polar_flux",
            },
            "physical_discriminator": {"axial_dipole_moment"},
            "challenger": {
                "sunspot_rise_metric",
                "polar_aperture_field",
                "hemispheric_polar_flux",
                "axial_dipole_moment",
            },
        }[role]
        if observable_kind not in allowed_observables:
            expected = ", ".join(sorted(allowed_observables))
            raise ContractError(
                f"{label}: portfolio_role={role} 的 receipt 必须使用 {expected}，"
                f"不能使用 {observable_kind}"
            )
    return {
        "portfolio_role": role,
        "portfolio_status": status,
        "forecast_origin": origin,
        "forecast_receipt_ref": receipt_ref,
    }


def validate_portfolio_ranking(
    payload: object,
    register: EvidenceRegister,
) -> dict[str, Any]:
    """Validate claim-specific support and experiment priority without a total score.

    Semantic normalization, claim classification, and experiment value are bounded
    model judgments.  This validator closes their identifiers and evidence anchors,
    recomputes independent dependency groups, and applies only objective release
    caps.  Scientific support and research priority remain separate ordinal views.
    """

    request = _object(payload, "portfolio ranking")
    _exact_fields(
        request,
        {
            "schema_version",
            "source_runs",
            "hypothesis_groups",
            "ranked_hypotheses",
            "selected_next_experiment",
        },
        "portfolio ranking",
    )
    if request["schema_version"] != PORTFOLIO_RANKING_VERSION:
        raise ContractError(f"schema_version 必须为 {PORTFOLIO_RANKING_VERSION}")

    source_runs = _text_list(
        request["source_runs"], "portfolio ranking.source_runs", min_items=1
    )
    if len(source_runs) != len(set(source_runs)):
        raise ContractError("portfolio ranking.source_runs 必须互不相同")
    source_run_set = set(source_runs)

    groups: list[dict[str, Any]] = []
    hypothesis_ids: list[str] = []
    assigned_members: set[tuple[str, str]] = set()
    for index, item in enumerate(
        _array(
            request["hypothesis_groups"],
            "portfolio ranking.hypothesis_groups",
            min_items=1,
            max_items=30,
        )
    ):
        label = f"portfolio ranking.hypothesis_groups[{index}]"
        row = _object(item, label)
        _exact_fields(
            row,
            {
                "hypothesis_id",
                "normalized_statement",
                "member_candidates",
                "deduplication_rationale",
            },
            label,
        )
        hypothesis_id = _id(row["hypothesis_id"], f"{label}.hypothesis_id")
        members: list[dict[str, str]] = []
        for member_index, member_value in enumerate(
            _array(
                row["member_candidates"],
                f"{label}.member_candidates",
                min_items=1,
                max_items=30,
            )
        ):
            member_label = f"{label}.member_candidates[{member_index}]"
            member = _object(member_value, member_label)
            _exact_fields(member, {"run_id", "candidate_id"}, member_label)
            run_id = _text(member["run_id"], f"{member_label}.run_id", max_length=200)
            if run_id not in source_run_set:
                raise ContractError(f"{member_label}.run_id 未列入 source_runs")
            candidate_id = _id(member["candidate_id"], f"{member_label}.candidate_id")
            member_key = (run_id, candidate_id)
            if member_key in assigned_members:
                raise ContractError(
                    f"候选 {run_id}/{candidate_id} 只能归入一个规范化假设"
                )
            assigned_members.add(member_key)
            members.append({"run_id": run_id, "candidate_id": candidate_id})
        hypothesis_ids.append(hypothesis_id)
        groups.append(
            {
                "hypothesis_id": hypothesis_id,
                "normalized_statement": _text(
                    row["normalized_statement"],
                    f"{label}.normalized_statement",
                    max_length=2_000,
                ),
                "member_candidates": members,
                "deduplication_rationale": _text(
                    row["deduplication_rationale"],
                    f"{label}.deduplication_rationale",
                    max_length=2_000,
                ),
            }
        )
    if len(hypothesis_ids) != len(set(hypothesis_ids)):
        raise ContractError(
            "portfolio ranking.hypothesis_groups 的 hypothesis_id 必须唯一"
        )
    hypothesis_set = set(hypothesis_ids)

    ranked: list[dict[str, Any]] = []
    ranked_ids: list[str] = []
    for index, item in enumerate(
        _array(
            request["ranked_hypotheses"],
            "portfolio ranking.ranked_hypotheses",
            min_items=1,
            max_items=30,
        )
    ):
        label = f"portfolio ranking.ranked_hypotheses[{index}]"
        row = _object(item, label)
        ranked_fields = {
            "hypothesis_id",
            "support_rank",
            "research_priority_rank",
            "claim_type",
            "current_evidence_status",
            "scientific_support",
            "research_priority",
            "data_sources_verified",
            "support_evidence",
            "opposing_evidence",
            "out_of_sample_validation",
            "effect_uncertainty",
            "sensitivity",
            "falsifiability",
            "key_limitations",
            "strongest_null_hypothesis",
            "next_experiment",
            "ranking_rationale",
            "release_boundary",
        }
        if _LIFECYCLE_FIELDS.intersection(row):
            ranked_fields |= _LIFECYCLE_FIELDS
        _exact_fields(
            row,
            ranked_fields,
            label,
        )
        hypothesis_id = _id(row["hypothesis_id"], f"{label}.hypothesis_id")
        if hypothesis_id not in hypothesis_set:
            raise ContractError(f"{label}.hypothesis_id 未指向规范化假设")
        lifecycle = _normalize_lifecycle(row, label, register)

        support = _object(row["scientific_support"], f"{label}.scientific_support")
        _exact_fields(support, {"level", "rationale"}, f"{label}.scientific_support")
        support_level = _enum(
            support["level"], SUPPORT_LEVELS, f"{label}.scientific_support.level"
        )
        support_row = {
            "level": support_level,
            "rationale": _text(
                support["rationale"],
                f"{label}.scientific_support.rationale",
                max_length=2_000,
            ),
        }

        priority = _object(row["research_priority"], f"{label}.research_priority")
        _exact_fields(priority, {"level", "rationale"}, f"{label}.research_priority")
        priority_row = {
            "level": _enum(
                priority["level"],
                PRIORITY_LEVELS,
                f"{label}.research_priority.level",
            ),
            "rationale": _text(
                priority["rationale"],
                f"{label}.research_priority.rationale",
                max_length=2_000,
            ),
        }

        support_evidence = _evidence_links(
            row["support_evidence"],
            f"{label}.support_evidence",
            register,
            expected_roles={"supports"},
        )
        opposing_evidence = _evidence_links(
            row["opposing_evidence"],
            f"{label}.opposing_evidence",
            register,
            expected_roles={"opposes", "limits"},
        )
        all_evidence_ids = {
            item["evidence_id"] for item in support_evidence + opposing_evidence
        }
        if len(all_evidence_ids) != len(support_evidence) + len(opposing_evidence):
            raise ContractError(f"{label} 同一证据不能同时作为支持与反对证据")

        out_of_sample = _object(
            row["out_of_sample_validation"], f"{label}.out_of_sample_validation"
        )
        _exact_fields(
            out_of_sample,
            {"status", "baseline_comparison"},
            f"{label}.out_of_sample_validation",
        )
        oos_status = _enum(
            out_of_sample["status"],
            OUT_OF_SAMPLE_STATUSES,
            f"{label}.out_of_sample_validation.status",
        )
        out_of_sample_row = {
            "status": oos_status,
            "baseline_comparison": _text(
                out_of_sample["baseline_comparison"],
                f"{label}.out_of_sample_validation.baseline_comparison",
                max_length=2_000,
            ),
        }

        effect = _object(row["effect_uncertainty"], f"{label}.effect_uncertainty")
        _exact_fields(
            effect,
            {"effect_summary", "interval_summary", "interval_crosses_null"},
            f"{label}.effect_uncertainty",
        )
        crosses_null = effect["interval_crosses_null"]
        if crosses_null is not None:
            crosses_null = _boolean(
                crosses_null, f"{label}.effect_uncertainty.interval_crosses_null"
            )
        effect_row = {
            "effect_summary": _text(
                effect["effect_summary"],
                f"{label}.effect_uncertainty.effect_summary",
                max_length=2_000,
            ),
            "interval_summary": _text(
                effect["interval_summary"],
                f"{label}.effect_uncertainty.interval_summary",
                max_length=2_000,
            ),
            "interval_crosses_null": crosses_null,
        }

        sensitivity = _object(row["sensitivity"], f"{label}.sensitivity")
        sensitivity_fields = {
            "leave_one_out",
            "temporal_split",
            "measurement_regime",
            "definition",
        }
        _exact_fields(sensitivity, sensitivity_fields, f"{label}.sensitivity")
        sensitivity_row = {
            key: _enum(
                sensitivity[key], SENSITIVITY_STATUSES, f"{label}.sensitivity.{key}"
            )
            for key in sensitivity_fields
        }

        falsifiability = _object(row["falsifiability"], f"{label}.falsifiability")
        _exact_fields(
            falsifiability,
            {"status", "conditions"},
            f"{label}.falsifiability",
        )
        falsifiability_row = {
            "status": _enum(
                falsifiability["status"],
                FALSIFIABILITY_STATUSES,
                f"{label}.falsifiability.status",
            ),
            "conditions": _text_list(
                falsifiability["conditions"],
                f"{label}.falsifiability.conditions",
                min_items=1,
            ),
        }

        next_experiment = _object(row["next_experiment"], f"{label}.next_experiment")
        _exact_fields(
            next_experiment,
            {"objective", "discriminating_power", "feasibility"},
            f"{label}.next_experiment",
        )
        next_experiment_row = {
            "objective": _text(
                next_experiment["objective"],
                f"{label}.next_experiment.objective",
                max_length=2_000,
            ),
            "discriminating_power": _text(
                next_experiment["discriminating_power"],
                f"{label}.next_experiment.discriminating_power",
                max_length=2_000,
            ),
            "feasibility": _enum(
                next_experiment["feasibility"],
                FEASIBILITY_STATUSES,
                f"{label}.next_experiment.feasibility",
            ),
        }

        claim_type = _enum(row["claim_type"], CLAIM_TYPES, f"{label}.claim_type")
        evidence_status = _enum(
            row["current_evidence_status"],
            EVIDENCE_STATUSES,
            f"{label}.current_evidence_status",
        )
        sources_verified = _boolean(
            row["data_sources_verified"], f"{label}.data_sources_verified"
        )

        if lifecycle["portfolio_status"] == "active_top3":
            if not sources_verified or evidence_status in {"unsupported", "insufficient"}:
                raise ContractError(
                    f"{label}: active_top3 必须有可用且已核验的数据与非否定证据状态"
                )
            if claim_type == "predictive" and oos_status not in {
                "beats_baseline",
                "skill_supported",
            }:
                raise ContractError(
                    f"{label}: out-of-sample 状态 {oos_status} 不得进入 active_top3"
                )
            if claim_type == "predictive" and lifecycle["forecast_receipt_ref"] is None:
                raise ContractError(
                    f"{label}: active_top3 预测主张必须绑定 forecast receipt"
                )

        # Two objective release gates: traceable evidence and claim-specific
        # validation.  The model still judges novelty, value, and discrimination.
        if support_level == "high":
            if not support_evidence or not sources_verified:
                raise ContractError(
                    f"{label}: 高科学支持度必须有已核验支持证据和已核验数据来源"
                )
            if evidence_status != "supported":
                raise ContractError(
                    f"{label}: 当前证据状态为 {evidence_status}，科学支持度不得为 high"
                )
            if claim_type == "predictive" and oos_status != "beats_baseline":
                raise ContractError(
                    f"{label}: 预测主张未胜过基线，科学支持度不得为 high"
                )
            if crosses_null is True:
                raise ContractError(f"{label}: 区间跨越零效应，科学支持度不得为 high")

        ranked_ids.append(hypothesis_id)
        ranked.append(
            {
                "hypothesis_id": hypothesis_id,
                "support_rank": _rank(row["support_rank"], f"{label}.support_rank"),
                "research_priority_rank": _rank(
                    row["research_priority_rank"],
                    f"{label}.research_priority_rank",
                ),
                "claim_type": claim_type,
                "current_evidence_status": evidence_status,
                "scientific_support": support_row,
                "research_priority": priority_row,
                "data_sources_verified": sources_verified,
                "support_evidence": support_evidence,
                "opposing_evidence": opposing_evidence,
                "independent_support_group_count": len(
                    {item["dependency_group_id"] for item in support_evidence}
                ),
                "out_of_sample_validation": out_of_sample_row,
                "effect_uncertainty": effect_row,
                "sensitivity": sensitivity_row,
                "falsifiability": falsifiability_row,
                "key_limitations": _text_list(
                    row["key_limitations"], f"{label}.key_limitations", min_items=1
                ),
                "strongest_null_hypothesis": _text(
                    row["strongest_null_hypothesis"],
                    f"{label}.strongest_null_hypothesis",
                    max_length=2_000,
                ),
                "next_experiment": next_experiment_row,
                "ranking_rationale": _text(
                    row["ranking_rationale"],
                    f"{label}.ranking_rationale",
                    max_length=2_000,
                ),
                "release_boundary": _text(
                    row["release_boundary"],
                    f"{label}.release_boundary",
                    max_length=2_000,
                ),
                **lifecycle,
            }
        )

    if set(ranked_ids) != hypothesis_set or len(ranked_ids) != len(hypothesis_set):
        raise ContractError("ranked_hypotheses 必须恰好覆盖全部规范化假设")
    expected_ranks = list(range(1, len(ranked) + 1))
    if sorted(row["support_rank"] for row in ranked) != expected_ranks:
        raise ContractError("support_rank 必须是从 1 开始的连续名次")
    if sorted(row["research_priority_rank"] for row in ranked) != expected_ranks:
        raise ContractError("research_priority_rank 必须是从 1 开始的连续名次")
    active = [row for row in ranked if row["portfolio_status"] == "active_top3"]
    if len(active) > 3:
        raise ContractError("active_top3 最多三个假设")
    active_roles = [row["portfolio_role"] for row in active]
    if len(active_roles) != len(set(active_roles)):
        raise ContractError("active_top3 的 portfolio_role 必须互不重复")

    selected = _object(
        request["selected_next_experiment"],
        "portfolio ranking.selected_next_experiment",
    )
    _exact_fields(
        selected,
        {
            "hypothesis_ids",
            "objective",
            "discriminating_power",
            "feasibility",
            "rationale",
        },
        "portfolio ranking.selected_next_experiment",
    )
    selected_ids = [
        _id(item, f"portfolio ranking.selected_next_experiment.hypothesis_ids[{index}]")
        for index, item in enumerate(
            _array(
                selected["hypothesis_ids"],
                "portfolio ranking.selected_next_experiment.hypothesis_ids",
                min_items=1,
                max_items=30,
            )
        )
    ]
    if not set(selected_ids) <= hypothesis_set:
        raise ContractError("selected_next_experiment 引用了未定义假设")

    lifecycle_partitions = {
        status: [
            row["hypothesis_id"]
            for row in ranked
            if row["portfolio_status"] == status
        ]
        for status in PORTFOLIO_STATUSES
    }
    return {
        "schema_version": PORTFOLIO_RANKING_VERSION,
        "source_runs": source_runs,
        "hypothesis_groups": groups,
        "ranked_hypotheses": ranked,
        "lifecycle_partitions": lifecycle_partitions,
        "selected_next_experiment": {
            "hypothesis_ids": selected_ids,
            "objective": _text(
                selected["objective"],
                "portfolio ranking.selected_next_experiment.objective",
                max_length=2_000,
            ),
            "discriminating_power": _text(
                selected["discriminating_power"],
                "portfolio ranking.selected_next_experiment.discriminating_power",
                max_length=2_000,
            ),
            "feasibility": _enum(
                selected["feasibility"],
                FEASIBILITY_STATUSES,
                "portfolio ranking.selected_next_experiment.feasibility",
            ),
            "rationale": _text(
                selected["rationale"],
                "portfolio ranking.selected_next_experiment.rationale",
                max_length=2_000,
            ),
        },
    }


__all__ = [
    "GRADE_LABELS",
    "GRADE_STRENGTH",
    "PORTFOLIO_RANKING_VERSION",
    "RANKING_VERSION",
    "RUBRIC_DIMENSIONS",
    "RUBRIC_KEYS",
    "check_ranking_consistency",
    "compute_dimension_scores",
    "validate_portfolio_ranking",
    "validate_ranking_request",
]
