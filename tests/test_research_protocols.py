from jw.research_protocols import (
    F107_DISCONTINUITY_PROTOCOL,
    F107_DISCONTINUITY_REQUIRED_MEASUREMENTS,
    SILSO_CYCLE_EXTREMA_DATA_PRODUCT,
    SILSO_CYCLE_REPRODUCTION_PROTOCOL,
    SOLAR_CYCLE_26_READINESS_DATA_PRODUCT,
    SOLAR_CYCLE_26_READINESS_DATASET_IDS,
    SOLAR_CYCLE_26_READINESS_PROTOCOL,
    SOLAR_CYCLE_26_FORECAST_BACKTEST_PROTOCOL,
    SOLAR_POLAR_PRECURSOR_DATA_PRODUCT,
    SOLAR_POLAR_PRECURSOR_PROTOCOL,
    SILSO_CYCLE_MORPHOLOGY_PROTOCOL,
    detect_analysis_protocol,
    f107_discontinuity_directive,
    plan_dataset_selection_conflicts_protocol,
    render_silso_cycle_reproduction_markdown,
    required_data_product_for_protocol,
    silso_cycle_morphology_directive,
    solar_cycle_26_readiness_directive,
    solar_cycle_26_forecast_backtest_directive,
    solar_polar_precursor_directive,
)


def test_detects_only_f107_discontinuity_requests() -> None:
    assert (
        detect_analysis_protocol("分析 F10.7 在 1980-1981 年的不连续性")
        == F107_DISCONTINUITY_PROTOCOL
    )
    assert (
        detect_analysis_protocol("scan the F10.7 breakpoint across periods")
        == F107_DISCONTINUITY_PROTOCOL
    )
    assert detect_analysis_protocol("解释 F10.7 的物理意义") == "none"
    assert detect_analysis_protocol("分析 1980 年的太阳黑子数") == "none"


def test_f107_directive_names_every_required_measurement() -> None:
    directive = f107_discontinuity_directive()

    assert "F10.7 as the response" in directive
    assert "SN=100" in directive
    assert "approximately 10.5%" in directive
    assert all(
        measurement_id in directive
        for measurement_id in F107_DISCONTINUITY_REQUIRED_MEASUREMENTS
    )


def test_detects_bounded_silso_cycle_extrema_reproduction() -> None:
    request = (
        "Use WDC-SILSO Version 2.0 13-month smoothed monthly sunspot numbers "
        "to reproduce the official minima, maxima, and rise time for cycles 21-24."
    )

    assert detect_analysis_protocol(request) == SILSO_CYCLE_REPRODUCTION_PROTOCOL


def test_protocol_not_available_inputs_selects_required_data_product() -> None:
    assert (
        required_data_product_for_protocol(SILSO_CYCLE_REPRODUCTION_PROTOCOL)
        == SILSO_CYCLE_EXTREMA_DATA_PRODUCT
    )
    assert (
        required_data_product_for_protocol(SOLAR_POLAR_PRECURSOR_PROTOCOL)
        == SOLAR_POLAR_PRECURSOR_DATA_PRODUCT
    )
    request = (
        "使用 SILSO 复现周期21至24的极值和上升时间；"
        "不要加入极区磁场、F10.7或周期26预测。"
    )
    assert detect_analysis_protocol(request) == SILSO_CYCLE_REPRODUCTION_PROTOCOL


def test_explicit_polar_precursor_request_has_separate_protocol() -> None:
    assert (
        detect_analysis_protocol("比较极小附近极区磁场作为下一周期振幅前兆")
        == SOLAR_POLAR_PRECURSOR_PROTOCOL
    )


def test_polar_precursor_survives_excluding_one_cycle_and_protocol_rerun() -> None:
    request = """
    使用本次上传的 solar_precursor_cycle_features.csv 完成极区磁场前兆统计。
    不要重新调用 solar_polar_precursor_v1 或 silso_cycle_reproduction_v1。
    排除预测窗口覆盖不完整的第 15 周后，比较 MWO 代理时期和 WSO 直接观测时期。
    """
    assert detect_analysis_protocol(request) == SOLAR_POLAR_PRECURSOR_PROTOCOL


def test_morphology_excludes_polar_data_with_compact_chinese_negation() -> None:
    request = (
        "完成 SILSO 太阳活动周形态统计，分析上升时间与峰值，"
        "不使用极区磁场数据，不分析第26周。"
    )
    assert detect_analysis_protocol(request) == SILSO_CYCLE_MORPHOLOGY_PROTOCOL


def test_detects_independent_silso_cycle_morphology_experiment() -> None:
    request = """
    请完成一次独立的 SILSO 太阳活动周形态统计实验，分析第 1—24 周的周期长度、
    上升时间、下降时间与峰值强度关系，报告 Waldmeier 效应、Bootstrap 和留一分析。
    """
    assert detect_analysis_protocol(request) == SILSO_CYCLE_MORPHOLOGY_PROTOCOL


def test_morphology_directive_calibrates_claim_specific_confidence() -> None:
    directive = silso_cycle_morphology_directive()

    assert "claim-specific confidence" in directive
    assert "source reconstruction" in directive
    assert "rise-time association" in directive
    assert "medium-high" in directive
    assert "must never upgrade a causal mechanism" in directive
    assert "do not call scientific_hypothesis_validate_response" in directive


def test_cycle_26_launch_gate_has_broader_readiness_protocol() -> None:
    request = (
        "资料截止在 2026 年 6 月 30 日，请系统研究第 26 太阳活动周强度预测"
        "现在是否可以启动，重点核查 SILSO、F10.7 和 WSO 极区磁场，最终回答"
        "可以启动或暂不启动。"
    )

    assert detect_analysis_protocol(request) == SOLAR_CYCLE_26_READINESS_PROTOCOL
    assert (
        required_data_product_for_protocol(SOLAR_CYCLE_26_READINESS_PROTOCOL)
        == SOLAR_CYCLE_26_READINESS_DATA_PRODUCT
    )
    assert SOLAR_CYCLE_26_READINESS_DATASET_IDS == (
        "silso-monthly-total-v2",
        "silso-monthly-smoothed-v2",
        "silso-cycle-extrema-v2",
        "noaa-swpc-monthly-f107-v1",
        "mwo-wso-polar-field-v2",
        "wso-current-polar-field-v1",
    )
    assert detect_analysis_protocol("请判断第26太阳活动周预测是否可以启动") == "none"


def test_cycle_26_preliminary_probability_forecast_uses_readiness_protocol() -> None:
    request = (
        "请系统研究并正式发布第 26 太阳活动周的初步概率预测，"
        "给出 13 个月平滑峰值的点预测、80% 和 95% 预测区间。"
    )

    assert detect_analysis_protocol(request) == SOLAR_CYCLE_26_READINESS_PROTOCOL


def test_cycle_26_historical_backtest_and_formal_forecast_use_dedicated_protocol() -> (
    None
):
    request = (
        "先对第1—24周做严格按时间顺序的历史回测，报告MAE和RMSE；"
        "历史回测完成后，正式给出第26周峰值预测、95%预测区间和可视化。"
    )

    assert (
        detect_analysis_protocol(request) == SOLAR_CYCLE_26_FORECAST_BACKTEST_PROTOCOL
    )
    assert required_data_product_for_protocol(
        SOLAR_CYCLE_26_FORECAST_BACKTEST_PROTOCOL
    ) == ("solar_cycle_26_forecast_backtest_v1")
    assert "historical backtest" in solar_cycle_26_forecast_backtest_directive()


def test_cycle_26_directive_separates_preliminary_and_final_forecasts() -> None:
    directive = solar_cycle_26_readiness_directive()

    assert "preliminary operational probability forecast" in directive
    assert "must not return only a not-ready decision" in directive
    assert "13-month smoothed SILSO v2" in directive
    assert "80% and 95% prediction intervals" in directive
    assert "claim-specific confidence" in directive
    assert "high confidence" in directive
    assert "must not be upgraded" in directive


def test_cycle_26_readiness_directive_pins_raw_parser_roles_and_anchors() -> None:
    directive = solar_cycle_26_readiness_directive()

    assert "semicolon-delimited" in directive
    assert "fixed whitespace columns" in directive
    assert "12-column annual calibration history" in directive
    assert "Polar.html" in directive
    assert "10-day current WSO observations" in directive
    assert "2026-01-09" in directive
    assert "17 explicit XXX rows" in directive
    assert "2026-01" in directive
    assert "104.2" in directive
    assert "parser-validation anchors" in directive
    assert "technical failure" in directive
    assert "derive the reported values from the raw bytes" in directive


def test_plan_dataset_selection_conflict_detection_matches_protocol() -> None:
    plan = {
        "required_datasets": [
            {"selected_source_id": "silso-monthly-total-v2"},
            {"selected_source_id": "foreign-dataset-v1"},
        ]
    }

    assert (
        plan_dataset_selection_conflicts_protocol(plan, SOLAR_POLAR_PRECURSOR_PROTOCOL)
        is True
    )
    assert (
        plan_dataset_selection_conflicts_protocol(plan, SOLAR_POLAR_PRECURSOR_PROTOCOL)
        is True
    )


def test_polar_precursor_directive_requires_cycle_level_out_of_sample_analysis() -> (
    None
):
    directive = solar_polar_precursor_directive()

    assert "cycle N" in directive
    assert "cycle N+1" in directive
    assert "adjacent minima" in directive
    assert "independent sample unit" in directive
    assert "rolling-origin" in directive
    assert "MAE and RMSE" in directive
    assert "leave-one-cycle" in directive
    assert "MWO" in directive
    assert "WSO" in directive
    assert "Do not preselect the interaction sign" in directive


def test_silso_final_markdown_is_deterministic_and_scoped() -> None:
    official = {
        21: ("1976-03", 17.8, "1979-12", 232.9, 45),
        22: ("1986-09", 13.5, "1989-11", 212.5, 38),
        23: ("1996-08", 11.2, "2001-11", 180.3, 63),
        24: ("2008-12", 2.2, "2014-04", 116.4, 64),
    }
    rows = []
    for cycle, (minimum, min_value, maximum, max_value, rise) in official.items():
        min_year, min_month = map(int, minimum.split("-"))
        max_year, max_month = map(int, maximum.split("-"))
        recomputed_minimum = "1996-05" if cycle == 23 else minimum
        rmin_year, rmin_month = map(int, recomputed_minimum.split("-"))
        rows.append(
            {
                "cycle": cycle,
                "official_minimum": {
                    "year": min_year,
                    "month": min_month,
                    "year_month": minimum,
                    "sunspot_number": min_value,
                },
                "official_maximum": {
                    "year": max_year,
                    "month": max_month,
                    "year_month": maximum,
                    "sunspot_number": max_value,
                },
                "recomputed_minimum": {
                    "year": rmin_year,
                    "month": rmin_month,
                    "year_month": recomputed_minimum,
                    "sunspot_number": min_value,
                },
                "recomputed_maximum": {
                    "year": max_year,
                    "month": max_month,
                    "year_month": maximum,
                    "sunspot_number": max_value,
                },
                "official_rise_months": rise,
                "recomputed_rise_months": 66 if cycle == 23 else rise,
                "minimum_matches_official": cycle != 23,
                "maximum_matches_official": True,
                "difference_explanation": (
                    "同一最小平滑值对应不同月份，保留官方值和重算值。"
                    if cycle == 23
                    else "官方值与重算值一致。"
                ),
            }
        )
    output = render_silso_cycle_reproduction_markdown(
        {
            "schema_version": "silso-cycle-reproduction-v1",
            "analysis_protocol": SILSO_CYCLE_REPRODUCTION_PROTOCOL,
            "cycles": [21, 22, 23, 24],
            "comparison": rows,
        }
    )

    assert "WDC-SILSO Sunspot Number Version 2.0" in output
    assert "官方 13 个月平滑" in output
    assert all(f"{months} 个月" in output for months in (45, 38, 63, 64))
    assert "周期 21 > 周期 22 > 周期 23 > 周期 24" in output
    assert "周期 22 上升最快" in output
    assert "周期 24 上升最慢" in output
    assert "1996-08" in output
    assert "1996-05" in output
    assert "相同最小平滑值的平台期" in output
    assert "The recomputation" not in output
    assert all(term not in output for term in ("F10.7", "极区磁场", "发电机机制"))
    assert "不涉及周期 26 的预测" in output
