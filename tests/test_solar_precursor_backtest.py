from __future__ import annotations

import numpy as np
import pytest

from jw.solar_forecast.backtest import run_precursor_backtest


def _ten_cycle_fixture() -> list[dict[str, object]]:
    values = [0.8, 1.4, 1.0, 1.8, 1.2, 2.0, 0.9, 1.6, 1.1, 1.9]
    targets = [92.0, 151.0, 111.0, 190.0, 132.0, 207.0, 101.0, 171.0, 121.0, 198.0]
    return [
        {
            "feature_id": f"polar-minimum-cycle-{cycle}",
            "hypothesis_id": "h2_polar_precursor",
            "target_cycle_id": cycle,
            "value": value,
            "target": target,
            "measurement_regime": "MWO" if cycle <= 21 else "WSO",
            "observable_kind": "polar_aperture_field",
            "source_kind": "polar_aperture_observation",
        }
        for cycle, value, target in zip(
            range(15, 25), values, targets, strict=True
        )
    ]


def _polar_rows_mislabeled_as_axial() -> list[dict[str, object]]:
    return [
        {
            **row,
            "feature_id": str(row["feature_id"]).replace("polar", "axial"),
            "hypothesis_id": "h3_axial_dipole_discriminator",
            "observable_kind": "axial_dipole_moment",
            "source_kind": "polar_aperture_observation",
        }
        for row in _ten_cycle_fixture()
    ]


def _registered_axial_rows() -> list[dict[str, object]]:
    return [
        {
            **row,
            "feature_id": str(row["feature_id"]).replace("polar", "axial"),
            "hypothesis_id": "h3_axial_dipole_discriminator",
            "observable_kind": "axial_dipole_moment",
            "source_kind": "registered_axial_dipole",
            "value": float(row["value"]) * 0.7,
        }
        for row in _ten_cycle_fixture()
    ]


def test_each_fold_uses_only_earlier_cycles() -> None:
    result = run_precursor_backtest(_ten_cycle_fixture(), bootstrap_resamples=200)

    for fold in result["folds"]:
        assert max(fold["training_cycles"]) < fold["test_cycle"]


def test_training_mean_and_persistence_are_recomputed_per_fold() -> None:
    result = run_precursor_backtest(_ten_cycle_fixture(), bootstrap_resamples=200)
    first = result["folds"][0]
    train_targets = [row["target"] for row in _ten_cycle_fixture()[:5]]

    assert first["training_mean_prediction"] == pytest.approx(np.mean(train_targets))
    assert first["persistence_prediction"] == pytest.approx(train_targets[-1])


def test_axial_comparison_refuses_polar_aperture_values() -> None:
    with pytest.raises(ValueError, match="axial_dipole_moment"):
        run_precursor_backtest(
            _ten_cycle_fixture(),
            discriminator_rows=_polar_rows_mislabeled_as_axial(),
            bootstrap_resamples=200,
        )


def test_registered_axial_comparison_uses_identical_test_cycles() -> None:
    result = run_precursor_backtest(
        _ten_cycle_fixture(),
        discriminator_rows=_registered_axial_rows(),
        bootstrap_resamples=200,
    )

    comparison = result["discriminator_comparison"]
    assert comparison["test_cycles"] == result["test_cycles"]
    assert comparison["complexity_matched"] is True
    assert comparison["observable_kind"] == "axial_dipole_moment"


def test_fixed_seed_reproduces_bootstrap_interval() -> None:
    first = run_precursor_backtest(
        _ten_cycle_fixture(), seed=20260828, bootstrap_resamples=500
    )
    second = run_precursor_backtest(
        _ten_cycle_fixture(), seed=20260828, bootstrap_resamples=500
    )

    assert (
        first["metrics"]["mae_improvement_interval"]
        == second["metrics"]["mae_improvement_interval"]
    )


def test_mae_rmse_and_leave_one_fold_are_recomputed() -> None:
    result = run_precursor_backtest(_ten_cycle_fixture(), bootstrap_resamples=200)
    observed = np.asarray([fold["observed"] for fold in result["folds"]])
    predicted = np.asarray(
        [fold["candidate_prediction"] for fold in result["folds"]]
    )

    assert result["metrics"]["candidate_mae"] == pytest.approx(
        np.mean(np.abs(observed - predicted))
    )
    assert result["metrics"]["candidate_rmse"] == pytest.approx(
        np.sqrt(np.mean((observed - predicted) ** 2))
    )
    assert len(result["sensitivity"]["leave_one_fold"]) == len(result["folds"])


def test_measurement_regime_sign_is_reported_separately() -> None:
    result = run_precursor_backtest(_ten_cycle_fixture(), bootstrap_resamples=200)

    assert set(result["sensitivity"]["measurement_regimes"]) == {"MWO", "WSO"}
    assert isinstance(result["sensitivity"]["regime_consistent"], bool)


def test_requires_seven_unique_chronological_cycles() -> None:
    with pytest.raises(ValueError, match="at least seven"):
        run_precursor_backtest(_ten_cycle_fixture()[:6], bootstrap_resamples=200)
    duplicated = _ten_cycle_fixture()
    duplicated[-1]["target_cycle_id"] = 23
    with pytest.raises(ValueError, match="unique"):
        run_precursor_backtest(duplicated, bootstrap_resamples=200)
