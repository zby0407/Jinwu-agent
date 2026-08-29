from __future__ import annotations

import csv
import hashlib
import json
import multiprocessing
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier, Lock
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from jw import paths
from jw.middleware.research_review_orchestration import (
    _CANONICAL_CHECKPOINT_DIRECTIVE,
    ResearchReviewOrchestrationMiddleware,
    _data_pair_mapping_note,
    _ensure_solar_cycle_pair_analysis_table,
    _persist_solar_cycle_pair_analysis_table,
    _solar_cycle_pair_analysis_from_path,
    _upstream_context,
    _write_hypothesis_request,
)
from jw.research_review import ResearchReviewStore
from jw.research_protocols import SOLAR_CYCLE_26_FORECAST_BACKTEST_DATA_PRODUCT
from jw.tools import research_planner as planner_tools
from jw.tools.research_review import (
    _normalize_issues,
    evidence_review_record_assessment,
    evidence_review_record_scientific_quality,
    evidence_review_submit_round,
    evidence_review_submit_verdict,
)
from jw.tools.solar_feature import _task_chat_session
from jw.workspaces import ensure_thread_workspace, register_project_data_file
from research_review.adapters import (
    adapt_v1_producer_output,
    project_forecast_claim_from_receipt,
)
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
from scientific_hypothesis import TAIL_REVIEW_VERSION, candidate_pool_sha256
from scientific_hypothesis.harness import validate_evidence_provenance


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


def _quality_claim(claim_id: str, *, component: str = "statement") -> dict[str, object]:
    return {
        "claim_id": claim_id,
        "claim_component": component,
        "load_bearing": True,
        "evidence_matrix": [
            {
                "source_ref": None,
                "evidence_role": "gap",
                "source_class": "unknown",
                "evidence_scope": "unknown",
                "directness": "not_assessable",
                "scope_match": "not_assessable",
                "independence_group": "unresolved-gap",
                "locator": "No inspected source section is available.",
                "entailment": "not_assessable",
                "quality_cap": "exploratory",
                "rationale": "The missing source is recorded as a gap, not support.",
            }
        ],
        "method_assessment": {
            "design_status": "not_assessed",
            "independent_sample_unit": "not assessed",
            "independent_sample_count": None,
            "validation_status": "not_assessed",
            "uncertainty_status": "not_assessed",
            "reproducibility_status": "not_assessed",
            "notes": "No empirical analysis is asserted by this planning claim.",
        },
        "novelty_assessment": {
            "status": "novelty_not_assessed",
            "contribution_type": "not_assessed",
            "novelty_delta": "Novelty has not been adjudicated.",
            "nearest_prior_art": [],
            "query_axes": [],
            "searched_family_count": 0,
            "search_cutoff": None,
            "coverage_gaps": ["Nearest-prior-art review is pending."],
        },
        "conclusion_cap": "exploratory",
        "quality_status": "exploratory",
        "key_gaps": ["Independent evidence is unavailable."],
    }


def _accept(store: ResearchReviewStore, mode: str) -> None:
    target = store.latest_artifact(mode)
    assert target is not None
    store.submit_verdict(
        mode=mode,
        decision="accept",
        issues=[],
        accepted_claims=[target["claims"][0]["claim_id"]],
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
    assert "canonical JSON" in projection["inspection_instruction"]
    assert "raw file bytes" in projection["inspection_instruction"]
    assert "producer_result" not in projection
    assert "REDUNDANT-PRODUCER-REPORT" not in encoded
    assert len(encoded) < 20_000


def test_review_context_explains_accepted_upstream_boundary(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "upstream-acceptance-context")
    data = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content="A task-local historical cycle table was produced.",
        phase="bounded_data",
    )
    data_claim_id = data["claims"][0]["claim_id"]
    store.submit_verdict(
        mode="data",
        decision="accept_with_limits",
        issues=[],
        accepted_claims=[data_claim_id],
        carry_forward_limits=["No current-cycle predictive skill was tested."],
    )
    store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="A bounded precursor hypothesis.",
        phase="bounded_hypothesis",
    )

    context = store.review_context("hypothesis")

    assert context["upstream_acceptance"] == [
        {
            "artifact_ref": store._long_ref(data),
            "stage": "data",
            "decision": "accept_with_limits",
            "accepted_claims": [data_claim_id],
            "carry_forward_limits": ["No current-cycle predictive skill was tested."],
            "interpretation": (
                "The upstream artifact and its declared data/provenance boundary "
                "passed Evidence review. Preserve its limits. This acceptance does "
                "not by itself establish predictive skill, a causal mechanism, or "
                "support for the current stage's scientific claim."
            ),
        }
    ]


def test_upstream_producer_context_preserves_acceptance_without_claiming_skill(
    tmp_path: Path,
) -> None:
    store = ResearchReviewStore(tmp_path, "upstream-producer-context")
    data = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content="A historical cycle table was produced.",
        phase="bounded_data",
    )
    claim_id = data["claims"][0]["claim_id"]
    store.submit_verdict(
        mode="data",
        decision="accept_with_limits",
        issues=[],
        accepted_claims=[claim_id],
        carry_forward_limits=["The current cycle was not analyzed."],
    )

    context = json.loads(_upstream_context(store, "hypothesis"))

    assert context[0]["review_decision"] == "accept_with_limits"
    assert context[0]["accepted_claims"] == [claim_id]
    assert context[0]["carry_forward_limits"] == ["The current cycle was not analyzed."]
    assert "Do not describe it as absent, unreviewed" in context[0]["interpretation"]
    assert "does not establish predictive skill" in context[0]["interpretation"]


def test_hypothesis_request_declares_accepted_data_as_verified_material(
    tmp_path: Path,
) -> None:
    store = ResearchReviewStore(tmp_path, "hypothesis-upstream-request")
    (tmp_path / "task.json").write_text(
        json.dumps({"research_question": "极区磁场能否约束下一太阳活动周期强度？"}),
        encoding="utf-8",
    )
    data = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content="已生成周期 15 至 24 的极区磁场前兆表；样本小且相互依赖。",
        phase="bounded_data",
    )
    store.submit_verdict(
        mode="data",
        decision="accept_with_limits",
        issues=[],
        accepted_claims=[data["claims"][0]["claim_id"]],
        carry_forward_limits=["不能把特征表直接解释为预测技能。"],
    )

    relative = _write_hypothesis_request(store)
    request = json.loads((tmp_path / relative).read_text(encoding="utf-8"))

    assert request["research_question"] == "极区磁场能否约束下一太阳活动周期强度？"
    assert len(request["upstream_materials"]) == 1
    material = request["upstream_materials"][0]
    assert material["material_kind"] == "data_feature"
    assert "已生成周期 15 至 24" in material["content_notes"]
    assert "不能把特征表直接解释为预测技能" in material["content_notes"]
    assert "not predictive skill or a causal mechanism" in material["content_notes"]


def test_sc26_backtest_hypothesis_request_is_source_restricted(
    tmp_path: Path,
) -> None:
    """A fixed forecast/backtest must use the accepted result capsule only."""

    store = ResearchReviewStore(tmp_path, "sc26-source-restricted-request")
    (tmp_path / "task.json").write_text(
        json.dumps(
            {
                "research_question": (
                    "对第1至24周做历史回测，然后正式预测第26太阳活动周峰值。"
                )
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    data = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content=(
            "历史回测候选模型 MAE=45.999，训练均值基线 MAE=42.422；"
            "第26周点预测174.994，95%区间65.806至277.656，低置信度。"
        ),
        phase="bounded_data",
    )
    store.submit_verdict(
        mode="data",
        decision="accept_with_limits",
        issues=[],
        accepted_claims=[data["claims"][0]["claim_id"]],
        carry_forward_limits=["候选模型未超过基线，保留负结果。"],
    )

    relative = _write_hypothesis_request(
        store, analysis_protocol="solar_cycle_26_forecast_backtest_v1"
    )
    request = json.loads((tmp_path / relative).read_text(encoding="utf-8"))
    notes = request["upstream_materials"][0]["content_notes"]

    assert "[source_restricted_evidence_boundary]" in notes
    assert "MAE=45.999" in notes
    assert not (
        tmp_path / "work/research_quality/hypothesis_evidence_seed.json"
    ).exists()


def test_hypothesis_request_transports_hash_matched_reviewed_result_excerpt(
    tmp_path: Path,
) -> None:
    """A2A must carry usable accepted facts, not only an unreadable file path."""

    store = ResearchReviewStore(tmp_path, "hypothesis-reviewed-result-capsule")
    (tmp_path / "task.json").write_text(
        json.dumps(
            {
                "research_question": (
                    "SILSO 第1至24周的上升时间与峰值强度是否呈稳定负相关？"
                )
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    report = outputs / "cycle_morphology_strength_report.md"
    report.write_text(
        "\n".join(
            [
                "# SILSO 周形态实验",
                "",
                "## 1. 数据来源",
                "仅使用已注册的 SILSO v2.0 数据。",
                "",
                "## 4. 三组关系、p 值与 bootstrap 区间",
                "| cycle length vs peak strength | 24 | -0.3242 (0.1222) | -0.3139 (0.1353) | [-0.7058, 0.0930] | [-0.6814, 0.1337] | 10000/10000 |",
                "| rise time vs peak strength | 24 | -0.7495 (<0.0001) | -0.7619 (<0.0001) | [-0.8835, -0.5672] | [-0.8866, -0.5297] | 10000/10000 |",
                "| decline time vs peak strength | 24 | 0.3827 (0.0649) | 0.3211 (0.1260) | [0.0551, 0.6415] | [-0.1171, 0.6711] | 10000/10000 |",
                "上升时间—峰值强度：Pearson r=-0.7495，Spearman rho=-0.7619；",
                "两种 95% bootstrap 区间均完全低于 0。",
                "",
                "## 8. 主要结论",
                "历史第1至24周支持 Waldmeier 效应的统计表述，但不证明因果机制。",
                "",
                "## 9. 局限性与不可作出的因果推断",
                "样本量仅24个完整周期，不用于分析或预测第26周。",
            ]
        ),
        encoding="utf-8",
    )
    data = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content=(
            "已生成并核验 outputs/cycle_morphology_strength_report.md；"
            "结果只用于描述历史统计关系。"
        ),
        phase="bounded_data",
    )
    store.submit_verdict(
        mode="data",
        decision="accept_with_limits",
        issues=[],
        accepted_claims=[data["claims"][0]["claim_id"]],
        carry_forward_limits=["不得作太阳发电机因果解释。"],
    )

    relative = _write_hypothesis_request(
        store, analysis_protocol="silso_cycle_morphology_v1"
    )
    request = json.loads((tmp_path / relative).read_text(encoding="utf-8"))
    notes = request["upstream_materials"][0]["content_notes"]

    assert "[source_restricted_evidence_boundary]" in notes
    assert "Evidence-inspected source excerpt" in notes
    assert "Pearson r=-0.7495" in notes
    assert "Spearman rho=-0.7619" in notes
    assert "不用于分析或预测第26周" in notes
    assert "不得作太阳发电机因果解释" in notes

    seed_path = tmp_path / "work/research_quality/hypothesis_evidence_seed.json"
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    assert seed["schema_version"] == "scientific-hypothesis-evidence-seed-v1"
    assert seed["request_sha256"] == canonical_json_sha256(request)
    assert {row["relationship_key"] for row in seed["evidence"]} == {
        "cycle_length_peak",
        "rise_time_peak",
        "decline_time_peak",
    }
    material_ids = {material["id"] for material in request["upstream_materials"]}
    for row in seed["evidence"]:
        evidence = {
            key: value for key, value in row.items() if key != "relationship_key"
        }
        assert evidence["material_id"] in material_ids
        validate_evidence_provenance(request, evidence)


def test_data_pair_mapping_note_keeps_target_cycles_on_pair_right_endpoints() -> None:
    note = _data_pair_mapping_note(
        {
            "payload": {
                "data_result_summary": {
                    "pair_coverage": {
                        "available_pairs": [
                            f"{cycle}->{cycle + 1}" for cycle in range(14, 24)
                        ]
                    }
                }
            }
        }
    )

    assert "14->15 through 23->24" in note
    assert "predictor/previous cycles are 14-23" in note
    assert "target cycles are 15-24" in note
    assert "must not shift this mapping to cycle 25" in note


def test_post_experiment_hypothesis_request_binds_verified_result_and_prior(
    tmp_path: Path,
) -> None:
    store = ResearchReviewStore(tmp_path, "post-experiment-request")
    (tmp_path / "task.json").write_text(
        json.dumps(
            {"research_question": "周期长度是否调节前兆场与下一周期振幅的关系？"}
        ),
        encoding="utf-8",
    )
    for stage, producer, content in (
        ("planning", "solar-planner", "建立逐周期交互分析与证据核验计划。"),
        ("data", "solar-data", "已生成逐周期特征表。"),
        ("hypothesis", "solar-hypothesis", "周期较长时负交互更强。"),
        ("experiment_design", "solar-experiment", "检验交互项和样本外误差。"),
    ):
        store.checkpoint_producer_result(
            stage=stage, producer=producer, content=content
        )
        _accept(store, stage)

    run_root = tmp_path / "experiment" / "runs" / "run-1"
    run_root.mkdir(parents=True)
    record = {
        "schema_version": "automatic-experiment-record-v1",
        "outcome": "high_uncertainty",
        "outcome_reason": "交互项区间覆盖零且留一周期后符号不稳定。",
        "task": "比较有无交互项的周期级预测模型",
        "worker_result": {
            "execution_completed": True,
            "measurements": [
                {
                    "name": "interaction_coefficient",
                    "value": -0.42,
                    "unit": "standardized",
                    "role": "primary",
                    "source_artifact": "summary.json",
                },
                {
                    "name": "interaction_interval_low",
                    "value": -1.10,
                    "unit": "standardized",
                    "role": "secondary",
                    "source_artifact": "summary.json",
                },
                {
                    "name": "interaction_interval_high",
                    "value": 0.31,
                    "unit": "standardized",
                    "role": "secondary",
                    "source_artifact": "summary.json",
                },
            ],
            "result_items": [],
            "scientific_payload": {
                "primary_estimand": "standardized interaction coefficient",
                "estimate": -0.42,
                "interval": [-1.10, 0.31],
                "equivalence_bounds": None,
                "sensitivity": "leave-one-cycle sign instability",
                "uncertainty_reasons": ["Only nine independent cycle pairs."],
            },
        },
        "scientific_assessment": {
            "proposed_outcome": "high_uncertainty",
            "uncertainty_reasons": ["区间覆盖零。", "留一周期后符号不稳定。"],
        },
    }
    (run_root / "record.json").write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8"
    )
    (run_root / "entry_result.json").write_text("{}", encoding="utf-8")
    (run_root / "report.md").write_text("# 真实实验报告\n", encoding="utf-8")
    store.checkpoint_producer_result(
        stage="experiment_result",
        producer="solar-experiment",
        content="experiment/runs/run-1/record.json",
    )
    _accept(store, "experiment_result")

    relative = _write_hypothesis_request(store)
    request = json.loads((tmp_path / relative).read_text(encoding="utf-8"))

    assert len(request["prior_hypotheses"]) == 1
    assert request["prior_hypotheses"][0]["statement"] == "周期较长时负交互更强。"
    experiment = next(
        item
        for item in request["upstream_materials"]
        if item["material_kind"] == "experiment_result"
    )
    assert experiment["experiment_summary"]["outcome"] == "uncertain"
    assert experiment["experiment_summary"]["execution_completed"] is True
    assert experiment["experiment_summary"]["metrics"][0]["name"] == (
        "interaction_coefficient"
    )
    assert "区间覆盖零" in experiment["experiment_summary"]["uncertainty_notes"]


def test_experiment_adapter_projects_verified_measurements_into_claim() -> None:
    source_ref = "experiment/runs/run-1/record.json"
    adapted = adapt_v1_producer_output(
        stage="experiment_result",
        version=1,
        phase="experiment_result",
        text=source_ref,
        evidence_refs=[source_ref],
        canonical_documents=[
            {
                "source_ref": source_ref,
                "payload": {
                    "schema_version": "automatic-experiment-record-v1",
                    "outcome": "high_uncertainty",
                    "outcome_reason": "区间覆盖零。",
                    "task": "通用交互分析",
                    "worker_result": {
                        "execution_completed": True,
                        "measurements": [
                            {
                                "name": "interaction_coefficient",
                                "value": -0.42,
                                "unit": "standardized",
                                "role": "primary",
                                "source_artifact": "summary.json",
                            }
                        ],
                        "result_items": [],
                        "scientific_payload": {
                            "primary_estimand": "interaction coefficient",
                            "estimate": -0.42,
                            "interval": [-1.1, 0.31],
                            "equivalence_bounds": None,
                            "sensitivity": None,
                            "uncertainty_reasons": ["small independent sample"],
                        },
                    },
                    "scientific_assessment": {
                        "proposed_outcome": "high_uncertainty",
                        "uncertainty_reasons": ["区间覆盖零。"],
                    },
                },
            }
        ],
    )

    claim = adapted["claims"][0]
    assert "interaction_coefficient=-0.42 standardized" in claim["text"]
    assert claim["supporting_evidence"] == [source_ref]
    assert adapted["payload"]["experiment_result_summary"]["outcome"] == "uncertain"


@pytest.mark.parametrize("raw_outcome", ["technical_failure", "budget_stopped"])
def test_experiment_adapter_drops_metrics_from_non_scientific_terminal(
    raw_outcome: str,
) -> None:
    source_ref = "experiment/runs/run-failed/record.json"
    adapted = adapt_v1_producer_output(
        stage="experiment_result",
        version=1,
        phase="experiment_result",
        text=source_ref,
        evidence_refs=[source_ref],
        canonical_documents=[
            {
                "source_ref": source_ref,
                "payload": {
                    "schema_version": "automatic-experiment-record-v1",
                    "outcome": raw_outcome,
                    "outcome_reason": "实验记录已终止，未形成合同通过的科学结果。",
                    "task": "通用交互分析",
                    "worker_result": {
                        "execution_completed": True,
                        "measurements": [
                            {
                                "name": "interaction_coefficient",
                                "value": 2.7,
                                "unit": "gauss",
                                "role": "primary",
                                "source_artifact": "summary.json",
                            }
                        ],
                        "result_items": [
                            {
                                "id": "hypothesis_relation",
                                "display_name": "Hypothesis relation",
                                "value": "supports",
                                "role": "primary",
                                "source_artifact": "summary.json",
                            }
                        ],
                    },
                    "scientific_assessment": {
                        "proposed_outcome": raw_outcome,
                        "uncertainty_reasons": ["结果合同未通过。"],
                    },
                },
            }
        ],
    )

    summary = adapted["payload"]["experiment_result_summary"]
    assert summary["execution_completed"] is False
    assert summary["outcome"] == "technical_failure"
    assert summary["metrics"] == []
    assert "Verified results" not in adapted["claims"][0]["text"]
    assert "interaction_coefficient" not in adapted["claims"][0]["text"]


@pytest.mark.parametrize(
    "diagnostic_items",
    [
        [
            ("primary_interval_low", -0.8),
            ("primary_interval_high", 0.2),
            ("out_of_sample_complete", True),
        ],
        [
            ("candidate_mae", 10.0),
            ("baseline_mae", 11.0),
            ("candidate_rmse", 15.0),
            ("baseline_rmse", 14.0),
            ("out_of_sample_complete", True),
        ],
        [
            ("out_of_sample_complete", True),
            ("influential_unit_changes_conclusion", True),
        ],
        [("out_of_sample_complete", False)],
    ],
)
def test_experiment_adapter_recomputes_uncertain_relation_from_diagnostics(
    diagnostic_items: list[tuple[str, object]],
) -> None:
    source_ref = "experiment/runs/run-1/record.json"
    result_items = [
        {
            "id": "hypothesis_relation",
            "display_name": "Hypothesis relation",
            "value_kind": "text",
            "value": "supports",
            "unit": "category",
            "role": "primary",
            "source_artifact": "summary.json",
        },
        *[
            {
                "id": name,
                "display_name": name,
                "value_kind": ("boolean" if isinstance(value, bool) else "number"),
                "value": value,
                "unit": "diagnostic",
                "role": "diagnostic",
                "source_artifact": "summary.json",
            }
            for name, value in diagnostic_items
        ],
    ]
    adapted = adapt_v1_producer_output(
        stage="experiment_result",
        version=1,
        phase="experiment_result",
        text=source_ref,
        evidence_refs=[source_ref],
        canonical_documents=[
            {
                "source_ref": source_ref,
                "payload": {
                    "schema_version": "automatic-experiment-record-v1",
                    "outcome": "completed_interpretable",
                    "outcome_reason": "模型比较已完成。",
                    "task": "通用预测假设检验",
                    "worker_result": {
                        "execution_completed": True,
                        "measurements": [],
                        "result_items": result_items,
                        "scientific_payload": {
                            "primary_estimand": "registered effect",
                            "estimate": 0.1,
                            "interval": None,
                            "equivalence_bounds": None,
                            "sensitivity": None,
                            "uncertainty_reasons": [],
                        },
                    },
                    "scientific_assessment": {
                        "proposed_outcome": "completed_interpretable",
                        "uncertainty_reasons": [],
                    },
                },
            }
        ],
    )

    metrics = {
        item["name"]: item["value_text"]
        for item in adapted["payload"]["experiment_result_summary"]["metrics"]
    }
    assert metrics["hypothesis_relation"] == "uncertain"


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


def test_planning_sample_count_gap_is_carried_to_data_stage(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "planning-sample-count-gap-task")
    artifact = store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content="The planning contract leaves the sample count for the Data stage.",
        phase="planning",
    )
    claim_id = artifact["claims"][0]["claim_id"]
    issue = {
        "issue_id": "sample-count-gap-001",
        "rule_id": "NUMERIC_SOURCE_BOUND",
        "severity": "major",
        "claim_ref": "work/research_quality/planning.analysis_claim.json#independent_sample_count",
        "evidence_refs": ["work/research_quality/planning.analysis_claim.json"],
        "owner": "solar-data",
        "message": (
            "independent_sample_count is intentionally left null at planning stage; "
            "the data stage must bind it from the actual six input receipts."
        ),
        "required_action": "Bind the count from the six Data receipts.",
        "acceptance_test": "Data reports a derived count or an explicit gap row.",
        "fingerprint": issue_fingerprint(
            "NUMERIC_SOURCE_BOUND",
            "work/research_quality/planning.analysis_claim.json#independent_sample_count",
            "solar-data",
        ),
    }

    verdict = store.submit_verdict(
        mode="planning",
        decision="accept_with_limits",
        issues=[issue],
        accepted_claims=[claim_id],
    )

    assert verdict["decision"] == "accept_with_limits"
    assert verdict["issues"] == []
    assert any(
        "independent_sample_count" in limit for limit in verdict["carry_forward_limits"]
    )


def test_planning_sample_count_carry_forward_uses_structured_identity_only() -> None:
    issue = {
        "rule_id": "NUMERIC_SOURCE_BOUND",
        "owner": "solar-data",
        "claim_ref": "work/research_quality/planning.analysis_claim.json#independent_sample_count",
        "message": "A reviewer narrative that is unrelated to the carry-forward rule.",
    }

    unresolved, limits = ResearchReviewStore._carry_planning_data_binding_gaps(
        "planning", [issue]
    )

    assert unresolved == []
    assert limits == [
        "Planning does not bind independent_sample_count; Data must derive it "
        "from accepted task inputs or record an explicit gap row."
    ]
    assert "six" not in limits[0].lower()


def test_planning_sample_count_limit_does_not_depend_on_input_count(
    tmp_path: Path,
) -> None:
    issue = {
        "rule_id": "NUMERIC_SOURCE_BOUND",
        "owner": "solar-data",
        "claim_ref": "work/research_quality/planning.analysis_claim.json#independent_sample_count",
        "message": "",
    }

    for input_count in (None, 1, 4, 6):
        workspace = tmp_path / f"input-count-{input_count}"
        workspace.mkdir()
        task_id = f"input-count-{input_count}"
        if input_count is not None:
            input_rows = []
            for index in range(input_count):
                input_path = workspace / "inputs" / f"input-{index}.csv"
                input_path.parent.mkdir(parents=True, exist_ok=True)
                input_path.write_text(f"value\n{index}\n", encoding="utf-8")
                raw = input_path.read_bytes()
                input_rows.append(
                    {
                        "path": input_path.relative_to(workspace).as_posix(),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "bytes": len(raw),
                        "role": "user_input",
                    }
                )
            (workspace / "task.json").write_text(
                json.dumps({"thread_id": task_id, "research_question": "question"}),
                encoding="utf-8",
            )
            (workspace / "input_manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": "task-input-manifest-v1",
                        "thread_id": task_id,
                        "inputs": input_rows,
                        "project_inputs": [],
                    }
                ),
                encoding="utf-8",
            )
        store = ResearchReviewStore(workspace, task_id)
        unresolved, limits = store._carry_planning_data_binding_gaps(
            "planning", [issue]
        )
        assert unresolved == [], input_count
        assert limits == [
            "Planning does not bind independent_sample_count; Data must derive it "
            "from accepted task inputs or record an explicit gap row."
        ], input_count


def test_other_planning_numeric_source_gap_remains_actionable(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "planning-other-numeric-gap-task")
    artifact = store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content="The planning contract includes a date that needs a source.",
        phase="planning",
    )
    issue = {
        "issue_id": "date-gap-001",
        "rule_id": "NUMERIC_SOURCE_BOUND",
        "severity": "major",
        "claim_ref": "planning-plan-v1#scope.population_or_period",
        "evidence_refs": [],
        "owner": "solar-planner",
        "message": "A date is asserted without a source.",
        "required_action": "Bind the date to an inspected source.",
        "acceptance_test": "The plan points to the source for the date.",
        "fingerprint": issue_fingerprint(
            "NUMERIC_SOURCE_BOUND",
            "planning-plan-v1#scope.population_or_period",
            "solar-planner",
        ),
    }

    verdict = store.submit_verdict(
        mode="planning",
        decision="accept_with_limits",
        issues=[issue],
        accepted_claims=[artifact["claims"][0]["claim_id"]],
    )

    assert verdict["decision"] == "revise"
    assert verdict["next_owner"] == "solar-planner"
    assert verdict["issues"][0]["rule_id"] == "NUMERIC_SOURCE_BOUND"


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


def _write_authoritative_data_context(
    root: Path,
    task_id: str,
    *,
    missing_required_dataset_ids: list[str],
    must_stop: bool,
) -> Path:
    question = (
        "Can polar fields in cycles 14-23 predict amplitudes for "
        "the following cycles 15-24?"
    )
    task_path = root / "task.json"
    task_path.write_text(
        json.dumps({"thread_id": task_id, "research_question": question}),
        encoding="utf-8",
    )
    manifest_path = root / "input_manifest.json"
    required_dataset_ids = [
        "silso-monthly-total-v2",
        "mwo-wso-polar-field-v2",
    ]
    input_rows: list[dict[str, object]] = []
    for index, dataset_id in enumerate(required_dataset_ids, start=1):
        if dataset_id in missing_required_dataset_ids:
            continue
        input_path = root / "inputs" / f"dataset-{index}.csv"
        input_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.write_text(f"dataset_id,value\n{dataset_id},1\n", encoding="utf-8")
        raw = input_path.read_bytes()
        input_rows.append(
            {
                "path": input_path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
                "role": "user_input",
                "source_group": "inputs",
                "dataset_id": dataset_id,
            }
        )
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "task-input-manifest-v1",
                "thread_id": task_id,
                "inputs": input_rows,
                "project_inputs": [],
            }
        ),
        encoding="utf-8",
    )
    plan_ref = "planner/runs/current/research_plan.json"
    plan_path = root / plan_ref
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    route_steps = [
        {
            "id": "R1",
            "stage": "data",
            "objective": "Build cycle pairs 14->15 through 23->24.",
        },
        {
            "id": "R2",
            "stage": "hypothesis",
            "objective": "Generate a testable hypothesis.",
        },
        {
            "id": "R3",
            "stage": "experiment_design",
            "objective": "Specify the experiment.",
        },
        {"id": "R4", "stage": "experiment_result", "objective": "Run the experiment."},
        {
            "id": "R5",
            "stage": "hypothesis_update",
            "objective": "Update the hypothesis.",
        },
    ]
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": "research-plan-v1",
                "research_question": question,
                "research_route": route_steps,
                "required_datasets": [
                    {
                        "id": dataset_id,
                        "selected_source_id": dataset_id,
                        "purpose": "Authoritative test input.",
                    }
                    for dataset_id in required_dataset_ids
                ],
            }
        ),
        encoding="utf-8",
    )
    data_steps = [step for step in route_steps if step["stage"] == "data"]
    store = ResearchReviewStore(root, task_id)
    planning = store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content="Accepted canonical planning route.",
    )
    planning_verdict = store.submit_verdict(
        mode="planning",
        decision="accept",
        issues=[],
        accepted_claims=[claim["claim_id"] for claim in planning["claims"]],
    )
    body = {
        "schema_version": "solar-data-context-v1",
        "context_mode": "full_research",
        "task_id": task_id,
        "analysis_protocol": "solar_polar_precursor_v1",
        "required_data_product": "solar_polar_precursor_table_v1",
        "required_dataset_ids": required_dataset_ids,
        "missing_required_dataset_ids": missing_required_dataset_ids,
        "eligible_inputs": input_rows,
        "status": (
            "input_missing"
            if missing_required_dataset_ids or must_stop
            else "inputs_available"
        ),
        "must_stop": must_stop,
        "task_sha256": hashlib.sha256(task_path.read_bytes()).hexdigest(),
        "research_question_sha256": hashlib.sha256(
            question.encode("utf-8")
        ).hexdigest(),
        "input_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "plan_source_ref": plan_ref,
        "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        "planning_artifact_ref": store.artifact_ref(planning),
        "planning_verdict_ref": {
            "review_id": planning_verdict["review_id"],
            "verdict_sha256": planning_verdict["verdict_sha256"],
        },
        "data_steps": data_steps,
    }
    context_sha256 = canonical_json_sha256(body)
    receipt = (
        root / "receipts" / "datasets" / f"data-context-{context_sha256[:16]}.json"
    )
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps({**body, "context_sha256": context_sha256}), encoding="utf-8"
    )
    return receipt


def _write_strict_full_data_context(
    root: Path, task_id: str
) -> tuple[Path, ResearchReviewStore, dict[str, object], dict[str, object]]:
    """Create one accepted-Planning/full-Data context for authority regressions."""

    from jw.research_protocols import (
        SOLAR_POLAR_PRECURSOR_PROTOCOL,
        required_data_product_for_protocol,
        required_dataset_ids_for_protocol,
    )

    question = (
        "Can polar fields predict the next solar cycle amplitude from the "
        "following cycles?"
    )
    task_path = root / "task.json"
    task_path.write_text(
        json.dumps({"thread_id": task_id, "research_question": question}),
        encoding="utf-8",
    )
    input_rows: list[dict[str, object]] = []
    dataset_ids = required_dataset_ids_for_protocol(SOLAR_POLAR_PRECURSOR_PROTOCOL)
    for index, dataset_id in enumerate(dataset_ids, start=1):
        relative = f"inputs/dataset-{index}.csv"
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"dataset_id,value\n{dataset_id},1\n", encoding="utf-8")
        raw = path.read_bytes()
        input_rows.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
                "role": "user_input",
                "source_group": "inputs",
                "dataset_id": dataset_id,
            }
        )
    manifest_path = root / "input_manifest.json"
    manifest = {
        "schema_version": "task-input-manifest-v1",
        "thread_id": task_id,
        "inputs": input_rows,
        "project_inputs": [],
    }
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    plan_ref = "planner/runs/current/research_plan.json"
    plan_path = root / plan_ref
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    data_steps = [
        {
            "id": "R1",
            "stage": "data",
            "objective": "Construct the task-bound precursor table.",
        },
        {"id": "R2", "stage": "hypothesis_generation", "objective": "Form hypotheses."},
        {"id": "R3", "stage": "experiment_design", "objective": "Design the test."},
        {"id": "R4", "stage": "experiment_result", "objective": "Run the test."},
        {"id": "R5", "stage": "hypothesis_update", "objective": "Update hypotheses."},
    ]
    plan = {
        "schema_version": "research-plan-v1",
        "research_question": question,
        "research_route": data_steps,
        "required_datasets": [],
    }
    plan_path.write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")

    store = ResearchReviewStore(root, task_id)
    planning = store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content="Accepted task-bound planning artifact.",
    )
    planning_verdict = store.submit_verdict(
        mode="planning",
        decision="accept",
        issues=[],
        accepted_claims=[planning["claims"][0]["claim_id"]],
    )
    protocol = SOLAR_POLAR_PRECURSOR_PROTOCOL
    required_ids = list(dataset_ids)
    body: dict[str, object] = {
        "schema_version": "solar-data-context-v1",
        "context_mode": "full_research",
        "task_id": task_id,
        "analysis_protocol": protocol,
        "required_data_product": required_data_product_for_protocol(protocol),
        "planning_artifact_ref": store.artifact_ref(planning),
        "planning_verdict_ref": {
            "review_id": planning_verdict["review_id"],
            "verdict_sha256": planning_verdict["verdict_sha256"],
        },
        "plan_source_ref": plan_ref,
        "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        "task_sha256": hashlib.sha256(task_path.read_bytes()).hexdigest(),
        "research_question_sha256": hashlib.sha256(question.encode()).hexdigest(),
        "input_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "required_datasets": [],
        "required_dataset_ids": required_ids,
        "missing_required_dataset_ids": [],
        "data_steps": [data_steps[0]],
        "planned_outputs": [],
        "eligible_inputs": input_rows,
        "status": "inputs_available",
        "must_stop": False,
    }
    context_sha256 = canonical_json_sha256(body)
    receipt = (
        root / "receipts" / "datasets" / f"data-context-{context_sha256[:16]}.json"
    )
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                **body,
                "context_sha256": context_sha256,
                "created_at": "2026-01-01T00:00:00Z",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return receipt, store, planning, planning_verdict


def test_full_context_rebuilds_eligible_inputs_from_current_manifest(
    tmp_path: Path,
) -> None:
    task_id = "manifest-authority-regression"
    receipt, store, _planning, _verdict = _write_strict_full_data_context(
        tmp_path, task_id
    )
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    ref = receipt.relative_to(tmp_path).as_posix()
    assert store._data_context_is_authoritative(ref, payload, phase="data")

    forged = dict(payload)
    forged["eligible_inputs"] = [dict(item) for item in payload["eligible_inputs"]]
    forged["eligible_inputs"][0]["sha256"] = "f" * 64
    forged_body = {
        key: value
        for key, value in forged.items()
        if key not in {"context_sha256", "created_at"}
    }
    forged_sha256 = canonical_json_sha256(forged_body)
    forged_ref = f"receipts/datasets/data-context-{forged_sha256[:16]}.json"
    forged_path = tmp_path / forged_ref
    forged_path.write_text(
        json.dumps({**forged_body, "context_sha256": forged_sha256}, sort_keys=True),
        encoding="utf-8",
    )

    assert not store._data_context_is_authoritative(
        forged_ref,
        json.loads(forged_path.read_text(encoding="utf-8")),
        phase="data",
    )


def test_data_review_uses_authoritative_context_when_stale_receipt_remains(
    tmp_path: Path,
) -> None:
    task_id = "authoritative-context-selection"
    context_path, store, _planning, _verdict = _write_strict_full_data_context(
        tmp_path, task_id
    )
    context = json.loads(context_path.read_text(encoding="utf-8"))

    output_ref = "work/solar_data/solar_precursor_cycle_features.csv"
    output = tmp_path / output_ref
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_complete_boundary_cycle_table(output, include_uncertainty=True)
    requested_pairs = [f"{cycle}->{cycle + 1}" for cycle in range(14, 24)]
    receipt = tmp_path / "receipts/datasets/solar_precursor_cycle_table.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "solar-precursor-cycle-table-v2",
                "receipt_type": "solar_precursor_cycle_table",
                "status": "verified",
                "producer": "solar-data",
                "task_id": task_id,
                "row_count": 11,
                "cycle_numbers": list(range(14, 25)),
                "analysis_cycle_numbers": list(range(15, 25)),
                "boundary_cycle_numbers": [14],
                "pair_coverage": {
                    "requested_pairs": requested_pairs,
                    "available_pairs": requested_pairs,
                    "unavailable_pairs": [],
                },
                "input_refs": context["eligible_inputs"],
                "outputs": [
                    {
                        "path": output_ref,
                        "bytes": output.stat().st_size,
                        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    current_ref = context_path.relative_to(tmp_path).as_posix()
    current_name = context_path.name
    stale_body = {
        key: value
        for key, value in context.items()
        if key not in {"context_sha256", "created_at"}
    }
    stale_body.update(
        {
            "eligible_inputs": [],
            "missing_required_dataset_ids": context["required_dataset_ids"],
            "status": "input_missing",
            "must_stop": True,
            "input_manifest_sha256": "0" * 64,
        }
    )
    for nonce in range(1000):
        stale_body["planned_outputs"] = [{"nonce": nonce}]
        stale_sha256 = canonical_json_sha256(stale_body)
        stale_name = f"data-context-{stale_sha256[:16]}.json"
        if stale_name > current_name:
            break
    else:
        raise AssertionError("could not construct a later stale context receipt")
    stale = context_path.with_name(stale_name)
    stale.write_text(
        json.dumps({**stale_body, "context_sha256": stale_sha256}),
        encoding="utf-8",
    )

    artifact = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content=f"{current_ref}; {receipt.relative_to(tmp_path).as_posix()}",
        phase="data",
        require_canonical_source=True,
    )

    assert store._deterministic_semantic_issues("data", [artifact]) == []


def test_full_context_rebuilds_registered_project_inputs_from_virtual_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review authority must resolve the same /project/data paths as the tools."""

    registry = tmp_path / "bindings"
    base = tmp_path / "workspace"
    source = tmp_path / "solar.csv"
    source.write_text("cycle,value\n24,115\n", encoding="utf-8")
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(registry))

    register_project_data_file(
        base,
        source,
        "solar/precursor.csv",
        dataset_id="solar-precursor-v1",
        provenance={"source_url": "https://example.test/solar"},
    )
    binding = ensure_thread_workspace(
        "project-input-authority", base, project_id="default"
    )
    store = ResearchReviewStore(Path(binding.workspace), binding.thread_id)
    path_type = type(Path())
    original_is_absolute = path_type.is_absolute

    def windows_like_is_absolute(path: Path) -> bool:
        if path.as_posix().startswith("/project/data/"):
            return False
        return original_is_absolute(path)

    monkeypatch.setattr(path_type, "is_absolute", windows_like_is_absolute)

    # Production WebUI manifests also retain an uploaded workspace copy of
    # the same registered file using ``size`` instead of ``bytes``.  The full
    # context must not reject that harmless alias.
    manifest_path = Path(binding.workspace) / "input_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["inputs"] = [
        {
            "name": "precursor.csv",
            "path": "inputs/precursor.csv",
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "size": source.stat().st_size,
        }
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    records = store._current_manifest_input_records()

    assert records == [
        {
            "path": "/project/data/solar/precursor.csv",
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "bytes": source.stat().st_size,
            "role": "primary_data",
            "source_group": "project_inputs",
            "dataset_id": "solar-precursor-v1",
            "provenance_ref": "/project/data/solar/precursor.csv.provenance.json",
        }
    ]


def test_full_context_rejects_unregistered_project_input_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shared file must also be present in the project data registry."""

    registry = tmp_path / "bindings"
    base = tmp_path / "workspace"
    source = tmp_path / "registered.csv"
    source.write_text("cycle,value\n24,115\n", encoding="utf-8")
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(registry))
    register_project_data_file(
        base,
        source,
        "solar/registered.csv",
        dataset_id="registered",
        provenance={},
    )
    binding = ensure_thread_workspace(
        "unregistered-project-input", base, project_id="default"
    )

    unregistered = Path(binding.project_shared) / "data/solar/unregistered.csv"
    unregistered.write_text("cycle,value\n25,120\n", encoding="utf-8")
    manifest_path = Path(binding.workspace) / "input_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw = unregistered.read_bytes()
    manifest["project_inputs"].append(
        {
            "path": "/project/data/solar/unregistered.csv",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "role": "primary_data",
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    store = ResearchReviewStore(Path(binding.workspace), binding.thread_id)

    assert store._current_manifest_input_records() is None


def test_full_context_binds_current_accepted_planning_artifact_and_verdict(
    tmp_path: Path,
) -> None:
    task_id = "planning-binding-regression"
    receipt, store, planning, verdict = _write_strict_full_data_context(
        tmp_path, task_id
    )
    payload = json.loads(receipt.read_text(encoding="utf-8"))

    forged = dict(payload)
    forged["planning_artifact_ref"] = {
        **store.artifact_ref(planning),
        "artifact_sha256": "0" * 64,
    }
    forged["planning_verdict_ref"] = {
        "review_id": verdict["review_id"],
        "verdict_sha256": "0" * 64,
    }
    forged_body = {
        key: value
        for key, value in forged.items()
        if key not in {"context_sha256", "created_at"}
    }
    forged_sha256 = canonical_json_sha256(forged_body)
    forged_ref = f"receipts/datasets/data-context-{forged_sha256[:16]}.json"
    forged_path = tmp_path / forged_ref
    forged_path.write_text(
        json.dumps({**forged_body, "context_sha256": forged_sha256}, sort_keys=True),
        encoding="utf-8",
    )

    assert not store._data_context_is_authoritative(
        forged_ref,
        json.loads(forged_path.read_text(encoding="utf-8")),
        phase="data",
    )


@pytest.mark.parametrize("field", ["path", "bytes", "dataset_id"])
def test_full_context_rejects_manifest_record_field_forgery(
    tmp_path: Path, field: str
) -> None:
    task_id = f"manifest-field-{field}"
    receipt, store, _planning, _verdict = _write_strict_full_data_context(
        tmp_path, task_id
    )
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    forged = dict(payload)
    forged["eligible_inputs"] = [dict(item) for item in payload["eligible_inputs"]]
    if field == "path":
        forged["eligible_inputs"][0][field] = "inputs/foreign.csv"
    elif field == "bytes":
        forged["eligible_inputs"][0][field] += 1
    else:
        forged["eligible_inputs"][0][field] = "forged-dataset"
    forged_body = {
        key: value
        for key, value in forged.items()
        if key not in {"context_sha256", "created_at"}
    }
    forged_sha256 = canonical_json_sha256(forged_body)
    forged_ref = f"receipts/datasets/data-context-{forged_sha256[:16]}.json"
    forged_path = tmp_path / forged_ref
    forged_path.write_text(
        json.dumps({**forged_body, "context_sha256": forged_sha256}, sort_keys=True),
        encoding="utf-8",
    )

    assert not store._data_context_is_authoritative(
        forged_ref,
        json.loads(forged_path.read_text(encoding="utf-8")),
        phase="data",
    )


def test_full_context_plan_protocol_conflict_is_planning_owned_revise(
    tmp_path: Path,
) -> None:
    from jw.research_protocols import (
        SOLAR_POLAR_PRECURSOR_PROTOCOL,
        required_data_product_for_protocol,
    )

    task_id = "planning-protocol-conflict"
    question = (
        "Can polar fields predict the next solar cycle amplitude from the "
        "following cycles?"
    )
    task_path = tmp_path / "task.json"
    task_path.write_text(
        json.dumps({"thread_id": task_id, "research_question": question}),
        encoding="utf-8",
    )
    input_path = tmp_path / "inputs" / "dataset-1.csv"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_text("dataset_id,value\nforeign-dataset-v1,1\n", encoding="utf-8")
    raw_input = input_path.read_bytes()
    input_rows = [
        {
            "path": "inputs/dataset-1.csv",
            "sha256": hashlib.sha256(raw_input).hexdigest(),
            "bytes": len(raw_input),
            "role": "user_input",
            "source_group": "inputs",
            "dataset_id": "foreign-dataset-v1",
        }
    ]
    manifest_path = tmp_path / "input_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "task-input-manifest-v1",
                "thread_id": task_id,
                "inputs": input_rows,
                "project_inputs": [],
            }
        ),
        encoding="utf-8",
    )
    plan_ref = "planner/runs/current/research_plan.json"
    plan_path = tmp_path / plan_ref
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan = {
        "schema_version": "research-plan-v1",
        "research_question": question,
        "research_route": [
            {
                "id": "R1",
                "stage": "data",
                "objective": "Construct the task-bound precursor table.",
            },
            {
                "id": "R2",
                "stage": "hypothesis",
                "objective": "Generate a testable hypothesis.",
            },
            {
                "id": "R3",
                "stage": "experiment_design",
                "objective": "Specify the experiment.",
            },
            {
                "id": "R4",
                "stage": "experiment_result",
                "objective": "Run the experiment.",
            },
            {
                "id": "R5",
                "stage": "hypothesis_update",
                "objective": "Update the hypothesis.",
            },
        ],
        "required_datasets": [
            {
                "id": "D1",
                "selected_source_id": "foreign-dataset-v1",
                "purpose": "Conflict with the active protocol mapping.",
            }
        ],
    }
    plan_path.write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")

    store = ResearchReviewStore(tmp_path, task_id)
    planning = store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content="Accepted task-bound planning artifact.",
    )
    planning_verdict = store.submit_verdict(
        mode="planning",
        decision="accept",
        issues=[],
        accepted_claims=[planning["claims"][0]["claim_id"]],
    )
    body: dict[str, object] = {
        "schema_version": "solar-data-context-v1",
        "context_mode": "full_research",
        "task_id": task_id,
        "analysis_protocol": SOLAR_POLAR_PRECURSOR_PROTOCOL,
        "required_data_product": required_data_product_for_protocol(
            SOLAR_POLAR_PRECURSOR_PROTOCOL
        ),
        "planning_artifact_ref": store.artifact_ref(planning),
        "planning_verdict_ref": {
            "review_id": planning_verdict["review_id"],
            "verdict_sha256": planning_verdict["verdict_sha256"],
        },
        "plan_source_ref": plan_ref,
        "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        "task_sha256": hashlib.sha256(task_path.read_bytes()).hexdigest(),
        "research_question_sha256": hashlib.sha256(question.encode()).hexdigest(),
        "input_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "required_datasets": plan["required_datasets"],
        "required_dataset_ids": ["foreign-dataset-v1"],
        "missing_required_dataset_ids": [],
        "data_steps": [plan["research_route"][0]],
        "planned_outputs": [],
        "eligible_inputs": input_rows,
        "status": "inputs_available",
        "must_stop": False,
    }
    context_sha256 = canonical_json_sha256(body)
    receipt = (
        tmp_path / "receipts" / "datasets" / f"data-context-{context_sha256[:16]}.json"
    )
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                **body,
                "context_sha256": context_sha256,
                "created_at": "2026-08-17T00:00:00Z",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content="Conflict data checkpoint.",
        phase="data",
    )

    verdict = store.persist_deterministic_preflight_verdict("data")

    assert verdict is not None
    assert verdict["decision"] == "revise"
    assert verdict["issues"][0]["owner"] == "solar-planner"
    assert verdict["issues"][0]["rule_id"] == "PLAN_DATASET_PROTOCOL_CONFLICT"
    assert store.persist_deterministic_preflight_verdict("data") is None
    assert store.load_state()["stage_status"]["data"] == "revise"


def test_data_checkpoint_does_not_treat_task_input_or_harness_trace_as_output(
    tmp_path: Path,
) -> None:
    task_id = "data-output-boundary"
    question = "A bounded Data request"
    task_path = tmp_path / "task.json"
    task_path.write_text(
        json.dumps({"thread_id": task_id, "research_question": question}),
        encoding="utf-8",
    )
    input_path = tmp_path / "inputs" / "user.csv"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_text("value\n1\n", encoding="utf-8")
    raw_input = input_path.read_bytes()
    manifest_path = tmp_path / "input_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "task-input-manifest-v1",
                "thread_id": task_id,
                "inputs": [
                    {
                        "path": "inputs/user.csv",
                        "sha256": hashlib.sha256(raw_input).hexdigest(),
                        "bytes": len(raw_input),
                        "role": "user_input",
                    }
                ],
                "project_inputs": [],
            }
        ),
        encoding="utf-8",
    )
    context_body = {
        "schema_version": "solar-data-context-v1",
        "context_mode": "bounded_data",
        "task_id": task_id,
        "analysis_protocol": "none",
        "required_data_product": "generic_data_product_v1",
        "task_sha256": hashlib.sha256(task_path.read_bytes()).hexdigest(),
        "research_question_sha256": hashlib.sha256(question.encode()).hexdigest(),
        "input_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "required_dataset_ids": [],
        "missing_required_dataset_ids": [],
        "eligible_inputs": [],
        "status": "inputs_available",
        "must_stop": False,
        "data_steps": [],
    }
    context_sha256 = canonical_json_sha256(context_body)
    context_path = (
        tmp_path / "receipts" / "datasets" / f"data-context-{context_sha256[:16]}.json"
    )
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text(
        json.dumps({**context_body, "context_sha256": context_sha256}),
        encoding="utf-8",
    )
    trace = tmp_path / "research_review" / "harness" / task_id / "run" / "trace.json"
    trace.parent.mkdir(parents=True, exist_ok=True)
    trace.write_text("{}", encoding="utf-8")

    store = ResearchReviewStore(tmp_path, task_id)
    with pytest.raises(RuntimeError, match="canonical|artifact|Data"):
        store.checkpoint_producer_result(
            stage="data",
            producer="solar-data",
            content="Only the task input and Harness trace are present.",
            phase="bounded_data",
            require_canonical_source=True,
        )


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [
        ("analysis_protocol", "silso_cycle_reproduction_v1"),
        ("required_data_product", "silso_cycle_extrema_v1"),
        ("required_dataset_ids", ["forged-dataset-v1"]),
    ],
)
def test_full_research_context_semantics_are_recomputed_from_plan_and_protocol(
    tmp_path: Path,
    field: str,
    forged_value: object,
) -> None:
    task_id = "forged-data-context-semantics"
    original = _write_authoritative_data_context(
        tmp_path,
        task_id,
        missing_required_dataset_ids=[],
        must_stop=False,
    )
    store = ResearchReviewStore(tmp_path, task_id)
    original_payload = json.loads(original.read_text(encoding="utf-8"))
    assert store._data_context_is_authoritative(
        original.relative_to(tmp_path).as_posix(), original_payload, phase="data"
    )

    forged_body = {
        key: value for key, value in original_payload.items() if key != "context_sha256"
    }
    forged_body[field] = forged_value
    forged_sha256 = canonical_json_sha256(forged_body)
    forged = (
        tmp_path / "receipts" / "datasets" / f"data-context-{forged_sha256[:16]}.json"
    )
    forged.write_text(
        json.dumps({**forged_body, "context_sha256": forged_sha256}),
        encoding="utf-8",
    )

    assert not store._data_context_is_authoritative(
        forged.relative_to(tmp_path).as_posix(),
        json.loads(forged.read_text(encoding="utf-8")),
        phase="data",
    )


def _required_data_unavailable_issue(claim_ref: str) -> dict[str, object]:
    rule_id = "REQUIRED_DATA_INPUT_UNAVAILABLE"
    owner = "main"
    return {
        "issue_id": "reviewer-required-input-unavailable",
        "rule_id": rule_id,
        "severity": "critical",
        "claim_ref": claim_ref,
        "evidence_refs": [],
        "owner": owner,
        "message": "Cycles 25 and 26 were not supplied.",
        "required_action": "Supply cycles outside the accepted 15-24 target scope.",
        "acceptance_test": "Cycles 25 and 26 are present.",
        "fingerprint": issue_fingerprint(rule_id, claim_ref, owner),
    }


def test_reviewer_cannot_turn_available_data_context_into_permanent_block(
    tmp_path: Path,
) -> None:
    task_id = "reviewer-data-scope-recovery"
    store = ResearchReviewStore(tmp_path, task_id)
    planning = store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content=(
            "Accepted scope predicts cycles 15-24 from predictor cycles 14-23; "
            "cycle 25 is outside scope."
        ),
    )
    store.submit_verdict(
        mode="planning",
        decision="accept",
        issues=[],
        accepted_claims=[planning["claims"][0]["claim_id"]],
    )
    context = _write_authoritative_data_context(
        tmp_path,
        task_id,
        missing_required_dataset_ids=[],
        must_stop=False,
    )
    artifact = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content=f"Let me inspect things first. {context.relative_to(tmp_path).as_posix()}",
        phase="bounded_data",
    )

    verdict = store.submit_verdict(
        mode="data",
        decision="block",
        issues=[_required_data_unavailable_issue(artifact["claims"][0]["claim_id"])],
        blocked_claims=[artifact["claims"][0]["claim_id"]],
    )

    assert verdict["decision"] == "revise"
    assert verdict["next_owner"] == "solar-data"
    assert verdict["issues"][0]["rule_id"] == "DATA_SEMANTICS_BOUND"
    assert verdict["issues"][0]["owner"] == "solar-data"
    assert verdict["issues"][0]["fingerprint"] == issue_fingerprint(
        "DATA_SEMANTICS_BOUND",
        verdict["issues"][0]["claim_ref"],
        "solar-data",
    )
    assert store.load_state()["status"] == "active"
    action = store.next_action()
    assert action["kind"] == "producer"
    assert action["stage"] == "data"
    assert action["producer"] == "solar-data"
    assert "revision" in action["phase"]


def test_recovered_data_input_issue_clears_acceptance_and_blocked_claim_sets(
    tmp_path: Path,
) -> None:
    task_id = "reviewer-data-claim-set-recovery"
    context = _write_authoritative_data_context(
        tmp_path,
        task_id,
        missing_required_dataset_ids=[],
        must_stop=False,
    )
    store = ResearchReviewStore(tmp_path, task_id)
    artifact = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content=context.relative_to(tmp_path).as_posix(),
        phase="bounded_data",
    )
    claim_id = artifact["claims"][0]["claim_id"]

    verdict = store.submit_verdict(
        mode="data",
        decision="accept",
        issues=[_required_data_unavailable_issue(claim_id)],
        accepted_claims=[claim_id],
    )

    assert verdict["decision"] == "revise"
    assert verdict["accepted_claims"] == []
    assert verdict["blocked_claims"] == []
    capsule = store.revision_capsule(verdict["review_id"], "solar-data")
    assert claim_id not in capsule["do_not_reopen_claims"]


def test_recovered_data_input_issue_does_not_downgrade_another_real_block(
    tmp_path: Path,
) -> None:
    task_id = "reviewer-data-mixed-block"
    context = _write_authoritative_data_context(
        tmp_path,
        task_id,
        missing_required_dataset_ids=[],
        must_stop=False,
    )
    store = ResearchReviewStore(tmp_path, task_id)
    artifact = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content=context.relative_to(tmp_path).as_posix(),
        phase="bounded_data",
    )
    claim_id = artifact["claims"][0]["claim_id"]
    real_issue = _issue("solar-data")
    real_issue["claim_ref"] = claim_id
    real_issue["fingerprint"] = issue_fingerprint(
        str(real_issue["rule_id"]), claim_id, "solar-data"
    )

    verdict = store.submit_verdict(
        mode="data",
        decision="block",
        issues=[_required_data_unavailable_issue(claim_id), real_issue],
        blocked_claims=[claim_id],
    )

    assert verdict["decision"] == "block"
    assert verdict["next_owner"] is None
    assert {issue["rule_id"] for issue in verdict["issues"]} >= {
        "DATA_SEMANTICS_BOUND",
        "UNSUPPORTED_CLAIM",
    }


@pytest.mark.parametrize(
    ("missing_required_dataset_ids", "must_stop"),
    [
        (["mwo-wso-polar-field-v2"], True),
        (["silso-monthly-total-v2", "mwo-wso-polar-field-v2"], True),
    ],
)
def test_authoritative_missing_or_must_stop_remains_permanent_data_block(
    tmp_path: Path,
    missing_required_dataset_ids: list[str],
    must_stop: bool,
) -> None:
    task_id = "authoritative-data-stop"
    context = _write_authoritative_data_context(
        tmp_path,
        task_id,
        missing_required_dataset_ids=missing_required_dataset_ids,
        must_stop=must_stop,
    )
    store = ResearchReviewStore(tmp_path, task_id)
    artifact = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content=context.relative_to(tmp_path).as_posix(),
        phase="bounded_data",
    )

    verdict = store.submit_verdict(
        mode="data",
        decision="block",
        issues=[_required_data_unavailable_issue(artifact["claims"][0]["claim_id"])],
        blocked_claims=[artifact["claims"][0]["claim_id"]],
    )

    assert verdict["decision"] == "block"
    assert verdict["next_owner"] is None
    assert store.load_state()["status"] == "blocked"


@pytest.mark.parametrize("include_forged_context", [False, True])
def test_non_authoritative_or_missing_context_cannot_create_permanent_input_block(
    tmp_path: Path, include_forged_context: bool
) -> None:
    task_id = f"non-authoritative-context-{include_forged_context}"
    content = "Data output needs revision."
    if include_forged_context:
        context = tmp_path / "receipts/datasets/data-context-forged.json"
        context.parent.mkdir(parents=True, exist_ok=True)
        context.write_text(
            json.dumps(
                {
                    "schema_version": "solar-data-context-v1",
                    "context_mode": "full_research",
                    "task_id": task_id,
                    "status": "input_missing",
                    "must_stop": True,
                    "required_dataset_ids": ["required-solar-v1"],
                    "missing_required_dataset_ids": ["required-solar-v1"],
                    "eligible_inputs": [],
                    "context_sha256": "0" * 64,
                    "task_sha256": "1" * 64,
                    "research_question_sha256": "2" * 64,
                    "input_manifest_sha256": "3" * 64,
                }
            ),
            encoding="utf-8",
        )
        content = context.relative_to(tmp_path).as_posix()
    store = ResearchReviewStore(tmp_path, task_id)
    artifact = store.checkpoint_producer_result(
        stage="data", producer="solar-data", content=content, phase="bounded_data"
    )

    verdict = store.submit_verdict(
        mode="data",
        decision="block",
        issues=[_required_data_unavailable_issue(artifact["claims"][0]["claim_id"])],
        blocked_claims=[artifact["claims"][0]["claim_id"]],
    )

    assert verdict["decision"] == "revise"
    assert verdict["issues"][0]["rule_id"] == "DATA_SEMANTICS_BOUND"
    assert store.load_state()["status"] == "active"


def _write_curated_data_context(tmp_path: Path, task_id: str) -> Path:
    receipt = tmp_path / "receipts" / "datasets" / "data-context-curated.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "solar-data-context-v1",
                "task_id": task_id,
                "status": "inputs_available",
                "required_data_product": "solar_polar_precursor_table_v1",
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


def _write_silso_reproduction_artifacts(
    tmp_path: Path, task_id: str, *, include_precursor_receipt: bool = False
) -> list[str]:
    hashes = {
        "silso-monthly-total-v2": "a" * 64,
        "silso-monthly-smoothed-v2": "b" * 64,
        "silso-cycle-extrema-v2": "c" * 64,
        "mwo-wso-polar-field-v2": "d" * 64,
    }
    context_ref = "receipts/datasets/data-context-silso.json"
    context = tmp_path / context_ref
    context.parent.mkdir(parents=True, exist_ok=True)
    context.write_text(
        json.dumps(
            {
                "schema_version": "solar-data-context-v1",
                "task_id": task_id,
                "status": "inputs_available",
                "analysis_protocol": "silso_cycle_reproduction_v1",
                "required_data_product": "silso_cycle_extrema_v1",
                "eligible_inputs": [
                    {
                        "dataset_id": dataset_id,
                        "path": f"/project/{dataset_id}",
                        "sha256": sha256,
                    }
                    for dataset_id, sha256 in hashes.items()
                ],
            }
        ),
        encoding="utf-8",
    )
    official = {
        21: (1976, 3, 17.8, 1979, 12, 232.9, 45),
        22: (1986, 9, 13.5, 1989, 11, 212.5, 38),
        23: (1996, 8, 11.2, 2001, 11, 180.3, 63),
        24: (2008, 12, 2.2, 2014, 4, 116.4, 64),
    }
    rows = []
    csv_lines = [
        "cycle,official_minimum,official_minimum_sn,official_maximum,"
        "official_maximum_sn,official_rise_months,recomputed_minimum,"
        "recomputed_minimum_sn,recomputed_maximum,recomputed_maximum_sn,"
        "recomputed_rise_months,minimum_matches_official,"
        "maximum_matches_official,difference_explanation"
    ]
    for cycle, values in official.items():
        min_year, min_month, min_sn, max_year, max_month, max_sn, rise = values
        recomputed_month = 5 if cycle == 23 else min_month
        recomputed_rise = 66 if cycle == 23 else rise
        matches = cycle != 23
        explanation = (
            "Same smoothed minimum at a different month; both dates retained."
            if cycle == 23
            else "Official and recomputed extrema agree."
        )
        minimum = f"{min_year:04d}-{min_month:02d}"
        recomputed_minimum = f"{min_year:04d}-{recomputed_month:02d}"
        maximum = f"{max_year:04d}-{max_month:02d}"
        rows.append(
            {
                "cycle": cycle,
                "official_minimum": {
                    "year": min_year,
                    "month": min_month,
                    "year_month": minimum,
                    "sunspot_number": min_sn,
                },
                "official_maximum": {
                    "year": max_year,
                    "month": max_month,
                    "year_month": maximum,
                    "sunspot_number": max_sn,
                },
                "recomputed_minimum": {
                    "year": min_year,
                    "month": recomputed_month,
                    "year_month": recomputed_minimum,
                    "sunspot_number": min_sn,
                },
                "recomputed_maximum": {
                    "year": max_year,
                    "month": max_month,
                    "year_month": maximum,
                    "sunspot_number": max_sn,
                },
                "official_rise_months": rise,
                "recomputed_rise_months": recomputed_rise,
                "minimum_matches_official": matches,
                "maximum_matches_official": True,
                "difference_explanation": explanation,
            }
        )
        csv_lines.append(
            f"{cycle},{minimum},{min_sn:.1f},{maximum},{max_sn:.1f},{rise},"
            f"{recomputed_minimum},{min_sn:.1f},{maximum},{max_sn:.1f},"
            f"{recomputed_rise},{matches},True,{explanation}"
        )
    csv_ref = "work/solar_data/silso_cycle_extrema_comparison.csv"
    json_ref = "work/solar_data/silso_cycle_extrema_comparison.json"
    csv_path = tmp_path / csv_ref
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text("\n".join(csv_lines) + "\n", encoding="utf-8")
    json_path = tmp_path / json_ref
    json_path.write_text(
        json.dumps(
            {
                "schema_version": "silso-cycle-reproduction-v1",
                "analysis_protocol": "silso_cycle_reproduction_v1",
                "source": "WDC-SILSO Sunspot Number Version 2.0",
                "method": "Official extrema plus source-preserving recomputation.",
                "cycles": [21, 22, 23, 24],
                "comparison": rows,
            }
        ),
        encoding="utf-8",
    )
    receipt_ref = "receipts/datasets/silso_cycle_extrema_reproduction.json"
    receipt = tmp_path / receipt_ref
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "research-dataset-receipt-v1",
                "receipt_type": "silso_cycle_extrema_reproduction",
                "analysis_protocol": "silso_cycle_reproduction_v1",
                "status": "verified",
                "cycle_numbers": [21, 22, 23, 24],
                "row_count": 4,
                "inputs": [
                    {"dataset_id": dataset_id, "sha256": hashes[dataset_id]}
                    for dataset_id in (
                        "silso-monthly-total-v2",
                        "silso-monthly-smoothed-v2",
                        "silso-cycle-extrema-v2",
                    )
                ],
                "outputs": [
                    {
                        "path": csv_ref,
                        "sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
                    },
                    {
                        "path": json_ref,
                        "sha256": hashlib.sha256(json_path.read_bytes()).hexdigest(),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    refs = [context_ref, receipt_ref, csv_ref, json_ref]
    if include_precursor_receipt:
        precursor_ref = "receipts/datasets/solar_precursor_cycle_table.json"
        precursor = tmp_path / precursor_ref
        precursor.write_text(
            json.dumps({"schema_version": "solar-precursor-cycle-table-v1"}),
            encoding="utf-8",
        )
        refs.append(precursor_ref)
    return refs


def test_data_canonical_readiness_requires_output_when_inputs_exist(
    tmp_path: Path,
) -> None:
    context = _write_curated_data_context(tmp_path, "canonical-data-ready")
    store = ResearchReviewStore(tmp_path, "canonical-data-ready")

    assert store._canonical_stage_ready("data", [context]) is False

    output = tmp_path / "outputs" / "cycle_summary.csv"
    output.parent.mkdir(parents=True)
    output.write_text("cycle,maximum\n21,232.9\n", encoding="utf-8")
    assert store._canonical_stage_ready("data", [context, output]) is False


def test_full_data_accepts_silso_cycle_morphology_receipt(
    tmp_path: Path,
) -> None:
    """The morphology adapter receipt is a first-class full-research output."""

    _write_curated_data_context(tmp_path, "morphology-receipt")
    output = tmp_path / "outputs" / "cycle_morphology_table.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("cycle_number\n1\n", encoding="utf-8")
    receipt_ref = "receipts/datasets/silso_cycle_morphology.json"
    receipt = tmp_path / receipt_ref
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "solar-cycle-morphology-receipt-v1",
                "receipt_type": "silso_cycle_morphology",
                "producer": "solar-data",
                "task_id": "morphology-receipt",
                "status": "verified",
                "outputs": [
                    {
                        "path": "outputs/cycle_morphology_table.csv",
                        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    store = ResearchReviewStore(tmp_path, "morphology-receipt")
    assert output in store._receipted_solar_data_outputs([receipt])


def test_full_hypothesis_rejects_uncheckpointed_working_draft(
    tmp_path: Path,
) -> None:
    """A mutable Hypothesis draft is not the full-research stage artifact."""

    workspace = tmp_path / "workspace"
    state_path = workspace / "work" / "scientific_hypothesis_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "evidence_register": [],
                "checkpoint": None,
                "checkpoint_sha256": None,
                "checkpoint_evidence_sha256": None,
                "latest_draft": {
                    "schema_version": "scientific-hypothesis-response-v1",
                    "response_kind": "hypotheses_ready",
                    "candidates": [
                        {
                            "id": "H1",
                            "statement": "A reviewable but still mutable hypothesis.",
                        }
                    ],
                },
                "tail_review": None,
            }
        ),
        encoding="utf-8",
    )
    store = ResearchReviewStore(workspace, "full-hypothesis-draft")

    assert (
        store._canonical_stage_ready("hypothesis", [state_path], phase="hypothesis")
        is False
    )


def _current_full_hypothesis_state() -> dict[str, object]:
    evidence_register = [
        {
            "evidence_id": "E1",
            "role": "limits",
            "excerpt": "The independent sample is small.",
        }
    ]
    checkpoint = {
        "schema_version": "scientific-hypothesis-response-v1",
        "response_kind": "hypotheses_ready",
        "candidates": [
            {
                "id": "H-reviewed",
                "statement": "A reviewed, testable hypothesis.",
            }
        ],
    }
    checkpoint_sha256 = canonical_json_sha256(checkpoint)
    evidence_sha256 = canonical_json_sha256({"evidence_register": evidence_register})
    return {
        "schema_version": 1,
        "evidence_register": evidence_register,
        "checkpoint": checkpoint,
        "checkpoint_sha256": checkpoint_sha256,
        "checkpoint_evidence_sha256": evidence_sha256,
        "latest_draft": checkpoint,
        "latest_draft_sha256": checkpoint_sha256,
        "tail_review": {
            "schema_version": TAIL_REVIEW_VERSION,
            "evidence_sha256": evidence_sha256,
            "selected_candidate_ids": ["H-reviewed"],
            "selected_candidate_pool_sha256": candidate_pool_sha256(checkpoint),
        },
    }


def test_full_hypothesis_accepts_current_checkpoint_and_tail_review(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "work" / "scientific_hypothesis_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(_current_full_hypothesis_state()), encoding="utf-8"
    )
    store = ResearchReviewStore(tmp_path, "current-full-hypothesis")

    assert store._canonical_stage_ready("hypothesis", [state_path], phase="hypothesis")


def test_full_hypothesis_accepts_immutable_checkpoint_snapshot_after_draft_edit(
    tmp_path: Path,
) -> None:
    """A valid checkpoint remains canonical when the mutable draft changes later."""

    payload = _current_full_hypothesis_state()
    state_path = tmp_path / "work" / "scientific_hypothesis_state.json"
    snapshot_path = tmp_path / "work" / "scientific_hypothesis_checkpoint.json"
    state_path.parent.mkdir(parents=True)
    edited = dict(payload["latest_draft"])
    edited["candidates"] = [{"id": "H-later", "statement": "A later mutable draft."}]
    payload["latest_draft"] = edited
    payload["latest_draft_sha256"] = canonical_json_sha256(edited)
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    snapshot_path.write_text(
        json.dumps(
            {
                "schema_version": "scientific-hypothesis-checkpoint-v1",
                "checkpoint": payload["checkpoint"],
                "checkpoint_sha256": payload["checkpoint_sha256"],
                "checkpoint_evidence_sha256": payload["checkpoint_evidence_sha256"],
                "evidence_register": payload["evidence_register"],
                "tail_review": payload["tail_review"],
            }
        ),
        encoding="utf-8",
    )
    store = ResearchReviewStore(tmp_path, "snapshot-after-edit")
    sources = store._canonical_stage_sources("hypothesis")

    assert snapshot_path in sources
    assert state_path not in sources
    assert store._canonical_stage_ready("hypothesis", sources, phase="hypothesis")


def test_full_hypothesis_rejects_invalid_checkpoint_snapshot(
    tmp_path: Path,
) -> None:
    payload = _current_full_hypothesis_state()
    snapshot_path = tmp_path / "work" / "scientific_hypothesis_checkpoint.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(
        json.dumps(
            {
                "schema_version": "scientific-hypothesis-checkpoint-v1",
                "checkpoint": payload["checkpoint"],
                "checkpoint_sha256": "0" * 64,
                "checkpoint_evidence_sha256": payload["checkpoint_evidence_sha256"],
                "evidence_register": payload["evidence_register"],
                "tail_review": payload["tail_review"],
            }
        ),
        encoding="utf-8",
    )
    store = ResearchReviewStore(tmp_path, "invalid-snapshot")
    assert (
        store._canonical_stage_ready("hypothesis", [snapshot_path], phase="hypothesis")
        is False
    )


@pytest.mark.parametrize(
    "stale_field",
    [
        "checkpoint_sha256",
        "checkpoint_evidence_sha256",
        "latest_draft",
        "latest_draft_sha256",
        "tail_review",
    ],
)
def test_full_hypothesis_rejects_stale_checkpoint_bindings(
    tmp_path: Path, stale_field: str
) -> None:
    payload = _current_full_hypothesis_state()
    if stale_field == "latest_draft":
        payload[stale_field] = {
            "response_kind": "hypotheses_ready",
            "candidates": [{"id": "H-unreviewed", "statement": "A later draft."}],
        }
    elif stale_field == "tail_review":
        payload[stale_field] = {
            "schema_version": TAIL_REVIEW_VERSION,
            "evidence_sha256": payload["checkpoint_evidence_sha256"],
            "selected_candidate_ids": ["H-stale"],
            "selected_candidate_pool_sha256": "0" * 64,
        }
    else:
        payload[stale_field] = "0" * 64
    state_path = tmp_path / "work" / "scientific_hypothesis_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    store = ResearchReviewStore(tmp_path, f"stale-{stale_field}")

    assert (
        store._canonical_stage_ready("hypothesis", [state_path], phase="hypothesis")
        is False
    )


@pytest.mark.parametrize(
    ("response_kind", "details_key"),
    [
        ("clarification_needed", "questions"),
        ("hypothesis_blocked", "blockers"),
    ],
)
def test_full_hypothesis_accepts_honest_non_scientific_terminal_state(
    tmp_path: Path, response_kind: str, details_key: str
) -> None:
    terminal = {
        "schema_version": "scientific-hypothesis-response-v1",
        "response_kind": response_kind,
        details_key: ["A required observation is unavailable."],
    }
    state_path = tmp_path / "work" / "scientific_hypothesis_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "evidence_register": [],
                "checkpoint": None,
                "latest_draft": terminal,
                "latest_draft_sha256": canonical_json_sha256(terminal),
                "tail_review": None,
            }
        ),
        encoding="utf-8",
    )
    store = ResearchReviewStore(tmp_path, f"terminal-{response_kind}")

    assert store._canonical_stage_ready("hypothesis", [state_path], phase="hypothesis")


def test_full_data_readiness_rejects_task_input_without_context(
    tmp_path: Path,
) -> None:
    task_input = tmp_path / "inputs" / "accepted.csv"
    task_input.parent.mkdir(parents=True)
    task_input.write_text("cycle,value\n24,115\n", encoding="utf-8")
    store = ResearchReviewStore(tmp_path, "full-data-task-input-only")

    assert store._canonical_stage_ready("data", [task_input], phase="data") is False


def test_full_data_readiness_rejects_harness_trace_without_context(
    tmp_path: Path,
) -> None:
    trace = (
        tmp_path
        / "research_review"
        / "harness"
        / "full-data-harness-only"
        / "run"
        / "trace.json"
    )
    trace.parent.mkdir(parents=True)
    trace.write_text('{"request_id":"response-only"}', encoding="utf-8")
    store = ResearchReviewStore(tmp_path, "full-data-harness-only")

    assert store._canonical_stage_ready("data", [trace], phase="data") is False


def test_full_data_readiness_rejects_bounded_context_with_unreceipted_output(
    tmp_path: Path,
) -> None:
    context = tmp_path / "receipts" / "datasets" / "data-context-bounded.json"
    context.parent.mkdir(parents=True)
    context.write_text(
        json.dumps(
            {
                "schema_version": "solar-data-context-v1",
                "context_mode": "bounded_data",
                "task_id": "full-data-bounded-context",
                "status": "inputs_available",
                "eligible_inputs": [],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "outputs" / "unreceipted.csv"
    output.parent.mkdir(parents=True)
    output.write_text("cycle,value\n24,115\n", encoding="utf-8")
    store = ResearchReviewStore(tmp_path, "full-data-bounded-context")

    assert (
        store._canonical_stage_ready("data", [context, output], phase="data") is False
    )


def test_full_data_readiness_accepts_authoritative_context_and_receipted_output(
    tmp_path: Path,
) -> None:
    task_id = "full-data-receipted-output"
    context = _write_authoritative_data_context(
        tmp_path,
        task_id,
        missing_required_dataset_ids=[],
        must_stop=False,
    )
    output = tmp_path / "work" / "solar_data" / "cycle_summary.csv"
    output.parent.mkdir(parents=True)
    output.write_text("cycle,maximum\n24,115\n", encoding="utf-8")
    receipt = tmp_path / "receipts" / "datasets" / "cycle-summary.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "solar-precursor-cycle-table-v2",
                "receipt_type": "solar_precursor_cycle_table",
                "status": "verified",
                "producer": "solar-data",
                "task_id": task_id,
                "outputs": [
                    {
                        "path": output.relative_to(tmp_path).as_posix(),
                        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    store = ResearchReviewStore(tmp_path, task_id)

    assert (
        store._canonical_stage_ready("data", [context, receipt, output], phase="data")
        is True
    )


def test_full_data_readiness_accepts_cycle_26_readiness_inventory(
    tmp_path: Path,
) -> None:
    task_id = "full-data-cycle-26-readiness"
    context = _write_authoritative_data_context(
        tmp_path,
        task_id,
        missing_required_dataset_ids=[],
        must_stop=False,
    )
    output = (
        tmp_path / "work" / "solar_data" / "solar_cycle_26_readiness_inventory.json"
    )
    output.parent.mkdir(parents=True)
    output.write_text(
        json.dumps(
            {
                "schema_version": "solar-cycle-26-readiness-v1",
                "launch_readiness": "insufficient_evidence",
            }
        ),
        encoding="utf-8",
    )
    receipt = (
        tmp_path / "receipts" / "datasets" / "solar_cycle_26_readiness_inventory.json"
    )
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "solar-cycle-26-readiness-receipt-v1",
                "receipt_type": "solar_cycle_26_readiness_inventory",
                "status": "verified",
                "producer": "solar-data",
                "task_id": task_id,
                "outputs": [
                    {
                        "path": output.relative_to(tmp_path).as_posix(),
                        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    store = ResearchReviewStore(tmp_path, task_id)

    assert (
        store._canonical_stage_ready("data", [context, receipt, output], phase="data")
        is True
    )


def test_data_input_missing_context_is_a_complete_honest_artifact(
    tmp_path: Path,
) -> None:
    context = tmp_path / "receipts" / "datasets" / "data-context-missing.json"
    context.parent.mkdir(parents=True)
    context.write_text(
        json.dumps(
            {
                "schema_version": "solar-data-context-v1",
                "task_id": "canonical-input-missing",
                "status": "input_missing",
                "eligible_inputs": [],
            }
        ),
        encoding="utf-8",
    )
    store = ResearchReviewStore(tmp_path, "canonical-input-missing")

    assert store._canonical_stage_ready("data", [context], phase="bounded_data") is True


def test_unhashed_legacy_partial_input_context_is_not_canonical_blocker(
    tmp_path: Path,
) -> None:
    context = tmp_path / "receipts" / "datasets" / "data-context-missing-two.json"
    context.parent.mkdir(parents=True)
    context.write_text(
        json.dumps(
            {
                "schema_version": "solar-data-context-v1",
                "context_mode": "bounded_data",
                "task_id": "canonical-partial-input-missing",
                "status": "input_missing",
                "eligible_inputs": [
                    {
                        "dataset_id": "silso-monthly-total-v2",
                        "path": "/inputs/SN_m_tot.csv",
                        "sha256": "a" * 64,
                    }
                ],
                "missing_required_dataset_ids": [
                    "silso-monthly-smoothed-v2",
                    "silso-cycle-extrema-v2",
                ],
            }
        ),
        encoding="utf-8",
    )
    store = ResearchReviewStore(tmp_path, "canonical-partial-input-missing")

    assert store._canonical_stage_ready("data", [context]) is False


def test_forged_full_research_context_cannot_checkpoint_or_be_accepted(
    tmp_path: Path,
) -> None:
    task_id = "forged-full-context-readiness"
    context = tmp_path / "receipts/datasets/data-context-forged.json"
    context.parent.mkdir(parents=True)
    context.write_text(
        json.dumps(
            {
                "schema_version": "solar-data-context-v1",
                "context_mode": "full_research",
                "task_id": task_id,
                "status": "input_missing",
                "must_stop": True,
                "required_dataset_ids": ["required-solar-v1"],
                "missing_required_dataset_ids": ["required-solar-v1"],
                "eligible_inputs": [],
                "context_sha256": "0" * 64,
                "task_sha256": "1" * 64,
                "research_question_sha256": "2" * 64,
                "input_manifest_sha256": "3" * 64,
            }
        ),
        encoding="utf-8",
    )
    store = ResearchReviewStore(tmp_path, task_id)

    with pytest.raises(RuntimeError, match="complete task-local canonical"):
        store.checkpoint_producer_result(
            stage="data",
            producer="solar-data",
            content=context.relative_to(tmp_path).as_posix(),
            phase="bounded_data",
            require_canonical_source=True,
        )

    artifact = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content=context.relative_to(tmp_path).as_posix(),
        phase="bounded_data",
    )
    verdict = store.submit_verdict(
        mode="data",
        decision="accept",
        issues=[],
        accepted_claims=[artifact["claims"][0]["claim_id"]],
    )
    assert verdict["decision"] == "revise"
    assert any(
        issue["rule_id"] == "DATA_SEMANTICS_BOUND" for issue in verdict["issues"]
    )


def test_legacy_bounded_context_cannot_replace_invalid_full_context(
    tmp_path: Path,
) -> None:
    task_id = "legacy-context-full-research-crossing"
    receipts = tmp_path / "receipts/datasets"
    receipts.mkdir(parents=True)
    legacy = receipts / "data-context-legacy.json"
    legacy.write_text(
        json.dumps(
            {
                "schema_version": "solar-data-context-v1",
                "context_mode": "bounded_data",
                "task_id": task_id,
                "status": "input_missing",
                "eligible_inputs": [],
            }
        ),
        encoding="utf-8",
    )
    forged = receipts / "data-context-forged-full.json"
    forged.write_text(
        json.dumps(
            {
                "schema_version": "solar-data-context-v1",
                "context_mode": "full_research",
                "task_id": task_id,
                "status": "input_missing",
                "must_stop": True,
                "required_dataset_ids": ["required-solar-v1"],
                "missing_required_dataset_ids": ["required-solar-v1"],
                "eligible_inputs": [],
                "context_sha256": "0" * 64,
                "task_sha256": "1" * 64,
                "research_question_sha256": "2" * 64,
                "input_manifest_sha256": "3" * 64,
            }
        ),
        encoding="utf-8",
    )
    store = ResearchReviewStore(tmp_path, task_id)
    planning = store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content="Accepted full-research plan.",
    )
    store.submit_verdict(
        mode="planning",
        decision="accept",
        issues=[],
        accepted_claims=[planning["claims"][0]["claim_id"]],
    )

    with pytest.raises(RuntimeError, match="complete task-local canonical"):
        store.checkpoint_producer_result(
            stage="data",
            producer="solar-data",
            content="full-research Data result",
            phase="data",
            require_canonical_source=True,
        )

    artifact = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content="full-research Data result",
        phase="data",
    )
    verdict = store.submit_verdict(
        mode="data",
        decision="block",
        issues=[_required_data_unavailable_issue(artifact["claims"][0]["claim_id"])],
        blocked_claims=[artifact["claims"][0]["claim_id"]],
    )
    assert verdict["decision"] == "revise"
    assert all(
        issue["rule_id"] != "REQUIRED_DATA_INPUT_UNAVAILABLE"
        for issue in verdict["issues"]
    )
    assert any(
        issue["rule_id"] == "DATA_SEMANTICS_BOUND" for issue in verdict["issues"]
    )


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


def test_sc26_forecast_receipt_passes_host_deterministic_data_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deterministic SC26 worker must not depend on a narrative reviewer."""

    task_id = "sc26-deterministic-data-boundary"
    context_ref = "receipts/datasets/data-context-sc26.json"
    context = tmp_path / context_ref
    context.parent.mkdir(parents=True, exist_ok=True)
    context.write_text(
        json.dumps(
            {
                "schema_version": "solar-data-context-v1",
                "context_mode": "full_research",
                "task_id": task_id,
                "status": "inputs_available",
                "required_data_product": SOLAR_CYCLE_26_FORECAST_BACKTEST_DATA_PRODUCT,
                "required_dataset_ids": [
                    "silso-monthly-total-v2",
                    "silso-monthly-smoothed-v2",
                    "silso-cycle-extrema-v2",
                ],
                "missing_required_dataset_ids": [],
                "must_stop": False,
            }
        ),
        encoding="utf-8",
    )
    receipt_ref = "receipts/datasets/solar_cycle_26_forecast_backtest.json"
    receipt = tmp_path / receipt_ref
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "solar-cycle-26-forecast-backtest-receipt-v1",
                "receipt_type": "solar_cycle_26_forecast_backtest",
                "analysis_protocol": "solar_cycle_26_forecast_backtest_v1",
                "status": "verified",
                "cycle_numbers": list(range(1, 25)),
                "row_count": 24,
                "forecast": {"point_estimate": 174.994},
            }
        ),
        encoding="utf-8",
    )
    store = ResearchReviewStore(tmp_path, task_id)
    planning = store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="Accepted plan."
    )
    store.submit_verdict(
        mode="planning",
        decision="accept",
        issues=[],
        accepted_claims=[planning["claims"][0]["claim_id"]],
    )
    artifact = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content=f"{context_ref} {receipt_ref}",
        phase="data",
    )
    # Authority validation has dedicated coverage; isolate this regression to
    # the SC26 context dispatch that must persist an accept verdict.
    monkeypatch.setattr(store, "_deterministic_semantic_issues", lambda *_args: [])

    verdict = store.persist_deterministic_preflight_verdict("data")

    assert verdict is not None
    assert verdict["decision"] == "accept"
    assert verdict["accepted_claims"] == [artifact["claims"][0]["claim_id"]]


def test_curated_v2_boundary_table_passes_deterministic_boundary(
    tmp_path: Path,
) -> None:
    task_id = "curated-data-v2-complete"
    context = _write_curated_data_context(tmp_path, task_id)
    table_ref = "work/solar_data/solar_precursor_cycle_features.csv"
    table = tmp_path / table_ref
    table.parent.mkdir(parents=True)
    _write_complete_boundary_cycle_table(table)
    table_bytes = table.read_bytes()
    receipt = tmp_path / "receipts" / "datasets" / "solar_precursor_cycle_table.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "solar-precursor-cycle-table-v2",
                "receipt_type": "solar_precursor_cycle_table",
                "status": "verified",
                "producer": "solar-data",
                "task_id": task_id,
                "input_refs": [
                    {"dataset_id": "silso-monthly-total-v2", "sha256": "a" * 64},
                    {"dataset_id": "mwo-wso-polar-field-v2", "sha256": "b" * 64},
                ],
                "row_count": 11,
                "cycle_numbers": list(range(14, 25)),
                "analysis_cycle_numbers": list(range(15, 25)),
                "boundary_cycle_numbers": [14],
                "pair_coverage": {
                    "requested_pairs": [
                        f"{cycle}->{cycle + 1}" for cycle in range(14, 24)
                    ],
                    "available_pairs": [
                        f"{cycle}->{cycle + 1}" for cycle in range(14, 24)
                    ],
                    "unavailable_pairs": [],
                },
                "outputs": [
                    {
                        "path": table_ref,
                        "bytes": len(table_bytes),
                        "sha256": hashlib.sha256(table_bytes).hexdigest(),
                    }
                ],
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


def test_silso_protocol_ignores_unrequested_polar_inputs(tmp_path: Path) -> None:
    task_id = "silso-with-extra-polar-input"
    refs = _write_silso_reproduction_artifacts(tmp_path, task_id)
    store = ResearchReviewStore(tmp_path, task_id)
    store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content=" ".join(refs),
        phase="bounded_data",
    )

    verdict = store.persist_deterministic_preflight_verdict("data")
    assert verdict is not None
    assert verdict["decision"] == "accept"
    rendered = store.accepted_bounded_markdown(
        "data", analysis_protocol="silso_cycle_reproduction_v1"
    )

    assert rendered is not None
    assert "周期 21 > 周期 22 > 周期 23 > 周期 24" in rendered
    assert "周期 22 上升最快（38 个月）" in rendered
    assert "周期 24 上升最慢（64 个月）" in rendered
    assert "极区磁场" not in rendered


def test_silso_protocol_rejects_incompatible_precursor_revision(tmp_path: Path) -> None:
    task_id = "silso-incompatible-revision"
    refs = _write_silso_reproduction_artifacts(
        tmp_path, task_id, include_precursor_receipt=True
    )
    store = ResearchReviewStore(tmp_path, task_id)
    store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content=" ".join(refs),
        phase="bounded_data_revision",
    )

    verdict = store.persist_deterministic_preflight_verdict("data")

    assert verdict is not None
    assert verdict["decision"] == "revise"
    assert verdict["issues"][0]["issue_id"] == (
        "deterministic-silso-cycle-reproduction"
    )
    assert "outside this protocol" in verdict["issues"][0]["message"]


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


def test_evidence_issue_normalizer_assigns_id_when_optional_field_is_null() -> None:
    issue = _issue("solar-data")
    issue["issue_id"] = None

    normalized = _normalize_issues([issue])

    assert normalized[0]["issue_id"] == "issue-001"


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


def test_tool_failure_receipt_persists_sanitized_diagnostics(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "diagnostic-failure-task")
    receipt = store.block_for_tool_failures(
        stage="data",
        producer="solar-data",
        fingerprints=["a" * 64],
        failure_summaries=[
            "RuntimeError:   data returned without\nits canonical artifact"
        ],
    )

    assert receipt["schema_version"] == "research-tool-failure-v1"
    assert receipt["failure_summaries"] == [
        "RuntimeError: data returned without its canonical artifact"
    ]
    assert receipt["recovery"] == "new_task_after_fix"


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


def test_reviewer_failure_reopen_preserves_unreviewed_artifact(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "reviewer-reopen-task")
    artifact = store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="frozen plan"
    )
    failure = store.block_for_review_failures(
        stage="planning",
        reviewer="solar-evidence",
        fingerprints=["a" * 64],
    )

    recovery = store.reopen_tool_failure_after_harness_change(
        failure_receipt_sha256=failure["receipt_sha256"],
        change_id="evidence-navigation-v1",
    )

    assert recovery["restored_stage_status"] == "produced"
    assert store.latest_artifact("planning") == artifact
    assert store.next_action() == {
        "kind": "review",
        "stage": "planning",
        "review_mode": "planning",
        "artifact_refs": [store.artifact_ref(artifact)],
    }


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


def test_checkpoint_ignores_truncated_directory_reference_from_ellipsis(
    tmp_path: Path,
) -> None:
    store = ResearchReviewStore(tmp_path, "truncated-path-task")

    artifact = store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content=(
            "The prior record was described as "
            "planner/drafts/…/evidence_revisions/r0001.json, while the concrete "
            "receipt remains receipts/source.json."
        ),
    )

    assert "planner/drafts/" not in artifact["evidence_refs"]
    assert "receipts/source.json" in artifact["evidence_refs"]


def test_checkpoint_drops_nonexistent_planned_output_refs(tmp_path: Path) -> None:
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / "source.json").write_text("{}", encoding="utf-8")
    store = ResearchReviewStore(tmp_path, "planned-output-task")

    artifact = store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content=(
            "Planned outputs: outputs/cycle_morphology_table.csv and "
            "outputs/cycle_morphology_strength_report.md. "
            "Existing receipt: receipts/source.json."
        ),
    )

    assert artifact["evidence_refs"] == ["receipts/source.json"]


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
                    "evidence_register": [
                        {
                            "evidence_id": "E1",
                            "evidence_kind": "literature",
                            "material_id": "litbundle-1",
                            "excerpt": "Exact abstract excerpt.",
                            "verified_support": True,
                            "role": "supports",
                        }
                    ],
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
    assert adapted["claims"][0]["supporting_evidence"] == ["hypothesis-evidence:E1"]
    assert adapted["claims"][0]["limiting_evidence"] == []
    assert adapted["payload"]["result_status"] == "scientific_content"
    assert adapted["claims"][0]["confidence"] == "low"


def test_hypothesis_adapter_exposes_portfolio_ranking_for_evidence_review() -> None:
    source_ref = "work/scientific_hypothesis_state.json"
    ranking = {
        "schema_version": "scientific-hypothesis-portfolio-ranking-v2",
        "ranked_hypotheses": [
            {
                "hypothesis_id": "H1",
                "scientific_support": {"level": "low", "rationale": "limited"},
                "research_priority": {"level": "high", "rationale": "discriminating"},
                "strongest_null_hypothesis": "sampling variation",
                "next_experiment": {"objective": "run the discriminating test"},
                "release_boundary": "do not claim mechanism support",
            }
        ],
        "selected_next_experiment": {
            "hypothesis_ids": ["H1"],
            "objective": "run the discriminating test",
        },
    }
    adapted = adapt_v1_producer_output(
        stage="hypothesis",
        version=1,
        phase="hypothesis",
        text="rendered hypothesis result",
        evidence_refs=[source_ref],
        canonical_documents=[
            {
                "source_ref": source_ref,
                "payload": {
                    "schema_version": 1,
                    "evidence_register": [],
                    "portfolio_ranking": ranking,
                    "checkpoint": {
                        "response_kind": "hypotheses_ready",
                        "candidates": [
                            {
                                "id": "H1",
                                "statement": "A bounded mechanism candidate.",
                                "applicability": "The registered scope.",
                                "supporting_evidence": [],
                                "opposing_evidence": [],
                                "evidence_gaps": ["Direct support is absent."],
                                "confidence": {"level": "low"},
                            }
                        ],
                    },
                },
            }
        ],
    )

    assert adapted["payload"]["hypothesis_portfolio_ranking"] == ranking


def _forecast_receipt_for_projection(
    *, observable_kinds: list[str] | None = None
) -> dict[str, object]:
    return {
        "schema_version": "solar-forecast-experiment-receipt-v1",
        "experiment_id": "polar-precursor-rolling-origin-v1",
        "status": "mixed_evidence",
        "forecast_origin": "cycle_minimum",
        "hypothesis_ids": ["h2_polar_precursor"],
        "feature_ids": ["polar-cycle-15", "polar-cycle-16"],
        "observable_kinds": observable_kinds or ["polar_aperture_field"],
        "baseline_names": ["training_mean", "persistence"],
        "candidate_name": "linear_polar_precursor",
        "training_cycles": [15, 16, 17, 18, 19],
        "test_cycles": [20, 21],
        "folds": [
            {
                "training_cycles": [15, 16, 17, 18, 19],
                "test_cycle": 20,
                "observed": 180.0,
                "candidate_prediction": 170.0,
                "training_mean_prediction": 160.0,
                "persistence_prediction": 155.0,
                "measurement_regime": "MWO",
            },
            {
                "training_cycles": [15, 16, 17, 18, 19, 20],
                "test_cycle": 21,
                "observed": 165.0,
                "candidate_prediction": 160.0,
                "training_mean_prediction": 150.0,
                "persistence_prediction": 180.0,
                "measurement_regime": "WSO",
            },
        ],
        "metrics": {
            "candidate_mae": 7.5,
            "candidate_rmse": 7.91,
            "training_mean_mae": 17.5,
            "training_mean_rmse": 17.68,
            "persistence_mae": 20.0,
            "persistence_rmse": 20.62,
            "mae_improvement": 10.0,
            "mae_improvement_interval": [-1.0, 21.0],
        },
        "bootstrap": {"seed": 20260828, "resamples": 10_000},
        "sensitivity": {
            "measurement_regimes": {
                "MWO": {
                    "fold_count": 1,
                    "mae_improvement": 10.0,
                    "eligible_for_consistency": False,
                },
                "WSO": {
                    "fold_count": 1,
                    "mae_improvement": 10.0,
                    "eligible_for_consistency": False,
                },
            },
            "regime_consistent": False,
            "leave_one_fold": [],
        },
        "leakage_audit": {
            "passed": True,
            "rule": "every training cycle precedes its held-out test cycle",
        },
        "h3_data_status": {
            "status": "blocked_by_data",
            "data_gap": "NO_REGISTERED_AXIAL_DIPOLE_OR_SYNOPTIC_MAP_INPUT",
        },
    }


def test_axial_prose_is_rejected_when_receipt_contains_only_polar_aperture() -> None:
    receipt = _forecast_receipt_for_projection()

    with pytest.raises(ValueError, match="axial_dipole_moment"):
        project_forecast_claim_from_receipt("轴向偶极矩预测更稳定", receipt)


def test_forecast_projection_uses_receipt_numbers_and_origin() -> None:
    receipt = _forecast_receipt_for_projection()

    summary = project_forecast_claim_from_receipt(
        "模型声称候选误差为 0，但这个数字不能进入投影。",
        receipt,
    )

    assert summary == {
        "hypothesis_ids": ["h2_polar_precursor"],
        "forecast_origin": "cycle_minimum",
        "feature_ids": ["polar-cycle-15", "polar-cycle-16"],
        "observable_kinds": ["polar_aperture_field"],
        "candidate_mae": 7.5,
        "baseline_mae": 17.5,
        "mae_improvement": 10.0,
        "mae_improvement_interval": [-1.0, 21.0],
        "skill_status": "mixed_evidence",
        "regime_consistent": False,
        "h3_data_status": {
            "status": "blocked_by_data",
            "data_gap": "NO_REGISTERED_AXIAL_DIPOLE_OR_SYNOPTIC_MAP_INPUT",
        },
    }


def test_forecast_projection_rejects_receipt_status_not_supported_by_metrics() -> None:
    receipt = _forecast_receipt_for_projection()
    receipt["status"] = "skill_supported"

    with pytest.raises(ValueError, match="status"):
        project_forecast_claim_from_receipt("极区前兆具备预测技能", receipt)


def test_experiment_adapter_exposes_receipt_backed_forecast_summary() -> None:
    source_ref = "experiment/runs/polar-run/forecast_experiment_receipt.json"
    adapted = adapt_v1_producer_output(
        stage="experiment_result",
        version=1,
        phase="experiment_result",
        text="模型正文不能替代预测回执。",
        evidence_refs=[source_ref],
        canonical_documents=[
            {
                "source_ref": source_ref,
                "payload": _forecast_receipt_for_projection(),
            }
        ],
    )

    summary = adapted["payload"]["experiment_result_summary"]["forecast_summary"]
    assert summary["source_ref"] == source_ref
    assert summary["forecast_origin"] == "cycle_minimum"
    assert summary["observable_kinds"] == ["polar_aperture_field"]
    assert summary["skill_status"] == "mixed_evidence"
    assert adapted["claims"][0]["supporting_evidence"] == [source_ref]


def test_hypothesis_adapter_prefers_checkpoint_over_newer_working_draft() -> None:
    source_ref = "work/scientific_hypothesis_state.json"
    checkpoint = {
        "response_kind": "hypotheses_ready",
        "candidates": [
            {
                "id": "H-reviewed",
                "statement": "The independently reviewed hypothesis.",
                "applicability": "The reviewed scope.",
                "supporting_evidence": [],
                "opposing_evidence": [],
                "evidence_gaps": [],
                "confidence": {"level": "low"},
            }
        ],
    }
    adapted = adapt_v1_producer_output(
        stage="hypothesis",
        version=1,
        phase="hypothesis",
        text="rendered hypothesis result",
        evidence_refs=[source_ref],
        canonical_documents=[
            {
                "source_ref": source_ref,
                "payload": {
                    "schema_version": 1,
                    "evidence_register": [],
                    "checkpoint": checkpoint,
                    "latest_draft": {
                        "response_kind": "hypotheses_ready",
                        "candidates": [
                            {
                                "id": "H-unreviewed",
                                "statement": "A later mutable draft.",
                                "applicability": "An unreviewed scope.",
                                "supporting_evidence": [],
                                "opposing_evidence": [],
                                "evidence_gaps": [],
                                "confidence": {"level": "low"},
                            }
                        ],
                    },
                },
            }
        ],
    )

    assert [claim["claim_id"] for claim in adapted["claims"]] == [
        "hypothesis-H-reviewed"
    ]
    assert adapted["claims"][0]["text"] == checkpoint["candidates"][0]["statement"]


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


def test_experiment_design_manifest_excludes_mutable_run_state(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "task-1")
    run_root = tmp_path / "experiment" / "runs" / "accepted-run"
    run_root.mkdir(parents=True)
    for name in ("state.json", "request.json", "response.json", "design.json"):
        (run_root / name).write_text(json.dumps({"source": name}), encoding="utf-8")

    artifact = store.checkpoint_producer_result(
        stage="experiment_design",
        producer="solar-experiment",
        content="experiment/runs/accepted-run/design.json",
        phase="bounded_experiment_design",
        require_canonical_source=True,
    )

    source_refs = {row["source_ref"] for row in artifact["payload"]["source_manifest"]}
    assert "experiment/runs/accepted-run/design.json" in source_refs
    assert "experiment/runs/accepted-run/request.json" in source_refs
    assert "experiment/runs/accepted-run/response.json" in source_refs
    assert "experiment/runs/accepted-run/state.json" not in source_refs


def test_experiment_result_context_keeps_accepted_run_id_before_truncation(
    tmp_path: Path,
) -> None:
    store = ResearchReviewStore(tmp_path, "experiment-run-context")
    planning = store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content="large accepted planning context\n" + ("x" * 40_000),
    )
    store.submit_verdict(
        mode="planning",
        decision="accept",
        issues=[],
        accepted_claims=[planning["claims"][0]["claim_id"]],
    )
    run_id = "question_9000493da665-20260815T204740Z-6ddfb5d7"
    run_root = tmp_path / "experiment" / "runs" / run_id
    run_root.mkdir(parents=True)
    (run_root / "state.json").write_text("{}", encoding="utf-8")
    (run_root / "design.json").write_text(
        '{"schema_version":"automatic-experiment-design-v1"}', encoding="utf-8"
    )
    design = store.checkpoint_producer_result(
        stage="experiment_design",
        producer="solar-experiment",
        content=f"run_id: `{run_id}`\nexperiment/runs/{run_id}/design.json",
        phase="bounded_experiment_design",
        require_canonical_source=True,
    )
    store.submit_verdict(
        mode="experiment_design",
        decision="accept",
        issues=[],
        accepted_claims=[design["claims"][0]["claim_id"]],
    )

    context = _upstream_context(store, "experiment_result")

    assert f'"run_id": "{run_id}"' in context


def test_experiment_result_directive_resumes_without_binding_a_new_run() -> None:
    directive = _CANONICAL_CHECKPOINT_DIRECTIVE["experiment_result"]

    assert "resume the exact accepted run_id" in directive
    assert "automatic_experiment_bind_request" not in directive
    assert "Do not call automatic_experiment_create_single_stage_design" in directive
    assert "automatic_experiment_validate_design" in directive
    assert "If inspect_inputs or finalize reports a terminal state" in directive


def test_experiment_result_directive_reads_worker_contract_before_prepare() -> None:
    directive = _CANONICAL_CHECKPOINT_DIRECTIVE["experiment_result"]

    assert "automatic_experiment_inspect_inputs" in directive
    assert "required_worker_outputs" in directive
    assert "before automatic_experiment_prepare_attempt" in directive.lower()
    assert "files as a JSON array" in directive
    assert "files as a JSON object" not in directive
    assert "from the prepare response" not in directive


def test_experiment_result_directive_rejects_empty_parser_outputs_and_text() -> None:
    directive = _CANONICAL_CHECKPOINT_DIRECTIVE["experiment_result"]

    assert "non-empty source" in directive
    assert "accepted upstream inventory" in directive
    assert "technical failure" in directive
    assert "non-empty text" in directive


def test_full_hypothesis_directive_requires_current_review_and_checkpoint() -> None:
    directive = _CANONICAL_CHECKPOINT_DIRECTIVE["hypothesis"]

    assert "scientific_hypothesis_review_tail" in directive
    assert "scientific_hypothesis_checkpoint_draft" in directive
    assert "scientific_hypothesis_get_draft" in directive


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
        _accept(store, stage)
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
        _accept(store, stage)

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
    _accept(store, "hypothesis")
    assert store.next_action()["review_mode"] == "integration"


def test_integration_mechanism_claims_complete_after_evidence_review(
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
        _accept(store, stage)
    store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="post-result mechanism update",
        phase="hypothesis_update",
    )
    _accept(store, "hypothesis")
    integration = store.ensure_integration_artifact()

    verdict = store.submit_verdict(
        mode="integration",
        decision="accept",
        issues=[],
        accepted_claims=[claim["claim_id"] for claim in integration["claims"]],
    )

    assert verdict["decision"] == "accept"
    assert store.next_action()["kind"] == "prepare_release"


def test_final_release_defers_semantic_report_review_to_evidence(
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
        _accept(store, stage)
    store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="hypothesis updated from the observed result",
        phase="hypothesis_update",
    )
    _accept(store, "hypothesis")
    integration = store.ensure_integration_artifact()
    store.submit_verdict(
        mode="integration",
        decision="accept_with_limits",
        issues=[],
        accepted_claims=[claim["claim_id"] for claim in integration["claims"]],
        carry_forward_limits=["No external replication is available."],
    )

    cited_claim_id = integration["claims"][0]["claim_id"]
    report = (
        "# Result 2042\n\n首次提出一项原创机制。\n\n"
        "An additional interpretation without a paragraph-level citation.\n\n"
        "External replication remains unavailable."
    )
    release = store.prepare_release(
        report,
        [
            {
                "claim_id": cited_claim_id,
                "draft_excerpt": "A semantic citation locator, not a verbatim quote.",
            }
        ],
    )

    assert release["payload"]["producer_result"] == report
    assert release["limitations"] == ["No external replication is available."]
    assert release["payload"]["claim_citations"] == [
        {
            "claim_id": cited_claim_id,
            "draft_excerpt": "A semantic citation locator, not a verbatim quote.",
        }
    ]
    action = store.next_action()
    assert action["kind"] == "review"
    assert action["review_mode"] == "final_release"

    issue = _issue("main")
    issue["claim_ref"] = release["claims"][0]["claim_id"]
    issue["fingerprint"] = issue_fingerprint(
        str(issue["rule_id"]), str(issue["claim_ref"]), "main"
    )
    store.submit_verdict(
        mode="final_release",
        decision="revise",
        issues=[issue],
        next_owner="main",
    )
    assert store.next_action()["kind"] == "prepare_release"

    revised_report = (
        "# Result\n\nA coherent accepted synthesis.\n\n"
        "External replication remains unavailable."
    )
    revised = store.prepare_release(
        revised_report,
        [
            {
                "claim_id": cited_claim_id,
                "draft_excerpt": "A coherent accepted synthesis.",
            }
        ],
    )
    final_verdict = store.submit_verdict(
        mode="final_release",
        decision="accept",
        issues=[],
        accepted_claims=[revised["claims"][0]["claim_id"]],
    )

    assert final_verdict["decision"] == "accept"
    assert store.next_action()["kind"] == "released"
    assert store.accepted_release_markdown() == revised_report
    assert store.load_state()["status"] == "release_ready"
    store.mark_release_delivered()
    delivered_state = store.load_state()
    assert delivered_state["status"] == "released"
    assert delivered_state["current_stage"] == "final_release"


def test_final_release_uses_limits_curated_by_integration_review(
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
        _accept(store, stage)
    store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="hypothesis updated from the observed result",
        phase="hypothesis_update",
    )
    _accept(store, "hypothesis")
    integration = store.ensure_integration_artifact()
    integration = build_research_artifact(
        artifact_id=integration["artifact_id"],
        task_id=integration["task_id"],
        stage="integration",
        version=integration["version"],
        producer="supervisor",
        upstream_refs=integration["upstream_refs"],
        claims=integration["claims"],
        evidence_refs=integration["evidence_refs"],
        limitations=["Internal receipt detail must stay out of the reader report."],
        payload=integration["payload"],
    )
    integration_path = (
        store.root
        / "artifacts"
        / integration["artifact_id"]
        / f"v{integration['version']:04d}.json"
    )
    integration_path.write_text(
        json.dumps(integration, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    accepted_claim_id = integration["claims"][0]["claim_id"]
    store.submit_verdict(
        mode="integration",
        decision="accept_with_limits",
        issues=[],
        accepted_claims=[accepted_claim_id],
        carry_forward_limits=["Reader-facing scientific limit."],
    )

    action = store.next_action()
    assert action["kind"] == "prepare_release"
    assert action["release_context"]["required_limits"] == [
        "Reader-facing scientific limit."
    ]
    assert [claim["claim_id"] for claim in action["release_context"]["claims"]] == [
        accepted_claim_id
    ]

    cited_claim_id = accepted_claim_id
    report = (
        "# Result\n\nA coherent accepted synthesis.\n\nReader-facing scientific limit."
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

    assert release["limitations"] == ["Reader-facing scientific limit."]
    assert "Internal receipt detail" not in release["payload"]["producer_result"]


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


def test_orchestration_uses_preliminary_data_stage_for_hypothesis_route(
    monkeypatch,
) -> None:
    store = type(
        "Store",
        (),
        {
            "task_id": "bounded-hypothesis-route",
            "bounded_sequence_action": lambda self, stages: {
                "kind": "producer",
                "stage": "data",
                "producer": "solar-data",
                "phase": "bounded_data",
            },
            "accepted_artifacts": lambda self: [],
            "reserve_action": lambda self, action: None,
        },
    )()
    monkeypatch.setattr(
        "jw.middleware.research_review_orchestration.store_from_config",
        lambda _config: store,
    )
    monkeypatch.setattr(
        "jw.middleware.research_review_orchestration._open_data_context_preflight",
        lambda _config, **_kwargs: {
            "status": "input_missing",
            "context_mode": "bounded",
            "analysis_protocol": "none",
            "required_data_product": None,
            "must_stop": True,
            "receipt_ref": "receipts/datasets/context.json",
            "required_dataset_ids": [],
            "missing_required_dataset_ids": [],
            "eligible_inputs": [],
            "instruction": "Report the missing input.",
        },
    )
    request = _Request(
        {
            "name": "task",
            "id": "call-preliminary-data",
            "args": {"subagent_type": "solar-data", "description": "prepare evidence"},
        },
        {
            "research_route": {
                "mode": "verified_analysis",
                "task_intent": "hypothesis_generation",
                "required_specialist": "solar-hypothesis",
                "preliminary_stages": ["data"],
            }
        },
        _Runtime({}),
    )

    rewritten, action, early = ResearchReviewOrchestrationMiddleware()._prepare(request)

    assert early is None
    assert action["stage"] == "data"
    assert "stage=data" in rewritten.tool_call["args"]["description"]


def test_orchestration_binds_exact_user_question_into_planner_dispatch(
    tmp_path: Path, monkeypatch
) -> None:
    thread_id = "planner-bound-question"
    config = _config(tmp_path, monkeypatch, thread_id)
    workspace = Path(ensure_thread_workspace(thread_id, tmp_path).workspace)
    question = "比较极区场、黑子数和 aa 指数对下一太阳活动周振幅的前兆能力。"
    (workspace / "task.json").write_text(
        json.dumps({"research_question": question}, ensure_ascii=False),
        encoding="utf-8",
    )
    request = _Request(
        {
            "name": "task",
            "id": "call-planner",
            "args": {
                "subagent_type": "solar-planner",
                "description": "deterministic ResearchRunStateV2 producer route",
            },
        },
        {"research_route": {"mode": "full_research"}},
        _Runtime(config),
    )

    rewritten, action, early = ResearchReviewOrchestrationMiddleware()._prepare(request)

    assert early is None
    assert action["stage"] == "planning"
    description = rewritten.tool_call["args"]["description"]
    assert f'bound_research_question="{question}"' in description
    assert "Plan this exact task-bound question" in description


def test_full_research_hypothesis_dispatch_ignores_parent_free_form_summary(
    tmp_path: Path, monkeypatch
) -> None:
    thread_id = "hypothesis-canonical-dispatch"
    config = _config(tmp_path, monkeypatch, thread_id)
    workspace = Path(ensure_thread_workspace(thread_id, tmp_path).workspace)
    question = "上一活动周长度是否调制极区场对下一活动周振幅的前兆关系？"
    (workspace / "task.json").write_text(
        json.dumps({"research_question": question}, ensure_ascii=False),
        encoding="utf-8",
    )
    store = ResearchReviewStore(workspace, thread_id)
    store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content="按活动周对检验极区场与上一活动周长度的交互。",
        phase="planning",
    )
    _accept(store, "planning")
    data = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content=(
            "MWO 光斑代理覆盖活动周 15 至 20，WSO 磁图覆盖活动周 21 至 24；"
            "两个时代分别报告。"
        ),
        phase="data",
    )
    store.submit_verdict(
        mode="data",
        decision="accept_with_limits",
        issues=[],
        accepted_claims=[data["claims"][0]["claim_id"]],
        carry_forward_limits=["MWO 与 WSO 两个测量时代必须分别报告。"],
    )
    misleading_parent_summary = (
        "No pre-magnetogram polar-field proxy series was located for cycles 15-20."
    )
    request = _Request(
        {
            "name": "task",
            "id": "call-hypothesis",
            "args": {
                "subagent_type": "solar-hypothesis",
                "description": misleading_parent_summary,
            },
        },
        {"research_route": {"mode": "full_research"}},
        _Runtime(config),
    )

    rewritten, action, early = ResearchReviewOrchestrationMiddleware()._prepare(request)

    assert early is None
    assert action["stage"] == "hypothesis"
    description = rewritten.tool_call["args"]["description"]
    assert misleading_parent_summary not in description
    assert "bound_hypothesis_request=@" in description
    assert "accepted_upstream=" in description


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


def test_data_dispatch_opens_and_injects_deterministic_context_once(
    tmp_path: Path, monkeypatch
) -> None:
    from jw.middleware import research_review_orchestration as orchestration

    config = _config(tmp_path, monkeypatch, "data-preflight-dispatch")
    calls: list[object] = []
    monkeypatch.setattr(
        ResearchReviewStore,
        "next_action",
        lambda _self: {
            "kind": "producer",
            "stage": "data",
            "producer": "solar-data",
            "phase": "data",
        },
    )

    def open_context(received_config, **kwargs):
        calls.append(received_config)
        assert kwargs == {"route_kind": "full", "analysis_protocol": "none"}
        return {
            "schema_version": "solar-data-context-v1",
            "status": "inputs_available",
            "must_stop": False,
            "receipt_ref": "receipts/datasets/data-context-a.json",
            "context_sha256": "a" * 64,
            "eligible_inputs": [
                {"path": "/project/data/SN_m_tot.csv", "sha256": "b" * 64}
            ],
            "instruction": "use the exact input",
        }

    monkeypatch.setattr(orchestration, "_open_data_context_preflight", open_context)
    request = _Request(
        {
            "name": "task",
            "id": "call-data",
            "args": {"subagent_type": "solar-data", "description": "prepare"},
        },
        {"research_route": {"mode": "full_research"}, "messages": []},
        _Runtime(config),
    )

    rewritten, action, terminal = ResearchReviewOrchestrationMiddleware()._prepare(
        request
    )

    assert action is not None
    assert action["stage"] == "data"
    assert terminal is None
    assert calls == [config]
    description = rewritten.tool_call["args"]["description"]
    assert "deterministic_data_context=" in description
    assert "/project/data/SN_m_tot.csv" in description
    assert "persist at least one additional task-local data artifact" in description


@pytest.mark.parametrize(
    ("analysis_protocol", "expected_ids"),
    [
        (
            "solar_polar_precursor_v1",
            ("silso-monthly-total-v2", "mwo-wso-polar-field-v2"),
        ),
        (
            "solar_cycle_26_readiness_v1",
            (
                "silso-monthly-total-v2",
                "silso-monthly-smoothed-v2",
                "silso-cycle-extrema-v2",
                "noaa-swpc-monthly-f107-v1",
                "mwo-wso-polar-field-v2",
                "wso-current-polar-field-v1",
            ),
        ),
    ],
)
def test_data_preflight_acquires_curated_inputs_for_natural_language_task(
    tmp_path: Path,
    monkeypatch,
    analysis_protocol: str,
    expected_ids: tuple[str, ...],
) -> None:
    from jw.middleware import research_review_orchestration as orchestration

    config = _config(tmp_path, monkeypatch, "data-preflight-acquisition")
    acquisition_calls: list[tuple[Path, str, tuple[str, ...]]] = []

    def acquire(
        base_workspace,
        *,
        project_id="default",
        dataset_ids=None,
    ):
        selected = tuple(dataset_ids or ())
        acquisition_calls.append((Path(base_workspace), project_id, selected))
        records = []
        for dataset_id in selected:
            source = tmp_path / f"{dataset_id}.csv"
            source.write_text(f"source={dataset_id}\n", encoding="utf-8")
            records.append(
                register_project_data_file(
                    base_workspace,
                    source,
                    f"curated/{dataset_id}/{source.name}",
                    dataset_id=dataset_id,
                    provenance={"authority_url": f"https://example.test/{source.name}"},
                    project_id=project_id,
                )
            )
        return records

    monkeypatch.setattr(
        "jw.solar_data_catalog.acquire_authoritative_solar_data", acquire
    )

    context = orchestration._open_data_context_preflight(
        config,
        route_kind="bounded",
        analysis_protocol=analysis_protocol,
    )
    repeated = orchestration._open_data_context_preflight(
        config,
        route_kind="bounded",
        analysis_protocol=analysis_protocol,
    )

    assert context["status"] == "inputs_available"
    assert context["must_stop"] is False
    assert {item["dataset_id"] for item in context["eligible_inputs"]} == set(
        expected_ids
    )
    assert repeated["status"] == "inputs_available"
    assert acquisition_calls == [
        (
            tmp_path,
            "default",
            expected_ids,
        )
    ]
    context_receipts = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in tmp_path.rglob("data-context-*.json")
    ]
    assert context_receipts
    assert {receipt["status"] for receipt in context_receipts} == {"inputs_available"}


def test_data_preflight_preserves_failed_dataset_diagnostic(
    tmp_path: Path, monkeypatch
) -> None:
    from jw.middleware import research_review_orchestration as orchestration

    config = _config(tmp_path, monkeypatch, "data-preflight-diagnostic")

    def acquire(*_args, **_kwargs):
        raise RuntimeError(
            "authoritative solar dataset noaa-swpc-monthly-f107-v1 "
            "acquisition failed: TimeoutError"
        )

    monkeypatch.setattr(
        "jw.solar_data_catalog.acquire_authoritative_solar_data", acquire
    )

    with pytest.raises(
        RuntimeError,
        match=r"noaa-swpc-monthly-f107-v1.*TimeoutError",
    ):
        orchestration._open_data_context_preflight(
            config,
            route_kind="bounded",
            analysis_protocol="solar_cycle_26_readiness_v1",
        )


def test_experiment_input_staging_failure_is_explicit(
    tmp_path: Path, monkeypatch
) -> None:
    from jw.middleware import research_review_orchestration as orchestration

    config = _config(tmp_path, monkeypatch, "experiment-staging-failure")
    monkeypatch.setattr(
        ResearchReviewStore,
        "next_action",
        lambda _self: {
            "kind": "producer",
            "stage": "experiment_design",
            "producer": "solar-experiment",
            "phase": "experiment_design",
        },
    )
    monkeypatch.setattr(
        orchestration,
        "_stage_data_produced_inputs",
        lambda _store: (_ for _ in ()).throw(RuntimeError("accepted CSV unreadable")),
    )
    request = _Request(
        {
            "name": "task",
            "id": "call-experiment-design",
            "args": {"subagent_type": "solar-experiment", "description": "design"},
        },
        {"research_route": {"mode": "full_research"}, "messages": []},
        _Runtime(config),
    )

    _rewritten, action, terminal = ResearchReviewOrchestrationMiddleware()._prepare(
        request
    )

    assert action is not None
    assert terminal is not None
    assert "experiment input staging failed" in str(terminal.content)
    assert "accepted CSV unreadable" in str(terminal.content)


def test_experiment_dispatch_persists_host_owned_research_scope(
    tmp_path: Path, monkeypatch
) -> None:
    from jw.middleware import research_review_orchestration as orchestration

    thread_id = "experiment-host-scope"
    config = _config(tmp_path, monkeypatch, thread_id)
    workspace = Path(ensure_thread_workspace(thread_id, tmp_path).workspace)
    portfolio_capsule = {
        "scientific_support": [{"hypothesis_id": "hypothesis-a", "rank": 1}],
        "research_priority": [{"hypothesis_id": "hypothesis-b", "rank": 1}],
        "strongest_null": [
            {"hypothesis_id": "hypothesis-b", "statement": "no interaction"}
        ],
        "next_experiment": {"objective": "distinguish a from b"},
        "release_boundary": [
            {"hypothesis_id": "hypothesis-a", "boundary": "association only"}
        ],
    }
    ranking_sidecar = (
        workspace / "work" / "research_quality" / "hypothesis_portfolio_ranking.json"
    )
    ranking_sidecar.parent.mkdir(parents=True)
    ranking_sidecar.write_text(json.dumps(portfolio_capsule), encoding="utf-8")
    store = ResearchReviewStore(workspace, thread_id)
    state = store.load_state()
    state["current_stage"] = "experiment_design"
    store._save_state(state)
    monkeypatch.setattr(
        ResearchReviewStore,
        "next_action",
        lambda _self: {
            "kind": "producer",
            "stage": "experiment_design",
            "producer": "solar-experiment",
            "phase": "experiment_design_revision_from_experiment_design",
            "revision_review_id": "review-experiment-design-2",
        },
    )
    accepted = [
        {
            "artifact_id": "hypothesis-artifact",
            "version": 2,
            "artifact_sha256": "b" * 64,
            "stage": "hypothesis",
            "payload": {},
            "limitations": [],
        },
        {
            "artifact_id": "data-artifact",
            "version": 1,
            "artifact_sha256": "a" * 64,
            "stage": "data",
            "payload": {},
            "limitations": [],
        },
    ]
    monkeypatch.setattr(
        ResearchReviewStore,
        "accepted_artifacts",
        lambda _self: list(accepted),
    )
    monkeypatch.setattr(
        ResearchReviewStore,
        "revision_capsule",
        lambda _self, review_id, producer: {
            "review_id": review_id,
            "producer": producer,
        },
    )
    monkeypatch.setattr(
        orchestration,
        "_stage_data_produced_inputs",
        lambda _store: ["inputs/data.csv"],
    )
    stale_run_id = "question_stale-20260823T191613Z-deadbeef"
    request = _Request(
        {
            "name": "task",
            "id": "call-experiment-design-scope",
            "args": {
                "subagent_type": "solar-experiment",
                "description": f"Revise the old run {stale_run_id} in place.",
            },
        },
        {"research_route": {"mode": "full_research"}, "messages": []},
        _Runtime(config),
    )

    rewritten, action, terminal = ResearchReviewOrchestrationMiddleware()._prepare(
        request
    )

    assert terminal is None
    assert action is not None
    description = rewritten.tool_call["args"]["description"]
    assert stale_run_id not in description
    assert "experiment_design_revision_from_experiment_design" in description
    assert "automatic_experiment_bind_request" in description
    assert (
        "use the returned run_id for every subsequent experiment tool call"
        in description
    )
    assert "revision_review_id=review-experiment-design-2" in description
    handoff = json.loads(
        description.split("[A2A_HANDOFF_V1]\n", 1)[1].split(
            "\n\n[RESEARCH_PRODUCER_V2]", 1
        )[0]
    )
    assert handoff["portfolio_ranking"] == portfolio_capsule
    scope = json.loads(
        (workspace / "research_review" / "experiment_scope.json").read_text(
            encoding="utf-8"
        )
    )
    assert scope == {
        "schema_version": "research-experiment-scope-v1",
        "task_id": thread_id,
        "stage": "experiment_design",
        "accepted_upstream_refs": [
            {
                "artifact_id": "data-artifact",
                "version": 1,
                "artifact_sha256": "a" * 64,
                "stage": "data",
            },
            {
                "artifact_id": "hypothesis-artifact",
                "version": 2,
                "artifact_sha256": "b" * 64,
                "stage": "hypothesis",
            },
        ],
        "revision_review_id": "review-experiment-design-2",
        "design_validation_limit": 4,
        "portfolio_ranking": portfolio_capsule,
    }


@pytest.mark.parametrize("existing_scope", [False, True])
def test_experiment_scope_is_not_published_when_action_reservation_fails(
    tmp_path: Path,
    monkeypatch,
    existing_scope: bool,
) -> None:
    from jw.middleware import research_review_orchestration as orchestration

    thread_id = f"scope-reserve-failure-{existing_scope}"
    config = _config(tmp_path, monkeypatch, thread_id)
    workspace = Path(ensure_thread_workspace(thread_id, tmp_path).workspace)
    store = ResearchReviewStore(workspace, thread_id)
    state = store.load_state()
    state["current_stage"] = "experiment_design"
    store._save_state(state)
    scope_path = workspace / "research_review" / "experiment_scope.json"
    original = ""
    if existing_scope:
        original = '{"existing":"scope"}\n'
        scope_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(
        ResearchReviewStore,
        "next_action",
        lambda _self: {
            "kind": "producer",
            "stage": "experiment_design",
            "producer": "solar-experiment",
            "phase": "experiment_design",
        },
    )
    monkeypatch.setattr(
        ResearchReviewStore,
        "reserve_action",
        lambda _self, _action: (_ for _ in ()).throw(
            RuntimeError("reservation rejected")
        ),
    )
    monkeypatch.setattr(
        orchestration,
        "_stage_data_produced_inputs",
        lambda _store: ["inputs/data.csv"],
    )
    request = _Request(
        {
            "name": "task",
            "id": "call-scope-reserve-failure",
            "args": {"subagent_type": "solar-experiment", "description": "design"},
        },
        {"research_route": {"mode": "full_research"}, "messages": []},
        _Runtime(config),
    )

    _rewritten, _action, terminal = ResearchReviewOrchestrationMiddleware()._prepare(
        request
    )

    assert terminal is not None
    assert "reservation rejected" in str(terminal.content)
    assert scope_path.is_file() is existing_scope
    if existing_scope:
        assert scope_path.read_text(encoding="utf-8") == original


def test_concurrent_experiment_scope_publish_uses_unique_atomic_temporary_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from jw import research_review as review_store_module
    from jw.middleware import research_review_orchestration as orchestration

    root = tmp_path / "research_review"
    store = SimpleNamespace(
        root=root,
        task_id="concurrent-scope-publish",
        accepted_artifacts=lambda: [],
    )
    action = {
        "stage": "experiment_design",
        "revision_review_id": None,
    }
    start = Barrier(2)
    replace_guard = Lock()
    replace_active = False
    original_replace = review_store_module.os.replace
    replaced_sources: list[str] = []

    def synchronized_replace(source: object, target: object) -> None:
        nonlocal replace_active
        source_path = Path(source)
        if not (
            source_path.name.startswith(".experiment_scope.json.")
            and source_path.name.endswith(".tmp")
        ):
            original_replace(source, target)
            return
        with replace_guard:
            if replace_active:
                raise PermissionError(13, "Access is denied")
            replace_active = True
        try:
            replaced_sources.append(source_path.name)
            time.sleep(0.05)
            original_replace(source, target)
        finally:
            with replace_guard:
                replace_active = False

    monkeypatch.setattr(review_store_module.os, "replace", synchronized_replace)

    def publish(_index: int) -> Exception | None:
        try:
            start.wait(timeout=2)
            orchestration._persist_experiment_scope(store, action)
        except Exception as exc:
            return exc
        return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        errors = list(executor.map(publish, range(2)))

    assert errors == [None, None]
    scope = json.loads((root / "experiment_scope.json").read_text(encoding="utf-8"))
    assert scope["task_id"] == "concurrent-scope-publish"
    assert len(replaced_sources) == 2
    assert len(set(replaced_sources)) == 2
    assert not list(root.glob("*.tmp"))


def test_polar_experiment_dispatch_injects_cycle_analysis_contract(
    tmp_path: Path, monkeypatch
) -> None:
    from jw.middleware import research_review_orchestration as orchestration

    config = _config(tmp_path, monkeypatch, "polar-experiment-contract")
    monkeypatch.setattr(
        ResearchReviewStore,
        "next_action",
        lambda _self: {
            "kind": "producer",
            "stage": "experiment_design",
            "producer": "solar-experiment",
            "phase": "experiment_design",
        },
    )
    monkeypatch.setattr(
        orchestration,
        "_stage_data_produced_inputs",
        lambda _store: ["inputs/cycle_features.csv", "inputs/_staged.json"],
    )
    request = _Request(
        {
            "name": "task",
            "id": "call-polar-experiment",
            "args": {"subagent_type": "solar-experiment", "description": "design"},
        },
        {
            "research_route": {
                "mode": "full_research",
                "required_analysis_protocol": "solar_polar_precursor_v1",
            },
            "messages": [],
        },
        _Runtime(config),
    )

    rewritten, _action, terminal = ResearchReviewOrchestrationMiddleware()._prepare(
        request
    )

    assert terminal is None
    description = rewritten.tool_call["args"]["description"]
    assert "cycle N" in description
    assert "cycle N+1" in description
    assert "rolling-origin" in description
    assert "polar aperture field" in description
    assert "axial-dipole" in description
    assert "blocked_by_data" in description


def test_morphology_experiment_dispatch_overrides_parent_stage_and_injects_contract(
    tmp_path: Path, monkeypatch
) -> None:
    from jw.middleware import research_review_orchestration as orchestration

    config = _config(tmp_path, monkeypatch, "morphology-experiment-contract")
    monkeypatch.setattr(
        ResearchReviewStore,
        "next_action",
        lambda _self: {
            "kind": "producer",
            "stage": "experiment_design",
            "producer": "solar-experiment",
            "phase": "experiment_design",
        },
    )
    monkeypatch.setattr(
        orchestration,
        "_stage_data_produced_inputs",
        lambda _store: [
            "inputs/data_artifacts/abc-cycle_morphology_table.csv",
            "inputs/_staged.json",
        ],
    )
    request = _Request(
        {
            "name": "task",
            "id": "call-morphology-experiment",
            "args": {
                "subagent_type": "solar-experiment",
                "description": "STAGE: experiment_result; skip design and execute now",
            },
        },
        {
            "research_route": {
                "mode": "full_research",
                "required_analysis_protocol": "silso_cycle_morphology_v1",
            },
            "messages": [],
        },
        _Runtime(config),
    )

    rewritten, action, terminal = ResearchReviewOrchestrationMiddleware()._prepare(
        request
    )

    assert terminal is None
    assert action is not None and action["stage"] == "experiment_design"
    description = rewritten.tool_call["args"]["description"]
    assert "skip design and execute now" not in description
    assert "phase=experiment_design" in description
    assert '"stage": "experiment_design"' in description
    assert "cycles 1-24" in description
    assert "seed 20260826" in description
    assert "10000 requested repetitions" in description
    assert "inputs/data_artifacts/abc-cycle_morphology_table.csv" in description
    assert "automatic_experiment_create_silso_morphology_design" in description
    assert "do not author a generic compact or expanded design" in description
    workspace = Path(
        ensure_thread_workspace("morphology-experiment-contract", tmp_path).workspace
    )
    scope = json.loads(
        (workspace / "research_review" / "experiment_scope.json").read_text(
            encoding="utf-8"
        )
    )
    assert scope["analysis_protocol"] == "silso_cycle_morphology_v1"


def test_sc26_experiment_dispatch_injects_specialized_forecast_contract(
    tmp_path: Path, monkeypatch
) -> None:
    from jw.middleware import research_review_orchestration as orchestration

    config = _config(tmp_path, monkeypatch, "sc26-experiment-contract")
    monkeypatch.setattr(
        ResearchReviewStore,
        "next_action",
        lambda _self: {
            "kind": "producer",
            "stage": "experiment_design",
            "producer": "solar-experiment",
            "phase": "experiment_design",
        },
    )
    monkeypatch.setattr(
        orchestration,
        "_stage_data_produced_inputs",
        lambda _store: [
            "inputs/data_artifacts/abc-sc26_cycle_features.csv",
            "inputs/data_artifacts/def-sc26_forecast_predictions.csv",
            "inputs/data_artifacts/ghi-sc26_formal_forecast.json",
            "inputs/data_artifacts/jkl-run_summary.json",
            "inputs/data_artifacts/mno-data_manifest.json",
        ],
    )
    request = _Request(
        {
            "name": "task",
            "id": "call-sc26-experiment",
            "args": {
                "subagent_type": "solar-experiment",
                "description": "STAGE: experiment_result; skip design and execute now",
            },
        },
        {
            "research_route": {
                "mode": "full_research",
                "required_analysis_protocol": "solar_cycle_26_forecast_backtest_v1",
            },
            "messages": [],
        },
        _Runtime(config),
    )

    rewritten, action, terminal = ResearchReviewOrchestrationMiddleware()._prepare(
        request
    )

    assert terminal is None
    assert action is not None and action["stage"] == "experiment_design"
    description = rewritten.tool_call["args"]["description"]
    assert "skip design and execute now" not in description
    assert "seed 20260827" in description
    assert "10000" in description
    assert "abc-sc26_cycle_features.csv" in description
    assert "automatic_experiment_create_sc26_forecast_design" in description
    assert "do not author a generic compact or expanded design" in description
    workspace = Path(
        ensure_thread_workspace("sc26-experiment-contract", tmp_path).workspace
    )
    scope = json.loads(
        (workspace / "research_review" / "experiment_scope.json").read_text(
            encoding="utf-8"
        )
    )
    assert scope["analysis_protocol"] == "solar_cycle_26_forecast_backtest_v1"


def test_polar_experiment_dispatch_materializes_pair_table_before_staging(
    tmp_path: Path, monkeypatch
) -> None:
    from jw.middleware import research_review_orchestration as orchestration

    config = _config(tmp_path, monkeypatch, "polar-pair-preflight")
    monkeypatch.setattr(
        ResearchReviewStore,
        "next_action",
        lambda _self: {
            "kind": "producer",
            "stage": "experiment_design",
            "producer": "solar-experiment",
            "phase": "experiment_design",
        },
    )
    calls: list[str] = []

    def ensure_pair_table(received_config):
        assert received_config is config
        calls.append("pair_table")
        receipt_ref = "receipts/datasets/solar_cycle_pair_analysis_table.json"
        workspace = Path(
            ensure_thread_workspace("polar-pair-preflight", tmp_path).workspace
        )
        receipt_path = workspace / receipt_ref
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(
            json.dumps(
                {
                    "schema_version": "solar-cycle-pair-analysis-table-v2",
                    "status": "verified",
                    "analysis_status": "analysis_table_ready",
                    "row_count": 10,
                    "predictor_cycles": list(range(14, 24)),
                    "target_cycles": list(range(15, 25)),
                    "pair_coverage": {
                        "requested_pairs": [
                            f"{cycle}->{cycle + 1}" for cycle in range(14, 24)
                        ],
                        "available_pairs": [
                            f"{cycle}->{cycle + 1}" for cycle in range(14, 24)
                        ],
                        "unavailable_pairs": [],
                    },
                    "sample_size": {
                        "independent_sample_unit": "solar_cycle_pair",
                        "independent_sample_count": 10,
                        "n_eff_upper_bound": 10,
                        "n_eff_status": "bounded_not_estimated",
                    },
                }
            ),
            encoding="utf-8",
        )
        return receipt_ref

    def stage_inputs(_store):
        assert calls == ["pair_table"]
        calls.append("stage_inputs")
        return [
            "inputs/solar_cycle_pair_analysis_table.csv",
            "inputs/solar_cycle_pair_analysis_table.json",
            "inputs/_staged.json",
        ]

    monkeypatch.setattr(
        orchestration,
        "_ensure_solar_cycle_pair_analysis_table",
        ensure_pair_table,
    )
    monkeypatch.setattr(orchestration, "_stage_data_produced_inputs", stage_inputs)
    request = _Request(
        {
            "name": "task",
            "id": "call-polar-pair-preflight",
            "args": {"subagent_type": "solar-experiment", "description": "design"},
        },
        {
            "research_route": {
                "mode": "full_research",
                "required_analysis_protocol": "solar_polar_precursor_v1",
            },
            "messages": [],
        },
        _Runtime(config),
    )

    rewritten, _action, terminal = ResearchReviewOrchestrationMiddleware()._prepare(
        request
    )

    assert terminal is None
    assert calls == ["pair_table", "stage_inputs"]
    description = str(rewritten.tool_call["args"]["description"])
    assert "inputs/solar_cycle_pair_analysis_table.csv" in description
    assert (
        'verified_cycle_pair_context={"schema_version": "solar-cycle-pair-analysis-table-v2"'
        in description
    )
    assert '"row_count": 10' in description
    assert '"predictor_cycles": [14, 15, 16, 17, 18, 19, 20, 21, 22, 23]' in description
    assert '"target_cycles": [15, 16, 17, 18, 19, 20, 21, 22, 23, 24]' in description
    assert '"n_eff_status": "bounded_not_estimated"' in description
    assert "authoritative for sample mapping and row count" in description


def test_data_revision_returns_supervisor_pair_table_without_reopening_context(
    tmp_path: Path, monkeypatch
) -> None:
    from jw.middleware import research_review_orchestration as orchestration

    config = _config(tmp_path, monkeypatch, "data-readiness-dispatch")
    monkeypatch.setattr(
        ResearchReviewStore,
        "next_action",
        lambda _self: {
            "kind": "producer",
            "stage": "data",
            "producer": "solar-data",
            "phase": "data_revision_from_data",
        },
    )
    monkeypatch.setattr(
        orchestration,
        "_open_data_context_preflight",
        lambda *_args, **_kwargs: {
            "schema_version": "solar-data-context-v1",
            "status": "inputs_available",
            "must_stop": False,
            "receipt_ref": "receipts/datasets/data-context-a.json",
            "eligible_inputs": [{"path": "/project/data/SN_m_tot.csv"}],
        },
    )
    persisted: list[object] = []

    def persist(received_config, data_context):
        persisted.append((received_config, dict(data_context)))
        relative = Path("receipts/datasets/solar_cycle_pair_analysis_table.json")
        workspace = Path(
            ensure_thread_workspace("data-readiness-dispatch", tmp_path).workspace
        )
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "schema_version": "solar-cycle-pair-analysis-table-v2",
                    "status": "partial",
                    "analysis_status": "analysis_table_incomplete",
                    "row_count": 9,
                    "predictor_cycles": list(range(15, 24)),
                    "target_cycles": list(range(16, 25)),
                    "output_ref": (
                        "receipts/datasets/solar_cycle_pair_analysis_table.csv"
                    ),
                    "independent_sample_unit": "solar_cycle_pair",
                    "pair_coverage": {
                        "requested_pairs": [
                            f"{cycle}->{cycle + 1}" for cycle in range(14, 24)
                        ],
                        "available_pairs": [
                            f"{cycle}->{cycle + 1}" for cycle in range(15, 24)
                        ],
                        "unavailable_pairs": ["14->15"],
                    },
                    "observation_cutoff": "2026-08-15",
                    "limitations": ["Nine cycle pairs are a small sample."],
                }
            ),
            encoding="utf-8",
        )
        return relative.as_posix()

    monkeypatch.setattr(
        orchestration, "_persist_solar_cycle_pair_analysis_table", persist
    )
    request = _Request(
        {
            "name": "task",
            "id": "call-data-readiness",
            "args": {"subagent_type": "solar-data", "description": "revise"},
        },
        {
            "research_route": {
                "mode": "full_research",
                "required_analysis_protocol": "solar_polar_precursor_v1",
            },
            "messages": [],
        },
        _Runtime(config),
    )

    rewritten, action, terminal = ResearchReviewOrchestrationMiddleware()._prepare(
        request
    )

    assert action is not None
    assert terminal is None
    assert len(persisted) == 1
    assert "Independent cycle-pair rows: 9" in action["precomputed_producer_text"]
    assert "solar_cycle_pair_analysis_table.csv" in action["precomputed_producer_text"]
    description = rewritten.tool_call["args"]["description"]
    assert "produced_data_receipt_ref" in description
    assert "solar_cycle_pair_analysis_table.json" in description
    assert '"status": "analysis_table_incomplete"' in description
    assert '"status": "analysis_table_ready"' not in description
    assert '"must_stop": false' in description
    assert (
        "return the receipt-bound analysis table without another tool call"
        in description
    )
    assert "do not open or rediscover the context again" in description


def test_sc26_data_revision_uses_verified_receipt_summary_without_model_loop(
    tmp_path: Path, monkeypatch
) -> None:
    from jw.middleware import research_review_orchestration as orchestration

    task_id = "sc26-data-revision"
    config = _config(tmp_path, monkeypatch, task_id)
    monkeypatch.setattr(
        ResearchReviewStore,
        "next_action",
        lambda _self: {
            "kind": "producer",
            "stage": "data",
            "producer": "solar-data",
            "phase": "data_revision_from_data",
        },
    )
    workspace = Path(ensure_thread_workspace(task_id, tmp_path).workspace)
    receipt_ref = Path("receipts/datasets/solar_cycle_26_forecast_backtest.json")
    receipt_path = workspace / receipt_ref
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(
            {
                "schema_version": "solar-cycle-26-forecast-backtest-receipt-v1",
                "status": "verified",
                "row_count": 24,
                "bootstrap_seed": 20260827,
                "bootstrap_repetitions": 10000,
                "inputs": [
                    {"dataset_id": "silso-monthly-total-v2"},
                    {"dataset_id": "silso-monthly-smoothed-v2"},
                    {"dataset_id": "silso-cycle-extrema-v2"},
                ],
                "forecast": {
                    "point_estimate": 174.99411497816038,
                    "predictive_interval_95": [65.80607396181932, 277.6561818601972],
                    "confidence": "low",
                    "cycle_25_peak_used": 160.9,
                },
            }
        ),
        encoding="utf-8",
    )
    summary_path = workspace / "outputs/sc26_forecast/run_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(
            {
                "lag_peak": {
                    "candidate_mae": 45.99851778834578,
                    "baseline_mae": 42.42238066697664,
                    "mae_improvement": -3.576137121369136,
                    "mae_improvement_ci95": [-11.590422869291944, 3.8906180154420387],
                },
                "same_cycle": {
                    "mae_improvement": 11.233635583400378,
                    "mae_improvement_ci95": [-5.924189371187324, 31.284994276017446],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        orchestration,
        "_open_data_context_preflight",
        lambda *_args, **_kwargs: {
            "schema_version": "solar-data-context-v1",
            "status": "analysis_ready",
            "must_stop": False,
            "produced_data_receipt_ref": receipt_ref.as_posix(),
        },
    )
    request = _Request(
        {
            "name": "task",
            "id": "call-sc26-data-revision",
            "args": {"subagent_type": "solar-data", "description": "revise"},
        },
        {
            "research_route": {
                "mode": "full_research",
                "required_analysis_protocol": "solar_cycle_26_forecast_backtest_v1",
            },
            "messages": [],
        },
        _Runtime(config),
    )

    _rewritten, action, terminal = ResearchReviewOrchestrationMiddleware()._prepare(
        request
    )

    assert terminal is None
    assert action is not None
    text = action["precomputed_producer_text"]
    assert "45.999" in text and "42.422" in text
    assert "[-11.590, 3.891]" in text
    assert "174.994" in text and "[65.806, 277.656]" in text
    assert "low confidence" in text
    assert "Tool call" not in text


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


def test_orchestration_stops_third_evidence_round_without_verdict(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, monkeypatch, "evidence-no-verdict-stop")
    binding = ensure_thread_workspace("evidence-no-verdict-stop", tmp_path)
    store = ResearchReviewStore(Path(binding.workspace), "evidence-no-verdict-stop")
    store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="plan"
    )
    messages = [HumanMessage(content="run full research")]
    failure_text = (
        "[RESEARCH REVIEW BLOCKED] solar-evidence returned without persisting "
        "a hash-bound ReviewVerdictV2"
    )
    for index in range(2):
        call_id = f"call-evidence-{index}"
        messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {"subagent_type": "solar-evidence"},
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
            "id": "call-evidence-third",
            "args": {
                "subagent_type": "solar-evidence",
                "description": "retry Evidence",
            },
        },
        {"research_route": {"mode": "full_research"}, "messages": messages},
        _Runtime(config),
    )

    result = ResearchReviewOrchestrationMiddleware().wrap_tool_call(
        request,
        lambda _rewritten: pytest.fail("third no-verdict review must stop"),
    )

    assert isinstance(result, Command)
    assert result.goto == "__end__"
    assert "failed twice" in str(result.update["messages"][0].content)
    assert store.load_state()["status"] == "blocked"
    assert store.load_state()["stage_status"]["planning"] == "blocked"
    receipt = store.latest_tool_failure_receipt()
    assert receipt is not None
    assert receipt["specialist"] == "solar-evidence"
    assert receipt["specialist_role"] == "reviewer"
    assert "producer" not in receipt


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


def test_bounded_mechanism_hypothesis_completes_after_evidence_review(
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
    assert verdict["decision"] == "accept"
    action = store.bounded_stage_action("hypothesis")
    assert action["kind"] == "released"


def test_bounded_sequence_advances_data_before_hypothesis(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "bounded-sequence-task")

    first = store.bounded_sequence_action(("data", "hypothesis"))
    assert first["stage"] == "data"
    data = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content="bounded data result at receipts/data.json",
        phase="bounded_data",
    )
    _accept(store, "data")

    second = store.bounded_sequence_action(("data", "hypothesis"))
    assert second["stage"] == "hypothesis"
    assert second["producer"] == "solar-hypothesis"
    hypothesis = store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="A hypothesis constrained by the accepted Data result.",
        phase="bounded_hypothesis",
    )
    assert hypothesis["upstream_refs"] == [store._long_ref(data)]


def test_accepted_hypothesis_markdown_includes_evidence_and_closes_state(
    tmp_path: Path,
) -> None:
    store = ResearchReviewStore(tmp_path, "accepted-hypothesis-reader-task")
    artifact = store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="# 最值得检验的假设\n\n极区场前兆主张。",
        phase="bounded_hypothesis",
    )
    claim = artifact["claims"][0]
    store.record_assessment(
        mode="hypothesis",
        assessment_review_mode="two_pass",
        claims=[
            {
                "claim_id": claim["claim_id"],
                "kind": claim["kind"],
                "disposition": "limited_support",
                "supporting_evidence": ["inspected abstract"],
                "opposing_evidence": [],
                "rationale": "The inspected source supports only the historical relation.",
                "key_uncertainty": "Cycle 25 is not a closed independent sample.",
                "confidence": "low",
                "next_test": "Freeze a cutoff and test after the cycle closes.",
            }
        ],
    )
    store.record_scientific_quality_assessment(
        mode="hypothesis",
        assessment_review_mode="two_pass",
        claims=[_quality_claim(claim["claim_id"], component="mechanism")],
    )
    store.submit_verdict(
        mode="hypothesis",
        decision="accept_with_limits",
        issues=[],
        accepted_claims=[claim["claim_id"]],
        carry_forward_limits=["当前周期尚未闭合。"],
    )

    rendered = store.accepted_bounded_markdown("hypothesis")

    assert rendered is not None
    assert "# 最值得检验的假设" in rendered
    assert "## 独立证据审查" in rendered
    assert "有限支持" in rendered
    assert "### 证据矩阵" in rendered
    assert "当前周期尚未闭合" in rendered
    state = store.load_state()
    assert state["status"] == "released"
    assert state["current_stage"] == "hypothesis"


def test_accept_decision_with_major_issue_routes_to_revision(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "decision-consistency-task")
    artifact = store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="bounded plan"
    )
    verdict = store.submit_verdict(
        mode="planning",
        decision="accept",
        issues=[_issue("solar-planner")],
        accepted_claims=[artifact["claims"][0]["claim_id"]],
    )

    assert verdict["decision"] == "revise"
    assert verdict["next_owner"] == "solar-planner"


def test_accept_with_limits_and_critical_issue_routes_to_revision(
    tmp_path: Path,
) -> None:
    store = ResearchReviewStore(tmp_path, "critical-consistency-task")
    artifact = store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="bounded plan"
    )
    issue = _issue("solar-planner")
    issue["severity"] = "critical"
    verdict = store.submit_verdict(
        mode="planning",
        decision="accept_with_limits",
        issues=[issue],
        accepted_claims=[artifact["claims"][0]["claim_id"]],
        carry_forward_limits=["The critical defect remains unresolved."],
    )

    assert verdict["decision"] == "revise"
    assert verdict["next_owner"] == "solar-planner"


def test_stale_policy_verdict_reopens_current_artifact(
    tmp_path: Path,
) -> None:
    store = ResearchReviewStore(tmp_path, "stale-verdict-task")
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
    assert verdict["decision"] == "accept"
    verdict_path = store.root / "verdicts" / f"{verdict['review_id']}.json"
    stale = json.loads(verdict_path.read_text(encoding="utf-8"))
    stale["policy_version"] = "evidence-policy-obsolete"
    verdict_path.write_text(json.dumps(stale), encoding="utf-8")

    action = store.bounded_stage_action("hypothesis")
    assert action["kind"] == "review"
    assert action["artifact_refs"] == [store.artifact_ref(artifact)]
    assert store.load_state()["status"] == "active"


def test_reviewer_issue_can_state_a_numeric_acceptance_test(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "reviewer-number-task")
    store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content="Plan for solar cycle 25 without a dated sample inventory.",
    )
    issue = _issue("solar-planner")
    issue["message"] = "The direct observations allegedly begin in 1976."
    issue["required_action"] = "State that only 4-5 independent pairs exist."
    verdict = store.submit_verdict(
        mode="planning",
        decision="revise",
        issues=[issue],
        next_owner="solar-planner",
    )

    assert verdict["decision"] == "revise"
    assert verdict["issues"][0]["required_action"] == issue["required_action"]


def test_reviewer_issue_identifier_suffix_is_not_a_scientific_number(
    tmp_path: Path,
) -> None:
    store = ResearchReviewStore(tmp_path, "reviewer-identifier-task")
    artifact = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content="The producer returned a framework stop instead of a data result.",
        phase="bounded_data",
    )
    issue = _issue("solar-data")
    issue["message"] = (
        "Close issue_prov_bound_001 only after a real bounded result exists."
    )
    issue["required_action"] = (
        "Replace issue_prov_bound_001 with a source-bound data result."
    )
    issue["acceptance_test"] = (
        "issue_prov_bound_001 is backed by the accepted immutable source."
    )

    verdict = store.submit_verdict(
        mode="data",
        decision="revise",
        issues=[issue],
        next_owner="solar-data",
    )

    assert verdict["artifact_refs"] == [store.artifact_ref(artifact)]
    assert verdict["decision"] == "revise"


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


def test_orchestration_never_checkpoints_model_call_limit_as_science(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, monkeypatch, "producer-budget-stop")
    workspace = Path(
        ensure_thread_workspace("producer-budget-stop", tmp_path).workspace
    )
    data_state = workspace / "work" / "solar_data" / "chat_session.json"
    data_state.parent.mkdir(parents=True)
    data_state.write_text('{"schema_version":1}', encoding="utf-8")
    request = _Request(
        {
            "name": "task",
            "id": "call-data-budget-stop",
            "args": {"subagent_type": "solar-data", "description": "prepare data"},
        },
        {
            "research_route": {
                "mode": "verified_analysis",
                "task_intent": "data_preparation",
                "required_specialist": "solar-data",
            }
        },
        _Runtime(config),
    )

    result = ResearchReviewOrchestrationMiddleware().wrap_tool_call(
        request,
        lambda rewritten: ToolMessage(
            content="Model call limits exceeded: run limit (24/24)",
            tool_call_id=rewritten.tool_call["id"],
            name="task",
        ),
    )

    assert result.status == "error"
    assert "model-call budget" in str(result.content)
    store = ResearchReviewStore(workspace, "producer-budget-stop")
    assert store.latest_artifact("data") is None


def test_solar_cycle_pair_analysis_constructs_temporally_valid_cycle_rows(
    tmp_path: Path,
) -> None:
    source = tmp_path / "solar_precursor_cycle_features.csv"
    source.write_text(
        "cycle_number,minimum_date,maximum_date,peak_smoothed_sunspot_number,"
        "peak_smoothed_sunspot_number_sigma,minimum_date_sensitivity_start,"
        "minimum_date_sensitivity_end,minimum_date_sensitivity_span_months,"
        "polar_field_proxy_gauss,polar_field_proxy_sem_gauss,"
        "predictor_window_start_decimal_year,predictor_window_end_decimal_year,"
        "north_measurement_date,north_source,south_measurement_date,south_source,"
        "predictor_cutoff_decimal_year\n"
        "15,1913-07,1917-08,175.666667,11.8,1912-01,1914-01,24,"
        "1.226355,0.260832,1913.041667,1914.041667,1913.7,MWO,1913.2,MWO,1914.041667\n"
        "16,1923-08,1928-04,130.229167,10.2,1922-09,1923-12,15,"
        "2.09455,0.437951,1923.125,1924.125,1923.7,MWO,1923.2,MWO,1924.125\n"
        "17,1933-09,1937-04,198.641667,12.6,1933-05,1934-05,12,"
        "1.31318,0.277685,1933.208333,1934.208333,1933.7,MWO,1934.2,MWO,1934.208333\n",
        encoding="utf-8",
    )

    rows = _solar_cycle_pair_analysis_from_path(source)

    assert len(rows) == 2
    first = rows[0]
    assert first["predictor_cycle_n"] == 15
    assert first["target_cycle_n_plus_1"] == 16
    assert first["cycle_length_months"] == 121
    assert first["polar_field_at_ending_minimum_gauss"] == pytest.approx(2.09455)
    assert first["next_cycle_amplitude"] == pytest.approx(130.229167)
    assert first["next_cycle_amplitude_sigma"] == pytest.approx(10.2)
    assert first["cycle_end_minimum_sensitivity_start"] == "1922-09"
    assert first["cycle_end_minimum_sensitivity_end"] == "1923-12"
    assert first["n_eff_upper_bound"] == 2
    assert first["prediction_issue_date"] == "1924-02"
    assert first["measurement_regime"] == "MWO_proxy"
    assert first["independent_sample_unit"] == "solar_cycle_pair"
    assert first["target_available_at_issue_time"] is False


def _write_complete_boundary_cycle_table(
    path: Path, *, include_uncertainty: bool = False
) -> None:
    minima = [
        (14, "1902-01", ""),
        (15, "1913-07", "1917-08"),
        (16, "1923-08", "1928-04"),
        (17, "1933-09", "1937-04"),
        (18, "1944-02", "1947-05"),
        (19, "1954-04", "1958-03"),
        (20, "1964-10", "1968-11"),
        (21, "1976-03", "1979-12"),
        (22, "1986-09", "1989-11"),
        (23, "1996-08", "2001-11"),
        (24, "2008-12", "2014-04"),
    ]
    fieldnames = [
        "row_role",
        "cycle_number",
        "minimum_date",
        "minimum_smoothed_sunspot_number",
        "maximum_date",
        "peak_smoothed_sunspot_number",
        "polar_field_proxy_gauss",
        "polar_field_proxy_sem_gauss",
        "north_measurement_date",
        "north_source",
        "south_measurement_date",
        "south_source",
        "predictor_cutoff_decimal_year",
    ]
    if include_uncertainty:
        fieldnames.extend(
            [
                "peak_smoothed_sunspot_number_sigma",
                "minimum_date_sensitivity_start",
                "minimum_date_sensitivity_end",
                "minimum_date_sensitivity_span_months",
                "predictor_window_start_decimal_year",
                "predictor_window_end_decimal_year",
            ]
        )
    rows = []
    for cycle, minimum, maximum in minima:
        if cycle == 14:
            rows.append(
                {
                    "row_role": "boundary",
                    "cycle_number": cycle,
                    "minimum_date": minimum,
                    "maximum_date": "1906-02",
                    "peak_smoothed_sunspot_number": 64.2,
                    **(
                        {"peak_smoothed_sunspot_number_sigma": 9.0}
                        if include_uncertainty
                        else {}
                    ),
                }
            )
            continue
        year, month = (int(value) for value in minimum.split("-"))
        center = year + (month - 0.5) / 12
        cutoff = center + 0.5 if include_uncertainty else center
        row = {
            "row_role": "analysis",
            "cycle_number": cycle,
            "minimum_date": minimum,
            "minimum_smoothed_sunspot_number": 1.0,
            "maximum_date": maximum,
            "peak_smoothed_sunspot_number": 100 + cycle,
            "polar_field_proxy_gauss": f"{cycle / 10:.2f}",
            "polar_field_proxy_sem_gauss": 0.1,
            "north_measurement_date": f"{center - 0.1:.6f}",
            "north_source": "MWO",
            "south_measurement_date": f"{center - 0.05:.6f}",
            "south_source": "MWO",
            "predictor_cutoff_decimal_year": f"{cutoff:.6f}",
        }
        if include_uncertainty:
            row.update(
                {
                    "peak_smoothed_sunspot_number_sigma": 10.0,
                    "minimum_date_sensitivity_start": minimum,
                    "minimum_date_sensitivity_end": minimum,
                    "minimum_date_sensitivity_span_months": 0,
                    "predictor_window_start_decimal_year": f"{center - 0.5:.6f}",
                    "predictor_window_end_decimal_year": f"{center + 0.5:.6f}",
                }
            )
        rows.append(row)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_solar_cycle_pair_analysis_covers_all_ten_requested_pairs(
    tmp_path: Path,
) -> None:
    source = tmp_path / "solar_precursor_cycle_features.csv"
    _write_complete_boundary_cycle_table(source, include_uncertainty=True)

    rows = _solar_cycle_pair_analysis_from_path(source)

    assert [
        (row["predictor_cycle_n"], row["target_cycle_n_plus_1"]) for row in rows
    ] == [(cycle, cycle + 1) for cycle in range(14, 24)]
    assert all(row["temporal_order_validated"] is True for row in rows)
    assert all(row["target_availability_date"] for row in rows)
    assert all(row["previous_cycle_amplitude"] is not None for row in rows)
    assert rows[0]["previous_cycle_amplitude"] == pytest.approx(64.2)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("north_measurement_date", "1901.0"),
        ("south_measurement_date", "1914.0"),
        ("predictor_cutoff_decimal_year", "1914.2"),
        ("maximum_date", "1912-01"),
    ],
)
def test_solar_cycle_pair_analysis_rejects_invalid_temporal_order(
    tmp_path: Path, field: str, value: str
) -> None:
    source = tmp_path / "invalid_temporal_order.csv"
    _write_complete_boundary_cycle_table(source)
    rows = list(csv.DictReader(source.read_text(encoding="utf-8").splitlines()))[:2]
    rows[1][field] = value
    with source.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    with pytest.raises(ValueError, match="temporal order"):
        _solar_cycle_pair_analysis_from_path(source)


def test_solar_cycle_pair_analysis_persists_reviewable_receipt_and_csv(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, monkeypatch, "cycle-pair-persistence")
    workspace = Path(
        ensure_thread_workspace("cycle-pair-persistence", tmp_path).workspace
    )
    source = workspace / "work/solar_data/solar_precursor_cycle_features.csv"
    source.parent.mkdir(parents=True, exist_ok=True)
    _write_complete_boundary_cycle_table(source, include_uncertainty=True)
    precursor_receipt = workspace / "receipts/datasets/solar_precursor_cycle_table.json"
    precursor_receipt.parent.mkdir(parents=True, exist_ok=True)
    precursor_receipt.write_text(
        json.dumps(
            {
                "schema_version": "solar-precursor-cycle-table-v2",
                "receipt_type": "solar_precursor_cycle_table",
                "status": "verified",
                "producer": "solar-data",
                "task_id": "cycle-pair-persistence",
                "created_at": "2026-08-15T00:00:00+00:00",
                "dataset_ids": [
                    "silso-monthly-total-v2",
                    "mwo-wso-polar-field-v2",
                ],
                "outputs": [
                    {
                        "path": "work/solar_data/solar_precursor_cycle_features.csv",
                        "bytes": source.stat().st_size,
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    }
                ],
                "limitations": ["MWO is a calibrated proxy."],
            }
        ),
        encoding="utf-8",
    )

    receipt_ref = _persist_solar_cycle_pair_analysis_table(config, {})

    receipt = json.loads((workspace / receipt_ref).read_text(encoding="utf-8"))
    table_output = receipt["outputs"][0]
    table_path = workspace / table_output["path"]
    table = table_path.read_text(encoding="utf-8")
    assert receipt["schema_version"] == "solar-cycle-pair-analysis-table-v2"
    assert receipt["receipt_type"] == "solar_cycle_pair_analysis_table"
    assert receipt["producer"] == "solar-data"
    assert receipt["task_id"] == "cycle-pair-persistence"
    assert receipt["dataset_ids"] == [
        "silso-monthly-total-v2",
        "mwo-wso-polar-field-v2",
    ]
    assert receipt["row_count"] == 10
    assert receipt["pair_coverage"]["requested_pairs"] == [
        f"{cycle}->{cycle + 1}" for cycle in range(14, 24)
    ]
    assert (
        receipt["pair_coverage"]["available_pairs"]
        == receipt["pair_coverage"]["requested_pairs"]
    )
    assert receipt["pair_coverage"]["unavailable_pairs"] == []
    assert receipt["column_schema"]
    column_schema = {row["name"]: row for row in receipt["column_schema"]}
    assert column_schema["previous_cycle_amplitude"]["type"] == "number"
    assert column_schema["previous_cycle_amplitude_sigma"]["type"] == "number"
    assert column_schema["previous_cycle_peak_date"]["type"] == "year_month"
    assert receipt["units"]["cycle_length_months"] == "month"
    assert (
        receipt["units"]["previous_cycle_amplitude"] == "international_sunspot_number"
    )
    assert (
        receipt["units"]["previous_cycle_amplitude_sigma"]
        == "international_sunspot_number"
    )
    assert receipt["sign_convention"]
    assert receipt["temporal_ordering_rule"]
    assert receipt["sample_size"] == {
        "independent_sample_unit": "solar_cycle_pair",
        "independent_sample_count": 10,
        "n_eff_upper_bound": 10,
        "n_eff_status": "bounded_not_estimated",
    }
    assert set(receipt["uncertainty_fields"]["reported"]) >= {
        "previous_cycle_amplitude_sigma",
        "next_cycle_amplitude_sigma",
        "cycle_end_minimum_sensitivity_start",
        "cycle_end_minimum_sensitivity_end",
    }
    assert table_output["bytes"] == table_path.stat().st_size
    assert table_output["sha256"] == hashlib.sha256(table_path.read_bytes()).hexdigest()
    assert "cycle_length_months" in table
    assert "n_eff_upper_bound" in table
    assert "next_cycle_amplitude_sigma" in table
    assert table.count("\n") == 11


def test_pair_receipt_marks_unverified_measurement_dates_as_gap(
    tmp_path: Path, monkeypatch
) -> None:
    task_id = "cycle-pair-missing-measurement-dates"
    config = _config(tmp_path, monkeypatch, task_id)
    workspace = Path(ensure_thread_workspace(task_id, tmp_path).workspace)
    source = workspace / "work/solar_data/solar_precursor_cycle_features.csv"
    source.parent.mkdir(parents=True, exist_ok=True)
    _write_complete_boundary_cycle_table(source)
    rows = list(csv.DictReader(source.read_text(encoding="utf-8").splitlines()))
    for row in rows[1:]:
        row["north_measurement_date"] = ""
        row["south_measurement_date"] = ""
    with source.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    source_bytes = source.read_bytes()
    receipt = workspace / "receipts/datasets/solar_precursor_cycle_table.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "solar-precursor-cycle-table-v2",
                "receipt_type": "solar_precursor_cycle_table",
                "status": "verified",
                "producer": "solar-data",
                "task_id": task_id,
                "dataset_ids": [
                    "silso-monthly-total-v2",
                    "mwo-wso-polar-field-v2",
                ],
                "outputs": [
                    {
                        "path": "work/solar_data/solar_precursor_cycle_features.csv",
                        "bytes": len(source_bytes),
                        "sha256": hashlib.sha256(source_bytes).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    receipt_ref = _persist_solar_cycle_pair_analysis_table(config, {})
    payload = json.loads((workspace / receipt_ref).read_text(encoding="utf-8"))

    assert payload["status"] == "partial"
    assert payload["analysis_status"] == "analysis_table_incomplete"
    assert any(
        gap["code"] == "PREDICTOR_MEASUREMENT_DATES_NOT_VERIFIED"
        for gap in payload["gaps"]
    )
    assert payload["pair_coverage"]["unavailable_pairs"] == []


def test_legacy_v1_nine_pair_receipt_is_partial_and_cannot_enter_experiment(
    tmp_path: Path, monkeypatch
) -> None:
    task_id = "legacy-v1-nine-pairs"
    config = _config(tmp_path, monkeypatch, task_id)
    workspace = Path(ensure_thread_workspace(task_id, tmp_path).workspace)
    source = workspace / "work/solar_data/solar_precursor_cycle_features.csv"
    source.parent.mkdir(parents=True, exist_ok=True)
    _write_complete_boundary_cycle_table(source)
    rows = list(csv.DictReader(source.read_text(encoding="utf-8").splitlines()))[1:]
    with source.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    source_bytes = source.read_bytes()
    receipt = workspace / "receipts/datasets/solar_precursor_cycle_table.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "solar-precursor-cycle-table-v1",
                "receipt_type": "solar_precursor_cycle_table",
                "status": "verified",
                "producer": "solar-data",
                "task_id": task_id,
                "input_refs": [
                    {"dataset_id": "silso-monthly-total-v2"},
                    {"dataset_id": "mwo-wso-polar-field-v2"},
                ],
                "outputs": [
                    {
                        "path": "work/solar_data/solar_precursor_cycle_features.csv",
                        "bytes": len(source_bytes),
                        "sha256": hashlib.sha256(source_bytes).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    receipt_ref = _persist_solar_cycle_pair_analysis_table(config, {})
    payload = json.loads((workspace / receipt_ref).read_text(encoding="utf-8"))

    assert payload["status"] == "partial"
    assert payload["analysis_status"] == "analysis_table_incomplete"
    assert payload["dataset_ids"] == [
        "silso-monthly-total-v2",
        "mwo-wso-polar-field-v2",
    ]
    assert payload["pair_coverage"]["unavailable_pairs"] == ["14->15"]
    with pytest.raises(RuntimeError, match="incomplete"):
        _ensure_solar_cycle_pair_analysis_table(config)


def test_partial_pair_output_enters_canonical_manifest_and_structured_claim(
    tmp_path: Path, monkeypatch
) -> None:
    task_id = "partial-pair-canonical-review"
    config = _config(tmp_path, monkeypatch, task_id)
    workspace = Path(ensure_thread_workspace(task_id, tmp_path).workspace)
    source = workspace / "work/solar_data/solar_precursor_cycle_features.csv"
    source.parent.mkdir(parents=True, exist_ok=True)
    _write_complete_boundary_cycle_table(source)
    source_rows = list(csv.DictReader(source.read_text(encoding="utf-8").splitlines()))[
        1:
    ]
    with source.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(source_rows[0]))
        writer.writeheader()
        writer.writerows(source_rows)
    source_bytes = source.read_bytes()
    precursor_receipt = workspace / "receipts/datasets/solar_precursor_cycle_table.json"
    precursor_receipt.parent.mkdir(parents=True, exist_ok=True)
    precursor_receipt.write_text(
        json.dumps(
            {
                "schema_version": "solar-precursor-cycle-table-v1",
                "receipt_type": "solar_precursor_cycle_table",
                "status": "verified",
                "producer": "solar-data",
                "task_id": task_id,
                "input_refs": [
                    {"dataset_id": "silso-monthly-total-v2"},
                    {"dataset_id": "mwo-wso-polar-field-v2"},
                ],
                "outputs": [
                    {
                        "path": "work/solar_data/solar_precursor_cycle_features.csv",
                        "bytes": len(source_bytes),
                        "sha256": hashlib.sha256(source_bytes).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    pair_receipt_ref = _persist_solar_cycle_pair_analysis_table(config, {})
    pair_receipt = json.loads(
        (workspace / pair_receipt_ref).read_text(encoding="utf-8")
    )
    assert pair_receipt["status"] == "partial"

    store = ResearchReviewStore(workspace, task_id)
    artifact = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content="Let me inspect the output first.",
        phase="bounded_data",
        require_canonical_source=True,
    )

    pair_output = pair_receipt["outputs"][0]["path"]
    manifest_refs = {
        row["source_ref"] for row in artifact["payload"]["source_manifest"]
    }
    assert pair_output in manifest_refs
    summary = artifact["payload"]["data_result_summary"]
    assert summary["status"] == "partial"
    assert any(
        gap["code"] == "REQUESTED_CYCLE_PAIRS_UNAVAILABLE" for gap in summary["gaps"]
    )
    assert "Let me inspect" not in artifact["claims"][0]["text"]
    with pytest.raises(RuntimeError, match="incomplete"):
        _ensure_solar_cycle_pair_analysis_table(config)


def test_stale_pair_output_is_regenerated_instead_of_reused(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, monkeypatch, "cycle-pair-stale-output")
    workspace = Path(
        ensure_thread_workspace("cycle-pair-stale-output", tmp_path).workspace
    )
    source = workspace / "work/solar_data/solar_precursor_cycle_features.csv"
    source.parent.mkdir(parents=True, exist_ok=True)
    _write_complete_boundary_cycle_table(source)
    precursor_receipt = workspace / "receipts/datasets/solar_precursor_cycle_table.json"
    precursor_receipt.parent.mkdir(parents=True, exist_ok=True)
    precursor_receipt.write_text(
        json.dumps(
            {
                "schema_version": "solar-precursor-cycle-table-v2",
                "receipt_type": "solar_precursor_cycle_table",
                "status": "verified",
                "producer": "solar-data",
                "task_id": "cycle-pair-stale-output",
                "dataset_ids": [
                    "silso-monthly-total-v2",
                    "mwo-wso-polar-field-v2",
                ],
                "outputs": [
                    {
                        "path": "work/solar_data/solar_precursor_cycle_features.csv",
                        "bytes": source.stat().st_size,
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    }
                ],
                "limitations": [],
            }
        ),
        encoding="utf-8",
    )
    receipt_ref = _persist_solar_cycle_pair_analysis_table(config, {})
    first_receipt = json.loads((workspace / receipt_ref).read_text(encoding="utf-8"))
    output_path = workspace / first_receipt["outputs"][0]["path"]
    output_path.write_text(
        output_path.read_text(encoding="utf-8") + "STALE\n", encoding="utf-8"
    )

    reused_ref = _ensure_solar_cycle_pair_analysis_table(config)

    assert reused_ref == receipt_ref
    refreshed = json.loads((workspace / receipt_ref).read_text(encoding="utf-8"))
    assert "STALE" not in output_path.read_text(encoding="utf-8")
    assert (
        refreshed["outputs"][0]["sha256"]
        == hashlib.sha256(output_path.read_bytes()).hexdigest()
    )


def test_precursor_and_pair_receipts_project_structured_data_claim(
    tmp_path: Path,
) -> None:
    precursor_ref = "receipts/datasets/solar_precursor_cycle_table.json"
    pair_ref = "receipts/datasets/solar_cycle_pair_analysis_table.json"
    precursor_output = "work/solar_data/solar_precursor_cycle_features.csv"
    pair_output = "work/solar_data/solar_cycle_pair_analysis_table.csv"
    common = {
        "dataset_ids": [
            "silso-monthly-total-v2",
            "mwo-wso-polar-field-v2",
        ],
        "column_schema": [{"name": "cycle_number", "type": "integer"}],
        "units": {"polar_field_proxy_gauss": "gauss"},
        "sign_convention": {"polar_field_proxy_gauss": "unsigned magnitude"},
        "temporal_ordering_rule": "predictor precedes target",
        "uncertainty_fields": {"reported": ["polar_field_proxy_sem_gauss"]},
        "gaps": [{"code": "TARGET_AMPLITUDE_UNCERTAINTY_NOT_COMPUTED"}],
    }
    documents = [
        {
            "source_ref": precursor_ref,
            "payload": {
                "schema_version": "solar-precursor-cycle-table-v2",
                "receipt_type": "solar_precursor_cycle_table",
                "status": "verified",
                "producer": "solar-data",
                "task_id": "structured-data-claim",
                **common,
                "row_count": 11,
                "pair_coverage": {
                    "requested_pairs": [
                        f"{cycle}->{cycle + 1}" for cycle in range(14, 24)
                    ],
                    "available_pairs": [
                        f"{cycle}->{cycle + 1}" for cycle in range(14, 24)
                    ],
                    "unavailable_pairs": [],
                },
                "outputs": [{"path": precursor_output, "sha256": "a" * 64}],
            },
        },
        {
            "source_ref": pair_ref,
            "payload": {
                "schema_version": "solar-cycle-pair-analysis-table-v2",
                "receipt_type": "solar_cycle_pair_analysis_table",
                "status": "verified",
                "analysis_status": "analysis_table_ready",
                "producer": "solar-data",
                "task_id": "structured-data-claim",
                **common,
                "row_count": 10,
                "pair_coverage": {
                    "requested_pairs": [
                        f"{cycle}->{cycle + 1}" for cycle in range(14, 24)
                    ],
                    "available_pairs": [
                        f"{cycle}->{cycle + 1}" for cycle in range(14, 24)
                    ],
                    "unavailable_pairs": [],
                },
                "outputs": [{"path": pair_output, "sha256": "b" * 64}],
            },
        },
    ]

    adapted = adapt_v1_producer_output(
        stage="data",
        version=1,
        phase="data",
        text=(
            "Let me inspect the produced artifacts via shell. "
            "A proposed but unpersisted record would be at "
            "receipts/datasets/solar_precursor_semantics.json."
        ),
        evidence_refs=[
            precursor_ref,
            pair_ref,
            precursor_output,
            pair_output,
            "receipts/datasets/solar_precursor_semantics.json",
        ],
        canonical_documents=documents,
        current_task_id="structured-data-claim",
        source_manifest=[
            {"source_ref": precursor_ref, "sha256": "c" * 64},
            {"source_ref": pair_ref, "sha256": "d" * 64},
            {"source_ref": precursor_output, "sha256": "a" * 64},
            {"source_ref": pair_output, "sha256": "b" * 64},
        ],
    )

    claim = adapted["claims"][0]
    summary = json.loads(claim["text"])
    assert "Let me inspect" not in claim["text"]
    assert summary["dataset_ids"] == common["dataset_ids"]
    assert summary["pair_coverage"]["available_pairs"] == [
        f"{cycle}->{cycle + 1}" for cycle in range(14, 24)
    ]
    assert summary["row_count"] == 10
    assert set(claim["supporting_evidence"]) >= {
        precursor_ref,
        pair_ref,
        precursor_output,
        pair_output,
    }
    assert adapted["evidence_refs"] == [
        pair_ref,
        precursor_ref,
        pair_output,
        precursor_output,
    ]
    assert "solar_precursor_semantics.json" not in adapted["evidence_refs"]
    assert adapted["payload"]["data_result_summary"] == summary


def test_readiness_receipt_projects_structured_data_claim_from_verified_output() -> (
    None
):
    receipt_ref = "receipts/datasets/solar_cycle_26_readiness_inventory.json"
    output_ref = "work/solar_data/solar_cycle_26_readiness_inventory.json"
    receipt = {
        "schema_version": "solar-cycle-26-readiness-receipt-v1",
        "receipt_type": "solar_cycle_26_readiness_inventory",
        "status": "verified",
        "producer": "solar-data",
        "task_id": "sc26-readiness-claim",
        "dataset_ids": [
            "silso-monthly-total-v2",
            "silso-monthly-smoothed-v2",
            "silso-cycle-extrema-v2",
            "noaa-swpc-monthly-f107-v1",
            "mwo-wso-polar-field-v2",
            "wso-current-polar-field-v1",
        ],
        "launch_readiness": "insufficient_evidence",
        "formal_classification_ready": False,
        "testable_peak_interval_ready": False,
        "evidence_gaps": [
            {
                "code": "NEXT_MINIMUM_NOT_ESTABLISHED",
                "effect": "The cycle-25/26 boundary is not yet observed.",
            }
        ],
        "outputs": [{"path": output_ref, "sha256": "a" * 64}],
    }
    inventory = {
        "schema_version": "solar-cycle-26-readiness-inventory-v1",
        "analysis_protocol": "solar_cycle_26_readiness_v1",
        "cutoff_date": "2026-06-30",
        "launch_readiness": "insufficient_evidence",
        "formal_classification_ready": False,
        "testable_peak_interval_ready": False,
        "cycle_25_state_assessment": {
            "activity_below_observed_peaks": True,
            "next_minimum_status": "not_established",
        },
        "cycle_26_precursor_assessment": {
            "status": "unavailable",
            "same_definition_ready": False,
        },
        "observations": {
            "silso_smoothed": {
                "cycle_25_smoothed_peak_month": "2024-10",
                "cycle_25_smoothed_peak_value": 160.9,
                "latest_month": "2026-01",
                "latest_value": 104.2,
            }
        },
        "evidence_gaps": receipt["evidence_gaps"],
        "interpretation_boundary": (
            "A cycle-26 precursor requires a confirmed next minimum and "
            "same-definition polar measurements near that minimum."
        ),
    }

    adapted = adapt_v1_producer_output(
        stage="data",
        version=1,
        phase="data",
        text="Let me try a different tool parameter format.",
        evidence_refs=[receipt_ref, output_ref],
        canonical_documents=[
            {"source_ref": receipt_ref, "payload": receipt},
            {"source_ref": output_ref, "payload": inventory},
        ],
        current_task_id="sc26-readiness-claim",
        source_manifest=[
            {"source_ref": receipt_ref, "sha256": "b" * 64},
            {"source_ref": output_ref, "sha256": "a" * 64},
        ],
    )

    claim = adapted["claims"][0]
    summary = json.loads(claim["text"])
    assert "different tool parameter" not in claim["text"]
    assert summary["schema_version"] == "solar-data-readiness-summary-v1"
    assert summary["launch_readiness"] == "insufficient_evidence"
    assert summary["observations"] == inventory["observations"]
    assert summary["evidence_gaps"] == receipt["evidence_gaps"]
    assert claim["supporting_evidence"] == [receipt_ref, output_ref]
    assert adapted["payload"]["data_result_summary"] == summary


def test_structured_data_projection_rejects_stale_pair_output_hash() -> None:
    precursor_ref = "receipts/datasets/solar_precursor_cycle_table.json"
    pair_ref = "receipts/datasets/solar_cycle_pair_analysis_table.json"
    precursor_output = "work/solar_data/solar_precursor_cycle_features.csv"
    pair_output = "work/solar_data/solar_cycle_pair_analysis_table.csv"
    requested = [f"{cycle}->{cycle + 1}" for cycle in range(14, 24)]
    common = {
        "producer": "solar-data",
        "task_id": "stale-structured-data",
        "dataset_ids": [
            "silso-monthly-total-v2",
            "mwo-wso-polar-field-v2",
        ],
        "column_schema": [{"name": "cycle_number", "type": "integer"}],
        "units": {"polar_field_proxy_gauss": "gauss"},
        "sign_convention": {"polar_field_proxy_gauss": "unsigned magnitude"},
        "temporal_ordering_rule": "predictor precedes target",
        "uncertainty_fields": {"reported": ["polar_field_proxy_sem_gauss"]},
        "gaps": [],
    }
    documents = [
        {
            "source_ref": precursor_ref,
            "payload": {
                "schema_version": "solar-precursor-cycle-table-v2",
                "receipt_type": "solar_precursor_cycle_table",
                "status": "verified",
                **common,
                "row_count": 11,
                "pair_coverage": {
                    "requested_pairs": requested,
                    "available_pairs": requested,
                    "unavailable_pairs": [],
                },
                "outputs": [{"path": precursor_output, "sha256": "a" * 64}],
            },
        },
        {
            "source_ref": pair_ref,
            "payload": {
                "schema_version": "solar-cycle-pair-analysis-table-v2",
                "receipt_type": "solar_cycle_pair_analysis_table",
                "status": "verified",
                "analysis_status": "analysis_table_ready",
                **common,
                "row_count": 10,
                "pair_coverage": {
                    "requested_pairs": requested,
                    "available_pairs": requested,
                    "unavailable_pairs": [],
                },
                "outputs": [{"path": pair_output, "sha256": "b" * 64}],
            },
        },
    ]

    adapted = adapt_v1_producer_output(
        stage="data",
        version=1,
        phase="data",
        text="garbage producer narration",
        evidence_refs=[precursor_ref, pair_ref, precursor_output, pair_output],
        canonical_documents=documents,
        current_task_id="stale-structured-data",
        source_manifest=[
            {"source_ref": precursor_ref, "sha256": "c" * 64},
            {"source_ref": pair_ref, "sha256": "d" * 64},
            {"source_ref": precursor_output, "sha256": "a" * 64},
            {"source_ref": pair_output, "sha256": "e" * 64},
        ],
    )

    summary = adapted["payload"]["data_result_summary"]
    assert summary["row_count"] == 11
    assert summary["source_receipt_refs"] == [precursor_ref]
    assert pair_output not in adapted["claims"][0]["supporting_evidence"]


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


def test_reviewer_without_verdict_preserves_kimi_structured_failure_summary(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, monkeypatch, "review-kimi-diagnostic-task")
    binding = ensure_thread_workspace("review-kimi-diagnostic-task", tmp_path)
    store = ResearchReviewStore(Path(binding.workspace), "review-kimi-diagnostic-task")
    store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="plan"
    )
    request = _Request(
        {
            "name": "task",
            "id": "call-evidence-kimi-diagnostic",
            "args": {"subagent_type": "solar-evidence", "description": "review"},
        },
        {"research_route": {"mode": "full_research"}},
        _Runtime(config),
    )
    capsule = (
        "[KIMI EVIDENCE STRUCTURED SUBMIT FAILED]\n"
        "event=kimi_evidence_structured_submit_failed\n"
        "error_type=ValueError\n"
        "parsed_present=false\n"
        "raw_message_present=true\n"
        "fingerprint=" + ("a" * 64)
    )

    result = ResearchReviewOrchestrationMiddleware().wrap_tool_call(
        request,
        lambda rewritten: ToolMessage(
            content=capsule,
            tool_call_id=rewritten.tool_call["id"],
            name="task",
        ),
    )

    assert result.status == "error"
    assert "without persisting" in str(result.content)
    assert "event=kimi_evidence_structured_submit_failed" in str(result.content)
    assert "error_type=ValueError" in str(result.content)


def test_review_delegation_discards_parent_copied_artifact_text(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, monkeypatch, "review-description-task")
    binding = ensure_thread_workspace("review-description-task", tmp_path)
    store = ResearchReviewStore(Path(binding.workspace), "review-description-task")
    store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="bounded hypothesis",
        phase="bounded_hypothesis",
    )
    request = _Request(
        {
            "name": "task",
            "id": "call-evidence-description",
            "args": {
                "subagent_type": "solar-evidence",
                "description": "PARENT_COPY " + ("duplicated artifact " * 200),
            },
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
    captured = {}

    def handler(rewritten):
        captured["description"] = rewritten.tool_call["args"]["description"]
        verdict = ResearchReviewStore(
            Path(binding.workspace), "review-description-task"
        ).persist_deterministic_preflight_verdict("hypothesis")
        assert verdict is None
        return ToolMessage(
            content="review ended before submission",
            tool_call_id=rewritten.tool_call["id"],
            name="task",
        )

    ResearchReviewOrchestrationMiddleware().wrap_tool_call(request, handler)

    assert captured["description"].startswith("[EVIDENCE_REVIEW_V2]")
    assert "review_mode=hypothesis" in captured["description"]
    assert "PARENT_COPY" not in captured["description"]
    assert "duplicated artifact" not in captured["description"]


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


def test_record_assessment_roundtrip_and_state_isolation(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "assessment-task")
    artifact = store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content="A plan grounded in an inspected source.",
    )
    artifact_claim = artifact["claims"][0]
    claim_id = artifact_claim["claim_id"]

    assessment = store.record_assessment(
        mode="planning",
        assessment_review_mode="two_pass",
        claims=[
            {
                "claim_id": claim_id,
                "kind": artifact_claim["kind"],
                "disposition": "supported",
                "supporting_evidence": ["inspected source excerpt"],
                "opposing_evidence": [],
                "rationale": "Grounded in the inspected source.",
                "key_uncertainty": "Single-cycle sample.",
                "confidence": "medium",
                "next_test": "Holdout replication on the next cycle.",
            }
        ],
    )

    assert assessment["schema_version"] == "review-assessment-v1"
    assert assessment["assessment_review_mode"] == "two_pass"
    assert assessment["round"] == 1
    assert len(assessment["assessment_sha256"]) == 64
    assert assessment["artifact_refs"] == [store.artifact_ref(artifact)]

    persisted = (
        tmp_path
        / "research_review"
        / "assessments"
        / (assessment["assessment_id"] + ".json")
    )
    assert persisted.exists()

    rows = store.assessments(mode="planning")
    assert [row["assessment_id"] for row in rows] == [assessment["assessment_id"]]

    # The sidecar must not touch run_state, the verdict list, or the budget.
    state = store.load_state()
    assert state["verdicts"] == []
    assert state["review_invocations"] == 0


def test_evidence_tool_requires_exactly_one_complete_current_assessment(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("JW_EVIDENCE_REVIEW_MODE", "two_pass")
    config = _config(tmp_path, monkeypatch, "assessment-tool-task")
    binding = ensure_thread_workspace("assessment-tool-task", tmp_path)
    store = ResearchReviewStore(Path(binding.workspace), "assessment-tool-task")
    artifact = store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content="A bounded plan grounded in an inspected source.",
    )
    claim = artifact["claims"][0]

    missing = json.loads(
        evidence_review_submit_verdict.func(
            review_mode="planning",
            decision="accept",
            issues=[],
            accepted_claims=[claim["claim_id"]],
            config=config,
        )
    )
    assert missing["ok"] is False
    assert "exactly one ReviewAssessmentV1" in missing["message"]

    claims = [
        {
            "claim_id": claim["claim_id"],
            "kind": claim["kind"],
            "disposition": "supported",
            "supporting_evidence": ["inspected source excerpt"],
            "opposing_evidence": [],
            "rationale": "The source supports the bounded wording.",
            "key_uncertainty": "Only one source was inspected.",
            "confidence": "medium",
            "next_test": "Replicate against an independent source.",
        }
    ]
    recorded = json.loads(
        evidence_review_record_assessment.func(
            review_mode="planning",
            assessment_review_mode="two_pass",
            claims=claims,
            config=config,
        )
    )
    assert recorded["ok"] is True

    duplicate = json.loads(
        evidence_review_record_assessment.func(
            review_mode="planning",
            assessment_review_mode="two_pass",
            claims=claims,
            config=config,
        )
    )
    assert duplicate["ok"] is False
    assert "already recorded" in duplicate["message"]

    quality = json.loads(
        evidence_review_record_scientific_quality.func(
            review_mode="planning",
            assessment_review_mode="two_pass",
            claims=[_quality_claim(claim["claim_id"])],
            config=config,
        )
    )
    assert quality["ok"] is True

    accepted = json.loads(
        evidence_review_submit_verdict.func(
            review_mode="planning",
            decision="accept",
            issues=[],
            accepted_claims=[claim["claim_id"]],
            config=config,
        )
    )
    assert accepted["ok"] is True
    assert accepted["result"]["decision"] == "accept"


def test_evidence_submit_round_persists_exactly_one_bound_triplet(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("JW_EVIDENCE_REVIEW_MODE", "two_pass")
    config = _config(tmp_path, monkeypatch, "atomic-round-task")
    binding = ensure_thread_workspace("atomic-round-task", tmp_path)
    store = ResearchReviewStore(Path(binding.workspace), "atomic-round-task")
    artifact = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content="Three independent cycles; monthly rows are repeated measures.",
        phase="bounded_data",
    )
    claim = artifact["claims"][0]
    assessment_claim = {
        "claim_id": claim["claim_id"],
        "kind": "conclusion",
        "disposition": "limited_support",
        "supporting_evidence": [],
        "opposing_evidence": ["Only three independent solar-cycle units."],
        "rationale": "Monthly rows cannot inflate the independent cycle count.",
        "key_uncertainty": "No independent holdout cycle is available.",
        "confidence": "high",
        "next_test": "Evaluate a future independently observed solar cycle.",
    }

    submitted = json.loads(
        evidence_review_submit_round.func(
            review_mode="data",
            assessment_review_mode="two_pass",
            assessment_claims=[assessment_claim],
            scientific_quality_claims=[_quality_claim(claim["claim_id"])],
            decision="block",
            issues=[
                {
                    "rule_id": "SAMPLE_INDEPENDENCE_AND_UNCERTAINTY",
                    "severity": "critical",
                    "claim_ref": claim["claim_id"],
                    "evidence_refs": [],
                    "owner": "solar-data",
                    "message": "Monthly rows are not independent cycle outcomes.",
                    "required_action": "Use the solar cycle as the independent unit.",
                    "acceptance_test": "The reported n equals independent cycles.",
                }
            ],
            blocked_claims=[claim["claim_id"]],
            carry_forward_limits=["Only three independent complete cycles."],
            config=config,
        )
    )

    assert submitted["ok"] is True
    result = submitted["result"]
    assert result["round"] == 1
    assert result["assessment"]["round"] == 1
    assert result["assessment"]["claims"][0]["kind"] == claim["kind"]
    assert result["assessment"]["claims"][0]["disposition"] == "undecided"
    assert result["scientific_quality_assessment"]["round"] == 1
    assert result["verdict"]["round"] == 1
    assert result["verdict"]["decision"] == "block"
    assert len(store.assessments(mode="data")) == 1
    assert len(store.scientific_quality_assessments(mode="data")) == 1
    assert len(store.verdicts(mode="data")) == 1


def test_evidence_submit_round_does_not_call_an_unsupported_claim_limited_support(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("JW_EVIDENCE_REVIEW_MODE", "two_pass")
    config = _config(tmp_path, monkeypatch, "atomic-round-no-support-task")
    binding = ensure_thread_workspace("atomic-round-no-support-task", tmp_path)
    store = ResearchReviewStore(Path(binding.workspace), "atomic-round-no-support-task")
    artifact = store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="An exploratory, falsifiable interaction hypothesis with no empirical result.",
        phase="bounded_hypothesis",
    )
    claim = artifact["claims"][0]

    submitted = json.loads(
        evidence_review_submit_round.func(
            review_mode="hypothesis",
            assessment_review_mode="two_pass",
            assessment_claims=[
                {
                    "claim_id": claim["claim_id"],
                    "kind": claim["kind"],
                    "disposition": "limited_support",
                    "supporting_evidence": [],
                    "opposing_evidence": [],
                    "rationale": "The claim is testable but has not been tested.",
                    "key_uncertainty": "The interaction estimate is unavailable.",
                    "confidence": "low",
                    "next_test": "Estimate the preregistered interaction.",
                }
            ],
            scientific_quality_claims=[_quality_claim(claim["claim_id"])],
            decision="accept_with_limits",
            issues=[],
            accepted_claims=[claim["claim_id"]],
            carry_forward_limits=["No empirical interaction result is available."],
            config=config,
        )
    )

    assert submitted["ok"] is True
    assert submitted["result"]["assessment"]["claims"][0]["disposition"] == "undecided"
    assert submitted["result"]["verdict"]["decision"] == "accept_with_limits"


def test_evidence_submit_round_rejection_leaves_no_unbound_sidecars(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("JW_EVIDENCE_REVIEW_MODE", "two_pass")
    config = _config(tmp_path, monkeypatch, "atomic-round-rejection-task")
    binding = ensure_thread_workspace("atomic-round-rejection-task", tmp_path)
    store = ResearchReviewStore(Path(binding.workspace), "atomic-round-rejection-task")
    artifact = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content="Three independent cycles; monthly rows are repeated measures.",
        phase="bounded_data",
    )
    claim = artifact["claims"][0]

    submitted = json.loads(
        evidence_review_submit_round.func(
            review_mode="data",
            assessment_review_mode="two_pass",
            assessment_claims=[
                {
                    "claim_id": claim["claim_id"],
                    "kind": claim["kind"],
                    "disposition": "limited_support",
                    "supporting_evidence": [],
                    "opposing_evidence": [],
                    "rationale": "The independent sample is small.",
                    "key_uncertainty": "No holdout cycle is available.",
                    "confidence": "high",
                    "next_test": "Observe another complete cycle.",
                }
            ],
            scientific_quality_claims=[_quality_claim(claim["claim_id"])],
            decision="revise",
            issues=[
                {
                    "rule_id": "SAMPLE_INDEPENDENCE_AND_UNCERTAINTY",
                    "severity": "major",
                    "claim_ref": claim["claim_id"],
                    "evidence_refs": [],
                    "owner": "solar-data",
                    "message": "The scope-matched count is unresolved.",
                    "required_action": "Report the complete-cycle count.",
                    "acceptance_test": "The count equals independent cycles.",
                }
            ],
            next_owner="",
            config=config,
        )
    )

    assert submitted["ok"] is False
    assert store.assessments(mode="data") == []
    assert store.scientific_quality_assessments(mode="data") == []
    assert store.verdicts(mode="data") == []


def test_record_assessment_requires_every_reviewed_claim(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "assessment-completeness-task")
    store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content="A bounded plan grounded in an inspected source.",
    )

    with pytest.raises(ValueError, match="omits reviewed claim ids"):
        store.record_assessment(
            mode="planning",
            assessment_review_mode="closed",
            claims=[],
        )


def test_record_assessment_rejects_unknown_claim(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "assessment-unknown-claim-task")
    store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content="A plan grounded in an inspected source.",
    )

    with pytest.raises(ValueError, match="unknown claim ids"):
        store.record_assessment(
            mode="planning",
            assessment_review_mode="closed",
            claims=[
                {
                    "claim_id": "no-such-claim",
                    "kind": "fact",
                    "disposition": "supported",
                    "supporting_evidence": [],
                    "opposing_evidence": [],
                    "rationale": "none",
                    "key_uncertainty": "none",
                    "confidence": "low",
                    "next_test": "none",
                }
            ],
        )


def test_record_assessment_without_artifact_raises(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "assessment-no-artifact-task")
    with pytest.raises(RuntimeError, match="no planning artifact"):
        store.record_assessment(
            mode="planning",
            assessment_review_mode="closed",
            claims=[],
        )
