"""Typed tools for the independent Evidence Reviewer and final release gate."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from jw.research_review import ALL_REVIEW_MODES, store_from_config
from research_review.contracts import issue_fingerprint

from .registry import register_tool_bundle

logger = logging.getLogger(__name__)


class AssessmentClaimInput(BaseModel):
    """One ReviewAssessmentV1 row for one exact artifact claim id."""

    model_config = ConfigDict(extra="forbid")
    claim_id: str
    kind: str
    disposition: Literal[
        "supported", "limited_support", "opposed", "contradicted", "undecided"
    ]
    supporting_evidence: list[str]
    opposing_evidence: list[str]
    rationale: str
    key_uncertainty: str
    confidence: Literal["unknown", "low", "medium", "high"]
    next_test: str


class EvidenceMatrixRowInput(BaseModel):
    """One locator-level evidence relationship."""

    model_config = ConfigDict(extra="forbid")
    source_ref: str | None
    evidence_role: Literal["supports", "opposes", "limits", "gap"]
    source_class: Literal[
        "direct_observation",
        "real_experiment",
        "simulation",
        "method_paper",
        "review",
        "data_documentation",
        "user_premise",
        "wiki_context",
        "unknown",
    ]
    evidence_scope: Literal[
        "full_text",
        "abstract_only",
        "dataset_record",
        "experiment_record",
        "user_statement",
        "wiki_entry",
        "unknown",
    ]
    directness: Literal["direct", "indirect", "context_only", "not_assessable"]
    scope_match: Literal["matched", "partial", "mismatch", "not_assessable"]
    independence_group: str
    locator: str
    entailment: Literal["entailed", "partial", "not_entailed", "not_assessable"]
    quality_cap: Literal["exploratory", "evidence_constrained", "release_candidate"]
    rationale: str


class MethodAssessmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    design_status: Literal[
        "valid", "limited", "invalid", "not_applicable", "not_assessed"
    ]
    independent_sample_unit: str
    independent_sample_count: int | None
    validation_status: Literal[
        "valid", "limited", "invalid", "not_applicable", "not_assessed"
    ]
    uncertainty_status: Literal[
        "valid", "limited", "invalid", "not_applicable", "not_assessed"
    ]
    reproducibility_status: Literal[
        "valid", "limited", "invalid", "not_applicable", "not_assessed"
    ]
    notes: str


class NearestPriorArtInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_ref: str
    existing_claim: str
    overlap: str
    difference: str
    duplication_risk: str


class NoveltyAssessmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal[
        "known_baseline",
        "incremental_extension",
        "potentially_novel",
        "novelty_not_assessed",
        "not_applicable",
    ]
    contribution_type: Literal[
        "known_baseline",
        "mechanism_extension",
        "new_prediction",
        "new_data_linkage",
        "new_method_application",
        "measurement_or_null_explanation",
        "not_assessed",
    ]
    novelty_delta: str
    nearest_prior_art: list[NearestPriorArtInput]
    query_axes: list[str]
    searched_family_count: int = Field(ge=0)
    search_cutoff: str | None
    coverage_gaps: list[str]


class ScientificQualityClaimInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: str
    claim_component: Literal[
        "statement",
        "mechanism",
        "prediction",
        "scope",
        "numeric_result",
        "conclusion",
        "workflow_status",
    ]
    load_bearing: bool
    evidence_matrix: list[EvidenceMatrixRowInput] = Field(min_length=1)
    method_assessment: MethodAssessmentInput
    novelty_assessment: NoveltyAssessmentInput
    conclusion_cap: Literal["exploratory", "evidence_constrained", "release_candidate"]
    quality_status: Literal[
        "release_candidate",
        "evidence_constrained",
        "exploratory",
        "blocked",
        "workflow_status",
    ]
    key_gaps: list[str]


class VerdictIssueInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_id: str | None = None
    rule_id: str
    severity: Literal["critical", "major", "minor"]
    claim_ref: str
    evidence_refs: list[str]
    owner: Literal[
        "solar-planner",
        "solar-data",
        "solar-hypothesis",
        "solar-experiment",
        "main",
    ]
    message: str
    required_action: str
    acceptance_test: str


def _configured_assessment_review_mode() -> str:
    mode = os.environ.get("JW_EVIDENCE_REVIEW_MODE", "two_pass").strip().lower()
    return mode if mode in {"closed", "two_pass"} else "two_pass"


def _require_current_assessment(store: Any, review_mode: str) -> dict[str, Any]:
    """Return the one current-round assessment bound to current targets."""

    round_number = len(store.verdicts(mode=review_mode)) + 1
    rows = [
        row
        for row in store.assessments(mode=review_mode)
        if row["round"] == round_number
    ]
    if len(rows) != 1:
        raise ValueError(
            "record exactly one ReviewAssessmentV1 for the current review round "
            "before submitting the verdict"
        )
    assessment = rows[0]
    configured_mode = _configured_assessment_review_mode()
    if assessment["assessment_review_mode"] != configured_mode:
        raise ValueError(
            "assessment_review_mode does not match the configured Evidence mode: "
            f"expected {configured_mode}"
        )
    current_refs = [
        store.artifact_ref(item) for item in store.review_targets(review_mode)
    ]
    if assessment["artifact_refs"] != current_refs:
        raise ValueError(
            "the recorded assessment does not match the current review artifacts"
        )
    return assessment


def _require_current_scientific_quality(store: Any, review_mode: str) -> dict[str, Any]:
    round_number = len(store.verdicts(mode=review_mode)) + 1
    rows = [
        row
        for row in store.scientific_quality_assessments(mode=review_mode)
        if row["round"] == round_number
    ]
    if len(rows) != 1:
        raise ValueError(
            "record exactly one ScientificQualityAssessmentV1 for the current "
            "review round before submitting the verdict"
        )
    assessment = rows[0]
    configured_mode = _configured_assessment_review_mode()
    if assessment["assessment_review_mode"] != configured_mode:
        raise ValueError(
            "scientific quality assessment mode does not match configured Evidence "
            f"mode: expected {configured_mode}"
        )
    refs = [store.artifact_ref(item) for item in store.review_targets(review_mode)]
    if assessment["artifact_refs"] != refs:
        raise ValueError(
            "scientific quality assessment does not match current review artifacts"
        )
    return assessment


def _json_arg(value: Any, label: str, expected: type) -> Any:
    if isinstance(value, expected):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label} must be valid JSON") from exc
        if isinstance(parsed, expected):
            return parsed
    raise ValueError(
        f"{label} must be {expected.__name__} or JSON-encoded {expected.__name__}"
    )


def _normalize_assessment_claims(
    value: Any, label: str = "assessment_claims"
) -> list[Any]:
    """Do not describe an evidence-free claim as supported."""

    normalized: list[Any] = []
    for raw in _json_arg(value, label, list):
        row = raw.model_dump() if isinstance(raw, BaseModel) else raw
        if not isinstance(row, dict):
            normalized.append(row)
            continue
        row = dict(row)
        if row.get("disposition") in {"supported", "limited_support"} and not row.get(
            "supporting_evidence"
        ):
            row["disposition"] = "undecided"
        normalized.append(row)
    return normalized


def _ok(payload: object) -> str:
    return json.dumps({"ok": True, "result": payload}, ensure_ascii=False)


def _error(exc: Exception) -> str:
    return json.dumps(
        {
            "ok": False,
            "status": "error",
            "error_type": type(exc).__name__,
            "message": str(exc),
        },
        ensure_ascii=False,
    )


def _normalize_issues(value: Any) -> list[dict[str, Any]]:
    rows = _json_arg(value, "issues", list)
    normalized: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str]] = set()
    for index, item in enumerate(rows, start=1):
        if isinstance(item, BaseModel):
            item = item.model_dump()
        if not isinstance(item, dict):
            raise ValueError(f"issues[{index - 1}] must be an object")
        row = dict(item)
        if not isinstance(row.get("issue_id"), str) or not row["issue_id"].strip():
            row["issue_id"] = f"issue-{index:03d}"
        for field in (
            "rule_id",
            "severity",
            "claim_ref",
            "owner",
            "message",
            "required_action",
            "acceptance_test",
        ):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"issues[{index - 1}].{field} is required")
        row.setdefault("evidence_refs", [])
        identity = (row["rule_id"], row["claim_ref"], row["owner"])
        if identity in identities:
            raise ValueError(
                f"issues[{index - 1}] duplicates a rule_id/claim_ref/owner identity; "
                "use a stable field-level claim_ref such as "
                "artifact-id#plan_content.section.item.field for each distinct defect"
            )
        identities.add(identity)
        row["fingerprint"] = issue_fingerprint(
            row["rule_id"], row["claim_ref"], row["owner"]
        )
        normalized.append(row)
    return normalized


def _string_list(value: Any, label: str) -> list[str]:
    rows = _json_arg(value, label, list)
    if not all(isinstance(item, str) for item in rows):
        raise ValueError(f"{label} must contain strings")
    return rows


def _normalize_quality_submission(
    claims: list[dict[str, Any] | BaseModel],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Apply only safety-preserving repairs before the strict quality contract.

    Reviewers sometimes attach a made-up path to a gap or leave a release cap
    on a row that explicitly records a gap. Those repairs only remove an
    unsupported reference or lower the claim ceiling; they never upgrade an
    evidence row or add support.
    """

    normalized: list[dict[str, Any]] = []
    notes: list[str] = []
    for index, raw in enumerate(claims):
        row = raw.model_dump() if isinstance(raw, BaseModel) else dict(raw)
        matrix = row.get("evidence_matrix")
        if not isinstance(matrix, list):
            normalized.append(row)
            continue
        repaired_matrix: list[dict[str, Any]] = []
        has_gap = False
        for evidence_index, raw_evidence in enumerate(matrix):
            evidence = (
                dict(raw_evidence)
                if isinstance(raw_evidence, Mapping)
                else raw_evidence
            )
            if isinstance(evidence, dict) and evidence.get("evidence_role") == "gap":
                has_gap = True
                if evidence.get("source_ref") is not None:
                    evidence["source_ref"] = None
                    notes.append(
                        f"claims[{index}].evidence_matrix[{evidence_index}] gap source_ref cleared"
                    )
            if isinstance(evidence, dict):
                quality_cap = evidence.get("quality_cap")
                if (
                    evidence.get("directness") == "context_only"
                    and quality_cap != "exploratory"
                ):
                    evidence["quality_cap"] = "exploratory"
                    notes.append(
                        f"claims[{index}].evidence_matrix[{evidence_index}] context-only cap downgraded"
                    )
                elif quality_cap == "release_candidate" and (
                    evidence.get("evidence_scope")
                    in {"abstract_only", "wiki_entry", "unknown"}
                    or evidence.get("source_class")
                    in {"simulation", "review", "wiki_context", "user_premise"}
                ):
                    evidence["quality_cap"] = "evidence_constrained"
                    notes.append(
                        f"claims[{index}].evidence_matrix[{evidence_index}] release-ineligible cap downgraded"
                    )
            repaired_matrix.append(evidence)
        row["evidence_matrix"] = repaired_matrix
        if has_gap and row.get("conclusion_cap") == "release_candidate":
            row["conclusion_cap"] = "evidence_constrained"
            notes.append(f"claims[{index}] conclusion_cap downgraded for declared gap")
        if has_gap and row.get("quality_status") == "release_candidate":
            row["quality_status"] = "evidence_constrained"
            notes.append(f"claims[{index}] quality_status downgraded for declared gap")
        if row.get("quality_status") == "release_candidate":
            novelty = row.get("novelty_assessment")
            novelty_status = (
                novelty.get("status") if isinstance(novelty, Mapping) else None
            )
            unresolved_scope = any(
                isinstance(item, Mapping)
                and (
                    item.get("scope_match") in {"mismatch", "not_assessable"}
                    or item.get("entailment") in {"not_entailed", "not_assessable"}
                )
                for item in repaired_matrix
            )
            direct_primary = [
                item
                for item in repaired_matrix
                if isinstance(item, Mapping)
                and item.get("evidence_role") == "supports"
                and item.get("source_class")
                in {"direct_observation", "real_experiment"}
                and item.get("evidence_scope")
                in {"full_text", "dataset_record", "experiment_record"}
                and item.get("directness") == "direct"
                and item.get("scope_match") == "matched"
                and item.get("entailment") == "entailed"
            ]
            support_groups = {
                item.get("independence_group")
                for item in repaired_matrix
                if isinstance(item, Mapping)
                and item.get("evidence_role") == "supports"
                and item.get("scope_match") == "matched"
                and item.get("entailment") == "entailed"
                and item.get("quality_cap") == "release_candidate"
                and item.get("independence_group")
            }
            release_ineligible = (
                bool(row.get("key_gaps"))
                or novelty_status in {"potentially_novel", "novelty_not_assessed"}
                or unresolved_scope
                or (
                    bool(row.get("load_bearing"))
                    and (not direct_primary or len(support_groups) < 2)
                )
            )
            if release_ineligible:
                row["quality_status"] = "evidence_constrained"
                if row.get("conclusion_cap") == "release_candidate":
                    row["conclusion_cap"] = "evidence_constrained"
                notes.append(
                    f"claims[{index}] release status downgraded for unresolved eligibility"
                )
        if (
            row.get("quality_status") == "release_candidate"
            and row.get("conclusion_cap") != "release_candidate"
        ):
            row["quality_status"] = row["conclusion_cap"]
            notes.append(f"claims[{index}] quality_status aligned with conclusion_cap")
        normalized.append(row)
    return normalized, notes


@tool(parse_docstring=True)
def evidence_review_open_context(
    review_mode: str,
    config: RunnableConfig = None,
) -> str:
    """Open the immutable artifact set for one typed review mode.

    Args:
        review_mode: One of planning, data, hypothesis, experiment_design,
            experiment_result, integration, or final_release.

    Returns:
        JSON with compact hash-bound artifact projections, declared source refs,
            prior verdicts, policy version, and budget. Detailed scientific content
            must be opened explicitly with evidence_review_read_source.
    """

    try:
        if review_mode not in ALL_REVIEW_MODES:
            raise ValueError(f"unsupported review_mode: {review_mode}")
        return _ok(store_from_config(config).review_context(review_mode))
    except Exception as exc:
        return _error(exc)


@tool(parse_docstring=True)
def evidence_review_read_source(
    review_mode: str,
    source_ref: str,
    config: RunnableConfig = None,
) -> str:
    """Read one source declared by the current immutable review artifact.

    Args:
        review_mode: The same typed mode passed to evidence_review_open_context.
        source_ref: An exact evidence, opposing-evidence, or upstream reference
            returned by the server-bound review context.

    Returns:
        A bounded read-only source record with SHA-256 and text content when the
        referenced file is UTF-8, or an immutable ResearchArtifactV2 record.
    """

    try:
        if review_mode not in ALL_REVIEW_MODES:
            raise ValueError(f"unsupported review_mode: {review_mode}")
        return _ok(store_from_config(config).review_source(review_mode, source_ref))
    except Exception as exc:
        return _error(exc)


@tool(parse_docstring=True)
def evidence_review_search_document(
    review_mode: str,
    source_ref: str,
    query: str,
    max_hits: int = 8,
    config: RunnableConfig = None,
) -> str:
    """Search a declared task-local PDF/Markdown/HTML/text source by section.

    Args:
        review_mode: Typed stage being reviewed.
        source_ref: Exact declared source reference from the review context.
        query: Concrete mechanism, observable, method, or contradiction query.
        max_hits: Maximum section hits, from 1 to 20.

    Returns:
        Exact section ids and bounded excerpts. An empty result is a search gap,
        never evidence that the claim is true.
    """

    try:
        if review_mode not in ALL_REVIEW_MODES:
            raise ValueError(f"unsupported review_mode: {review_mode}")
        return _ok(
            store_from_config(config).search_document(
                review_mode, source_ref, query, max_hits=max_hits
            )
        )
    except Exception as exc:
        return _error(exc)


@tool(parse_docstring=True)
def evidence_review_read_document_sections(
    review_mode: str,
    source_ref: str,
    section_ids: list[str] | str,
    config: RunnableConfig = None,
) -> str:
    """Read exact sections previously located in a declared document.

    Args:
        review_mode: Typed stage being reviewed.
        source_ref: Exact declared source reference from the review context.
        section_ids: JSON list of 1 to 12 exact section ids.

    Returns:
        Full bounded text for the requested sections and stable locators.
    """

    try:
        if review_mode not in ALL_REVIEW_MODES:
            raise ValueError(f"unsupported review_mode: {review_mode}")
        return _ok(
            store_from_config(config).read_document_sections(
                review_mode,
                source_ref,
                _string_list(section_ids, "section_ids"),
            )
        )
    except Exception as exc:
        return _error(exc)


@tool(parse_docstring=True)
def evidence_review_submit_verdict(
    review_mode: str,
    decision: str,
    issues: list[dict[str, Any]] | str,
    accepted_claims: list[str] | str = "[]",
    blocked_claims: list[str] | str = "[]",
    carry_forward_limits: list[str] | str = "[]",
    next_owner: str = "",
    config: RunnableConfig = None,
) -> str:
    """Persist a hash-bound review verdict without modifying producer artifacts.

    Args:
        review_mode: Typed stage being reviewed.
        decision: accept, accept_with_limits, revise, or block.
        issues: JSON issue list with rule_id, severity, claim_ref, owner,
            message, required_action, acceptance_test, and evidence_refs. Each
            distinct defect must use a unique, stable field-level claim_ref.
        accepted_claims: JSON list of accepted claim ids.
        blocked_claims: JSON list of blocked claim ids.
        carry_forward_limits: JSON list of limitations that final prose must retain.
        next_owner: Producer responsible for a revise decision.

    Returns:
        The persisted ReviewVerdictV2, including server-bound hashes and round.
    """

    try:
        store = store_from_config(config)
        _require_current_assessment(store, review_mode)
        _require_current_scientific_quality(store, review_mode)
        verdict = store.submit_verdict(
            mode=review_mode,
            decision=decision,
            issues=_normalize_issues(issues),
            accepted_claims=_string_list(accepted_claims, "accepted_claims"),
            blocked_claims=_string_list(blocked_claims, "blocked_claims"),
            carry_forward_limits=_string_list(
                carry_forward_limits, "carry_forward_limits"
            ),
            next_owner=next_owner.strip() or None,
        )
        return _ok(verdict)
    except Exception as exc:
        return _error(exc)


@tool
def evidence_review_get_status(config: RunnableConfig = None) -> str:
    """Return the task's persisted ResearchRunStateV2 and deterministic next action."""

    try:
        store = store_from_config(config)
        return _ok({"state": store.load_state(), "next_action": store.next_action()})
    except Exception as exc:
        return _error(exc)


@tool(parse_docstring=True)
def evidence_review_record_assessment(
    review_mode: str,
    assessment_review_mode: str,
    claims: list[dict[str, Any]] | str,
    config: RunnableConfig = None,
) -> str:
    """Record a per-claim ReviewAssessmentV1 sidecar before submitting the verdict.

    Call this once per review round, after the closed pass (and, when in
    two_pass mode, after the active-falsification pass), immediately before
    evidence_review_submit_verdict.  The assessment never changes routing: it
    only captures, per claim, the evidence-for/against picture, the
    disposition, and the single most discriminating next test.

    Args:
        review_mode: Typed stage being reviewed.
        assessment_review_mode: closed or two_pass.
        claims: JSON list, one object per reviewed claim with claim_id, kind,
            disposition (supported, limited_support, opposed, contradicted,
            undecided), supporting_evidence, opposing_evidence, rationale,
            key_uncertainty, confidence, and next_test.

    Returns:
        The persisted ReviewAssessmentV1, including its content hash and round.
    """

    try:
        configured_mode = _configured_assessment_review_mode()
        requested_mode = assessment_review_mode.strip() or configured_mode
        if requested_mode != configured_mode:
            raise ValueError(
                "assessment_review_mode does not match the configured Evidence mode: "
                f"expected {configured_mode}"
            )
        assessment = store_from_config(config).record_assessment(
            mode=review_mode,
            assessment_review_mode=requested_mode,
            claims=_normalize_assessment_claims(claims, "claims"),
        )
        return _ok(assessment)
    except Exception as exc:
        return _error(exc)


@tool(parse_docstring=True)
def evidence_review_record_scientific_quality(
    review_mode: str,
    assessment_review_mode: str,
    claims: list[dict[str, Any]] | str,
    config: RunnableConfig = None,
) -> str:
    """Record the claim-level high-quality research review matrix.

    Call this exactly once per review round after inspecting the declared
    sources and before submitting the verdict. It is a sidecar and never
    overrides ReviewVerdictV2 routing.

    Args:
        review_mode: Typed stage being reviewed.
        assessment_review_mode: closed or two_pass, matching backend config.
        claims: One quality object per reviewed claim component. A claim may
            have multiple rows, but each claim_id + claim_component pair is unique. Each object contains
            claim_id, claim_component, load_bearing, a non-empty
            evidence_matrix, method_assessment, novelty_assessment,
            conclusion_cap, quality_status, and key_gaps. Evidence rows record
            source_ref, evidence_role, source_class, evidence_scope, directness, scope_match,
            independence_group, locator, entailment, quality_cap, and rationale.

    Returns:
        The persisted ScientificQualityAssessmentV1 for the current round.
    """

    try:
        configured_mode = _configured_assessment_review_mode()
        requested_mode = assessment_review_mode.strip() or configured_mode
        if requested_mode != configured_mode:
            raise ValueError(
                "assessment_review_mode does not match the configured Evidence mode: "
                f"expected {configured_mode}"
            )
        assessment = store_from_config(config).record_scientific_quality_assessment(
            mode=review_mode,
            assessment_review_mode=requested_mode,
            claims=_json_arg(claims, "claims", list),
        )
        return _ok(assessment)
    except Exception as exc:
        return _error(exc)


@tool(parse_docstring=True)
def evidence_review_submit_round(
    review_mode: str,
    assessment_review_mode: str,
    assessment_claims: list[AssessmentClaimInput] | str,
    scientific_quality_claims: list[ScientificQualityClaimInput] | str,
    decision: str,
    issues: list[VerdictIssueInput] | str,
    accepted_claims: list[str] | str = "[]",
    blocked_claims: list[str] | str = "[]",
    carry_forward_limits: list[str] | str = "[]",
    next_owner: str = "",
    config: RunnableConfig = None,
) -> str:
    """Persist one complete Evidence round through a single model tool call.

    This convenience entry point does not weaken any contract. It validates and
    writes exactly one ReviewAssessmentV1, one ScientificQualityAssessmentV1,
    and then one ReviewVerdictV2 for the same current artifact and round. A
    retry may replace only sidecars that have not yet been bound by a verdict.

    Args:
        review_mode: Typed stage being reviewed.
        assessment_review_mode: closed or two_pass, matching backend config.
        assessment_claims: Exactly one ReviewAssessmentV1 row per target artifact
            claim. Reuse each exact claim_id once; do not add component suffixes or
            create synthetic ids.
        scientific_quality_claims: Complete ScientificQualityAssessmentV1 rows.
            These may reuse an exact artifact claim_id for distinct claim_component
            values; each claim_id plus claim_component pair must be unique.
        decision: accept, accept_with_limits, revise, or block.
        issues: Complete ReviewVerdictV2 issue rows; issues contain only producer-fixable defects.
            Put no-action downstream limitations in carry_forward_limits instead,
            with an empty issues list for an otherwise acceptable artifact.
        accepted_claims: Exact target artifact claim ids accepted by the verdict.
            accept and accept_with_limits require at least one such id.
        blocked_claims: Blocked claim ids.
        carry_forward_limits: Limitations that downstream prose must retain.
        next_owner: Producer responsible for a revise decision.

    Returns:
        All three persisted records and their shared round.
    """

    logger.info("Evidence atomic round submission started for mode=%s", review_mode)
    store = None
    pending_round = None
    sidecar_backups: dict[Any, bytes | None] = {}
    try:
        if review_mode not in ALL_REVIEW_MODES:
            raise ValueError(f"unsupported review_mode: {review_mode}")
        configured_mode = _configured_assessment_review_mode()
        requested_mode = assessment_review_mode.strip() or configured_mode
        if requested_mode != configured_mode:
            raise ValueError(
                "assessment_review_mode does not match the configured Evidence mode: "
                f"expected {configured_mode}"
            )
        store = store_from_config(config)
        pending_round = len(store.verdicts(mode=review_mode)) + 1
        sidecar_paths = (
            store.root
            / "assessments"
            / f"{review_mode}-assessment-{pending_round:04d}.json",
            store.root
            / "scientific_quality_assessments"
            / f"{review_mode}-quality-{pending_round:04d}.json",
        )
        sidecar_backups = {
            path: path.read_bytes() if path.exists() else None for path in sidecar_paths
        }
        normalized_assessment_claims = _normalize_assessment_claims(assessment_claims)
        reviewed_kinds = {
            claim["claim_id"]: claim["kind"]
            for target in store.review_targets(review_mode)
            for claim in target.get("claims", [])
            if isinstance(claim, dict)
            and isinstance(claim.get("claim_id"), str)
            and isinstance(claim.get("kind"), str)
        }
        normalized_assessment_claims = [
            {
                **row,
                "kind": reviewed_kinds.get(row.get("claim_id"), row.get("kind")),
            }
            if isinstance(row, dict)
            else row
            for row in normalized_assessment_claims
        ]
        accepted_claim_list = _string_list(accepted_claims, "accepted_claims")
        if decision in {"accept", "accept_with_limits"} and not accepted_claim_list:
            raise ValueError("an accepting verdict must name accepted_claims")
        assessment = store.record_assessment(
            mode=review_mode,
            assessment_review_mode=requested_mode,
            claims=normalized_assessment_claims,
            replace_uncommitted=True,
        )
        normalized_quality_claims, normalization_notes = _normalize_quality_submission(
            [
                row.model_dump() if isinstance(row, BaseModel) else row
                for row in _json_arg(
                    scientific_quality_claims, "scientific_quality_claims", list
                )
            ]
        )
        quality = store.record_scientific_quality_assessment(
            mode=review_mode,
            assessment_review_mode=requested_mode,
            claims=normalized_quality_claims,
            replace_uncommitted=True,
        )
        _require_current_assessment(store, review_mode)
        _require_current_scientific_quality(store, review_mode)
        verdict = store.submit_verdict(
            mode=review_mode,
            decision=decision,
            issues=_normalize_issues(issues),
            accepted_claims=accepted_claim_list,
            blocked_claims=_string_list(blocked_claims, "blocked_claims"),
            carry_forward_limits=_string_list(
                carry_forward_limits, "carry_forward_limits"
            ),
            next_owner=next_owner.strip() or None,
        )
        if not (
            assessment["round"] == quality["round"] == verdict["round"]
            and assessment["artifact_refs"]
            == quality["artifact_refs"]
            == verdict["artifact_refs"]
        ):
            raise RuntimeError("Evidence round records do not share one binding")
        logger.info(
            "Evidence atomic round submission persisted mode=%s round=%s decision=%s",
            review_mode,
            verdict["round"],
            verdict["decision"],
        )
        return _ok(
            {
                "round": verdict["round"],
                "assessment": assessment,
                "scientific_quality_assessment": quality,
                "verdict": verdict,
                "normalization_notes": normalization_notes,
            }
        )
    except Exception as exc:
        for path, original_bytes in sidecar_backups.items():
            if original_bytes is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(original_bytes)
        logger.warning(
            "Evidence atomic round submission rejected for mode=%s: %s: %s",
            review_mode,
            type(exc).__name__,
            exc,
        )
        return _error(exc)


@tool(parse_docstring=True)
def research_release_prepare(
    draft_markdown: str,
    claim_citations: list[dict[str, Any]] | str,
    config: RunnableConfig = None,
) -> str:
    """Checkpoint a coherent final report draft after integration acceptance.

    Args:
        draft_markdown: Reader-facing report synthesized only from accepted claims.
        claim_citations: JSON list binding each material draft passage to one
            accepted integration claim_id; semantic adequacy is decided by the
            final Evidence review.

    Returns:
        A hash-bound final_release ResearchArtifactV2 awaiting Evidence review.
    """

    try:
        return _ok(
            store_from_config(config).prepare_release(
                draft_markdown,
                _json_arg(claim_citations, "claim_citations", list),
            )
        )
    except Exception as exc:
        return _error(exc)


@tool
def research_release_get_accepted(config: RunnableConfig = None) -> str:
    """Return the exact accepted final report; fail if the release gate is open."""

    try:
        report = store_from_config(config).accepted_release_markdown()
        if report is None:
            raise RuntimeError("no final report has an accepted hash-bound verdict")
        return _ok({"status": "accepted", "report_markdown": report})
    except Exception as exc:
        return _error(exc)


RESEARCH_REVIEW_TOOLS = [
    evidence_review_open_context,
    evidence_review_read_source,
    evidence_review_search_document,
    evidence_review_read_document_sections,
    evidence_review_submit_verdict,
    evidence_review_get_status,
    evidence_review_record_assessment,
    evidence_review_record_scientific_quality,
    evidence_review_submit_round,
]
EVIDENCE_REVIEW_TOOLS = [
    evidence_review_open_context,
    evidence_review_read_source,
    evidence_review_search_document,
    evidence_review_read_document_sections,
    evidence_review_get_status,
    evidence_review_submit_round,
]
RESEARCH_RELEASE_TOOLS = [
    research_release_prepare,
    research_release_get_accepted,
]

register_tool_bundle("research-review", RESEARCH_REVIEW_TOOLS)
register_tool_bundle("evidence-review", EVIDENCE_REVIEW_TOOLS)
register_tool_bundle("research-release", RESEARCH_RELEASE_TOOLS, include_in_main=True)

__all__ = [
    "EVIDENCE_REVIEW_TOOLS",
    "RESEARCH_RELEASE_TOOLS",
    "RESEARCH_REVIEW_TOOLS",
] + [tool.name for tool in [*RESEARCH_REVIEW_TOOLS, *RESEARCH_RELEASE_TOOLS]]
