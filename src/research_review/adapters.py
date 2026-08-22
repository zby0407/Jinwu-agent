"""Explicit conservative adapters from current producer v1 outputs to v2."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
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
_HARNESS_PREFIX = "research_review/harness/"
_HARNESS_PROVENANCE_CLASSES = {
    "external_lead",
    "request",
    "response",
    "trace",
    "receipt",
}
_HARNESS_PROVENANCE_BASENAMES = {
    "request.json",
    "response.json",
    "receipt.json",
    "trace.json",
}
_HARNESS_RECEIPT_PARTS = 5


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


def _numeric_result(
    result_by_id: dict[str, dict[str, Any]], result_id: str
) -> float | None:
    value = (result_by_id.get(result_id) or {}).get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _manifest_index(
    source_manifest: list[dict[str, Any]] | None,
) -> dict[str, str]:
    rows: dict[str, str] = {}
    for item in source_manifest or []:
        if not isinstance(item, dict):
            continue
        source_ref = item.get("source_ref")
        sha256 = item.get("sha256")
        if isinstance(source_ref, str) and isinstance(sha256, str) and sha256:
            rows[source_ref] = sha256
    return rows


def _strict_manifest_indexes(
    source_manifest: list[dict[str, Any]] | None,
) -> tuple[dict[str, str], dict[str, dict[str, Any]]] | None:
    """Build unambiguous manifest indexes for canonical Harness projection."""

    hashes: dict[str, str] = {}
    rows: dict[str, dict[str, Any]] = {}
    for item in source_manifest or []:
        if not isinstance(item, dict) or not isinstance(item.get("source_ref"), str):
            continue
        source_ref = str(item["source_ref"])
        if source_ref in rows:
            return None
        sha256 = item.get("sha256")
        if not isinstance(sha256, str) or not sha256:
            return None
        rows[source_ref] = item
        hashes[source_ref] = sha256
    return hashes, rows


def _manifest_row_index(
    source_manifest: list[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    return {
        str(item["source_ref"]): item
        for item in source_manifest or []
        if isinstance(item, dict) and isinstance(item.get("source_ref"), str)
    }


def _assert_current_harness_ref(source_ref: str, current_task_id: str | None) -> None:
    if not source_ref.startswith(_HARNESS_PREFIX) or current_task_id is None:
        return
    suffix = source_ref.removeprefix(_HARNESS_PREFIX)
    task_segment = suffix.split("/", 1)[0]
    if task_segment != current_task_id:
        raise ValueError("Harness source_ref belongs to a foreign task")


def _normalized_harness_ref(source_ref: str) -> str:
    return posixpath.normpath(source_ref.replace("\\", "/"))


def _strict_harness_receipt_ref(
    source_ref: object, current_task_id: str, *, expected: str | None = None
) -> str | None:
    if not isinstance(source_ref, str) or not source_ref:
        return None
    if (
        "\\" in source_ref
        or source_ref.startswith("/")
        or _normalized_harness_ref(source_ref) != source_ref
    ):
        return None
    parts = source_ref.split("/")
    if (
        len(parts) != _HARNESS_RECEIPT_PARTS
        or parts[0:2] != ["research_review", "harness"]
        or parts[2] != current_task_id
        or not parts[3]
        or parts[3] in {".", ".."}
        or parts[4] != "receipt.json"
    ):
        return None
    if expected is not None and source_ref != expected:
        return None
    return source_ref


def _empty_harness_projection(
    envelope: dict[str, Any], *, reason: str = "canonical Harness binding rejected"
) -> dict[str, Any]:
    binding = (
        envelope.get("binding") if isinstance(envelope.get("binding"), dict) else {}
    )
    return {
        "schema_version": "harness-evidence-v1",
        "status": "invalid",
        "task_id": envelope.get("task_id"),
        "binding": binding,
        "items": [],
        "artifacts": [],
        "evidence_refs": [],
        "candidate_evidence_refs": [],
        "provenance_refs": [],
        "gap_refs": [],
        "limitations": [reason],
    }


def _forced_provenance_ref(source_ref: str, receipt_ref: object) -> bool:
    normalized = _normalized_harness_ref(source_ref)
    if posixpath.basename(normalized).casefold() in _HARNESS_PROVENANCE_BASENAMES:
        return True
    return isinstance(receipt_ref, str) and normalized == _normalized_harness_ref(
        receipt_ref
    )


def _candidate_harness_item(
    item: dict[str, Any],
    *,
    source_ref: str,
    harness_status: object,
    receipt_input_hashes_validated: bool,
    output_hash_validated: bool,
    forced_provenance: bool,
) -> bool:
    source_class = item.get("source_class")
    if forced_provenance or source_class in _HARNESS_PROVENANCE_CLASSES:
        return False
    if source_class == "retrieved_text":
        url = item.get("url")
        basename = posixpath.basename(_normalized_harness_ref(source_ref))
        return (
            harness_status == "completed"
            and output_hash_validated
            and item.get("tool") in {"web_extractor", "web_extractor_local"}
            and item.get("evidence_scope") == "full_text"
            and isinstance(url, str)
            and url.startswith("https://")
            and re.fullmatch(r"(?:local-)?extracted-\d+\.md", basename) is not None
        )
    if source_class == "derived_calculation":
        return (
            harness_status == "completed"
            and output_hash_validated
            and receipt_input_hashes_validated
        )
    return False


def _harness_projection(
    decoded: Any,
    *,
    canonical_documents: list[dict[str, Any]],
    current_task_id: str | None,
    source_manifest: list[dict[str, Any]] | None,
    current_harness_receipt_ref: str | None = None,
) -> dict[str, Any] | None:
    """Expose only checkpoint-bound Harness evidence without judging support."""

    if not isinstance(decoded, dict):
        return None
    envelope = decoded.get("harness_evidence")
    if (
        not isinstance(envelope, dict)
        or envelope.get("schema_version") != "harness-evidence-v1"
    ):
        return None
    strict_mode = current_harness_receipt_ref is not None
    if strict_mode:
        if current_task_id is None:
            return _empty_harness_projection(
                envelope, reason="Harness task binding is missing"
            )
        strict_indexes = _strict_manifest_indexes(source_manifest)
        if strict_indexes is None:
            return _empty_harness_projection(
                envelope,
                reason="Harness source manifest contains duplicate or invalid refs",
            )
        manifest_by_ref, manifest_rows = strict_indexes
    else:
        manifest_by_ref = _manifest_index(source_manifest)
        manifest_rows = _manifest_row_index(source_manifest)
    receipt_ref = envelope.get("receipt_ref")
    if strict_mode:
        expected_receipt_ref = _strict_harness_receipt_ref(
            current_harness_receipt_ref, current_task_id or ""
        )
        if (
            expected_receipt_ref is None
            or _strict_harness_receipt_ref(
                receipt_ref, current_task_id or "", expected=expected_receipt_ref
            )
            is None
        ):
            return _empty_harness_projection(
                envelope,
                reason="Harness receipt_ref is not bound to the current invocation",
            )
        receipt_ref = expected_receipt_ref
        document_refs = [
            document.get("source_ref")
            for document in canonical_documents
            if isinstance(document, dict)
        ]
        if len(document_refs) != len(set(document_refs)):
            return _empty_harness_projection(
                envelope, reason="Harness canonical documents contain duplicate refs"
            )
        canonical_matches = [
            document
            for document in canonical_documents
            if isinstance(document, dict) and document.get("source_ref") == receipt_ref
        ]
        if len(canonical_matches) != 1:
            return _empty_harness_projection(
                envelope, reason="Current Harness receipt is not uniquely canonical"
            )
        canonical_document = canonical_matches[0]
        raw_bytes = canonical_document.get("raw_bytes")
        receipt_manifest = manifest_rows.get(receipt_ref)
        if not isinstance(raw_bytes, bytes) or not isinstance(receipt_manifest, dict):
            return _empty_harness_projection(
                envelope,
                reason="Current Harness receipt is not bound to its manifest row",
            )
        if (
            len(raw_bytes) <= 0
            or receipt_manifest.get("bytes") != len(raw_bytes)
            or receipt_manifest.get("sha256") != hashlib.sha256(raw_bytes).hexdigest()
        ):
            return _empty_harness_projection(
                envelope,
                reason="Current Harness receipt content does not match its manifest",
            )
        try:
            decoded_receipt = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _empty_harness_projection(
                envelope, reason="Current Harness receipt is not valid UTF-8 JSON"
            )
        if decoded_receipt != canonical_document.get("payload"):
            return _empty_harness_projection(
                envelope, reason="Current Harness receipt payload is not byte-bound"
            )
        canonical_receipt = decoded_receipt
    else:
        canonical_receipt = None
    canonical_receipt = next(
        (
            document.get("payload")
            for document in canonical_documents
            if isinstance(receipt_ref, str)
            and document.get("source_ref") == receipt_ref
            and isinstance(document.get("payload"), dict)
            and receipt_ref in manifest_by_ref
        ),
        canonical_receipt,
    )
    if strict_mode:
        # The strict branch above selected the only current receipt; this second
        # lookup is intentionally limited to preserving the legacy projection
        # shape for callers that provide a canonical document payload.
        canonical_receipt = canonical_receipt or next(
            (
                document.get("payload")
                for document in canonical_documents
                if isinstance(document, dict)
                and document.get("source_ref") == receipt_ref
                and isinstance(document.get("payload"), dict)
            ),
            None,
        )
    if canonical_documents and isinstance(receipt_ref, str):
        if (
            not isinstance(canonical_receipt, dict)
            or canonical_receipt.get("schema_version") != "harness-evidence-v1"
        ):
            return None
        value = canonical_receipt
        canonical_mode = True
    else:
        value = envelope
        canonical_mode = False
    binding = value.get("binding") if isinstance(value.get("binding"), dict) else {}
    if current_task_id is not None:
        envelope_task_ids = {
            task_id
            for task_id in (value.get("task_id"), binding.get("task_id"))
            if isinstance(task_id, str) and task_id
        }
        if not envelope_task_ids:
            raise ValueError("Harness envelope must declare task_id")
        declared_task_ids = {
            task_id
            for task_id in (
                value.get("task_id"),
                binding.get("task_id"),
            )
            if isinstance(task_id, str) and task_id
        }
        if declared_task_ids and declared_task_ids != {current_task_id}:
            raise ValueError("Harness task_id/binding does not match the current task")

    declared_hashes: dict[str, str] = {}
    candidate_artifacts: set[str] = set()
    for artifact in value.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        path = artifact.get("path")
        sha256 = artifact.get("sha256")
        if isinstance(path, str):
            _assert_current_harness_ref(path, current_task_id)
            if isinstance(sha256, str) and sha256:
                declared_hashes[path] = sha256
                manifest_row = manifest_rows.get(path, {})
                declared_bytes = artifact.get("bytes")
                manifest_bytes = manifest_row.get("bytes")
                bytes_match = (
                    isinstance(declared_bytes, int)
                    and not isinstance(declared_bytes, bool)
                    and declared_bytes > 0
                    and declared_bytes == manifest_bytes
                )
                if sha256 == manifest_by_ref.get(path) and (
                    bytes_match or (not canonical_mode and not strict_mode)
                ):
                    candidate_artifacts.add(path)

    normalized_run_root = (
        _normalized_harness_ref(receipt_ref).rsplit("/", 1)[0]
        if canonical_mode and isinstance(receipt_ref, str)
        else None
    )

    def _same_invocation(source_ref: str) -> bool:
        if normalized_run_root is None:
            return True
        normalized = _normalized_harness_ref(source_ref)
        return normalized.startswith(f"{normalized_run_root}/")

    def _checkpoint_bound(source_ref: str) -> bool:
        if strict_mode and source_ref.startswith(_HARNESS_PREFIX):
            if _normalized_harness_ref(source_ref) != source_ref or "\\" in source_ref:
                return False
        _assert_current_harness_ref(source_ref, current_task_id)
        if not _same_invocation(source_ref):
            return False
        checkpoint_sha256 = manifest_by_ref.get(source_ref)
        if checkpoint_sha256 is None:
            return False
        declared_sha256 = declared_hashes.get(source_ref)
        return declared_sha256 is None or declared_sha256 == checkpoint_sha256

    analysis_inputs = value.get("analysis_inputs")
    receipt_input_hashes_validated = bool(analysis_inputs) and isinstance(
        analysis_inputs, list
    )
    for raw_input in analysis_inputs if isinstance(analysis_inputs, list) else []:
        if not isinstance(raw_input, dict):
            receipt_input_hashes_validated = False
            break
        source_ref = raw_input.get("source_ref")
        sha256 = raw_input.get("sha256")
        declared_bytes = raw_input.get("bytes")
        if (
            not isinstance(source_ref, str)
            or not isinstance(sha256, str)
            or (
                strict_mode
                and (
                    not isinstance(declared_bytes, int)
                    or isinstance(declared_bytes, bool)
                    or declared_bytes <= 0
                )
            )
        ):
            receipt_input_hashes_validated = False
            break
        _assert_current_harness_ref(source_ref, current_task_id)
        manifest_row = manifest_rows.get(source_ref, {})
        if manifest_by_ref.get(source_ref) != sha256 or (
            strict_mode and manifest_row.get("bytes") != declared_bytes
        ):
            receipt_input_hashes_validated = False
            break

    items: list[dict[str, Any]] = []
    candidate_refs: set[str] = set()
    gap_refs: set[str] = set()
    item_refs: set[str] = set()
    for raw_item in value.get("items", []):
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        source_ref = item.get("source_ref")
        if isinstance(source_ref, str):
            if strict_mode and source_ref not in declared_hashes:
                continue
            if not _checkpoint_bound(source_ref):
                continue
            item_refs.add(source_ref)
            output_hash_validated = source_ref in candidate_artifacts
            forced_provenance = _forced_provenance_ref(source_ref, receipt_ref)
            is_candidate = _candidate_harness_item(
                item,
                source_ref=source_ref,
                harness_status=value.get("status"),
                receipt_input_hashes_validated=receipt_input_hashes_validated,
                output_hash_validated=output_hash_validated,
                forced_provenance=forced_provenance,
            )
            if is_candidate:
                candidate_refs.add(source_ref)
            if not is_candidate and (
                item.get("claim_role") == "gap"
                or item.get("source_class") in _HARNESS_PROVENANCE_CLASSES
                or item.get("source_class") == "retrieved_text"
                or forced_provenance
            ):
                gap_refs.add(source_ref)
        elif item.get("source_class") != "external_lead":
            continue
        items.append(item)

    artifacts: list[dict[str, Any]] = []
    artifact_refs: set[str] = set()
    for raw_artifact in value.get("artifacts", []):
        if not isinstance(raw_artifact, dict):
            continue
        path = raw_artifact.get("path")
        if isinstance(path, str) and _checkpoint_bound(path):
            artifacts.append(dict(raw_artifact))
            artifact_refs.add(path)
    if isinstance(receipt_ref, str) and _checkpoint_bound(receipt_ref):
        artifact_refs.add(receipt_ref)
        run_root = receipt_ref.rsplit("/", 1)[0]
        for name in ("request.json", "response.json", "trace.json"):
            provenance_ref = f"{run_root}/{name}"
            if _checkpoint_bound(provenance_ref):
                artifact_refs.add(provenance_ref)

    evidence_refs = sorted(item_refs | artifact_refs)
    provenance_refs = sorted(set(evidence_refs) - candidate_refs)
    return {
        "schema_version": "harness-evidence-v1",
        **({"receipt_ref": receipt_ref} if isinstance(receipt_ref, str) else {}),
        "status": value.get("status", "unknown"),
        "task_id": value.get("task_id"),
        "binding": binding,
        "items": items,
        "artifacts": artifacts,
        "evidence_refs": evidence_refs,
        "candidate_evidence_refs": sorted(candidate_refs),
        "provenance_refs": provenance_refs,
        "gap_refs": sorted(gap_refs),
        "limitations": list(value.get("limitations", []))
        if isinstance(value.get("limitations"), list)
        else [],
    }


def _boolean_result(
    result_by_id: dict[str, dict[str, Any]], result_id: str
) -> bool | None:
    value = (result_by_id.get(result_id) or {}).get("value")
    return value if isinstance(value, bool) else None


def _data_result_projection(
    documents: list[dict[str, Any]],
    *,
    current_task_id: str | None,
    source_manifest: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Project verified solar Data receipts without using producer narration."""

    manifest_by_ref = _manifest_index(source_manifest)
    recognized_contracts = {
        "solar-precursor-cycle-table-v2": "solar_precursor_cycle_table",
        "solar-cycle-pair-analysis-table-v2": "solar_cycle_pair_analysis_table",
    }
    recognized: list[tuple[str, dict[str, Any]]] = []
    for row in documents:
        source_ref = row.get("source_ref")
        payload = row.get("payload")
        if not isinstance(source_ref, str) or not isinstance(payload, dict):
            continue
        schema = payload.get("schema_version")
        status = payload.get("status")
        expected_receipt_type = recognized_contracts.get(str(schema))
        outputs = payload.get("outputs")
        output_hashes_match = (
            isinstance(outputs, list)
            and bool(outputs)
            and all(
                isinstance(output, dict)
                and isinstance(output.get("path"), str)
                and isinstance(output.get("sha256"), str)
                and manifest_by_ref.get(str(output["path"])) == output["sha256"]
                for output in outputs
            )
        )
        task_matches = (
            current_task_id is None or payload.get("task_id") == current_task_id
        )
        if not (
            expected_receipt_type is not None
            and payload.get("receipt_type") == expected_receipt_type
            and payload.get("producer") == "solar-data"
            and task_matches
            and source_ref in manifest_by_ref
            and output_hashes_match
        ):
            continue
        if (schema == "solar-precursor-cycle-table-v2" and status == "verified") or (
            schema == "solar-cycle-pair-analysis-table-v2"
            and status
            in {
                "verified",
                "partial",
                "analysis_table_ready",
                "analysis_table_incomplete",
            }
        ):
            recognized.append((source_ref, payload))
    if not recognized:
        return None

    pair_payload = next(
        (
            payload
            for _ref, payload in recognized
            if payload.get("schema_version") == "solar-cycle-pair-analysis-table-v2"
        ),
        None,
    )
    primary = pair_payload or recognized[0][1]

    def _ordered_strings(field: str) -> list[str]:
        result: list[str] = []
        for _ref, payload in recognized:
            values = payload.get(field)
            for value in values if isinstance(values, list) else []:
                if isinstance(value, str) and value and value not in result:
                    result.append(value)
        return result

    output_refs: list[str] = []
    for _ref, payload in recognized:
        for output in payload.get("outputs", []):
            path = output.get("path") if isinstance(output, dict) else None
            if isinstance(path, str) and path and path not in output_refs:
                output_refs.append(path)
    gaps: list[dict[str, Any]] = []
    for _ref, payload in recognized:
        for gap in payload.get("gaps", []):
            if isinstance(gap, dict) and gap not in gaps:
                gaps.append(dict(gap))
    limitations: list[str] = []
    for _ref, payload in recognized:
        for limit in payload.get("limitations", []):
            if isinstance(limit, str) and limit and limit not in limitations:
                limitations.append(limit)

    summary = {
        "schema_version": "solar-data-result-summary-v1",
        "status": primary.get("status"),
        "dataset_ids": _ordered_strings("dataset_ids"),
        "source_receipt_refs": [source_ref for source_ref, _payload in recognized],
        "output_refs": output_refs,
        "row_count": primary.get("row_count"),
        "pair_coverage": primary.get("pair_coverage", {}),
        "column_schema": primary.get("column_schema", []),
        "units": primary.get("units", {}),
        "sign_convention": primary.get("sign_convention", {}),
        "temporal_ordering_rule": primary.get("temporal_ordering_rule"),
        "uncertainty_fields": primary.get("uncertainty_fields", {}),
        "gaps": gaps,
        "limitations": limitations,
    }
    return {
        "summary": summary,
        "supporting_refs": [
            *summary["source_receipt_refs"],
            *output_refs,
        ],
    }


def adapt_v1_producer_output(
    *,
    stage: str,
    version: int,
    phase: str,
    text: str,
    evidence_refs: list[str],
    canonical_documents: list[dict[str, Any]] | None = None,
    current_task_id: str | None = None,
    current_harness_receipt_ref: str | None = None,
    source_manifest: list[dict[str, Any]] | None = None,
    canonical_source_manifest: list[dict[str, Any]] | None = None,
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
    if source_manifest is not None and canonical_source_manifest is not None:
        raise ValueError("provide source_manifest only once")
    canonical_manifest = (
        source_manifest if source_manifest is not None else canonical_source_manifest
    )
    harness_projection = _harness_projection(
        decoded,
        canonical_documents=documents,
        current_task_id=current_task_id,
        source_manifest=canonical_manifest,
        current_harness_receipt_ref=current_harness_receipt_ref,
    )
    projected_evidence_refs = [
        ref for ref in evidence_refs if not ref.startswith(_HARNESS_PREFIX)
    ]
    claim_supporting_refs = list(projected_evidence_refs)
    if harness_projection is not None:
        projected_evidence_refs = sorted(
            {
                *projected_evidence_refs,
                *harness_projection["evidence_refs"],
            }
        )[:200]
    claims = _claims_from_known_v1(
        stage=stage,
        version=version,
        documents=documents,
    )
    hypothesis_projection = (
        _hypothesis_projection(documents) if stage == "hypothesis" else None
    )
    experiment_projection = (
        _experiment_projection(documents) if stage == "experiment_result" else None
    )
    data_projection = (
        _data_result_projection(
            documents,
            current_task_id=current_task_id,
            source_manifest=canonical_manifest,
        )
        if stage == "data"
        else None
    )
    if data_projection is not None:
        summary = data_projection["summary"]
        verified_data_refs = {
            ref for ref in data_projection["supporting_refs"] if isinstance(ref, str)
        }
        verified_data_refs.update(
            row["source_ref"]
            for row in documents
            if isinstance(row.get("source_ref"), str)
        )
        projected_evidence_refs = sorted(
            ref for ref in projected_evidence_refs if ref in verified_data_refs
        )
        claims = [
            _claim(
                claim_id=f"data-structured-v{version}",
                kind="observation",
                text=json.dumps(
                    summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
                scope="Task-bound solar-cycle Data product and pair coverage.",
                supporting=list(data_projection["supporting_refs"]),
                limiting=[],
                unknowns=[
                    json.dumps(gap, ensure_ascii=False, sort_keys=True)
                    for gap in summary.get("gaps", [])
                    if isinstance(gap, dict)
                ],
            )
        ]
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
        for claim in claims:
            supporting = claim.get("supporting_evidence")
            if isinstance(supporting, list):
                claim["supporting_evidence"] = [
                    ref
                    for ref in supporting
                    if not isinstance(ref, str) or not ref.startswith(_HARNESS_PREFIX)
                ]
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
                "supporting_evidence": claim_supporting_refs,
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
            hypothesis_projection["evidence_refs"] or projected_evidence_refs
            if hypothesis_projection is not None
            else projected_evidence_refs
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
                {"harness_evidence": harness_projection}
                if harness_projection is not None
                else {}
            ),
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
            **(
                {"experiment_result_summary": experiment_projection}
                if experiment_projection is not None
                else {}
            ),
            **(
                {"data_result_summary": data_projection["summary"]}
                if data_projection is not None
                else {}
            ),
        },
    }


def _experiment_projection(
    documents: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Project only verified fields already persisted by Automatic Experiment."""

    scientific_outcomes = {
        "completed_interpretable",
        "scientific_null",
        "high_uncertainty",
        "partial_result",
    }
    outcome_map = {
        "completed_interpretable": "completed",
        "scientific_null": "null_result",
        "high_uncertainty": "uncertain",
        "partial_result": "uncertain",
        "technical_failure": "technical_failure",
        "budget_reached": "technical_failure",
        "budget_stopped": "technical_failure",
    }
    for row in documents:
        source_ref = row.get("source_ref")
        payload = row.get("payload")
        if not (
            isinstance(source_ref, str)
            and source_ref.endswith("/record.json")
            and isinstance(payload, dict)
            and payload.get("schema_version") == "automatic-experiment-record-v1"
        ):
            continue
        worker = payload.get("worker_result")
        worker = worker if isinstance(worker, dict) else {}
        assessment = payload.get("scientific_assessment")
        assessment = assessment if isinstance(assessment, dict) else {}
        raw_outcome = str(
            payload.get("outcome") or assessment.get("proposed_outcome") or ""
        )
        scientific_result_available = raw_outcome in scientific_outcomes and bool(
            worker.get("execution_completed")
        )
        measurements: list[dict[str, str]] = []
        worker_measurements = (
            worker.get("measurements") or [] if scientific_result_available else []
        )
        for measurement in worker_measurements:
            if not isinstance(measurement, dict) or not measurement.get("name"):
                continue
            unit = str(measurement.get("unit") or "").strip()
            value = measurement.get("value")
            value_text = f"{value}{' ' + unit if unit else ''}"
            definition_parts = [str(measurement.get("role") or "measurement")]
            if measurement.get("source_artifact"):
                definition_parts.append(
                    f"source artifact {measurement['source_artifact']}"
                )
            measurements.append(
                {
                    "name": str(measurement["name"])[:200],
                    "value_text": value_text[:500],
                    "definition": "; ".join(definition_parts)[:500],
                }
            )
        result_items = (
            [
                item
                for item in worker.get("result_items") or []
                if isinstance(item, dict)
            ]
            if scientific_result_available
            else []
        )
        for item in result_items:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            measurements.append(
                {
                    "name": str(item["id"])[:200],
                    "value_text": str(item.get("value"))[:500],
                    "definition": str(
                        item.get("display_name") or item.get("role") or "result item"
                    )[:500],
                }
            )
        uncertainty: list[str] = []
        for source in (
            assessment.get("uncertainty_reasons"),
            (worker.get("scientific_payload") or {}).get("uncertainty_reasons")
            if isinstance(worker.get("scientific_payload"), dict)
            else None,
        ):
            for value in source if isinstance(source, list) else []:
                text = str(value).strip()
                if text and text not in uncertainty:
                    uncertainty.append(text)
        reason = str(payload.get("outcome_reason") or "").strip()
        if reason and reason not in uncertainty:
            uncertainty.insert(0, reason)
        execution_completed = scientific_result_available
        outcome = (
            outcome_map.get(raw_outcome, "technical_failure")
            if scientific_result_available
            else "technical_failure"
        )
        result_by_id = {
            str(item.get("id")): item
            for item in result_items
            if isinstance(item.get("id"), str)
        }

        diagnostic_reasons: list[str] = []
        interval_low = _numeric_result(result_by_id, "primary_interval_low")
        interval_high = _numeric_result(result_by_id, "primary_interval_high")
        if (
            interval_low is not None
            and interval_high is not None
            and interval_low <= 0 <= interval_high
        ):
            diagnostic_reasons.append("The primary interval crosses zero.")
        candidate_mae = _numeric_result(result_by_id, "candidate_mae")
        baseline_mae = _numeric_result(result_by_id, "baseline_mae")
        candidate_rmse = _numeric_result(result_by_id, "candidate_rmse")
        baseline_rmse = _numeric_result(result_by_id, "baseline_rmse")
        if None not in {
            candidate_mae,
            baseline_mae,
            candidate_rmse,
            baseline_rmse,
        } and ((candidate_mae < baseline_mae) != (candidate_rmse < baseline_rmse)):
            diagnostic_reasons.append(
                "MAE and RMSE disagree on whether the candidate improves the baseline."
            )
        if _boolean_result(result_by_id, "out_of_sample_complete") is False:
            diagnostic_reasons.append(
                "The registered out-of-sample evaluation is incomplete."
            )
        if _boolean_result(result_by_id, "leave_one_unit_direction_stable") is False:
            diagnostic_reasons.append(
                "The effect direction is not stable to leave-one-unit analysis."
            )
        if _boolean_result(result_by_id, "independent_sample_adequate") is False:
            diagnostic_reasons.append(
                "The independent sample is inadequate for the fitted complexity."
            )
        if _boolean_result(result_by_id, "influential_unit_changes_conclusion") is True:
            diagnostic_reasons.append(
                "An influential held-out unit changes the conclusion."
            )
        if diagnostic_reasons:
            relation_metric = next(
                (
                    metric
                    for metric in measurements
                    if metric["name"] == "hypothesis_relation"
                ),
                None,
            )
            if relation_metric is None:
                measurements.append(
                    {
                        "name": "hypothesis_relation",
                        "value_text": "uncertain",
                        "definition": "Deterministically reconciled from registered diagnostics.",
                    }
                )
            else:
                relation_metric["value_text"] = "uncertain"
                relation_metric["definition"] = (
                    "Deterministically reconciled from registered diagnostics; "
                    + relation_metric["definition"]
                )[:500]
            outcome = "uncertain"
            for diagnostic_reason in diagnostic_reasons:
                if diagnostic_reason not in uncertainty:
                    uncertainty.append(diagnostic_reason)
        return {
            "source_ref": source_ref,
            "execution_completed": execution_completed,
            "outcome": outcome,
            "metrics": measurements[:40],
            "uncertainty_notes": " ".join(uncertainty)[:2_000]
            or "No additional uncertainty note was recorded.",
            "record_sha256": None,
        }
    return None


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
        draft = payload.get("checkpoint") or payload.get("latest_draft")
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
            draft = payload.get("checkpoint") or payload.get("latest_draft")
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
            projection = _experiment_projection([row])
            assert projection is not None
            metric_text = "; ".join(
                f"{metric['name']}={metric['value_text']}"
                for metric in projection["metrics"]
            )
            reason = str(payload.get("outcome_reason") or payload.get("outcome"))
            text = (
                reason
                if not metric_text
                else f"{reason} Verified results: {metric_text}"
            )
            return [
                _claim(
                    claim_id=f"experiment-result-v{version}",
                    kind="observation",
                    text=text,
                    scope=str(payload.get("task") or "Verified experiment result"),
                    supporting=[source_ref],
                    confidence=(
                        "medium" if projection["outcome"] == "completed" else "low"
                    ),
                    unknowns=(
                        []
                        if projection["outcome"] == "completed"
                        else [projection["uncertainty_notes"]]
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
