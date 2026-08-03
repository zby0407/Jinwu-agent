from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts/run_solar_cycle_asof_experiment.py"
SPEC = importlib.util.spec_from_file_location("run_solar_cycle_asof_experiment", SCRIPT)
assert SPEC
assert SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _preview(ci_low: float) -> dict:
    return {
        "trusted_worker_result": {
            "measurements": [
                {"name": "rolling_mae_improvement", "value": 11.2},
                {"name": "mae_improvement_ci_low", "value": ci_low},
                {"name": "mae_improvement_ci_high", "value": 31.3},
            ]
        },
        "criterion_evidence": [
            {"criterion_id": "strict_input_audit"},
            {"criterion_id": "rolling_backtest_complete"},
        ],
    }


def test_assessment_preserves_uncertainty_when_interval_crosses_zero() -> None:
    assessment = MODULE.build_assessment(_preview(-5.9))

    assert assessment["proposed_outcome"] == "high_uncertainty"
    assert assessment["stage_outcome"] == "inconclusive"
    assert assessment["null_assessment"] is None
    assert [item["criterion_id"] for item in assessment["criterion_results"]] == [
        "strict_input_audit",
        "rolling_backtest_complete",
    ]


def test_assessment_reports_interpretable_result_when_interval_is_positive() -> None:
    assessment = MODULE.build_assessment(_preview(1.0))

    assert assessment["proposed_outcome"] == "completed_interpretable"
    assert assessment["stage_outcome"] == "completed"
