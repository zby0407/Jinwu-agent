"""Dependency-light contracts for high-quality research review packages.

These sidecars do not replace ReviewVerdictV2.  They make evidence quality,
method validity, and novelty uncertainty explicit without turning model review
into publication-priority adjudication.
"""

from __future__ import annotations

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
    predictive_text = " ".join(
        (
            result["estimand"],
            result["primary_analysis"],
        )
    ).lower()
    if any(
        marker in predictive_text
        for marker in (
            "predict",
            "forecast",
            "预测",
            "预报",
            "下一活动周",
            "next cycle",
        )
    ):
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
        if not any(marker in validation for marker in out_of_sample_markers):
            raise ContractError(
                "predictive claims require rolling-origin, temporal holdout, external holdout, "
                "or another explicit out-of-sample validation design"
            )
        if any(
            marker in validation
            for marker in ("training correlation", "train correlation", "训练相关")
        ):
            raise ContractError(
                "training correlation cannot satisfy an out-of-sample prediction gate"
            )
        unit = result["independent_sample_unit"].lower()
        cycle_estimand = any(
            marker in predictive_text
            for marker in ("下一活动周", "next cycle", "solar-cycle", "solar cycle")
        )
        if cycle_estimand and any(
            marker in unit for marker in ("month", "monthly", "月度", "月份")
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
            raise ContractError(
                f"{label} potentially_novel requires nearest prior art"
            )
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
        (claim["claim_id"], claim["claim_component"])
        for claim in normalized_claims
    ]
    if len(component_keys) != len(set(component_keys)):
        raise ContractError(
            "quality claim id and component pairs must be unique"
        )
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
