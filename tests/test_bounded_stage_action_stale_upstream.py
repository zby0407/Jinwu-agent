#!/usr/bin/env python3
"""Regression test: bounded_stage_action must detect stale upstream after invalidation.

Bug: bounded_stage_action returned released for an accepted hypothesis artifact even
after data (its upstream dependency) was re-produced and invalidated hypothesis to
pending. The router then refused re-delegation because stage_status was pending while
the bounded action claimed released.
"""
from pathlib import Path

from jw.research_review import ResearchReviewStore


def _issue() -> dict:
    return {
        "rule_id": "X",
        "severity": "minor",
        "owner": "solar-data",
        "statement": "s",
        "evidence": [],
        "required_fix": "f",
        "acceptance_test": "t",
    }


def test_bounded_stage_action_returns_dependency_refresh_when_upstream_stale(tmp_path: Path) -> None:
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
        stage="data", producer="solar-data", content="data v1"
    )
    data_v1 = store.latest_artifact("data")
    store.submit_verdict(
        mode="data",
        decision="accept",
        issues=[],
        accepted_claims=[data_v1["claims"][0]["claim_id"]],
    )
    # Hypothesis produced BEFORE data existed: upstream_refs cannot contain data.
    store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="hypothesis v1 without data",
        phase="bounded_hypothesis",
    )
    hyp_v1 = store.latest_artifact("hypothesis")
    store.submit_verdict(
        mode="hypothesis",
        decision="accept_with_limits",
        issues=[],
        accepted_claims=[hyp_v1["claims"][0]["claim_id"]],
        carry_forward_limits=["data pending at production time"],
    )
    # Data re-produced (new hash) -> invalidates hypothesis to pending.
    store.checkpoint_producer_result(
        stage="data", producer="solar-data", content="data v2 with real dataset"
    )
    data_v2 = store.latest_artifact("data")
    store.submit_verdict(
        mode="data",
        decision="accept_with_limits",
        issues=[],
        accepted_claims=[data_v2["claims"][0]["claim_id"]],
        carry_forward_limits=["limited cycle coverage"],
    )

    state = store.load_state()
    assert state["stage_status"]["hypothesis"] == "pending"

    action = store.bounded_stage_action("hypothesis")

    assert action["kind"] == "producer"
    assert action["stage"] == "hypothesis"
    assert action["phase"] == "bounded_hypothesis_dependency_refresh"
    assert action.get("reason") == "accepted upstream artifact hash changed"
