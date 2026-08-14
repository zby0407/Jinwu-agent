"""Dependency-free contracts for the integrated research review harness.

The v2 contracts are additive.  Existing producer v1 contracts remain the
authority for producer-local validation; adapters persist their outputs as
immutable v2 artifacts for cross-stage review.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from .policies import POLICY_VERSION

ARTIFACT_VERSION = "research-artifact-v2"
CLAIM_VERSION = "claim-evidence-v2"
VERDICT_VERSION = "review-verdict-v2"
RUN_STATE_VERSION = "research-run-state-v2"
REVISION_RESPONSE_VERSION = "revision-response-v2"
REVISION_CAPSULE_VERSION = "revision-capsule-v2"

STAGES = {
    "planning",
    "data",
    "hypothesis",
    "experiment_design",
    "experiment_result",
    "integration",
    "final_release",
}
REVIEW_MODES = STAGES
CLAIM_KINDS = {
    "fact",
    "observation",
    "inference",
    "mechanism",
    "prediction",
    "unknown",
}
CONFIDENCE_LEVELS = {"high", "medium", "low", "unknown"}
DECISIONS = {
    "accept",
    "accept_with_limits",
    "revise",
    "block",
}
SEVERITIES = {"critical", "major", "minor"}
ISSUE_OWNERS = {
    "solar-planner",
    "solar-data",
    "solar-hypothesis",
    "solar-experiment",
    "main",
}
RUN_STATUSES = {
    "active",
    "blocked",
    "release_ready",
    "released",
}

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ContractError(ValueError):
    """Raised before a malformed research-review payload is persisted."""


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
        raise ContractError("payload must contain only finite JSON values") from exc
    return hashlib.sha256(encoded).hexdigest()


def _clone(value: object, label: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} must contain only finite JSON values") from exc


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be a JSON object")
    return value


def _exact(value: dict[str, Any], fields: set[str], label: str) -> None:
    missing = sorted(fields - set(value))
    unknown = sorted(set(value) - fields)
    if missing:
        raise ContractError(f"{label} missing fields: {', '.join(missing)}")
    if unknown:
        raise ContractError(f"{label} has unknown fields: {', '.join(unknown)}")


def _text(
    value: object,
    label: str,
    *,
    minimum: int = 1,
    maximum: int = 20_000,
) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{label} must be a string")
    normalized = value.strip()
    if len(normalized) < minimum:
        raise ContractError(f"{label} must contain at least {minimum} characters")
    if len(normalized) > maximum:
        raise ContractError(f"{label} exceeds {maximum} characters")
    return normalized


def _id(value: object, label: str) -> str:
    normalized = _text(value, label, maximum=128)
    if SAFE_ID.fullmatch(normalized) is None:
        raise ContractError(f"{label} must be a safe id")
    return normalized


def _sha(value: object, label: str) -> str:
    normalized = _text(value, label, maximum=64)
    if SHA256.fullmatch(normalized) is None:
        raise ContractError(f"{label} must be a lowercase SHA-256")
    return normalized


def _enum(value: object, choices: set[str], label: str) -> str:
    normalized = _text(value, label, maximum=80)
    if normalized not in choices:
        raise ContractError(f"{label} must be one of: {sorted(choices)}")
    return normalized


def _integer(value: object, label: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{label} must be an integer")
    if value < minimum or value > maximum:
        raise ContractError(f"{label} must be in [{minimum}, {maximum}]")
    return value


def _text_list(
    value: object,
    label: str,
    *,
    maximum: int = 200,
    item_maximum: int = 4_000,
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ContractError(f"{label} must be an array with at most {maximum} items")
    result = [
        _text(item, f"{label}[{index}]", maximum=item_maximum)
        for index, item in enumerate(value)
    ]
    if len(result) != len(set(result)):
        raise ContractError(f"{label} must contain unique items")
    return result


def _timestamp(value: object, label: str) -> str:
    normalized = _text(value, label, maximum=64)
    try:
        datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ContractError(f"{label} must be an ISO-8601 timestamp") from exc
    return normalized


def _now() -> str:
    return datetime.now(UTC).isoformat()


def issue_fingerprint(rule_id: str, claim_ref: str, owner: str) -> str:
    """Return a stable identity for no-progress detection across revisions."""

    return canonical_json_sha256(
        {
            "rule_id": _id(rule_id, "rule_id"),
            "claim_ref": _text(claim_ref, "claim_ref", maximum=256),
            "owner": _id(owner, "owner"),
        }
    )


def _validate_claim(value: object, label: str) -> dict[str, Any]:
    claim = _object(value, label)
    required_fields = {
        "schema_version",
        "claim_id",
        "kind",
        "text",
        "scope",
        "supporting_evidence",
        "opposing_evidence",
        "confidence",
        "unknowns",
    }
    optional_fields = {"limiting_evidence"}
    missing = sorted(required_fields - set(claim))
    unknown = sorted(set(claim) - required_fields - optional_fields)
    if missing:
        raise ContractError(f"{label} missing fields: {', '.join(missing)}")
    if unknown:
        raise ContractError(f"{label} has unknown fields: {', '.join(unknown)}")
    if claim["schema_version"] != CLAIM_VERSION:
        raise ContractError(f"{label}.schema_version must be {CLAIM_VERSION}")
    return {
        "schema_version": CLAIM_VERSION,
        "claim_id": _id(claim["claim_id"], f"{label}.claim_id"),
        "kind": _enum(claim["kind"], CLAIM_KINDS, f"{label}.kind"),
        "text": _text(claim["text"], f"{label}.text", maximum=20_000),
        "scope": _text(claim["scope"], f"{label}.scope", maximum=4_000),
        "supporting_evidence": _text_list(
            claim["supporting_evidence"], f"{label}.supporting_evidence"
        ),
        "opposing_evidence": _text_list(
            claim["opposing_evidence"], f"{label}.opposing_evidence"
        ),
        "limiting_evidence": _text_list(
            claim.get("limiting_evidence", []), f"{label}.limiting_evidence"
        ),
        "confidence": _enum(
            claim["confidence"], CONFIDENCE_LEVELS, f"{label}.confidence"
        ),
        "unknowns": _text_list(claim["unknowns"], f"{label}.unknowns"),
    }


def validate_research_artifact(value: object) -> dict[str, Any]:
    artifact = _object(value, "research artifact")
    fields = {
        "schema_version",
        "artifact_id",
        "task_id",
        "stage",
        "version",
        "producer",
        "upstream_refs",
        "claims",
        "evidence_refs",
        "limitations",
        "payload",
        "created_at",
        "artifact_sha256",
    }
    _exact(artifact, fields, "research artifact")
    if artifact["schema_version"] != ARTIFACT_VERSION:
        raise ContractError(f"schema_version must be {ARTIFACT_VERSION}")
    claims_raw = artifact["claims"]
    if not isinstance(claims_raw, list) or len(claims_raw) > 200:
        raise ContractError("claims must be an array with at most 200 items")
    claims = [
        _validate_claim(item, f"claims[{index}]")
        for index, item in enumerate(claims_raw)
    ]
    claim_ids = [item["claim_id"] for item in claims]
    if len(claim_ids) != len(set(claim_ids)):
        raise ContractError("claim_id values must be unique")
    validated = {
        "schema_version": ARTIFACT_VERSION,
        "artifact_id": _id(artifact["artifact_id"], "artifact_id"),
        "task_id": _id(artifact["task_id"], "task_id"),
        "stage": _enum(artifact["stage"], STAGES, "stage"),
        "version": _integer(artifact["version"], "version", minimum=1, maximum=9999),
        "producer": _id(artifact["producer"], "producer"),
        "upstream_refs": _text_list(
            artifact["upstream_refs"], "upstream_refs", item_maximum=256
        ),
        "claims": claims,
        "evidence_refs": _text_list(
            artifact["evidence_refs"], "evidence_refs", item_maximum=1_000
        ),
        "limitations": _text_list(artifact["limitations"], "limitations"),
        "payload": _clone(artifact["payload"], "payload"),
        "created_at": _timestamp(artifact["created_at"], "created_at"),
        "artifact_sha256": _sha(artifact["artifact_sha256"], "artifact_sha256"),
    }
    unhashed = dict(validated)
    declared = unhashed.pop("artifact_sha256")
    if canonical_json_sha256(unhashed) != declared:
        raise ContractError("artifact_sha256 does not match the artifact")
    return validated


def build_research_artifact(
    *,
    artifact_id: str,
    task_id: str,
    stage: str,
    version: int,
    producer: str,
    upstream_refs: list[str] | None = None,
    claims: list[dict[str, Any]] | None = None,
    evidence_refs: list[str] | None = None,
    limitations: list[str] | None = None,
    payload: object,
    created_at: str | None = None,
) -> dict[str, Any]:
    artifact = {
        "schema_version": ARTIFACT_VERSION,
        "artifact_id": artifact_id,
        "task_id": task_id,
        "stage": stage,
        "version": version,
        "producer": producer,
        "upstream_refs": upstream_refs or [],
        "claims": [
            _validate_claim(item, f"claims[{index}]")
            for index, item in enumerate(claims or [])
        ],
        "evidence_refs": evidence_refs or [],
        "limitations": limitations or [],
        "payload": payload,
        "created_at": created_at or _now(),
    }
    artifact["artifact_sha256"] = canonical_json_sha256(artifact)
    return validate_research_artifact(artifact)


def _validate_issue(value: object, label: str) -> dict[str, Any]:
    issue = _object(value, label)
    fields = {
        "issue_id",
        "rule_id",
        "severity",
        "claim_ref",
        "evidence_refs",
        "owner",
        "message",
        "required_action",
        "acceptance_test",
        "fingerprint",
    }
    _exact(issue, fields, label)
    validated = {
        "issue_id": _id(issue["issue_id"], f"{label}.issue_id"),
        "rule_id": _id(issue["rule_id"], f"{label}.rule_id"),
        "severity": _enum(issue["severity"], SEVERITIES, f"{label}.severity"),
        "claim_ref": _text(issue["claim_ref"], f"{label}.claim_ref", maximum=256),
        "evidence_refs": _text_list(
            issue["evidence_refs"], f"{label}.evidence_refs", item_maximum=1_000
        ),
        "owner": _enum(issue["owner"], ISSUE_OWNERS, f"{label}.owner"),
        "message": _text(issue["message"], f"{label}.message", maximum=4_000),
        "required_action": _text(
            issue["required_action"], f"{label}.required_action", maximum=4_000
        ),
        "acceptance_test": _text(
            issue["acceptance_test"], f"{label}.acceptance_test", maximum=4_000
        ),
        "fingerprint": _sha(issue["fingerprint"], f"{label}.fingerprint"),
    }
    expected = issue_fingerprint(
        validated["rule_id"], validated["claim_ref"], validated["owner"]
    )
    if validated["fingerprint"] != expected:
        raise ContractError(f"{label}.fingerprint does not match issue identity")
    return validated


def _artifact_ref(value: object, label: str) -> dict[str, Any]:
    ref = _object(value, label)
    _exact(ref, {"artifact_id", "version", "artifact_sha256"}, label)
    return {
        "artifact_id": _id(ref["artifact_id"], f"{label}.artifact_id"),
        "version": _integer(
            ref["version"], f"{label}.version", minimum=1, maximum=9999
        ),
        "artifact_sha256": _sha(ref["artifact_sha256"], f"{label}.artifact_sha256"),
    }


def validate_review_verdict(value: object) -> dict[str, Any]:
    verdict = _object(value, "review verdict")
    fields = {
        "schema_version",
        "review_id",
        "task_id",
        "review_mode",
        "artifact_refs",
        "policy_version",
        "round",
        "decision",
        "issues",
        "accepted_claims",
        "blocked_claims",
        "carry_forward_limits",
        "next_owner",
        "reviewer_context",
        "created_at",
        "verdict_sha256",
    }
    _exact(verdict, fields, "review verdict")
    if verdict["schema_version"] != VERDICT_VERSION:
        raise ContractError(f"schema_version must be {VERDICT_VERSION}")
    refs_raw = verdict["artifact_refs"]
    if not isinstance(refs_raw, list) or not 1 <= len(refs_raw) <= 20:
        raise ContractError("artifact_refs must contain 1 to 20 items")
    refs = [
        _artifact_ref(item, f"artifact_refs[{i}]") for i, item in enumerate(refs_raw)
    ]
    issues_raw = verdict["issues"]
    if not isinstance(issues_raw, list) or len(issues_raw) > 200:
        raise ContractError("issues must be an array with at most 200 items")
    issues = [
        _validate_issue(item, f"issues[{i}]") for i, item in enumerate(issues_raw)
    ]
    issue_ids = [item["issue_id"] for item in issues]
    if len(issue_ids) != len(set(issue_ids)):
        raise ContractError("issue_id values must be unique")
    validated = {
        "schema_version": VERDICT_VERSION,
        "review_id": _id(verdict["review_id"], "review_id"),
        "task_id": _id(verdict["task_id"], "task_id"),
        "review_mode": _enum(verdict["review_mode"], REVIEW_MODES, "review_mode"),
        "artifact_refs": refs,
        "policy_version": _id(verdict["policy_version"], "policy_version"),
        "round": _integer(verdict["round"], "round", minimum=1, maximum=9999),
        "decision": _enum(verdict["decision"], DECISIONS, "decision"),
        "issues": issues,
        "accepted_claims": _text_list(
            verdict["accepted_claims"], "accepted_claims", item_maximum=256
        ),
        "blocked_claims": _text_list(
            verdict["blocked_claims"], "blocked_claims", item_maximum=256
        ),
        "carry_forward_limits": _text_list(
            verdict["carry_forward_limits"], "carry_forward_limits"
        ),
        "next_owner": (
            None
            if verdict["next_owner"] is None
            else _enum(verdict["next_owner"], ISSUE_OWNERS, "next_owner")
        ),
        "reviewer_context": _enum(
            verdict["reviewer_context"], {"isolated"}, "reviewer_context"
        ),
        "created_at": _timestamp(verdict["created_at"], "created_at"),
        "verdict_sha256": _sha(verdict["verdict_sha256"], "verdict_sha256"),
    }
    major = any(issue["severity"] in {"critical", "major"} for issue in issues)
    critical = any(issue["severity"] == "critical" for issue in issues)
    decision = validated["decision"]
    if decision == "accept" and (major or validated["blocked_claims"]):
        raise ContractError("accept cannot retain critical/major or blocked claims")
    if decision == "accept_with_limits" and (
        critical or not validated["carry_forward_limits"]
    ):
        raise ContractError(
            "accept_with_limits requires limits and cannot retain critical issues"
        )
    if decision == "revise" and (not issues or validated["next_owner"] is None):
        raise ContractError("revise requires issues and next_owner")
    if decision == "revise" and not any(
        issue["owner"] == validated["next_owner"] for issue in issues
    ):
        raise ContractError("next_owner must own at least one revision issue")
    if decision == "block" and not issues:
        raise ContractError(f"{decision} requires at least one issue")
    unhashed = dict(validated)
    declared = unhashed.pop("verdict_sha256")
    if canonical_json_sha256(unhashed) != declared:
        raise ContractError("verdict_sha256 does not match the verdict")
    return validated


def build_review_verdict(
    *,
    review_id: str,
    task_id: str,
    review_mode: str,
    artifact_refs: list[dict[str, Any]],
    round_number: int,
    decision: str,
    issues: list[dict[str, Any]],
    accepted_claims: list[str] | None = None,
    blocked_claims: list[str] | None = None,
    carry_forward_limits: list[str] | None = None,
    next_owner: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    verdict = {
        "schema_version": VERDICT_VERSION,
        "review_id": review_id,
        "task_id": task_id,
        "review_mode": review_mode,
        "artifact_refs": artifact_refs,
        "policy_version": POLICY_VERSION,
        "round": round_number,
        "decision": decision,
        "issues": issues,
        "accepted_claims": accepted_claims or [],
        "blocked_claims": blocked_claims or [],
        "carry_forward_limits": carry_forward_limits or [],
        "next_owner": next_owner,
        "reviewer_context": "isolated",
        "created_at": created_at or _now(),
    }
    verdict["verdict_sha256"] = canonical_json_sha256(verdict)
    return validate_review_verdict(verdict)


def validate_revision_response(value: object) -> dict[str, Any]:
    response = _object(value, "revision response")
    fields = {
        "schema_version",
        "task_id",
        "stage",
        "producer",
        "artifact_version",
        "prior_review_ref",
        "issue_responses",
    }
    _exact(response, fields, "revision response")
    if response["schema_version"] != REVISION_RESPONSE_VERSION:
        raise ContractError(
            f"revision response schema_version must be {REVISION_RESPONSE_VERSION}"
        )
    prior = _object(response["prior_review_ref"], "prior_review_ref")
    _exact(prior, {"review_id", "verdict_sha256"}, "prior_review_ref")
    rows = response["issue_responses"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
        raise ContractError("issue_responses must contain 1 to 200 items")
    normalized_rows: list[dict[str, Any]] = []
    issue_ids: set[str] = set()
    for index, raw in enumerate(rows):
        label = f"issue_responses[{index}]"
        row = _object(raw, label)
        _exact(
            row,
            {"issue_id", "fingerprint", "status", "response", "acceptance_evidence"},
            label,
        )
        issue_id = _id(row["issue_id"], f"{label}.issue_id")
        if issue_id in issue_ids:
            raise ContractError("issue_responses must contain unique issue_id values")
        issue_ids.add(issue_id)
        normalized_rows.append(
            {
                "issue_id": issue_id,
                "fingerprint": _sha(row["fingerprint"], f"{label}.fingerprint"),
                "status": _enum(
                    row["status"],
                    {"resubmitted", "unable", "disputed"},
                    f"{label}.status",
                ),
                "response": _text(row["response"], f"{label}.response", maximum=4_000),
                "acceptance_evidence": _text_list(
                    row["acceptance_evidence"],
                    f"{label}.acceptance_evidence",
                    item_maximum=1_000,
                ),
            }
        )
    return {
        "schema_version": REVISION_RESPONSE_VERSION,
        "task_id": _id(response["task_id"], "task_id"),
        "stage": _enum(response["stage"], STAGES, "stage"),
        "producer": _id(response["producer"], "producer"),
        "artifact_version": _integer(
            response["artifact_version"], "artifact_version", minimum=1, maximum=9999
        ),
        "prior_review_ref": {
            "review_id": _id(prior["review_id"], "prior_review_ref.review_id"),
            "verdict_sha256": _sha(
                prior["verdict_sha256"], "prior_review_ref.verdict_sha256"
            ),
        },
        "issue_responses": normalized_rows,
    }


def build_revision_response(
    *,
    task_id: str,
    stage: str,
    producer: str,
    artifact_version: int,
    prior_verdict: dict[str, Any],
    acceptance_evidence: list[str],
) -> dict[str, Any]:
    issues = prior_verdict.get("issues")
    if prior_verdict.get("decision") != "revise" or not isinstance(issues, list):
        raise ContractError("revision response requires a prior revise verdict")
    return validate_revision_response(
        {
            "schema_version": REVISION_RESPONSE_VERSION,
            "task_id": task_id,
            "stage": stage,
            "producer": producer,
            "artifact_version": artifact_version,
            "prior_review_ref": {
                "review_id": prior_verdict.get("review_id"),
                "verdict_sha256": prior_verdict.get("verdict_sha256"),
            },
            "issue_responses": [
                {
                    "issue_id": issue.get("issue_id"),
                    "fingerprint": issue.get("fingerprint"),
                    "status": "resubmitted",
                    "response": (
                        "A new immutable producer artifact was submitted; the Evidence "
                        "Reviewer must rerun the stated acceptance test before closure."
                    ),
                    "acceptance_evidence": acceptance_evidence,
                }
                for issue in issues
                if isinstance(issue, dict) and issue.get("owner") == producer
            ],
        }
    )


def validate_revision_capsule(value: object) -> dict[str, Any]:
    """Validate the compact, hash-bound feedback passed to a producer."""

    capsule = _object(value, "revision capsule")
    fields = {
        "schema_version",
        "task_id",
        "review_mode",
        "review_id",
        "verdict_sha256",
        "policy_version",
        "artifact_refs",
        "round",
        "owner",
        "unresolved_issues",
        "carry_forward_limits",
        "do_not_reopen_claims",
        "instruction",
        "capsule_sha256",
    }
    _exact(capsule, fields, "revision capsule")
    if capsule["schema_version"] != REVISION_CAPSULE_VERSION:
        raise ContractError(
            f"revision capsule schema_version must be {REVISION_CAPSULE_VERSION}"
        )
    refs_raw = capsule["artifact_refs"]
    if not isinstance(refs_raw, list) or not 1 <= len(refs_raw) <= 20:
        raise ContractError("revision capsule artifact_refs must contain 1 to 20 items")
    issues_raw = capsule["unresolved_issues"]
    if not isinstance(issues_raw, list) or not 1 <= len(issues_raw) <= 200:
        raise ContractError(
            "revision capsule unresolved_issues must contain 1 to 200 items"
        )
    issues = [
        _validate_issue(item, f"unresolved_issues[{index}]")
        for index, item in enumerate(issues_raw)
    ]
    owner = _enum(capsule["owner"], ISSUE_OWNERS, "owner")
    if any(issue["owner"] != owner for issue in issues):
        raise ContractError("revision capsule may contain only its owner's issues")
    validated = {
        "schema_version": REVISION_CAPSULE_VERSION,
        "task_id": _id(capsule["task_id"], "task_id"),
        "review_mode": _enum(capsule["review_mode"], REVIEW_MODES, "review_mode"),
        "review_id": _id(capsule["review_id"], "review_id"),
        "verdict_sha256": _sha(capsule["verdict_sha256"], "verdict_sha256"),
        "policy_version": _id(capsule["policy_version"], "policy_version"),
        "artifact_refs": [
            _artifact_ref(item, f"artifact_refs[{index}]")
            for index, item in enumerate(refs_raw)
        ],
        "round": _integer(capsule["round"], "round", minimum=1, maximum=9999),
        "owner": owner,
        "unresolved_issues": issues,
        "carry_forward_limits": _text_list(
            capsule["carry_forward_limits"], "carry_forward_limits"
        ),
        "do_not_reopen_claims": _text_list(
            capsule["do_not_reopen_claims"],
            "do_not_reopen_claims",
            item_maximum=256,
        ),
        "instruction": _text(capsule["instruction"], "instruction", maximum=1_000),
        "capsule_sha256": _sha(capsule["capsule_sha256"], "capsule_sha256"),
    }
    unhashed = dict(validated)
    declared = unhashed.pop("capsule_sha256")
    if canonical_json_sha256(unhashed) != declared:
        raise ContractError("capsule_sha256 does not match the revision capsule")
    return validated


def build_revision_capsule(
    *, prior_verdict: dict[str, Any], owner: str
) -> dict[str, Any]:
    """Build a compact feedback view while the full verdict remains authoritative."""

    verdict = validate_review_verdict(prior_verdict)
    if verdict["decision"] != "revise":
        raise ContractError("revision capsule requires a revise verdict")
    issues = [issue for issue in verdict["issues"] if issue["owner"] == owner]
    if not issues:
        raise ContractError("revision capsule owner has no unresolved issue")
    capsule = {
        "schema_version": REVISION_CAPSULE_VERSION,
        "task_id": verdict["task_id"],
        "review_mode": verdict["review_mode"],
        "review_id": verdict["review_id"],
        "verdict_sha256": verdict["verdict_sha256"],
        "policy_version": verdict["policy_version"],
        "artifact_refs": verdict["artifact_refs"],
        "round": verdict["round"],
        "owner": owner,
        "unresolved_issues": issues,
        "carry_forward_limits": verdict["carry_forward_limits"],
        "do_not_reopen_claims": verdict["accepted_claims"],
        "instruction": (
            "Resolve only these fingerprints, preserve accepted and unchanged work, "
            "and stop after persisting one new immutable artifact. If an acceptance "
            "test cannot be met from inspected evidence, report the blocker honestly."
        ),
    }
    capsule["capsule_sha256"] = canonical_json_sha256(capsule)
    return validate_revision_capsule(capsule)


def validate_run_state(value: object) -> dict[str, Any]:
    state = _object(value, "research run state")
    fields = {
        "schema_version",
        "task_id",
        "revision_policy",
        "max_revisions",
        "no_progress_patience",
        "budget_multiplier",
        "action_invocations",
        "max_action_invocations",
        "review_invocations",
        "max_review_invocations",
        "status",
        "current_stage",
        "artifacts",
        "verdicts",
        "stage_status",
        "dependency_graph",
        "updated_at",
    }
    _exact(state, fields, "research run state")
    if state["schema_version"] != RUN_STATE_VERSION:
        raise ContractError(f"schema_version must be {RUN_STATE_VERSION}")
    revision_policy = _enum(
        state["revision_policy"], {"adaptive", "fixed"}, "revision_policy"
    )
    max_revisions = _integer(
        state["max_revisions"], "max_revisions", minimum=0, maximum=100
    )
    if revision_policy == "fixed" and max_revisions < 1:
        raise ContractError("fixed revision policy requires max_revisions >= 1")
    current_stage = state["current_stage"]
    if current_stage is not None:
        current_stage = _enum(current_stage, STAGES, "current_stage")
    artifacts = _text_list(state["artifacts"], "artifacts", item_maximum=1_000)
    verdicts = _text_list(state["verdicts"], "verdicts", item_maximum=1_000)
    stage_status = _object(state["stage_status"], "stage_status")
    unknown_stages = sorted(set(stage_status) - STAGES)
    if unknown_stages:
        raise ContractError(
            f"stage_status has unknown stages: {', '.join(unknown_stages)}"
        )
    normalized_status: dict[str, str] = {}
    for stage, status in stage_status.items():
        normalized_status[stage] = _enum(
            status,
            {
                "pending",
                "produced",
                "accepted",
                "accepted_with_limits",
                "revise",
                "blocked",
            },
            f"stage_status.{stage}",
        )
    dependency_graph = _object(state["dependency_graph"], "dependency_graph")
    _exact(
        dependency_graph,
        {"schema_version", "source_ref", "stage_dependencies", "planner_steps"},
        "dependency_graph",
    )
    if dependency_graph["schema_version"] != "research-dependency-graph-v2":
        raise ContractError(
            "dependency_graph.schema_version must be research-dependency-graph-v2"
        )
    source_ref = dependency_graph["source_ref"]
    if source_ref is not None:
        source_ref = _text(source_ref, "dependency_graph.source_ref", maximum=1_000)
    stage_dependencies = _object(
        dependency_graph["stage_dependencies"],
        "dependency_graph.stage_dependencies",
    )
    if set(stage_dependencies) != STAGES:
        raise ContractError("dependency_graph.stage_dependencies must name every stage")
    normalized_dependencies: dict[str, list[str]] = {}
    for stage, dependencies in stage_dependencies.items():
        normalized_dependencies[stage] = [
            _enum(item, STAGES, f"dependency_graph.stage_dependencies.{stage}")
            for item in _text_list(
                dependencies,
                f"dependency_graph.stage_dependencies.{stage}",
                maximum=len(STAGES),
                item_maximum=80,
            )
        ]
        if stage in normalized_dependencies[stage]:
            raise ContractError(
                f"dependency graph stage {stage} cannot depend on itself"
            )
    planner_steps_raw = dependency_graph["planner_steps"]
    if not isinstance(planner_steps_raw, list) or len(planner_steps_raw) > 30:
        raise ContractError(
            "dependency_graph.planner_steps must contain at most 30 items"
        )
    planner_steps: list[dict[str, Any]] = []
    planner_ids: set[str] = set()
    for index, raw in enumerate(planner_steps_raw):
        label = f"dependency_graph.planner_steps[{index}]"
        step = _object(raw, label)
        _exact(step, {"step_id", "stage", "prerequisite_step_ids"}, label)
        step_id = _id(step["step_id"], f"{label}.step_id")
        if step_id in planner_ids:
            raise ContractError("dependency_graph.planner_steps has duplicate step_id")
        planner_ids.add(step_id)
        planner_steps.append(
            {
                "step_id": step_id,
                "stage": _id(step["stage"], f"{label}.stage"),
                "prerequisite_step_ids": _text_list(
                    step["prerequisite_step_ids"],
                    f"{label}.prerequisite_step_ids",
                    maximum=30,
                    item_maximum=128,
                ),
            }
        )
    for step in planner_steps:
        unknown = sorted(set(step["prerequisite_step_ids"]) - planner_ids)
        if unknown:
            raise ContractError(
                "dependency_graph planner step has unknown prerequisites: "
                + ", ".join(unknown)
            )
    return {
        "schema_version": RUN_STATE_VERSION,
        "task_id": _id(state["task_id"], "task_id"),
        "revision_policy": revision_policy,
        "max_revisions": max_revisions,
        "no_progress_patience": _integer(
            state["no_progress_patience"],
            "no_progress_patience",
            minimum=1,
            maximum=20,
        ),
        "budget_multiplier": _integer(
            state["budget_multiplier"], "budget_multiplier", minimum=1, maximum=20
        ),
        "action_invocations": _integer(
            state["action_invocations"], "action_invocations", minimum=0, maximum=10000
        ),
        "max_action_invocations": _integer(
            state["max_action_invocations"],
            "max_action_invocations",
            minimum=1,
            maximum=10000,
        ),
        "review_invocations": _integer(
            state["review_invocations"], "review_invocations", minimum=0, maximum=10000
        ),
        "max_review_invocations": _integer(
            state["max_review_invocations"],
            "max_review_invocations",
            minimum=1,
            maximum=10000,
        ),
        "status": _enum(state["status"], RUN_STATUSES, "status"),
        "current_stage": current_stage,
        "artifacts": artifacts,
        "verdicts": verdicts,
        "stage_status": normalized_status,
        "dependency_graph": {
            "schema_version": "research-dependency-graph-v2",
            "source_ref": source_ref,
            "stage_dependencies": normalized_dependencies,
            "planner_steps": planner_steps,
        },
        "updated_at": _timestamp(state["updated_at"], "updated_at"),
    }


__all__ = [
    "ARTIFACT_VERSION",
    "CLAIM_VERSION",
    "POLICY_VERSION",
    "REVISION_CAPSULE_VERSION",
    "REVISION_RESPONSE_VERSION",
    "RUN_STATE_VERSION",
    "VERDICT_VERSION",
    "ContractError",
    "build_research_artifact",
    "build_review_verdict",
    "build_revision_capsule",
    "build_revision_response",
    "canonical_json_sha256",
    "issue_fingerprint",
    "validate_research_artifact",
    "validate_review_verdict",
    "validate_revision_capsule",
    "validate_revision_response",
    "validate_run_state",
]
