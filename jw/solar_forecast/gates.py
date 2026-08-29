"""Executable evidence gates shared by solar forecast runners and reviewers."""

from __future__ import annotations

import math
from typing import Any

from .contracts import classify_forecast_skill


def evaluate_forecast_gate(
    *,
    mae_improvement: float | None,
    ci_low: float | None,
    ci_high: float | None,
    regime_consistent: bool | None,
    leakage_passed: bool,
    data_available: bool,
) -> dict[str, Any]:
    """Evaluate forecast evidence without allowing model prose to upgrade it."""
    finite_metrics = all(
        value is not None and math.isfinite(float(value))
        for value in (mae_improvement, ci_low, ci_high)
    )
    interval_excludes_zero = bool(finite_metrics and float(ci_low) > 0)
    checks = {
        "data_available": bool(data_available),
        "leakage_audit_passed": bool(leakage_passed),
        "finite_metrics": finite_metrics,
        "interval_excludes_zero": interval_excludes_zero,
        "regime_consistent": regime_consistent,
    }
    reasons: list[str] = []
    if not leakage_passed:
        reasons.append("leakage_audit_failed")
    if not data_available:
        return {
            "status": "blocked_by_data",
            "claim_cap": "await_necessary_material",
            "checks": checks,
            "reasons": reasons + ["required_data_unavailable"],
        }
    if (
        not leakage_passed
        or not finite_metrics
        or not isinstance(regime_consistent, bool)
    ):
        if not finite_metrics:
            reasons.append("metrics_missing_or_nonfinite")
        if not isinstance(regime_consistent, bool):
            reasons.append("regime_consistency_missing")
        return {
            "status": "execution_failed",
            "claim_cap": "no_scientific_verdict",
            "checks": checks,
            "reasons": list(dict.fromkeys(reasons)),
        }
    status = classify_forecast_skill(
        execution_completed=True,
        data_available=True,
        mae_improvement=float(mae_improvement),
        ci_low=float(ci_low),
        ci_high=float(ci_high),
        regime_consistent=regime_consistent,
    )
    return {
        "status": status,
        "claim_cap": (
            "skill_supported"
            if status == "skill_supported"
            else "conditional_statistical_forecast"
        ),
        "checks": checks,
        "reasons": reasons,
    }


__all__ = ["evaluate_forecast_gate"]
