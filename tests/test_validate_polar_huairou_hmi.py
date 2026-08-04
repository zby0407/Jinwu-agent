from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

ROOT = Path(__file__).parents[1]
SCRIPT = (
    ROOT
    / "jw/subagents/solar/skills/solar-cycle/scripts/validate_polar_huairou_hmi.py"
)
SPEC = importlib.util.spec_from_file_location("validate_polar_huairou_hmi", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def test_smft_date_and_affine_convention():
    path = Path("L523npl230101123456.fit")
    assert validator.smft_observation_date(path) == "2023-01-01"
    x, y = validator.affine_coordinates(
        (2, 2), np.asarray([[2, 0, 10], [0, 3, 20]])
    )
    np.testing.assert_allclose(x, [[10, 12], [10, 12]])
    np.testing.assert_allclose(y, [[20, 20], [23, 23]])


def test_hmi_timestamp_parser_accepts_jsoc_t_obs():
    header = fits.Header({"T_OBS": "2023.01.01_12:34:56.75_TAI"})
    assert validator.fits_observation_time(header).isoformat() == "2023-01-01T12:34:56"


def test_heliographic_disk_center_tracks_b0():
    latitude, cmd, mu = validator.heliographic_geometry(
        np.asarray([[0.0]]), np.asarray([[0.0]]), 960.0, 7.0
    )
    assert latitude[0, 0] == pytest.approx(7.0)
    assert cmd[0, 0] == pytest.approx(0.0)
    assert mu[0, 0] == pytest.approx(1.0)


def test_hmi_like_wcs_returns_helioprojective_arcsec():
    header = fits.Header(
        {
            "NAXIS": 2,
            "NAXIS1": 5,
            "NAXIS2": 5,
            "CTYPE1": "HPLN-TAN",
            "CTYPE2": "HPLT-TAN",
            "CUNIT1": "arcsec",
            "CUNIT2": "arcsec",
            "CRPIX1": 3.0,
            "CRPIX2": 3.0,
            "CRVAL1": 0.0,
            "CRVAL2": 0.0,
            "CDELT1": 0.5,
            "CDELT2": 0.5,
            "CRLT_OBS": 0.0,
            "RSUN_OBS": 960.0,
        }
    )
    x_arcsec, y_arcsec = validator.helioprojective_arcsec(
        header, np.asarray([[2.0, 3.0]]), np.asarray([[2.0, 2.0]])
    )
    np.testing.assert_allclose(x_arcsec, [[0.0, 0.5]], atol=1e-10)
    np.testing.assert_allclose(y_arcsec, [[0.0, 0.0]], atol=1e-10)


def test_affine_from_control_points_reports_residual():
    smft = np.asarray([[0, 0], [10, 0], [0, 10], [10, 10]], dtype=float)
    expected = np.asarray([[2, 0.1, 100], [-0.2, 3, 200]], dtype=float)
    design = np.column_stack([smft, np.ones(len(smft))])
    hmi = design @ expected.T
    measured, rms = validator.affine_from_control_points(smft, hmi)
    np.testing.assert_allclose(measured, expected, atol=1e-12)
    assert rms == pytest.approx(0.0, abs=1e-12)


def test_fixed_latitude_mask_selects_requested_hemisphere():
    thresholds = validator.DEFAULT_THRESHOLDS
    latitude = np.asarray([[65.0, -65.0, 80.0]])
    cmd = np.zeros_like(latitude)
    mu = np.ones_like(latitude)
    north = validator.fixed_latitude_mask(latitude, cmd, mu, "N", thresholds)
    south = validator.fixed_latitude_mask(latitude, cmd, mu, "S", thresholds)
    np.testing.assert_array_equal(north, [[True, False, False]])
    np.testing.assert_array_equal(south, [[False, True, False]])


def test_summary_passes_both_hemispheres_and_infers_plane_order():
    records = []
    for hemisphere in ("N", "S"):
        for index in range(3):
            hmi_mean = 5.0 if hemisphere == "N" else -5.0
            records.append(
                {
                    "hemisphere": hemisphere,
                    "mask_pixels": 1000,
                    "registration_correlation": 0.8,
                    "raw_field_correlation": -0.7 - 0.01 * index,
                    "raw_smft_region_mean": -hmi_mean / 10000,
                    "hmi_region_mean_g": hmi_mean,
                    "raw_calibration_slope_g": -10000.0,
                    "calibrat_header": 10000.0,
                }
            )
    summary = validator.summarize(records, validator.DEFAULT_THRESHOLDS)
    assert summary["p0_p1_mapping_inferred"] == "P0=Vr, P1=Vl"
    assert set(summary["gates"].values()) == {"pass"}
    assert summary["metrics"]["median_calibration_slope_g"] == pytest.approx(10000)


def test_json_safe_replaces_non_finite_values():
    assert validator._json_safe({"nan": float("nan"), "ok": 1.0}) == {
        "nan": None,
        "ok": 1.0,
    }


def test_manifest_end_to_end_with_synthetic_signed_pairs(tmp_path: Path):
    y, x = np.mgrid[:64, :64]
    intensity = 1000.0 + 40 * np.sin(x / 5.0) + 30 * np.cos(y / 7.0)
    pairs = []
    for hemisphere, code, latitude_sign in (("N", "npl", 1), ("S", "spl", -1)):
        for index in range(3):
            seconds = f"{index:02d}"
            stamp = f"2301011200{seconds}"
            hmi_field = latitude_sign * (
                5.0 + 15 * np.sin(x / 9.0) + 10 * np.cos(y / 11.0)
            )
            raw_v_over_i = -hmi_field / 10000.0
            smft = np.stack(
                [
                    intensity * (1 + raw_v_over_i) / 2,
                    intensity * (1 - raw_v_over_i) / 2,
                ]
            )
            smft_path = tmp_path / f"L523{code}{stamp}.fit"
            fits.PrimaryHDU(smft, header=fits.Header({"CALIBRAT": 10000.0})).writeto(
                smft_path
            )

            header = fits.Header(
                {
                    "CTYPE1": "HPLN-TAN",
                    "CTYPE2": "HPLT-TAN",
                    "CUNIT1": "arcsec",
                    "CUNIT2": "arcsec",
                    "CRPIX1": 32.5,
                    "CRPIX2": 32.5,
                    "CRVAL1": 0.0,
                    "CRVAL2": latitude_sign * 850.0,
                    "CDELT1": 0.5,
                    "CDELT2": 0.5,
                    "CRLT_OBS": 0.0,
                    "RSUN_OBS": 960.0,
                    "T_OBS": f"2023.01.01_12:00:{seconds}_TAI",
                }
            )
            mag_path = tmp_path / f"mag_{hemisphere}_{index}.fits"
            cont_path = tmp_path / f"cont_{hemisphere}_{index}.fits"
            fits.PrimaryHDU(hmi_field, header=header).writeto(mag_path)
            fits.PrimaryHDU(intensity, header=header).writeto(cont_path)
            pairs.append(
                {
                    "smft": smft_path.name,
                    "hemisphere": hemisphere,
                    "hmi_magnetogram": mag_path.name,
                    "hmi_continuum": cont_path.name,
                    "smft_to_hmi_affine": [[1, 0, 0], [0, 1, 0]],
                }
            )

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"pairs": pairs}), encoding="utf-8")
    result = validator.run_manifest(manifest_path)
    assert result["errors"] == []
    assert result["p0_p1_mapping_inferred"] == "P0=Vr, P1=Vl"
    assert set(result["gates"].values()) == {"pass"}
    assert result["metrics"]["median_calibration_slope_g"] == pytest.approx(10000)
