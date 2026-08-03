from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scientific_hypothesis.contracts import ContractError  # noqa: E402
from scientific_hypothesis.tail_search import (  # noqa: E402
    BENEFIT_METRICS,
    GENERAL_GUIDELINES,
    RUBRIC_DEFINITIONS,
    RUBRIC_ITEMS,
    TAIL_METRIC_ANCHORS,
    TAIL_REVIEW_VERSION,
    candidate_pool_sha256,
    tail_review_is_current,
    tail_review_scoring_guide,
    validate_and_select_tail_review,
)


def _candidate(candidate_id: str) -> dict[str, object]:
    return {
        "id": candidate_id,
        "statement": f"Mechanism proposed by {candidate_id}",
        "mechanism": {"summary": f"Distinct mechanism for {candidate_id}"},
    }


def _row(
    candidate_id: str,
    *,
    operator: str,
    region: str,
    signature: str,
    benefits: str = "medium",
    evidence_risk: str = "medium",
    test_cost: str = "medium",
    violation: str | None = None,
) -> dict[str, object]:
    rubric = {
        key: {
            "status": "violation" if key == violation else "pass",
            "violated_guidelines": (
                ["handles_all_criteria"] if key == violation else []
            ),
            "rationale": (
                f"Reviewer checked {key} against the candidate's explicit fields."
            ),
        }
        for key in RUBRIC_ITEMS
    }
    metrics = dict.fromkeys(BENEFIT_METRICS, benefits)
    metrics.update({"evidence_risk": evidence_risk, "test_cost": test_cost})
    return {
        "candidate_id": candidate_id,
        "generation_operator": operator,
        "search_region": region,
        "mechanism_signature": signature,
        "novelty_status": (
            "known_baseline"
            if region == "modal_baseline"
            else "tail_candidate_unverified"
        ),
        "rubric": rubric,
        "tail_metrics": metrics,
        "reviewer_summary": (
            f"Independent violation-first review of {candidate_id} is complete."
        ),
    }


def _review(
    draft: dict[str, object], rows: list[dict[str, object]]
) -> dict[str, object]:
    instance_rubrics = [
        {
            "id": f"ir_{row['candidate_id']}",
            "candidate_id": row["candidate_id"],
            "criterion": (
                f"The candidate {row['candidate_id']} must address the "
                "question-specific observable contrast."
            ),
            "basis": "Derived from the bound question and candidate contrast.",
            "status": "pass",
            "violated_guidelines": [],
            "rationale": "The candidate contains a directly discriminating prediction.",
        }
        for row in rows
    ]
    return {
        "schema_version": TAIL_REVIEW_VERSION,
        "candidate_pool_sha256": candidate_pool_sha256(draft),
        "reviewer_mode": "independent_violation_first",
        "instance_rubrics": instance_rubrics,
        "candidates": rows,
    }


def test_pareto_selection_preserves_sparse_two_sided_tail_and_null_sentinel() -> None:
    draft = {
        "candidates": [
            _candidate("H_baseline"),
            _candidate("H_positive"),
            _candidate("H_negative"),
            _candidate("H_null"),
            _candidate("H_dominated"),
        ]
    }
    rows = [
        _row(
            "H_baseline",
            operator="modal_baseline",
            region="modal_baseline",
            signature="baseline direct driver",
            benefits="medium",
            evidence_risk="low",
            test_cost="low",
        ),
        _row(
            "H_positive",
            operator="latent_driver",
            region="positive_tail",
            signature="latent driver activates response",
            benefits="high",
            evidence_risk="high",
            test_cost="high",
        ),
        _row(
            "H_negative",
            operator="causal_reversal",
            region="negative_tail",
            signature="reverse pathway suppresses response",
            benefits="high",
            evidence_risk="medium",
            test_cost="high",
        ),
        _row(
            "H_null",
            operator="measurement_null",
            region="null_control",
            signature="measurement proxy creates signal",
            benefits="low",
            evidence_risk="low",
            test_cost="low",
        ),
        _row(
            "H_dominated",
            operator="residual_anomaly",
            region="positive_tail",
            signature="weak residual extension candidate",
            benefits="low",
            evidence_risk="high",
            test_cost="high",
        ),
    ]

    outcome = validate_and_select_tail_review(
        _review(draft, rows),
        draft,
        evidence_sha256="evidence-hash",
    )

    assert "H_negative" in outcome["pareto_frontier_ids"]
    assert "H_positive" not in outcome["pareto_frontier_ids"]
    assert outcome["regional_frontier_ids"]["positive_tail"] == ["H_positive"]
    assert "H_dominated" in outcome["dominated_candidate_ids"]
    assert "H_null" in outcome["sentinel_candidate_ids"]
    assert "H_null" in outcome["selected_candidate_ids"]
    assert "H_positive" in outcome["selected_candidate_ids"]
    assert "H_dominated" not in outcome["selected_candidate_ids"]


def test_hard_violation_cannot_be_offset_by_high_tail_metrics() -> None:
    draft = {"candidates": [_candidate("H_baseline"), _candidate("H_speculative")]}
    rows = [
        _row(
            "H_baseline",
            operator="modal_baseline",
            region="modal_baseline",
            signature="bounded baseline mechanism",
        ),
        _row(
            "H_speculative",
            operator="premise_reversal",
            region="negative_tail",
            signature="unbounded speculative reversal",
            benefits="high",
            evidence_risk="low",
            test_cost="low",
            violation="boundary_completeness",
        ),
    ]

    outcome = validate_and_select_tail_review(
        _review(draft, rows),
        draft,
        evidence_sha256="evidence-hash",
    )

    assert outcome["rejected_candidate_ids"] == ["H_speculative"]
    speculative = next(
        row for row in outcome["candidates"] if row["candidate_id"] == "H_speculative"
    )
    assert speculative["rubric_reward"] == pytest.approx(7 / 8)
    assert speculative["hard_gate_passed"] is False
    assert "H_speculative" not in outcome["pareto_frontier_ids"]
    assert "H_speculative" not in outcome["selected_candidate_ids"]


def test_mechanism_distance_alone_cannot_dominate_adjacent_candidate() -> None:
    draft = {"candidates": [_candidate("H_adjacent"), _candidate("H_far")]}
    rows = [
        _row(
            "H_adjacent",
            operator="latent_driver",
            region="positive_tail",
            signature="adjacent latent driver mechanism",
        ),
        _row(
            "H_far",
            operator="regime_boundary",
            region="positive_tail",
            signature="remote regime boundary mechanism",
        ),
    ]
    rows[0]["tail_metrics"]["mechanism_distance"] = "medium"
    rows[1]["tail_metrics"]["mechanism_distance"] = "high"

    outcome = validate_and_select_tail_review(
        _review(draft, rows),
        draft,
        evidence_sha256="evidence-hash",
    )

    assert outcome["regional_frontier_ids"]["positive_tail"] == [
        "H_adjacent",
        "H_far",
    ]
    assert outcome["dominated_candidate_ids"] == []
    assert (
        "cannot dominate another candidate"
        in outcome["selection_policy"]["tail_metric_use"]
    )


def test_instance_rubric_basis_cannot_only_repeat_task_requirements() -> None:
    draft = {"candidates": [_candidate("H1"), _candidate("H2")]}
    rows = [
        _row(
            "H1",
            operator="modal_baseline",
            region="modal_baseline",
            signature="baseline mechanism with observable response",
        ),
        _row(
            "H2",
            operator="premise_reversal",
            region="negative_tail",
            signature="premise reversal with observable response",
        ),
    ]
    payload = _review(draft, rows)
    payload["instance_rubrics"][1]["basis"] = (
        "绑定问题要求每个候选必须给出削弱条件和边界字段。"
    )

    with pytest.raises(ContractError, match="只复述了任务或格式要求"):
        validate_and_select_tail_review(
            payload,
            draft,
            evidence_sha256="evidence-hash",
        )


def test_instance_specific_violation_is_also_a_hard_gate() -> None:
    draft = {"candidates": [_candidate("H1"), _candidate("H2")]}
    rows = [
        _row(
            "H1",
            operator="modal_baseline",
            region="modal_baseline",
            signature="question baseline mechanism",
        ),
        _row(
            "H2",
            operator="premise_reversal",
            region="negative_tail",
            signature="question premise reversal",
            benefits="high",
            evidence_risk="low",
            test_cost="low",
        ),
    ]
    payload = _review(draft, rows)
    payload["instance_rubrics"][1]["status"] = "violation"
    payload["instance_rubrics"][1]["violated_guidelines"] = ["handles_all_criteria"]
    payload["instance_rubrics"][1]["rationale"] = (
        "The candidate does not address the question-specific temporal contrast."
    )

    outcome = validate_and_select_tail_review(
        payload,
        draft,
        evidence_sha256="evidence-hash",
    )
    candidate = next(
        row for row in outcome["candidates"] if row["candidate_id"] == "H2"
    )

    assert candidate["rubric_reward"] == pytest.approx(7 / 8)
    assert candidate["hard_gate_passed"] is False
    assert outcome["rejected_candidate_ids"] == ["H2"]


def test_scoring_guide_has_detailed_rubrics_guidelines_and_metric_anchors() -> None:
    guide = tail_review_scoring_guide()

    assert set(guide["general_guidelines"]) == set(GENERAL_GUIDELINES)
    assert set(guide["scientific_rubrics"]) == set(RUBRIC_ITEMS)
    assert set(guide["tail_metric_anchors"]) == {
        *BENEFIT_METRICS,
        "evidence_risk",
        "test_cost",
    }
    for definition in RUBRIC_DEFINITIONS.values():
        assert definition["criterion"]
        assert len(definition["pass_when"]) >= 3
        assert len(definition["violation_when"]) >= 3
        assert definition["edge_rule"]
    for anchors in TAIL_METRIC_ANCHORS.values():
        assert anchors["low"]
        assert anchors["medium"]
        assert anchors["high"]


def test_rubric_status_must_match_violation_codes() -> None:
    draft = {"candidates": [_candidate("H1")]}
    rows = [
        _row(
            "H1",
            operator="modal_baseline",
            region="modal_baseline",
            signature="bounded baseline mechanism",
        )
    ]
    payload = _review(draft, rows)
    payload["candidates"][0]["rubric"]["falsifiability"]["violated_guidelines"] = [
        "detailed_and_specific"
    ]

    with pytest.raises(ContractError, match="只有零违规才能 pass"):
        validate_and_select_tail_review(
            payload,
            draft,
            evidence_sha256="evidence-hash",
        )


def test_review_rejects_duplicate_mechanism_signatures() -> None:
    draft = {"candidates": [_candidate("H1"), _candidate("H2")]}
    rows = [
        _row(
            "H1",
            operator="modal_baseline",
            region="modal_baseline",
            signature="same mechanism signature",
        ),
        _row(
            "H2",
            operator="premise_reversal",
            region="negative_tail",
            signature=" SAME   mechanism SIGNATURE ",
        ),
    ]

    with pytest.raises(ContractError, match="同义改写"):
        validate_and_select_tail_review(
            _review(draft, rows),
            draft,
            evidence_sha256="evidence-hash",
        )


def test_four_candidate_pool_requires_both_tail_regions() -> None:
    draft = {"candidates": [_candidate(f"H{i}") for i in range(4)]}
    rows = [
        _row(
            f"H{i}",
            operator="modal_baseline" if i == 0 else "residual_anomaly",
            region="positive_tail" if i else "modal_baseline",
            signature=f"mechanism signature number {i}",
        )
        for i in range(4)
    ]

    with pytest.raises(ContractError, match="negative_tail"):
        validate_and_select_tail_review(
            _review(draft, rows),
            draft,
            evidence_sha256="evidence-hash",
        )


def test_explicit_long_tail_search_cannot_shrink_away_one_side() -> None:
    draft = {"candidates": [_candidate("H_baseline"), _candidate("H_negative")]}
    rows = [
        _row(
            "H_baseline",
            operator="modal_baseline",
            region="modal_baseline",
            signature="conventional baseline mechanism",
        ),
        _row(
            "H_negative",
            operator="premise_reversal",
            region="negative_tail",
            signature="reversed premise mechanism",
        ),
    ]

    with pytest.raises(ContractError, match="不能通过缩小候选池"):
        validate_and_select_tail_review(
            _review(draft, rows),
            draft,
            evidence_sha256="evidence-hash",
            require_two_sided_tail=True,
        )


def test_review_currency_tracks_selected_pool_and_evidence() -> None:
    draft = {"candidates": [_candidate("H1"), _candidate("H2")]}
    rows = [
        _row(
            "H1",
            operator="modal_baseline",
            region="modal_baseline",
            signature="baseline signal pathway",
        ),
        _row(
            "H2",
            operator="premise_reversal",
            region="negative_tail",
            signature="reversed signal pathway",
            benefits="high",
            evidence_risk="high",
            test_cost="high",
        ),
    ]
    outcome = validate_and_select_tail_review(
        _review(draft, rows),
        draft,
        evidence_sha256="evidence-hash",
    )
    selected = {
        "candidates": [
            candidate
            for candidate in draft["candidates"]
            if candidate["id"] in outcome["selected_candidate_ids"]
        ]
    }
    outcome["selected_candidate_pool_sha256"] = candidate_pool_sha256(selected)

    assert tail_review_is_current(outcome, selected, evidence_sha256="evidence-hash")
    changed = deepcopy(selected)
    changed["candidates"][0]["statement"] = "Changed after review"
    assert not tail_review_is_current(outcome, changed, evidence_sha256="evidence-hash")
    assert not tail_review_is_current(
        outcome, selected, evidence_sha256="new-evidence-hash"
    )


def test_model_cannot_submit_its_own_selected_winner() -> None:
    draft = {"candidates": [_candidate("H1"), _candidate("H2")]}
    rows = [
        _row(
            "H1",
            operator="modal_baseline",
            region="modal_baseline",
            signature="baseline pathway signature",
        ),
        _row(
            "H2",
            operator="causal_reversal",
            region="negative_tail",
            signature="causal reversal signature",
        ),
    ]
    payload = _review(draft, rows)
    payload["selected_candidate_ids"] = ["H1"]

    with pytest.raises(ContractError, match="未知字段"):
        validate_and_select_tail_review(
            payload,
            draft,
            evidence_sha256="evidence-hash",
        )
