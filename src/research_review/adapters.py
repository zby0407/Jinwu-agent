"""Explicit conservative adapters from current producer v1 outputs to v2."""

from __future__ import annotations

import json
from typing import Any

from .contracts import CLAIM_VERSION

_ADAPTERS = {
    "planning": ("research-planner-v1-to-v2", "inference"),
    "data": ("solar-data-receipt-v1-to-v2", "observation"),
    "hypothesis": ("scientific-hypothesis-v1-to-v2", "mechanism"),
    "experiment_design": ("automatic-experiment-design-v1-to-v2", "prediction"),
    "experiment_result": ("automatic-experiment-result-v1-to-v2", "observation"),
    "final_release": ("main-release-v1-to-v2", "inference"),
}
_HYPOTHESIS_NONSCIENTIFIC_METADATA = {
    "artifact_version",
    "created_at",
    "draft_version",
    "generated_at",
    "revision",
    "response_timestamp",
    "timestamp",
    "ts",
    "updated_at",
}


def _strip_hypothesis_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_hypothesis_metadata(item)
            for key, item in value.items()
            if key not in _HYPOTHESIS_NONSCIENTIFIC_METADATA
        }
    if isinstance(value, list):
        return [_strip_hypothesis_metadata(item) for item in value]
    return value


def adapt_v1_producer_output(
    *,
    stage: str,
    version: int,
    phase: str,
    text: str,
    evidence_refs: list[str],
    canonical_documents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return explicit claim/payload fields without inferring unsupported facts."""

    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        decoded = None
    documents = canonical_documents or []
    source_schema = (
        decoded.get("schema_version")
        if isinstance(decoded, dict) and isinstance(decoded.get("schema_version"), str)
        else "unstructured-producer-result"
    )
    adapter_id, claim_kind = _ADAPTERS[stage]
    claims = _claims_from_known_v1(
        stage=stage,
        version=version,
        documents=documents,
    )
    hypothesis_projection = (
        _hypothesis_projection(documents) if stage == "hypothesis" else None
    )
    if claims:
        known_schemas = [
            row["payload"].get("schema_version")
            for row in documents
            if isinstance(row.get("payload"), dict)
        ]
        source_schema = next(
            (str(value) for value in known_schemas if value is not None),
            source_schema,
        )
    else:
        claims = [
            {
                "schema_version": CLAIM_VERSION,
                "claim_id": f"{stage}-output-v{version}",
                "kind": claim_kind,
                "text": text[:20_000],
                "scope": (
                    f"Explicit {adapter_id} output for {stage}; Evidence must inspect "
                    "the source receipt and must not infer support from prose."
                ),
                "supporting_evidence": evidence_refs,
                "opposing_evidence": [],
                "limiting_evidence": [],
                "confidence": "unknown",
                "unknowns": [
                    "The v1 adapter preserves content but does not infer scientific support."
                ],
            }
        ]
    return {
        "claims": claims,
        # Adapter provenance is internal metadata, not a reader-facing
        # scientific limitation. Material limitations enter through producer
        # contracts or ReviewVerdictV2.carry_forward_limits.
        "limitations": (
            hypothesis_projection["limitations"]
            if hypothesis_projection is not None
            else []
        ),
        "evidence_refs": (
            hypothesis_projection["evidence_refs"] or evidence_refs
            if hypothesis_projection is not None
            else evidence_refs
        ),
        "payload": {
            "adapter_id": adapter_id,
            "source_schema_version": source_schema,
            "canonical_source_refs": [
                row["source_ref"]
                for row in documents
                if isinstance(row.get("source_ref"), str)
            ],
            "phase": phase,
            "producer_result": text,
            **(
                {
                    "result_status": hypothesis_projection["result_status"],
                    "hypothesis_evidence_index": hypothesis_projection[
                        "evidence_index"
                    ],
                    "hypothesis_scientific_content": _strip_hypothesis_metadata(
                        hypothesis_projection["draft"]
                    ),
                }
                if hypothesis_projection is not None
                else {}
            ),
        },
    }


def _hypothesis_projection(
    documents: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return the status and task-local evidence objects from hypothesis state.

    Evidence ids are exposed as virtual source refs.  The review store resolves
    those refs back to the exact register row, so literature limits and opposing
    evidence no longer collapse into the state-file path or masquerade as
    support.
    """

    for row in documents:
        source_ref = row.get("source_ref")
        payload = row.get("payload")
        if not (
            isinstance(source_ref, str)
            and source_ref.endswith("work/scientific_hypothesis_state.json")
            and isinstance(payload, dict)
        ):
            continue
        draft = payload.get("latest_draft") or payload.get("checkpoint")
        response_kind = draft.get("response_kind") if isinstance(draft, dict) else None
        result_status = {
            "hypotheses_ready": "scientific_content",
            "clarification_needed": "clarification_status",
            "hypothesis_blocked": "blocked_status",
        }.get(str(response_kind), "blocked_status")
        evidence_index: dict[str, dict[str, Any]] = {}
        limitations: list[str] = []
        register = payload.get("evidence_register")
        if isinstance(register, list):
            for entry in register:
                evidence_id = (
                    entry.get("evidence_id") if isinstance(entry, dict) else None
                )
                if isinstance(evidence_id, str) and evidence_id:
                    evidence_index[f"hypothesis-evidence:{evidence_id}"] = dict(entry)
                    excerpt = entry.get("excerpt")
                    if (
                        entry.get("role") == "limits"
                        and isinstance(excerpt, str)
                        and excerpt.strip()
                        and excerpt not in limitations
                    ):
                        limitations.append(excerpt[:4_000])
        return {
            "result_status": result_status,
            "evidence_index": evidence_index,
            "evidence_refs": list(evidence_index),
            "limitations": limitations,
            "draft": draft,
        }
    return None


def _claims_from_known_v1(
    *, stage: str, version: int, documents: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    for row in documents:
        source_ref = row.get("source_ref")
        payload = row.get("payload")
        if not isinstance(source_ref, str) or not isinstance(payload, dict):
            continue
        schema = payload.get("schema_version")
        if stage == "planning" and schema == "research-plan-v1":
            unknowns = []
            if payload.get("planning_readiness") == "external_inputs_required":
                unknowns.append("The frozen plan still requires external inputs.")
            return [
                _claim(
                    claim_id=f"planning-plan-v{version}",
                    kind="unknown",
                    text=str(
                        payload.get("research_question") or "Frozen research plan"
                    ),
                    scope=str(payload.get("scope") or "Planning artifact only"),
                    supporting=[source_ref],
                    unknowns=unknowns,
                )
            ]
        if (
            stage == "data"
            and source_ref.endswith("receipts/datasets/f107_semantics.json")
            and payload.get("status") == "verified"
            and isinstance(payload.get("canonical_sha256"), str)
        ):
            text = " | ".join(
                str(payload.get(key) or "")
                for key in ("product_id", "product_version", "canonical_artifact")
            ).strip(" |")
            scope = " to ".join(
                str(payload.get(key) or "")
                for key in ("coverage_start", "coverage_end")
            ).strip(" to")
            return [
                _claim(
                    claim_id=f"data-f107-v{version}",
                    kind="observation",
                    text=text or "Verified F10.7 dataset semantic receipt",
                    scope=scope or "Scope recorded in the referenced semantic receipt",
                    supporting=[source_ref],
                )
            ]
        if stage == "hypothesis" and source_ref.endswith(
            "work/scientific_hypothesis_state.json"
        ):
            draft = payload.get("latest_draft") or payload.get("checkpoint")
            if not isinstance(draft, dict):
                continue
            response_kind = draft.get("response_kind")
            if response_kind in {"clarification_needed", "hypothesis_blocked"}:
                details = (
                    draft.get("questions")
                    if response_kind == "clarification_needed"
                    else draft.get("blockers")
                )
                return [
                    _claim(
                        claim_id=f"hypothesis-status-v{version}",
                        kind="unknown",
                        text=f"{response_kind}: {json.dumps(details or [], ensure_ascii=False)}",
                        scope="Workflow status only; this is not a scientific hypothesis or mechanism claim.",
                        supporting=[],
                        unknowns=[
                            "No reviewable scientific hypothesis portfolio was produced."
                        ],
                    )
                ]
            if response_kind != "hypotheses_ready":
                continue
            register = {
                entry.get("evidence_id"): entry
                for entry in payload.get("evidence_register", [])
                if isinstance(entry, dict) and isinstance(entry.get("evidence_id"), str)
            }
            result: list[dict[str, Any]] = []
            for index, candidate in enumerate(draft.get("candidates", []), start=1):
                if not isinstance(candidate, dict):
                    continue
                candidate_id = str(candidate.get("id") or f"candidate-{index}")
                confidence = candidate.get("confidence")
                level = (
                    confidence.get("level")
                    if isinstance(confidence, dict)
                    else "unknown"
                )
                if level not in {"high", "medium", "low"}:
                    level = "unknown"
                supporting = candidate.get("supporting_evidence")
                opposing = candidate.get("opposing_evidence")
                supporting_refs: list[str] = []
                opposing_refs: list[str] = []
                limiting_refs: list[str] = []
                links = [
                    *(supporting if isinstance(supporting, list) else []),
                    *(opposing if isinstance(opposing, list) else []),
                ]
                for link in links:
                    evidence_id = (
                        link.get("evidence_id") if isinstance(link, dict) else None
                    )
                    entry = register.get(evidence_id)
                    if not isinstance(evidence_id, str) or not isinstance(entry, dict):
                        continue
                    ref = f"hypothesis-evidence:{evidence_id}"
                    role = entry.get("role")
                    target = {
                        "supports": supporting_refs,
                        "opposes": opposing_refs,
                        "limits": limiting_refs,
                    }.get(role)
                    if target is not None and ref not in target:
                        target.append(ref)
                result.append(
                    _claim(
                        claim_id=f"hypothesis-{candidate_id}",
                        kind="mechanism",
                        text=str(candidate.get("statement") or candidate_id),
                        scope=str(
                            candidate.get("applicability")
                            or "Scope recorded in the hypothesis state"
                        ),
                        supporting=supporting_refs,
                        opposing=opposing_refs,
                        limiting=limiting_refs,
                        confidence=str(level),
                        unknowns=_string_items(candidate.get("evidence_gaps")),
                    )
                )
            if result:
                return result
        if stage == "experiment_design" and schema == "automatic-experiment-design-v1":
            frame = payload.get("research_frame")
            frame = frame if isinstance(frame, dict) else {}
            return [
                _claim(
                    claim_id=f"experiment-design-v{version}",
                    kind="prediction",
                    text=str(
                        payload.get("design_summary") or payload.get("normalized_task")
                    ),
                    scope=str(
                        frame.get("claim_scope") or frame.get("primary_question")
                    ),
                    supporting=[source_ref],
                    unknowns=[
                        *_string_items(frame.get("deferred_questions")),
                        *_string_items(frame.get("threats_to_validity")),
                    ],
                )
            ]
        if stage == "experiment_result" and schema == "automatic-experiment-record-v1":
            return [
                _claim(
                    claim_id=f"experiment-result-v{version}",
                    kind="observation",
                    text=str(payload.get("outcome_reason") or payload.get("outcome")),
                    scope=str(payload.get("task") or "Verified experiment result"),
                    supporting=[source_ref],
                    unknowns=(
                        []
                        if payload.get("outcome") == "completed_interpretable"
                        else [f"Recorded outcome: {payload.get('outcome')}"]
                    ),
                )
            ]
    return []


def _string_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        str(item)[:4_000] for item in value if isinstance(item, str) and item.strip()
    ]


def _claim(
    *,
    claim_id: str,
    kind: str,
    text: str,
    scope: str,
    supporting: list[str],
    opposing: list[str] | None = None,
    limiting: list[str] | None = None,
    confidence: str = "unknown",
    unknowns: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": CLAIM_VERSION,
        "claim_id": claim_id[:128],
        "kind": kind,
        "text": text[:20_000] or "Known v1 artifact",
        "scope": scope[:4_000] or "Scope recorded in the referenced v1 artifact",
        "supporting_evidence": supporting,
        "opposing_evidence": opposing or [],
        "limiting_evidence": limiting or [],
        "confidence": confidence,
        "unknowns": unknowns or [],
    }


__all__ = ["adapt_v1_producer_output"]
