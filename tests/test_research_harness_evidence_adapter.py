from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from jw.research_review import ResearchReviewStore
from research_review.adapters import adapt_v1_producer_output


def _adapt_canonical_harness_receipt(
    *,
    envelope: dict[str, object],
    receipt: dict[str, object],
    manifest: list[dict[str, object]],
) -> dict[str, object]:
    receipt_ref = str(envelope["receipt_ref"])
    return adapt_v1_producer_output(
        stage="data",
        version=1,
        phase="data",
        text=json.dumps(
            {
                "task_id": "task-1",
                "harness_evidence": envelope,
            }
        ),
        evidence_refs=[],
        canonical_documents=[
            {
                "source_ref": receipt_ref,
                "payload": receipt,
            }
        ],
        current_task_id="task-1",
        source_manifest=manifest,
    )


def _canonical_page_case(
    *,
    receipt_status: str = "completed",
    page_ref: str = "research_review/harness/task-1/run-new/sources/extracted-1.md",
    declare_artifact: bool = True,
    page_bytes: int = 16,
) -> tuple[dict[str, object], dict[str, object], list[dict[str, object]]]:
    receipt_ref = "research_review/harness/task-1/run-new/receipt.json"
    page_sha = "a" * 64
    receipt_sha = "b" * 64
    envelope = {
        "schema_version": "harness-evidence-v1",
        "status": "completed",
        "task_id": "task-1",
        "binding": {"task_id": "task-1", "focus": "polar field"},
        "receipt_ref": receipt_ref,
        "items": [
            {
                "source_ref": page_ref,
                "tool": "web_extractor",
                "source_class": "retrieved_text",
                "evidence_scope": "full_text",
                "claim_role": "gap",
                "url": "https://example.test/paper",
                "quote_or_excerpt": "Extracted source text.",
            }
        ],
        "artifacts": [{"path": page_ref, "sha256": page_sha, "bytes": page_bytes}],
    }
    receipt = {
        "schema_version": "harness-evidence-v1",
        "status": receipt_status,
        "task_id": "task-1",
        "binding": {"task_id": "task-1", "focus": "polar field"},
        "items": list(envelope["items"]),
        "artifacts": list(envelope["artifacts"]) if declare_artifact else [],
        "analysis_inputs": [],
        "limitations": [],
    }
    manifest = [
        {"source_ref": receipt_ref, "sha256": receipt_sha, "bytes": 500},
        {"source_ref": page_ref, "sha256": page_sha, "bytes": page_bytes},
    ]
    return envelope, receipt, manifest


def _strict_receipt_case(
    *,
    envelope: dict[str, object] | None = None,
    receipt: dict[str, object] | None = None,
    manifest: list[dict[str, object]] | None = None,
    raw_receipt: bytes | None = None,
) -> tuple[
    dict[str, object],
    dict[str, object],
    list[dict[str, object]],
    list[dict[str, object]],
    str,
]:
    base_envelope, base_receipt, base_manifest = _canonical_page_case()
    envelope = dict(envelope or base_envelope)
    receipt = dict(receipt or base_receipt)
    manifest = [dict(row) for row in (manifest or base_manifest)]
    receipt_ref = str(envelope["receipt_ref"])
    raw_receipt = raw_receipt or (
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True).encode(
            "utf-8"
        )
        + b"\n"
    )
    receipt_digest = hashlib.sha256(raw_receipt).hexdigest()
    for row in manifest:
        if row.get("source_ref") == receipt_ref:
            row.update({"sha256": receipt_digest, "bytes": len(raw_receipt)})
            break
    else:
        manifest.append(
            {
                "source_ref": receipt_ref,
                "sha256": receipt_digest,
                "bytes": len(raw_receipt),
            }
        )
    documents = [
        {
            "source_ref": receipt_ref,
            "payload": receipt,
            "raw_bytes": raw_receipt,
        }
    ]
    return envelope, receipt, manifest, documents, receipt_ref


def _strict_adapt(
    *,
    envelope: dict[str, object],
    manifest: list[dict[str, object]],
    documents: list[dict[str, object]],
    receipt_ref: str,
) -> dict[str, object]:
    return adapt_v1_producer_output(
        stage="data",
        version=1,
        phase="data",
        text=json.dumps(
            {"task_id": "task-1", "harness_evidence": envelope},
            ensure_ascii=False,
        ),
        evidence_refs=[],
        canonical_documents=documents,
        current_task_id="task-1",
        current_harness_receipt_ref=receipt_ref,
        source_manifest=manifest,
    )


def test_canonical_receipt_requires_current_invocation_and_raw_manifest_binding() -> (
    None
):
    envelope, _receipt, manifest, documents, receipt_ref = _strict_receipt_case()

    adapted = _strict_adapt(
        envelope=envelope,
        manifest=manifest,
        documents=documents,
        receipt_ref=receipt_ref,
    )

    assert adapted["payload"]["harness_evidence"]["candidate_evidence_refs"] == [
        str(envelope["items"][0]["source_ref"])
    ]


@pytest.mark.parametrize("variant", ["missing_ref", "unmanifested_receipt", "old_ref"])
def test_canonical_harness_never_falls_back_to_self_reported_envelope(
    variant: str,
) -> None:
    envelope, receipt, manifest, documents, current_ref = _strict_receipt_case()
    if variant == "missing_ref":
        envelope.pop("receipt_ref", None)
    elif variant == "unmanifested_receipt":
        manifest = [row for row in manifest if row.get("source_ref") != current_ref]
    else:
        old_ref = current_ref.replace("run-new", "run-old")
        envelope["receipt_ref"] = old_ref

    adapted = _strict_adapt(
        envelope=envelope,
        manifest=manifest,
        documents=documents,
        receipt_ref=current_ref,
    )

    assert adapted["payload"]["harness_evidence"]["candidate_evidence_refs"] == []


def test_canonical_receipt_content_hash_mismatch_fails_closed() -> None:
    envelope, _receipt, manifest, documents, receipt_ref = _strict_receipt_case(
        raw_receipt=b'{"tampered":true}\n'
    )
    documents[0]["raw_bytes"] = b'{"different":true}\n'

    adapted = _strict_adapt(
        envelope=envelope,
        manifest=manifest,
        documents=documents,
        receipt_ref=receipt_ref,
    )

    assert adapted["payload"]["harness_evidence"]["candidate_evidence_refs"] == []


def test_conflicting_duplicate_manifest_rows_fail_closed() -> None:
    envelope, _receipt, manifest, documents, receipt_ref = _strict_receipt_case()
    page_ref = str(envelope["items"][0]["source_ref"])
    manifest.append({"source_ref": page_ref, "sha256": "f" * 64, "bytes": 1})

    adapted = _strict_adapt(
        envelope=envelope,
        manifest=manifest,
        documents=documents,
        receipt_ref=receipt_ref,
    )

    assert adapted["payload"]["harness_evidence"]["candidate_evidence_refs"] == []


@pytest.mark.parametrize(
    "invalid_ref",
    [
        "research_review/harness/task-1/run-new/../run-new/receipt.json",
        "/tmp/receipt.json",
        "research_review/harness/task-foreign/run-new/receipt.json",
        "research_review/harness/task-1/run-newer/receipt.json",
    ],
)
def test_receipt_ref_path_and_invocation_must_match_exactly(invalid_ref: str) -> None:
    envelope, _receipt, manifest, documents, current_ref = _strict_receipt_case()
    envelope["receipt_ref"] = invalid_ref

    adapted = _strict_adapt(
        envelope=envelope,
        manifest=manifest,
        documents=documents,
        receipt_ref=current_ref,
    )

    assert adapted["payload"]["harness_evidence"]["candidate_evidence_refs"] == []


def test_duplicate_canonical_receipt_documents_fail_closed() -> None:
    envelope, _receipt, manifest, documents, receipt_ref = _strict_receipt_case()
    documents.append(dict(documents[0]))

    adapted = _strict_adapt(
        envelope=envelope,
        manifest=manifest,
        documents=documents,
        receipt_ref=receipt_ref,
    )

    assert adapted["payload"]["harness_evidence"]["candidate_evidence_refs"] == []


def test_zero_byte_analysis_input_cannot_authorize_calculation() -> None:
    envelope, receipt, manifest, documents, receipt_ref = _strict_receipt_case()
    input_ref = "inputs/empty.csv"
    calc_ref = str(envelope["items"][0]["source_ref"]).replace(
        "sources/extracted-1.md", "calculations/analysis.json"
    )
    input_sha = "c" * 64
    calc_sha = "d" * 64
    receipt["analysis_inputs"] = [
        {"source_ref": input_ref, "sha256": input_sha, "bytes": 0}
    ]
    receipt["items"] = [
        {
            "source_ref": calc_ref,
            "source_class": "derived_calculation",
            "evidence_scope": "experiment_record",
            "claim_role": "gap",
        }
    ]
    receipt["artifacts"] = [{"path": calc_ref, "sha256": calc_sha, "bytes": 10}]
    envelope["receipt_ref"] = receipt_ref
    envelope["items"] = list(receipt["items"])
    envelope["artifacts"] = list(receipt["artifacts"])
    manifest.extend(
        [
            {"source_ref": input_ref, "sha256": input_sha, "bytes": 0},
            {"source_ref": calc_ref, "sha256": calc_sha, "bytes": 10},
        ]
    )
    raw = (
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True).encode(
            "utf-8"
        )
        + b"\n"
    )
    documents[0]["raw_bytes"] = raw
    for row in manifest:
        if row.get("source_ref") == receipt_ref:
            row.update({"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})

    adapted = _strict_adapt(
        envelope=envelope,
        manifest=manifest,
        documents=documents,
        receipt_ref=receipt_ref,
    )

    assert adapted["payload"]["harness_evidence"]["candidate_evidence_refs"] == []


def test_canonical_partial_receipt_cannot_be_wrapped_as_completed() -> None:
    envelope, receipt, manifest = _canonical_page_case(receipt_status="partial")

    adapted = _adapt_canonical_harness_receipt(
        envelope=envelope,
        receipt=receipt,
        manifest=manifest,
    )

    harness = adapted["payload"]["harness_evidence"]
    assert harness["status"] == "partial"
    assert harness["candidate_evidence_refs"] == []


def test_canonical_receipt_rejects_item_from_older_invocation() -> None:
    old_ref = "research_review/harness/task-1/run-old/sources/extracted-1.md"
    envelope, receipt, manifest = _canonical_page_case(page_ref=old_ref)

    adapted = _adapt_canonical_harness_receipt(
        envelope=envelope,
        receipt=receipt,
        manifest=manifest,
    )

    harness = adapted["payload"]["harness_evidence"]
    assert old_ref not in harness["candidate_evidence_refs"]
    assert old_ref not in harness["evidence_refs"]


def test_canonical_receipt_rejects_page_not_declared_as_artifact() -> None:
    envelope, receipt, manifest = _canonical_page_case(declare_artifact=False)

    adapted = _adapt_canonical_harness_receipt(
        envelope=envelope,
        receipt=receipt,
        manifest=manifest,
    )

    assert adapted["payload"]["harness_evidence"]["candidate_evidence_refs"] == []


def test_canonical_receipt_rejects_zero_byte_extracted_page() -> None:
    envelope, receipt, manifest = _canonical_page_case(page_bytes=0)

    adapted = _adapt_canonical_harness_receipt(
        envelope=envelope,
        receipt=receipt,
        manifest=manifest,
    )

    assert adapted["payload"]["harness_evidence"]["candidate_evidence_refs"] == []


def test_canonical_completed_receipt_admits_hash_matched_page() -> None:
    envelope, receipt, manifest = _canonical_page_case()
    page_ref = str(receipt["artifacts"][0]["path"])

    adapted = _adapt_canonical_harness_receipt(
        envelope=envelope,
        receipt=receipt,
        manifest=manifest,
    )

    harness = adapted["payload"]["harness_evidence"]
    assert harness["receipt_ref"] == envelope["receipt_ref"]
    assert harness["status"] == "completed"
    assert harness["candidate_evidence_refs"] == [page_ref]


def test_data_adapter_preserves_harness_binding_and_declared_sources() -> None:
    extracted_ref = "research_review/harness/task-1/sources/extracted-1.md"
    receipt_ref = "research_review/harness/task-1/receipt.json"
    extracted_sha = "a" * 64
    receipt_sha = "b" * 64
    text = json.dumps(
        {
            "schema_version": "solar-data-harness-result-v1",
            "status": "completed",
            "task_id": "task-1",
            "artifact_refs": ["research_review/harness/task-1/sources/extracted-1.md"],
            "receipt_refs": ["research_review/harness/task-1/receipt.json"],
            "harness_evidence": {
                "schema_version": "harness-evidence-v1",
                "status": "completed",
                "task_id": "task-1",
                "binding": {"focus": "polar field precursor"},
                "items": [
                    {
                        "source_ref": extracted_ref,
                        "tool": "web_extractor",
                        "source_class": "retrieved_text",
                        "evidence_scope": "full_text",
                        "claim_role": "gap",
                        "url": "https://example.test/paper",
                        "locator": "page 2",
                    },
                    {
                        "source_ref": "research_review/harness/task-1/unmanifested.md",
                        "source_class": "retrieved_text",
                        "evidence_scope": "full_text",
                        "claim_role": "supports",
                        "url": "https://example.test/unmanifested",
                    },
                ],
                "artifacts": [
                    {"path": extracted_ref, "sha256": extracted_sha},
                    {"path": receipt_ref, "sha256": receipt_sha},
                ],
                "limitations": ["external source requires independent review"],
            },
        },
        ensure_ascii=False,
    )

    adapted = adapt_v1_producer_output(
        stage="data",
        version=1,
        phase="data",
        text=text,
        evidence_refs=["receipts/datasets/data-context-test.json"],
        current_task_id="task-1",
        canonical_source_manifest=[
            {"source_ref": extracted_ref, "sha256": extracted_sha},
            {"source_ref": receipt_ref, "sha256": receipt_sha},
        ],
    )

    assert adapted["payload"]["harness_evidence"]["binding"]["focus"] == (
        "polar field precursor"
    )
    harness = adapted["payload"]["harness_evidence"]
    assert harness["items"][0]["claim_role"] == "gap"
    assert harness["items"][0]["evidence_scope"] == "full_text"
    assert harness["evidence_refs"] == sorted([extracted_ref, receipt_ref])
    assert harness["candidate_evidence_refs"] == [extracted_ref]
    assert (
        "research_review/harness/task-1/unmanifested.md" not in harness["evidence_refs"]
    )
    assert extracted_ref not in adapted["claims"][0]["supporting_evidence"]
    assert receipt_ref not in adapted["claims"][0]["supporting_evidence"]
    assert (
        "receipts/datasets/data-context-test.json"
        in adapted["claims"][0]["supporting_evidence"]
    )


def test_harness_adapter_rejects_foreign_task_binding() -> None:
    text = json.dumps(
        {
            "harness_evidence": {
                "schema_version": "harness-evidence-v1",
                "status": "completed",
                "task_id": "task-foreign",
                "binding": {"task_id": "task-foreign"},
                "items": [],
                "artifacts": [],
            }
        }
    )

    with pytest.raises(ValueError, match="task"):
        adapt_v1_producer_output(
            stage="data",
            version=1,
            phase="data",
            text=text,
            evidence_refs=[],
            current_task_id="task-current",
            canonical_source_manifest=[],
        )


def test_self_reported_calculation_hash_booleans_do_not_make_candidate() -> None:
    refs = {
        "lead": "research_review/harness/task-1/sources/lead.json",
        "trace": "research_review/harness/task-1/run/trace.json",
        "calc": "research_review/harness/task-1/calculations/analysis.json",
    }
    text = json.dumps(
        {
            "harness_evidence": {
                "schema_version": "harness-evidence-v1",
                "status": "completed",
                "task_id": "task-1",
                "items": [
                    {
                        "source_ref": refs["lead"],
                        "source_class": "external_lead",
                        "evidence_scope": "web_result",
                        "claim_role": "gap",
                        "url": "https://example.test/lead",
                    },
                    {
                        "source_ref": refs["trace"],
                        "source_class": "trace",
                        "evidence_scope": "unknown",
                        "claim_role": "gap",
                    },
                    {
                        "source_ref": refs["calc"],
                        "source_class": "derived_calculation",
                        "evidence_scope": "experiment_record",
                        "claim_role": "supports",
                        "status": "completed",
                        "input_sha256": "c" * 64,
                        "output_sha256": "d" * 64,
                        "input_hash_valid": True,
                        "output_hash_valid": True,
                    },
                ],
                "artifacts": [
                    {"path": ref, "sha256": digest}
                    for ref, digest in zip(
                        refs.values(), ("1" * 64, "2" * 64, "3" * 64)
                    )
                ],
            }
        }
    )
    manifest = [
        {"source_ref": ref, "sha256": digest}
        for ref, digest in zip(refs.values(), ("1" * 64, "2" * 64, "3" * 64))
    ]

    adapted = adapt_v1_producer_output(
        stage="data",
        version=1,
        phase="data",
        text=text,
        evidence_refs=[],
        current_task_id="task-1",
        canonical_source_manifest=manifest,
    )

    harness = adapted["payload"]["harness_evidence"]
    assert harness["candidate_evidence_refs"] == []
    assert refs["lead"] in harness["provenance_refs"]
    assert refs["trace"] in harness["provenance_refs"]
    assert refs["lead"] not in harness["candidate_evidence_refs"]
    assert refs["trace"] not in harness["candidate_evidence_refs"]


@pytest.mark.parametrize(
    "harness_evidence",
    [
        None,
        {
            "schema_version": "not-harness-evidence",
            "task_id": "task-1",
            "binding": {"task_id": "task-1"},
        },
    ],
)
def test_invalid_harness_envelope_never_supports_prose_path(
    harness_evidence: dict[str, object] | None,
) -> None:
    response_ref = "research_review/harness/task-1/run/response.json"
    decoded: dict[str, object] = {
        "summary": f"Provider response at {response_ref}",
    }
    if harness_evidence is not None:
        decoded["harness_evidence"] = harness_evidence

    adapted = adapt_v1_producer_output(
        stage="data",
        version=1,
        phase="data",
        text=json.dumps(decoded),
        evidence_refs=[response_ref],
        current_task_id="task-1",
        source_manifest=[{"source_ref": response_ref, "sha256": "a" * 64}],
    )

    assert response_ref not in adapted["claims"][0]["supporting_evidence"]
    assert response_ref not in adapted["evidence_refs"]


def test_harness_envelope_requires_current_task_id() -> None:
    text = json.dumps(
        {
            "harness_evidence": {
                "schema_version": "harness-evidence-v1",
                "status": "completed",
                "items": [],
                "artifacts": [],
            }
        }
    )

    with pytest.raises(ValueError, match="task_id"):
        adapt_v1_producer_output(
            stage="data",
            version=1,
            phase="data",
            text=text,
            evidence_refs=[],
            current_task_id="task-1",
            source_manifest=[],
        )


@pytest.mark.parametrize(
    "basename", ["request.json", "response.json", "receipt.json", "trace.json"]
)
def test_harness_runtime_record_path_cannot_disguise_itself_as_extracted_page(
    basename: str,
) -> None:
    source_ref = f"research_review/harness/task-1/run/{basename}"
    text = json.dumps(
        {
            "harness_evidence": {
                "schema_version": "harness-evidence-v1",
                "status": "completed",
                "task_id": "task-1",
                "binding": {"task_id": "task-1"},
                "receipt_ref": (source_ref if basename == "receipt.json" else None),
                "items": [
                    {
                        "source_ref": source_ref,
                        "source_class": "retrieved_text",
                        "evidence_scope": "full_text",
                        "claim_role": "supports",
                        "url": "https://example.test/paper",
                    }
                ],
                "artifacts": [{"path": source_ref, "sha256": "b" * 64}],
            }
        }
    )

    adapted = adapt_v1_producer_output(
        stage="data",
        version=1,
        phase="data",
        text=text,
        evidence_refs=[source_ref],
        current_task_id="task-1",
        source_manifest=[{"source_ref": source_ref, "sha256": "b" * 64}],
    )

    harness = adapted["payload"]["harness_evidence"]
    assert source_ref not in harness["candidate_evidence_refs"]
    assert source_ref in harness["provenance_refs"]


def test_completed_calculation_uses_receipt_input_and_output_hashes() -> None:
    input_ref = "outputs/task-input.csv"
    calculation_ref = "research_review/harness/task-1/calculations/analysis.json"
    input_sha = "4" * 64
    calculation_sha = "5" * 64
    text = json.dumps(
        {
            "harness_evidence": {
                "schema_version": "harness-evidence-v1",
                "status": "completed",
                "task_id": "task-1",
                "analysis_inputs": [
                    {"source_ref": input_ref, "sha256": input_sha, "bytes": 10}
                ],
                "items": [
                    {
                        "source_ref": calculation_ref,
                        "source_class": "derived_calculation",
                        "evidence_scope": "experiment_record",
                        "claim_role": "gap",
                    }
                ],
                "artifacts": [{"path": calculation_ref, "sha256": calculation_sha}],
            }
        }
    )

    adapted = adapt_v1_producer_output(
        stage="data",
        version=1,
        phase="data",
        text=text,
        evidence_refs=[input_ref],
        current_task_id="task-1",
        source_manifest=[
            {"source_ref": input_ref, "sha256": input_sha},
            {"source_ref": calculation_ref, "sha256": calculation_sha},
        ],
    )

    assert adapted["payload"]["harness_evidence"]["candidate_evidence_refs"] == [
        calculation_ref
    ]


@pytest.mark.parametrize("status", ["error", "partial", "incomplete"])
def test_item_status_cannot_override_incomplete_harness_calculation(
    status: str,
) -> None:
    input_ref = "outputs/task-input.csv"
    calculation_ref = "research_review/harness/task-1/calculations/analysis.json"
    input_sha = "4" * 64
    calculation_sha = "5" * 64
    text = json.dumps(
        {
            "harness_evidence": {
                "schema_version": "harness-evidence-v1",
                "status": status,
                "task_id": "task-1",
                "binding": {"task_id": "task-1"},
                "analysis_inputs": [
                    {"source_ref": input_ref, "sha256": input_sha, "bytes": 10}
                ],
                "items": [
                    {
                        "source_ref": calculation_ref,
                        "source_class": "derived_calculation",
                        "evidence_scope": "experiment_record",
                        "claim_role": "gap",
                        "status": "completed",
                    }
                ],
                "artifacts": [{"path": calculation_ref, "sha256": calculation_sha}],
            }
        }
    )

    adapted = adapt_v1_producer_output(
        stage="data",
        version=1,
        phase="data",
        text=text,
        evidence_refs=[input_ref],
        current_task_id="task-1",
        source_manifest=[
            {"source_ref": input_ref, "sha256": input_sha},
            {"source_ref": calculation_ref, "sha256": calculation_sha},
        ],
    )

    assert adapted["payload"]["harness_evidence"]["candidate_evidence_refs"] == []


@pytest.mark.parametrize(
    ("tool", "scope", "url", "basename"),
    [
        ("web_search", "full_text", "https://example.test/paper", "extracted-1.md"),
        ("web_extractor", "web_result", "https://example.test/paper", "extracted-1.md"),
        ("web_extractor", "full_text", "http://example.test/paper", "extracted-1.md"),
        ("web_extractor", "full_text", "https://example.test/paper", "search-1.json"),
    ],
)
def test_only_real_https_extractor_pages_are_candidates(
    tool: str, scope: str, url: str, basename: str
) -> None:
    source_ref = f"research_review/harness/task-1/run/sources/{basename}"
    digest = "9" * 64
    text = json.dumps(
        {
            "harness_evidence": {
                "schema_version": "harness-evidence-v1",
                "status": "completed",
                "task_id": "task-1",
                "binding": {"task_id": "task-1"},
                "items": [
                    {
                        "source_ref": source_ref,
                        "tool": tool,
                        "source_class": "retrieved_text",
                        "evidence_scope": scope,
                        "claim_role": "supports",
                        "url": url,
                    }
                ],
                "artifacts": [{"path": source_ref, "sha256": digest}],
            }
        }
    )

    adapted = adapt_v1_producer_output(
        stage="data",
        version=1,
        phase="data",
        text=text,
        evidence_refs=[source_ref],
        current_task_id="task-1",
        source_manifest=[{"source_ref": source_ref, "sha256": digest}],
    )

    harness = adapted["payload"]["harness_evidence"]
    assert harness["candidate_evidence_refs"] == []
    assert source_ref in harness["provenance_refs"]
    assert source_ref in harness["gap_refs"]


def test_request_response_and_receipt_stay_provenance_only() -> None:
    run_root = "research_review/harness/task-1/run-abc"
    refs = [
        f"{run_root}/request.json",
        f"{run_root}/response.json",
        f"{run_root}/receipt.json",
    ]
    manifest = [
        {"source_ref": source_ref, "sha256": str(index) * 64}
        for index, source_ref in enumerate(refs, start=6)
    ]
    text = json.dumps(
        {
            "harness_evidence": {
                "schema_version": "harness-evidence-v1",
                "status": "completed",
                "task_id": "task-1",
                "receipt_ref": refs[-1],
                "items": [],
                "artifacts": [],
            }
        }
    )

    adapted = adapt_v1_producer_output(
        stage="data",
        version=1,
        phase="data",
        text=text,
        evidence_refs=refs,
        current_task_id="task-1",
        source_manifest=manifest,
    )

    harness = adapted["payload"]["harness_evidence"]
    assert harness["provenance_refs"] == sorted(refs)
    assert harness["candidate_evidence_refs"] == []
    assert all(ref not in adapted["claims"][0]["supporting_evidence"] for ref in refs)


def test_data_checkpoint_manifest_scans_only_current_task_harness(
    tmp_path: Path,
) -> None:
    current = tmp_path / "research_review" / "harness" / "task-current" / "receipt.json"
    foreign = tmp_path / "research_review" / "harness" / "task-foreign" / "receipt.json"
    current.parent.mkdir(parents=True)
    foreign.parent.mkdir(parents=True)
    current.write_text('{"task_id":"task-current"}', encoding="utf-8")
    foreign.write_text('{"task_id":"task-foreign"}', encoding="utf-8")
    store = ResearchReviewStore(tmp_path, "task-current")

    artifact = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content="bounded task-local data result",
        phase="bounded_data",
    )

    refs = {row["source_ref"] for row in artifact["payload"]["source_manifest"]}
    assert "research_review/harness/task-current/receipt.json" in refs
    assert "research_review/harness/task-foreign/receipt.json" not in refs


def test_review_context_exposes_conservative_harness_roles(tmp_path: Path) -> None:
    source_ref = "research_review/harness/task-1/run/lead.json"
    source = tmp_path / source_ref
    source.parent.mkdir(parents=True)
    source.write_text('{"url":"https://example.test/lead"}', encoding="utf-8")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    envelope = {
        "schema_version": "harness-evidence-v1",
        "status": "completed",
        "task_id": "task-1",
        "binding": {"task_id": "task-1"},
        "items": [
            {
                "source_ref": source_ref,
                "source_class": "external_lead",
                "evidence_scope": "web_result",
                "claim_role": "gap",
                "url": "https://example.test/lead",
            }
        ],
        "artifacts": [{"path": source_ref, "sha256": source_sha}],
    }
    store = ResearchReviewStore(tmp_path, "task-1")
    store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content=json.dumps({"task_id": "task-1", "harness_evidence": envelope}),
        phase="bounded_data",
    )

    metadata = store.review_context("data")["artifacts"][0]["payload_metadata"]

    assert metadata["harness_evidence_roles"] == {
        "candidate_evidence_refs": [],
        "provenance_refs": [source_ref],
        "gap_refs": [source_ref],
    }


def test_receipted_solar_data_output_enters_analysis_candidate_manifest(
    tmp_path: Path,
) -> None:
    task_id = "task-1"
    input_ref = "work/solar_data/generated.csv"
    calculation_ref = "research_review/harness/task-1/run/calculations/analysis.json"
    receipt_ref = "research_review/harness/task-1/run/receipt.json"
    generated = tmp_path / input_ref
    generated.parent.mkdir(parents=True)
    generated.write_text("cycle,value\n24,115\n", encoding="utf-8")
    input_sha = hashlib.sha256(generated.read_bytes()).hexdigest()
    producer_receipt = tmp_path / "receipts" / "datasets" / "producer.json"
    producer_receipt.parent.mkdir(parents=True)
    producer_receipt.write_text(
        json.dumps(
            {
                "schema_version": "research-dataset-receipt-v1",
                "receipt_type": "silso_cycle_extrema_reproduction",
                "status": "verified",
                "producer": "solar-data",
                "task_id": task_id,
                "outputs": [{"path": input_ref, "sha256": input_sha}],
            }
        ),
        encoding="utf-8",
    )
    calculation = tmp_path / calculation_ref
    calculation.parent.mkdir(parents=True)
    calculation.write_text('{"result":0.42}', encoding="utf-8")
    calculation_sha = hashlib.sha256(calculation.read_bytes()).hexdigest()
    envelope = {
        "schema_version": "harness-evidence-v1",
        "status": "completed",
        "task_id": task_id,
        "binding": {"task_id": task_id},
        "receipt_ref": receipt_ref,
        "analysis_inputs": [
            {
                "source_ref": input_ref,
                "sha256": input_sha,
                "bytes": generated.stat().st_size,
            }
        ],
        "items": [
            {
                "source_ref": calculation_ref,
                "source_class": "derived_calculation",
                "evidence_scope": "experiment_record",
                "claim_role": "gap",
            }
        ],
        "artifacts": [
            {
                "path": calculation_ref,
                "sha256": calculation_sha,
                "bytes": calculation.stat().st_size,
            }
        ],
    }
    harness_receipt = tmp_path / receipt_ref
    harness_receipt.write_text(json.dumps(envelope), encoding="utf-8")
    store = ResearchReviewStore(tmp_path, task_id)

    artifact = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content=json.dumps({"task_id": task_id, "harness_evidence": envelope}),
        phase="bounded_data",
    )

    manifest_refs = {
        row["source_ref"] for row in artifact["payload"]["source_manifest"]
    }
    assert input_ref in manifest_refs
    assert artifact["payload"]["harness_evidence"]["candidate_evidence_refs"] == [
        calculation_ref
    ]


@pytest.mark.parametrize("declared", [False, True])
def test_only_task_manifest_declared_input_can_qualify_calculation(
    tmp_path: Path, declared: bool
) -> None:
    task_id = "task-input-manifest"
    input_ref = "inputs/accepted.csv"
    calculation_ref = (
        f"research_review/harness/{task_id}/run/calculations/analysis.json"
    )
    input_path = tmp_path / input_ref
    input_path.parent.mkdir(parents=True)
    input_path.write_text("cycle,value\n24,115\n", encoding="utf-8")
    input_sha = hashlib.sha256(input_path.read_bytes()).hexdigest()
    (tmp_path / "task.json").write_text(
        json.dumps({"thread_id": task_id, "research_question": "question"}),
        encoding="utf-8",
    )
    (tmp_path / "input_manifest.json").write_text(
        json.dumps(
            {
                "inputs": (
                    [
                        {
                            "path": input_ref,
                            "sha256": input_sha,
                            "bytes": input_path.stat().st_size,
                            "role": "user_input",
                        }
                    ]
                    if declared
                    else []
                )
            }
        ),
        encoding="utf-8",
    )
    calculation = tmp_path / calculation_ref
    calculation.parent.mkdir(parents=True)
    calculation.write_text('{"result":0.42}', encoding="utf-8")
    calculation_sha = hashlib.sha256(calculation.read_bytes()).hexdigest()
    envelope = {
        "schema_version": "harness-evidence-v1",
        "status": "completed",
        "task_id": task_id,
        "binding": {"task_id": task_id},
        "analysis_inputs": [
            {
                "source_ref": input_ref,
                "sha256": input_sha,
                "bytes": input_path.stat().st_size,
            }
        ],
        "items": [
            {
                "source_ref": calculation_ref,
                "source_class": "derived_calculation",
                "evidence_scope": "experiment_record",
                "claim_role": "gap",
            }
        ],
        "artifacts": [{"path": calculation_ref, "sha256": calculation_sha}],
    }
    store = ResearchReviewStore(tmp_path, task_id)

    artifact = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content=json.dumps({"task_id": task_id, "harness_evidence": envelope}),
        phase="bounded_data",
    )

    manifest_refs = {
        row["source_ref"] for row in artifact["payload"]["source_manifest"]
    }
    candidates = artifact["payload"]["harness_evidence"]["candidate_evidence_refs"]
    if declared:
        assert input_ref in manifest_refs
        assert candidates == [calculation_ref]
    else:
        assert input_ref not in manifest_refs
        assert candidates == []
