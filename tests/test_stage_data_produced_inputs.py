#!/usr/bin/env python3
"""Regression test: _stage_data_produced_inputs copies the accepted data artifact's
produced files into the run workspace inputs/ directory, idempotently.

Bug: the experiment_design producer kept binding input_refs to paths that were never
readable in the run workspace (planning-declared provenance paths, empty inputs/),
so design.json was never persisted and experiment_design failed twice.
"""

import json
from pathlib import Path

from jw.middleware.research_review_orchestration import _stage_data_produced_inputs
from jw.research_review import ResearchReviewStore


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

    assert "inputs/solar_precursor_cycle_features.csv" in staged
    assert "inputs/solar_precursor_cycle_table.json" in staged
    assert (
        tmp_path / "inputs" / "solar_precursor_cycle_features.csv"
    ).read_bytes() == csv_path.read_bytes()


def test_staging_is_idempotent(tmp_path: Path) -> None:
    csv_path = tmp_path / "work" / "solar_data" / "features.csv"
    csv_path.parent.mkdir(parents=True)
    csv_path.write_text("a,b\n", encoding="utf-8")
    store = _accepted_data_store(
        tmp_path, data_content="data v1; wrote work/solar_data/features.csv"
    )

    first = _stage_data_produced_inputs(store)
    target = tmp_path / "inputs" / "features.csv"
    mtime = target.stat().st_mtime_ns
    second = _stage_data_produced_inputs(store)

    assert first == second == ["inputs/features.csv", "inputs/_staged.json"]
    assert target.stat().st_mtime_ns == mtime


def test_no_data_artifact_stages_nothing(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "task-1")
    assert _stage_data_produced_inputs(store) == []
