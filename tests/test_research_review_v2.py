from __future__ import annotations

import hashlib
import json
import multiprocessing
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from jw import paths
from jw.middleware.research_review_orchestration import (
    ResearchReviewOrchestrationMiddleware,
)
from jw.research_review import ResearchReviewStore
from jw.tools import research_planner as planner_tools
from jw.tools.research_review import _normalize_issues, research_independent_review
from jw.tools.solar_feature import _task_chat_session
from jw.workspaces import ensure_thread_workspace
from research_review.adapters import adapt_v1_producer_output
from research_review.contracts import (
    POLICY_VERSION,
    ContractError,
    build_research_artifact,
    canonical_json_sha256,
    issue_fingerprint,
    validate_research_artifact,
    validate_revision_capsule,
)
from research_review.policies import policy_registry


def _checkpoint_from_process(
    workspace: str,
    start: object,
    ready: object,
    output: object,
    content: str,
) -> None:
    ready.put(True)
    start.wait(10)
    store = ResearchReviewStore(Path(workspace), "concurrent-task")
    artifact = store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content=content,
    )
    output.put(artifact["version"])


def _issue(owner: str = "solar-planner") -> dict[str, object]:
    rule_id = "UNSUPPORTED_CLAIM"
    claim_ref = "planning-output-v1"
    return {
        "issue_id": "issue-001",
        "rule_id": rule_id,
        "severity": "major",
        "claim_ref": claim_ref,
        "evidence_refs": [],
        "owner": owner,
        "message": "The claim has no inspected evidence.",
        "required_action": "Bind an inspected source or narrow the claim.",
        "acceptance_test": "The revised claim points to a verified source excerpt.",
        "fingerprint": issue_fingerprint(rule_id, claim_ref, owner),
    }


def _accept(
    store: ResearchReviewStore, mode: str, *, independently: bool = False
) -> None:
    target = store.latest_artifact(mode)
    assert target is not None
    independent_review = None
    if independently:
        refs = [store.artifact_ref(target)]
        _write_independent_receipt(store, mode, refs)
        independent_review = {"status": "heterogeneous_pass"}
    store.submit_verdict(
        mode=mode,
        decision="accept",
        issues=[],
        accepted_claims=[target["claims"][0]["claim_id"]],
        independent_review=independent_review,
    )


def _write_independent_receipt(
    store: ResearchReviewStore,
    mode: str,
    artifact_refs: list[dict[str, object]],
) -> None:
    receipt = {
        "schema_version": "independent-review-receipt-v1",
        "task_id": store.task_id,
        "review_mode": mode,
        "artifact_refs": artifact_refs,
        "reviewer_kind": "heterogeneous_model",
        "reviewer_id": "review-model-b",
        "decision": "pass",
        "notes": "Independent hash-bound pass.",
        "created_at": "2026-08-01T00:00:00+00:00",
    }
    receipt["receipt_sha256"] = canonical_json_sha256(receipt)
    receipt_dir = store.root / "independent_reviews" / mode
    receipt_dir.mkdir(parents=True, exist_ok=True)
    (receipt_dir / "review-model-b.json").write_text(
        json.dumps(receipt), encoding="utf-8"
    )


def test_artifact_hash_detects_tampering() -> None:
    artifact = build_research_artifact(
        artifact_id="planning-artifact",
        task_id="task-1",
        stage="planning",
        version=1,
        producer="solar-planner",
        payload={"text": "bounded plan"},
    )
    artifact["payload"]["text"] = "silently changed"

    with pytest.raises(ContractError, match="artifact_sha256"):
        validate_research_artifact(artifact)


def test_review_context_omits_long_producer_report_but_keeps_hash_and_sources(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "work" / "scientific_hypothesis_state.json"
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "latest_draft": {
                    "response_kind": "hypotheses_ready",
                    "candidates": [
                        {
                            "id": "cand1",
                            "statement": "A bounded mechanism claim.",
                            "applicability": "Only under the stated regime.",
                            "supporting_evidence": [{"evidence_id": "E1"}],
                            "opposing_evidence": [],
                            "confidence": {"level": "low"},
                            "evidence_gaps": ["Independent replication."],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    store = ResearchReviewStore(workspace, "compact-review-context-task")
    long_report = "REDUNDANT-PRODUCER-REPORT " * 4_000
    artifact = store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content=long_report,
        phase="bounded_hypothesis",
        require_canonical_source=True,
    )

    context = store.review_context("hypothesis")
    projection = context["artifacts"][0]
    encoded = json.dumps(context, ensure_ascii=False)

    assert projection["schema_version"] == "research-artifact-review-projection-v1"
    assert projection["artifact_sha256"] == artifact["artifact_sha256"]
    assert projection["evidence_refs"] == ["work/scientific_hypothesis_state.json"]
    assert projection["producer_result_omitted_chars"] == len(long_report.strip())
    assert "producer_result" not in projection
    assert "REDUNDANT-PRODUCER-REPORT" not in encoded
    assert len(encoded) < 20_000


def test_policy_registry_severity_is_a_deterministic_floor(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "severity-floor-task")
    artifact = store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="A bounded precursor hypothesis requiring causal-scope revision.",
        phase="bounded_hypothesis",
    )
    claim_id = artifact["claims"][0]["claim_id"]
    issue = {
        "issue_id": "causal-scope-001",
        "rule_id": "CAUSAL_SCOPE_BOUNDED",
        "severity": "minor",
        "claim_ref": claim_id,
        "evidence_refs": [],
        "owner": "solar-hypothesis",
        "message": "Predictive association is written as causal dominance.",
        "required_action": "Narrow the wording to the supported association.",
        "acceptance_test": "The claim no longer asserts causal dominance.",
        "fingerprint": issue_fingerprint(
            "CAUSAL_SCOPE_BOUNDED", claim_id, "solar-hypothesis"
        ),
    }

    verdict = store.submit_verdict(
        mode="hypothesis",
        decision="revise",
        issues=[issue],
        next_owner="solar-hypothesis",
    )

    assert verdict["issues"][0]["severity"] == "major"
    capsule = store.revision_capsule(verdict["review_id"], "solar-hypothesis")
    assert validate_revision_capsule(capsule) == capsule
    assert capsule["verdict_sha256"] == verdict["verdict_sha256"]
    assert capsule["unresolved_issues"][0]["fingerprint"] == issue["fingerprint"]
    assert "reviewer_context" not in capsule


def test_policy_version_has_one_canonical_source() -> None:
    assert POLICY_VERSION == policy_registry()["policy_version"]


def test_same_cycle_bmr_causality_cannot_be_model_accepted(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "temporal-hard-gate-task")
    artifact = store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content=("下一太阳活动周期振幅受该周期自身BMR倾斜角随机涨落影响。"),
        phase="bounded_hypothesis",
    )

    verdict = store.submit_verdict(
        mode="hypothesis",
        decision="accept",
        issues=[],
        accepted_claims=[claim["claim_id"] for claim in artifact["claims"]],
    )

    assert verdict["decision"] == "revise"
    assert verdict["next_owner"] == "solar-hypothesis"
    issue = next(
        row for row in verdict["issues"] if row["rule_id"] == "TEMPORAL_CAUSAL_ORDER"
    )
    assert issue["severity"] == "major"


def test_deterministic_preflight_persists_revise_without_model_vote(
    tmp_path: Path,
) -> None:
    store = ResearchReviewStore(tmp_path, "temporal-preflight-task")
    artifact = store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="下一太阳活动周期振幅受该周期自身BMR倾斜角随机涨落影响。",
        phase="bounded_hypothesis",
    )

    verdict = store.persist_deterministic_preflight_verdict("hypothesis")

    assert verdict is not None
    assert verdict["decision"] == "revise"
    assert verdict["next_owner"] == "solar-hypothesis"
    assert verdict["blocked_claims"] == [artifact["claims"][0]["claim_id"]]
    assert {issue["rule_id"] for issue in verdict["issues"]} == {
        "TEMPORAL_CAUSAL_ORDER"
    }
    assert store.persist_deterministic_preflight_verdict("hypothesis") is None


def test_data_input_missing_receipt_is_deterministically_blocked(
    tmp_path: Path,
) -> None:
    receipt = tmp_path / "receipts" / "datasets" / "data-context-deadbeef.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "solar-data-context-v1",
                "task_id": "data-input-missing-task",
                "status": "input_missing",
                "eligible_inputs": [],
            }
        ),
        encoding="utf-8",
    )
    store = ResearchReviewStore(tmp_path, "data-input-missing-task")
    artifact = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content=(
            "No eligible immutable input is bound; see "
            "receipts/datasets/data-context-deadbeef.json"
        ),
        phase="bounded_data",
    )

    verdict = store.persist_deterministic_preflight_verdict("data")

    assert verdict is not None
    assert verdict["decision"] == "block"
    assert verdict["next_owner"] is None
    assert verdict["blocked_claims"] == [artifact["claims"][0]["claim_id"]]
    assert verdict["issues"][0]["rule_id"] == "REQUIRED_DATA_INPUT_UNAVAILABLE"
    state = store.load_state()
    assert state["status"] == "blocked"
    assert state["stage_status"]["data"] == "blocked"


def test_data_input_missing_cannot_be_model_accepted(tmp_path: Path) -> None:
    receipt = tmp_path / "receipts" / "datasets" / "data-context-deadbeef.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "solar-data-context-v1",
                "task_id": "data-model-vote-task",
                "status": "input_missing",
                "eligible_inputs": [],
            }
        ),
        encoding="utf-8",
    )
    store = ResearchReviewStore(tmp_path, "data-model-vote-task")
    artifact = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content="receipts/datasets/data-context-deadbeef.json",
        phase="bounded_data",
    )

    verdict = store.submit_verdict(
        mode="data",
        decision="accept",
        issues=[],
        accepted_claims=[artifact["claims"][0]["claim_id"]],
    )

    assert verdict["decision"] == "block"
    assert any(
        issue["rule_id"] == "REQUIRED_DATA_INPUT_UNAVAILABLE"
        for issue in verdict["issues"]
    )


def _write_curated_data_context(tmp_path: Path, task_id: str) -> Path:
    receipt = tmp_path / "receipts" / "datasets" / "data-context-curated.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "solar-data-context-v1",
                "task_id": task_id,
                "status": "inputs_available",
                "eligible_inputs": [
                    {
                        "dataset_id": "silso-monthly-total-v2",
                        "path": "/project/silso.txt",
                        "sha256": "a" * 64,
                    },
                    {
                        "dataset_id": "mwo-wso-polar-field-v2",
                        "path": "/project/polar.csv",
                        "sha256": "b" * 64,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return receipt


def test_curated_data_context_without_cycle_table_requires_revision(
    tmp_path: Path,
) -> None:
    task_id = "curated-data-incomplete"
    context = _write_curated_data_context(tmp_path, task_id)
    store = ResearchReviewStore(tmp_path, task_id)
    store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content=context.relative_to(tmp_path).as_posix(),
        phase="bounded_data",
    )

    verdict = store.persist_deterministic_preflight_verdict("data")

    assert verdict is not None
    assert verdict["decision"] == "revise"
    assert verdict["next_owner"] == "solar-data"
    assert verdict["issues"][0]["rule_id"] == "DATA_SEMANTICS_BOUND"


def test_curated_data_cycle_table_passes_deterministic_boundary(tmp_path: Path) -> None:
    task_id = "curated-data-complete"
    context = _write_curated_data_context(tmp_path, task_id)
    table_ref = "work/solar_data/solar_precursor_cycle_features.csv"
    table = tmp_path / table_ref
    table.parent.mkdir(parents=True)
    lines = [
        "cycle_number,north_measurement_date,south_measurement_date,predictor_cutoff_decimal_year"
    ]
    for cycle in range(15, 25):
        cutoff = 1900.0 + cycle
        lines.append(f"{cycle},{cutoff - 0.2},{cutoff - 0.1},{cutoff}")
    table.write_text("\n".join(lines) + "\n", encoding="utf-8")
    table_sha = hashlib.sha256(table.read_bytes()).hexdigest()
    receipt = tmp_path / "receipts" / "datasets" / "solar_precursor_cycle_table.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "solar-precursor-cycle-table-v1",
                "status": "verified",
                "input_refs": [
                    {
                        "dataset_id": "silso-monthly-total-v2",
                        "sha256": "a" * 64,
                    },
                    {
                        "dataset_id": "mwo-wso-polar-field-v2",
                        "sha256": "b" * 64,
                    },
                ],
                "row_count": 10,
                "cycle_numbers": list(range(15, 25)),
                "outputs": [{"path": table_ref, "sha256": table_sha}],
            }
        ),
        encoding="utf-8",
    )
    store = ResearchReviewStore(tmp_path, task_id)
    store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content=(
            f"{context.relative_to(tmp_path).as_posix()} "
            f"{receipt.relative_to(tmp_path).as_posix()} {table_ref}"
        ),
        phase="bounded_data",
    )

    assert store.persist_deterministic_preflight_verdict("data") is None


def test_evidence_issue_identities_must_be_field_level_unique() -> None:
    issue = {
        "rule_id": "PREDICTION_VALIDATION_AND_CALIBRATION",
        "severity": "major",
        "claim_ref": "planning-plan-v1",
        "owner": "solar-planner",
        "message": "A distinct validation defect.",
        "required_action": "Repair the exact field.",
        "acceptance_test": "The exact field passes.",
        "evidence_refs": [],
    }

    with pytest.raises(ValueError, match="stable field-level claim_ref"):
        _normalize_issues([issue, {**issue, "message": "A second defect."}])


def test_planning_preflight_requires_full_global_stage_closure(tmp_path: Path) -> None:
    planner_run = tmp_path / "planner" / "runs" / "missing-design"
    planner_run.mkdir(parents=True)
    (planner_run / "research_plan.json").write_text(
        json.dumps(
            {
                "schema_version": "research-plan-v1",
                "research_question": "Test an empirical precursor claim.",
                "research_route": [
                    {"id": "rs1", "stage": "data_preparation"},
                    {"id": "rs2", "stage": "experiment_result"},
                    {"id": "rs3", "stage": "hypothesis"},
                ],
            }
        ),
        encoding="utf-8",
    )
    store = ResearchReviewStore(tmp_path, "planning-stage-closure")
    store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content="planner/runs/missing-design/research_plan.json",
        require_canonical_source=True,
    )

    verdict = store.persist_deterministic_preflight_verdict("planning")

    assert verdict is not None
    assert verdict["decision"] == "revise"
    assert verdict["next_owner"] == "solar-planner"
    issue = verdict["issues"][0]
    assert issue["rule_id"] == "CROSS_STAGE_CLOSURE"
    assert issue["claim_ref"] == "planning-plan-v1#research_route.stage_sequence"


def test_run_state_persists_planner_dag_and_task_action_budget(tmp_path: Path) -> None:
    planner_run = tmp_path / "planner" / "runs" / "plan-1"
    planner_run.mkdir(parents=True)
    (planner_run / "research_plan.json").write_text(
        json.dumps(
            {
                "schema_version": "research-plan-v1",
                "research_question": "极区场前兆能否稳定外推下一活动周振幅?",
                "scope": "历史活动周时间外推验证",
                "research_route": [
                    {
                        "id": "prepare-data",
                        "stage": "data_preparation",
                        "prerequisite_step_ids": [],
                    },
                    {
                        "id": "test-hypothesis",
                        "stage": "hypothesis_test",
                        "prerequisite_step_ids": ["prepare-data"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = ResearchReviewStore(tmp_path, "task-1", budget_multiplier=1)
    artifact = store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content="planner/runs/plan-1/research_plan.json",
        require_canonical_source=True,
    )

    state = store.load_state()
    assert state["dependency_graph"]["source_ref"].startswith("planning-artifact@v1:")
    assert state["dependency_graph"]["planner_steps"][1] == {
        "step_id": "test-hypothesis",
        "stage": "hypothesis_test",
        "prerequisite_step_ids": ["prepare-data"],
    }
    assert artifact["artifact_sha256"] in state["dependency_graph"]["source_ref"]

    store.reserve_action(store.next_action())
    reopened = ResearchReviewStore(tmp_path, "task-1", budget_multiplier=1)
    assert reopened.load_state()["action_invocations"] == 1
    exhausted = reopened.load_state()
    exhausted["action_invocations"] = exhausted["max_action_invocations"]
    reopened._save_state(exhausted)
    assert reopened.next_action()["reason"] == "RESEARCH_ACTION_BUDGET_EXHAUSTED"


def test_blocked_stage_recovers_from_later_canonical_source_without_budget_reset(
    tmp_path: Path,
) -> None:
    store = ResearchReviewStore(tmp_path, "canonical-recovery-task")
    state = store.load_state()
    state["action_invocations"] = 7
    store._save_state(state)
    failure = store.block_for_tool_failures(
        stage="planning",
        producer="solar-planner",
        fingerprints=["a" * 64, "b" * 64],
    )
    planner_run = tmp_path / "planner" / "runs" / "plan-recovered"
    planner_run.mkdir(parents=True)
    (planner_run / "research_plan.json").write_text(
        json.dumps(
            {
                "schema_version": "research-plan-v1",
                "research_question": "Recovered validated plan",
                "research_route": [],
            }
        ),
        encoding="utf-8",
    )

    recovery = store.recover_canonical_producer_after_tool_failure()

    assert recovery is not None
    assert recovery["failure_receipt_sha256"] == failure["receipt_sha256"]
    assert recovery["preserved_action_invocations"] == 7
    recovered_state = store.load_state()
    assert recovered_state["status"] == "active"
    assert recovered_state["stage_status"]["planning"] == "produced"
    assert recovered_state["action_invocations"] == 7
    assert store.latest_artifact("planning") is not None


def test_blocked_stage_reopens_after_versioned_harness_change_without_budget_reset(
    tmp_path: Path,
) -> None:
    store = ResearchReviewStore(tmp_path, "harness-reopen-task")
    store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="initial plan"
    )
    issue = _issue(owner="solar-planner")
    issue["claim_ref"] = "planning.scope.objective"
    issue["fingerprint"] = issue_fingerprint(
        str(issue["rule_id"]), str(issue["claim_ref"]), "solar-planner"
    )
    store.submit_verdict(
        mode="planning",
        decision="revise",
        issues=[issue],
        next_owner="solar-planner",
    )
    state = store.load_state()
    state["action_invocations"] = 9
    store._save_state(state)
    failure = store.block_for_tool_failures(
        stage="planning",
        producer="solar-planner",
        fingerprints=["a" * 64, "b" * 64],
    )

    with pytest.raises(RuntimeError, match="does not match latest"):
        store.reopen_tool_failure_after_harness_change(
            failure_receipt_sha256="f" * 64,
            change_id="planner-shadow-revision-v1",
        )

    recovery = store.reopen_tool_failure_after_harness_change(
        failure_receipt_sha256=failure["receipt_sha256"],
        change_id="planner-shadow-revision-v1",
    )

    assert recovery["restored_stage_status"] == "revise"
    assert recovery["preserved_action_invocations"] == 9
    reopened_state = store.load_state()
    assert reopened_state["status"] == "active"
    assert reopened_state["stage_status"]["planning"] == "revise"
    assert reopened_state["action_invocations"] == 9
    assert (
        store.reopen_tool_failure_after_harness_change(
            failure_receipt_sha256=failure["receipt_sha256"],
            change_id="planner-shadow-revision-v1",
        )
        == recovery
    )


def test_latest_tool_failure_is_chronological_across_stage_directories(
    tmp_path: Path,
) -> None:
    store = ResearchReviewStore(tmp_path, "cross-stage-failure-order")
    planning_failure = store.block_for_tool_failures(
        stage="planning",
        producer="solar-planner",
        fingerprints=["a" * 64],
    )
    data_failure = store.block_for_tool_failures(
        stage="data",
        producer="solar-data",
        fingerprints=["b" * 64],
    )
    assert planning_failure["created_at"] <= data_failure["created_at"]
    assert store.latest_tool_failure_receipt() == data_failure

    # A prior buggy recovery could leave global status active while the latest
    # failed stage stayed blocked. The hash-bound reopen repairs that one-way
    # inconsistency without resetting the action budget.
    state = store.load_state()
    state["status"] = "active"
    state["current_stage"] = "planning"
    state["action_invocations"] = 11
    store._save_state(state)
    recovery = store.reopen_tool_failure_after_harness_change(
        failure_receipt_sha256=data_failure["receipt_sha256"],
        change_id="data-context-v1",
    )
    repaired = store.load_state()
    assert recovery["stage"] == "data"
    assert recovery["preserved_action_invocations"] == 11
    assert repaired["status"] == "active"
    assert repaired["current_stage"] == "data"
    assert repaired["stage_status"]["data"] == "pending"


def test_cross_stage_revision_response_binds_prior_verdict_hash(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "task-1")
    store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="initial plan"
    )
    _accept(store, "planning")
    store.checkpoint_producer_result(
        stage="data", producer="solar-data", content="data receipt"
    )
    issue = _issue(owner="solar-planner")
    issue["claim_ref"] = "data-output-v1"
    issue["fingerprint"] = issue_fingerprint(
        str(issue["rule_id"]), str(issue["claim_ref"]), "solar-planner"
    )
    verdict = store.submit_verdict(
        mode="data",
        decision="revise",
        issues=[issue],
        next_owner="solar-planner",
    )

    action = store.next_action()
    assert action["revision_review_id"] == verdict["review_id"]
    revision = store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content="revised plan for data semantic issue",
        phase=action["phase"],
        revision_review_id=action["revision_review_id"],
    )

    response = revision["payload"]["revision_response"]
    assert response["prior_review_ref"] == {
        "review_id": verdict["review_id"],
        "verdict_sha256": verdict["verdict_sha256"],
    }
    assert response["issue_responses"] == [
        {
            "issue_id": issue["issue_id"],
            "fingerprint": issue["fingerprint"],
            "status": "resubmitted",
            "response": (
                "A new immutable producer artifact was submitted; the Evidence "
                "Reviewer must rerun the stated acceptance test before closure."
            ),
            "acceptance_evidence": [],
        }
    ]


@pytest.mark.parametrize(
    ("stage", "adapter_id"),
    [
        ("planning", "research-planner-v1-to-v2"),
        ("data", "solar-data-receipt-v1-to-v2"),
        ("hypothesis", "scientific-hypothesis-v1-to-v2"),
        ("experiment_design", "automatic-experiment-design-v1-to-v2"),
        ("experiment_result", "automatic-experiment-result-v1-to-v2"),
    ],
)
def test_each_producer_uses_an_explicit_conservative_v1_adapter(
    stage: str, adapter_id: str
) -> None:
    adapted = adapt_v1_producer_output(
        stage=stage,
        version=1,
        phase=stage,
        text='{"schema_version":"producer-v1","result":"bounded"}',
        evidence_refs=["receipts/source.json"],
    )

    assert adapted["payload"]["adapter_id"] == adapter_id
    assert adapted["payload"]["source_schema_version"] == "producer-v1"
    assert adapted["claims"][0]["confidence"] == "unknown"


def test_known_hypothesis_v1_state_adapts_candidates_as_separate_claims() -> None:
    source_ref = "work/scientific_hypothesis_state.json"
    adapted = adapt_v1_producer_output(
        stage="hypothesis",
        version=2,
        phase="hypothesis_update",
        text="rendered hypothesis result",
        evidence_refs=[source_ref],
        canonical_documents=[
            {
                "source_ref": source_ref,
                "payload": {
                    "schema_version": 1,
                    "latest_draft": {
                        "response_kind": "hypotheses_ready",
                        "candidates": [
                            {
                                "id": "H1",
                                "statement": "极向磁通输运调节下一极小附近极区场。",
                                "applicability": "仅限所声明观测窗口。",
                                "supporting_evidence": [
                                    {"evidence_id": "E1", "relation_note": "支持"}
                                ],
                                "opposing_evidence": [],
                                "evidence_gaps": ["独立活动周数量有限。"],
                                "confidence": {"level": "low", "basis": "样本有限"},
                            }
                        ],
                    },
                },
            }
        ],
    )

    assert [claim["claim_id"] for claim in adapted["claims"]] == ["hypothesis-H1"]
    assert adapted["claims"][0]["supporting_evidence"] == [source_ref]
    assert adapted["claims"][0]["confidence"] == "low"


@pytest.mark.parametrize(
    ("stage", "source_ref", "payload", "expected_claim_id"),
    [
        (
            "planning",
            "planner/runs/run-1/research_plan.json",
            {
                "schema_version": "research-plan-v1",
                "research_question": "检验极区场前兆关系",
                "scope": {"included": ["周期回报检验"]},
                "planning_readiness": "external_inputs_required",
            },
            "planning-plan-v1",
        ),
        (
            "data",
            "receipts/datasets/f107_semantics.json",
            {
                "status": "verified",
                "canonical_sha256": "a" * 64,
                "product_id": "f107_adjusted",
                "product_version": "Canadian adjusted flux",
                "canonical_artifact": "canonical_f107_monthly.csv",
                "coverage_start": "1947-01-01",
                "coverage_end": "2015-12-01",
            },
            "data-f107-v1",
        ),
        (
            "experiment_design",
            "experiment/runs/run-1/design.json",
            {
                "schema_version": "automatic-experiment-design-v1",
                "normalized_task": "滚动回报检验",
                "design_summary": "按预测发出时点冻结输入并滚动留出。",
                "research_frame": {
                    "primary_question": "能否时间外推",
                    "claim_scope": "仅限历史活动周",
                    "deferred_questions": ["新仪器外推"],
                    "threats_to_validity": ["独立周期较少"],
                },
            },
            "experiment-design-v1",
        ),
        (
            "experiment_result",
            "experiment/runs/run-1/record.json",
            {
                "schema_version": "automatic-experiment-record-v1",
                "outcome": "scientific_null",
                "outcome_reason": "滚动留出未优于历史平均基线。",
                "task": "比较极区场前兆与历史平均基线",
            },
            "experiment-result-v1",
        ),
    ],
)
def test_known_v1_documents_use_stage_specific_adapters(
    stage: str,
    source_ref: str,
    payload: dict[str, object],
    expected_claim_id: str,
) -> None:
    adapted = adapt_v1_producer_output(
        stage=stage,
        version=1,
        phase=stage,
        text="rendered producer output",
        evidence_refs=[source_ref],
        canonical_documents=[{"source_ref": source_ref, "payload": payload}],
    )

    assert adapted["claims"][0]["claim_id"] == expected_claim_id
    assert adapted["claims"][0]["supporting_evidence"] == [source_ref]


def test_solar_data_chat_session_is_task_scoped(tmp_path: Path, monkeypatch) -> None:
    created: list[Path] = []

    class FakeChatSession:
        def __init__(self, path: Path) -> None:
            created.append(path)

    monkeypatch.setitem(
        sys.modules,
        "chat_session",
        SimpleNamespace(ChatSession=FakeChatSession),
    )
    roots = iter((tmp_path / "task-a", tmp_path / "task-b"))
    monkeypatch.setattr(
        "jw.tools.solar_feature.workspace_root_from_config",
        lambda _config: next(roots),
    )

    _task_chat_session({"configurable": {"thread_id": "a"}})
    _task_chat_session({"configurable": {"thread_id": "b"}})

    assert created == [
        tmp_path / "task-a" / "work" / "solar_data" / "chat_session.json",
        tmp_path / "task-b" / "work" / "solar_data" / "chat_session.json",
    ]


def test_artifact_versions_are_serialized_across_processes(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    ready = context.Queue()
    output = context.Queue()
    processes = [
        context.Process(
            target=_checkpoint_from_process,
            args=(str(tmp_path), start, ready, output, f"plan {index}"),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    assert [ready.get(timeout=20) for _ in processes] == [True, True]
    start.set()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    assert {output.get(timeout=5) for _ in processes} == {1, 2}
    store = ResearchReviewStore(tmp_path, "concurrent-task")
    assert [artifact["version"] for artifact in store.artifacts(stage="planning")] == [
        1,
        2,
    ]


def test_bounded_data_stage_runs_without_full_research_dependencies(
    tmp_path: Path,
) -> None:
    store = ResearchReviewStore(tmp_path, "task-1")
    assert store.bounded_stage_action("data")["producer"] == "solar-data"
    artifact = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content="bounded data result at receipts/data.json",
        phase="bounded_data",
    )
    assert artifact["upstream_refs"] == []
    _accept(store, "data")
    assert store.bounded_stage_action("data")["kind"] == "released"


def test_evidence_reader_only_opens_declared_task_local_sources(
    tmp_path: Path,
) -> None:
    receipt = tmp_path / "receipts" / "data.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text('{"dataset":"SILSO","rows":42}', encoding="utf-8")
    secret = tmp_path / "work" / "private.txt"
    secret.parent.mkdir(parents=True)
    secret.write_text("not declared", encoding="utf-8")
    store = ResearchReviewStore(tmp_path, "task-1")
    store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content="verified receipt: receipts/data.json",
        phase="bounded_data",
    )

    source = store.review_source("data", "receipts/data.json")

    assert source["kind"] == "workspace_file"
    assert source["content"] == '{"dataset":"SILSO","rows":42}'
    assert len(source["sha256"]) == 64
    with pytest.raises(PermissionError, match="not declared"):
        store.review_source("data", "work/private.txt")


def test_evidence_reader_reports_hash_mismatch_after_checkpoint(
    tmp_path: Path,
) -> None:
    session = tmp_path / "work" / "solar_data" / "chat_session.json"
    session.parent.mkdir(parents=True)
    session.write_text('{"dataset":"SILSO","rows":42}', encoding="utf-8")
    store = ResearchReviewStore(tmp_path, "task-1")
    store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content="task-local data receipt",
        phase="bounded_data",
    )
    session.write_text('{"dataset":"SILSO","rows":43}', encoding="utf-8")

    source = store.review_source("data", "work/solar_data/chat_session.json")

    assert source["checkpoint_sha256"] != source["sha256"]
    assert source["hash_matches_checkpoint"] is False
    artifact = store.latest_artifact("data")
    assert artifact is not None
    verdict = store.submit_verdict(
        mode="data",
        decision="accept",
        issues=[],
        accepted_claims=[artifact["claims"][0]["claim_id"]],
    )
    assert verdict["decision"] == "block"
    assert any(
        issue["rule_id"] == "ARTIFACT_SOURCE_HASH_MISMATCH"
        for issue in verdict["issues"]
    )


def test_evidence_reader_rejects_declared_path_that_escapes_workspace(
    tmp_path: Path,
) -> None:
    store = ResearchReviewStore(tmp_path, "task-1")
    artifact = store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content="source at inputs/../outside.txt",
        phase="bounded_planning",
    )
    # The adapter may preserve a producer-supplied path, but the reader still
    # resolves it against the task root and refuses any escape.
    artifact["evidence_refs"] = ["../outside.txt"]
    artifact["claims"][0]["supporting_evidence"] = ["../outside.txt"]
    artifact["artifact_sha256"] = canonical_json_sha256(
        {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    )
    path = store.root / "artifacts" / "planning-artifact" / "v0001.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(PermissionError, match="escapes"):
        store.review_source("planning", "../outside.txt")


def test_new_artifact_version_invalidates_old_acceptance(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "task-1")
    first = store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="first plan"
    )
    _accept(store, "planning")
    assert store.next_action()["stage"] == "data"

    second = store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="revised plan"
    )

    assert first["artifact_sha256"] != second["artifact_sha256"]
    action = store.next_action()
    assert action["kind"] == "review"
    assert action["stage"] == "planning"


def test_old_policy_verdict_cannot_approve_current_artifact(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "task-1")
    artifact = store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="first plan"
    )
    _accept(store, "planning")
    verdict_path = store.root / "verdicts" / "planning-review-0001.json"
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    verdict["policy_version"] = "evidence-policy-old"
    verdict["verdict_sha256"] = canonical_json_sha256(
        {key: value for key, value in verdict.items() if key != "verdict_sha256"}
    )
    verdict_path.write_text(json.dumps(verdict), encoding="utf-8")

    action = store.next_action()

    assert action["kind"] == "review"
    assert action["artifact_refs"] == [store.artifact_ref(artifact)]


def test_upstream_hash_change_invalidates_downstream_stage(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "task-1")
    store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="plan v1"
    )
    _accept(store, "planning")
    store.checkpoint_producer_result(
        stage="data", producer="solar-data", content="data bound to plan v1"
    )
    _accept(store, "data")
    store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="plan v2"
    )
    _accept(store, "planning")

    action = store.next_action()

    assert action["kind"] == "producer"
    assert action["stage"] == "data"
    assert action["phase"] == "data_dependency_refresh"
    assert store.load_state()["stage_status"]["data"] == "pending"


def test_experiment_result_is_bound_to_the_accepted_design_run(
    tmp_path: Path,
) -> None:
    store = ResearchReviewStore(tmp_path, "task-1")
    accepted_run = tmp_path / "experiment" / "runs" / "accepted-run"
    accepted_run.mkdir(parents=True)
    (accepted_run / "state.json").write_text("{}", encoding="utf-8")
    (accepted_run / "design.json").write_text(
        '{"schema_version":"automatic-experiment-design-v1"}', encoding="utf-8"
    )
    design = store.checkpoint_producer_result(
        stage="experiment_design",
        producer="solar-experiment",
        content="experiment/runs/accepted-run/design.json",
        phase="bounded_experiment_design",
        require_canonical_source=True,
    )
    store.submit_verdict(
        mode="experiment_design",
        decision="accept",
        issues=[],
        accepted_claims=[design["claims"][0]["claim_id"]],
    )
    wrong_run = tmp_path / "experiment" / "runs" / "new-unreviewed-run"
    wrong_run.mkdir(parents=True)
    for name in ("record.json", "entry_result.json"):
        (wrong_run / name).write_text("{}", encoding="utf-8")
    (wrong_run / "report.md").write_text("wrong run", encoding="utf-8")

    with pytest.raises(RuntimeError, match="complete task-local canonical"):
        store.checkpoint_producer_result(
            stage="experiment_result",
            producer="solar-experiment",
            content="experiment/runs/new-unreviewed-run/report.md",
            phase="bounded_experiment_result",
            require_canonical_source=True,
        )


def test_adaptive_review_blocks_after_two_no_progress_repeats(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "task-1", no_progress_patience=2)
    for version in range(1, 4):
        store.checkpoint_producer_result(
            stage="planning",
            producer="solar-planner",
            content=f"plan revision {version}",
        )
        verdict = store.submit_verdict(
            mode="planning",
            decision="revise",
            issues=[_issue()],
            next_owner="solar-planner",
        )

    assert verdict["decision"] == "block"
    assert any(issue["rule_id"] == "NO_PROGRESS_STOP" for issue in verdict["issues"])
    assert store.load_state()["status"] == "blocked"


def test_lowered_severity_counts_as_progress(tmp_path: Path) -> None:
    store = ResearchReviewStore(
        tmp_path, "severity-progress-task", no_progress_patience=2
    )
    verdict = None
    for version, severity in [(1, "critical"), (2, "major"), (3, "major")]:
        store.checkpoint_producer_result(
            stage="planning",
            producer="solar-planner",
            content=f"severity progress revision {version}",
        )
        issue = _issue()
        issue["severity"] = severity
        verdict = store.submit_verdict(
            mode="planning",
            decision="revise",
            issues=[issue],
            next_owner="solar-planner",
        )

    assert verdict is not None
    assert verdict["decision"] == "revise"


def test_same_artifact_policy_rereview_does_not_consume_no_progress_patience(
    tmp_path: Path,
) -> None:
    store = ResearchReviewStore(tmp_path, "same-artifact-task", no_progress_patience=2)
    artifact = store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content="unchanged bounded plan",
    )
    issue = _issue()
    first = store.submit_verdict(
        mode="planning",
        decision="revise",
        issues=[issue],
        next_owner="solar-planner",
    )
    second = store.submit_verdict(
        mode="planning",
        decision="revise",
        issues=[issue],
        next_owner="solar-planner",
    )
    third = store.submit_verdict(
        mode="planning",
        decision="revise",
        issues=[issue],
        next_owner="solar-planner",
    )

    assert first["artifact_refs"] == [store.artifact_ref(artifact)]
    assert second["decision"] == "revise"
    assert third["decision"] == "revise"
    assert store.load_state()["status"] == "active"


def test_full_graph_stops_before_an_over_budget_review(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "task-1", budget_multiplier=1)
    state = store.load_state()
    state["review_invocations"] = state["max_review_invocations"]
    store._save_state(state)

    action = store.next_action()

    assert action == {
        "kind": "terminal",
        "status": "blocked",
        "reason": "REVIEW_BUDGET_EXHAUSTED",
    }
    assert store.load_state()["status"] == "blocked"


def test_experiment_result_issue_routes_back_to_data_owner(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "task-1")
    producers = {
        "planning": "solar-planner",
        "data": "solar-data",
        "hypothesis": "solar-hypothesis",
        "experiment_design": "solar-experiment",
    }
    for stage, producer in producers.items():
        store.checkpoint_producer_result(
            stage=stage, producer=producer, content=f"{stage} result"
        )
        _accept(store, stage, independently=stage == "hypothesis")
    store.checkpoint_producer_result(
        stage="experiment_result",
        producer="solar-experiment",
        content="result with a data semantic defect",
    )
    store.submit_verdict(
        mode="experiment_result",
        decision="revise",
        issues=[_issue("solar-data")],
        next_owner="solar-data",
    )

    action = store.next_action()

    assert action["producer"] == "solar-data"
    assert action["stage"] == "data"
    assert action["phase"] == "data_revision_from_experiment_result"


def test_full_graph_requires_post_experiment_hypothesis_update(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "task-1")
    producers = {
        "planning": "solar-planner",
        "data": "solar-data",
        "hypothesis": "solar-hypothesis",
        "experiment_design": "solar-experiment",
        "experiment_result": "solar-experiment",
    }
    for stage, producer in producers.items():
        store.checkpoint_producer_result(
            stage=stage, producer=producer, content=f"{stage} result"
        )
        _accept(store, stage, independently=stage == "hypothesis")

    action = store.next_action()
    assert action == {
        "kind": "producer",
        "stage": "hypothesis",
        "producer": "solar-hypothesis",
        "phase": "hypothesis_update",
        "required_upstream": action["required_upstream"],
    }

    updated = store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="hypothesis updated from the verified null result",
        phase="hypothesis_update",
    )
    assert any("experiment_result" in ref for ref in updated["upstream_refs"])
    assert store.load_state()["stage_status"]["experiment_result"] == "accepted"
    _accept(store, "hypothesis", independently=True)
    assert store.next_action()["review_mode"] == "integration"


def test_reviewer_cannot_self_attest_heterogeneous_pass(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "task-1")
    artifact = store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="bounded plan"
    )

    verdict = store.submit_verdict(
        mode="planning",
        decision="accept",
        issues=[],
        accepted_claims=[artifact["claims"][0]["claim_id"]],
        independent_review={
            "status": "heterogeneous_pass",
            "reviewer": "self-asserted-model",
            "notes": "claimed without a receipt",
        },
    )

    assert verdict["independent_review"]["status"] == "not_configured"
    assert verdict["independent_review"]["reviewer"] is None


def test_hash_bound_independent_receipt_is_accepted(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "task-1")
    artifact = store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="bounded plan"
    )
    ref = store.artifact_ref(artifact)
    _write_independent_receipt(store, "planning", [ref])

    verdict = store.submit_verdict(
        mode="planning",
        decision="accept",
        issues=[],
        accepted_claims=[artifact["claims"][0]["claim_id"]],
        independent_review={"status": "heterogeneous_pass"},
    )

    assert verdict["independent_review"]["status"] == "heterogeneous_pass"
    assert verdict["independent_review"]["reviewer"] == "review-model-b"


def test_integration_mechanism_claims_require_independent_review(
    tmp_path: Path,
) -> None:
    store = ResearchReviewStore(tmp_path, "task-1")
    producers = {
        "planning": "solar-planner",
        "data": "solar-data",
        "hypothesis": "solar-hypothesis",
        "experiment_design": "solar-experiment",
        "experiment_result": "solar-experiment",
    }
    for stage, producer in producers.items():
        store.checkpoint_producer_result(
            stage=stage, producer=producer, content=f"{stage} result"
        )
        _accept(store, stage, independently=stage == "hypothesis")
    store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="post-result mechanism update",
        phase="hypothesis_update",
    )
    _accept(store, "hypothesis", independently=True)
    integration = store.ensure_integration_artifact()

    verdict = store.submit_verdict(
        mode="integration",
        decision="accept",
        issues=[],
        accepted_claims=[claim["claim_id"] for claim in integration["claims"]],
        independent_review={"status": "heterogeneous_pass"},
    )

    assert verdict["decision"] == "human_review"
    assert verdict["issues"][0]["rule_id"] == "INDEPENDENT_REVIEW_REQUIRED"
    assert store.next_action()["kind"] == "independent_review"

    refs = [store.artifact_ref(integration)]
    store.mark_independent_review_unavailable(
        "integration", refs, "auxiliary model unavailable"
    )
    assert store.next_action() == {"kind": "terminal", "status": "human_review"}

    _write_independent_receipt(store, "integration", refs)
    action = store.next_action()
    assert action["kind"] == "review"
    assert action["independent_review"] == "pass"


def test_final_release_requires_limits_and_returns_exact_accepted_text(
    tmp_path: Path,
) -> None:
    store = ResearchReviewStore(tmp_path, "task-1")
    producers = {
        "planning": "solar-planner",
        "data": "solar-data",
        "hypothesis": "solar-hypothesis",
        "experiment_design": "solar-experiment",
        "experiment_result": "solar-experiment",
    }
    for stage, producer in producers.items():
        store.checkpoint_producer_result(
            stage=stage, producer=producer, content=f"{stage} result"
        )
        _accept(store, stage, independently=stage == "hypothesis")
    store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="hypothesis updated from the observed result",
        phase="hypothesis_update",
    )
    _accept(store, "hypothesis", independently=True)
    integration = store.ensure_integration_artifact()
    _write_independent_receipt(store, "integration", [store.artifact_ref(integration)])
    store.submit_verdict(
        mode="integration",
        decision="accept_with_limits",
        issues=[],
        accepted_claims=[claim["claim_id"] for claim in integration["claims"]],
        carry_forward_limits=["No external replication is available."],
        independent_review={"status": "heterogeneous_pass"},
    )

    cited_claim_id = integration["claims"][0]["claim_id"]
    with pytest.raises(ValueError, match="omits required carried limitations"):
        store.prepare_release(
            "A coherent report without its limitations.",
            [
                {
                    "claim_id": cited_claim_id,
                    "draft_excerpt": "A coherent report without its limitations.",
                }
            ],
        )

    report = (
        "# Result\n\nA coherent accepted synthesis.\n\n"
        + "\n\n".join(integration["limitations"])
        + "\n\nNo external replication is available."
    )
    with pytest.raises(ValueError, match="numbers absent from cited accepted claims"):
        store.prepare_release(
            report + "\n\nThe projected value is 2042.",
            [
                {
                    "claim_id": cited_claim_id,
                    "draft_excerpt": "A coherent accepted synthesis.",
                }
            ],
        )
    with pytest.raises(ValueError, match="material blocks without claim_citations"):
        store.prepare_release(
            report + "\n\nAn additional uncited interpretation.",
            [
                {
                    "claim_id": cited_claim_id,
                    "draft_excerpt": "A coherent accepted synthesis.",
                }
            ],
        )
    release = store.prepare_release(
        report,
        [
            {
                "claim_id": cited_claim_id,
                "draft_excerpt": "A coherent accepted synthesis.",
            }
        ],
    )
    first_release_verdict = store.submit_verdict(
        mode="final_release",
        decision="accept",
        issues=[],
        accepted_claims=[release["claims"][0]["claim_id"]],
    )

    assert first_release_verdict["decision"] == "human_review"
    assert store.next_action()["kind"] == "independent_review"
    _write_independent_receipt(store, "final_release", [store.artifact_ref(release)])
    store.submit_verdict(
        mode="final_release",
        decision="accept",
        issues=[],
        accepted_claims=[release["claims"][0]["claim_id"]],
        independent_review={"status": "heterogeneous_pass"},
    )

    assert store.next_action()["kind"] == "released"
    assert store.accepted_release_markdown() == report


@dataclass
class _Runtime:
    config: dict[str, object]


class _Request:
    def __init__(self, tool_call, state, runtime):
        self.tool_call = tool_call
        self.state = state
        self.runtime = runtime

    def override(self, *, tool_call):
        return _Request(tool_call, self.state, self.runtime)


def _config(tmp_path: Path, monkeypatch, thread_id: str) -> dict[str, object]:
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "registry"))
    monkeypatch.setattr(paths, "WORKSPACE_ROOT", tmp_path)
    ensure_thread_workspace(thread_id, tmp_path)
    return {
        "configurable": {
            "thread_id": thread_id,
            "workspace_thread_id": thread_id,
        }
    }


def test_orchestration_checkpoints_producer_and_forces_reviewer(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, monkeypatch, "review-task")
    middleware = ResearchReviewOrchestrationMiddleware()
    state = {"research_route": {"mode": "full_research"}}
    planner_run = (
        Path(ensure_thread_workspace("review-task", tmp_path).workspace)
        / "planner"
        / "runs"
        / "run-1"
    )
    planner_run.mkdir(parents=True)
    (planner_run / "research_plan.json").write_text(
        '{"schema_version":"research-plan-v1"}', encoding="utf-8"
    )
    request = _Request(
        {
            "name": "task",
            "id": "call-planner",
            "args": {"subagent_type": "solar-planner", "description": "plan"},
        },
        state,
        _Runtime(config),
    )

    result = middleware.wrap_tool_call(
        request,
        lambda rewritten: ToolMessage(
            content="bounded planning output",
            tool_call_id=rewritten.tool_call["id"],
            name="task",
        ),
    )

    assert "RESEARCH_ARTIFACT_V2" in str(result.content)
    wrong = _Request(
        {
            "name": "task",
            "id": "call-wrong",
            "args": {"subagent_type": "solar-data", "description": "skip review"},
        },
        state,
        _Runtime(config),
    )
    blocked = middleware.wrap_tool_call(wrong, lambda _request: object())
    assert blocked.status == "error"
    assert "expected solar-evidence" in str(blocked.content)


def test_orchestration_registers_planner_evidence_revision_before_delegation(
    tmp_path: Path, monkeypatch
) -> None:
    thread_id = "planner-review-registration"
    config = _config(tmp_path, monkeypatch, thread_id)
    workspace = Path(ensure_thread_workspace(thread_id, tmp_path).workspace)
    example = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "research/planner/examples/definition_audit_response.json"
        ).read_text(encoding="utf-8")
    )
    brief = json.loads(
        planner_tools.research_planner_get_brief.invoke(
            {"request_input": example["research_question"]}, config=config
        )
    )
    for section_name in planner_tools._PLAN_SECTION_ORDER:
        result = json.loads(
            planner_tools.research_planner_update_draft.invoke(
                {
                    "section_name": section_name,
                    "section_json": json.dumps(
                        example["plan_content"][section_name], ensure_ascii=False
                    ),
                    "request_sha256": brief["request_sha256"],
                },
                config=config,
            )
        )
        assert result["status"] == "draft_section_persisted"
    assert (
        json.loads(
            planner_tools.research_planner_validate_draft.invoke(
                {"request_sha256": brief["request_sha256"]}, config=config
            )
        )["status"]
        == "plan_ready"
    )

    store = ResearchReviewStore(workspace, thread_id)
    artifact = store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="initial plan"
    )
    issue = _issue(owner="solar-planner")
    issue["claim_ref"] = artifact["claims"][0]["claim_id"]
    issue["fingerprint"] = issue_fingerprint(
        issue["rule_id"], issue["claim_ref"], issue["owner"]
    )
    verdict = store.submit_verdict(
        mode="planning",
        decision="revise",
        issues=[issue],
        next_owner="solar-planner",
    )
    request = _Request(
        {
            "name": "task",
            "id": "call-planner-revision",
            "args": {
                "subagent_type": "solar-planner",
                "description": "revise the plan",
            },
        },
        {"research_route": {"mode": "full_research"}},
        _Runtime(config),
    )

    rewritten, action, early = ResearchReviewOrchestrationMiddleware()._prepare(request)

    assert early is None
    assert action["revision_review_id"] == verdict["review_id"]
    assert "planner_revision_checkpoint" in rewritten.tool_call["args"]["description"]
    checkpoint = json.loads(
        planner_tools.research_planner_get_draft_status.invoke(
            {"request_sha256": brief["request_sha256"]}, config=config
        )
    )
    assert checkpoint["validated"] is False
    assert checkpoint["next_action"] == "repair_evidence_revision"
    assert checkpoint["pending_evidence_revision"]["review_id"] == verdict["review_id"]


def test_orchestration_deterministically_freezes_validated_planner_draft(
    tmp_path: Path, monkeypatch
) -> None:
    thread_id = "planner-auto-freeze"
    config = _config(tmp_path, monkeypatch, thread_id)
    workspace = Path(ensure_thread_workspace(thread_id, tmp_path).workspace)
    freeze_calls: list[object] = []

    def fake_freeze(config_arg: object) -> dict[str, object]:
        freeze_calls.append(config_arg)
        planner_run = workspace / "planner" / "runs" / "auto-frozen-plan"
        planner_run.mkdir(parents=True)
        plan_path = planner_run / "research_plan.json"
        plan_path.write_text(
            json.dumps(
                {
                    "schema_version": "research-plan-v1",
                    "research_question": "Auto-frozen validated plan",
                    "research_route": [],
                }
            ),
            encoding="utf-8",
        )
        return {
            "status": "frozen_and_valid",
            "research_plan_path": "planner/runs/auto-frozen-plan/research_plan.json",
        }

    monkeypatch.setattr(
        "jw.middleware.research_review_orchestration._freeze_validated_planner_draft",
        fake_freeze,
    )
    request = _Request(
        {
            "name": "task",
            "id": "call-planner-auto-freeze",
            "args": {"subagent_type": "solar-planner", "description": "plan"},
        },
        {"research_route": {"mode": "full_research"}},
        _Runtime(config),
    )

    result = ResearchReviewOrchestrationMiddleware().wrap_tool_call(
        request,
        lambda rewritten: ToolMessage(
            content="validated planner draft is ready",
            tool_call_id=rewritten.tool_call["id"],
            name="task",
        ),
    )

    assert freeze_calls == [config]
    assert "DETERMINISTIC PLANNER FREEZE" in str(result.content)
    assert "RESEARCH_ARTIFACT_V2" in str(result.content)
    store = ResearchReviewStore(workspace, thread_id)
    assert store.load_state()["stage_status"]["planning"] == "produced"


def test_planner_auto_freeze_validates_complete_draft_before_freezing(
    monkeypatch,
) -> None:
    from jw.middleware import research_review_orchestration as orchestration

    calls: list[str] = []

    def validate(*, request_sha256, config):
        calls.append(f"validate:{request_sha256}:{config}")
        return json.dumps({"status": "plan_ready"})

    def freeze(*, request_sha256, config):
        calls.append(f"freeze:{request_sha256}:{config}")
        return json.dumps({"status": "frozen_and_valid"})

    monkeypatch.setattr(planner_tools.research_planner_validate_draft, "func", validate)
    monkeypatch.setattr(planner_tools.research_planner_freeze_plan, "func", freeze)

    assert orchestration._freeze_validated_planner_draft("cfg") == {
        "status": "frozen_and_valid"
    }
    assert calls == ["validate::cfg", "freeze::cfg"]


def test_planner_auto_freeze_refuses_invalid_complete_draft(monkeypatch) -> None:
    from jw.middleware import research_review_orchestration as orchestration

    monkeypatch.setattr(
        planner_tools.research_planner_validate_draft,
        "func",
        lambda **_kwargs: json.dumps(
            {"status": "revision_required", "errors": ["route mismatch"]}
        ),
    )

    with pytest.raises(RuntimeError, match="route mismatch"):
        orchestration._freeze_validated_planner_draft("cfg")


def test_orchestration_stops_third_required_specialist_failure(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, monkeypatch, "planner-failure-stop")
    messages = [HumanMessage(content="run full research")]
    for index in range(2):
        call_id = f"call-planner-{index}"
        messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {"subagent_type": "solar-planner"},
                            "id": call_id,
                            "type": "tool_call",
                        }
                    ],
                ),
                ToolMessage(
                    content=(
                        "[TOOL ERROR CAPSULE]\nfingerprint="
                        + (("a" if index == 0 else "b") * 64)
                        + "\n"
                        "tool=task\nerror=RuntimeError: planner deterministic "
                        "validation did not pass"
                    ),
                    tool_call_id=call_id,
                    name="task",
                    status="error",
                ),
            ]
        )
    request = _Request(
        {
            "name": "task",
            "id": "call-planner-third",
            "args": {
                "subagent_type": "solar-planner",
                "description": "retry planner",
            },
        },
        {"research_route": {"mode": "full_research"}, "messages": messages},
        _Runtime(config),
    )

    result = ResearchReviewOrchestrationMiddleware().wrap_tool_call(
        request,
        lambda _rewritten: pytest.fail("third identical specialist call must stop"),
    )

    assert isinstance(result, Command)
    assert result.goto == "__end__"
    tool_result = result.update["messages"][0]
    assert "failed twice" in str(tool_result.content)
    store = ResearchReviewStore(
        Path(ensure_thread_workspace("planner-failure-stop", tmp_path).workspace),
        "planner-failure-stop",
    )
    state = store.load_state()
    assert state["action_invocations"] == 0
    assert state["status"] == "blocked"
    assert state["stage_status"]["planning"] == "blocked"
    receipt = store.latest_tool_failure_receipt()
    assert receipt is not None
    assert receipt["reason_code"] == "REQUIRED_SPECIALIST_FAILED_TWICE"
    assert receipt["fingerprints"] == ["a" * 64, "b" * 64]
    assert store.next_action() == {
        "kind": "terminal",
        "status": "blocked",
        "reason": "REQUIRED_SPECIALIST_FAILED_TWICE",
    }


def test_orchestration_counts_local_preflight_failures_as_specialist_failures(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, monkeypatch, "planner-preflight-failure-stop")
    messages = [HumanMessage(content="run full research")]
    failure_text = (
        "[RESEARCH REVIEW BLOCKED] producer local preflight failed before Evidence "
        "review: RuntimeError: planning returned without its complete task-local "
        "canonical v1 artifact"
    )
    for index in range(2):
        call_id = f"call-planner-preflight-{index}"
        messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {"subagent_type": "solar-planner"},
                            "id": call_id,
                            "type": "tool_call",
                        }
                    ],
                ),
                ToolMessage(
                    content=failure_text,
                    tool_call_id=call_id,
                    name="task",
                    status="error",
                ),
            ]
        )
    request = _Request(
        {
            "name": "task",
            "id": "call-planner-preflight-third",
            "args": {
                "subagent_type": "solar-planner",
                "description": "retry planner",
            },
        },
        {"research_route": {"mode": "full_research"}, "messages": messages},
        _Runtime(config),
    )

    result = ResearchReviewOrchestrationMiddleware().wrap_tool_call(
        request,
        lambda _rewritten: pytest.fail("third preflight failure retry must stop"),
    )

    assert isinstance(result, Command)
    assert result.goto == "__end__"
    assert "failed twice" in str(result.update["messages"][0].content)
    store = ResearchReviewStore(
        Path(
            ensure_thread_workspace(
                "planner-preflight-failure-stop", tmp_path
            ).workspace
        ),
        "planner-preflight-failure-stop",
    )
    assert store.load_state()["status"] == "blocked"


def test_orchestration_checkpoints_command_shaped_task_result(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, monkeypatch, "command-result-task")
    middleware = ResearchReviewOrchestrationMiddleware()
    workspace = Path(ensure_thread_workspace("command-result-task", tmp_path).workspace)
    hypothesis_state = workspace / "work" / "scientific_hypothesis_state.json"
    hypothesis_state.parent.mkdir(parents=True, exist_ok=True)
    hypothesis_state.write_text(
        '{"schema_version":1,"latest_draft":{"candidates":[]}}', encoding="utf-8"
    )
    request = _Request(
        {
            "name": "task",
            "id": "call-hypothesis",
            "args": {
                "subagent_type": "solar-hypothesis",
                "description": "hypothesis",
            },
        },
        {
            "research_route": {
                "mode": "verified_analysis",
                "task_intent": "hypothesis_generation",
                "required_specialist": "solar-hypothesis",
            }
        },
        _Runtime(config),
    )

    result = middleware.wrap_tool_call(
        request,
        lambda rewritten: Command(
            update={
                "messages": [
                    ToolMessage(
                        content="persisted hypothesis",
                        tool_call_id=rewritten.tool_call["id"],
                        name="task",
                    )
                ],
                "subagent_state": "preserved",
            }
        ),
    )

    assert isinstance(result, Command)
    assert result.update["subagent_state"] == "preserved"
    assert "RESEARCH_ARTIFACT_V2" in str(result.update["messages"][0].content)
    store = ResearchReviewStore(workspace, "command-result-task")
    assert store.latest_artifact("hypothesis") is not None


def test_canonical_revision_must_change_producer_source(
    tmp_path: Path, monkeypatch
) -> None:
    _config(tmp_path, monkeypatch, "unchanged-revision-task")
    workspace = Path(
        ensure_thread_workspace("unchanged-revision-task", tmp_path).workspace
    )
    state_path = workspace / "work" / "scientific_hypothesis_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        '{"schema_version":1,"latest_draft":{"candidates":[{"id":"H1"}]}}',
        encoding="utf-8",
    )
    store = ResearchReviewStore(workspace, "unchanged-revision-task")
    first = store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="initial hypothesis",
        phase="bounded_hypothesis",
        require_canonical_source=True,
    )
    issue = _issue(owner="solar-hypothesis")
    verdict = store.submit_verdict(
        mode="hypothesis",
        decision="revise",
        issues=[issue],
        next_owner="solar-hypothesis",
    )

    with pytest.raises(RuntimeError, match="did not change any task-local canonical"):
        store.checkpoint_producer_result(
            stage="hypothesis",
            producer="solar-hypothesis",
            content="reviewer prose accidentally returned as producer output",
            phase="bounded_hypothesis_revision",
            require_canonical_source=True,
            revision_review_id=verdict["review_id"],
        )

    state_path.write_text(
        '{"schema_version":1,"latest_draft":{"candidates":[{"id":"H1","revision":2}]}}',
        encoding="utf-8",
    )
    revised = store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="revised hypothesis",
        phase="bounded_hypothesis_revision",
        require_canonical_source=True,
        revision_review_id=verdict["review_id"],
    )
    assert revised["version"] == first["version"] + 1


def test_bounded_mechanism_hypothesis_routes_to_independent_review(
    tmp_path: Path,
) -> None:
    store = ResearchReviewStore(tmp_path, "bounded-independent-task")
    artifact = store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="bounded mechanism hypothesis",
        phase="bounded_hypothesis",
    )
    verdict = store.submit_verdict(
        mode="hypothesis",
        decision="accept",
        issues=[],
        accepted_claims=[artifact["claims"][0]["claim_id"]],
    )
    assert verdict["decision"] == "human_review"
    action = store.bounded_stage_action("hypothesis")
    assert action["kind"] == "independent_review"

    _write_independent_receipt(store, "hypothesis", action["artifact_refs"])
    resumed = store.bounded_stage_action("hypothesis")
    assert resumed["kind"] == "review"
    assert resumed["independent_review"] == "pass"


def test_wrong_task_call_is_deterministically_redirected_to_independent_review(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, monkeypatch, "wrong-independent-action-task")
    workspace = Path(
        ensure_thread_workspace("wrong-independent-action-task", tmp_path).workspace
    )
    store = ResearchReviewStore(workspace, "wrong-independent-action-task")
    artifact = store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="bounded mechanism hypothesis",
        phase="bounded_hypothesis",
    )
    store.submit_verdict(
        mode="hypothesis",
        decision="accept",
        issues=[],
        accepted_claims=[artifact["claims"][0]["claim_id"]],
    )
    middleware = ResearchReviewOrchestrationMiddleware()
    redirected: list[tuple[str, object]] = []
    monkeypatch.setattr(
        research_independent_review,
        "func",
        lambda mode, config=None: (
            redirected.append((mode, config))
            or '{"ok":false,"message":"heterogeneous reviewer unavailable"}'
        ),
    )
    route = {
        "research_route": {
            "mode": "verified_analysis",
            "task_intent": "hypothesis_update",
            "required_specialist": "solar-hypothesis",
        }
    }
    first_request = _Request(
        {
            "name": "task",
            "id": "wrong-independent-1",
            "args": {"subagent_type": "solar-hypothesis"},
        },
        route,
        _Runtime(config),
    )
    result = middleware.wrap_tool_call(
        first_request, lambda _request: pytest.fail("wrong action must not run")
    )

    assert isinstance(result, ToolMessage)
    assert str(result.content).startswith("[DETERMINISTIC ACTION REDIRECT]")
    assert redirected == [("hypothesis", config)]
    assert store.bounded_stage_action("hypothesis")["kind"] == "independent_review"


def test_accept_decision_rejects_unresolved_major_issue(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "decision-consistency-task")
    artifact = store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="bounded plan"
    )
    with pytest.raises(
        ValueError, match="accept cannot carry unresolved critical or major"
    ):
        store.submit_verdict(
            mode="planning",
            decision="accept",
            issues=[_issue("solar-planner")],
            accepted_claims=[artifact["claims"][0]["claim_id"]],
        )


def test_accept_with_limits_rejects_unresolved_critical_issue(
    tmp_path: Path,
) -> None:
    store = ResearchReviewStore(tmp_path, "critical-consistency-task")
    artifact = store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="bounded plan"
    )
    issue = _issue("solar-planner")
    issue["severity"] = "critical"
    with pytest.raises(
        ValueError,
        match="accept_with_limits cannot carry unresolved critical or major",
    ):
        store.submit_verdict(
            mode="planning",
            decision="accept_with_limits",
            issues=[issue],
            accepted_claims=[artifact["claims"][0]["claim_id"]],
            carry_forward_limits=["The critical defect remains unresolved."],
        )


def test_stale_policy_human_review_reopens_current_artifact(
    tmp_path: Path,
) -> None:
    store = ResearchReviewStore(tmp_path, "stale-human-review-task")
    artifact = store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="bounded mechanism hypothesis",
        phase="bounded_hypothesis",
    )
    verdict = store.submit_verdict(
        mode="hypothesis",
        decision="accept",
        issues=[],
        accepted_claims=[artifact["claims"][0]["claim_id"]],
    )
    assert verdict["decision"] == "human_review"
    verdict_path = store.root / "verdicts" / f"{verdict['review_id']}.json"
    stale = json.loads(verdict_path.read_text(encoding="utf-8"))
    stale["policy_version"] = "evidence-policy-obsolete"
    verdict_path.write_text(json.dumps(stale), encoding="utf-8")

    action = store.bounded_stage_action("hypothesis")
    assert action["kind"] == "review"
    assert action["artifact_refs"] == [store.artifact_ref(artifact)]
    assert store.load_state()["status"] == "active"


def test_reviewer_issue_cannot_invent_unbound_numbers(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "reviewer-number-task")
    store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content="Plan for solar cycle 25 without a dated sample inventory.",
    )
    issue = _issue("solar-planner")
    issue["message"] = "The direct observations allegedly begin in 1976."
    issue["required_action"] = "State that only 4-5 independent pairs exist."
    with pytest.raises(ValueError, match="numbers absent from the reviewed"):
        store.submit_verdict(
            mode="planning",
            decision="revise",
            issues=[issue],
            next_owner="solar-planner",
        )


def test_orchestration_applies_same_review_loop_to_bounded_data(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, monkeypatch, "bounded-data-task")
    middleware = ResearchReviewOrchestrationMiddleware()
    workspace = Path(ensure_thread_workspace("bounded-data-task", tmp_path).workspace)
    data_state = workspace / "work" / "solar_data" / "chat_session.json"
    data_state.parent.mkdir(parents=True)
    data_state.write_text('{"schema_version":1}', encoding="utf-8")
    state = {
        "research_route": {
            "mode": "verified_analysis",
            "task_intent": "data_preparation",
            "required_specialist": "solar-data",
        }
    }
    request = _Request(
        {
            "name": "task",
            "id": "call-data",
            "args": {"subagent_type": "solar-data", "description": "prepare data"},
        },
        state,
        _Runtime(config),
    )

    result = middleware.wrap_tool_call(
        request,
        lambda rewritten: ToolMessage(
            content="bounded data output",
            tool_call_id=rewritten.tool_call["id"],
            name="task",
        ),
    )

    assert "RESEARCH_ARTIFACT_V2" in str(result.content)
    store = ResearchReviewStore(
        Path(ensure_thread_workspace("bounded-data-task", tmp_path).workspace),
        "bounded-data-task",
    )
    artifact = store.latest_artifact("data")
    assert artifact is not None
    assert artifact["payload"]["phase"] == "bounded_data"


def test_reviewer_cannot_pass_without_persisted_verdict(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, monkeypatch, "review-task")
    binding = ensure_thread_workspace("review-task", tmp_path)
    store = ResearchReviewStore(Path(binding.workspace), "review-task")
    store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="plan"
    )
    request = _Request(
        {
            "name": "task",
            "id": "call-evidence",
            "args": {"subagent_type": "solar-evidence", "description": "review"},
        },
        {"research_route": {"mode": "full_research"}},
        _Runtime(config),
    )
    middleware = ResearchReviewOrchestrationMiddleware()

    result = middleware.wrap_tool_call(
        request,
        lambda rewritten: ToolMessage(
            content="looks good",
            tool_call_id=rewritten.tool_call["id"],
            name="task",
        ),
    )

    assert result.status == "error"
    assert "without persisting" in str(result.content)


def test_orchestration_short_circuits_model_for_deterministic_review_defect(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, monkeypatch, "deterministic-review-task")
    binding = ensure_thread_workspace("deterministic-review-task", tmp_path)
    store = ResearchReviewStore(Path(binding.workspace), "deterministic-review-task")
    store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="下一太阳活动周期振幅受该周期自身BMR倾斜角随机涨落影响。",
        phase="bounded_hypothesis",
    )
    request = _Request(
        {
            "name": "task",
            "id": "call-evidence-preflight",
            "args": {"subagent_type": "solar-evidence", "description": "review"},
        },
        {
            "research_route": {
                "mode": "verified_analysis",
                "task_intent": "hypothesis_update",
                "required_specialist": "solar-hypothesis",
            }
        },
        _Runtime(config),
    )
    middleware = ResearchReviewOrchestrationMiddleware()

    result = middleware.wrap_tool_call(
        request,
        lambda _rewritten: pytest.fail("remote Evidence model must not be invoked"),
    )

    assert result.status != "error"
    assert "[DETERMINISTIC REVIEW VERDICT]" in str(result.content)
    verdict = store.verdicts(mode="hypothesis")[-1]
    assert verdict["decision"] == "revise"
    assert verdict["issues"][0]["rule_id"] == "TEMPORAL_CAUSAL_ORDER"


def test_configured_heterogeneous_tool_writes_separate_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, monkeypatch, "heterogeneous-task")
    binding = ensure_thread_workspace("heterogeneous-task", tmp_path)
    store = ResearchReviewStore(Path(binding.workspace), "heterogeneous-task")
    producers = {
        "planning": "solar-planner",
        "data": "solar-data",
        "hypothesis": "solar-hypothesis",
        "experiment_design": "solar-experiment",
        "experiment_result": "solar-experiment",
    }
    for stage, producer in producers.items():
        store.checkpoint_producer_result(
            stage=stage, producer=producer, content=f"{stage} result"
        )
        _accept(store, stage, independently=stage == "hypothesis")
    store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="post-result mechanism update",
        phase="hypothesis_update",
    )
    _accept(store, "hypothesis", independently=True)
    integration = store.ensure_integration_artifact()
    store.submit_verdict(
        mode="integration",
        decision="accept",
        issues=[],
        accepted_claims=[claim["claim_id"] for claim in integration["claims"]],
        independent_review={"status": "heterogeneous_pass"},
    )

    class FakeModel:
        def with_structured_output(self, _schema):
            return self

        def invoke(self, _messages):
            return {"decision": "pass", "notes": "independent bounded pass"}

    monkeypatch.setattr(
        "jw.config.get_effective_config",
        lambda: SimpleNamespace(
            model="primary-model",
            provider="primary-provider",
            auxiliary_model="review-model",
            auxiliary_provider="review-provider",
        ),
    )
    monkeypatch.setattr("jw.llm.get_chat_model", lambda **_kwargs: FakeModel())

    result = json.loads(research_independent_review.func("integration", config=config))

    assert result["ok"] is True
    assert result["result"]["reviewer_id"] == "review-provider:review-model"
    assert store.next_action()["kind"] == "review"


def test_same_qwen_family_cannot_satisfy_heterogeneous_release_gate(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, monkeypatch, "same-family-review-task")
    binding = ensure_thread_workspace("same-family-review-task", tmp_path)
    store = ResearchReviewStore(Path(binding.workspace), "same-family-review-task")
    artifact = store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="bounded mechanism hypothesis",
        phase="bounded_hypothesis",
    )
    verdict = store.submit_verdict(
        mode="hypothesis",
        decision="accept",
        issues=[],
        accepted_claims=[artifact["claims"][0]["claim_id"]],
    )
    assert verdict["decision"] == "human_review"

    monkeypatch.setattr(
        "jw.config.get_effective_config",
        lambda: SimpleNamespace(
            model="qwen3.7-max",
            provider="dashscope",
            auxiliary_model="qwen3.7-plus",
            auxiliary_provider="dashscope",
        ),
    )
    monkeypatch.setattr(
        "jw.llm.get_chat_model",
        lambda **_kwargs: pytest.fail("same-family reviewer must not be invoked"),
    )

    result = json.loads(research_independent_review.func("hypothesis", config=config))

    assert result["ok"] is False
    assert "no genuinely heterogeneous auxiliary model family" in result["message"]
    assert store.bounded_stage_action("hypothesis")["kind"] == "terminal"
    assert store.load_state()["status"] == "human_review"


def test_changed_auxiliary_reviewer_configuration_reopens_independent_action(
    tmp_path: Path, monkeypatch
) -> None:
    store = ResearchReviewStore(tmp_path, "changed-reviewer-task")
    artifact = store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="bounded mechanism hypothesis",
        phase="bounded_hypothesis",
    )
    store.submit_verdict(
        mode="hypothesis",
        decision="accept",
        issues=[],
        accepted_claims=[artifact["claims"][0]["claim_id"]],
    )
    refs = [store.artifact_ref(artifact)]
    store.mark_independent_review_unavailable(
        "hypothesis",
        refs,
        "review-model-a unavailable",
        reviewer_id="provider-a:review-model-a@independent-review-tool-v2",
    )
    current = SimpleNamespace(
        model="primary-model",
        provider="primary-provider",
        auxiliary_model="review-model-a",
        auxiliary_provider="provider-a",
    )
    monkeypatch.setattr(
        "jw.config.get_effective_config",
        lambda: current,
    )
    assert store.bounded_stage_action("hypothesis")["kind"] == "terminal"

    current.auxiliary_model = "review-model-b"
    reopened = store.bounded_stage_action("hypothesis")
    assert reopened["kind"] == "independent_review"
    assert reopened["artifact_refs"] == refs
