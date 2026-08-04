from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from astropy.io import fits

ROOT = Path(__file__).parents[1]
SCRIPT_DIR = ROOT / "jw/subagents/solar/skills/solar-cycle/scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


loader = _load("load_polar_huairou")
merger = _load("merge_polar_outputs")


def _header(bitpix: int, shape: tuple[int, ...], camera: str) -> dict:
    header = {
        "BITPIX": bitpix,
        "NAXIS": len(shape),
        "CAMERA": camera,
        "T_START": "2015-10-2 1:14:29",
        "HSOS_NO": "15npl",
        "CALIBRAT": "10000",
    }
    for axis, size in enumerate(reversed(shape), start=1):
        header[f"NAXIS{axis}"] = size
    return header


@pytest.mark.parametrize(
    ("shape", "bitpix", "camera", "raw_value", "expected", "normalization"),
    [
        ((480, 640), 16, "PULNIX 6701AN", 256, 1, "pulnix-little-endian-byteswap"),
        (
            (2, 1000, 992),
            32,
            "PULNIX 6701AN",
            16777216,
            1,
            "pulnix-little-endian-byteswap",
        ),
        ((2, 992, 992), 32, "IMPERX 1M48", 123, 123, "fits-standard-byte-order"),
    ],
)
def test_known_fits_byte_order(
    shape, bitpix, camera, raw_value, expected, normalization
):
    dtype = ">i2" if bitpix == 16 else ">i4"
    raw = np.full(shape, raw_value, dtype=dtype)
    decoded, label = loader.normalize_fits_data(raw, _header(bitpix, shape, camera))
    assert decoded.dtype == np.float64
    assert decoded.flat[0] == expected
    assert label == normalization


def test_uint8_synthetic_fits_round_trip(tmp_path: Path):
    path = tmp_path / "L515npl.fit"
    data = np.full((480, 640), 7, dtype=np.uint8)
    hdu = fits.PrimaryHDU(data)
    hdu.header["HSOS_NO"] = "15npl"
    hdu.header["T_START"] = "2015-10-2 1:14:29"
    hdu.header["CAMERA"] = "PULNIX 6701AN"
    hdu.writeto(path)

    raw, header = loader._read_fits_image(path)
    decoded, normalization = loader.normalize_fits_data(raw, header)
    assert np.all(decoded == 7)
    assert normalization == "fits-standard-byte-order"


def test_unknown_fits_layout_is_rejected():
    data = np.zeros((100, 100), dtype=">i2")
    with pytest.raises(ValueError, match="Unsupported FITS layout"):
        loader.normalize_fits_data(data, _header(16, data.shape, "UNKNOWN"))


def test_cube_signals_and_calibration():
    cube = np.array([[[6.0, 3.0]], [[2.0, -3.0]]])
    signals = loader.compute_cube_signals(cube, {"CALIBRAT": 10})
    np.testing.assert_allclose(signals["difference"][0, 0], 4)
    np.testing.assert_allclose(signals["vi"][0, 0], 0.5)
    np.testing.assert_allclose(signals["calibrated_vi"][0, 0], 5)
    assert np.isnan(signals["vi"][0, 1])


def test_cube_requires_explicit_signal_and_checks_plane():
    cube = np.ones((2, 4, 4), dtype=float)
    with pytest.raises(ValueError, match="requires explicit"):
        loader.select_fits_signal(cube, {}, None)
    with pytest.raises(ValueError, match="out of range"):
        loader.select_fits_signal(cube, {}, None, plane=2)
    selected, definition, unit, status = loader.select_fits_signal(
        cube, {}, "difference"
    )
    assert not selected.any()
    assert definition == "difference"
    assert unit == "detector_count_proxy"
    assert status == "uncalibrated"


@pytest.mark.parametrize(
    ("kwargs", "expected_ratio"),
    [
        (
            {
                "cap_rows": 2,
                "center_box": (2, 2),
                "aperture_mode": "polar-strip",
            },
            1.0,
        ),
        (
            {"center_radius": 2, "aperture_mode": "center-circle"},
            1.0,
        ),
        (
            {"aperture_box": (4, 4), "aperture_mode": "center-box"},
            1.0,
        ),
    ],
)
def test_aperture_geometries(kwargs, expected_ratio):
    image = np.full((10, 10), 5.0)
    result = loader.extract_features(image, "N", **kwargs)
    assert result["field_mean_abs"] == 0
    assert result["valid_pixel_ratio"] == expected_ratio


def test_nonfinite_zero_and_saturation_masks():
    image = np.full((10, 10), 5.0)
    image[0, 0] = 0
    image[0, 1] = np.nan
    result = loader.extract_features(
        image,
        "N",
        cap_rows=2,
        center_box=(2, 2),
        aperture_mode="polar-strip",
    )
    assert result["valid_pixel_ratio"] == pytest.approx(0.9)


def test_degenerate_fits_features_are_rejected():
    with pytest.raises(ValueError, match="degenerate polar signal"):
        loader._validate_fits_features({"field_mean_abs": 0.0})
    loader._validate_fits_features({"field_mean_abs": 0.01})


def test_unvalidated_geometry_requires_explicit_diagnostic_override():
    with pytest.raises(ValueError, match="no validated solar WCS"):
        loader._require_validated_geometry("imperx_fit32_2018_2026", False)
    loader._require_validated_geometry("imperx_fit32_2018_2026", True)
    loader._require_validated_geometry("imperx_fit32_2015_2017", False)


def test_filters_wpl_and_small_view():
    assert loader._should_skip_fits({"HSOS_NO": "10wpl"}, "L510wpl.fit", False)
    assert loader._should_skip_fits(
        {"HSOS_NO": "15npl", "CONTENT": "S"}, "S515npl.fit", False
    )
    assert loader._should_skip_fits(
        {"HSOS_NO": "15npl", "CONTENT": "L"}, "L515npl.fit", False
    ) is None


def test_filename_hemisphere_fallback_when_hsos_number_is_unhelpful():
    header = {"HSOS_NO": "AR1234", "CONTENT": "L"}
    assert loader._hemisphere_from_header(header, "L503npl18041028.fit") == "N"
    assert loader._should_skip_fits(header, "L503spl18040901.fit", False) is None


def test_parse_fits_metadata():
    data = np.zeros((2, 992, 992))
    header = _header(32, data.shape, "IMPERX 1M48")
    meta = loader.parse_fits_meta(Path("L515npl.fit"), header, data)
    assert meta["date"] == "2015-10-02"
    assert meta["hemisphere"] == "N"
    assert meta["shape"] == (992, 992)
    assert meta["n_planes"] == 2


def test_parse_fits_date_uses_audited_path_fallback():
    path = Path("2024/12/20241216/L524npl241216024748.fit")
    assert loader._parse_fits_date({}, path) == pd.Timestamp("2024-12-16 02:47:48")
    with pytest.raises(ValueError, match="dates disagree"):
        loader._parse_fits_date(
            {}, Path("2024/12/20241217/L524npl241216024748.fit")
        )


def test_hsos_2026_schema_v2_is_strictly_recognized(tmp_path: Path):
    path = tmp_path / "2026" / "20260528" / "fits" / "L526npl260528065402.fit"
    path.parent.mkdir(parents=True)
    plane0 = np.full((992, 992), 1000, dtype=np.int32)
    plane1 = np.full((992, 992), 900, dtype=np.int32)
    plane0[400:600, 400:600] = 1200
    hdu = fits.PrimaryHDU(np.stack([plane0, plane1]))
    hdu.header["BSCALE"] = 1
    hdu.header["BZERO"] = 32767
    hdu.header["CONTENT"] = "L"
    hdu.header["HSOS_NUMBER"] = "26npl"
    hdu.header["TIME_OBS"] = "2026-05-28 06:53:07"
    hdu.header["CALIBRAT"] = 10000
    hdu.header["SIZE_PIX"] = "0.242*2.242 ARC."
    hdu.header["WAVE"] = 5324
    hdu.header["STOKES"] = 3
    hdu.writeto(path)

    raw, header = loader._read_fits_image(path)
    assert header["BITPIX"] == 32
    assert raw.dtype == np.float64
    decoded, normalization = loader.normalize_fits_data(raw, header)
    assert normalization == "fits-bscale-bzero-standard"
    assert decoded[0, 0, 0] == 33767
    meta = loader.parse_fits_meta(path, header, raw)
    assert meta["date"] == "2026-05-28"
    assert meta["hemisphere"] == "N"
    assert (
        loader._instrument_epoch(
            meta["shape"],
            meta["camera"],
            meta["year"],
            header=header,
            n_planes=meta["n_planes"],
        )
        == "hsos_fit32_2026_schema_v2"
    )
    record = loader.process_file_fits(
        path,
        tmp_path,
        fit_signal="calibrated_vi",
        fit_aperture_mode="center-circle",
        allow_unvalidated_geometry=True,
    )
    assert record["instrument_epoch"] == "hsos_fit32_2026_schema_v2"
    assert record["byte_order_normalization"] == "fits-bscale-bzero-standard"


def test_hsos_2026_schema_v3_requires_audited_hybrid_header():
    header = {
        "BITPIX": 32,
        "NAXIS": 3,
        "NAXIS1": 992,
        "NAXIS2": 992,
        "NAXIS3": 2,
        "BSCALE": 1,
        "BZERO": 32767,
        "CONTENT": "L",
        "HSOS_NO": "26npl",
        "T_START": "2026-07-07 02:50:10",
        "CALIBRAT": 10000,
        "SIZE_PIX": "0.242*2.242 ARC.",
        "STOKES": 3,
    }
    assert loader._is_hsos_schema_v3(header, (992, 992), 2)
    assert (
        loader._instrument_epoch(
            (992, 992), "unknown", 2026, header=header, n_planes=2
        )
        == "hsos_fit32_2026_schema_v3"
    )
    assert not loader._is_hsos_schema_v3(
        {**header, "SIZE_PIX": "unknown"}, (992, 992), 2
    )


def test_imperx_epoch_distinguishes_new_archive_cohort():
    assert (
        loader._instrument_epoch((992, 992), "IMPERX 1M48", 2014)
        == "imperx_fit32_2014"
    )
    assert (
        loader._instrument_epoch((992, 992), "IMPERX 1M48", 2015)
        == "imperx_fit32_2015_2017"
    )
    assert (
        loader._instrument_epoch((992, 992), "IMPERX 1M48", 2020)
        == "imperx_fit32_2018_2026"
    )
    assert (
        loader._instrument_epoch((992, 992), "IMPERX 1M48", 2026)
        == "imperx_fit32_2018_2026"
    )
    with pytest.raises(ValueError, match="acquisition year"):
        loader._instrument_epoch((992, 992), "IMPERX 1M48", 2013)


def test_legacy_dat_regression_and_metadata(tmp_path: Path):
    path = tmp_path / "1990" / "apr" / "01" / "l501npla.dat"
    path.parent.mkdir(parents=True)
    image = np.full(loader.LARGE_SHAPE, 12, dtype="<i2")
    path.write_bytes(image.tobytes() + b"legacy trailer")

    record = loader.process_file_dat(path, tmp_path)
    assert record["date"] == "1990-04-01"
    assert record["field_mean_abs"] == 0
    assert record["instrument_epoch"] == "legacy_dat_1987_2001"
    assert record["source_format"] == "dat-int16-le"


def test_aggregation_retains_scientific_metadata():
    rows = []
    for value in (2.0, 4.0):
        rows.append(
            {
                "date": "2015-01-02",
                "hemisphere": "N",
                "instrument_epoch": "imperx_fit32_2015_2017",
                "camera": "IMPERX 1M48",
                "source_format": "fits-bitpix32",
                "signal_definition": "vi",
                "signal_unit": "dimensionless",
                "calibration_status": "derived",
                "byte_order_normalization": "fits-standard-byte-order",
                "field_mean_raw": value,
                "field_mean_center": 0.0,
                "field_mean_corrected": value,
                "field_mean_abs": value,
                "valid_pixel_ratio": 1.0,
                "file_path": f"file-{value}",
            }
        )
    daily = loader.aggregate_daily(pd.DataFrame(rows))
    monthly = loader.aggregate_monthly(daily)
    assert daily.loc[0, "field_mean_abs"] == 3
    assert daily.loc[0, "n_obs"] == 2
    assert monthly.loc[0, "instrument_epoch"] == "imperx_fit32_2015_2017"


def test_merge_adds_legacy_metadata_and_rejects_duplicates(tmp_path: Path):
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    output = tmp_path / "merged.csv"
    pd.DataFrame(
        [{"date": "1990-01-01", "hemisphere": "N", "field_mean_abs": 1}]
    ).to_csv(first, index=False)
    pd.DataFrame(
        [{"date": "2002-01-01", "hemisphere": "N", "field_mean_abs": 2}]
    ).to_csv(second, index=False)

    merged = merger.merge_csvs([first, second], output, monthly=False)
    assert output.exists()
    assert set(merger.METADATA_DEFAULTS) <= set(merged.columns)

    duplicate = tmp_path / "duplicate.csv"
    pd.DataFrame(
        [{"date": "1990-01-01", "hemisphere": "N", "field_mean_abs": 3}]
    ).to_csv(duplicate, index=False)
    with pytest.raises(ValueError, match="Duplicate"):
        merger.merge_csvs([first, duplicate], output, monthly=False)
