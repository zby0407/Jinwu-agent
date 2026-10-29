"""Dependency-light contracts for high-quality research review packages.

These sidecars do not replace ReviewVerdictV2.  They make evidence quality,
method validity, and novelty uncertainty explicit without turning model review
into publication-priority adjudication.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from research_review.contracts import (
    REVIEW_MODES,
    ContractError,
    _artifact_ref,
    _enum,
    _exact,
    _id,
    _integer,
    _object,
    _text,
    _text_list,
    _timestamp,
)

ANALYSIS_CLAIM_VERSION = "analysis-claim-contract-v1"
SCIENTIFIC_QUALITY_VERSION = "scientific-quality-assessment-v1"

CLAIM_COMPONENTS = {
    "statement",
    "mechanism",
    "prediction",
    "scope",
    "numeric_result",
    "conclusion",
    "workflow_status",
}
EVIDENCE_ROLES = {"supports", "opposes", "limits", "gap"}
SOURCE_CLASSES = {
    "direct_observation",
    "real_experiment",
    "simulation",
    "method_paper",
    "review",
    "data_documentation",
    "user_premise",
    "wiki_context",
    "unknown",
}
EVIDENCE_SCOPES = {
    "full_text",
    "abstract_only",
    "dataset_record",
    "experiment_record",
    "user_statement",
    "wiki_entry",
    "unknown",
}
DIRECTNESS = {"direct", "indirect", "context_only", "not_assessable"}
SCOPE_MATCH = {"matched", "partial", "mismatch", "not_assessable"}
ENTAILMENT = {"entailed", "partial", "not_entailed", "not_assessable"}
QUALITY_CAPS = {"exploratory", "evidence_constrained", "release_candidate"}
METHOD_STATUSES = {"valid", "limited", "invalid", "not_applicable", "not_assessed"}
NOVELTY_STATUSES = {
    "known_baseline",
    "incremental_extension",
    "potentially_novel",
    "novelty_not_assessed",
    "not_applicable",
}
CONTRIBUTION_TYPES = {
    "known_baseline",
    "mechanism_extension",
    "new_prediction",
    "new_data_linkage",
    "new_method_application",
    "measurement_or_null_explanation",
    "not_assessed",
}
QUALITY_STATUSES = {
    "release_candidate",
    "evidence_constrained",
    "exploratory",
    "blocked",
    "workflow_status",
}


def _nullable_text(value: object, label: str, *, maximum: int = 4_000) -> str | None:
    if value is None:
        return None
    return _text(value, label, maximum=maximum)


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError(f"{label} must be a boolean")
    return value


_TRAINING_CORRELATION = r"training\s+correlation|train\s+correlation|训练相关"
_PREDICTIVE_MENTION = r"predict(?:ions?|ive|ing|ed|s)?|forecast(?:ing|ed|s)?|预测|预报"
_NEGATION_REVERSALS = (
    r"不得不|不能不|不只是|不只|不仅|不单|不禁止|未禁止|没有禁止|"
    r"not\s+only|not\s+(?:prohibited|forbidden)|cannot\s+avoid|can't\s+avoid|"
    r"not\s+not|cannot\s+not|can't\s+not|不不|"
    r"(?:并非|并?不是|禁止|不允许|不必|不要|不应|不得|不能|否认|不承认|不认同)"
    r".{0,16}(?:不使用|不采用|不用|不做|不作|不以|不进行|不开展|"
    r"禁止使用|禁止采用|不得使用|不允许使用)|"
    r"(?:并非|并?不是)不$|"
    r"\b(?:false|untrue|incorrect|not true|not correct|deny|denies|denied)\s+that\b"
)


def _validation_clauses(text: str) -> list[str]:
    """Keep explicit local negation separate from a later positive assertion."""
    return re.split(
        r"[，,。；;\n.!?\uff01\uff1f]|\b(?:but|however|instead)\b|但是|然而|但仍",
        text.casefold(),
    )


def _plain_negation_scope(prefix: str) -> bool:
    """Allow only an empty or a small neutral subject before an exclusion."""
    return bool(
        re.fullmatch(
            r"(?:(?:本次|本研究|本方案|本阶段|本轮|本分析|本合同|我们|当前|实际|本方法|该方法)"
            r"的?(?:明确|严格|完全|实际|将|均|都|仅|绝|暂)?|"
            r"明确|严格|完全|绝|实际|"
            r"we|i|this (?:study|work|analysis|method|contract)|the (?:study|method|analysis)|"
            r"our (?:method|analysis)|it is|this is)?",
            prefix.strip(),
        )
    )


def _plain_prefix_exclusion(before: str, pattern: str) -> bool:
    match = re.search(pattern, before)
    if match is None:
        return False
    prefix = before[: match.start()].strip()
    if _plain_negation_scope(prefix):
        return True
    # A direct selection of an OOS measure rather than correlation is also a
    # single-layer exclusion. Arbitrary reported or quoted speech is not.
    return bool(
        re.search(r"rather than|instead of", match.group())
        and re.fullmatch(
            r"(?:out-of-sample|rolling-origin|holdout)(?:\s+(?:errors?|validation|assessment|design)){0,3}",
            prefix,
        )
    )


def _explicitly_negated_mention(
    clause: str, start: int, end: int, *, kind: str
) -> bool:
    """Recognize a small exclusion vocabulary, never infer scientific validity.

    An unrecognized or mixed assertion remains positive for the existing gate.
    Double negation and 'not only' are deliberately not exemptions.
    """
    before, after = clause[:start].rstrip(), clause[end:].lstrip()
    if re.search(_NEGATION_REVERSALS, before[-90:]):
        return False
    cn_prefix = (
        r"(?:不使用|不采用|不用|不做|不作|不进行|不开展|不以|无需|"
        r"禁止(?:使用|采用|用|以)?|不得(?:使用|采用|用|以)?|"
        r"不能(?:使用|采用|用|以)|不允许(?:使用|采用|用|以)|"
        r"避免(?:使用|采用|用))\s*(?:任何|该|所述)?\s*$"
    )
    en_prefix = (
        r"(?:\b(?:do not|does not|must not|should not|never)\s+"
        r"(?:use|apply|adopt|conduct|perform|rely on|treat)|\bwithout|\bno use of|"
        r"\brather than|\binstead of)\s*(?:the)?$"
    )
    if (
        (
            _plain_prefix_exclusion(before, cn_prefix)
            or _plain_prefix_exclusion(before, en_prefix)
        )
        if kind != "oos"
        else (re.search(cn_prefix, before) or re.search(en_prefix, before))
    ):
        return True
    if kind == "training":
        if not _plain_negation_scope(before):
            return False
        if re.match(
            r"(?:性|系数)?(?:不能|不得|不应|不可|无法|不足以)"
            r"(?:直接|单独)?(?:用于|用作|作为|替代|代替|证明|支撑|验证|说明)",
            after,
        ) and not re.match(r"(?:性|系数)?(?:不能不|不得不)", after):
            return True
        if re.match(r"(?:性|系数)?不是(?:有效的)?(?:验证|证据|依据|证明)", after):
            return True
        return bool(
            re.match(
                r"(?:(?:is|are|was|were)\s+not\s+(?:used(?!\s+(?:alone|only))|"
                r"(?:valid\s+)?evidence|(?:a\s+)?proof|validation)|"
                r"(?:cannot|can't|must not|should not)\s+"
                r"(?:replace|substitute for|prove|establish|validate))\b",
                after,
            )
        )
    if kind == "predictive":
        prefixes = (
            r"(?<!不)不\s*$",
            r"\b(?:do not|does not|will not|shall not|must not|never|not)\s*$",
            r"\b(?:do not|does not|will not|never)\s+(?:make|produce|undertake)\s+(?:any\s+)?$",
            r"(?:\bno|\bnot (?:a|an)|\bnot making|\bnot intended to)\s*$",
            r"(?:不是|不涉及)\s*$",
        )
        if any(_plain_prefix_exclusion(before, pattern) for pattern in prefixes):
            return True
        if not _plain_negation_scope(before):
            return False
        return bool(
            re.match(
                r"(?:性研究|性分析|任务|目标)?(?:不适用|不是本次(?:目标|任务)|不属于本次范围)",
                after,
            )
            or re.match(
                r"(?:is|are)\s+not\s+(?:a\s+)?(?:goal|aim|objective|part of|included|intended)\b",
                after,
            )
        )
    # Mentioning an absent design, or saying correlation cannot establish one,
    # does not supply the required affirmative out-of-sample design.
    return bool(
        re.search(
            r"\b(?:do not|does not|must not|should not|never)\s+"
            r"(?:use|conduct|perform|apply|adopt)"
            r"(?:\s+(?:rolling[- ]origin|out-of-sample|holdout|external|validation)){0,3}\s*$",
            before,
        )
        or re.search(
            r"(?:不做|不采用|不使用|不进行)"
            r"(?:样本外|留出|rolling-origin|out-of-sample|验证|设计){0,4}\s*$",
            before,
        )
        or re.search(r"(?:没有|未进行|不按|不能替代|不能证明|无法证明)\s*$", before)
        or re.search(r"(?:没有|未)(?:计划|打算)(?:进行|采用|使用|实施)?\s*$", before)
        or re.search(
            r"\b(?:no plans? to|not planning to)\s*(?:(?:perform|conduct|do|use|apply|adopt)\s*)?$",
            before,
        )
        or re.search(
            r"\b(?:no|not (?:doing|using)|cannot replace|cannot establish)\s*$", before
        )
        or re.match(
            r"(?:样本外|留出|验证|评估|检验|设计|方法){0,4}(?:不做|不用|不采用|不进行|不开展)",
            after,
        )
        or re.match(
            r"(?:(?:rolling[- ]origin|out-of-sample|validation|design|tests?|testing|assessment|protocol)\s+){0,3}"
            r"(?:is|are)\s+not\s+(?:used|adopted|planned|proposed|established|demonstrated)\b",
            after,
        )
    )


def _has_predictive_claim(text: str) -> bool:
    if "下一活动周" in text or "next cycle" in text:
        return True
    return any(
        not _explicitly_negated_mention(
            clause, match.start(), match.end(), kind="predictive"
        )
        for clause in _validation_clauses(text)
        for match in re.finditer(_PREDICTIVE_MENTION, clause)
    )


def _has_affirmative_oos_design(text: str, markers: tuple[str, ...]) -> bool:
    masked_clauses: list[str] = []
    found = False
    all_markers = (
        *markers,
        "rolling-origin",
        "时间外推",
        "前向链式",
        "滚动起点",
        "留未来验证",
        "留未来交叉验证",
    )
    pattern = "|".join(
        re.escape(marker) for marker in sorted(set(all_markers), key=len, reverse=True)
    )
    for clause in _validation_clauses(text):
        chars = list(clause)
        training = list(re.finditer(_TRAINING_CORRELATION, clause))
        for match in re.finditer(pattern, clause):
            negated = _explicitly_negated_mention(
                clause, match.start(), match.end(), kind="oos"
            )
            # In a training-correlation exclusion, a later OOS noun can be just
            # the object of that exclusion. Require a separate affirmative act.
            negative_training = any(
                _explicitly_negated_mention(
                    clause, row.start(), row.end(), kind="training"
                )
                for row in training
            )
            independent_declaration = (
                re.search(
                    r"(?:只做|采用|使用|进行|实施|计划|拟做|use|conduct|perform|apply)\s*(?:the\s+)?$",
                    clause[: match.start()].rstrip(),
                )
                or re.match(
                    r"\s*(?:(?:validation|design|assessment|errors?)\s+){0,2}"
                    r"(?:is|are|will be)\s+(?:used|adopted|performed|applied|conducted)\b",
                    clause[match.end() :],
                )
                or any(
                    row.start() > match.end()
                    and re.search(
                        r"\b(?:rather than|instead of)\b|而非|而不是",
                        clause[match.end() : row.start()],
                    )
                    for row in training
                )
            )
            if negative_training and not independent_declaration:
                negated = True
            if negated:
                chars[match.start() : match.end()] = " " * (match.end() - match.start())
            elif match.group() in (*markers, "rolling-origin"):
                found = True
        masked_clauses.append("".join(chars))
    return found or _explicit_chinese_forward_validation("；".join(masked_clauses))


def _uses_training_correlation_as_validation(text: str) -> bool:
    return any(
        not _explicitly_negated_mention(
            clause, match.start(), match.end(), kind="training"
        )
        for clause in _validation_clauses(text)
        for match in re.finditer(_TRAINING_CORRELATION, clause)
    )


def _cycle_unit_with_explicit_month_exclusion(text: str) -> bool:
    """Recognize a clean cycle-unit declaration with excluded monthly units.

    This does not determine a sample count. Unclear, mixed, or double-negative
    unit descriptions retain the existing monthly-unit rejection.
    """
    month_pattern = r"monthly|months?|月度|月份"
    clauses = re.split(r"[，,。；;()（）\[\]\n]", text.casefold())
    cycle_declaration = (
        r"(?:以)?(?:完整|独立|一个|一轮)?(?:太阳活动周|活动周期|活动周|完整周期)"
        r"(?:(?:为|作为)?(?:独立)?(?:单位|样本|观测单位))?|"
        r"(?:(?:one|a|complete|independent|full)\s+)*solar[- ]cycles?"
        r"(?:\s+(?:(?:are|is|as)\s+)?(?:the\s+)?(?:independent\s+)?(?:units?|samples?))?"
    )
    declared = any(
        re.fullmatch(
            rf"(?:{cycle_declaration})(?:(?:\s+(?:rather than|instead of|not)\s+|而非|而不是).+)?",
            clause.strip(),
        )
        for clause in clauses
    )
    if not declared:
        return False
    mentions = 0
    for clause in clauses:
        for match in re.finditer(month_pattern, clause):
            mentions += 1
            before, after = (
                clause[: match.start()].strip(),
                clause[match.end() :].strip(),
            )
            if re.search(_NEGATION_REVERSALS, before[-90:]):
                return False
            prefix = re.search(
                r"(?:不是|并非|不以|不把|不用|不使用|不作为|排除|不包括|而非|而不是|"
                r"\bnot|\bno|\bwithout|\brather than|\binstead of)\s*$",
                before,
            )
            if prefix is not None:
                lead = before[: prefix.start()].strip()
                if _plain_negation_scope(lead) or re.fullmatch(cycle_declaration, lead):
                    continue
            if re.match(
                r"(?:点|记录|数据|样本)?(?:不作为|不是|不算)(?:独立)?(?:单位|样本|观测单位)|"
                r"(?:records?|points?|observations?|rows?)\s+(?:are|is)\s+not\s+"
                r"(?:the\s+|an?\s+)?(?:independent\s+)?(?:units?|samples?)",
                after,
            ) and _plain_negation_scope(before):
                continue
            return False
    return mentions > 0


def _explicit_chinese_forward_validation(text: str) -> bool:
    """Recognize a complete cutoff-based description, not a lone design label.

    This is an additional language-equivalence path for the authoring gate.
    It does not assess whether the proposed scientific design is valid.
    """
    compact = re.sub(r"\s+", "", text).casefold()
    required = (
        r"时间外推|前向链式|滚动起点",
        r"训练(?:集|数据|样本)?.{0,20}(?:只|仅).{0,24}(?:早于|之前|以前).{0,20}(?:检验|测试|目标|预测)",
        r"每(?:一)?折.{0,20}(?:只|仅).{0,12}(?:使用|用|包含|来自).{0,24}(?:信息截止|截止时刻|签发时刻|截止|截点)(?:之|以)?前",
        r"(?:禁止|不得|不允许|不能).{0,16}(?:跨越|跨过|越过|晚于).{0,8}(?:截止|截点|签发)",
    )
    indexed_expanding_window = (
        r"时间外推|留未来(?:交叉)?验证|前向链式|滚动起点",
        r"按(?:活动周期|周期|活动周)(?:时间)?顺序.{0,16}(?:逐步|逐次|依次|滚动)?.{0,8}(?:扩张|扩展|扩大|扩充|增加).{0,8}训练(?:集|数据|样本)",
        r"第(?P<forecast_cycle>[a-z]|\d+)(?:个)?(?:活动)?周(?:的)?预测.{0,8}(?:只|仅)(?:允许|能|可)?(?:使用|用|采用).{0,8}第(?P=forecast_cycle)(?:个)?(?:活动)?周(?:极小|起始|签发)(?:值|点|时刻|时点)?(?:之|以)?前.{0,8}(?:可得|可用|已知|可获取).{0,8}(?:信息|数据|观测)",
    )
    complete_cutoff_description = all(
        re.search(pattern, compact) for pattern in required
    )
    complete_indexed_description = all(
        re.search(pattern, compact) for pattern in indexed_expanding_window
    )
    if not (complete_cutoff_description or complete_indexed_description):
        return False
    # Do not let this new path admit an explicitly contradictory method merely
    # because the same paragraph also contains the required cutoff sentences.
    prohibition = r"(?:不做|不采用|不使用|禁止|不得|不能|不允许|避免|绝不).{0,12}$"
    unsafe = (
        r"随机(?:k折|k-fold|切分|拆分|划分|交叉验证)|random(?:k-fold|split|fold)",
        r"(?:使用|利用|纳入).{0,8}未来.{0,8}(?:标签|数据|样本|观测|响应)",
    )
    for clause in re.split(r"[，,。；;：:!?\uff01\uff1f\n]", compact):
        for pattern in unsafe:
            for match in re.finditer(pattern, clause):
                if not re.search(prohibition, clause[: match.start()]):
                    return False
    return True


def validate_analysis_claim_contract(value: object) -> dict[str, Any]:
    row = _object(value, "analysis claim contract")
    fields = {
        "schema_version",
        "estimand",
        "independent_sample_unit",
        "independent_sample_count",
        "observation_cutoff",
        "information_set",
        "primary_analysis",
        "baseline",
        "validation_design",
        "decision_rule",
        "missingness",
        "censoring",
        "data_revision",
        "measurement_regime",
        "measurement_kind",
        "effect_size",
        "uncertainty_interval",
        "sensitivity_analysis",
        "influence_analysis",
        "outcome_branches",
    }
    _exact(row, fields, "analysis claim contract")
    if row["schema_version"] != ANALYSIS_CLAIM_VERSION:
        raise ContractError(f"schema_version must be {ANALYSIS_CLAIM_VERSION}")
    sample_count = row["independent_sample_count"]
    if sample_count is not None:
        sample_count = _integer(
            sample_count,
            "independent_sample_count",
            minimum=0,
            maximum=10**9,
        )
    measurement_kind = _enum(
        row["measurement_kind"],
        {"direct", "proxy", "mixed", "not_assessed"},
        "measurement_kind",
    )
    branches = row["outcome_branches"]
    if not isinstance(branches, list) or len(branches) < 2 or len(branches) > 12:
        raise ContractError("outcome_branches must contain 2 to 12 items")
    normalized_branches: list[dict[str, str]] = []
    for index, branch in enumerate(branches):
        label = f"outcome_branches[{index}]"
        item = _object(branch, label)
        _exact(item, {"outcome", "claim_update"}, label)
        normalized_branches.append(
            {
                "outcome": _text(item["outcome"], f"{label}.outcome", maximum=2_000),
                "claim_update": _text(
                    item["claim_update"], f"{label}.claim_update", maximum=2_000
                ),
            }
        )
    result = {"schema_version": ANALYSIS_CLAIM_VERSION}
    for field in fields - {
        "schema_version",
        "independent_sample_count",
        "measurement_kind",
        "outcome_branches",
    }:
        result[field] = _text(row[field], field, maximum=4_000)
    result["independent_sample_count"] = sample_count
    result["measurement_kind"] = measurement_kind
    result["outcome_branches"] = normalized_branches
    predictive_text = "; ".join(
        (
            result["estimand"],
            result["primary_analysis"],
        )
    ).lower()
    if _has_predictive_claim(predictive_text):
        validation = result["validation_design"].lower()
        out_of_sample_markers = (
            "rolling",
            "origin",
            "holdout",
            "external",
            "out-of-sample",
            "time split",
            "leave-one-cycle",
            "时间顺序",
            "留出",
            "样本外",
            "逐活动周",
        )
        if not _has_affirmative_oos_design(validation, out_of_sample_markers):
            raise ContractError(
                "predictive claims require rolling-origin, temporal holdout, external holdout, "
                "or another explicit out-of-sample validation design"
            )
        if _uses_training_correlation_as_validation(validation):
            raise ContractError(
                "training correlation cannot satisfy an out-of-sample prediction gate"
            )
        unit = result["independent_sample_unit"].lower()
        cycle_estimand = any(
            marker in predictive_text
            for marker in ("下一活动周", "next cycle", "solar-cycle", "solar cycle")
        )
        if (
            cycle_estimand
            and any(marker in unit for marker in ("month", "monthly", "月度", "月份"))
            and not _cycle_unit_with_explicit_month_exclusion(unit)
        ):
            raise ContractError(
                "monthly records cannot inflate the independent sample count for a solar-cycle estimand"
            )
    return result


def _validate_evidence_row(value: object, label: str) -> dict[str, Any]:
    row = _object(value, label)
    fields = {
        "source_ref",
        "evidence_role",
        "source_class",
        "evidence_scope",
        "directness",
        "scope_match",
        "independence_group",
        "locator",
        "entailment",
        "quality_cap",
        "rationale",
    }
    _exact(row, fields, label)
    normalized = {
        "source_ref": _nullable_text(
            row["source_ref"], f"{label}.source_ref", maximum=1_000
        ),
        "evidence_role": _enum(
            row["evidence_role"], EVIDENCE_ROLES, f"{label}.evidence_role"
        ),
        "source_class": _enum(
            row["source_class"], SOURCE_CLASSES, f"{label}.source_class"
        ),
        "evidence_scope": _enum(
            row["evidence_scope"], EVIDENCE_SCOPES, f"{label}.evidence_scope"
        ),
        "directness": _enum(row["directness"], DIRECTNESS, f"{label}.directness"),
        "scope_match": _enum(row["scope_match"], SCOPE_MATCH, f"{label}.scope_match"),
        "independence_group": _text(
            row["independence_group"], f"{label}.independence_group", maximum=256
        ),
        "locator": _text(row["locator"], f"{label}.locator", maximum=2_000),
        "entailment": _enum(row["entailment"], ENTAILMENT, f"{label}.entailment"),
        "quality_cap": _enum(row["quality_cap"], QUALITY_CAPS, f"{label}.quality_cap"),
        "rationale": _text(row["rationale"], f"{label}.rationale", maximum=4_000),
    }
    if normalized["evidence_role"] == "gap":
        if normalized["source_ref"] is not None:
            raise ContractError(f"{label} evidence gap must not invent a source_ref")
    elif normalized["source_ref"] is None:
        raise ContractError(f"{label} non-gap evidence requires a source_ref")
    if normalized["evidence_scope"] in {"abstract_only", "wiki_entry", "unknown"}:
        if normalized["quality_cap"] == "release_candidate":
            raise ContractError(
                f"{label} abstract-only, Wiki, or unknown-scope evidence cannot support release_candidate"
            )
    if (
        normalized["directness"] == "context_only"
        and normalized["quality_cap"] != "exploratory"
    ):
        raise ContractError(f"{label} context-only evidence is capped at exploratory")
    if normalized["source_class"] in {
        "simulation",
        "review",
        "wiki_context",
        "user_premise",
    }:
        if normalized["quality_cap"] == "release_candidate":
            raise ContractError(
                f"{label} simulation, review, Wiki, or user-premise evidence cannot alone carry a release claim"
            )
    return normalized


def _validate_method(value: object, label: str) -> dict[str, Any]:
    row = _object(value, label)
    fields = {
        "design_status",
        "independent_sample_unit",
        "independent_sample_count",
        "validation_status",
        "uncertainty_status",
        "reproducibility_status",
        "notes",
    }
    _exact(row, fields, label)
    count = row["independent_sample_count"]
    if count is not None:
        count = _integer(
            count, f"{label}.independent_sample_count", minimum=0, maximum=10**9
        )
    return {
        "design_status": _enum(
            row["design_status"], METHOD_STATUSES, f"{label}.design_status"
        ),
        "independent_sample_unit": _text(
            row["independent_sample_unit"],
            f"{label}.independent_sample_unit",
            maximum=1_000,
        ),
        "independent_sample_count": count,
        "validation_status": _enum(
            row["validation_status"], METHOD_STATUSES, f"{label}.validation_status"
        ),
        "uncertainty_status": _enum(
            row["uncertainty_status"], METHOD_STATUSES, f"{label}.uncertainty_status"
        ),
        "reproducibility_status": _enum(
            row["reproducibility_status"],
            METHOD_STATUSES,
            f"{label}.reproducibility_status",
        ),
        "notes": _text(row["notes"], f"{label}.notes", maximum=4_000),
    }


def _validate_novelty(value: object, label: str) -> dict[str, Any]:
    row = _object(value, label)
    fields = {
        "status",
        "contribution_type",
        "novelty_delta",
        "nearest_prior_art",
        "query_axes",
        "searched_family_count",
        "search_cutoff",
        "coverage_gaps",
    }
    _exact(row, fields, label)
    prior = row["nearest_prior_art"]
    if not isinstance(prior, list) or len(prior) > 20:
        raise ContractError(f"{label}.nearest_prior_art must contain at most 20 items")
    normalized_prior: list[dict[str, str]] = []
    for index, item in enumerate(prior):
        plabel = f"{label}.nearest_prior_art[{index}]"
        art = _object(item, plabel)
        _exact(
            art,
            {
                "source_ref",
                "existing_claim",
                "overlap",
                "difference",
                "duplication_risk",
            },
            plabel,
        )
        normalized_prior.append(
            {key: _text(art[key], f"{plabel}.{key}", maximum=4_000) for key in art}
        )
    cutoff = _nullable_text(row["search_cutoff"], f"{label}.search_cutoff", maximum=64)
    if cutoff is not None:
        try:
            datetime.fromisoformat(cutoff)
        except ValueError as exc:
            raise ContractError(f"{label}.search_cutoff must be ISO-8601") from exc
    result = {
        "status": _enum(row["status"], NOVELTY_STATUSES, f"{label}.status"),
        "contribution_type": _enum(
            row["contribution_type"], CONTRIBUTION_TYPES, f"{label}.contribution_type"
        ),
        "novelty_delta": _text(
            row["novelty_delta"], f"{label}.novelty_delta", maximum=4_000
        ),
        "nearest_prior_art": normalized_prior,
        "query_axes": _text_list(row["query_axes"], f"{label}.query_axes", maximum=10),
        "searched_family_count": _integer(
            row["searched_family_count"],
            f"{label}.searched_family_count",
            minimum=0,
            maximum=10_000,
        ),
        "search_cutoff": cutoff,
        "coverage_gaps": _text_list(
            row["coverage_gaps"], f"{label}.coverage_gaps", maximum=20
        ),
    }
    if result["status"] == "potentially_novel":
        if len(result["query_axes"]) < 3 or result["searched_family_count"] < 8:
            raise ContractError(
                f"{label} cannot mark potentially_novel without 3 query axes and 8 source families"
            )
        if not result["nearest_prior_art"]:
            raise ContractError(f"{label} potentially_novel requires nearest prior art")
    return result


def _validate_quality_claim(value: object, label: str) -> dict[str, Any]:
    row = _object(value, label)
    fields = {
        "claim_id",
        "claim_component",
        "load_bearing",
        "evidence_matrix",
        "method_assessment",
        "novelty_assessment",
        "conclusion_cap",
        "quality_status",
        "key_gaps",
    }
    _exact(row, fields, label)
    matrix = row["evidence_matrix"]
    if not isinstance(matrix, list) or not matrix or len(matrix) > 100:
        raise ContractError(f"{label}.evidence_matrix must contain 1 to 100 items")
    normalized = {
        "claim_id": _id(row["claim_id"], f"{label}.claim_id"),
        "claim_component": _enum(
            row["claim_component"], CLAIM_COMPONENTS, f"{label}.claim_component"
        ),
        "load_bearing": _boolean(row["load_bearing"], f"{label}.load_bearing"),
        "evidence_matrix": [
            _validate_evidence_row(item, f"{label}.evidence_matrix[{index}]")
            for index, item in enumerate(matrix)
        ],
        "method_assessment": _validate_method(
            row["method_assessment"], f"{label}.method_assessment"
        ),
        "novelty_assessment": _validate_novelty(
            row["novelty_assessment"], f"{label}.novelty_assessment"
        ),
        "conclusion_cap": _enum(
            row["conclusion_cap"], QUALITY_CAPS, f"{label}.conclusion_cap"
        ),
        "quality_status": _enum(
            row["quality_status"], QUALITY_STATUSES, f"{label}.quality_status"
        ),
        "key_gaps": _text_list(row["key_gaps"], f"{label}.key_gaps", maximum=30),
    }
    if (
        normalized["novelty_assessment"]["status"]
        in {
            "potentially_novel",
            "novelty_not_assessed",
        }
        and normalized["quality_status"] == "release_candidate"
    ):
        raise ContractError(
            f"{label} unresolved novelty cannot be an automatic release_candidate"
        )
    if any(item["evidence_role"] == "gap" for item in normalized["evidence_matrix"]):
        if normalized["conclusion_cap"] == "release_candidate":
            raise ContractError(
                f"{label} evidence gaps cap the conclusion below release_candidate"
            )
    if normalized["quality_status"] == "release_candidate":
        if normalized["conclusion_cap"] != "release_candidate":
            raise ContractError(
                f"{label} release_candidate status requires a release_candidate conclusion cap"
            )
        if normalized["key_gaps"]:
            raise ContractError(
                f"{label} unresolved key gaps prevent release_candidate status"
            )
        unacceptable = [
            item
            for item in normalized["evidence_matrix"]
            if item["scope_match"] in {"mismatch", "not_assessable"}
            or item["entailment"] in {"not_entailed", "not_assessable"}
        ]
        if unacceptable:
            raise ContractError(
                f"{label} unresolved scope or entailment defects prevent release_candidate status"
            )
        direct_primary = [
            item
            for item in normalized["evidence_matrix"]
            if item["evidence_role"] == "supports"
            and item["source_class"] in {"direct_observation", "real_experiment"}
            and item["evidence_scope"]
            in {"full_text", "dataset_record", "experiment_record"}
            and item["directness"] == "direct"
            and item["scope_match"] == "matched"
            and item["entailment"] == "entailed"
        ]
        if normalized["load_bearing"] and not direct_primary:
            raise ContractError(
                f"{label} load-bearing release claim requires matched direct primary evidence"
            )
        support_groups = {
            item["independence_group"]
            for item in normalized["evidence_matrix"]
            if item["evidence_role"] == "supports"
            and item["scope_match"] == "matched"
            and item["entailment"] == "entailed"
            and item["quality_cap"] == "release_candidate"
        }
        if normalized["load_bearing"] and len(support_groups) < 2:
            raise ContractError(
                f"{label} repeated papers from one evidence family do not establish an independent release claim"
            )
    return normalized


def validate_scientific_quality_assessment(value: object) -> dict[str, Any]:
    row = _object(value, "scientific quality assessment")
    fields = {
        "schema_version",
        "assessment_id",
        "task_id",
        "review_mode",
        "assessment_review_mode",
        "artifact_refs",
        "round",
        "claims",
        "created_at",
    }
    _exact(row, fields, "scientific quality assessment")
    if row["schema_version"] != SCIENTIFIC_QUALITY_VERSION:
        raise ContractError(f"schema_version must be {SCIENTIFIC_QUALITY_VERSION}")
    refs = row["artifact_refs"]
    if not isinstance(refs, list) or not 1 <= len(refs) <= 20:
        raise ContractError("artifact_refs must contain 1 to 20 items")
    claims = row["claims"]
    if not isinstance(claims, list) or not 1 <= len(claims) <= 200:
        raise ContractError("claims must contain 1 to 200 items")
    normalized_claims = [
        _validate_quality_claim(item, f"claims[{index}]")
        for index, item in enumerate(claims)
    ]
    component_keys = [
        (claim["claim_id"], claim["claim_component"]) for claim in normalized_claims
    ]
    if len(component_keys) != len(set(component_keys)):
        raise ContractError("quality claim id and component pairs must be unique")
    return {
        "schema_version": SCIENTIFIC_QUALITY_VERSION,
        "assessment_id": _id(row["assessment_id"], "assessment_id"),
        "task_id": _id(row["task_id"], "task_id"),
        "review_mode": _enum(row["review_mode"], REVIEW_MODES, "review_mode"),
        "assessment_review_mode": _enum(
            row["assessment_review_mode"],
            {"closed", "two_pass"},
            "assessment_review_mode",
        ),
        "artifact_refs": [
            _artifact_ref(item, f"artifact_refs[{index}]")
            for index, item in enumerate(refs)
        ],
        "round": _integer(row["round"], "round", minimum=1, maximum=9999),
        "claims": normalized_claims,
        "created_at": _timestamp(row["created_at"], "created_at"),
    }


def build_scientific_quality_assessment(**kwargs: Any) -> dict[str, Any]:
    return validate_scientific_quality_assessment(
        {"schema_version": SCIENTIFIC_QUALITY_VERSION, **kwargs}
    )
