from __future__ import annotations

import json
from pathlib import Path

import pytest

from jw.research_review import ResearchReviewStore
from research_quality.contracts import (
    ANALYSIS_CLAIM_VERSION,
    ContractError,
    build_scientific_quality_assessment,
    validate_analysis_claim_contract,
)
from research_review.adapters import adapt_v1_producer_output


def _analysis_contract() -> dict[str, object]:
    return {
        "schema_version": ANALYSIS_CLAIM_VERSION,
        "estimand": "下一活动周峰值的时间外预测误差",
        "independent_sample_unit": "一个完整太阳活动周",
        "independent_sample_count": 6,
        "observation_cutoff": "每个预测折发出时点",
        "information_set": "仅使用该折截止时已经完成的历史活动周",
        "primary_analysis": "rolling-origin 线性预测",
        "baseline": "训练样本目标均值",
        "validation_design": "按时间顺序逐活动周留出",
        "decision_rule": "同时比较 MAE、RMSE 与逐折误差，不以训练相关性裁决",
        "missingness": "缺失折不插补未来信息",
        "censoring": "未闭合目标不进入最终误差",
        "data_revision": "每折只使用当时可得版本",
        "measurement_regime": "SILSO Version 2.0 同口径",
        "measurement_kind": "direct",
        "effect_size": "候选相对基线的误差差",
        "uncertainty_interval": "按活动周重采样或置换评估",
        "sensitivity_analysis": "更换合法峰值定义后复算",
        "influence_analysis": "逐活动周删除并报告极端折",
        "outcome_branches": [
            {
                "outcome": "候选在 MAE 与 RMSE 均优于基线",
                "claim_update": "提高预测主张证据约束",
            },
            {
                "outcome": "指标冲突或候选不优于基线",
                "claim_update": "降级或拒绝预测主张",
            },
        ],
    }


def _quality_claim(claim_id: str) -> dict[str, object]:
    return {
        "claim_id": claim_id,
        "claim_component": "mechanism",
        "load_bearing": True,
        "evidence_matrix": [
            {
                "source_ref": "hypothesis-evidence:E1",
                "evidence_role": "limits",
                "source_class": "simulation",
                "evidence_scope": "abstract_only",
                "directness": "indirect",
                "scope_match": "partial",
                "independence_group": "model-family-1",
                "locator": "abstract sentence 2",
                "entailment": "partial",
                "quality_cap": "exploratory",
                "rationale": "The simulation limits a transport regime but does not observe the Sun directly.",
            }
        ],
        "method_assessment": {
            "design_status": "limited",
            "independent_sample_unit": "solar cycle",
            "independent_sample_count": 6,
            "validation_status": "limited",
            "uncertainty_status": "limited",
            "reproducibility_status": "not_assessed",
            "notes": "The available sample is small and regime transfer is unresolved.",
        },
        "novelty_assessment": {
            "status": "incremental_extension",
            "contribution_type": "mechanism_extension",
            "novelty_delta": "Tests a narrower regime boundary than the nearest prior work.",
            "nearest_prior_art": [
                {
                    "source_ref": "doi:10.example/prior",
                    "existing_claim": "The prior model covers one transport regime.",
                    "overlap": "Both address polar-field transport.",
                    "difference": "The candidate adds an observational regime contrast.",
                    "duplication_risk": "The proposed contrast may already appear in full text.",
                }
            ],
            "query_axes": ["mechanism", "mechanism observable", "rival null"],
            "searched_family_count": 8,
            "search_cutoff": "2026-08-12T00:00:00+08:00",
            "coverage_gaps": ["One paywalled full text was unavailable."],
        },
        "conclusion_cap": "exploratory",
        "quality_status": "exploratory",
        "key_gaps": ["Direct observational adjudication is missing."],
    }


def test_analysis_claim_contract_requires_two_outcome_branches() -> None:
    payload = _analysis_contract()
    assert validate_analysis_claim_contract(payload)["independent_sample_count"] == 6
    payload["outcome_branches"] = payload["outcome_branches"][:1]
    with pytest.raises(ContractError, match="outcome_branches"):
        validate_analysis_claim_contract(payload)


def test_training_correlation_cannot_satisfy_prediction_gate() -> None:
    payload = _analysis_contract()
    payload["validation_design"] = "训练相关性与拟合优度"
    with pytest.raises(ContractError, match="out-of-sample"):
        validate_analysis_claim_contract(payload)


def test_monthly_records_cannot_inflate_cycle_sample_count() -> None:
    payload = _analysis_contract()
    payload["independent_sample_unit"] = "月度观测记录"
    with pytest.raises(ContractError, match="monthly records"):
        validate_analysis_claim_contract(payload)


def test_potential_novelty_requires_search_coverage_and_nearest_prior_art() -> None:
    claim = _quality_claim("hypothesis-H1")
    claim["novelty_assessment"]["status"] = "potentially_novel"
    claim["novelty_assessment"]["searched_family_count"] = 7
    with pytest.raises(ContractError, match="8 source families"):
        build_scientific_quality_assessment(
            assessment_id="hypothesis-quality-0001",
            task_id="task-1",
            review_mode="hypothesis",
            assessment_review_mode="two_pass",
            artifact_refs=[
                {
                    "artifact_id": "hypothesis-artifact",
                    "version": 1,
                    "artifact_sha256": "a" * 64,
                }
            ],
            round=1,
            claims=[claim],
            created_at="2026-08-12T00:00:00+00:00",
        )


def test_quality_assessment_allows_distinct_components_for_one_claim() -> None:
    mechanism = _quality_claim("hypothesis-H1")
    prediction = _quality_claim("hypothesis-H1")
    prediction["claim_component"] = "prediction"

    assessment = build_scientific_quality_assessment(
        assessment_id="hypothesis-quality-components-0001",
        task_id="task-1",
        review_mode="hypothesis",
        assessment_review_mode="two_pass",
        artifact_refs=[
            {
                "artifact_id": "hypothesis-artifact",
                "version": 1,
                "artifact_sha256": "a" * 64,
            }
        ],
        round=1,
        claims=[mechanism, prediction],
        created_at="2026-08-12T00:00:00+00:00",
    )

    assert [row["claim_component"] for row in assessment["claims"]] == [
        "mechanism",
        "prediction",
    ]


def test_quality_assessment_rejects_duplicate_claim_component_pair() -> None:
    claim = _quality_claim("hypothesis-H1")
    with pytest.raises(ContractError, match="id and component pairs"):
        build_scientific_quality_assessment(
            assessment_id="hypothesis-quality-components-0002",
            task_id="task-1",
            review_mode="hypothesis",
            assessment_review_mode="two_pass",
            artifact_refs=[
                {
                    "artifact_id": "hypothesis-artifact",
                    "version": 1,
                    "artifact_sha256": "a" * 64,
                }
            ],
            round=1,
            claims=[claim, dict(claim)],
            created_at="2026-08-12T00:00:00+00:00",
        )


@pytest.mark.parametrize(
    ("source_class", "evidence_scope"),
    [
        ("direct_observation", "abstract_only"),
        ("wiki_context", "wiki_entry"),
        ("simulation", "full_text"),
    ],
)
def test_abstract_wiki_and_simulation_cannot_carry_release_claim(
    source_class: str,
    evidence_scope: str,
) -> None:
    claim = _quality_claim("hypothesis-H1")
    evidence = claim["evidence_matrix"][0]
    evidence.update(
        {
            "source_ref": "source:one",
            "evidence_role": "supports",
            "source_class": source_class,
            "evidence_scope": evidence_scope,
            "directness": "direct",
            "scope_match": "matched",
            "entailment": "entailed",
            "quality_cap": "release_candidate",
        }
    )
    with pytest.raises(ContractError, match="cannot"):
        build_scientific_quality_assessment(
            assessment_id="hypothesis-quality-0002",
            task_id="task-1",
            review_mode="hypothesis",
            assessment_review_mode="two_pass",
            artifact_refs=[
                {
                    "artifact_id": "hypothesis-artifact",
                    "version": 1,
                    "artifact_sha256": "a" * 64,
                }
            ],
            round=1,
            claims=[claim],
            created_at="2026-08-12T00:00:00+00:00",
        )


def test_repeated_sources_from_one_family_do_not_establish_release_claim() -> None:
    claim = _quality_claim("hypothesis-H1")
    evidence = {
        "source_ref": "dataset:one",
        "evidence_role": "supports",
        "source_class": "direct_observation",
        "evidence_scope": "dataset_record",
        "directness": "direct",
        "scope_match": "matched",
        "independence_group": "shared-solar-cycle-series",
        "locator": "table result row 4",
        "entailment": "entailed",
        "quality_cap": "release_candidate",
        "rationale": "The row directly reports the stated observable.",
    }
    claim["evidence_matrix"] = [
        evidence,
        {**evidence, "source_ref": "paper:derived-from-dataset-one"},
    ]
    claim["novelty_assessment"]["status"] = "incremental_extension"
    claim["conclusion_cap"] = "release_candidate"
    claim["quality_status"] = "release_candidate"
    claim["key_gaps"] = []
    with pytest.raises(ContractError, match="one evidence family"):
        build_scientific_quality_assessment(
            assessment_id="hypothesis-quality-0003",
            task_id="task-1",
            review_mode="hypothesis",
            assessment_review_mode="two_pass",
            artifact_refs=[
                {
                    "artifact_id": "hypothesis-artifact",
                    "version": 1,
                    "artifact_sha256": "a" * 64,
                }
            ],
            round=1,
            claims=[claim],
            created_at="2026-08-12T00:00:00+00:00",
        )


def test_two_independent_primary_families_can_form_release_candidate() -> None:
    claim = _quality_claim("hypothesis-H1")
    evidence = {
        "source_ref": "dataset:one",
        "evidence_role": "supports",
        "source_class": "direct_observation",
        "evidence_scope": "dataset_record",
        "directness": "direct",
        "scope_match": "matched",
        "independence_group": "observatory-a",
        "locator": "table result row 4",
        "entailment": "entailed",
        "quality_cap": "release_candidate",
        "rationale": "The row directly reports the stated observable.",
    }
    claim["evidence_matrix"] = [
        evidence,
        {
            **evidence,
            "source_ref": "experiment:two",
            "source_class": "real_experiment",
            "evidence_scope": "experiment_record",
            "independence_group": "experiment-b",
        },
    ]
    claim["conclusion_cap"] = "release_candidate"
    claim["quality_status"] = "release_candidate"
    claim["key_gaps"] = []
    assessment = build_scientific_quality_assessment(
        assessment_id="hypothesis-quality-0004",
        task_id="task-1",
        review_mode="hypothesis",
        assessment_review_mode="two_pass",
        artifact_refs=[
            {
                "artifact_id": "hypothesis-artifact",
                "version": 1,
                "artifact_sha256": "a" * 64,
            }
        ],
        round=1,
        claims=[claim],
        created_at="2026-08-12T00:00:00+00:00",
    )
    assert assessment["claims"][0]["quality_status"] == "release_candidate"


def test_blocked_hypothesis_is_workflow_status_not_mechanism() -> None:
    source_ref = "work/scientific_hypothesis_state.json"
    adapted = adapt_v1_producer_output(
        stage="hypothesis",
        version=1,
        phase="bounded_hypothesis",
        text="blocked",
        evidence_refs=[source_ref],
        canonical_documents=[
            {
                "source_ref": source_ref,
                "payload": {
                    "schema_version": 1,
                    "evidence_register": [],
                    "latest_draft": {
                        "response_kind": "hypothesis_blocked",
                        "blockers": ["missing indispensable observation"],
                    },
                },
            }
        ],
    )
    assert adapted["payload"]["result_status"] == "blocked_status"
    assert adapted["claims"][0]["kind"] == "unknown"
    assert "not a scientific hypothesis" in adapted["claims"][0]["scope"]


def test_hypothesis_clarification_is_workflow_status_not_mechanism() -> None:
    source_ref = "work/scientific_hypothesis_state.json"
    adapted = adapt_v1_producer_output(
        stage="hypothesis",
        version=1,
        phase="bounded_hypothesis",
        text="clarification",
        evidence_refs=[source_ref],
        canonical_documents=[
            {
                "source_ref": source_ref,
                "payload": {
                    "schema_version": 1,
                    "evidence_register": [],
                    "latest_draft": {
                        "response_kind": "clarification_needed",
                        "questions": ["Which observable is in scope?"],
                    },
                },
            }
        ],
    )
    assert adapted["payload"]["result_status"] == "clarification_status"
    assert adapted["claims"][0]["kind"] == "unknown"
    assert "not a scientific hypothesis" in adapted["claims"][0]["scope"]


def test_hypothesis_limit_is_not_projected_as_support() -> None:
    source_ref = "work/scientific_hypothesis_state.json"
    adapted = adapt_v1_producer_output(
        stage="hypothesis",
        version=1,
        phase="bounded_hypothesis",
        text="candidate",
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
                            "material_id": "bundle-1",
                            "excerpt": "A model-family limitation.",
                            "verified_support": True,
                            "role": "limits",
                        }
                    ],
                    "latest_draft": {
                        "response_kind": "hypotheses_ready",
                        "candidates": [
                            {
                                "id": "H1",
                                "statement": "A bounded candidate.",
                                "applicability": "One regime.",
                                "supporting_evidence": [{"evidence_id": "E1"}],
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
    claim = adapted["claims"][0]
    assert claim["supporting_evidence"] == []
    assert claim["limiting_evidence"] == ["hypothesis-evidence:E1"]


def test_hypothesis_adapter_uses_registered_role_for_all_evidence_links() -> None:
    source_ref = "work/scientific_hypothesis_state.json"
    roles = {"E-support": "supports", "E-oppose": "opposes", "E-limit": "limits"}
    adapted = adapt_v1_producer_output(
        stage="hypothesis",
        version=1,
        phase="bounded_hypothesis",
        text="candidate",
        evidence_refs=[source_ref],
        canonical_documents=[
            {
                "source_ref": source_ref,
                "payload": {
                    "schema_version": 1,
                    "evidence_register": [
                        {
                            "evidence_id": evidence_id,
                            "evidence_kind": "literature",
                            "material_id": evidence_id,
                            "excerpt": evidence_id,
                            "verified_support": True,
                            "role": role,
                        }
                        for evidence_id, role in roles.items()
                    ],
                    "latest_draft": {
                        "response_kind": "hypotheses_ready",
                        "candidates": [
                            {
                                "id": "H1",
                                "statement": "A bounded candidate.",
                                "applicability": "One regime.",
                                "supporting_evidence": [
                                    {"evidence_id": "E-oppose"},
                                    {"evidence_id": "E-limit"},
                                ],
                                "opposing_evidence": [{"evidence_id": "E-support"}],
                                "evidence_gaps": [],
                                "confidence": {"level": "low"},
                            }
                        ],
                    },
                },
            }
        ],
    )
    claim = adapted["claims"][0]
    assert claim["supporting_evidence"] == ["hypothesis-evidence:E-support"]
    assert claim["opposing_evidence"] == ["hypothesis-evidence:E-oppose"]
    assert claim["limiting_evidence"] == ["hypothesis-evidence:E-limit"]
    assert adapted["evidence_refs"] == [
        "hypothesis-evidence:E-support",
        "hypothesis-evidence:E-oppose",
        "hypothesis-evidence:E-limit",
    ]
    assert adapted["limitations"] == ["E-limit"]


def test_hypothesis_checkpoint_exposes_virtual_evidence_and_limitations(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "work" / "scientific_hypothesis_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "evidence_register": [
                    {
                        "evidence_id": "E-support",
                        "role": "supports",
                        "excerpt": "A scoped supporting observation.",
                    },
                    {
                        "evidence_id": "E-limit",
                        "role": "limits",
                        "excerpt": "Only a small direct-observation subset is available.",
                    },
                ],
                "latest_draft": {
                    "response_kind": "hypotheses_ready",
                    "candidates": [
                        {
                            "id": "H1",
                            "statement": "A bounded candidate.",
                            "supporting_evidence": [
                                {"evidence_id": "E-support"},
                                {"evidence_id": "E-limit"},
                            ],
                            "opposing_evidence": [],
                            "evidence_gaps": [],
                            "confidence": {"level": "low"},
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    store = ResearchReviewStore(tmp_path, "hypothesis-projection-task")

    artifact = store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="rendered hypothesis result",
        phase="bounded_hypothesis",
        require_canonical_source=True,
    )

    assert artifact["evidence_refs"] == [
        "hypothesis-evidence:E-support",
        "hypothesis-evidence:E-limit",
    ]
    assert artifact["limitations"] == [
        "Only a small direct-observation subset is available."
    ]
    assert artifact["claims"][0]["supporting_evidence"] == [
        "hypothesis-evidence:E-support"
    ]
    assert artifact["claims"][0]["limiting_evidence"] == ["hypothesis-evidence:E-limit"]


def test_duplicate_producer_checkpoint_reuses_existing_artifact(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "idempotent-artifact-task")
    first = store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="same bounded plan"
    )
    second = store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="same bounded plan"
    )
    assert second == first
    assert len(store.artifacts(stage="planning")) == 1


def test_hypothesis_timestamp_only_change_does_not_create_substantive_version(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "work" / "scientific_hypothesis_state.json"
    state_path.parent.mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "updated_at": "2026-08-12T01:00:00+00:00",
        "evidence_register": [],
        "latest_draft": {
            "response_kind": "hypotheses_ready",
            "response_timestamp": "2026-08-12T01:00:00+00:00",
            "candidates": [
                {
                    "id": "H1",
                    "statement": "A bounded scientific candidate.",
                    "applicability": "One declared regime.",
                    "supporting_evidence": [],
                    "opposing_evidence": [],
                    "evidence_gaps": ["Direct evidence is unavailable."],
                    "confidence": {"level": "low"},
                }
            ],
        },
    }
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    store = ResearchReviewStore(tmp_path, "hypothesis-idempotence-task")
    first = store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="See work/scientific_hypothesis_state.json",
        phase="bounded_hypothesis",
        require_canonical_source=True,
    )

    payload["updated_at"] = "2026-08-12T02:00:00+00:00"
    payload["latest_draft"]["response_timestamp"] = "2026-08-12T02:00:00+00:00"
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    same = store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="Timestamp-only refresh in work/scientific_hypothesis_state.json",
        phase="bounded_hypothesis",
        require_canonical_source=True,
    )
    assert same == first
    assert len(store.artifacts(stage="hypothesis")) == 1

    payload["latest_draft"]["candidates"][0]["statement"] = (
        "A scientifically different candidate."
    )
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    changed = store.checkpoint_producer_result(
        stage="hypothesis",
        producer="solar-hypothesis",
        content="Scientific refresh in work/scientific_hypothesis_state.json",
        phase="bounded_hypothesis",
        require_canonical_source=True,
    )
    assert changed["version"] == 2


def test_task_local_document_search_returns_exact_sections(tmp_path: Path) -> None:
    source = tmp_path / "inputs" / "paper.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        "# Mechanism\n\nPolar field is a precursor, not proof of causal dominance.\n\n"
        "# Counterevidence\n\nA shared dataset does not provide independent replication.",
        encoding="utf-8",
    )
    store = ResearchReviewStore(tmp_path, "document-search-task")
    store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content="Inspect inputs/paper.md before making the plan.",
    )
    result = store.search_document(
        "planning", "inputs/paper.md", "polar field causal dominance"
    )
    assert result["search_gap"] is False
    section_id = result["hits"][0]["section_id"]
    read = store.read_document_sections("planning", "inputs/paper.md", [section_id])
    assert "causal dominance" in read["sections"][0]["text"]
