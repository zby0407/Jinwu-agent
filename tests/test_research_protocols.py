from jw.research_protocols import (
    F107_DISCONTINUITY_PROTOCOL,
    F107_DISCONTINUITY_REQUIRED_MEASUREMENTS,
    detect_analysis_protocol,
    f107_discontinuity_directive,
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
