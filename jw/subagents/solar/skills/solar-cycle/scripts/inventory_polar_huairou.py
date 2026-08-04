#!/usr/bin/env python3
"""Inventory Huairou SMFT polar FITS layouts before server processing."""

from __future__ import annotations

import argparse
import json
import warnings
from collections import Counter
from pathlib import Path

import load_polar_huairou as loader
import pandas as pd
from astropy.io import fits

WCS_KEYS = ("CTYPE1", "CTYPE2", "CRPIX1", "CRPIX2", "CDELT1", "CDELT2")
RECORD_COLUMNS = [
    "file",
    "file_size",
    "year",
    "hemisphere",
    "shape",
    "naxis",
    "bitpix",
    "camera",
    "calibrat",
    "wcs_complete",
    "instrument_epoch",
    "status",
    "error",
]


def discover_archive_fits(root: Path, start_year: int, end_year: int) -> list[Path]:
    """Return all FITS files in the requested year directories."""
    paths: list[Path] = []
    for year in range(start_year, end_year + 1):
        year_dir = root / str(year)
        if not year_dir.is_dir():
            continue
        for path in year_dir.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".fit", ".fits"}:
                paths.append(path)
    return sorted(paths)


def _is_polar_filename(path: Path) -> bool:
    name = path.stem.lower()
    return "npl" in name or "spl" in name


def discover_polar_fits(root: Path, start_year: int, end_year: int) -> list[Path]:
    """Return filename-labelled NPL/SPL FITS files in the requested years."""
    return [
        path
        for path in discover_archive_fits(root, start_year, end_year)
        if _is_polar_filename(path)
    ]


def inspect_file(path: Path, root: Path) -> dict:
    """Classify one FITS header without reading its image payload."""
    relative = path.relative_to(root)
    fallback_year = int(relative.parts[0])
    record = {
        "file": str(relative),
        "file_size": path.stat().st_size,
        "year": fallback_year,
        "hemisphere": "N" if "npl" in path.stem.lower() else "S",
        "shape": "",
        "naxis": "",
        "bitpix": "",
        "camera": "",
        "calibrat": "",
        "wcs_complete": False,
        "instrument_epoch": "",
        "status": "read_error",
        "error": "",
    }
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", fits.verify.VerifyWarning)
            warnings.simplefilter("ignore", UserWarning)
            header = dict(fits.getheader(path, 0, ignore_missing_end=True))

        naxis = loader._header_int(header, "NAXIS")
        bitpix = loader._header_int(header, "BITPIX")
        if naxis == 2:
            raw_shape = (
                loader._header_int(header, "NAXIS2"),
                loader._header_int(header, "NAXIS1"),
            )
            n_planes = 1
            plane_shape = raw_shape
        elif naxis == 3:
            raw_shape = (
                loader._header_int(header, "NAXIS3"),
                loader._header_int(header, "NAXIS2"),
                loader._header_int(header, "NAXIS1"),
            )
            n_planes = raw_shape[0]
            plane_shape = raw_shape[-2:]
        else:
            raise ValueError(f"Unsupported FITS NAXIS={naxis}")

        hemisphere = loader._hemisphere_from_header(header, path.name)
        if hemisphere is None:
            raise ValueError("Could not determine hemisphere from FITS header")
        camera = str(header.get("CAMERA", "")).strip() or "unknown"
        acquisition_year = loader._parse_fits_date(header, path).year
        record.update(
            {
                "hemisphere": hemisphere,
                "shape": "x".join(str(value) for value in raw_shape),
                "naxis": naxis,
                "bitpix": bitpix,
                "camera": camera,
                "calibrat": str(header.get("CALIBRAT", "")),
                "wcs_complete": all(key in header for key in WCS_KEYS),
            }
        )
        skip_reason = loader._should_skip_fits(header, path.name, False)
        if skip_reason:
            if skip_reason.startswith("non-longitudinal CONTENT="):
                record["status"] = "excluded_non_longitudinal"
                record["error"] = skip_reason
                return record
            raise ValueError(skip_reason)

        camera_upper = camera.upper()
        derived_folder = any(
            part.lower() in {"full", "ful"} for part in relative.parts[:-1]
        )
        if (
            bitpix == -64
            and plane_shape == (992, 992)
            and n_planes == 2
            and camera == "unknown"
            and derived_folder
        ):
            record["status"] = "excluded_non_smft_derived"
            record["error"] = (
                "explicitly excluded audited full/ful derivative: "
                "BITPIX=-64 and CAMERA missing"
            )
            return record
        known_layout = (
            bitpix == 8
            and (
                (plane_shape == (480, 640) and n_planes == 1)
                or (plane_shape in {(1000, 992), (992, 992)} and n_planes == 2)
            )
        ) or (
            bitpix == 16
            and plane_shape == (480, 640)
            and n_planes == 1
            and "PULNIX" in camera_upper
        ) or (
            bitpix == 32
            and plane_shape == (1000, 992)
            and n_planes == 2
            and "PULNIX" in camera_upper
        ) or (
            bitpix == 32
            and plane_shape == (992, 992)
            and n_planes == 2
            and "IMPERX" in camera_upper
        ) or (
            bitpix == 32
            and loader._is_hsos_schema_v2(header, plane_shape, n_planes)
        ) or (
            bitpix == 32
            and loader._is_hsos_schema_v3(header, plane_shape, n_planes)
        )
        if not known_layout:
            raise ValueError(
                "Unsupported FITS layout: "
                f"BITPIX={bitpix}, shape={raw_shape}, CAMERA={camera}"
            )
        record["instrument_epoch"] = loader._instrument_epoch(
            plane_shape,
            camera,
            acquisition_year,
            header=header,
            n_planes=n_planes,
        )
        record["status"] = "supported"
    except ValueError as exc:
        record["status"] = "unsupported"
        record["error"] = str(exc)
    except Exception as exc:  # pragma: no cover - real archive defense
        known_corrupt_day = fallback_year == 2015 and "20150815" in relative.parts
        if known_corrupt_day and "No SIMPLE card found" in repr(exc):
            record["status"] = "excluded_known_corrupt"
            record["error"] = "known 2015-08-15 file without SIMPLE card"
        else:
            record["status"] = "read_error"
            record["error"] = repr(exc)
    return record


def mark_duplicate_copies(frame: pd.DataFrame) -> pd.DataFrame:
    """Exclude numbered download copies when an equal-sized original exists."""
    frame = frame.copy()
    canonical = frame["file"].str.replace(
        r"\(\d+\)(?=\.fits?$)", "", regex=True
    ).str.lower()
    is_copy = frame["file"].str.contains(
        r"\(\d+\)\.fits?$", case=False, regex=True
    )
    for canonical_path in canonical[is_copy].unique():
        group = frame.loc[canonical == canonical_path]
        originals = group.loc[~is_copy.loc[group.index]]
        if originals.empty:
            continue
        original_size = int(originals.iloc[0]["file_size"])
        for index, row in group.loc[is_copy.loc[group.index]].iterrows():
            if int(row["file_size"]) == original_size:
                frame.at[index, "status"] = "duplicate_copy"
                frame.at[index, "error"] = (
                    "numbered copy skipped; equal-sized original exists"
                )
            else:
                frame.at[index, "status"] = "unsupported"
                frame.at[index, "error"] = (
                    "numbered copy conflicts with original file size"
                )
    return frame


def run_inventory(
    root: Path, start_year: int, end_year: int
) -> tuple[dict, pd.DataFrame]:
    """Inspect every polar candidate and return a JSON summary plus records."""
    archive_paths = discover_archive_fits(root, start_year, end_year)
    paths = [path for path in archive_paths if _is_polar_filename(path)]
    nonpolar_paths = [path for path in archive_paths if not _is_polar_filename(path)]
    records = [inspect_file(path, root) for path in paths]
    frame = pd.DataFrame(records, columns=RECORD_COLUMNS)
    frame = mark_duplicate_copies(frame)
    records = frame.to_dict(orient="records")

    status_counts = Counter(record["status"] for record in records)
    year_counts = Counter(record["year"] for record in records)
    archive_year_counts = Counter(
        int(path.relative_to(root).parts[0]) for path in archive_paths
    )
    nonpolar_year_counts = Counter(
        int(path.relative_to(root).parts[0]) for path in nonpolar_paths
    )
    hemisphere_counts = Counter(
        (record["year"], record["hemisphere"]) for record in records
    )
    signatures = Counter(
        (
            record["year"],
            record["shape"],
            record["bitpix"],
            record["camera"],
            record["calibrat"],
            record["wcs_complete"],
            record["instrument_epoch"],
            record["status"],
        )
        for record in records
    )
    years = list(range(start_year, end_year + 1))
    summary = {
        "polar_dir": str(root),
        "start_year": start_year,
        "end_year": end_year,
        "archive_fits_files": len(archive_paths),
        "candidate_files": len(paths),
        "nonpolar_fits_files": len(nonpolar_paths),
        "supported_files": status_counts["supported"],
        "unsupported_files": status_counts["unsupported"],
        "read_error_files": status_counts["read_error"],
        "duplicate_files": status_counts["duplicate_copy"],
        "excluded_files": sum(
            count
            for status, count in status_counts.items()
            if status.startswith("excluded_")
        ),
        "empty_years": [year for year in years if year_counts[year] == 0],
        "empty_polar_years": [year for year in years if year_counts[year] == 0],
        "years_with_archive_fits_but_no_polar_candidates": [
            year
            for year in years
            if archive_year_counts[year] > 0 and year_counts[year] == 0
        ],
        "years": [
            {
                "year": year,
                "archive_fits_files": archive_year_counts[year],
                "files": year_counts[year],
                "nonpolar_fits_files": nonpolar_year_counts[year],
                "north_files": hemisphere_counts[(year, "N")],
                "south_files": hemisphere_counts[(year, "S")],
            }
            for year in years
        ],
        "signatures": [
            {
                "year": key[0],
                "shape": key[1],
                "bitpix": key[2],
                "camera": key[3],
                "calibrat": key[4],
                "wcs_complete": key[5],
                "instrument_epoch": key[6],
                "status": key[7],
                "files": count,
            }
            for key, count in sorted(signatures.items(), key=lambda item: item[0])
        ],
    }
    return summary, frame


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--polar-dir", required=True, type=Path)
    parser.add_argument("--start-year", type=int, default=2014)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/inventory_2014_2026.json")
    )
    parser.add_argument(
        "--records-output",
        type=Path,
        default=Path("artifacts/inventory_2014_2026.csv"),
    )
    args = parser.parse_args()
    if args.start_year > args.end_year:
        parser.error("--start-year must not exceed --end-year")

    summary, records = run_inventory(
        args.polar_dir, args.start_year, args.end_year
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.records_output.parent.mkdir(parents=True, exist_ok=True)
    records.to_csv(args.records_output, index=False)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
