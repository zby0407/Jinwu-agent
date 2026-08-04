#!/usr/bin/env python3
"""Validate Huairou SMFT polar FITS against user-supplied local HMI files.

No network access is used.  A JSON manifest supplies same-day SMFT, HMI LOS
magnetogram, HMI continuum, and an affine map from SMFT pixels to HMI pixels.
The affine convention is::

    x_hmi = a00 * x_smft + a01 * y_smft + a02
    y_hmi = a10 * x_smft + a11 * y_smft + a12
"""

from __future__ import annotations

import argparse
import json
import re
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from scipy import ndimage

REQUIRED_HMI_WCS_KEYS = (
    "CTYPE1",
    "CTYPE2",
    "CRPIX1",
    "CRPIX2",
    "CDELT1",
    "CDELT2",
    "CRLT_OBS",
    "RSUN_OBS",
)
DEFAULT_THRESHOLDS = {
    "latitude_min_deg": 60.0,
    "latitude_max_deg": 75.0,
    "central_meridian_max_deg": 50.0,
    "mu_min": 0.25,
    "weak_field_abs_max_g": 800.0,
    "min_mask_pixels": 100,
    "min_pairs_per_hemisphere": 3,
    "registration_correlation_min": 0.30,
    "signed_field_correlation_min": 0.30,
    "regional_mean_sign_fraction_min": 0.80,
    "regional_mean_abs_min_g": 1.0,
    "calibrat_relative_tolerance": 0.25,
    "max_time_separation_minutes": 120.0,
}


def _first_image_hdu(path: Path, required_ndim: int = 2) -> tuple[np.ndarray, fits.Header]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with fits.open(path, memmap=False, ignore_missing_end=True) as hdul:
            for hdu in hdul:
                if hdu.data is not None and np.asarray(hdu.data).ndim == required_ndim:
                    return np.asarray(hdu.data, dtype=float), hdu.header.copy()
    raise ValueError(f"no {required_ndim}-D image HDU in {path}")


def read_smft(path: Path) -> tuple[np.ndarray, fits.Header]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with fits.open(path, memmap=False, ignore_missing_end=True) as hdul:
            data = np.asarray(hdul[0].data, dtype=float)
            header = hdul[0].header.copy()
    if data.ndim != 3 or data.shape[0] != 2:
        raise ValueError(f"expected two SMFT planes, got {data.shape} in {path}")
    return data, header


def smft_observation_time(path: Path) -> datetime:
    match = re.search(r"(?:npl|spl)(\d{6})\d{6}", path.stem, re.IGNORECASE)
    if not match:
        raise ValueError(f"cannot parse YYMMDDhhmmss from {path.name}")
    full_match = re.search(r"(?:npl|spl)(\d{12})", path.stem, re.IGNORECASE)
    assert full_match is not None
    value = full_match.group(1)
    century = "19" if int(value[:2]) >= 80 else "20"
    return datetime.strptime(century + value, "%Y%m%d%H%M%S")


def fits_observation_time(header: fits.Header) -> datetime:
    for key in ("T_OBS", "DATE-OBS", "DATE_OBS", "DATE"):
        value = header.get(key)
        if value:
            match = re.search(
                r"(\d{4})[.\-/](\d{2})[.\-/](\d{2})"
                r"(?:[T_ ](\d{2}):?(\d{2}):?(\d{2})(?:\.\d+)?)?",
                str(value),
            )
            if match:
                parts = [int(part) if part is not None else 0 for part in match.groups()]
                return datetime(*parts)
    raise ValueError("HMI header has no parseable observation time")


def smft_observation_date(path: Path) -> str:
    return smft_observation_time(path).date().isoformat()


def fits_observation_date(header: fits.Header) -> str:
    return fits_observation_time(header).date().isoformat()


def validate_matching_hmi_grids(
    magnetogram_header: fits.Header,
    continuum_header: fits.Header,
) -> None:
    for key in ("CTYPE1", "CTYPE2"):
        if str(magnetogram_header.get(key)) != str(continuum_header.get(key)):
            raise ValueError(f"HMI magnetogram/continuum {key} differs")
    for key in ("CRPIX1", "CRPIX2", "CRVAL1", "CRVAL2", "CDELT1", "CDELT2"):
        first = float(magnetogram_header.get(key, np.nan))
        second = float(continuum_header.get(key, np.nan))
        if not np.isfinite(first) or not np.isfinite(second) or not np.isclose(first, second):
            raise ValueError(f"HMI magnetogram/continuum {key} differs")


def affine_coordinates(shape: tuple[int, int], matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    matrix = np.asarray(matrix, dtype=float)
    if matrix.shape != (2, 3) or not np.isfinite(matrix).all():
        raise ValueError("smft_to_hmi_affine must be a finite 2x3 matrix")
    y, x = np.indices(shape, dtype=float)
    x_hmi = matrix[0, 0] * x + matrix[0, 1] * y + matrix[0, 2]
    y_hmi = matrix[1, 0] * x + matrix[1, 1] * y + matrix[1, 2]
    return x_hmi, y_hmi


def affine_from_control_points(
    smft_points: np.ndarray, hmi_points: np.ndarray
) -> tuple[np.ndarray, float]:
    """Fit the documented x/y affine map and return control-point RMS."""
    smft_points = np.asarray(smft_points, dtype=float)
    hmi_points = np.asarray(hmi_points, dtype=float)
    if (
        smft_points.ndim != 2
        or smft_points.shape[1:] != (2,)
        or hmi_points.shape != smft_points.shape
        or len(smft_points) < 3
        or not np.isfinite(smft_points).all()
        or not np.isfinite(hmi_points).all()
    ):
        raise ValueError("control points must be matching finite Nx2 arrays with N >= 3")
    design = np.column_stack([smft_points, np.ones(len(smft_points))])
    coefficients, _, rank, _ = np.linalg.lstsq(design, hmi_points, rcond=None)
    if rank < 3:
        raise ValueError("SMFT control points are collinear")
    predicted = design @ coefficients
    rms = float(np.sqrt(np.mean(np.sum((predicted - hmi_points) ** 2, axis=1))))
    return coefficients.T, rms


def pair_affine(pair: dict) -> tuple[np.ndarray, int | None, float | None]:
    if "smft_to_hmi_affine" in pair:
        matrix = np.asarray(pair["smft_to_hmi_affine"], dtype=float)
        if matrix.shape != (2, 3) or not np.isfinite(matrix).all():
            raise ValueError("smft_to_hmi_affine must be a finite 2x3 matrix")
        return matrix, None, None
    if "smft_control_points" in pair and "hmi_control_points" in pair:
        matrix, rms = affine_from_control_points(
            pair["smft_control_points"], pair["hmi_control_points"]
        )
        return matrix, len(pair["smft_control_points"]), rms
    raise ValueError("pair needs an affine matrix or matching SMFT/HMI control points")


def sample_image(image: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return ndimage.map_coordinates(
        image,
        [y, x],
        order=1,
        mode="constant",
        cval=np.nan,
        prefilter=False,
    )


def helioprojective_arcsec(
    header: fits.Header, x: np.ndarray, y: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    if not all(key in header for key in REQUIRED_HMI_WCS_KEYS):
        missing = [key for key in REQUIRED_HMI_WCS_KEYS if key not in header]
        raise ValueError(f"HMI WCS missing {missing}")
    wcs = WCS(header, naxis=2, preserve_units=True)
    world = wcs.all_pix2world(x, y, 0)
    output = []
    for values, unit in zip(world, wcs.wcs.cunit, strict=True):
        name = str(unit).lower()
        if name in {"deg", "degree"}:
            values = values * 3600.0
        elif name not in {"arcsec", "arcsecond"}:
            raise ValueError(f"unsupported HMI WCS angular unit {unit!s}")
        output.append(np.asarray(values, dtype=float))
    return output[0], output[1]


def heliographic_geometry(
    x_arcsec: np.ndarray,
    y_arcsec: np.ndarray,
    rsun_arcsec: float,
    b0_deg: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return latitude, central-meridian distance, and mu for HPLN/HPLT."""
    x = x_arcsec / rsun_arcsec
    y = y_arcsec / rsun_arcsec
    rho2 = x * x + y * y
    mu = np.sqrt(np.clip(1.0 - rho2, 0.0, None))
    b0 = np.deg2rad(b0_deg)
    latitude = np.arcsin(np.clip(y * np.cos(b0) + mu * np.sin(b0), -1, 1))
    cmd = np.arctan2(x, mu * np.cos(b0) - y * np.sin(b0))
    outside = rho2 > 1.0
    latitude[outside] = np.nan
    cmd[outside] = np.nan
    mu[outside] = np.nan
    return np.rad2deg(latitude), np.rad2deg(cmd), mu


def fixed_latitude_mask(
    latitude: np.ndarray,
    cmd: np.ndarray,
    mu: np.ndarray,
    hemisphere: str,
    thresholds: dict,
) -> np.ndarray:
    signed_latitude = latitude if hemisphere == "N" else -latitude
    return (
        np.isfinite(signed_latitude)
        & (signed_latitude >= thresholds["latitude_min_deg"])
        & (signed_latitude <= thresholds["latitude_max_deg"])
        & (np.abs(cmd) <= thresholds["central_meridian_max_deg"])
        & (mu >= thresholds["mu_min"])
    )


def _correlation(first: np.ndarray, second: np.ndarray, valid: np.ndarray) -> float:
    valid = valid & np.isfinite(first) & np.isfinite(second)
    if np.count_nonzero(valid) < 3:
        return float("nan")
    a = first[valid]
    b = second[valid]
    if np.std(a) == 0 or np.std(b) == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def _gradient_magnitude(image: np.ndarray) -> np.ndarray:
    smooth = ndimage.gaussian_filter(image, 1.5)
    return np.hypot(ndimage.sobel(smooth, axis=0), ndimage.sobel(smooth, axis=1))


def _linear_calibration(x: np.ndarray, y: np.ndarray, valid: np.ndarray) -> tuple[float, float]:
    valid = valid & np.isfinite(x) & np.isfinite(y)
    if np.count_nonzero(valid) < 3 or np.std(x[valid]) == 0:
        return float("nan"), float("nan")
    slope, intercept = np.polyfit(x[valid], y[valid], 1)
    return float(slope), float(intercept)


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base / path


def inspect_pair(pair: dict, base: Path, thresholds: dict) -> tuple[dict, dict]:
    smft_path = _resolve(base, pair["smft"])
    hmi_mag_path = _resolve(base, pair["hmi_magnetogram"])
    hmi_cont_path = _resolve(base, pair["hmi_continuum"])
    hemisphere = str(pair.get("hemisphere", "")).upper()
    if hemisphere not in {"N", "S"}:
        raise ValueError("pair hemisphere must be N or S")

    smft, smft_header = read_smft(smft_path)
    hmi_mag, hmi_header = _first_image_hdu(hmi_mag_path)
    hmi_cont, hmi_cont_header = _first_image_hdu(hmi_cont_path)
    if hmi_mag.shape != hmi_cont.shape:
        raise ValueError(
            f"HMI magnetogram/continuum shapes differ: {hmi_mag.shape} != {hmi_cont.shape}"
        )
    validate_matching_hmi_grids(hmi_header, hmi_cont_header)
    observation_times = [
        smft_observation_time(smft_path),
        fits_observation_time(hmi_header),
        fits_observation_time(hmi_cont_header),
    ]
    dates = {value.date().isoformat() for value in observation_times}
    if len(dates) != 1:
        raise ValueError(f"not same-day data: {sorted(dates)}")
    time_separation_minutes = (
        max(observation_times) - min(observation_times)
    ).total_seconds() / 60.0
    if time_separation_minutes > thresholds["max_time_separation_minutes"]:
        raise ValueError(
            f"observation separation {time_separation_minutes:.1f} min exceeds threshold"
        )

    affine, control_point_count, control_point_rms = pair_affine(pair)
    x_hmi, y_hmi = affine_coordinates(smft.shape[1:], affine)
    sampled_mag = sample_image(hmi_mag, x_hmi, y_hmi)
    sampled_cont = sample_image(hmi_cont, x_hmi, y_hmi)
    x_arcsec, y_arcsec = helioprojective_arcsec(hmi_header, x_hmi, y_hmi)
    latitude, cmd, mu = heliographic_geometry(
        x_arcsec,
        y_arcsec,
        float(hmi_header["RSUN_OBS"]),
        float(hmi_header["CRLT_OBS"]),
    )
    mask = fixed_latitude_mask(latitude, cmd, mu, hemisphere, thresholds)

    intensity = smft[0] + smft[1]
    denominator_valid = np.isfinite(intensity) & (np.abs(intensity) > 0)
    raw_v_over_i = np.full(intensity.shape, np.nan)
    raw_v_over_i[denominator_valid] = (
        (smft[0] - smft[1])[denominator_valid] / intensity[denominator_valid]
    )
    valid = (
        mask
        & denominator_valid
        & np.isfinite(sampled_mag)
        & (np.abs(sampled_mag) <= thresholds["weak_field_abs_max_g"])
    )
    registration_valid = mask & np.isfinite(intensity) & np.isfinite(sampled_cont)
    registration_corr = _correlation(
        _gradient_magnitude(intensity),
        _gradient_magnitude(np.nan_to_num(sampled_cont, nan=np.nanmedian(sampled_cont))),
        registration_valid,
    )
    raw_field_corr = _correlation(raw_v_over_i, sampled_mag, valid)
    raw_slope, raw_intercept = _linear_calibration(raw_v_over_i, sampled_mag, valid)
    record = {
        "date": dates.pop(),
        "time_separation_minutes": time_separation_minutes,
        "hemisphere": hemisphere,
        "smft": str(smft_path),
        "hmi_magnetogram": str(hmi_mag_path),
        "hmi_continuum": str(hmi_cont_path),
        "smft_to_hmi_affine": affine.tolist(),
        "control_point_count": control_point_count,
        "control_point_rms_hmi_px": control_point_rms,
        "mask_pixels": int(np.count_nonzero(mask)),
        "valid_field_pixels": int(np.count_nonzero(valid)),
        "registration_correlation": registration_corr,
        "raw_field_correlation": raw_field_corr,
        "raw_calibration_slope_g": raw_slope,
        "raw_calibration_intercept_g": raw_intercept,
        "raw_smft_region_mean": float(np.nanmean(raw_v_over_i[valid])),
        "hmi_region_mean_g": float(np.nanmean(sampled_mag[valid])),
        "calibrat_header": float(smft_header.get("CALIBRAT", np.nan)),
    }
    arrays = {
        "raw_v_over_i": raw_v_over_i,
        "sampled_hmi_magnetogram": sampled_mag,
        "latitude_deg": latitude,
        "cmd_deg": cmd,
        "mu": mu,
        "mask": mask,
        "valid": valid,
    }
    return record, arrays


def summarize(records: list[dict], thresholds: dict) -> dict:
    finite_corr = np.asarray(
        [record["raw_field_correlation"] for record in records], dtype=float
    )
    finite_corr = finite_corr[np.isfinite(finite_corr)]
    median_raw_corr = float(np.median(finite_corr)) if finite_corr.size else float("nan")
    p0_minus_p1_sign = 1 if median_raw_corr >= 0 else -1
    signed_correlations = p0_minus_p1_sign * finite_corr

    sign_checks: dict[str, list[bool]] = {"N": [], "S": []}
    slopes = []
    calibrat_values = []
    for record in records:
        signed_smft_mean = p0_minus_p1_sign * record["raw_smft_region_mean"]
        hmi_mean = record["hmi_region_mean_g"]
        if (
            np.isfinite(signed_smft_mean)
            and np.isfinite(hmi_mean)
            and abs(hmi_mean) >= thresholds["regional_mean_abs_min_g"]
        ):
            sign_checks[record["hemisphere"]].append(
                np.sign(signed_smft_mean) == np.sign(hmi_mean)
            )
        slope = p0_minus_p1_sign * record["raw_calibration_slope_g"]
        if np.isfinite(slope):
            slopes.append(slope)
        if np.isfinite(record["calibrat_header"]):
            calibrat_values.append(record["calibrat_header"])

    counts = {
        hemisphere: sum(record["hemisphere"] == hemisphere for record in records)
        for hemisphere in ("N", "S")
    }
    enough_pairs = all(
        count >= thresholds["min_pairs_per_hemisphere"] for count in counts.values()
    )
    registration_pass = bool(
        records
        and all(
            record["mask_pixels"] >= thresholds["min_mask_pixels"]
            and record["registration_correlation"]
            >= thresholds["registration_correlation_min"]
            for record in records
        )
    )
    geometry_pass = enough_pairs and registration_pass
    median_signed_corr = (
        float(np.median(signed_correlations)) if signed_correlations.size else float("nan")
    )
    all_sign_checks = sign_checks["N"] + sign_checks["S"]
    enough_unambiguous_signs = all(
        len(sign_checks[hemisphere]) >= thresholds["min_pairs_per_hemisphere"]
        for hemisphere in ("N", "S")
    )
    sign_fraction = (
        float(np.mean(all_sign_checks)) if all_sign_checks else float("nan")
    )
    sign_pass = bool(
        enough_pairs
        and enough_unambiguous_signs
        and finite_corr.size == len(records)
        and registration_pass
        and median_signed_corr >= thresholds["signed_field_correlation_min"]
        and sign_fraction >= thresholds["regional_mean_sign_fraction_min"]
    )
    median_slope = float(np.median(slopes)) if slopes else float("nan")
    median_calibrat = (
        float(np.median(calibrat_values)) if calibrat_values else float("nan")
    )
    calibrat_relative_error = (
        abs(median_slope - median_calibrat) / abs(median_calibrat)
        if np.isfinite(median_slope) and np.isfinite(median_calibrat) and median_calibrat
        else float("nan")
    )
    calibrat_pass = bool(
        sign_pass
        and len(slopes) == len(records)
        and np.isfinite(calibrat_relative_error)
        and calibrat_relative_error <= thresholds["calibrat_relative_tolerance"]
    )
    mapping = "P0=Vl, P1=Vr" if p0_minus_p1_sign == 1 else "P0=Vr, P1=Vl"
    return {
        "pair_counts": counts,
        "p0_p1_mapping_inferred": mapping if sign_pass else None,
        "metrics": {
            "median_signed_field_correlation": median_signed_corr,
            "regional_mean_sign_fraction": sign_fraction,
            "median_calibration_slope_g": median_slope,
            "median_calibrat_header": median_calibrat,
            "calibrat_relative_error": calibrat_relative_error,
        },
        "gates": {
            "smft_hmi_registration": "pass" if registration_pass else "fail",
            "hmi_wcs_geometry": "pass" if geometry_pass else "fail",
            "fixed_heliographic_latitude_mask": "pass" if geometry_pass else "fail",
            "p0_p1_sign": "pass" if sign_pass else "fail",
            "signed_field_consistency": "pass" if sign_pass else "fail",
            "calibrat_semantics": "pass" if calibrat_pass else "fail",
        },
    }


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (float, np.floating)) and not np.isfinite(value):
        return None
    if isinstance(value, np.integer):
        return int(value)
    return value


def run_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    thresholds = {**DEFAULT_THRESHOLDS, **manifest.get("thresholds", {})}
    records = []
    errors = []
    for index, pair in enumerate(manifest.get("pairs", [])):
        try:
            record, _ = inspect_pair(pair, path.parent, thresholds)
            records.append(record)
        except Exception as exc:
            errors.append({"pair_index": index, "error": repr(exc)})
    result = summarize(records, thresholds)
    result.update({"manifest": str(path), "thresholds": thresholds, "records": records, "errors": errors})
    if errors:
        for gate in result["gates"]:
            result["gates"][gate] = "fail"
    return _json_safe(result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/polar_validation/hmi_reference_audit.json"),
    )
    args = parser.parse_args()
    result = run_manifest(args.manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["gates"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
