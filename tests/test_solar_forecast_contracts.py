from __future__ import annotations

import pytest

from jw.solar_forecast import (
    classify_forecast_skill,
    validate_forecast_experiment_receipt,
    validate_precursor_feature_record,
)


def _feature(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "schema_version": "solar-precursor-feature-record-v1",
        "feature_id": "h2-cycle-20-polar-field",
        "hypothesis_id": "H2",
        "forecast_origin": "cycle_minimum",
        "observable_kind": "polar_aperture_field",
        "physical_quantity": "absolute polar aperture field near minimum",
        "unit": "gauss",
        "source_dataset_ids": ["mwo-wso-polar-field-v2"],
        "source_artifact_ids": ["solar_precursor_cycle_features.csv#cycle=20"],
        "observation_start": "1976-01-01",
        "observation_end": "1976-12-31",
        "available_at": "1977-01-01",
        "cycle_id": 20,
        "target_cycle_id": 21,
        "value": 1.25,
        "uncertainty": None,
        "measurement_regime": "MWO",
        "derivation_method": "registered cycle-minimum aggregation",
        "source_kind": "registered_polar_aperture",
        "status": "available",
    }
    record.update(overrides)
    return record


def _receipt(**overrides: object) -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema_version": "solar-forecast-experiment-receipt-v1",
        "experiment_id": "polar-rolling-origin-20260828",
        "status": "mixed_evidence",
        "forecast_origin": "cycle_minimum",
        "hypothesis_ids": ["H2"],
        "feature_ids": ["h2-cycle-20-polar-field"],
        "observable_kinds": ["polar_aperture_field"],
        "baseline_names": ["training_mean", "persistence"],
        "candidate_name": "linear_polar_precursor",
        "training_cycles": [16, 17, 18, 19, 20],
        "test_cycles": [21, 22],
        "folds": [
            {
                "training_cycles": [16, 17, 18, 19, 20],
                "test_cycle": 21,
                "observed": 232.9,
                "candidate_prediction": 220.0,
                "training_mean_prediction": 185.0,
                "persistence_prediction": 156.6,
                "measurement_regime": "MWO",
            },
            {
                "training_cycles": [16, 17, 18, 19, 20, 21],
                "test_cycle": 22,
                "observed": 212.5,
                "candidate_prediction": 205.0,
                "training_mean_prediction": 193.0,
                "persistence_prediction": 232.9,
                "measurement_regime": "WSO",
            },
        ],
        "metrics": {
            "candidate_mae": 10.2,
            "candidate_rmse": 10.55,
            "training_mean_mae": 23.7,
            "training_mean_rmse": 25.0,
            "persistence_mae": 61.35,
            "persistence_rmse": 63.0,
            "mae_improvement": 13.5,
            "mae_improvement_interval": [-2.0, 29.0],
        },
        "bootstrap": {"seed": 20260828, "resamples": 10_000},
        "sensitivity": {
            "measurement_regimes": {"MWO": 32.0, "WSO": 11.5},
            "regime_consistent": True,
            "leave_one_fold": [],
        },
        "leakage_audit": {
            "passed": True,
            "rule": "every training cycle precedes its test cycle",
        },
    }
    receipt.update(overrides)
    return receipt


def test_valid_polar_feature_is_normalized_without_mutating_input() -> None:
    record = _feature()

    validated = validate_precursor_feature_record(record)

    assert validated == record
    assert validated is not record


def test_polar_aperture_cannot_claim_axial_dipole() -> None:
    record = _feature(
        observable_kind="axial_dipole_moment",
        derivation_method="north/south WSO aperture average",
    )

    with pytest.raises(ValueError, match="axial dipole"):
        validate_precursor_feature_record(record)


def test_blocked_axial_feature_requires_explicit_gap_and_no_value() -> None:
    blocked = _feature(
        feature_id="h3-axial-dipole-unavailable",
        hypothesis_id="H3",
        observable_kind="axial_dipole_moment",
        physical_quantity="axial dipole moment near cycle minimum",
        source_dataset_ids=[],
        source_artifact_ids=[],
        value=None,
        source_kind="unavailable",
        status="blocked_by_data",
        data_gap="NO_REGISTERED_AXIAL_DIPOLE_OR_SYNOPTIC_MAP_INPUT",
    )

    assert validate_precursor_feature_record(blocked)["status"] == "blocked_by_data"

    with pytest.raises(ValueError, match="data_gap"):
        validate_precursor_feature_record({key: value for key, value in blocked.items() if key != "data_gap"})
    with pytest.raises(ValueError, match="value"):
        validate_precursor_feature_record({**blocked, "value": 0.4})


def test_available_feature_forbids_data_gap() -> None:
    with pytest.raises(ValueError, match="data_gap"):
        validate_precursor_feature_record(_feature(data_gap="not actually blocked"))


def test_completed_receipt_requires_strictly_chronological_folds() -> None:
    assert validate_forecast_experiment_receipt(_receipt())["status"] == "mixed_evidence"

    invalid = _receipt()
    invalid["folds"] = [
        {
            **invalid["folds"][0],  # type: ignore[index]
            "training_cycles": [16, 17, 21],
            "test_cycle": 21,
        }
    ]
    with pytest.raises(ValueError, match="training cycle"):
        validate_forecast_experiment_receipt(invalid)


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"execution_completed": False, "data_available": True}, "execution_failed"),
        ({"execution_completed": True, "data_available": False}, "blocked_by_data"),
        (
            {
                "execution_completed": True,
                "data_available": True,
                "mae_improvement": 4.0,
                "ci_low": 0.5,
                "ci_high": 8.0,
                "regime_consistent": True,
            },
            "skill_supported",
        ),
        (
            {
                "execution_completed": True,
                "data_available": True,
                "mae_improvement": 4.0,
                "ci_low": -1.0,
                "ci_high": 9.0,
                "regime_consistent": True,
            },
            "mixed_evidence",
        ),
        (
            {
                "execution_completed": True,
                "data_available": True,
                "mae_improvement": -0.1,
                "ci_low": -4.0,
                "ci_high": 3.0,
                "regime_consistent": True,
            },
            "tested_no_skill",
        ),
    ],
)
def test_skill_status_is_deterministic(kwargs: dict[str, object], expected: str) -> None:
    assert classify_forecast_skill(**kwargs) == expected  # type: ignore[arg-type]


def test_completed_classification_rejects_missing_or_nonfinite_metrics() -> None:
    with pytest.raises(ValueError, match="finite metrics"):
        classify_forecast_skill(
            execution_completed=True,
            data_available=True,
            mae_improvement=None,
            ci_low=0.0,
            ci_high=1.0,
            regime_consistent=True,
        )
    with pytest.raises(ValueError, match="finite metrics"):
        classify_forecast_skill(
            execution_completed=True,
            data_available=True,
            mae_improvement=float("nan"),
            ci_low=0.0,
            ci_high=1.0,
            regime_consistent=True,
        )
