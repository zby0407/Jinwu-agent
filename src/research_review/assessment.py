"""ReviewAssessmentV1: per-claim structured sidecar for the two-pass Evidence review.

This module is additive.  ReviewVerdictV2 remains the routing authority
(accept / accept_with_limits / revise / block); an assessment records, for every
reviewed claim, the evidence-for/evidence-against picture and the single most
discriminating next test, without feeding the verdict validator or the
no-progress fingerprint machinery.
"""

from __future__ import annotations

from typing import Any

from .contracts import (
    CLAIM_KINDS,
    CONFIDENCE_LEVELS,
    REVIEW_MODES,
    ContractError,
    _artifact_ref,
    _enum,
    _exact,
    _id,
    _integer,
    _object,
    _sha,
    _text,
    _text_list,
    _timestamp,
    canonical_json_sha256,
)

ASSESSMENT_VERSION = "review-assessment-v1"

# Per-claim disposition for the assessed producer claim.  Distinct from the
# verdict-level decision: a claim can be opposed while the artifact still
# routes to accept_with_limits on another claim's strength.
CLAIM_DISPOSITIONS = {
    "supported",
    "limited_support",
    "opposed",
    "contradicted",
    "undecided",
}

# Which pass surfaced the assessment: closed (stage-1 closed-book review of the
# producer's own evidence) or two_pass (stage-2 active falsification pass that
# may consult the local literature cache and bounded public web lookups).
ASSESSMENT_REVIEW_MODES = {"closed", "two_pass"}


def _validate_assessment_claim(value: object, label: str) -> dict[str, Any]:
    claim = _object(value, label)
    fields = {
        "claim_id",
        "kind",
        "disposition",
        "supporting_evidence",
        "opposing_evidence",
        "rationale",
        "key_uncertainty",
        "confidence",
        "next_test",
    }
    _exact(claim, fields, label)
    return {
        "claim_id": _id(claim["claim_id"], f"{label}.claim_id"),
        "kind": _enum(claim["kind"], CLAIM_KINDS, f"{label}.kind"),
        "disposition": _enum(
            claim["disposition"], CLAIM_DISPOSITIONS, f"{label}.disposition"
        ),
        "supporting_evidence": _text_list(
            claim["supporting_evidence"], f"{label}.supporting_evidence"
        ),
        "opposing_evidence": _text_list(
            claim["opposing_evidence"], f"{label}.opposing_evidence"
        ),
        "rationale": _text(claim["rationale"], f"{label}.rationale", maximum=4_000),
        "key_uncertainty": _text(
            claim["key_uncertainty"], f"{label}.key_uncertainty", maximum=2_000
        ),
        "confidence": _enum(
            claim["confidence"], CONFIDENCE_LEVELS, f"{label}.confidence"
        ),
        "next_test": _text(claim["next_test"], f"{label}.next_test", maximum=2_000),
    }


def validate_review_assessment(value: object) -> dict[str, Any]:
    assessment = _object(value, "review assessment")
    fields = {
        "schema_version",
        "assessment_id",
        "task_id",
        "review_mode",
        "assessment_review_mode",
        "artifact_refs",
        "policy_version",
        "round",
        "claims",
        "created_at",
        "assessment_sha256",
    }
    _exact(assessment, fields, "review assessment")
    if assessment["schema_version"] != ASSESSMENT_VERSION:
        raise ContractError(f"schema_version must be {ASSESSMENT_VERSION}")
    refs_raw = assessment["artifact_refs"]
    if not isinstance(refs_raw, list) or not 1 <= len(refs_raw) <= 20:
        raise ContractError("artifact_refs must contain 1 to 20 items")
    refs = [
        _artifact_ref(item, f"artifact_refs[{i}]") for i, item in enumerate(refs_raw)
    ]
    claims_raw = assessment["claims"]
    if not isinstance(claims_raw, list) or not 1 <= len(claims_raw) <= 200:
        raise ContractError("claims must contain 1 to 200 items")
    claims = [
        _validate_assessment_claim(item, f"claims[{i}]")
        for i, item in enumerate(claims_raw)
    ]
    claim_ids = [item["claim_id"] for item in claims]
    if len(claim_ids) != len(set(claim_ids)):
        raise ContractError("claim_id values must be unique")
    validated = {
        "schema_version": ASSESSMENT_VERSION,
        "assessment_id": _id(assessment["assessment_id"], "assessment_id"),
        "task_id": _id(assessment["task_id"], "task_id"),
        "review_mode": _enum(assessment["review_mode"], REVIEW_MODES, "review_mode"),
        "assessment_review_mode": _enum(
            assessment["assessment_review_mode"],
            ASSESSMENT_REVIEW_MODES,
            "assessment_review_mode",
        ),
        "artifact_refs": refs,
        "policy_version": _id(assessment["policy_version"], "policy_version"),
        "round": _integer(assessment["round"], "round", minimum=1, maximum=9999),
        "claims": claims,
        "created_at": _timestamp(assessment["created_at"], "created_at"),
        "assessment_sha256": _sha(
            assessment["assessment_sha256"], "assessment_sha256"
        ),
    }
    unhashed = dict(validated)
    declared = unhashed.pop("assessment_sha256")
    if canonical_json_sha256(unhashed) != declared:
        raise ContractError("assessment_sha256 does not match the assessment")
    return validated


def build_review_assessment(
    *,
    assessment_id: str,
    task_id: str,
    review_mode: str,
    assessment_review_mode: str,
    artifact_refs: list[dict[str, Any]],
    policy_version: str,
    round: int,
    claims: list[dict[str, Any]],
    created_at: str,
) -> dict[str, Any]:
    """Assemble and validate a ReviewAssessmentV1, computing its content hash."""

    payload = {
        "schema_version": ASSESSMENT_VERSION,
        "assessment_id": assessment_id,
        "task_id": task_id,
        "review_mode": review_mode,
        "assessment_review_mode": assessment_review_mode,
        "artifact_refs": artifact_refs,
        "policy_version": policy_version,
        "round": round,
        "claims": claims,
        "created_at": created_at,
    }
    payload["assessment_sha256"] = canonical_json_sha256(payload)
    return validate_review_assessment(payload)
