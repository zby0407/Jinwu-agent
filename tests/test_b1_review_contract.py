"""Synthetic contract checks; these fixtures make no solar-science claims."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from jw.research_protocols import (
    B1_FLARE_CASE_DATA_PRODUCT,
    B1_FLARE_CASE_DATASET_IDS,
    B1_FLARE_CASE_PROTOCOL,
    detect_analysis_protocol,
    required_data_product_for_protocol,
    required_dataset_ids_for_protocol,
    resolve_required_dataset_ids,
)
from jw.research_review import ResearchReviewStore, canonical_json_sha256


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(root: Path, path: Path) -> dict:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha(path),
        "bytes": path.stat().st_size,
    }


@pytest.fixture
def contract(tmp_path):
    task_id = "b1-contract-synthetic"
    question = "Synthetic b1-flare-case-v1 contract validation only."
    _json(tmp_path / "task.json", {"thread_id": task_id, "research_question": question})
    names = {
        "magnetic": "magnetic.csv",
        "events": "events.csv",
        "xrs": "xrs.csv",
        "parameters": "parameters.json",
        "sources": "source_manifest.json",
    }
    records = []
    for (kind, name), dataset_id in zip(
        names.items(), B1_FLARE_CASE_DATASET_IDS, strict=True
    ):
        path = tmp_path / "inputs" / name
        _json(path, {"synthetic_input_kind": kind})
        records.append(
            {
                **_record(tmp_path, path),
                "dataset_id": dataset_id,
                "role": "user_input",
                "source_group": "inputs",
            }
        )
    _json(
        tmp_path / "input_manifest.json",
        {"thread_id": task_id, "inputs": records, "project_inputs": []},
    )
    context = {
        "schema_version": "solar-data-context-v1",
        "context_mode": "bounded_data",
        "task_id": task_id,
        "analysis_protocol": B1_FLARE_CASE_PROTOCOL,
        "required_data_product": B1_FLARE_CASE_DATA_PRODUCT,
        "required_dataset_ids": list(B1_FLARE_CASE_DATASET_IDS),
        "missing_required_dataset_ids": [],
        "must_stop": False,
        "status": "inputs_available",
        "eligible_inputs": records,
        "data_steps": [],
        "task_sha256": _sha(tmp_path / "task.json"),
        "input_manifest_sha256": _sha(tmp_path / "input_manifest.json"),
        "research_question_sha256": hashlib.sha256(question.encode()).hexdigest(),
    }
    context_hash = canonical_json_sha256(context)
    context["context_sha256"] = context_hash
    context["created_at"] = "2026-09-22T00:00:00Z"
    context_ref = f"receipts/datasets/data-context-{context_hash[:16]}.json"
    _json(tmp_path / context_ref, context)
    products = tmp_path / "work/solar_data/b1_flare/synthetic/products"
    for name in (
        "report.md",
        "provenance.json",
        "windows.json",
        "windows.csv",
        "event_screening.json",
        "comparisons.csv",
        "output_hashes.json",
        "b1_compute_snapshot.py",
    ):
        _json(products / name, {"synthetic_contract_fixture": True})
    for name in names.values():
        path = products / "inputs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((tmp_path / "inputs" / name).read_bytes())
    _json(
        products / "receipt.json",
        {
            "status": "computed_not_scientifically_reviewed",
            "scientific_review": "not_performed",
            "agent_run": False,
        },
    )
    receipt = {
        "schema_version": "b1-flare-case-receipt-v1",
        "receipt_type": "b1_flare_case",
        "analysis_protocol": B1_FLARE_CASE_PROTOCOL,
        "task_id": task_id,
        "producer": "solar-data",
        "tool": "run_b1_flare_case",
        "attempt_id": "synthetic",
        "status": "verified",
        "calculation_status": "completed",
        "scientific_status": "not_reviewed",
        "scientific_not_reviewed": True,
        "execution_kind": "deterministic_tool",
        "task_sha256": context["task_sha256"],
        "input_manifest_sha256": context["input_manifest_sha256"],
        "research_question_sha256": context["research_question_sha256"],
        "inputs": [
            {**row, "input_kind": kind}
            for row, kind in zip(records, names, strict=True)
        ],
        "parameters_sha256": records[3]["sha256"],
        "sources_sha256": records[4]["sha256"],
        "outputs": [
            _record(tmp_path, path)
            for path in sorted(products.rglob("*"))
            if path.is_file()
        ],
    }
    receipt_ref = "receipts/datasets/b1-flare-synthetic.json"
    _json(tmp_path / receipt_ref, receipt)
    store = ResearchReviewStore(tmp_path, task_id)
    refs = [context_ref, receipt_ref] + [row["path"] for row in receipt["outputs"]]

    def artifact():
        return {
            "stage": "data",
            "payload": {
                "phase": "bounded_data",
                "source_manifest": [
                    {"source_ref": ref, "sha256": _sha(tmp_path / ref)}
                    for ref in refs
                    if (tmp_path / ref).is_file()
                ],
            },
        }

    return store, receipt, context, artifact, tmp_path / receipt_ref, products


def test_protocol_is_explicit_and_has_fixed_five_dataset_mapping():
    assert detect_analysis_protocol("B1 flare precursor study") == "none"
    assert detect_analysis_protocol("A2 JW-FD prediction") == "none"
    assert detect_analysis_protocol("Use b1-flare-case-v1.") == B1_FLARE_CASE_PROTOCOL
    assert detect_analysis_protocol("b1-flare-case-v10") == "none"
    assert (
        required_data_product_for_protocol(B1_FLARE_CASE_PROTOCOL)
        == B1_FLARE_CASE_DATA_PRODUCT
    )
    assert (
        required_dataset_ids_for_protocol(B1_FLARE_CASE_PROTOCOL)
        == B1_FLARE_CASE_DATASET_IDS
    )
    with pytest.raises(ValueError, match="semantics conflict"):
        resolve_required_dataset_ids(
            {"required_datasets": [{"selected_source_id": "jw-fd"}]},
            B1_FLARE_CASE_PROTOCOL,
        )


def test_current_complete_calculation_is_reviewable_but_not_scientifically_accepted(
    contract,
):
    store, receipt, context, artifact, receipt_path, _products = contract
    assert store._b1_flare_receipt_defect(receipt, context) is None
    assert store._data_input_boundary_issues([artifact()]) == []
    assert len(store._receipted_solar_data_outputs([receipt_path])) == len(
        receipt["outputs"]
    )
    assert store._accepted_stage("data") is None
    assert receipt["scientific_status"] == "not_reviewed"


@pytest.mark.parametrize(
    "mutation",
    [
        "input_hash",
        "input_id",
        "duplicate_kind",
        "missing_input",
        "parameters_hash",
        "sources_hash",
        "science_approved",
        "wrong_task",
        "wrong_question",
        "wrong_protocol",
        "missing_product",
    ],
)
def test_forged_or_incomplete_calculation_is_rejected(contract, mutation):
    store, original, context, artifact, receipt_path, _products = contract
    receipt = copy.deepcopy(original)
    if mutation == "input_hash":
        receipt["inputs"][0]["sha256"] = "0" * 64
    elif mutation == "input_id":
        receipt["inputs"][0]["dataset_id"] = "jw-fd"
    elif mutation == "duplicate_kind":
        receipt["inputs"][1]["input_kind"] = "magnetic"
    elif mutation == "missing_input":
        receipt["inputs"].pop()
    elif mutation == "parameters_hash":
        receipt["parameters_sha256"] = "0" * 64
    elif mutation == "sources_hash":
        receipt["sources_sha256"] = "0" * 64
    elif mutation == "science_approved":
        receipt["scientific_status"] = "accepted"
    elif mutation == "wrong_task":
        receipt["task_id"] = "another-task"
    elif mutation == "wrong_question":
        receipt["research_question_sha256"] = "0" * 64
    elif mutation == "wrong_protocol":
        receipt["analysis_protocol"] = "solar_cycle_26_readiness_v1"
    else:
        receipt["outputs"] = [
            row for row in receipt["outputs"] if not row["path"].endswith("report.md")
        ]
    _json(receipt_path, receipt)
    assert store._b1_flare_receipt_defect(receipt, context) is not None
    assert (
        store._data_input_boundary_issues([artifact()])[0]["rule_id"]
        == "DATA_SEMANTICS_BOUND"
    )
    assert store._receipted_solar_data_outputs([receipt_path]) == []


@pytest.mark.parametrize("which", ["input", "output", "task", "manifest", "snapshot"])
def test_changed_live_bytes_cannot_be_accepted(contract, which):
    store, receipt, context, artifact, receipt_path, products = contract
    path = {
        "input": store.workspace_root / "inputs/magnetic.csv",
        "output": products / "report.md",
        "task": store.workspace_root / "task.json",
        "manifest": store.workspace_root / "input_manifest.json",
        "snapshot": products / "inputs/magnetic.csv",
    }[which]
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    if which == "snapshot":
        # Even rehashing a modified snapshot must not sever its input binding.
        receipt["outputs"] = [
            _record(
                store.workspace_root,
                products
                / Path(row["path"]).relative_to(
                    products.relative_to(store.workspace_root)
                ),
            )
            for row in receipt["outputs"]
        ]
        _json(receipt_path, receipt)
    assert store._b1_flare_receipt_defect(receipt, context) is not None
    assert store._data_input_boundary_issues([artifact()])


def test_context_or_receipt_alone_cannot_complete_b1_data(contract):
    store, _receipt, _context, artifact, receipt_path, _products = contract
    candidate = artifact()
    candidate["payload"]["source_manifest"] = [
        row
        for row in candidate["payload"]["source_manifest"]
        if "data-context-" not in row["source_ref"]
    ]
    assert store._data_input_boundary_issues([candidate])
    receipt_path.unlink()
    assert store._data_input_boundary_issues([artifact()])


def test_context_must_match_current_input_hashes(contract):
    store, receipt, context, _artifact, _receipt_path, _products = contract
    changed = copy.deepcopy(context)
    changed["eligible_inputs"][0]["sha256"] = "0" * 64
    assert store._b1_flare_receipt_defect(receipt, changed) is not None


def test_invalid_input_kind_is_a_contract_defect_not_an_exception(contract):
    store, receipt, context, _artifact, _receipt_path, _products = contract
    receipt["inputs"][0]["input_kind"] = ["magnetic"]
    assert store._b1_flare_receipt_defect(receipt, context) is not None


def test_rehashed_product_cannot_claim_agent_or_scientific_success(contract):
    store, receipt, context, artifact, receipt_path, products = contract
    _json(
        products / "receipt.json",
        {
            "status": "scientifically_validated",
            "scientific_review": "accepted",
            "agent_run": True,
        },
    )
    receipt["outputs"] = [
        _record(store.workspace_root, store.workspace_root / row["path"])
        for row in receipt["outputs"]
    ]
    _json(receipt_path, receipt)
    assert store._b1_flare_receipt_defect(receipt, context) is not None
    assert store._data_input_boundary_issues([artifact()])


def test_stale_receipt_cannot_complete_data_product(contract):
    store, _receipt, _context, artifact, receipt_path, _products = contract
    candidate = artifact()
    receipt_path.write_text(
        receipt_path.read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )
    assert store._data_input_boundary_issues([candidate])
