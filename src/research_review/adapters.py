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
        "limitations": [],
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
        },
    }


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
            if (
                not isinstance(draft, dict)
                or draft.get("response_kind") != "hypotheses_ready"
            ):
                continue
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
                result.append(
                    _claim(
                        claim_id=f"hypothesis-{candidate_id}",
                        kind="mechanism",
                        text=str(candidate.get("statement") or candidate_id),
                        scope=str(
                            candidate.get("applicability")
                            or "Scope recorded in the hypothesis state"
                        ),
                        supporting=[source_ref] if supporting else [],
                        opposing=[source_ref] if opposing else [],
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
        "confidence": confidence,
        "unknowns": unknowns or [],
    }


__all__ = ["adapt_v1_producer_output"]
