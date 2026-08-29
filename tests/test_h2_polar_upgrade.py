from __future__ import annotations

import pytest

from jw.solar_forecast.h2_upgrade import run_h2_upgrade


def _finalized_rows() -> list[dict[str, object]]:
    cycles = range(15, 25)
    polar_mean = [
        1.413550,
        1.562750,
        0.895355,
        1.755450,
        2.189550,
        1.267580,
        1.215000,
        1.585000,
        1.220000,
        0.730000,
    ]
    weakest = [
        1.117800,
        1.486800,
        0.792250,
        1.454300,
        2.083700,
        0.981760,
        1.170000,
        1.330000,
        1.200000,
        0.670000,
    ]
    targets = [
        175.666667,
        130.229167,
        198.641667,
        218.733333,
        285.004167,
        156.629167,
        232.916667,
        212.483333,
        180.275000,
        116.425000,
    ]
    dispersions = [
        11.822207,
        10.167042,
        12.568943,
        10.328924,
        11.298599,
        8.350873,
        10.213043,
        12.679117,
        10.823778,
        8.238755,
    ]
    return [
        {
            "target_cycle_id": cycle,
            "polar_mean_abs_gauss": mean,
            "weakest_hemisphere_abs_gauss": weak,
            "target": target,
            "target_dispersion": dispersion,
            "measurement_regime": "MWO" if cycle <= 20 else "WSO",
        }
        for cycle, mean, weak, target, dispersion in zip(
            cycles,
            polar_mean,
            weakest,
            targets,
            dispersions,
            strict=True,
        )
    ]


def _provisional_cycle_25() -> dict[str, object]:
    return {
        "target_cycle_id": 25,
        "polar_mean_abs_gauss": 0.865,
        "weakest_hemisphere_abs_gauss": 0.710,
        "target": 160.9,
        "target_dispersion": 8.0,
        "measurement_regime": "WSO",
        "target_status": "provisional_as_of_2026-08-27",
    }


def test_primary_reproduces_registered_five_fold_result() -> None:
    result = run_h2_upgrade(_finalized_rows(), bootstrap_resamples=500)

    primary = result["models"]["mean_polar_linear"]
    assert primary["test_cycles"] == [20, 21, 22, 23, 24]
    assert primary["metrics"]["candidate_mae"] == pytest.approx(26.972492109184863)
    assert result["skill_gate_model"] == "mean_polar_linear"
    assert result["status"] == "mixed_evidence"


def test_fixed_sensitivity_models_are_reported_without_posthoc_promotion() -> None:
    result = run_h2_upgrade(_finalized_rows(), bootstrap_resamples=200)

    assert list(result["models"]) == [
        "mean_polar_linear",
        "sqrt_mean_polar_linear",
        "target_dispersion_weighted_linear",
        "weakest_hemisphere_linear",
    ]
    assert result["challenger_policy"] == "exploratory_not_promoted"
    assert result["selected_challenger"] is None
    assert (
        result["models"]["target_dispersion_weighted_linear"]["metrics"][
            "candidate_mae"
        ]
        < result["models"]["mean_polar_linear"]["metrics"]["candidate_mae"]
    )


def test_provisional_cycle_is_excluded_from_skill_gate() -> None:
    result = run_h2_upgrade(
        _finalized_rows(),
        provisional_row=_provisional_cycle_25(),
        bootstrap_resamples=200,
    )

    check = result["provisional_check"]
    assert check["target_cycle_id"] == 25
    assert check["target_status"] == "provisional_as_of_2026-08-27"
    assert check["excluded_from_skill_gate"] is True
    assert check["training_cycles"] == list(range(15, 25))
    assert result["models"]["mean_polar_linear"]["test_cycles"] == [20, 21, 22, 23, 24]


def test_provisional_cycle_must_follow_finalized_history() -> None:
    invalid = {**_provisional_cycle_25(), "target_cycle_id": 24}
    with pytest.raises(ValueError, match="follow finalized history"):
        run_h2_upgrade(
            _finalized_rows(),
            provisional_row=invalid,
            bootstrap_resamples=200,
        )


def test_target_dispersion_must_be_positive() -> None:
    rows = _finalized_rows()
    rows[0]["target_dispersion"] = 0.0
    with pytest.raises(ValueError, match="target_dispersion"):
        run_h2_upgrade(rows, bootstrap_resamples=200)
