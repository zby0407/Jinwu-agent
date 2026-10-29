"""Structural authoring guidance for the unchanged native portfolio validator.

This module supplies no hypothesis, evidence, rank, confidence, or scientific
default. Cross-references and scientific release gates remain native checks.
"""

from __future__ import annotations

import sys
from typing import Any

from jsonschema import Draft202012Validator, validators

from jw.runtime_layout import PROJECT_ROOT

_SRC = PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from scientific_hypothesis.ranking import (  # noqa: E402
    CLAIM_TYPES,
    EVIDENCE_STATUSES,
    FALSIFIABILITY_STATUSES,
    FEASIBILITY_STATUSES,
    FORECAST_ORIGINS,
    OUT_OF_SAMPLE_STATUSES,
    PORTFOLIO_RANKING_VERSION,
    PORTFOLIO_ROLES,
    PORTFOLIO_STATUSES,
    PRIORITY_LEVELS,
    SENSITIVITY_STATUSES,
    SUPPORT_LEVELS,
)

_LIFECYCLE_FIELDS = (
    "portfolio_role",
    "portfolio_status",
    "forecast_origin",
    "forecast_receipt_ref",
)


def _text(maximum: int = 2_000) -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "pattern": r"\S",
        "description": (
            "Non-empty text. The native validator normalizes whitespace and "
            f"requires 1 through {maximum} normalized characters. Raw whitespace "
            "is not an additional authoring restriction."
        ),
    }


def _identifier() -> dict[str, Any]:
    return {
        "type": "string",
        "pattern": r"^\s*[A-Za-z][A-Za-z0-9_-]{0,63}\s*$",
        "description": (
            "An existing declared identifier, 1 through 64 normalized characters; "
            "starts with a letter and contains only letters, digits, _ or -. "
            "The native validator remains responsible for identity references."
        ),
    }


def _enum(values: set[str]) -> dict[str, Any]:
    return {"type": "string", "enum": sorted(values)}


def _closed(
    properties: dict[str, Any], *, required: list[str] | None = None
) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties) if required is None else required,
        "properties": properties,
    }


def _array(item: dict[str, Any], *, minimum: int = 0, maximum: int = 30) -> dict:
    return {
        "type": "array",
        "minItems": minimum,
        "maxItems": maximum,
        "items": item,
    }


def portfolio_ranking_schema() -> dict[str, Any]:
    """Describe every native input field, without inventing scientific values."""
    member = _closed({"run_id": _text(200), "candidate_id": _identifier()})
    group = _closed(
        {
            "hypothesis_id": _identifier(),
            "normalized_statement": _text(),
            "member_candidates": _array(member, minimum=1),
            "deduplication_rationale": _text(),
        }
    )
    evidence_link = _closed(
        {
            "evidence_id": _identifier(),
            "dependency_group_id": _identifier(),
            "relation": _text(1_000),
        }
    )
    next_experiment = _closed(
        {
            "objective": _text(),
            "discriminating_power": _text(),
            "feasibility": _enum(FEASIBILITY_STATUSES),
        }
    )
    rank = {
        "type": "integer",
        "minimum": 1,
        "description": (
            "A JSON integer, without a fractional part or boolean coercion. "
            "The native validator requires each order to be contiguous from one."
        ),
    }
    ranked_properties = {
        "hypothesis_id": _identifier(),
        "support_rank": dict(rank),
        "research_priority_rank": dict(rank),
        "claim_type": _enum(CLAIM_TYPES),
        "current_evidence_status": _enum(EVIDENCE_STATUSES),
        "scientific_support": _closed(
            {"level": _enum(SUPPORT_LEVELS), "rationale": _text()}
        ),
        "research_priority": _closed(
            {"level": _enum(PRIORITY_LEVELS), "rationale": _text()}
        ),
        "data_sources_verified": {"type": "boolean"},
        "support_evidence": _array(evidence_link),
        "opposing_evidence": _array(evidence_link),
        "out_of_sample_validation": _closed(
            {
                "status": _enum(OUT_OF_SAMPLE_STATUSES),
                "baseline_comparison": _text(),
            }
        ),
        "effect_uncertainty": _closed(
            {
                "effect_summary": _text(),
                "interval_summary": _text(),
                "interval_crosses_null": {"type": ["boolean", "null"]},
            }
        ),
        "sensitivity": _closed(
            {
                key: _enum(SENSITIVITY_STATUSES)
                for key in (
                    "leave_one_out",
                    "temporal_split",
                    "measurement_regime",
                    "definition",
                )
            }
        ),
        "falsifiability": _closed(
            {
                "status": _enum(FALSIFIABILITY_STATUSES),
                "conditions": _array(_text(), minimum=1, maximum=20),
            }
        ),
        "key_limitations": _array(_text(), minimum=1, maximum=20),
        "strongest_null_hypothesis": _text(),
        "next_experiment": next_experiment,
        "ranking_rationale": _text(),
        "release_boundary": _text(),
    }
    ranked_required = list(ranked_properties)
    ranked_properties.update(
        portfolio_role=_enum(PORTFOLIO_ROLES),
        portfolio_status=_enum(PORTFOLIO_STATUSES),
        forecast_origin=_enum(FORECAST_ORIGINS),
        forecast_receipt_ref={
            "anyOf": [_text(500), {"type": "null"}],
            "description": (
                "Null or a task-relative forecast receipt path. The native validator "
                "checks experiment/runs/, traversal, filename, bound evidence and "
                "observable compatibility; no receipt is inferred by this schema."
            ),
        },
    )
    ranked = _closed(ranked_properties, required=ranked_required)
    ranked["dependentRequired"] = {
        key: [other for other in _LIFECYCLE_FIELDS if other != key]
        for key in _LIFECYCLE_FIELDS
    }
    ranked["description"] = (
        "Supply every required scientific assessment. Lifecycle fields remain "
        "legacy-compatible: either omit all four or supply all four. They are "
        "never partly filled by the authoring layer."
    )
    selected = _closed(
        {
            "hypothesis_ids": _array(_identifier(), minimum=1),
            "objective": _text(),
            "discriminating_power": _text(),
            "feasibility": _enum(FEASIBILITY_STATUSES),
            "rationale": _text(),
        }
    )
    return _closed(
        {
            "schema_version": {"type": "string", "const": PORTFOLIO_RANKING_VERSION},
            "source_runs": _array(_text(), minimum=1, maximum=20),
            "hypothesis_groups": _array(group, minimum=1),
            "ranked_hypotheses": _array(ranked, minimum=1),
            "selected_next_experiment": selected,
        }
    )


def portfolio_ranking_authoring_contract() -> dict[str, Any]:
    return {
        "version": "portfolio-ranking-authoring-v1",
        "schema": portfolio_ranking_schema(),
        "instruction": (
            "Supply the entire closed structure for the currently tail-reviewed "
            "candidate pool. Use actual candidate and evidence identifiers; "
            "separate scientific-support order from research-priority order. "
            "Do not invent evidence, confidence, rankings, grouping rationale or "
            "experiment results to satisfy the schema. No example or scientific "
            "default is supplied. Legacy JSON strings and native objects describe "
            "the same unmodified input contract."
        ),
        "semantic_validation": (
            "The existing validate_portfolio_ranking remains authoritative for "
            "normalized text lengths, identifier/run/evidence joins, verified "
            "evidence roles, independent dependency groups, complete candidate "
            "coverage, contiguous ranks, high-support limits, lifecycle/forecast "
            "receipt constraints, and selected-experiment references. Structural "
            "validation does not establish scientific validity or independent acceptance."
        ),
    }


def portfolio_ranking_contract() -> dict[str, Any]:
    """Task-neutral ranking guidance shared by request binding and draft reads."""
    return {
        "schema_version": PORTFOLIO_RANKING_VERSION,
        "separate_orders": ["scientific_support", "research_priority"],
        "evidence_dependency_rule": (
            "Evidence from models or papers sharing the same underlying data "
            "must use one dependency_group_id; shared data never creates "
            "multiple independent support groups."
        ),
        "authoring_contract": portfolio_ranking_authoring_contract(),
    }


def portfolio_ranking_tool_arguments_schema() -> dict[str, Any]:
    return _closed(
        {
            "ranking_json": {
                "anyOf": [portfolio_ranking_schema(), {"type": "string"}],
                "description": (
                    "Complete portfolio-ranking object, or the legacy JSON string "
                    "encoding that same object. No fields are added, dropped or repaired."
                ),
            }
        }
    )


_StrictStructureValidator = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda _checker, value: type(value) is int
    ),
)


def portfolio_ranking_structure_issues(payload: object) -> list[dict[str, str]]:
    """Aggregate structural failures without modifying any input value."""
    errors = _StrictStructureValidator(portfolio_ranking_schema()).iter_errors(payload)
    return [
        {
            "path": "/" + "/".join(str(part) for part in error.absolute_path),
            "message": error.message,
        }
        for error in sorted(
            errors, key=lambda item: (str(list(item.absolute_path)), item.message)
        )
    ]
