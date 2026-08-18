from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVAL_ROOT = ROOT / "research" / "review" / "evals"


def test_checked_in_challenge_suite_has_twelve_non_hidden_cases() -> None:
    suite = json.loads(
        (EVAL_ROOT / "full_research_heldout_v1.json").read_text(encoding="utf-8")
    )
    cases = suite["cases"]

    assert suite["exposure_status"] == "checked_in_visible"
    assert suite["release_gate_eligible"] is False
    assert len(cases) == 12
    assert len({case["id"] for case in cases}) == 12
    categories = {case["category"] for case in cases}
    assert {
        "mechanism",
        "prediction",
        "backtest",
        "data_conflict",
        "null_result",
        "insufficient_evidence",
        "cross_agent_contradiction",
    } <= categories
    assert all(case["required_stressors"] for case in cases)


def test_evaluation_policy_freezes_four_variants_and_release_thresholds() -> None:
    policy = json.loads(
        (EVAL_ROOT / "evaluation_policy_v2.json").read_text(encoding="utf-8")
    )

    assert set(policy["variants"]) == {"A", "B", "C", "D"}
    assert policy["reviewer_calibration"]["critical_false_release_max"] == 0
    assert policy["reviewer_calibration"]["major_recall_min"] == 0.9
    assert policy["adaptive_promotion"]["max_cost_ratio_vs_fixed_3"] == 2.0
    assert policy["hidden_release_suite"]["minimum_cases_per_variant"] == 12
    assert policy["hidden_release_suite"]["prompt_storage"] == "external_to_repository"
    assert policy["high_quality_review_gate"]["evidence_matrix_coverage_min"] == 1.0
    assert policy["high_quality_review_gate"]["external_hidden_cases_min"] == 12


def test_high_quality_visible_suite_has_ten_frozen_non_release_cases() -> None:
    suite = json.loads(
        (EVAL_ROOT / "high_quality_review_visible_v1.json").read_text(encoding="utf-8")
    )

    assert suite["visibility"] == "checked_in_visible"
    assert suite["release_gate_eligible"] is False
    assert len(suite["cases"]) == 10
    assert len({case["id"] for case in suite["cases"]}) == 10
    assert suite["implementation_baseline"]["fresh_8_12_1_runs_required"] is True
    assert (
        suite["implementation_baseline"]["historical_worktree_results_forbidden"]
        is True
    )
    assert suite["automated_hard_gates"]["stable_repetitions_required"] == 3
    assert suite["external_annotation_metrics"]["minimum_score_each"] == 80


def test_high_quality_scorer_refuses_to_claim_external_validation() -> None:
    script = EVAL_ROOT / "score_high_quality_records.py"
    spec = importlib.util.spec_from_file_location("score_high_quality_records", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    signature = {
        "conclusion": "evidence bounded",
        "release_class": "evidence_constrained",
        "evidence_limit_preservation": "unsupported priority is omitted",
    }
    rows = []
    for case_id in sorted(module.EXPECTED_CASES):
        for phase, repetitions, blind_score in (
            ("baseline", (1,), 80),
            ("post_freeze", (1, 2, 3), 86),
        ):
            for repetition in repetitions:
                rows.append(
                    {
                        "case_id": case_id,
                        "implementation_phase": phase,
                        "repetition": repetition,
                        "scientific_conclusion_signature": signature,
                        "workflow_status_misclassifications": 0,
                        "unsupported_critical_claims": 0,
                        "false_novelty_priority": 0,
                        "load_bearing_claims": 1,
                        "matrix_covered_claims": 1,
                        "seeded_conflicts_total": 1,
                        "seeded_conflicts_detected": 1,
                        "major_defects_total": 1,
                        "major_defects_detected": 1,
                        "potentially_novel_count": 0,
                        "domain_novelty_confirmed_count": 0,
                        "negative_outcome_expected": True,
                        "negative_outcome_preserved": True,
                        "external_annotation": {
                            "role_entailment_total": 1,
                            "role_entailment_correct": 1,
                            "blind_scores": {
                                "domain": blind_score,
                                "methods_statistics": blind_score,
                                "reproducibility": blind_score,
                            },
                            "unresolved_critical": 0,
                        },
                    }
                )
    result = module.score(rows)

    assert result["visible_suite_passed"] is True
    assert result["scientific_capability_validated"] is False


def test_evaluation_scorer_enforces_calibration_and_cost_gate() -> None:
    script = EVAL_ROOT / "score_review_records.py"
    spec = importlib.util.spec_from_file_location("score_review_records", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    common = {
        "critical_total": 2,
        "critical_missed": 0,
        "major_total": 10,
        "major_detected": 10,
        "stale_approval_blocked": True,
        "unreviewed_claim_blocked": True,
        "cost": 10,
        "p95_latency_seconds": 100,
        "hard_gates_passed": True,
        "unresolved_critical": 0,
        "real_experiment_reproducible": True,
        "all_visible_claims_traceable": True,
        "blind_review_votes": {
            "domain": True,
            "methods_statistics": True,
            "reproducibility": True,
        },
    }
    rows = []
    variant_scores = {
        "A": (70, 7),
        "B": (75, 6),
        "C": (86, 3),
        "D": (80, 5),
    }
    for index in range(12):
        for variant, (blind_score, unresolved) in variant_scores.items():
            rows.append(
                {
                    **common,
                    "variant": variant,
                    "case_id": f"hidden-{index:02d}",
                    "suite_visibility": "external_hidden",
                    "blind_score": blind_score,
                    "unresolved_major_critical": unresolved,
                }
            )
    result = module.score(rows)

    assert result["adaptive_promotion"]["calibration_passed"] is True
    assert result["adaptive_promotion"]["adaptive_default_allowed"] is True
    assert result["top_journal_candidate_gate"] == {
        "candidate_variant": "C",
        "evaluable": True,
        "criteria": {
            "external_hidden_suite": True,
            "all_hard_gates": True,
            "unresolved_critical_zero": True,
            "real_experiments_reproducible": True,
            "all_visible_claims_traceable": True,
            "blind_review_majority": True,
        },
        "passed": True,
    }


def test_visible_challenge_records_cannot_promote_adaptive() -> None:
    script = EVAL_ROOT / "score_review_records.py"
    spec = importlib.util.spec_from_file_location("score_visible_records", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = [
        {
            "variant": variant,
            "case_id": f"visible-{index:02d}",
            "suite_visibility": "checked_in_visible",
            "critical_total": 0,
            "critical_missed": 0,
            "major_total": 0,
            "major_detected": 0,
            "stale_approval_blocked": True,
            "unreviewed_claim_blocked": True,
            "blind_score": 100,
            "unresolved_major_critical": 0,
            "cost": 1,
            "p95_latency_seconds": 1,
        }
        for variant in ("A", "B", "C", "D")
        for index in range(12)
    ]

    result = module.score(rows)

    assert result["adaptive_promotion"]["hidden_release_eligible"] is False
    assert result["adaptive_promotion"]["adaptive_default_allowed"] is False
    assert result["top_journal_candidate_gate"]["passed"] is False


def test_hidden_scorer_rejects_duplicate_or_incomplete_records() -> None:
    script = EVAL_ROOT / "score_review_records.py"
    spec = importlib.util.spec_from_file_location("score_invalid_records", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    incomplete = {
        "variant": "C",
        "case_id": "hidden-01",
        "suite_visibility": "external_hidden",
        "critical_total": 0,
        "critical_missed": 0,
        "major_total": 0,
        "major_detected": 0,
        "stale_approval_blocked": True,
        "unreviewed_claim_blocked": True,
        "blind_score": 100,
        "unresolved_major_critical": 0,
        "cost": 1,
        "latency_seconds": 1,
    }

    with pytest.raises(ValueError, match="hard_gates_passed"):
        module.score([incomplete])

    visible = {
        **incomplete,
        "suite_visibility": "checked_in_visible",
    }
    with pytest.raises(ValueError, match="duplicate"):
        module.score([visible, dict(visible)])
