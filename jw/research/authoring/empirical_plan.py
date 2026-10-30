"""One model-facing and runtime contract for compact empirical plan authoring."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Annotated, Any

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema

from jw.runtime_layout import PROJECT_ROOT

CONTRACT_VERSION = "compact-empirical-plan-v2"
EMPIRICAL_ROUTE_STAGES = (
    "data",
    "hypothesis_generation",
    "experiment_design",
    "experiment_result",
    "hypothesis_update",
)
_FULL_SCHEMA = json.loads(
    (PROJECT_ROOT / "research/planner/specs/planner_response_v1.schema.json").read_text(
        encoding="utf-8"
    )
)


def _inline(value: Any) -> Any:
    if isinstance(value, list):
        return [_inline(item) for item in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        reference = value["$ref"]
        if not reference.startswith("#/$defs/"):
            raise ValueError("Compact plan contract cannot load external references")
        return _inline(_FULL_SCHEMA["$defs"][reference.removeprefix("#/$defs/")])
    return {key: _inline(item) for key, item in value.items()}


def _selected_object(definition: str, fields: tuple[str, ...]) -> dict:
    source = _FULL_SCHEMA["$defs"][definition]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(fields),
        "properties": {name: _inline(source["properties"][name]) for name in fields},
    }


def compact_empirical_plan_schema() -> dict:
    """Derive scientific field constraints from the full plan, with no defaults."""
    properties = {
        "scope": _inline(_FULL_SCHEMA["$defs"]["scope"]),
        "subquestions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 12,
            "items": _selected_object(
                "subquestion", ("question", "purpose", "completion_evidence")
            ),
        },
        "evidence_gaps": {
            "type": "array",
            "minItems": 1,
            "maxItems": 50,
            "items": _inline(_FULL_SCHEMA["$defs"]["text"]),
        },
        "datasets": {
            "type": "array",
            "minItems": 1,
            "maxItems": 20,
            "items": _selected_object(
                "dataset",
                (
                    "name",
                    "purpose",
                    "required_variables",
                    "time_coverage_needed",
                    "cadence_needed",
                    "quality_requirements",
                ),
            ),
        },
        "stage_methods": {
            "type": "object",
            "additionalProperties": False,
            "required": list(EMPIRICAL_ROUTE_STAGES),
            "properties": {
                stage: {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 4000,
                    "pattern": r"\S",
                }
                for stage in EMPIRICAL_ROUTE_STAGES
            },
        },
        "evaluation_focus": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5,
            "description": "One to five checks, assigned cyclically to the five stages; no item is silently ignored.",
            "items": _inline(_FULL_SCHEMA["$defs"]["text"]),
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


COMPACT_EMPIRICAL_PLAN_SCHEMA = compact_empirical_plan_schema()
_VALIDATOR = Draft202012Validator(COMPACT_EMPIRICAL_PLAN_SCHEMA)


class CompactEmpiricalPlanValidationError(ValueError):
    def __init__(self, issues: list[dict]):
        self.issues = issues
        super().__init__(
            "Compact plan schema validation failed: "
            + "; ".join(f"{item['path']}: {item['message']}" for item in issues)
        )


def validate_compact_empirical_plan(payload: Any) -> None:
    """Report all structural defects in one pass, without dropping/filling fields."""
    errors = sorted(
        _VALIDATOR.iter_errors(payload),
        key=lambda e: (str(list(e.absolute_path)), e.validator, e.message),
    )
    issues = [
        {
            "path": "$"
            + "".join(
                f"[{part}]" if isinstance(part, int) else f".{part}"
                for part in error.absolute_path
            ),
            "validator": error.validator,
            "message": error.message,
        }
        for error in errors
    ]
    if issues:
        raise CompactEmpiricalPlanValidationError(issues)


def empirical_plan_authoring_contract() -> dict:
    return {
        "version": CONTRACT_VERSION,
        "argument": "compact_plan_json",
        "preferred_encoding": "structured JSON object; legacy JSON-encoded string remains supported with identical validation",
        "schema": deepcopy(COMPACT_EMPIRICAL_PLAN_SCHEMA),
        "closed_fields_rule": "Every object rejects extra keys at every level. Do not copy fields from the expanded full-plan schema into this compact input.",
        "host_owned_fields": {
            "top_level": [
                "transitions",
                "stop_rules",
                "research_route",
                "research_artifacts",
                "iteration_policy",
            ],
            "subquestions": ["id", "depends_on"],
            "datasets": [
                "id",
                "source_kind",
                "selected_source_id",
                "acquisition_status",
                "version_requirement",
                "unit_requirements",
                "revision_requirements",
                "license_requirements",
            ],
        },
        "semantic_rule": "The author supplies every scientific field. Describe question-specific stopping/transition criteria inside stage_methods and evaluation_focus; lifecycle routing and identifiers are host-generated. No proposed method, plan readiness, confidence, or evidence gap is an experimental finding.",
        "validation_rule": "The same schema is applied before plan expansion or active-draft persistence. All structural issue paths are returned together. Full-plan and cross-section validation still run afterwards; no field is silently removed or invented.",
    }


CompactEmpiricalPlanArgument = Annotated[
    str | dict[str, Any],
    # LangChain rebuilds tool_call_schema as a subset model. Field metadata is
    # retained by that rebuild, whereas Field.json_schema_extra is discarded.
    WithJsonSchema(
        {
            "anyOf": [
                deepcopy(COMPACT_EMPIRICAL_PLAN_SCHEMA),
                {
                    "type": "string",
                    "description": "Legacy JSON-encoded object; its decoded contents must satisfy the same complete object schema.",
                },
            ]
        }
    ),
]


class EmpiricalPlanToolArguments(BaseModel):
    """Expose the real object schema while preserving the historical argument."""

    model_config = ConfigDict(extra="forbid")

    compact_plan_json: CompactEmpiricalPlanArgument = Field(
        description="Prefer a structured object satisfying the complete compact contract. Legacy JSON strings undergo exactly the same closed-field validation.",
    )
    request_sha256: str = Field(
        default="",
        description="Current bound request hash from research_planner_get_brief; omit only to use the active bound request.",
    )
