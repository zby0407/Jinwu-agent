from __future__ import annotations

import pytest

from scientific_hypothesis.contracts import ContractError
from scientific_hypothesis.harness import EvidenceRegister
from scientific_hypothesis.ranking import (
    PORTFOLIO_RANKING_VERSION,
    validate_portfolio_ranking,
)


def _register() -> EvidenceRegister:
    register = EvidenceRegister()
    for evidence_id, material_id, role in (
        ("ev_descriptive", "verified-statistical-table", "supports"),
        ("ev_model_a", "forecast-model-a", "supports"),
        ("ev_model_b", "forecast-model-b", "supports"),
        ("ev_forecast_baseline", "forecast-mean-baseline", "opposes"),
        ("ev_negative", "interaction-result", "opposes"),
    ):
        register.bind(
            {
                "evidence_id": evidence_id,
                "evidence_kind": "experiment",
                "material_id": material_id,
                "excerpt": f"verified result for {material_id}",
                "verified_support": True,
                "role": role,
            }
        )
    for evidence_id, material_id, receipt_ref, observable_kind in (
        (
            "ev_h1_receipt",
            "h1-forecast-receipt",
            "experiment/runs/h1/forecast_experiment_receipt.json",
            "sunspot_rise_metric",
        ),
        (
            "ev_h2_receipt",
            "h2-forecast-receipt",
            "experiment/runs/h2/forecast_experiment_receipt.json",
            "polar_aperture_field",
        ),
        (
            "ev_h3_receipt",
            "h3-forecast-receipt",
            "experiment/runs/h3/forecast_experiment_receipt.json",
            "axial_dipole_moment",
        ),
    ):
        register.bind(
            {
                "evidence_id": evidence_id,
                "evidence_kind": "experiment",
                "material_id": material_id,
                "excerpt": (
                    '{"forecast_receipt_ref":"'
                    + receipt_ref
                    + '","observable_kind":"'
                    + observable_kind
                    + '","status":"verified"}'
                ),
                "verified_support": True,
                "role": "supports",
            }
        )
    return register


def _assessment(
    hypothesis_id: str,
    *,
    support_rank: int,
    priority_rank: int,
    claim_type: str = "descriptive_relationship",
    evidence_status: str = "supported",
    support_level: str = "high",
    priority_level: str = "medium",
    support_evidence: list[dict[str, str]] | None = None,
    opposing_evidence: list[dict[str, str]] | None = None,
    out_of_sample_status: str = "not_applicable",
    interval_crosses_null: bool | None = False,
) -> dict[str, object]:
    return {
        "hypothesis_id": hypothesis_id,
        "support_rank": support_rank,
        "research_priority_rank": priority_rank,
        "claim_type": claim_type,
        "current_evidence_status": evidence_status,
        "scientific_support": {
            "level": support_level,
            "rationale": "Calibrated to the verified evidence and its scope.",
        },
        "research_priority": {
            "level": priority_level,
            "rationale": "Prioritized for discrimination, feasibility, and uncertainty reduction.",
        },
        "data_sources_verified": True,
        "support_evidence": (
            support_evidence
            if support_evidence is not None
            else [
                {
                    "evidence_id": "ev_descriptive",
                    "dependency_group_id": "historical-series-one",
                    "relation": "supports the bounded descriptive direction",
                }
            ]
        ),
        "opposing_evidence": opposing_evidence if opposing_evidence is not None else [],
        "out_of_sample_validation": {
            "status": out_of_sample_status,
            "baseline_comparison": "No predictive claim is made.",
        },
        "effect_uncertainty": {
            "effect_summary": "A bounded effect estimate is available.",
            "interval_summary": "The registered interval is used.",
            "interval_crosses_null": interval_crosses_null,
        },
        "sensitivity": {
            "leave_one_out": "supports",
            "temporal_split": "supports",
            "measurement_regime": "not_tested",
            "definition": "not_tested",
        },
        "falsifiability": {
            "status": "clear",
            "conditions": [
                "A preregistered opposing direction would weaken the claim."
            ],
        },
        "key_limitations": ["The conclusion is bounded to the registered sample."],
        "strongest_null_hypothesis": "The observed association is sampling variation.",
        "next_experiment": {
            "objective": "Test the candidate against its strongest null.",
            "discriminating_power": "Opposing outcome branches update the candidates differently.",
            "feasibility": "executable_now",
        },
        "ranking_rationale": "Support rank and research priority are intentionally separate.",
        "release_boundary": "Do not generalize beyond the registered sample or claim causality.",
    }


def _payload() -> dict[str, object]:
    return {
        "schema_version": PORTFOLIO_RANKING_VERSION,
        "source_runs": ["run-a", "run-b"],
        "hypothesis_groups": [
            {
                "hypothesis_id": "relation",
                "normalized_statement": "A bounded observable has a descriptive association.",
                "member_candidates": [
                    {"run_id": "run-a", "candidate_id": "candidate-1"},
                    {"run_id": "run-b", "candidate_id": "synonym-1"},
                ],
                "deduplication_rationale": "The two statements make the same scoped claim.",
            },
            {
                "hypothesis_id": "interaction",
                "normalized_statement": "A proposed interaction is currently unsupported.",
                "member_candidates": [
                    {"run_id": "run-a", "candidate_id": "candidate-2"}
                ],
                "deduplication_rationale": "This is a distinct, falsifiable interaction claim.",
            },
        ],
        "ranked_hypotheses": [
            _assessment("relation", support_rank=1, priority_rank=2),
            _assessment(
                "interaction",
                support_rank=2,
                priority_rank=1,
                claim_type="mechanism_candidate",
                evidence_status="unsupported",
                support_level="low",
                priority_level="high",
                support_evidence=[],
                opposing_evidence=[
                    {
                        "evidence_id": "ev_negative",
                        "dependency_group_id": "interaction-study-one",
                        "relation": "the registered result opposes the proposed direction",
                    }
                ],
                interval_crosses_null=True,
            ),
        ],
        "selected_next_experiment": {
            "hypothesis_ids": ["relation", "interaction"],
            "objective": "Run the feasible test with the largest discrimination between candidates.",
            "discriminating_power": "The result separates a descriptive relation from the interaction claim.",
            "feasibility": "executable_now",
            "rationale": "High priority reflects information value, not current support.",
        },
    }


def test_negative_result_can_be_high_priority_without_becoming_high_support() -> None:
    ranking = validate_portfolio_ranking(_payload(), _register())

    interaction = ranking["ranked_hypotheses"][1]
    assert interaction["scientific_support"]["level"] == "low"
    assert interaction["research_priority"]["level"] == "high"
    assert interaction["independent_support_group_count"] == 0


def test_shared_dataset_models_count_as_one_independent_support_group() -> None:
    payload = _payload()
    predictive = payload["ranked_hypotheses"][0]
    predictive["claim_type"] = "predictive"
    predictive["scientific_support"]["level"] = "low"
    predictive["out_of_sample_validation"] = {
        "status": "tested_no_skill",
        "baseline_comparison": "Both models fail to beat the same historical mean baseline.",
    }
    predictive["support_evidence"] = [
        {
            "evidence_id": "ev_model_a",
            "dependency_group_id": "same-historical-series",
            "relation": "model A result",
        },
        {
            "evidence_id": "ev_model_b",
            "dependency_group_id": "same-historical-series",
            "relation": "model B result",
        },
    ]

    ranking = validate_portfolio_ranking(payload, _register())

    assert ranking["ranked_hypotheses"][0]["independent_support_group_count"] == 1


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda row: row.update(
                {
                    "support_evidence": [],
                    "scientific_support": {"level": "high", "rationale": "Novel idea."},
                }
            ),
            "高科学支持度必须有已核验支持证据",
        ),
        (
            lambda row: row.update(
                {
                    "claim_type": "predictive",
                    "out_of_sample_validation": {
                        "status": "tested_no_skill",
                        "baseline_comparison": "The candidate loses to baseline.",
                    },
                }
            ),
            "预测主张未胜过基线",
        ),
        (
            lambda row: row.update(
                {
                    "effect_uncertainty": {
                        "effect_summary": "wide estimate",
                        "interval_summary": "the interval crosses the null",
                        "interval_crosses_null": True,
                    }
                }
            ),
            "区间跨越零效应",
        ),
    ],
)
def test_high_support_is_capped_by_objective_evidence_gates(mutate, message) -> None:
    payload = _payload()
    mutate(payload["ranked_hypotheses"][0])

    with pytest.raises(ContractError, match=message):
        validate_portfolio_ranking(payload, _register())


def test_one_candidate_cannot_be_merged_into_multiple_semantic_groups() -> None:
    payload = _payload()
    payload["hypothesis_groups"][1]["member_candidates"].append(
        {"run_id": "run-a", "candidate_id": "candidate-1"}
    )

    with pytest.raises(ContractError, match="只能归入一个规范化假设"):
        validate_portfolio_ranking(payload, _register())


def test_current_three_hypothesis_fixture_keeps_support_and_priority_separate() -> None:
    payload = _payload()
    payload["hypothesis_groups"].insert(
        1,
        {
            "hypothesis_id": "forecast",
            "normalized_statement": "The next cycle peak is below the current cycle peak.",
            "member_candidates": [{"run_id": "run-b", "candidate_id": "forecast-1"}],
            "deduplication_rationale": "This is a distinct future predictive claim.",
        },
    )
    relation, interaction = payload["ranked_hypotheses"]
    relation["research_priority_rank"] = 3
    forecast = _assessment(
        "forecast",
        support_rank=2,
        priority_rank=2,
        claim_type="predictive",
        evidence_status="mixed",
        support_level="low",
        priority_level="high",
        support_evidence=[
            {
                "evidence_id": "ev_model_a",
                "dependency_group_id": "same-historical-series",
                "relation": "provides a frozen point estimate and interval",
            }
        ],
        opposing_evidence=[
            {
                "evidence_id": "ev_forecast_baseline",
                "dependency_group_id": "same-historical-series",
                "relation": "the candidate did not beat the mean baseline",
            }
        ],
        out_of_sample_status="tested_no_skill",
        interval_crosses_null=None,
    )
    interaction["support_rank"] = 3
    interaction["research_priority_rank"] = 1
    payload["ranked_hypotheses"] = [relation, forecast, interaction]
    payload["selected_next_experiment"]["hypothesis_ids"] = ["interaction"]
    payload["selected_next_experiment"]["objective"] = (
        "Calibrate the two measurement regimes and repeat the frozen interaction test."
    )

    ranking = validate_portfolio_ranking(payload, _register())
    by_id = {row["hypothesis_id"]: row for row in ranking["ranked_hypotheses"]}

    assert by_id["relation"]["support_rank"] == 1
    assert by_id["relation"]["scientific_support"]["level"] == "high"
    assert by_id["forecast"]["scientific_support"]["level"] == "low"
    assert by_id["forecast"]["out_of_sample_validation"]["status"] == (
        "tested_no_skill"
    )
    assert by_id["interaction"]["scientific_support"]["level"] == "low"
    assert by_id["interaction"]["research_priority_rank"] == 1
    assert ranking["selected_next_experiment"]["hypothesis_ids"] == ["interaction"]


def _active_payload(roles: list[str]) -> dict[str, object]:
    receipt_by_role = {
        "empirical_anchor": "experiment/runs/h1/forecast_experiment_receipt.json",
        "physical_precursor": "experiment/runs/h2/forecast_experiment_receipt.json",
        "physical_discriminator": "experiment/runs/h3/forecast_experiment_receipt.json",
        "challenger": "experiment/runs/h2/forecast_experiment_receipt.json",
    }
    origin_by_role = {
        "empirical_anchor": "early_cycle",
        "physical_precursor": "cycle_minimum",
        "physical_discriminator": "cycle_minimum",
        "challenger": "not_applicable",
    }
    payload = _payload()
    payload["source_runs"] = ["run-a"]
    payload["hypothesis_groups"] = []
    payload["ranked_hypotheses"] = []
    for index, role in enumerate(roles, start=1):
        hypothesis_id = f"active-{index}"
        payload["hypothesis_groups"].append(
            {
                "hypothesis_id": hypothesis_id,
                "normalized_statement": f"Bounded predictive candidate {index}.",
                "member_candidates": [
                    {"run_id": "run-a", "candidate_id": f"candidate-{index}"}
                ],
                "deduplication_rationale": "Distinct forecast role.",
            }
        )
        row = _assessment(
            hypothesis_id,
            support_rank=index,
            priority_rank=index,
            claim_type="predictive",
            support_level="low",
            out_of_sample_status="beats_baseline",
        )
        row.update(
            portfolio_role=role,
            portfolio_status="active_top3",
            forecast_origin=origin_by_role[role],
            forecast_receipt_ref=receipt_by_role[role],
        )
        payload["ranked_hypotheses"].append(row)
    payload["selected_next_experiment"]["hypothesis_ids"] = ["active-1"]
    return payload


def test_legacy_rows_receive_non_active_lifecycle_defaults() -> None:
    ranking = validate_portfolio_ranking(_payload(), _register())

    assert all(
        row["portfolio_role"] == "challenger"
        and row["portfolio_status"] == "challenger_pool"
        and row["forecast_origin"] == "not_applicable"
        and row["forecast_receipt_ref"] is None
        for row in ranking["ranked_hypotheses"]
    )


def test_tested_no_skill_cannot_remain_active_top3() -> None:
    payload = _active_payload(["physical_precursor"])
    row = payload["ranked_hypotheses"][0]
    row["out_of_sample_validation"]["status"] = "tested_no_skill"

    with pytest.raises(ContractError, match="active_top3"):
        validate_portfolio_ranking(payload, _register())


def test_blocked_axial_candidate_stays_visible_but_not_active() -> None:
    payload = _payload()
    row = payload["ranked_hypotheses"][1]
    row.update(
        portfolio_role="physical_discriminator",
        portfolio_status="blocked_by_data",
        forecast_origin="cycle_minimum",
        forecast_receipt_ref=None,
    )

    ranking = validate_portfolio_ranking(payload, _register())

    assert ranking["ranked_hypotheses"][1]["portfolio_status"] == "blocked_by_data"


def test_active_top3_is_bounded_and_roles_are_unique() -> None:
    payload = _active_payload(
        [
            "empirical_anchor",
            "physical_precursor",
            "physical_discriminator",
            "challenger",
        ]
    )
    with pytest.raises(ContractError, match="最多三个"):
        validate_portfolio_ranking(payload, _register())

    payload = _active_payload(
        ["empirical_anchor", "physical_precursor", "physical_precursor"]
    )
    with pytest.raises(ContractError, match="portfolio_role"):
        validate_portfolio_ranking(payload, _register())


@pytest.mark.parametrize(
    ("role", "origin"),
    [
        ("empirical_anchor", "cycle_minimum"),
        ("physical_precursor", "early_cycle"),
    ],
)
def test_role_rejects_wrong_forecast_origin(role: str, origin: str) -> None:
    payload = _payload()
    payload["ranked_hypotheses"][0].update(
        portfolio_role=role,
        portfolio_status="candidate_pending_test",
        forecast_origin=origin,
        forecast_receipt_ref=None,
    )

    with pytest.raises(ContractError, match="forecast_origin"):
        validate_portfolio_ranking(payload, _register())


def test_axial_role_rejects_polar_aperture_receipt() -> None:
    payload = _payload()
    payload["ranked_hypotheses"][0].update(
        portfolio_role="physical_discriminator",
        portfolio_status="candidate_pending_test",
        forecast_origin="cycle_minimum",
        forecast_receipt_ref=(
            "experiment/runs/h2/forecast_experiment_receipt.json"
        ),
    )

    with pytest.raises(ContractError, match="axial_dipole_moment"):
        validate_portfolio_ranking(payload, _register())
