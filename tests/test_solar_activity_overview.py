from __future__ import annotations

import json

from jw.solar_activity_overview import (
    parse_noaa_monthly_f107,
    parse_silso_cycle_extrema,
    parse_silso_monthly_total,
    parse_silso_smoothed,
)


def test_parse_silso_monthly_total_marks_missing_and_provisional() -> None:
    rows = [
        f"{year} {month:02d} {year}.5 10.5 1.2 30 " + ("*" if month == 12 else "")
        for year in range(1749, 2027)
        for month in range(1, 13)
    ]
    rows[0] = "1749 01 1749.042 -1.0 -1.0 -1  "
    parsed = parse_silso_monthly_total(("\n".join(rows) + "\n").encode("ascii"))

    assert parsed["1749-01"]["sunspot_number"] is None
    assert parsed["1749-01"]["sunspot_stddev"] is None
    assert parsed["1749-01"]["sunspot_observations"] is None
    assert parsed["1749-12"]["sunspot_provisional"] is True
    assert parsed["1750-01"]["sunspot_provisional"] is False


def test_parse_silso_smoothed_uses_negative_one_as_missing() -> None:
    lines = []
    for year in range(1749, 2027):
        for month in range(1, 13):
            value = "20.5" if month < 12 else " -1.0"
            definitive = "1" if month < 12 else "0"
            lines.append(f"{year};{month:02d};{year}.5; {value}; 1.0; 30;{definitive}")
    parsed = parse_silso_smoothed(("\n".join(lines) + "\n").encode("ascii"))

    assert parsed["1749-01"]["smoothed_sunspot_number"] == 20.5
    assert parsed["1749-01"]["smoothed_definitive"] is True
    assert parsed["1749-12"]["smoothed_sunspot_number"] is None
    assert parsed["1749-12"]["smoothed_definitive"] is False


def test_parse_noaa_monthly_f107() -> None:
    payload = json.dumps(
        [
            {"time-tag": "2004-10", "f10.7": 92.3},
            {"time-tag": "2004-11", "f10.7": -1},
        ]
    ).encode("utf-8")
    parsed = parse_noaa_monthly_f107(payload)

    assert parsed == {
        "2004-10": {"f107": 92.3},
        "2004-11": {"f107": None},
    }


def test_parse_silso_cycle_extrema_accepts_incomplete_current_cycle() -> None:
    completed = "\n".join(
        f"{cycle:02d}    {1900 + cycle} 01    2.2  {1905 + cycle} 04  116.4   11 00"
        for cycle in range(1, 25)
    )
    payload = (
        "Cycle Minimum         Maximum         Duration in\n"
        "Nb    Year Month SN   Year Month SN   Years + Months\n\n"
        + completed
        + "\n25    2019 12    1.8\n"
    ).encode("ascii")
    parsed = parse_silso_cycle_extrema(payload)

    assert len(parsed) == 25
    assert parsed[-2]["completed"] is True
    assert parsed[-2]["maximum"]["label"] == "1929-04"
    assert parsed[-1]["completed"] is False
    assert parsed[-1]["maximum"] is None
    assert parsed[-1]["duration"] is None


def test_http_surface_mounts_overview_route(monkeypatch) -> None:
    from starlette.testclient import TestClient

    from jw.langgraph_dev import solar_activity_api
    from jw.langgraph_dev.http import app

    payload = {
        "status": "ok",
        "generated_at": "2026-11-01T00:00:00+00:00",
        "summary": {"coverage_start": "1749-01", "coverage_end": "2026-10"},
        "monthly_rows": [],
        "cycles": [],
        "sources": [],
        "capabilities": [],
    }
    calls = []

    def fake_build(**kwargs):
        calls.append(kwargs)
        return payload

    monkeypatch.setattr(solar_activity_api, "build_solar_activity_overview", fake_build)
    response = TestClient(app).get("/api/solar-activity/overview?months=30&cycles=5")

    assert response.status_code == 200
    assert response.json() == payload
    assert calls == [{"months": 30, "cycle_count": 5, "refresh": False}]


def test_http_surface_returns_502_when_no_valid_upstream_snapshot(monkeypatch) -> None:
    from starlette.testclient import TestClient

    from jw.langgraph_dev import solar_activity_api
    from jw.langgraph_dev.http import app

    def fake_build(**kwargs):
        raise RuntimeError("silso-monthly-total-v2 upstream fetch or validation failed")

    monkeypatch.setattr(solar_activity_api, "build_solar_activity_overview", fake_build)
    response = TestClient(app).get("/api/solar-activity/overview")

    assert response.status_code == 502
    assert response.json()["code"] == "upstream_unavailable"
