import json

import pytest

from jw.middleware.research_review_orchestration import (
    _load_portfolio_ranking_capsule,
    build_a2a_handoff_envelope,
)


def _persisted_portfolio_ranking() -> dict[str, object]:
    return {
        "schema_version": "scientific-hypothesis-portfolio-ranking-v2",
        "hypothesis_groups": [
            {
                "hypothesis_id": "hypothesis-low-support-high-priority",
                "normalized_statement": "A second measurement regime may reverse the effect.",
            },
            {
                "hypothesis_id": "hypothesis-high-support-low-priority",
                "normalized_statement": "The bounded descriptive direction is stable.",
            },
        ],
        "ranked_hypotheses": [
            {
                "hypothesis_id": "hypothesis-low-support-high-priority",
                "support_rank": 2,
                "research_priority_rank": 1,
                "scientific_support": {
                    "level": "low",
                    "rationale": "The current evidence is insufficient.",
                },
                "research_priority": {
                    "level": "high",
                    "rationale": "A feasible experiment cleanly separates the alternatives.",
                },
                "claim_type": "measurement_explanation",
                "current_evidence_status": "unsupported",
                "support_evidence": [],
                "opposing_evidence": [
                    {
                        "evidence_id": "negative-result",
                        "dependency_group_id": "measurement-one",
                        "relation": "the registered direction was not supported",
                    }
                ],
                "effect_uncertainty": {"interval_crosses_null": True},
                "sensitivity": {"measurement_regime": "fragile"},
                "key_limitations": ["Only one mixed-regime sample is available."],
                "falsifiability": {"status": "clear"},
                "strongest_null_hypothesis": "The apparent effect is measurement variation.",
                "next_experiment": {
                    "objective": "Repeat the measurement under a second regime.",
                    "discriminating_power": "The alternatives predict opposite changes.",
                    "feasibility": "executable_now",
                },
                "release_boundary": "Do not present the mechanism as established.",
                "portfolio_role": "physical_discriminator",
                "portfolio_status": "blocked_by_data",
                "forecast_origin": "cycle_minimum",
                "forecast_receipt_ref": None,
                "internal_evidence_details": ["must-not-cross-the-handoff"],
            },
            {
                "hypothesis_id": "hypothesis-high-support-low-priority",
                "support_rank": 1,
                "research_priority_rank": 2,
                "scientific_support": {
                    "level": "high",
                    "rationale": "Independent checks agree on the descriptive direction.",
                },
                "research_priority": {
                    "level": "low",
                    "rationale": "Another repetition has limited information value.",
                },
                "claim_type": "descriptive_relationship",
                "current_evidence_status": "supported",
                "support_evidence": [
                    {
                        "evidence_id": "descriptive-result",
                        "dependency_group_id": "historical-series-one",
                        "relation": "the registered direction is stable",
                    }
                ],
                "opposing_evidence": [],
                "effect_uncertainty": {"interval_crosses_null": False},
                "sensitivity": {"leave_one_out": "supports"},
                "key_limitations": ["The claim is descriptive and sample-bounded."],
                "falsifiability": {"status": "clear"},
                "strongest_null_hypothesis": "The direction vanishes outside this sample.",
                "next_experiment": {
                    "objective": "Validate on a future sample.",
                    "discriminating_power": "A held-out sample tests generalization.",
                    "feasibility": "requires_new_data",
                },
                "release_boundary": "Limit the conclusion to a descriptive association.",
                "portfolio_role": "empirical_anchor",
                "portfolio_status": "active_top3",
                "forecast_origin": "early_cycle",
                "forecast_receipt_ref": (
                    "experiment/runs/run-1/forecast_experiment_receipt.json"
                ),
            },
        ],
        "selected_next_experiment": {
            "hypothesis_ids": ["hypothesis-low-support-high-priority"],
            "objective": "Repeat the measurement under a second regime.",
            "discriminating_power": "The alternatives predict opposite changes.",
            "feasibility": "executable_now",
            "rationale": "This is the highest-value feasible discriminator.",
        },
    }


def test_a2a_handoff_envelope_carries_stage_contract_and_boundaries() -> None:
    envelope = build_a2a_handoff_envelope(
        task_id="task-1",
        action={
            "kind": "producer",
            "stage": "data",
            "phase": "bounded_data",
            "revision_review_id": None,
        },
        specialist="solar-data",
        analysis_protocol="silso_cycle_morphology_v1",
        accepted_upstream_refs=["planning-artifact@v1:abc"],
        data_context={
            "receipt_ref": "receipts/datasets/data-context.json",
            "context_sha256": "ctx",
            "must_stop": False,
            "eligible_inputs": [
                {
                    "dataset_id": "silso-cycle-extrema-v2",
                    "path": "data/extrema.txt",
                    "sha256": "abc",
                }
            ],
        },
    )

    assert envelope["schema_version"] == "a2a-handoff-v1"
    assert envelope["task_id"] == "task-1"
    assert envelope["owner"] == "solar-data"
    assert envelope["stage"] == "data"
    assert envelope["analysis_protocol"] == "silso_cycle_morphology_v1"
    assert envelope["accepted_upstream_refs"] == ["planning-artifact@v1:abc"]
    assert envelope["data_context"]["receipt_ref"].endswith("data-context.json")
    assert (
        envelope["data_context"]["eligible_inputs"][0]["dataset_id"]
        == "silso-cycle-extrema-v2"
    )
    assert "blocked" in envelope["return_contract"]["allowed_statuses"]
    assert "invent" in envelope["return_contract"]["hard_boundary"]
    assert "portfolio_ranking" not in envelope


def test_a2a_handoff_envelope_optionally_carries_only_minimal_portfolio_capsule() -> None:
    capsule = {
        "scientific_support": [{"hypothesis_id": "hypothesis-a", "rank": 1}],
        "research_priority": [{"hypothesis_id": "hypothesis-b", "rank": 1}],
        "strongest_null": [{"hypothesis_id": "hypothesis-b", "statement": "null"}],
        "next_experiment": {"objective": "discriminate a from b"},
        "release_boundary": [
            {"hypothesis_id": "hypothesis-a", "boundary": "association only"}
        ],
        "internal_debug": "must be dropped",
    }

    envelope = build_a2a_handoff_envelope(
        task_id="task-portfolio",
        action={"stage": "experiment_design", "phase": "initial"},
        specialist="solar-experiment",
        analysis_protocol="none",
        portfolio_ranking=capsule,
    )

    assert envelope["portfolio_ranking"] == {
        key: capsule[key]
        for key in (
            "scientific_support",
            "research_priority",
            "strongest_null",
            "next_experiment",
            "release_boundary",
        )
    }
    assert "internal_debug" not in envelope["portfolio_ranking"]


@pytest.mark.parametrize("stage", ["experiment_design", "integration", "final_release"])
def test_downstream_stage_loads_portfolio_capsule_from_working_state(
    tmp_path, stage: str
) -> None:
    state_path = tmp_path / "work" / "scientific_hypothesis_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "portfolio_ranking": _persisted_portfolio_ranking(),
                "portfolio_ranking_candidate_pool_sha256": "a" * 64,
                "portfolio_ranking_evidence_sha256": "b" * 64,
                "tail_review": {
                    "selected_candidate_pool_sha256": "a" * 64,
                    "evidence_sha256": "b" * 64,
                },
            }
        ),
        encoding="utf-8",
    )

    capsule = _load_portfolio_ranking_capsule(tmp_path, stage)

    assert capsule is not None
    assert list(capsule) == [
        "scientific_support",
        "research_priority",
        "strongest_null",
        "next_experiment",
        "release_boundary",
    ]
    assert [row["hypothesis_id"] for row in capsule["scientific_support"]] == [
        "hypothesis-high-support-low-priority",
        "hypothesis-low-support-high-priority",
    ]
    assert capsule["scientific_support"][0]["claim"] == (
        "The bounded descriptive direction is stable."
    )
    assert capsule["scientific_support"][0]["supporting_evidence"][0][
        "dependency_group_id"
    ] == "historical-series-one"
    assert capsule["scientific_support"][0]["portfolio_role"] == "empirical_anchor"
    assert capsule["scientific_support"][0]["portfolio_status"] == "active_top3"
    assert capsule["scientific_support"][0]["forecast_origin"] == "early_cycle"
    assert capsule["scientific_support"][0]["forecast_receipt_ref"] == (
        "experiment/runs/run-1/forecast_experiment_receipt.json"
    )
    assert capsule["scientific_support"][1]["opposing_evidence"][0][
        "evidence_id"
    ] == "negative-result"
    assert capsule["scientific_support"][1]["uncertainty"]["effect"] == {
        "interval_crosses_null": True
    }
    assert [row["hypothesis_id"] for row in capsule["research_priority"]] == [
        "hypothesis-low-support-high-priority",
        "hypothesis-high-support-low-priority",
    ]
    assert capsule["strongest_null"][0] == {
        "hypothesis_id": "hypothesis-high-support-low-priority",
        "statement": "The direction vanishes outside this sample.",
    }
    assert capsule["next_experiment"]["hypothesis_ids"] == [
        "hypothesis-low-support-high-priority"
    ]
    assert capsule["release_boundary"][0]["boundary"] == (
        "Limit the conclusion to a descriptive association."
    )


def test_non_downstream_stage_and_absent_ranking_preserve_legacy_handoff(
    tmp_path,
) -> None:
    state_path = tmp_path / "work" / "scientific_hypothesis_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "portfolio_ranking": _persisted_portfolio_ranking(),
                "portfolio_ranking_candidate_pool_sha256": "a" * 64,
                "portfolio_ranking_evidence_sha256": "b" * 64,
                "tail_review": {
                    "selected_candidate_pool_sha256": "a" * 64,
                    "evidence_sha256": "b" * 64,
                },
            }
        ),
        encoding="utf-8",
    )

    assert _load_portfolio_ranking_capsule(tmp_path, "hypothesis") is None
    assert _load_portfolio_ranking_capsule(tmp_path / "other", "integration") is None


def test_portfolio_sidecar_can_supply_an_already_minimal_capsule(tmp_path) -> None:
    capsule = {
        "scientific_support": [{"hypothesis_id": "hypothesis-a", "rank": 1}],
        "research_priority": [{"hypothesis_id": "hypothesis-a", "rank": 1}],
        "strongest_null": [{"hypothesis_id": "hypothesis-a", "statement": "null"}],
        "next_experiment": {"objective": "measure again"},
        "release_boundary": [
            {"hypothesis_id": "hypothesis-a", "boundary": "sample only"}
        ],
        "sidecar_only_metadata": "must be dropped",
    }
    sidecar = (
        tmp_path / "work" / "research_quality" / "hypothesis_portfolio_ranking.json"
    )
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(json.dumps(capsule), encoding="utf-8")

    loaded = _load_portfolio_ranking_capsule(tmp_path, "integration")

    assert loaded == {
        key: capsule[key]
        for key in (
            "scientific_support",
            "research_priority",
            "strongest_null",
            "next_experiment",
            "release_boundary",
        )
    }


def test_capsule_drops_absolute_forecast_receipt_path(tmp_path) -> None:
    ranking = _persisted_portfolio_ranking()
    ranking["ranked_hypotheses"][1]["forecast_receipt_ref"] = (
        "/tmp/private/forecast_experiment_receipt.json"
    )
    state_path = tmp_path / "work" / "scientific_hypothesis_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "portfolio_ranking": ranking,
                "portfolio_ranking_candidate_pool_sha256": "a" * 64,
                "portfolio_ranking_evidence_sha256": "b" * 64,
                "tail_review": {
                    "selected_candidate_pool_sha256": "a" * 64,
                    "evidence_sha256": "b" * 64,
                },
            }
        ),
        encoding="utf-8",
    )

    capsule = _load_portfolio_ranking_capsule(tmp_path, "integration")

    assert capsule is not None
    assert capsule["scientific_support"][0]["forecast_receipt_ref"] is None


def test_stale_portfolio_ranking_is_not_loaded(tmp_path) -> None:
    state_path = tmp_path / "work" / "scientific_hypothesis_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "portfolio_ranking": _persisted_portfolio_ranking(),
                "portfolio_ranking_candidate_pool_sha256": "a" * 64,
                "portfolio_ranking_evidence_sha256": "c" * 64,
                "tail_review": {
                    "selected_candidate_pool_sha256": "b" * 64,
                    "evidence_sha256": "c" * 64,
                },
            }
        ),
        encoding="utf-8",
    )

    assert _load_portfolio_ranking_capsule(tmp_path, "integration") is None
