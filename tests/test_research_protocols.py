from jw.research_protocols import (
    F107_DISCONTINUITY_PROTOCOL,
    F107_DISCONTINUITY_REQUIRED_MEASUREMENTS,
    SILSO_CYCLE_EXTREMA_DATA_PRODUCT,
    SILSO_CYCLE_REPRODUCTION_PROTOCOL,
    SOLAR_POLAR_PRECURSOR_DATA_PRODUCT,
    SOLAR_POLAR_PRECURSOR_PROTOCOL,
    detect_analysis_protocol,
    f107_discontinuity_directive,
    render_silso_cycle_reproduction_markdown,
    required_data_product_for_protocol,
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
