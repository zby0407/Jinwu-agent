#!/usr/bin/env python3
"""Audit Huairou SMFT polar FITS gates without downloading reference data."""

from __future__ import annotations

import argparse
import csv
import json
import warnings
from collections import defaultdict
from pathlib import Path

import numpy as np
from astropy.io import fits
from scipy import ndimage

STANDARD_WCS_KEYS = (
    "CTYPE1",
    "CTYPE2",
    "CRPIX1",
    "CRPIX2",
    "CDELT1",
    "CDELT2",
)
REGISTRATION_CORRELATION_MIN = 0.99
REGISTRATION_SHIFT_P95_MAX_PX = 0.1


def _hemisphere(path: Path) -> str | None:
    name = path.stem.lower()
    if "npl" in name:
        return "N"
    if "spl" in name:
        return "S"
    return None


def discover_polar_fits(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".fit", ".fits"}
        and _hemisphere(path) is not None
    )


def stratified_sample(
    paths: list[Path], root: Path, sample_per_group: int
) -> dict[tuple[str, str], list[Path]]:
    groups: dict[tuple[str, str], list[Path]] = defaultdict(list)
    for path in paths:
        relative = path.relative_to(root)
        year = relative.parts[0]
        hemisphere = _hemisphere(path)
        if hemisphere is not None:
            groups[(year, hemisphere)].append(path)

    sampled: dict[tuple[str, str], list[Path]] = {}
    for key, group in groups.items():
        group = sorted(group)
        count = min(sample_per_group, len(group))
        indices = np.linspace(0, len(group) - 1, count, dtype=int)
        sampled[key] = [group[index] for index in indices]
    return sampled


def phase_correlation_shift(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    """Return the subpixel y/x translation peak between two registered planes."""
    first_hp = ndimage.gaussian_filter(first, 1) - ndimage.gaussian_filter(first, 8)
    second_hp = ndimage.gaussian_filter(second, 1) - ndimage.gaussian_filter(second, 8)
    window = np.outer(np.hanning(first.shape[0]), np.hanning(first.shape[1]))
    first_hp = (first_hp - first_hp.mean()) * window
    second_hp = (second_hp - second_hp.mean()) * window

    first_fft = np.fft.rfftn(first_hp, axes=(0, 1))
    second_fft = np.fft.rfftn(second_hp, axes=(0, 1))
    cross_power = first_fft * np.conj(second_fft)
    cross_power /= np.maximum(np.abs(cross_power), 1e-20)
    correlation = np.fft.irfftn(cross_power, s=first.shape, axes=(0, 1))
    peak = np.unravel_index(np.argmax(correlation), correlation.shape)
    shift = np.asarray(peak, dtype=float)
    shape = np.asarray(first.shape)
    wrapped = shift > shape // 2
    shift[wrapped] -= shape[wrapped]

    for axis in range(2):
        lower = list(peak)
        upper = list(peak)
        lower[axis] = (lower[axis] - 1) % correlation.shape[axis]
        upper[axis] = (upper[axis] + 1) % correlation.shape[axis]
        y_lower = correlation[tuple(lower)]
        y_center = correlation[peak]
        y_upper = correlation[tuple(upper)]
        denominator = y_lower - 2 * y_center + y_upper
        if denominator != 0:
            shift[axis] += 0.5 * (y_lower - y_upper) / denominator
    return shift


def two_class_disk_mask(image: np.ndarray) -> np.ndarray:
    """Return a diagnostic bright-disc mask; this is not a heliographic mask."""
    smoothed = ndimage.gaussian_filter(image, 4)
    if min(smoothed.shape) > 80:
        values = smoothed[40:-40, 40:-40].ravel()
    else:
        values = smoothed.ravel()
    centers = np.percentile(values, [20, 80])
    for _ in range(20):
        labels = np.abs(values[:, None] - centers).argmin(axis=1)
        updated = np.asarray([np.median(values[labels == index]) for index in range(2)])
        if np.allclose(updated, centers):
            break
        centers = updated
    return smoothed > centers.mean()


def centered_circle_fraction(mask: np.ndarray, radius: int) -> float:
    rows, columns = mask.shape
    y, x = np.ogrid[:rows, :columns]
    cy, cx = rows / 2.0, columns / 2.0
    aperture = (y - cy) ** 2 + (x - cx) ** 2 <= radius**2
    return float(np.count_nonzero(mask & aperture) / np.count_nonzero(aperture))


def inspect_file(path: Path, root: Path, radius: int) -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with fits.open(path, memmap=False, ignore_missing_end=True) as hdul:
            header = hdul[0].header
            data = np.asarray(hdul[0].data, dtype=float)

    if data.ndim != 3 or data.shape[0] != 2:
        raise ValueError(f"expected two planes, got {data.shape}")
    shift = phase_correlation_shift(data[0], data[1])
    plane_correlation = float(np.corrcoef(data[0].ravel(), data[1].ravel())[0, 1])
    disk_mask = two_class_disk_mask(data[0] + data[1])
    relative = path.relative_to(root)
    return {
        "file": str(path),
        "year": relative.parts[0],
        "hemisphere": _hemisphere(path),
        "plane_correlation": plane_correlation,
        "shift_y_px": float(shift[0]),
        "shift_x_px": float(shift[1]),
        "shift_norm_px": float(np.hypot(*shift)),
        "calibrat": str(header.get("CALIBRAT", "")),
        "standard_wcs_complete": all(key in header for key in STANDARD_WCS_KEYS),
        "center_circle_disk_fraction": centered_circle_fraction(disk_mask, radius),
    }


def _group_summary(records: list[dict], archive_counts: dict) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for record in records:
        grouped[(record["year"], record["hemisphere"])].append(record)
    summaries = []
    for key in sorted(grouped):
        group = grouped[key]
        summaries.append(
            {
                "year": key[0],
                "hemisphere": key[1],
                "archive_files": archive_counts[key],
                "sample_files": len(group),
                "plane_correlation_min": min(
                    record["plane_correlation"] for record in group
                ),
                "shift_norm_px_max": max(record["shift_norm_px"] for record in group),
                "center_circle_disk_fraction_min": min(
                    record["center_circle_disk_fraction"] for record in group
                ),
                "center_circle_disk_fraction_max": max(
                    record["center_circle_disk_fraction"] for record in group
                ),
                "standard_wcs_complete_count": sum(
                    record["standard_wcs_complete"] for record in group
                ),
                "calibrat_values": sorted({record["calibrat"] for record in group}),
            }
        )
    return summaries


def run_audit(
    root: Path, sample_per_group: int, radius: int
) -> tuple[dict, list[dict]]:
    paths = discover_polar_fits(root)
    sampled = stratified_sample(paths, root, sample_per_group)
    archive_counts = defaultdict(int)
    for path in paths:
        archive_counts[(path.relative_to(root).parts[0], _hemisphere(path))] += 1

    records = []
    errors = []
    for key in sorted(sampled):
        for path in sampled[key]:
            try:
                records.append(inspect_file(path, root, radius))
            except Exception as exc:
                errors.append({"file": str(path), "error": repr(exc)})

    correlation_min = (
        min(record["plane_correlation"] for record in records) if records else None
    )
    shift_p95 = (
        float(np.percentile([r["shift_norm_px"] for r in records], 95))
        if records
        else None
    )
    registration_passed = bool(
        records
        and correlation_min is not None
        and correlation_min >= REGISTRATION_CORRELATION_MIN
        and shift_p95 is not None
        and shift_p95 <= REGISTRATION_SHIFT_P95_MAX_PX
    )
    summary = {
        "polar_dir": str(root),
        "archive_candidate_files": len(paths),
        "sample_files": len(records),
        "sample_errors": errors,
        "center_circle_radius_px": radius,
        "groups": _group_summary(records, archive_counts),
        "gates": {
            "p0_p1_registration": {
                "status": "pass" if registration_passed else "fail_or_not_tested",
                "plane_correlation_min": correlation_min,
                "plane_correlation_required_min": REGISTRATION_CORRELATION_MIN,
                "shift_norm_px_p95": shift_p95,
                "shift_norm_px_allowed_p95": REGISTRATION_SHIFT_P95_MAX_PX,
            },
            "p0_p1_sign": {"status": "not_tested_external_reference_required"},
            "calibrat_semantics": {"status": "partial_header_value_only"},
            "solar_wcs": {
                "status": (
                    "pass"
                    if records and all(r["standard_wcs_complete"] for r in records)
                    else "fail"
                )
            },
            "fixed_heliographic_latitude_mask": {"status": "not_implemented"},
            "solis_hmi_signed_consistency": {
                "status": "not_tested_local_reference_required"
            },
        },
    }
    return summary, records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--polar-dir", required=True, type=Path)
    parser.add_argument("--sample-per-group", type=int, default=12)
    parser.add_argument("--center-circle-radius", type=int, default=150)
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/polar_validation/audit.json")
    )
    parser.add_argument(
        "--records-output",
        type=Path,
        default=Path("artifacts/polar_validation/audit_records.csv"),
    )
    args = parser.parse_args()
    if args.sample_per_group <= 0:
        parser.error("--sample-per-group must be positive")
    if args.center_circle_radius <= 0:
        parser.error("--center-circle-radius must be positive")

    summary, records = run_audit(
        args.polar_dir, args.sample_per_group, args.center_circle_radius
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.records_output.parent.mkdir(parents=True, exist_ok=True)
    with args.records_output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]) if records else [])
        if records:
            writer.writeheader()
            writer.writerows(records)
    print(json.dumps(summary["gates"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
