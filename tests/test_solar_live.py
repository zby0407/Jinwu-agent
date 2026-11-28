from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from jw import solar_live
from jw.astronomy_data_sources import query_astronomy_data_source


class _Response:
    def __init__(self, body: bytes, modified: str | None = None):
        self.body = body
        self.headers = {"Last-Modified": modified} if modified else {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        return self.body if size < 0 else self.body[:size]


@pytest.fixture(autouse=True)
def _fresh_cache():
    solar_live.clear_cache()
    yield
    solar_live.clear_cache()


def _xray(stamp: str, flux: float) -> dict:
    return {"time_tag": stamp, "energy": "0.1-0.8nm", "flux": flux}


def test_flare_class_labels_goes_flux() -> None:
    assert solar_live.flare_class(7.52e-7) == "B7.5"
    assert solar_live.flare_class(2.17e-6) == "C2.1"
    assert solar_live.flare_class(1.2e-3) == "X12"
    assert solar_live.flare_class(None) is None


def test_xray_summary_reports_current_and_window_peaks() -> None:
    rows = [
        _xray("2026-11-27T05:00:00Z", 3.9e-6),
        _xray("2026-11-28T03:30:00Z", 9.0e-7),
        _xray("2026-11-28T04:28:00Z", 2.17e-6),
        {"time_tag": "2026-11-28T04:28:00Z", "energy": "0.05-0.4nm", "flux": 1e-3},
        _xray("2026-11-28T05:00:00Z", 7.52e-7),
        _xray("2026-11-28T05:01:00Z", -1),
    ]
    summary = solar_live.xray_summary(rows)
    assert summary["current"]["class"] == "B7.5"
    assert summary["max_2h"]["class"] == "C2.1"
    assert summary["max_24h"]["class"] == "C3.9"
    assert summary["points"][-1][1] is None


def test_proton_series_keeps_three_energies_and_s_scale() -> None:
    rows = [
        {"time_tag": "2026-11-28T05:00:00Z", "energy": f">={energy} MeV", "flux": flux}
        for energy, flux in ((10, 120.0), (50, 3.0), (100, 0.4), (500, 0.1))
    ]
    result = solar_live.proton_series(rows)
    assert set(result["series"]) == {"gte10", "gte50", "gte100"}
    assert result["latest_gte10"] == 120.0
    assert result["s_scale"] == 2


def test_region_projection_rotates_to_image_time() -> None:
    rows = [
        {
            "observed_date": "2026-11-27",
            "region": 4583,
            "latitude": 5,
            "longitude": 33,
            "location": "N05E33",
        },
        {
            "observed_date": "2026-11-27",
            "region": 4579,
            "latitude": 15,
            "longitude": -95,
            "location": "N15W95",
        },
        {
            "observed_date": "2026-11-26",
            "region": 4570,
            "latitude": 0,
            "longitude": 0,
            "location": "N00W00",
        },
    ]
    at = datetime(2026, 11, 28, 12, tzinfo=UTC)
    result = solar_live.active_regions(rows, at=at)
    assert [row["number"] for row in result["rows"]] == [4579, 4583]
    east = result["rows"][1]
    # E33 at report time, rotated ~6.7° west after half a day.
    assert east["longitude_west"] == pytest.approx(-33 + 0.5 * 13.36, abs=0.05)
    assert -0.5 < east["disk"]["x"] < -0.4
    assert east["disk"]["visible"] is True
    assert result["rows"][0]["disk"]["visible"] is False
    assert abs(result["b0_deg"]) < 7.3


def test_b0_matches_known_seasonal_extremes() -> None:
    assert solar_live.solar_b0_degrees(
        datetime(2026, 9, 8, tzinfo=UTC)
    ) == pytest.approx(7.25, abs=0.1)
    assert solar_live.solar_b0_degrees(
        datetime(2026, 3, 7, tzinfo=UTC)
    ) == pytest.approx(-7.25, abs=0.1)


def test_cache_serves_stale_value_when_upstream_fails(monkeypatch) -> None:
    payloads: list[object] = [
        json.dumps([_xray("2026-11-28T05:00:00Z", 1e-6)]).encode(),
        OSError("down"),
    ]
    calls = []

    def fake_urlopen(request, *, timeout):
        calls.append(request.full_url)
        item = payloads.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Response(item)

    monkeypatch.setattr("jw.solar_live.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(solar_live, "JSON_TTL_SECONDS", 0.0)
    assert solar_live.xray_rows()[0]["flux"] == 1e-6
    assert solar_live.xray_rows()[0]["flux"] == 1e-6
    assert len(calls) == 2


def test_data_center_queries_share_live_cache(monkeypatch) -> None:
    calls = []
    body = json.dumps(
        [
            {"observed_date": "2026-11-26", "region": 1, "location": "N01E01"},
            {"observed_date": "2026-11-27", "region": 2, "location": "N02E02"},
        ]
    ).encode()

    def fake_urlopen(request, *, timeout):
        calls.append(request.full_url)
        return _Response(body)

    monkeypatch.setattr("jw.solar_live.urllib.request.urlopen", fake_urlopen)
    result = query_astronomy_data_source("noaa-solar-regions", {})
    assert [row["region"] for row in result["rows"]] == [2]
    solar_live.region_rows()
    assert calls == [solar_live.REGIONS_URL]


def test_live_image_uses_directory_timestamp(monkeypatch) -> None:
    listing = b'<a href="20261128_0430_ccor1_1024by960.jpg"></a><a href="20261128_0445_ccor1_1024by960.jpg"></a>'
    jpeg = b"\xff\xd8\xff\xe0data\xff\xd9"

    def fake_urlopen(request, *, timeout):
        return _Response(listing if request.full_url.endswith("/") else jpeg)

    monkeypatch.setattr("jw.solar_live.urllib.request.urlopen", fake_urlopen)
    image = solar_live.live_image("ccor1")
    assert image["url"].endswith("20261128_0445_ccor1_1024by960.jpg")
    assert image["observed_at"] == "2026-11-28T04:45:00+00:00"
    assert image["bytes"] == jpeg


def _fake_listing_open(payload: bytes):
    class _Response:
        headers = {}

        def __init__(self, data: bytes) -> None:
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def read(self, limit: int) -> bytes:
            return self.data[:limit]

    def fake_urlopen(request, timeout):
        return _Response(payload)

    return fake_urlopen


def test_history_listing_groups_timestamps_per_channel(monkeypatch) -> None:
    listing = b"""
    <a href="20240510_000000_1024_HMIIF.jpg">x</a>
    <a href="20240510_000000_2048_HMIIF.jpg">x</a>
    <a href="20240510_001500_1024_HMIIF.jpg">x</a>
    <a href="20240510_000000_1024_HMIBC.jpg">magnetogram</a>
    <a href="20240510_000050_1024_0193.jpg">x</a>
    <a href="20240510_000050_512_0193.jpg">x</a>
    <a href="20240510_001034_1024_0131.jpg">x</a>
    <a href="20240509_235959_1024_HMIBC.jpg">wrong day</a>
    """
    monkeypatch.setattr(
        "jw.solar_live.urllib.request.urlopen", _fake_listing_open(listing)
    )
    result = solar_live.history_listing("2024-05-10")
    assert result["status"] == "ok"
    assert result["channels"]["hmi"]["times"] == ["000000", "001500"]
    assert result["channels"]["hmimag"]["times"] == ["000000"]
    assert result["channels"]["aia193"]["times"] == ["000050"]
    assert result["channels"]["aia131"]["count"] == 1


def test_history_listing_rejects_dates_outside_archive() -> None:
    with pytest.raises(ValueError, match="outside the SDO archive"):
        solar_live.history_listing("2009-12-31")
    with pytest.raises(ValueError, match="invalid history date"):
        solar_live.history_listing("not-a-date")


def test_history_image_validates_stamp_and_channel(monkeypatch) -> None:
    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 32
    monkeypatch.setattr(
        "jw.solar_live.urllib.request.urlopen", _fake_listing_open(jpeg)
    )
    image = solar_live.history_image("hmi", "20240510_120000")
    assert image["bytes"] == jpeg
    assert "2024/05/10/20240510_120000_1024_HMIIF.jpg" in image["url"]
    with pytest.raises(ValueError, match="unknown history channel"):
        solar_live.history_image("lasco", "20240510_120000")
    with pytest.raises(ValueError, match="invalid history timestamp"):
        solar_live.history_image("hmi", "../../etc/passwd")
