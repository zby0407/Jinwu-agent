from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = (
    ROOT
    / "jw"
    / "subagents"
    / "solar"
    / "skills"
    / "solar-flare-forecasting"
)


def _load_script(name: str):
    path = SKILL_DIR / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_flare_forecasting_skill_is_registered_and_progressively_loaded() -> None:
    skill_text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    metadata = yaml.safe_load(skill_text.split("---", 2)[1])
    bundle = yaml.safe_load(
        (ROOT / "jw" / "subagents" / "solar" / "bundle.yaml").read_text(
            encoding="utf-8"
        )
    )

    assert metadata["name"] == "solar-flare-forecasting"
    assert "GOES" in metadata["description"]
    assert "solar-flare-forecasting" in bundle["skills"]
    assert {
        "forecast-task-contract.md",
        "goes-label-semantics.md",
        "sharp-feature-semantics.md",
        "leakage-and-splitting.md",
        "baselines-and-metrics.md",
        "forecast-reporting.md",
    } == {path.name for path in (SKILL_DIR / "references").glob("*.md")}


def test_forecast_contract_rejects_future_available_features() -> None:
    module = _load_script("validate_forecast_contract")
    contract = {
        "schema_version": "solar-flare-forecast-task-v1",
        "task_id": "m1-24h",
        "forecast_mode": "research_backtest",
        "spatial_unit": "full_disk",
        "target_thresholds": ["M1.0+"],
        "issue_time": "2025-01-01T00:00:00Z",
        "data_cutoff": "2025-01-01T01:00:00Z",
        "observation_window": {
            "start": "2024-12-31T00:00:00Z",
            "end": "2025-01-01T01:00:00Z",
        },
        "prediction_window": {
            "start": "2025-01-01T00:00:00Z",
            "end": "2025-01-02T00:00:00Z",
        },
        "output_type": "probability",
    }

    result = module.validate_contract(contract)

    assert result["status"] == "error"
    assert "data_cutoff must not be after issue_time" in result["issues"]


def test_forecast_contract_accepts_active_region_policy() -> None:
    module = _load_script("validate_forecast_contract")
    contract = {
        "schema_version": "solar-flare-forecast-task-v1",
        "task_id": "ar-m1-24h",
        "forecast_mode": "simulated_operational",
        "spatial_unit": "active_region",
        "target_thresholds": ["M1.0+", "X1.0+"],
        "issue_time": "2025-01-01T00:00:00Z",
        "data_cutoff": "2025-01-01T00:00:00Z",
        "observation_window": {
            "start": "2024-12-31T00:00:00Z",
            "end": "2025-01-01T00:00:00Z",
        },
        "prediction_window": {
            "start": "2025-01-01T00:00:00Z",
            "end": "2025-01-02T00:00:00Z",
        },
        "output_type": "probability",
        "region_identifier_policy": "Frozen NOAA-to-HARP map at issue time",
    }

    assert module.validate_contract(contract)["status"] == "ok"


def test_split_validator_blocks_group_and_time_leakage() -> None:
    module = _load_script("validate_forecast_split")
    rows = [
        {"split": "train", "issue_time": "2025-02-01T00:00:00Z", "region_id": "1"},
        {"split": "validation", "issue_time": "2025-01-01T00:00:00Z", "region_id": "2"},
        {"split": "test", "issue_time": "2025-03-01T00:00:00Z", "region_id": "1"},
    ]

    result = module.validate_rows(rows)

    assert result["status"] == "error"
    assert any("spans splits" in issue for issue in result["issues"])
    assert any("overlap or follow" in issue for issue in result["issues"])


def test_probability_verifier_reports_baseline_relative_skill() -> None:
    module = _load_script("verify_probabilistic_forecast")
    rows = [
        {"probability": "0.8", "outcome": "1", "baseline_probability": "0.25"},
        {"probability": "0.6", "outcome": "1", "baseline_probability": "0.25"},
        {"probability": "0.3", "outcome": "0", "baseline_probability": "0.25"},
        {"probability": "0.1", "outcome": "0", "baseline_probability": "0.25"},
    ]

    result = module.verify(rows, bins=2)

    assert result["status"] == "ok"
    assert result["sample"] == {"count": 4, "event_count": 2, "event_rate": 0.5}
    assert result["probability_metrics"]["brier_score"] == 0.075
    assert result["probability_metrics"]["brier_skill_score"] == 0.76
    assert result["threshold_metrics"]["tss"] == 1.0
    assert [item["count"] for item in result["calibration_bins"]] == [2, 2]
