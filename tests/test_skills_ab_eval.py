from jw.solar_forecast.skills_ab_eval import build_ab_report


def test_ab_report_separates_static_skill_coverage_from_numeric_outputs(tmp_path):
    gate = tmp_path / "forecast_evidence_gate.json"
    gate.write_text('{"status": "mixed_evidence"}', encoding="utf-8")
    registry = tmp_path / "skill_registry.json"
    registry.write_text(
        '{"version": 1, "shared": ["verification-before-completion"], '
        '"main": ["solar-hypothesis-portfolio"], '
        '"agents": {"solar-data": ["solar-cycle-forecast-validation"]}}',
        encoding="utf-8",
    )

    report = build_ab_report(registry, {"solar-data": gate})

    assert report["schema_version"] == "jw-skills-ab-eval-v1"
    assert report["control"]["role_specific_skill_count"] == 0
    assert report["treatment"]["role_specific_skill_count"] == 2
    assert report["treatment"]["role_specific_skills"]["JW"] == [
        "solar-hypothesis-portfolio"
    ]
    assert "JW" in report["treatment"]["runtime_receipts"]
    assert report["treatment"]["gate_statuses"] == {"solar-data": "mixed_evidence"}
    assert report["interpretation"]["numeric_quality_claim"] is False
