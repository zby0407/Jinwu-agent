"""An excluded research domain must not select its deterministic protocol."""

import pytest

from jw.middleware.research_router import _with_analysis_protocol
from jw.research_protocols import (
    SOLAR_POLAR_PRECURSOR_PROTOCOL,
    detect_analysis_protocol,
)


@pytest.mark.parametrize(
    "exclusion",
    [
        "不是太阳黑子活动周或极区场前兆任务。",
        "并非太阳黑子活动周或极区场前兆任务。",
        "而非太阳黑子活动周或极区场前兆任务。",
        "This is not a polar field precursor task.",
        "This isn't a polar field precursor task.",
        "Not a polar field precursor task.",
    ],
)
def test_excluded_domain_does_not_hijack_another_forecast(exclusion):
    prompt = "Prepare hourly solar wind speed data and chronological splits. " + exclusion
    assert detect_analysis_protocol(prompt) == "none"
    route = _with_analysis_protocol(
        {"mode": "verified_analysis", "task_intent": "data_preparation"},
        text=prompt,
    )
    assert "required_analysis_protocol" not in route
    assert route.get("required_specialist") != "solar-data"


def test_affirmative_domain_after_exclusion_is_still_selected():
    prompt = "不是太阳风任务，而是比较极小附近极区磁场作为下一周期振幅前兆。"
    assert detect_analysis_protocol(prompt) == SOLAR_POLAR_PRECURSOR_PROTOCOL
