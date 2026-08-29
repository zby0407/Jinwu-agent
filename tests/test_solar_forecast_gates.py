from jw.solar_forecast.gates import evaluate_forecast_gate


def test_forecast_gate_preserves_mixed_evidence_when_interval_crosses_zero():
    result = evaluate_forecast_gate(
        mae_improvement=13.0,
        ci_low=-7.0,
        ci_high=31.0,
        regime_consistent=True,
        leakage_passed=True,
        data_available=True,
    )

    assert result["status"] == "mixed_evidence"
    assert result["claim_cap"] == "conditional_statistical_forecast"
    assert result["checks"]["interval_excludes_zero"] is False


def test_forecast_gate_fails_closed_on_leakage_or_missing_data():
    result = evaluate_forecast_gate(
        mae_improvement=20.0,
        ci_low=4.0,
        ci_high=30.0,
        regime_consistent=True,
        leakage_passed=False,
        data_available=True,
    )
    assert result["status"] == "execution_failed"
    assert "leakage_audit_failed" in result["reasons"]

    blocked = evaluate_forecast_gate(
        mae_improvement=None,
        ci_low=None,
        ci_high=None,
        regime_consistent=None,
        leakage_passed=True,
        data_available=False,
    )
    assert blocked["status"] == "blocked_by_data"
