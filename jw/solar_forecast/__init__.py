"""Deterministic contracts for receipt-backed solar-cycle forecasts."""

from .contracts import (
    classify_forecast_skill,
    validate_forecast_experiment_receipt,
    validate_precursor_feature_record,
)
from .h2_upgrade import run_h2_upgrade

__all__ = [
    "classify_forecast_skill",
    "validate_forecast_experiment_receipt",
    "validate_precursor_feature_record",
    "run_h2_upgrade",
]
