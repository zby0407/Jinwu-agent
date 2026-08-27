#!/usr/bin/env python3
"""Regression test: _stage_data_produced_inputs copies the accepted data artifact's
produced files into the run workspace inputs/ directory, idempotently.

Bug: the experiment_design producer kept binding input_refs to paths that were never
readable in the run workspace (planning-declared provenance paths, empty inputs/),
so design.json was never persisted and experiment_design failed twice.
"""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jw.middleware.research_review_orchestration import _stage_data_produced_inputs
from jw.research_review import ResearchReviewStore


def test_stages_hash_bound_outputs_from_data_source_manifest(tmp_path: Path) -> None:
    output = tmp_path / "outputs" / "cycle_morphology_table.csv"
    output.parent.mkdir(parents=True)
    output.write_text("cycle_number,value\n1,144.1\n", encoding="utf-8")
    raw = output.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    store = SimpleNamespace(
        workspace_root=tmp_path,
        accepted_artifacts=lambda: [
            {
                "stage": "data",
                "payload": {
                    "canonical_source_refs": [
                        "receipts/datasets/silso_cycle_morphology.json"
                    ],
                    "source_manifest": [
                        {
                            "source_ref": "outputs/cycle_morphology_table.csv",
                            "bytes": len(raw),
                            "sha256": digest,
                        }
                    ],
                },
            }
        ],
        _current_manifest_input_records=lambda: [],
    )

    staged = _stage_data_produced_inputs(store)

    staged_ref = f"inputs/data_artifacts/{digest[:16]}-cycle_morphology_table.csv"
    assert staged == [staged_ref, "inputs/_staged.json"]
    assert (tmp_path / staged_ref).read_bytes() == raw


def _accepted_data_store(
    tmp_path: Path,
    *,
    data_content: str = "data v1",
) -> ResearchReviewStore:
    store = ResearchReviewStore(tmp_path, "task-1")
    store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="plan v1"
    )
    plan_v1 = store.latest_artifact("planning")
    store.submit_verdict(
        mode="planning",
        decision="accept",
        issues=[],
        accepted_claims=[plan_v1["claims"][0]["claim_id"]],
    )
    store.checkpoint_producer_result(
        stage="data", producer="solar-data", content=data_content
    )
    data_v1 = store.latest_artifact("data")
    store.submit_verdict(
        mode="data",
        decision="accept_with_limits",
        issues=[],
        accepted_claims=[data_v1["claims"][0]["claim_id"]],
        carry_forward_limits=["limited coverage"],
    )
    return store


def test_stages_produced_csv_and_canonical_refs(tmp_path: Path) -> None:
    # Producer's real output file under work/solar_data/, mentioned in producer_result.
    csv_path = tmp_path / "work" / "solar_data" / "solar_precursor_cycle_features.csv"
    csv_path.parent.mkdir(parents=True)
    csv_path.write_text("cycle,value\n15,1.0\n16,2.0\n", encoding="utf-8")
    receipt = tmp_path / "receipts" / "datasets" / "solar_precursor_cycle_table.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({"rows": 10}), encoding="utf-8")

    store = _accepted_data_store(
        tmp_path,
        data_content=(
            "data v1; canonical source receipts/datasets/solar_precursor_cycle_table.json; "
            "produced work/solar_data/solar_precursor_cycle_features.csv (10 cycles)"
        ),
    )

    staged = _stage_data_produced_inputs(store)

    csv_ref = (
        "inputs/data_artifacts/"
        f"{hashlib.sha256(csv_path.read_bytes()).hexdigest()[:16]}-"
        "solar_precursor_cycle_features.csv"
    )
    receipt_ref = (
        "inputs/data_artifacts/"
        f"{hashlib.sha256(receipt.read_bytes()).hexdigest()[:16]}-"
        "solar_precursor_cycle_table.json"
    )
    assert csv_ref in staged
    assert receipt_ref in staged
    assert (tmp_path / csv_ref).read_bytes() == csv_path.read_bytes()


def test_staging_is_idempotent(tmp_path: Path) -> None:
    csv_path = tmp_path / "work" / "solar_data" / "features.csv"
    csv_path.parent.mkdir(parents=True)
    csv_path.write_text("a,b\n", encoding="utf-8")
    store = _accepted_data_store(
        tmp_path, data_content="data v1; wrote work/solar_data/features.csv"
    )

    first = _stage_data_produced_inputs(store)
    target = (
        tmp_path
        / "inputs"
        / "data_artifacts"
        / f"{hashlib.sha256(csv_path.read_bytes()).hexdigest()[:16]}-features.csv"
    )
    mtime = target.stat().st_mtime_ns
    second = _stage_data_produced_inputs(store)

    assert (
        first
        == second
        == [
            target.relative_to(tmp_path).as_posix(),
            "inputs/_staged.json",
        ]
    )
    assert target.stat().st_mtime_ns == mtime


def test_data_staging_preserves_same_named_manifest_user_input(tmp_path: Path) -> None:
    user_input = tmp_path / "inputs" / "features.csv"
    user_input.parent.mkdir(parents=True)
    user_input.write_text("source,user\n", encoding="utf-8")
    user_bytes = user_input.read_bytes()
    derived = tmp_path / "work" / "solar_data" / "features.csv"
    derived.parent.mkdir(parents=True)
    derived.write_text("source,derived\n", encoding="utf-8")
    derived_bytes = derived.read_bytes()
    (tmp_path / "task.json").write_text(
        json.dumps({"thread_id": "task-1", "research_question": "question"}),
        encoding="utf-8",
    )
    (tmp_path / "input_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "task-input-manifest-v1",
                "thread_id": "task-1",
                "inputs": [
                    {
                        "path": "inputs/features.csv",
                        "sha256": hashlib.sha256(user_bytes).hexdigest(),
                        "bytes": len(user_bytes),
                        "role": "user_input",
                    }
                ],
                "project_inputs": [],
            }
        ),
        encoding="utf-8",
    )
    store = _accepted_data_store(
        tmp_path, data_content="data v1; wrote work/solar_data/features.csv"
    )

    staged = _stage_data_produced_inputs(store)

    data_ref = (
        "inputs/data_artifacts/"
        f"{hashlib.sha256(derived_bytes).hexdigest()[:16]}-features.csv"
    )
    assert user_input.read_bytes() == user_bytes
    assert (tmp_path / data_ref).read_bytes() == derived_bytes
    assert staged == ["inputs/features.csv", data_ref, "inputs/_staged.json"]


def test_data_staging_rejects_a_conflicting_content_addressed_target(
    tmp_path: Path,
) -> None:
    derived = tmp_path / "work" / "solar_data" / "features.csv"
    derived.parent.mkdir(parents=True)
    derived.write_text("source,derived\n", encoding="utf-8")
    store = _accepted_data_store(
        tmp_path, data_content="data v1; wrote work/solar_data/features.csv"
    )
    target = (
        tmp_path
        / "inputs"
        / "data_artifacts"
        / f"{hashlib.sha256(derived.read_bytes()).hexdigest()[:16]}-features.csv"
    )
    target.parent.mkdir(parents=True)
    target.write_text("tampered\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="content-addressed staging conflict"):
        _stage_data_produced_inputs(store)


def test_data_staging_ignores_producer_result_traversal_outside_workspace(
    tmp_path: Path,
) -> None:
    traversal_ref = "work/solar_data/../../../outside.csv"
    outside = (tmp_path / traversal_ref).resolve()
    assert not outside.is_relative_to(tmp_path.resolve())
    try:
        (tmp_path / "work" / "solar_data").mkdir(parents=True)
        store = _accepted_data_store(
            tmp_path,
            data_content=f"data v1; wrote {traversal_ref}",
        )
        outside.write_text("source,outside\n", encoding="utf-8")

        assert _stage_data_produced_inputs(store) == []
        assert not (tmp_path / "inputs" / "data_artifacts").exists()
    finally:
        outside.unlink(missing_ok=True)


def test_data_staging_ignores_producer_result_symlink_outside_workspace(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.csv"
    outside.write_text("source,outside\n", encoding="utf-8")
    linked_output = tmp_path / "work" / "solar_data" / "linked.csv"
    linked_output.parent.mkdir(parents=True)
    linked_output.symlink_to(outside)
    try:
        store = _accepted_data_store(
            tmp_path, data_content="data v1; wrote work/solar_data/linked.csv"
        )

        assert _stage_data_produced_inputs(store) == []
        assert not (tmp_path / "inputs" / "data_artifacts").exists()
    finally:
        outside.unlink(missing_ok=True)


def test_stages_verified_project_manifest_inputs_for_experiment(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "default"
    workspace = project / "runs" / "run_task"
    source = project / "shared" / "data" / "solar" / "source.csv"
    source.parent.mkdir(parents=True)
    source.write_text("month,value\n2026-06,94.4\n", encoding="utf-8")
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    catalog = project / "shared" / "project_data_catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "files": [
                    {
                        "virtual_path": "/project/data/solar/source.csv",
                        "path": "solar/source.csv",
                        "sha256": digest,
                        "bytes": len(raw),
                        "role": "primary_data",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    workspace.mkdir(parents=True)
    (workspace / "task.json").write_text(
        json.dumps({"thread_id": "task-1", "research_question": "question"}),
        encoding="utf-8",
    )
    (workspace / "input_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "task-input-manifest-v1",
                "thread_id": "task-1",
                "inputs": [],
                "project_inputs": [
                    {
                        "path": "/project/data/solar/source.csv",
                        "sha256": digest,
                        "bytes": len(raw),
                        "role": "primary_data",
                        "dataset_id": "solar-source",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    store = _accepted_data_store(workspace)

    staged = _stage_data_produced_inputs(store)

    staged_ref = f"inputs/project/{digest[:16]}-source.csv"
    assert staged_ref in staged
    assert (workspace / staged_ref).read_bytes() == raw
    sidecar = json.loads((workspace / "inputs" / "_staged.json").read_text())
    assert staged_ref in [row["path"] for row in sidecar["input_refs"]]


def test_no_data_artifact_stages_nothing(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "task-1")
    assert _stage_data_produced_inputs(store) == []
