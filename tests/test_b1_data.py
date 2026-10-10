"""Data-adapter checks use artificial inputs only; they are not experiments."""

import csv
import hashlib
import json

import pytest

from jw.b1_compute import parse_utc
from jw.b1_data import (
    normalize_events,
    normalize_sharp,
    normalize_xrs,
    verify_raw_sources,
)


def test_raw_source_tampering_and_escape_are_rejected(tmp_path):
    raw = tmp_path / "raw.json"
    raw.write_text("{}", encoding="utf-8")
    manifest = tmp_path / "sources.json"
    source = {"path": "raw.json", "sha256": hashlib.sha256(b"{}").hexdigest()}
    manifest.write_text(json.dumps({"sources": [source]}), encoding="utf-8")
    assert len(verify_raw_sources(manifest)[1]) == 1
    raw.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_raw_sources(manifest)
    source["path"] = "../raw.json"
    manifest.write_text(json.dumps({"sources": [source]}), encoding="utf-8")
    with pytest.raises(ValueError, match="within"):
        verify_raw_sources(manifest)


def _sharp(tmp_path, **overrides):
    record = {
        "HARPNUM": "377",
        "NOAA_AR": "11158",
        "NOAA_ARS": "11158",
        "NOAA_NUM": "1",
        "T_REC": "2011.02.10_00:12:00_TAI",
        "T_OBS": "2011.02.10_00:11:53_TAI",
        "QUALITY": "0x00000000",
        "CMASK": "10",
        "CRVAL1": "20",
        "CRLN_OBS": "20",
        "CADENCE": "720",
        "USFLUX": "10",
        "TOTPOT": "20",
        "MEANSHR": "30",
        "ERRVF": "1",
        "ERRTPOT": "1",
        "ERRMSHA": "1",
    }
    record.update(overrides)
    path = tmp_path / "sharp.json"
    path.write_text(
        json.dumps(
            {
                "status": 0,
                "count": 1,
                "keywords": [{"name": k, "values": [v]} for k, v in record.items()],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_sharp_uses_observation_tai_and_keeps_temporal_support(tmp_path):
    rows, qa = normalize_sharp(
        _sharp(tmp_path),
        parse_utc("2011-02-10T00:00:00Z"),
        parse_utc("2011-02-11T00:00:00Z"),
    )
    assert rows[0]["time_utc"] == "2011-02-10T00:11:19Z"
    assert rows[0]["support_end_utc"] == "2011-02-10T00:22:34Z"
    assert rows[0]["totpot_erg_per_cm"] == 20
    assert qa["quality_valid_rows"] == 1


@pytest.mark.parametrize(
    "override,reason",
    [
        ({"CMASK": "0"}, "no_high_confidence_pixels"),
        ({"QUALITY": "0x400"}, "nonzero_quality"),
        ({"MEANSHR": "MISSING"}, "missing_or_invalid_metric"),
        ({"CRVAL1": "90"}, "patch_center_outside_60deg_longitude"),
    ],
)
def test_sharp_bad_records_are_preserved_but_never_quality_valid(
    tmp_path, override, reason
):
    rows, _ = normalize_sharp(
        _sharp(tmp_path, **override),
        parse_utc("2011-02-10T00:00:00Z"),
        parse_utc("2011-02-11T00:00:00Z"),
    )
    assert len(rows) == 1
    assert rows[0]["quality_valid"] == "false"
    assert reason in rows[0]["excluded_reasons"]


def test_sharp_rejects_ambiguous_region(tmp_path):
    with pytest.raises(ValueError, match="one verified"):
        normalize_sharp(
            _sharp(tmp_path, NOAA_ARS="11158,11159"),
            parse_utc("2011-02-10T00:00:00Z"),
            parse_utc("2011-02-11T00:00:00Z"),
        )


def test_events_keep_missing_end_and_do_not_guess_other_region_ids(tmp_path):
    path = tmp_path / "events.csv"
    original = {
        "start_time": "2011-02-10 01:00:00",
        "time": "2011-02-10 01:10:00",
        "end_time": "",
        "flare_id": "one",
        "active_region": "1158",
        "flare_class": "M1.0",
        "xrsb_irrad": "0.00001",
        "xrsb_irrad_source": "GOES-15",
        "peak_saturated": "0",
    }
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(original))
        writer.writeheader()
        writer.writerows(
            [original, {**original, "flare_id": "two", "active_region": "1159"}]
        )
    rows, _ = normalize_events(
        path, parse_utc("2011-02-10T00:00:00Z"), parse_utc("2011-02-11T00:00:00Z")
    )
    assert rows[0]["noaa_ar"] == 11158
    assert rows[0]["end_utc"] == ""
    assert rows[0]["end_time_missing"] == "true"
    assert rows[1]["noaa_ar"] == ""
    assert rows[1]["source_active_region"] == "1159"


def test_netcdf_time_quality_and_missing_flux(tmp_path):
    netcdf = pytest.importorskip("netCDF4")
    ds = netcdf.Dataset("inmemory", "w", memory=65536)
    ds.platform = "GOES-15"
    ds.createDimension("time", 3)
    time = ds.createVariable("time", "f8", ("time",))
    time.units = "seconds since 2000-01-01 12:00:00 UTC"
    time[:] = [0, 60, 120]
    flux = ds.createVariable("xrsb_flux", "f4", ("time",), fill_value=-9999)
    flux.units = "W/m2"
    flux[:] = [1e-7, 1e-7, -9999]
    for name, values in {
        "xrsb_flag": [0, 2, 0],
        "xrsb_num": [30, 30, 0],
        "xrsb_flag_excluded": [0, 4, 0],
    }.items():
        ds.createVariable(name, "i4", ("time",))[:] = values
    path = tmp_path / "人工.nc"
    path.write_bytes(bytes(ds.close()))
    rows, qa = normalize_xrs(
        [path], parse_utc("2000-01-01T12:00:00Z"), parse_utc("2000-01-02T00:00:00Z")
    )
    assert rows[0]["time_utc"] == "2000-01-01T12:00:00Z"
    assert rows[0]["support_end_utc"] == "2000-01-01T12:01:00Z"
    assert [r["quality_valid"] for r in rows] == ["true", "false", "false"]
    assert rows[2]["flux_w_m2"] is None
    assert qa["quality_valid_rows"] == 1
