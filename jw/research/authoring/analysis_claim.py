"""Discoverable authoring schema for the existing analysis-claim validator.

This module describes structure only. Producers supply every scientific value;
the established validator remains the authority for semantic acceptance.
"""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator

from research_quality.contracts import ANALYSIS_CLAIM_VERSION

ANALYSIS_CLAIM_STAGES = (
    "planning",
    "hypothesis",
    "experiment_design",
    "experiment_result",
)

_TEXT_DESCRIPTIONS = {
    "estimand": "The quantity or claim to be assessed, with its scope.",
    "independent_sample_unit": "The independent observational or experimental unit.",
    "observation_cutoff": "The observation cutoff and its meaning for this stage.",
    "information_set": "Information available at the declared cutoff.",
    "primary_analysis": "Primary analysis or, before execution, the proposed method.",
    "baseline": "The comparison baseline and its rationale.",
    "validation_design": "Validation design, including out-of-sample design for prediction.",
    "decision_rule": "Predeclared decision rule and treatment of inconclusive results.",
    "missingness": "Missingness handling and unresolved limitations.",
    "censoring": "Censoring or incomplete-observation handling.",
    "data_revision": "Data version and revision handling.",
    "measurement_regime": "Measurement definitions, calibration regimes, and comparability.",
    "effect_size": "Observed effect or, before execution, the effect definition to estimate.",
    "uncertainty_interval": "Observed uncertainty or, before execution, its proposed assessment.",
    "sensitivity_analysis": "Sensitivity analysis and its execution status.",
    "influence_analysis": "Influence analysis and its execution status.",
}


def analysis_claim_schema() -> dict[str, Any]:
    """Return a fresh closed schema; no scientific defaults or examples."""
    properties: dict[str, Any] = {
        "schema_version": {"type": "string", "const": ANALYSIS_CLAIM_VERSION},
        **{
            name: {
                "type": "string",
                "minLength": 1,
                "maxLength": 4000,
                "pattern": r"\S",
                "description": description,
            }
            for name, description in _TEXT_DESCRIPTIONS.items()
        },
        "independent_sample_count": {
            "type": ["integer", "null"],
            "minimum": 0,
            "maximum": 10**9,
            "description": "Actual independent count; null when not established. Do not invent a count.",
        },
        "measurement_kind": {
            "type": "string",
            "enum": ["direct", "proxy", "mixed", "not_assessed"],
        },
        "outcome_branches": {
            "type": "array",
            "minItems": 2,
            "maxItems": 12,
            "description": "Possible outcomes and their conditional claim updates, not observed results.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["outcome", "claim_update"],
                "properties": {
                    name: {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 2000,
                        "pattern": r"\S",
                    }
                    for name in ("outcome", "claim_update")
                },
            },
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


def analysis_claim_authoring_contract() -> dict[str, Any]:
    return {
        "version": "analysis-claim-authoring-v2",
        "schema": analysis_claim_schema(),
        "stages": list(ANALYSIS_CLAIM_STAGES),
        "instruction": (
            "Supply all 20 fields using the actual stage inputs and outputs. "
            "For planning/design distinguish proposed methods from measured results; "
            "state unestimated effects and uncertainty honestly. Unknown sample counts "
            "may be null. Do not invent evidence, outcomes, confidence, or conclusions. "
            "Recording this contract does not accept a stage or replace independent review."
        ),
        "semantic_validation": (
            "The native AnalysisClaimContractV1 validator still applies, including "
            "out-of-sample prediction and independent-unit requirements."
        ),
    }


def analysis_claim_tool_arguments_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["stage", "contract"],
        "properties": {
            "stage": {"type": "string", "enum": list(ANALYSIS_CLAIM_STAGES)},
            "contract": {
                "anyOf": [analysis_claim_schema(), {"type": "string"}],
                "description": (
                    "Complete 20-field object, or legacy JSON string encoding that exact object. "
                    "Read research_quality_get_analysis_claim for the full authoring contract."
                ),
            },
        },
    }


def analysis_claim_structure_issues(value: object) -> list[dict[str, str]]:
    """Aggregate structural failures without normalizing or repairing values."""
    errors = Draft202012Validator(analysis_claim_schema()).iter_errors(value)
    return [
        {
            "path": "/" + "/".join(str(part) for part in error.absolute_path),
            "message": error.message,
        }
        for error in sorted(
            errors, key=lambda e: (str(list(e.absolute_path)), e.message)
        )
    ]
