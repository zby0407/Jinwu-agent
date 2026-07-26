"""科学假设 Agent 1.0 的封闭合同验证。只使用 Python 标准库。"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

REQUEST_VERSION = "scientific-hypothesis-request-v1"
RESPONSE_VERSION = "scientific-hypothesis-response-v1"
PORTFOLIO_VERSION = "scientific-hypothesis-portfolio-v1"
OUTCOME_VERSION = "scientific-hypothesis-outcome-v1"

RESPONSE_KINDS = {"hypotheses_ready", "clarification_needed", "hypothesis_blocked"}
MATERIAL_KINDS = {
    "research_plan",
    "experiment_result",
    "data_feature",
    "literature_note",
    "user_material",
}
EVIDENCE_KINDS = {"experiment", "literature", "upstream", "user"}
EVIDENCE_ROLES = {"supports", "opposes", "limits", "gap"}
CONFIDENCE_LEVELS = {"high", "medium", "low"}
EXPERIMENT_OUTCOMES = {"completed", "null_result", "uncertain", "technical_failure"}
BLOCKER_CODES = {
    "unsupported_scope",
    "missing_indispensable_evidence",
    "unresearchable_formulation",
    "safety_boundary",
}

SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
NOVELTY_CLAIM = re.compile(r"首次提出|首次发现|国际上首次|国内外首创|未见任何报道")
PRECISE_PROBABILITY = re.compile(
    r"(?:置信度|可信概率|成立概率|为真的概率|把握)\s*[:：为约]?\s*\d+(?:\.\d+)?\s*%"
    r"|\b\d+(?:\.\d+)?\s*%\s*(?:置信|概率|把握)"
)
# ``confidence.basis`` is deliberately qualitative.  A narrower historical
# rule only caught phrases such as “置信度 73%”, so an agent could smuggle the
# same false precision through adjacent quantities (“理论功效 75–80%”,
# “非检出概率 20–25%”) despite having no calculation or evidence.  Reject any
# percentage, including ranges, in this field; quantitative study results
# belong in evidence/predictions with their own provenance, not in the
# confidence rationale.
PERCENTAGE_EXPRESSION = re.compile(
    r"\d+(?:\.\d+)?\s*(?:[-–—~至到]\s*\d+(?:\.\d+)?\s*)?%"
)
# 没有来源支撑的硬数值门槛（用于检验陈述、证伪条件、预测文本）。
HARD_NUMERIC_CUTOFF = re.compile(
    r"(?:至少|至多|不少于|不多于|不超过|不低于|不高于|超过|高于|低于|大于|小于|达到)"
    r"[^。；;.!?！？]{0,24}\d+(?:\.\d+)?"
    r"|\d+(?:\.\d+)?\s*(?:%|个|倍|年|月|天|小时|σ|sigma)\s*(?:及以上|及以下|以上|以下|之内|以内)"
)
# 候选主张或机制中的定量归因也必须可追溯。刻意不把“周”作为单位，
# 避免把“第24活动周”这类编号误判为数值效应。
QUANTITATIVE_EXPRESSION = re.compile(
    r"(?:约|大约|近|~|≈)?\s*\d+(?:\.\d+)?"
    r"(?:\s*[-–—~至到]\s*\d+(?:\.\d+)?)?\s*"
    r"(?:个月|小时|sigma|%|个|倍|年|月|天|σ)"
)

# 数据覆盖范围约束（写死的核验规则，与 upstream.KNOWN_DATA_COVERAGES 对应）：
# 材料只覆盖有限范围时，候选的可泛化表述一旦越界，置信度不得为 high。
DATA_COVERAGE_RULES: tuple[dict[str, Any], ...] = (
    {
        "product": "JW-FD 磁图数据集",
        "material_pattern": re.compile(r"JW-FD|JW_FD", re.IGNORECASE),
        "scope_pattern": re.compile(r"跨周期|所有活动周|普遍成立|任意活动周|每个活动周"),
        "coverage": "仅覆盖 2011 年前后个别活动区（AR 系列）的短时磁图观测",
    },
)


class ContractError(ValueError):
    """在任何结果落盘前返回的合同错误。"""


def canonical_json_sha256(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ContractError("内容必须只包含有限 JSON 值") from exc
    return hashlib.sha256(encoded).hexdigest()


def clone_json(value: object, label: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} 必须只包含有限 JSON 值") from exc


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} 必须是一个 JSON 对象")
    return value


def _array(value: object, label: str, *, min_items: int = 0, max_items: int = 10**6) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{label} 必须是 JSON 数组")
    if len(value) < min_items:
        raise ContractError(f"{label} 至少需要 {min_items} 项")
    if len(value) > max_items:
        raise ContractError(f"{label} 最多允许 {max_items} 项")
    return value


def _text(
    value: object,
    label: str,
    *,
    min_length: int = 1,
    max_length: int = 4_000,
) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{label} 必须是字符串")
    normalized = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    if len(normalized) < min_length:
        raise ContractError(f"{label} 至少需要 {min_length} 个字符")
    if len(normalized) > max_length:
        raise ContractError(f"{label} 不能超过 {max_length} 个字符")
    return normalized


def _exact_fields(value: dict[str, Any], required: set[str], label: str) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required)
    if missing:
        raise ContractError(f"{label} 缺少字段：{', '.join(missing)}")
    if unknown:
        raise ContractError(f"{label} 存在未定义字段：{', '.join(unknown)}")


def _enum(value: object, allowed: set[str], label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ContractError(f"{label} 只允许：{'、'.join(sorted(allowed))}")
    return value


def _id(value: object, label: str) -> str:
    text = _text(value, label, max_length=64)
    if SAFE_ID.match(text) is None:
        raise ContractError(f"{label} 必须是以字母开头、只含字母数字下划线或连字符的短 id")
    return text


def _string_list(
    value: object,
    label: str,
    *,
    min_items: int = 0,
    max_items: int = 30,
    item_max_length: int = 1_000,
) -> list[str]:
    items = _array(value, label, min_items=min_items, max_items=max_items)
    return [_text(item, f"{label}[{index}]", max_length=item_max_length) for index, item in enumerate(items)]


def _unique_strings(values: list[str], label: str) -> list[str]:
    if len(values) != len(set(values)):
        raise ContractError(f"{label} 中的条目必须互不相同")
    return values


def _sha256_optional(value: object, label: str) -> str | None:
    if value is None:
        return None
    text = _text(value, label, max_length=64)
    if re.fullmatch(r"[0-9a-f]{64}", text) is None:
        raise ContractError(f"{label} 必须是 64 位小写 sha256")
    return text


# ---------------------------------------------------------------------------
# 请求合同
# ---------------------------------------------------------------------------

def validate_hypothesis_request(payload: object) -> dict[str, Any]:
    request = _object(payload, "hypothesis request")
    _exact_fields(
        request,
        {
            "schema_version",
            "task_name",
            "research_question",
            "upstream_materials",
            "prior_hypotheses",
            "max_candidates",
        },
        "hypothesis request",
    )
    if request["schema_version"] != REQUEST_VERSION:
        raise ContractError(f"schema_version 必须为 {REQUEST_VERSION}")
    validated = {
        "schema_version": REQUEST_VERSION,
        "task_name": _text(request["task_name"], "task_name", max_length=200),
        "research_question": _text(
            request["research_question"], "research_question", min_length=8, max_length=4_000
        ),
        "upstream_materials": [],
        "prior_hypotheses": [],
        "max_candidates": request["max_candidates"],
    }
    max_candidates = validated["max_candidates"]
    if (
        not isinstance(max_candidates, int)
        or isinstance(max_candidates, bool)
        or not 1 <= max_candidates <= 8
    ):
        raise ContractError("max_candidates 必须是 1 到 8 之间的整数")

    materials = _array(
        request["upstream_materials"], "upstream_materials", max_items=12
    )
    for index, item in enumerate(materials):
        label = f"upstream_materials[{index}]"
        material = _object(item, label)
        _exact_fields(
            material,
            {"id", "material_kind", "title", "locator", "content_notes", "experiment_summary"},
            label,
        )
        kind = _enum(material["material_kind"], MATERIAL_KINDS, f"{label}.material_kind")
        row: dict[str, Any] = {
            "id": _id(material["id"], f"{label}.id"),
            "material_kind": kind,
            "title": _text(material["title"], f"{label}.title", max_length=300),
            "locator": _text(material["locator"], f"{label}.locator", max_length=1_000),
            "content_notes": _text(
                material["content_notes"], f"{label}.content_notes", max_length=8_000
            ),
            "experiment_summary": None,
        }
        summary = material["experiment_summary"]
        if summary is not None:
            slabel = f"{label}.experiment_summary"
            summary = _object(summary, slabel)
            _exact_fields(
                summary,
                {
                    "execution_completed",
                    "outcome",
                    "metrics",
                    "uncertainty_notes",
                    "record_sha256",
                },
                slabel,
            )
            if not isinstance(summary["execution_completed"], bool):
                raise ContractError(f"{slabel}.execution_completed 必须是布尔值")
            metrics = _array(summary["metrics"], f"{slabel}.metrics", max_items=40)
            metric_rows = []
            for m_index, metric in enumerate(metrics):
                mlabel = f"{slabel}.metrics[{m_index}]"
                metric = _object(metric, mlabel)
                _exact_fields(metric, {"name", "value_text", "definition"}, mlabel)
                metric_rows.append(
                    {
                        "name": _text(metric["name"], f"{mlabel}.name", max_length=200),
                        "value_text": _text(
                            metric["value_text"], f"{mlabel}.value_text", max_length=500
                        ),
                        "definition": _text(
                            metric["definition"], f"{mlabel}.definition", max_length=500
                        ),
                    }
                )
            outcome = _enum(summary["outcome"], EXPERIMENT_OUTCOMES, f"{slabel}.outcome")
            if kind != "experiment_result":
                raise ContractError(f"{slabel} 只允许出现在 experiment_result 材料上")
            if not summary["execution_completed"] and outcome != "technical_failure":
                raise ContractError(
                    f"{slabel}.outcome 在未完成执行时只能是 technical_failure"
                )
            if summary["execution_completed"] and outcome == "technical_failure":
                raise ContractError(
                    f"{slabel} 已完成执行的结果不能标记为 technical_failure"
                )
            if outcome == "technical_failure" and metric_rows:
                raise ContractError(f"{slabel} 技术失败记录不得携带指标")
            if summary["execution_completed"] and not metric_rows:
                raise ContractError(f"{slabel} 已完成执行的结果必须携带至少一项原始指标")
            row["experiment_summary"] = {
                "execution_completed": summary["execution_completed"],
                "outcome": outcome,
                "metrics": metric_rows,
                "uncertainty_notes": _text(
                    summary["uncertainty_notes"],
                    f"{slabel}.uncertainty_notes",
                    max_length=2_000,
                ),
                "record_sha256": _sha256_optional(
                    summary["record_sha256"], f"{slabel}.record_sha256"
                ),
            }
        elif kind == "experiment_result":
            raise ContractError(f"{label} 为 experiment_result 时必须提供 experiment_summary")
        validated["upstream_materials"].append(row)
    _unique_strings(
        [material["id"] for material in validated["upstream_materials"]],
        "upstream_materials.id",
    )

    priors = _array(request["prior_hypotheses"], "prior_hypotheses", max_items=12)
    for index, item in enumerate(priors):
        label = f"prior_hypotheses[{index}]"
        prior = _object(item, label)
        _exact_fields(prior, {"id", "statement", "version", "notes"}, label)
        version = prior["version"]
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ContractError(f"{label}.version 必须是不小于 1 的整数")
        validated["prior_hypotheses"].append(
            {
                "id": _id(prior["id"], f"{label}.id"),
                "statement": _text(prior["statement"], f"{label}.statement", max_length=1_000),
                "version": version,
                "notes": _text(prior["notes"], f"{label}.notes", max_length=2_000),
            }
        )
    _unique_strings(
        [prior["id"] for prior in validated["prior_hypotheses"]], "prior_hypotheses.id"
    )
    return validated


# ---------------------------------------------------------------------------
# 响应合同
# ---------------------------------------------------------------------------

def _validate_confidence(value: object, label: str) -> dict[str, Any]:
    confidence = _object(value, label)
    _exact_fields(confidence, {"level", "basis"}, label)
    level = _enum(confidence["level"], CONFIDENCE_LEVELS, f"{label}.level")
    basis = _text(confidence["basis"], f"{label}.basis", max_length=1_000)
    if PERCENTAGE_EXPRESSION.search(basis) is not None:
        raise ContractError(
            f"{label}.basis 不得使用百分比或百分比区间表达把握、功效或概率，"
            "应说明可追溯的定性理由"
        )
    return {"level": level, "basis": basis}


def _validate_prediction(value: object, label: str) -> dict[str, Any]:
    prediction = _object(value, label)
    _exact_fields(
        prediction,
        {"id", "statement", "observable", "distinguishes_from", "would_weaken_if"},
        label,
    )
    distinguishes = _string_list(
        prediction["distinguishes_from"],
        f"{label}.distinguishes_from",
        min_items=1,
        max_items=8,
    )
    return {
        "id": _id(prediction["id"], f"{label}.id"),
        "statement": _text(prediction["statement"], f"{label}.statement", max_length=1_000),
        "observable": _text(prediction["observable"], f"{label}.observable", max_length=1_000),
        "distinguishes_from": distinguishes,
        "would_weaken_if": _text(
            prediction["would_weaken_if"], f"{label}.would_weaken_if", max_length=1_000
        ),
    }


def _validate_evidence_link(value: object, label: str) -> dict[str, Any]:
    link = _object(value, label)
    _exact_fields(link, {"evidence_id", "relation_note"}, label)
    return {
        "evidence_id": _id(link["evidence_id"], f"{label}.evidence_id"),
        "relation_note": _text(link["relation_note"], f"{label}.relation_note", max_length=1_000),
    }


def _validate_candidate(
    value: object,
    label: str,
    prior_versions: dict[str, int],
) -> dict[str, Any]:
    candidate = _object(value, label)
    _exact_fields(
        candidate,
        {
            "id",
            "statement",
            "applicability",
            "mechanism",
            "assumptions",
            "predictions",
            "supporting_evidence",
            "opposing_evidence",
            "evidence_gaps",
            "alternative_explanations",
            "confounders",
            "falsification_conditions",
            "next_test",
            "confidence",
            "evidence_update",
            "prior_version_id",
        },
        label,
    )
    candidate_id = _id(candidate["id"], f"{label}.id")
    prior_version_id = candidate["prior_version_id"]
    if prior_version_id is not None:
        prior_version_id = _id(prior_version_id, f"{label}.prior_version_id")
        if prior_version_id not in prior_versions:
            raise ContractError(
                f"{label}.prior_version_id 未指向请求中任何已有假设：{prior_version_id}"
            )

    mechanism = _object(candidate["mechanism"], f"{label}.mechanism")
    _exact_fields(
        mechanism, {"summary", "physical_basis", "required_premises"}, f"{label}.mechanism"
    )
    mechanism_row = {
        "summary": _text(mechanism["summary"], f"{label}.mechanism.summary", max_length=2_000),
        "physical_basis": _text(
            mechanism["physical_basis"], f"{label}.mechanism.physical_basis", max_length=2_000
        ),
        "required_premises": _string_list(
            mechanism["required_premises"],
            f"{label}.mechanism.required_premises",
            min_items=1,
            max_items=8,
        ),
    }

    predictions = [
        _validate_prediction(item, f"{label}.predictions[{index}]")
        for index, item in enumerate(
            _array(candidate["predictions"], f"{label}.predictions", min_items=1, max_items=6)
        )
    ]
    _unique_strings([p["id"] for p in predictions], f"{label}.predictions.id")

    next_test = _object(candidate["next_test"], f"{label}.next_test")
    _exact_fields(
        next_test,
        {"objective", "discriminating_power", "expected_signals", "candidate_ids_distinguished"},
        f"{label}.next_test",
    )
    next_test_row = {
        "objective": _text(next_test["objective"], f"{label}.next_test.objective", max_length=1_000),
        "discriminating_power": _text(
            next_test["discriminating_power"],
            f"{label}.next_test.discriminating_power",
            max_length=1_000,
        ),
        "expected_signals": _string_list(
            next_test["expected_signals"],
            f"{label}.next_test.expected_signals",
            min_items=1,
            max_items=8,
        ),
        "candidate_ids_distinguished": [
            _id(item, f"{label}.next_test.candidate_ids_distinguished[{i}]")
            for i, item in enumerate(
                _array(
                    next_test["candidate_ids_distinguished"],
                    f"{label}.next_test.candidate_ids_distinguished",
                    min_items=1,
                    max_items=8,
                )
            )
        ],
    }

    update = candidate["evidence_update"]
    update_row = None
    if update is not None:
        ulabel = f"{label}.evidence_update"
        update = _object(update, ulabel)
        _exact_fields(update, {"summary", "reason"}, ulabel)
        update_row = {
            "summary": _text(update["summary"], f"{ulabel}.summary", max_length=1_000),
            "reason": _text(update["reason"], f"{ulabel}.reason", max_length=1_000),
        }

    return {
        "id": candidate_id,
        "statement": _text(candidate["statement"], f"{label}.statement", max_length=1_000),
        "applicability": _text(
            candidate["applicability"], f"{label}.applicability", max_length=1_000
        ),
        "mechanism": mechanism_row,
        "assumptions": _string_list(
            candidate["assumptions"], f"{label}.assumptions", min_items=1, max_items=8
        ),
        "predictions": predictions,
        "supporting_evidence": [
            _validate_evidence_link(item, f"{label}.supporting_evidence[{index}]")
            for index, item in enumerate(
                _array(candidate["supporting_evidence"], f"{label}.supporting_evidence", max_items=12)
            )
        ],
        "opposing_evidence": [
            _validate_evidence_link(item, f"{label}.opposing_evidence[{index}]")
            for index, item in enumerate(
                _array(candidate["opposing_evidence"], f"{label}.opposing_evidence", max_items=12)
            )
        ],
        "evidence_gaps": _string_list(
            candidate["evidence_gaps"], f"{label}.evidence_gaps", max_items=10
        ),
        "alternative_explanations": _string_list(
            candidate["alternative_explanations"],
            f"{label}.alternative_explanations",
            min_items=1,
            max_items=8,
        ),
        "confounders": _string_list(
            candidate["confounders"], f"{label}.confounders", max_items=10
        ),
        "falsification_conditions": _string_list(
            candidate["falsification_conditions"],
            f"{label}.falsification_conditions",
            min_items=1,
            max_items=8,
        ),
        "next_test": next_test_row,
        "confidence": _validate_confidence(candidate["confidence"], f"{label}.confidence"),
        "evidence_update": update_row,
        "prior_version_id": prior_version_id,
    }


def validate_hypothesis_response(
    payload: object, request: dict[str, Any], register: Any = None
) -> dict[str, Any]:
    response = _object(payload, "hypothesis response")
    kind = response.get("response_kind")
    if kind not in RESPONSE_KINDS:
        raise ContractError(
            "response_kind 必须为 hypotheses_ready、clarification_needed 或 hypothesis_blocked"
        )
    base_fields = {"schema_version", "task_name", "research_question", "response_kind"}
    kind_fields = {
        "hypotheses_ready": {"candidates", "pairwise_distinctions", "portfolio_notes"},
        "clarification_needed": {"questions"},
        "hypothesis_blocked": {"blockers"},
    }[kind]
    _exact_fields(response, base_fields | kind_fields, "hypothesis response")
    if response["schema_version"] != RESPONSE_VERSION:
        raise ContractError(f"schema_version 必须为 {RESPONSE_VERSION}")
    if response["task_name"] != request["task_name"]:
        raise ContractError("task_name 必须与已绑定请求逐字一致")
    if response["research_question"] != request["research_question"]:
        raise ContractError("research_question 必须与已绑定请求逐字一致")

    material_index = {m["id"]: m for m in request["upstream_materials"]}

    validated: dict[str, Any] = {
        "schema_version": RESPONSE_VERSION,
        "task_name": request["task_name"],
        "research_question": request["research_question"],
        "response_kind": kind,
    }

    if kind == "clarification_needed":
        questions = _array(response["questions"], "questions", min_items=1, max_items=3)
        rows = []
        for index, item in enumerate(questions):
            label = f"questions[{index}]"
            item = _object(item, label)
            _exact_fields(item, {"id", "question", "why_it_matters", "expected_answer"}, label)
            rows.append(
                {
                    "id": _id(item["id"], f"{label}.id"),
                    "question": _text(item["question"], f"{label}.question", max_length=1_000),
                    "why_it_matters": _text(
                        item["why_it_matters"], f"{label}.why_it_matters", max_length=1_000
                    ),
                    "expected_answer": _text(
                        item["expected_answer"], f"{label}.expected_answer", max_length=1_000
                    ),
                }
            )
        _unique_strings([row["id"] for row in rows], "questions.id")
        validated["questions"] = rows
        return validated

    if kind == "hypothesis_blocked":
        blockers = _array(response["blockers"], "blockers", min_items=1, max_items=6)
        rows = []
        for index, item in enumerate(blockers):
            label = f"blockers[{index}]"
            item = _object(item, label)
            _exact_fields(item, {"id", "code", "reason", "recoverable", "resolution"}, label)
            if not isinstance(item["recoverable"], bool):
                raise ContractError(f"{label}.recoverable 必须是布尔值")
            rows.append(
                {
                    "id": _id(item["id"], f"{label}.id"),
                    "code": _enum(item["code"], BLOCKER_CODES, f"{label}.code"),
                    "reason": _text(item["reason"], f"{label}.reason", max_length=1_000),
                    "recoverable": item["recoverable"],
                    "resolution": _text(
                        item["resolution"], f"{label}.resolution", max_length=1_000
                    ),
                }
            )
        _unique_strings([row["id"] for row in rows], "blockers.id")
        validated["blockers"] = rows
        return validated

    prior_versions = {
        prior["id"]: prior["version"] for prior in request["prior_hypotheses"]
    }
    candidates = [
        _validate_candidate(item, f"candidates[{index}]", prior_versions)
        for index, item in enumerate(
            _array(
                response["candidates"],
                "candidates",
                min_items=1,
                max_items=request["max_candidates"],
            )
        )
    ]
    _unique_strings([c["id"] for c in candidates], "candidates.id")
    candidate_ids = {c["id"] for c in candidates}

    for candidate in candidates:
        label = f"candidate {candidate['id']}"
        if not candidate["supporting_evidence"] and not candidate["evidence_gaps"]:
            raise ContractError(
                f"{label} 既无支持证据也未说明证据缺口；证据不足时必须诚实标注缺口"
            )
        for prediction in candidate["predictions"]:
            for target in prediction["distinguishes_from"]:
                if target not in candidate_ids and target not in prior_versions:
                    raise ContractError(
                        f"{label} 的预测 {prediction['id']} 的 distinguishes_from "
                        f"未指向任何候选或已有假设：{target}"
                    )
        for distinguished in candidate["next_test"]["candidate_ids_distinguished"]:
            if distinguished not in candidate_ids:
                raise ContractError(
                    f"{label} 的下一项检验引用了不存在的候选：{distinguished}"
                )
        if len(candidate["next_test"]["candidate_ids_distinguished"]) > 1:
            if candidate["id"] not in candidate["next_test"]["candidate_ids_distinguished"]:
                raise ContractError(
                    f"{label} 的下一项检验声称区分多个候选时，必须包含该候选自身"
                )
        if candidate["prior_version_id"] is not None and candidate["evidence_update"] is None:
            raise ContractError(
                f"{label} 声明更新已有假设时必须填写 evidence_update 说明更新原因"
                )
        # 数据覆盖范围核验规则：候选陈述或适用范围的泛化越出所绑定材料的
        # 已知覆盖范围时，置信度不得为 high（写死规则，不靠模型自觉）。
        if candidate["confidence"]["level"] == "high":
            candidate_scope = candidate["statement"] + " " + candidate["applicability"]
            supporting_material_ids: set[str] = set()
            for link in candidate["supporting_evidence"]:
                # evidence_id 与 material_id 是不同命名空间：先按 evidence_id 在
                # 登记簿精确查其 material_id，再按 id 重合约定回退（handoff 常用同一短名）。
                entry = register.get(link["evidence_id"]) if register is not None else None
                material_id = (
                    entry["material_id"] if entry is not None else link["evidence_id"]
                )
                if material_id in material_index:
                    supporting_material_ids.add(material_id)
            for rule in DATA_COVERAGE_RULES:
                if rule["scope_pattern"].search(candidate_scope) is None:
                    continue
                for material_id in supporting_material_ids:
                    material = material_index[material_id]
                    material_corpus = (
                        material["title"] + " " + material["content_notes"]
                    )
                    if rule["material_pattern"].search(material_corpus):
                        raise ContractError(
                            f"{label} 的可泛化表述超出 {rule['product']} 的覆盖范围"
                            f"（{rule['coverage']}），置信度不得为 high；"
                            "请收窄适用范围或降为 medium/low 并说明理由"
                        )

    distinctions = _array(
        response["pairwise_distinctions"],
        "pairwise_distinctions",
        max_items=28,
    )
    distinction_rows = []
    for index, item in enumerate(distinctions):
        label = f"pairwise_distinctions[{index}]"
        item = _object(item, label)
        _exact_fields(item, {"left_id", "right_id", "distinction"}, label)
        left = _id(item["left_id"], f"{label}.left_id")
        right = _id(item["right_id"], f"{label}.right_id")
        if left == right:
            raise ContractError(f"{label} 的左右两侧不能是同一候选")
        for side, side_label in ((left, "left_id"), (right, "right_id")):
            if side not in candidate_ids:
                raise ContractError(f"{label}.{side_label} 未指向任何候选：{side}")
        distinction_rows.append(
            {
                "left_id": left,
                "right_id": right,
                "distinction": _text(
                    item["distinction"], f"{label}.distinction", max_length=1_000
                ),
            }
        )
    pair_keys = [
        tuple(sorted((row["left_id"], row["right_id"]))) for row in distinction_rows
    ]
    if len(pair_keys) != len(set(pair_keys)):
        raise ContractError("pairwise_distinctions 中同一对候选只允许出现一次")

    if len(candidates) > 1:
        # 每个候选至少与另一候选有一条区分说明。
        covered = {side for key in pair_keys for side in key}
        uncovered = sorted(candidate_ids - covered)
        if uncovered:
            raise ContractError(
                "存在多个候选时，每个候选都必须在 pairwise_distinctions 中至少出现一次；"
                f"未覆盖：{', '.join(uncovered)}"
            )

    notes = response["portfolio_notes"]
    if notes is not None:
        notes = _text(notes, "portfolio_notes", max_length=2_000)
    validated["candidates"] = candidates
    validated["pairwise_distinctions"] = distinction_rows
    validated["portfolio_notes"] = notes
    return validated


# ---------------------------------------------------------------------------
# 证据绑定与组合合同
# ---------------------------------------------------------------------------

def validate_evidence_bind(payload: object) -> dict[str, Any]:
    bind = _object(payload, "evidence bind")
    _exact_fields(
        bind,
        {"evidence_id", "evidence_kind", "material_id", "excerpt", "verified_support", "role"},
        "evidence bind",
    )
    kind = _enum(bind["evidence_kind"], EVIDENCE_KINDS, "evidence bind.evidence_kind")
    role = _enum(bind["role"], EVIDENCE_ROLES, "evidence bind.role")
    material_id = _id(bind["material_id"], "evidence bind.material_id")
    excerpt = _text(bind["excerpt"], "evidence bind.excerpt", max_length=2_000)
    verified = bind["verified_support"]
    if not isinstance(verified, bool):
        raise ContractError("evidence bind.verified_support 必须是布尔值")
    if role in {"supports", "opposes", "limits"} and not verified:
        raise ContractError(
            "标为支持、反对或限制的证据必须已核对原文确实对应主张；"
            "未核对时请使用 role=gap 并把内容列为证据缺口"
        )
    return {
        "evidence_id": _id(bind["evidence_id"], "evidence bind.evidence_id"),
        "evidence_kind": kind,
        "material_id": material_id,
        "excerpt": excerpt,
        "verified_support": verified,
        "role": role,
    }


def validate_hypothesis_portfolio(payload: object) -> dict[str, Any]:
    portfolio = _object(payload, "hypothesis portfolio")
    _exact_fields(
        portfolio,
        {
            "schema_version",
            "portfolio_id",
            "created_at",
            "status",
            "request_sha256",
            "research_question",
            "candidates",
            "pairwise_distinctions",
            "evidence_register",
            "ranking",
            "counterexample_table",
            "portfolio_notes",
            "portfolio_sha256",
        },
        "hypothesis portfolio",
    )
    if portfolio["schema_version"] != PORTFOLIO_VERSION:
        raise ContractError(f"schema_version 必须为 {PORTFOLIO_VERSION}")
    if portfolio["status"] != "frozen":
        raise ContractError("hypothesis portfolio 的 status 必须为 frozen")
    for field in ("request_sha256", "portfolio_sha256"):
        if re.fullmatch(r"[0-9a-f]{64}", str(portfolio[field])) is None:
            raise ContractError(f"{field} 必须是 64 位小写 sha256")
    if not portfolio["candidates"]:
        raise ContractError("hypothesis portfolio 至少包含一个候选假设")
    candidate_ids = {c.get("id") for c in portfolio["candidates"] if isinstance(c, dict)}

    ranking = portfolio["ranking"]
    if ranking is not None:
        rlabel = "hypothesis portfolio.ranking"
        ranking = _object(ranking, rlabel)
        _exact_fields(ranking, {"schema_version", "weights", "ranked", "pairwise_judgments"}, rlabel)
        if ranking["schema_version"] != "scientific-hypothesis-ranking-v1":
            raise ContractError(f"{rlabel}.schema_version 必须为 scientific-hypothesis-ranking-v1")
        ranked_ids = []
        for index, item in enumerate(_array(ranking["ranked"], f"{rlabel}.ranked", min_items=1)):
            row = _object(item, f"{rlabel}.ranked[{index}]")
            for field in ("candidate_id", "rank", "rationale", "key_evidence_ids"):
                if field not in row:
                    raise ContractError(f"{rlabel}.ranked[{index}] 缺少字段：{field}")
            if not row["rationale"]:
                raise ContractError(
                    f"{rlabel}.ranked[{index}] 的名次必须附可追溯理由，不允许只输出排名序号"
                )
            ranked_ids.append(row["candidate_id"])
        if set(ranked_ids) != candidate_ids:
            raise ContractError(f"{rlabel}.ranked 必须覆盖全部候选")

    table = portfolio["counterexample_table"]
    if table is not None:
        tlabel = "hypothesis portfolio.counterexample_table"
        table = _object(table, tlabel)
        _exact_fields(table, {"rows", "notes"}, tlabel)
        for index, item in enumerate(_array(table["rows"], f"{tlabel}.rows")):
            row = _object(item, f"{tlabel}.rows[{index}]")
            _exact_fields(row, {"candidate_id", "kind", "summary", "evidence_id"}, f"{tlabel}.rows[{index}]")
            if row["kind"] not in {"counterexample", "conflict"}:
                raise ContractError(f"{tlabel}.rows[{index}].kind 只允许 counterexample 或 conflict")
            if row["candidate_id"] is not None and row["candidate_id"] not in candidate_ids:
                raise ContractError(
                    f"{tlabel}.rows[{index}].candidate_id 未指向任何候选：{row['candidate_id']}"
                )
    return portfolio


__all__ = [
    "BLOCKER_CODES",
    "CONFIDENCE_LEVELS",
    "DATA_COVERAGE_RULES",
    "EVIDENCE_KINDS",
    "EVIDENCE_ROLES",
    "EXPERIMENT_OUTCOMES",
    "HARD_NUMERIC_CUTOFF",
    "MATERIAL_KINDS",
    "NOVELTY_CLAIM",
    "OUTCOME_VERSION",
    "PERCENTAGE_EXPRESSION",
    "PORTFOLIO_VERSION",
    "PRECISE_PROBABILITY",
    "QUANTITATIVE_EXPRESSION",
    "REQUEST_VERSION",
    "RESPONSE_KINDS",
    "RESPONSE_VERSION",
    "ContractError",
    "canonical_json_sha256",
    "clone_json",
    "validate_evidence_bind",
    "validate_hypothesis_portfolio",
    "validate_hypothesis_request",
    "validate_hypothesis_response",
]
