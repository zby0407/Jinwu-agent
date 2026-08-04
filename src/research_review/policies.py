"""Versioned central policy registry for producer preflight and Evidence review."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

POLICY_VERSION = "evidence-policy-v2.8"

_RULES: tuple[dict[str, Any], ...] = (
    {
        "rule_id": "SCHEMA_VALID",
        "layer": "producer_hard",
        "stages": [
            "planning",
            "data",
            "hypothesis",
            "experiment_design",
            "experiment_result",
        ],
        "default_severity": "critical",
        "executor": "deterministic",
        "description": "The producer-local v1 contract and required fields pass before review.",
    },
    {
        "rule_id": "ARTIFACT_INTEGRITY",
        "layer": "producer_hard",
        "stages": [
            "planning",
            "data",
            "hypothesis",
            "experiment_design",
            "experiment_result",
            "integration",
            "final_release",
        ],
        "default_severity": "critical",
        "executor": "deterministic",
        "description": "Artifact version, task binding, upstream refs, and SHA-256 are valid.",
    },
    {
        "rule_id": "DATA_SEMANTICS_BOUND",
        "layer": "producer_hard",
        "stages": ["data", "experiment_design", "experiment_result"],
        "default_severity": "critical",
        "executor": "producer_and_deterministic",
        "description": "Dataset product, unit, coverage, time semantics, and immutable input hash are explicit.",
    },
    {
        "rule_id": "REQUIRED_DATA_INPUT_UNAVAILABLE",
        "layer": "orchestrator_hard",
        "stages": ["data"],
        "default_severity": "critical",
        "executor": "deterministic",
        "description": "A Data artifact proving that no eligible immutable input is bound is an honest terminal blocker, never an acceptable data product.",
    },
    {
        "rule_id": "NO_TEMPORAL_OR_FOLD_LEAKAGE",
        "layer": "producer_hard",
        "stages": ["data", "experiment_design", "experiment_result"],
        "default_severity": "critical",
        "executor": "producer_and_deterministic",
        "description": "Feature selection, preprocessing, tuning, and evaluation preserve the declared holdout boundary.",
    },
    {
        "rule_id": "PROVENANCE_BOUND",
        "layer": "shared_semantic",
        "stages": [
            "planning",
            "data",
            "hypothesis",
            "experiment_result",
            "integration",
            "final_release",
        ],
        "default_severity": "major",
        "executor": "producer_preflight_and_reviewer",
        "description": "Material claims point to inspected source excerpts, data receipts, or real measurements.",
    },
    {
        "rule_id": "CLAIM_EVIDENCE_ENTAILMENT",
        "layer": "shared_semantic",
        "stages": ["hypothesis", "experiment_result", "integration", "final_release"],
        "default_severity": "major",
        "executor": "producer_preflight_and_reviewer",
        "description": "Each cited source supports, opposes, or limits the exact claim and scope attributed to it.",
    },
    {
        "rule_id": "NUMERIC_SOURCE_BOUND",
        "layer": "shared_semantic",
        "stages": [
            "planning",
            "hypothesis",
            "experiment_design",
            "experiment_result",
            "integration",
            "final_release",
        ],
        "default_severity": "major",
        "executor": "producer_preflight_and_reviewer",
        "description": "Numbers, dates, thresholds, sample counts, and uncertainty have traceable origins.",
    },
    {
        "rule_id": "CAUSAL_SCOPE_BOUNDED",
        "layer": "shared_semantic",
        "stages": ["hypothesis", "experiment_result", "integration", "final_release"],
        "default_severity": "major",
        "executor": "producer_preflight_and_reviewer",
        "description": "Correlation, prediction, and mechanism claims do not exceed the design and evidence.",
    },
    {
        "rule_id": "TEMPORAL_CAUSAL_ORDER",
        "layer": "shared_semantic",
        "stages": ["hypothesis", "experiment_design", "experiment_result", "integration", "final_release"],
        "default_severity": "major",
        "executor": "producer_preflight_and_reviewer",
        "description": "Every proposed cause precedes its outcome on the declared solar-cycle timeline; predictors, descendants, and next-cycle effects are not reversed into contemporaneous causes.",
    },
    {
        "rule_id": "SOLAR_DYNAMO_REGIME_BOUNDARY",
        "layer": "reviewer_semantic",
        "stages": ["hypothesis", "experiment_design", "experiment_result", "integration"],
        "default_severity": "major",
        "executor": "reviewer",
        "description": "Surface versus subsurface transport, dynamo-model regime, transport-effect sign, and precursor-versus-dominance claims remain explicitly separated and evidence bounded.",
    },
    {
        "rule_id": "PROXY_MEASUREMENT_BOUNDARY",
        "layer": "shared_semantic",
        "stages": ["data", "hypothesis", "experiment_result", "integration"],
        "default_severity": "major",
        "executor": "producer_preflight_and_reviewer",
        "description": "Solar proxies are not silently reinterpreted as direct measurements of the target magnetic or dynamo quantity.",
    },
    {
        "rule_id": "SAMPLE_INDEPENDENCE_AND_UNCERTAINTY",
        "layer": "shared_semantic",
        "stages": [
            "data",
            "hypothesis",
            "experiment_design",
            "experiment_result",
            "integration",
        ],
        "default_severity": "major",
        "executor": "producer_preflight_and_reviewer",
        "description": "Independent solar-cycle count, dependence, censoring, uncertainty, and power bound the strength and generality of claims.",
    },
    {
        "rule_id": "PREDICTION_VALIDATION_AND_CALIBRATION",
        "layer": "shared_semantic",
        "stages": ["planning", "experiment_design", "experiment_result", "integration"],
        "default_severity": "major",
        "executor": "producer_preflight_and_reviewer",
        "description": "Predictive claims use time-valid baselines, rolling or external validation, uncertainty, and calibration appropriate to the question.",
    },
    {
        "rule_id": "EXPERIMENT_EXECUTION_REPRODUCIBLE",
        "layer": "producer_hard",
        "stages": ["experiment_result", "integration"],
        "default_severity": "critical",
        "executor": "deterministic_and_reviewer",
        "description": "A result comes from the accepted design, immutable inputs, real execution, verified measurements, and a replayable receipt.",
    },
    {
        "rule_id": "ALTERNATIVES_AND_FALSIFIERS",
        "layer": "reviewer_semantic",
        "stages": [
            "hypothesis",
            "experiment_design",
            "experiment_result",
            "integration",
        ],
        "default_severity": "major",
        "executor": "reviewer",
        "description": "Competing explanations, confounders, counterevidence, and discriminating falsifiers are preserved.",
    },
    {
        "rule_id": "HYPOTHESIS_PORTFOLIO_COMPLETE",
        "layer": "reviewer_semantic",
        "stages": ["hypothesis", "integration"],
        "default_severity": "major",
        "executor": "reviewer",
        "description": "Each material hypothesis states scope, mechanism, necessary conditions, predictions, weakening observations, alternatives, confounders, falsifiers, next test, and evidence-bounded confidence.",
    },
    {
        "rule_id": "CROSS_STAGE_CLOSURE",
        "layer": "reviewer_semantic",
        "stages": ["planning", "integration", "final_release"],
        "default_severity": "critical",
        "executor": "deterministic_and_reviewer",
        "description": "Question, plan, data, hypotheses, experiment, result, and conclusion form a consistent chain.",
    },
    {
        "rule_id": "NULL_RESULT_INTEGRITY",
        "layer": "reviewer_semantic",
        "stages": ["experiment_result", "integration", "final_release"],
        "default_severity": "critical",
        "executor": "reviewer",
        "description": "A valid null or negative result is retained and is not rerun or reframed to seek significance.",
    },
    {
        "rule_id": "LIMITS_CARRIED_FORWARD",
        "layer": "reviewer_semantic",
        "stages": ["integration", "final_release"],
        "default_severity": "critical",
        "executor": "deterministic_and_reviewer",
        "description": "Accepted-with-limits constraints remain explicit in every downstream artifact and final prose.",
    },
    {
        "rule_id": "FINAL_REPORT_ENTAILMENT",
        "layer": "reviewer_semantic",
        "stages": ["final_release"],
        "default_severity": "critical",
        "executor": "deterministic_and_reviewer",
        "description": "Every material final passage is semantically entailed by accepted claims; synthesis adds no unsupported number, cause, novelty, or forecast wording.",
    },
    {
        "rule_id": "NOVELTY_AND_PUBLICATION_ADJUDICATION",
        "layer": "human_only",
        "stages": ["integration", "final_release"],
        "default_severity": "critical",
        "executor": "heterogeneous_or_human",
        "description": "Novelty priority, major significance, ethics, and top-journal competitiveness require independent adjudication.",
    },
)


def policy_registry(*, stage: str | None = None) -> dict[str, Any]:
    rules = [row for row in _RULES if stage is None or stage in row["stages"]]
    return {"policy_version": POLICY_VERSION, "rules": deepcopy(rules)}


__all__ = ["POLICY_VERSION", "policy_registry"]
