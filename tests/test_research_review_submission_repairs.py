from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from jw import paths
from jw.research_review import ResearchReviewStore
from jw.tools.research_review import (
    _normalize_quality_submission,
    evidence_review_submit_round,
)
from jw.workspaces import ensure_thread_workspace


def test_quality_submission_downgrades_release_cap_when_gap_is_declared() -> None:
    rows = [
        {
            "claim_id": "claim-1",
            "claim_component": "statement",
            "quality_status": "release_candidate",
            "conclusion_cap": "release_candidate",
            "evidence_matrix": [
                {
                    "source_ref": "invented-gap-source",
                    "evidence_role": "gap",
                }
            ],
        }
    ]

    normalized, notes = _normalize_quality_submission(rows)

    assert normalized[0]["evidence_matrix"][0]["source_ref"] is None
    assert normalized[0]["quality_status"] == "evidence_constrained"
    assert normalized[0]["conclusion_cap"] == "evidence_constrained"
    assert notes


def test_quality_submission_downgrades_release_only_evidence_rows() -> None:
    rows = [
        {
            "claim_id": "claim-1",
            "claim_component": "statement",
            "quality_status": "evidence_constrained",
            "conclusion_cap": "evidence_constrained",
            "evidence_matrix": [
                {
                    "source_ref": "design.json",
                    "evidence_role": "supports",
                    "source_class": "simulation",
                    "evidence_scope": "experiment_record",
                    "directness": "indirect",
                    "scope_match": "matched",
                    "entailment": "entailed",
                    "quality_cap": "release_candidate",
                },
                {
                    "source_ref": "wiki.json",
                    "evidence_role": "limits",
                    "source_class": "wiki_context",
                    "evidence_scope": "wiki_entry",
                    "directness": "context_only",
                    "scope_match": "partial",
                    "entailment": "partial",
                    "quality_cap": "release_candidate",
                },
            ],
        }
    ]

    normalized, notes = _normalize_quality_submission(rows)

    assert normalized[0]["evidence_matrix"][0]["quality_cap"] == "evidence_constrained"
    assert normalized[0]["evidence_matrix"][1]["quality_cap"] == "exploratory"
    assert len(notes) == 2


def test_quality_submission_downgrades_ineligible_release_claim() -> None:
    rows = [
        {
            "claim_id": "claim-1",
            "claim_component": "statement",
            "load_bearing": True,
            "quality_status": "release_candidate",
            "conclusion_cap": "release_candidate",
            "key_gaps": ["The experiment has not been executed."],
            "novelty_assessment": {"status": "novelty_not_assessed"},
            "evidence_matrix": [
                {
                    "source_ref": "design.json",
                    "evidence_role": "supports",
                    "source_class": "data_documentation",
                    "evidence_scope": "experiment_record",
                    "directness": "indirect",
                    "scope_match": "matched",
                    "entailment": "entailed",
                    "quality_cap": "evidence_constrained",
                    "independence_group": "validated-design",
                }
            ],
        }
    ]

    normalized, notes = _normalize_quality_submission(rows)

    assert normalized[0]["quality_status"] == "evidence_constrained"
    assert normalized[0]["conclusion_cap"] == "evidence_constrained"
    assert notes


def _quality_claim(claim_id: str) -> dict[str, object]:
    return {
        "claim_id": claim_id,
        "claim_component": "statement",
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
            "notes": "No empirical analysis is asserted by this bounded claim.",
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


def _config(tmp_path: Path, monkeypatch, task_id: str) -> tuple[dict, Path]:
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "registry"))
    monkeypatch.setenv("JW_EVIDENCE_REVIEW_MODE", "two_pass")
    monkeypatch.setattr(paths, "WORKSPACE_ROOT", tmp_path)
    binding = ensure_thread_workspace(task_id, tmp_path)
    return (
        {
            "configurable": {
                "thread_id": task_id,
                "workspace_thread_id": task_id,
            }
        },
        Path(binding.workspace),
    )


def _external_lead_artifact(
    workspace: Path, task_id: str
) -> tuple[ResearchReviewStore, dict, str]:
    source_ref = f"research_review/harness/{task_id}/run/lead.json"
    source = workspace / source_ref
    source.parent.mkdir(parents=True)
    source.write_text('{"url":"https://example.test/lead"}', encoding="utf-8")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    envelope = {
        "schema_version": "harness-evidence-v1",
        "status": "completed",
        "task_id": task_id,
        "binding": {"task_id": task_id},
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
    store = ResearchReviewStore(workspace, task_id)
    artifact = store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content=json.dumps({"task_id": task_id, "harness_evidence": envelope}),
        phase="bounded_data",
    )
    return store, artifact, source_ref


def _assessment_claim(claim: dict, supporting_evidence: list[str]) -> dict[str, object]:
    return {
        "claim_id": claim["claim_id"],
        "kind": claim["kind"],
        "disposition": "supported",
        "supporting_evidence": supporting_evidence,
        "opposing_evidence": [],
        "rationale": "The reviewer relabeled the declared source as support.",
        "key_uncertainty": "The source role may cap this conclusion.",
        "confidence": "low",
        "next_test": "Inspect an eligible extracted page.",
    }


def _quality_supporting_claim(claim_id: str, source_ref: str) -> dict[str, object]:
    quality = _quality_claim(claim_id)
    quality["evidence_matrix"] = [
        {
            "source_ref": source_ref,
            "evidence_role": "supports",
            "source_class": "unknown",
            "evidence_scope": "unknown",
            "directness": "not_assessable",
            "scope_match": "not_assessable",
            "independence_group": "external-lead",
            "locator": "Search-result snippet only.",
            "entailment": "not_assessable",
            "quality_cap": "exploratory",
            "rationale": "The reviewer attempted to treat a lead as support.",
        }
    ]
    return quality


def test_atomic_round_rejects_external_lead_relabelled_as_support(
    tmp_path: Path, monkeypatch
) -> None:
    task_id = "external-lead-atomic"
    config, workspace = _config(tmp_path, monkeypatch, task_id)
    store, artifact, source_ref = _external_lead_artifact(workspace, task_id)
    claim = artifact["claims"][0]

    submitted = json.loads(
        evidence_review_submit_round.func(
            review_mode="data",
            assessment_review_mode="two_pass",
            assessment_claims=[_assessment_claim(claim, [source_ref])],
            scientific_quality_claims=[
                _quality_supporting_claim(claim["claim_id"], source_ref)
            ],
            decision="accept",
            issues=[],
            accepted_claims=[claim["claim_id"]],
            config=config,
        )
    )

    assert submitted["ok"] is False
    assert "provenance" in submitted["message"] or "gap" in submitted["message"]
    assert store.assessments(mode="data") == []
    assert store.scientific_quality_assessments(mode="data") == []
    assert store.verdicts(mode="data") == []


def test_quality_boundary_rejects_provenance_ref_as_support(
    tmp_path: Path,
) -> None:
    task_id = "external-lead-quality"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store, artifact, source_ref = _external_lead_artifact(workspace, task_id)
    claim = artifact["claims"][0]

    with pytest.raises(ValueError, match="provenance|gap"):
        store.record_scientific_quality_assessment(
            mode="data",
            assessment_review_mode="two_pass",
            claims=[_quality_supporting_claim(claim["claim_id"], source_ref)],
        )


def test_atomic_round_rejects_unknown_harness_ref_as_support(
    tmp_path: Path, monkeypatch
) -> None:
    task_id = "unknown-harness-atomic"
    config, workspace = _config(tmp_path, monkeypatch, task_id)
    store = ResearchReviewStore(workspace, task_id)
    artifact = store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="bounded plan"
    )
    claim = artifact["claims"][0]
    unknown_ref = f"research_review/harness/{task_id}/run/unknown.json"

    submitted = json.loads(
        evidence_review_submit_round.func(
            review_mode="planning",
            assessment_review_mode="two_pass",
            assessment_claims=[_assessment_claim(claim, [unknown_ref])],
            scientific_quality_claims=[
                _quality_supporting_claim(claim["claim_id"], unknown_ref)
            ],
            decision="accept",
            issues=[],
            accepted_claims=[claim["claim_id"]],
            config=config,
        )
    )

    assert submitted["ok"] is False
    assert "candidate" in submitted["message"]
    assert store.assessments(mode="planning") == []
    assert store.scientific_quality_assessments(mode="planning") == []
    assert store.verdicts(mode="planning") == []


def test_quality_boundary_rejects_unknown_harness_ref_as_support(
    tmp_path: Path,
) -> None:
    task_id = "unknown-harness-quality"
    store = ResearchReviewStore(tmp_path, task_id)
    artifact = store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="bounded plan"
    )
    claim = artifact["claims"][0]
    unknown_ref = f"research_review/harness/{task_id}/run/unknown.json"

    with pytest.raises(ValueError, match="candidate"):
        store.record_scientific_quality_assessment(
            mode="planning",
            assessment_review_mode="two_pass",
            claims=[_quality_supporting_claim(claim["claim_id"], unknown_ref)],
        )


@pytest.mark.parametrize("failure_stage", ["empty_accepted_claims", "late_verdict"])
def test_atomic_round_failure_preserves_preexisting_unbound_sidecars(
    tmp_path: Path, monkeypatch, failure_stage: str
) -> None:
    task_id = f"preserve-sidecars-{failure_stage}"
    config, workspace = _config(tmp_path, monkeypatch, task_id)
    store = ResearchReviewStore(workspace, task_id)
    artifact = store.checkpoint_producer_result(
        stage="planning", producer="solar-planner", content="bounded plan"
    )
    claim = artifact["claims"][0]
    prior_assessment = store.record_assessment(
        mode="planning",
        assessment_review_mode="two_pass",
        claims=[_assessment_claim(claim, ["receipts/prior.json"])],
    )
    prior_quality = store.record_scientific_quality_assessment(
        mode="planning",
        assessment_review_mode="two_pass",
        claims=[_quality_claim(claim["claim_id"])],
    )
    assessment_path = store.root / "assessments" / "planning-assessment-0001.json"
    quality_path = (
        store.root / "scientific_quality_assessments" / "planning-quality-0001.json"
    )
    original_assessment = assessment_path.read_bytes()
    original_quality = quality_path.read_bytes()
    kwargs = {
        "review_mode": "planning",
        "assessment_review_mode": "two_pass",
        "assessment_claims": [
            {
                **_assessment_claim(claim, ["receipts/replacement.json"]),
                "rationale": "Replacement that must roll back on failure.",
            }
        ],
        "scientific_quality_claims": [_quality_claim(claim["claim_id"])],
        "decision": "accept" if failure_stage == "empty_accepted_claims" else "revise",
        "issues": [],
        "config": config,
    }
    if failure_stage == "empty_accepted_claims":
        kwargs["accepted_claims"] = []

    submitted = json.loads(evidence_review_submit_round.func(**kwargs))

    assert submitted["ok"] is False
    assert assessment_path.read_bytes() == original_assessment
    assert quality_path.read_bytes() == original_quality
    assert store.assessments(mode="planning") == [prior_assessment]
    assert store.scientific_quality_assessments(mode="planning") == [prior_quality]
    assert store.verdicts(mode="planning") == []


@pytest.mark.parametrize("decision", ["accept", "accept_with_limits"])
@pytest.mark.parametrize("pass_empty_list", [False, True])
def test_accepting_round_requires_explicit_claim_ids_and_rolls_back(
    tmp_path: Path, monkeypatch, decision: str, pass_empty_list: bool
) -> None:
    task_id = f"explicit-claims-{decision}-{pass_empty_list}"
    monkeypatch.setenv("JW_WORKSPACE_BINDINGS_DIR", str(tmp_path / "registry"))
    monkeypatch.setattr(paths, "WORKSPACE_ROOT", tmp_path)
    binding = ensure_thread_workspace(task_id, tmp_path)
    config = {
        "configurable": {
            "thread_id": task_id,
            "workspace_thread_id": task_id,
        }
    }
    store = ResearchReviewStore(Path(binding.workspace), task_id)
    artifact = store.checkpoint_producer_result(
        stage="planning",
        producer="solar-planner",
        content="bounded plan at receipts/source.json",
    )
    claim = artifact["claims"][0]
    assessment_claim = {
        "claim_id": claim["claim_id"],
        "kind": claim["kind"],
        "disposition": "supported",
        "supporting_evidence": ["receipts/source.json"],
        "opposing_evidence": [],
        "rationale": "The bounded claim is accepted by this test reviewer.",
        "key_uncertainty": "No independent source was inspected.",
        "confidence": "low",
        "next_test": "Inspect an independent task-local source.",
    }
    kwargs = {
        "review_mode": "planning",
        "assessment_review_mode": "two_pass",
        "assessment_claims": [assessment_claim],
        "scientific_quality_claims": [_quality_claim(claim["claim_id"])],
        "decision": decision,
        "issues": [],
        "config": config,
    }
    if pass_empty_list:
        kwargs["accepted_claims"] = []
    if decision == "accept_with_limits":
        kwargs["carry_forward_limits"] = ["Independent source review is pending."]

    result = json.loads(evidence_review_submit_round.func(**kwargs))

    assert result["ok"] is False
    assert "accepted_claims" in result["message"]
    assert store.assessments(mode="planning") == []
    assert store.scientific_quality_assessments(mode="planning") == []
    assert store.verdicts(mode="planning") == []


def test_review_source_without_checkpoint_hash_is_not_a_match(tmp_path: Path) -> None:
    source = tmp_path / "receipts" / "data.json"
    source.parent.mkdir(parents=True)
    source.write_text('{"dataset":"SILSO"}', encoding="utf-8")
    store = ResearchReviewStore(tmp_path, "no-checkpoint-hash")
    store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content="verified receipt at receipts/data.json",
        phase="bounded_data",
    )

    reviewed = store.review_source("data", "receipts/data.json")

    assert reviewed["checkpoint_sha256"] is None
    assert reviewed["hash_matches_checkpoint"] is False
