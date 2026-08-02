#!/usr/bin/env python3
"""Extract polar-cap magnetic field proxy from Huairou SMFT .dat magnetograms.

This script targets the 1987-2001 legacy `.dat` archive. Files are assumed to be
little-endian 16-bit signed integer images produced by the Huairou Solar
Observing Station (HSOS) 35 cm SMFT longitudinal magnetograph. The `.dat`
format lacks a standard FITS header; the first 128 bytes are treated as an
opaque binary prefix and skipped.

Polar-cap aperture (confirmed with the user):
- North polar files (NPL): top 100 rows for the 512x512 view, top 50 rows for
  the 256x512 view.
- South polar files (SPL): bottom 100 rows for the 512x512 view, bottom 50
  rows for the 256x512 view.
- Signed mean is reported (polarity preserved).
- Center quiet-reference box is subtracted to remove instrumental zero offset.
"""

from __future__ import annotations

import argparse
import json
import struct
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Format constants inferred from the D:\极区前兆 sample.
# ---------------------------------------------------------------------------
# The legacy .dat archive stores raw 16-bit signed integer images followed by
# a small ASCII trailer (usually 79 B, sometimes 128 B, occasionally absent).
# We therefore infer the image payload from the file size rather than parsing
# a fixed header length.
LARGE_SHAPE = (512, 512)   # "l*" files, payload = 524288 B
SMALL_SHAPE = (256, 512)   # "s*" files, payload = 262144 B
HUGE_SHAPE = (1024, 512)   # occasional large frames, payload = 1048576 B

LARGE_CAP_ROWS = 100
SMALL_CAP_ROWS = 50

LARGE_CENTER_BOX = (256, 256)
SMALL_CENTER_BOX = (128, 256)

MONTH_MAP = {
    name: idx
    for idx, name in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun",
         "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
def _month_to_int(month_str: str) -> int:
    """Convert three-letter English month name to month number."""
    value = MONTH_MAP.get(month_str.lower())
    if value is None:
        raise ValueError(f"Unrecognized month: {month_str!r}")
    return value


def _looks_like_year(name: str) -> bool:
    return len(name) == 4 and name.isdigit()


def _looks_like_month(name: str) -> bool:
    return name.lower() in MONTH_MAP


def _looks_like_day(name: str) -> bool:
    return name.isdigit() and 1 <= int(name) <= 31


def parse_path(path: Path, polar_dir: Path) -> dict | None:
    """Extract date/hemisphere/view metadata from the directory tree.

    The archive layout is not uniform: most files are under
    ``YYYY/MON/DD/<file>.dat``, but some are nested one or two extra levels
    (e.g. ``.../DD/1/<file>.dat`` or ``.../DD/DYY/16/<file>.dat``). We therefore
    scan the parent chain for year/month/day tokens in the correct order.
    """
    try:
        rel = path.relative_to(polar_dir)
        if len(rel.parts) < 4:
            return None

        parents = list(path.parents)
        # path.parents[0] is the immediate parent, [1] is its parent, etc.
        # We need to find day -> month -> year going upward.
        # Start from the immediate parent and look for the first token that
        # looks like a day, then continue upward for month and year.
        day_idx: int | None = None
        for idx, par in enumerate(parents):
            if _looks_like_day(par.name):
                day_idx = idx
                break
        if day_idx is None:
            return None

        month_idx: int | None = None
        for idx in range(day_idx + 1, len(parents)):
            if _looks_like_month(parents[idx].name):
                month_idx = idx
                break
        if month_idx is None:
            return None

        year_idx: int | None = None
        for idx in range(month_idx + 1, len(parents)):
            if _looks_like_year(parents[idx].name):
                year_idx = idx
                break
        if year_idx is None:
            return None

        year = int(parents[year_idx].name)
        month = _month_to_int(parents[month_idx].name)
        day = int(parents[day_idx].name)
    except Exception:
        return None

    name = path.stem.lower()
    if "npl" in name:
        hemisphere = "N"
    elif "spl" in name:
        hemisphere = "S"
    else:
        return None

    if name.startswith("l"):
        view_type = "L"
    elif name.startswith("s"):
        view_type = "S"
    elif name[0].isalpha():
        # Other single-letter instrument prefixes (e.g. V, P, Q, D, H) are
        # treated as large-view frames when their payload is 512x512.
        view_type = name[0].upper()
    else:
        return None

    # Last alphabetic character, if any, is treated as the observation sequence
    # code for that day (a, b, c, ...).
    sequence = name[-1] if name and name[-1].isalpha() else ""

    return {
        "year": year,
        "month": month,
        "day": day,
        "hemisphere": hemisphere,
        "view_type": view_type,
        "sequence": sequence,
    }


def _infer_image_shape(path: Path) -> tuple[int, int] | None:
    """Infer image shape from total file size.

    The archive contains a small ASCII trailer of variable length after the
    payload, so we match the total size against known payload sizes plus a
    reasonable trailer allowance.
    """
    size = path.stat().st_size
    candidates = [
        (HUGE_SHAPE, HUGE_SHAPE[0] * HUGE_SHAPE[1] * 2),
        (LARGE_SHAPE, LARGE_SHAPE[0] * LARGE_SHAPE[1] * 2),
        (SMALL_SHAPE, SMALL_SHAPE[0] * SMALL_SHAPE[1] * 2),
    ]
    max_trailer = 2048
    best: tuple[int, tuple[int, int]] | None = None
    for shape, payload_bytes in candidates:
        if size < payload_bytes:
            continue
        trailer = size - payload_bytes
        if trailer <= max_trailer:
            # Prefer the shape whose payload is closest to the file size.
            if best is None or trailer < best[0]:
                best = (trailer, shape)
    if best is None:
        return None
    return best[1]


def _read_int16_image(path: Path, shape: tuple[int, int]) -> np.ndarray:
    """Read the image payload as little-endian int16 and reshape."""
    data = path.read_bytes()
    payload_bytes = shape[0] * shape[1] * 2
    if len(data) < payload_bytes:
        raise ValueError(
            f"{path}: need at least {payload_bytes} bytes for shape {shape}, got {len(data)}"
        )
    payload = data[:payload_bytes]
    ints = struct.unpack("<" + "h" * (len(payload) // 2), payload)
    return np.array(ints, dtype=np.int16).reshape(shape)


def _select_aperture(arr: np.ndarray, hemisphere: str, view_type: str) -> dict:
    """Select polar-cap and quiet-reference boxes.

    Only the 512x512 large frames are used for the polar precursor. The
    256x512 small-view frames have been observed to contain saturated/invalid
    center regions in this archive and are rejected at the file-selection
    stage by default. Other single-letter prefixes (e.g. V, P, Q) are treated
    as large-view frames when their inferred payload is 512x512.
    """
    rows, cols = arr.shape
    if rows != LARGE_SHAPE[0] or cols != LARGE_SHAPE[1]:
        raise ValueError(
            f"Unsupported image shape {rows}x{cols}; only 512x512 is used"
        )

    cap_rows = LARGE_CAP_ROWS
    center_h, center_w = LARGE_CENTER_BOX

    if center_h > rows or center_w > cols:
        raise ValueError(
            f"Center box {center_h}x{center_w} does not fit in image {rows}x{cols}"
        )
    if cap_rows > rows:
        raise ValueError(
            f"Polar cap rows {cap_rows} exceed image height {rows}"
        )

    if hemisphere == "N":
        cap = arr[:cap_rows, :]
    else:
        cap = arr[rows - cap_rows :, :]

    r0 = (rows - center_h) // 2
    c0 = (cols - center_w) // 2
    center = arr[r0 : r0 + center_h, c0 : c0 + center_w]

    return {"cap": cap, "center": center}


def _signed_mean_valid(arr: np.ndarray) -> float:
    """Mean of non-zero pixels; zero is treated as missing/padding."""
    valid = arr != 0
    if valid.sum() == 0:
        raise ValueError("No valid (non-zero) pixels")
    return float(arr[valid].mean())


def _median_valid(arr: np.ndarray) -> float:
    """Median of non-zero pixels; robust to outliers in the center reference."""
    valid = arr[arr != 0]
    if valid.size == 0:
        raise ValueError("No valid (non-zero) pixels")
    return float(np.median(valid))


def extract_features(arr: np.ndarray, hemisphere: str, view_type: str) -> dict:
    """Compute signed and unsigned polar-cap proxies.

    Because NPL/SPL files in this archive do not reliably show opposite
    magnetic polarity in the top/bottom caps, the unsigned pixel-level absolute
    value (after subtracting a robust zero-offset estimate) is used as the
    primary polar-field strength proxy.
    """
    aperture = _select_aperture(arr, hemisphere, view_type)
    cap = aperture["cap"]
    center = aperture["center"]

    raw_mean = _signed_mean_valid(cap)
    center_mean = _signed_mean_valid(center)
    center_median = _median_valid(center)

    # Unsigned pixel-level absolute value after zero-offset correction.
    # We use the median rather than the mean to avoid overweighting strong
    # active-region fields that leak into the nominally polar cap rows.
    cap_valid = cap[cap != 0]
    field_mean_abs = float(np.median(np.abs(cap_valid - center_median)))

    valid_ratio = float((cap != 0).mean())

    return {
        "field_mean_raw": raw_mean,
        "field_mean_center": center_mean,
        "field_mean_corrected": raw_mean - center_mean,
        "field_mean_abs": field_mean_abs,
        "valid_pixel_ratio": valid_ratio,
    }


def process_file(path: Path, polar_dir: Path) -> dict:
    """Process a single .dat file and return a record dict.

    Raises ValueError or a similar exception on unrecoverable issues so that
    the caller can log the specific failure reason.
    """
    meta = parse_path(path, polar_dir)
    if meta is None:
        raise ValueError("Could not parse date/hemisphere/view from path")

    # Guard against files that are actually FITS despite the .dat extension.
    header_peek = path.read_bytes()[:6]
    if header_peek == b"SIMPLE":
        raise ValueError("File starts with FITS header despite .dat extension")

    shape = _infer_image_shape(path)
    if shape is None:
        raise ValueError(f"Could not infer image shape from file size")

    arr = _read_int16_image(path, shape)
    feats = extract_features(arr, meta["hemisphere"], meta["view_type"])

    return {
        "date": f"{meta['year']:04d}-{meta['month']:02d}-{meta['day']:02d}",
        "year": meta["year"],
        "month": meta["month"],
        "day": meta["day"],
        "hemisphere": meta["hemisphere"],
        "view_type": meta["view_type"],
        "sequence": meta["sequence"],
        **feats,
        "file_path": str(path),
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def aggregate_daily(df: pd.DataFrame) -> pd.DataFrame:
    """Average multiple observations of the same hemisphere on the same day."""
    grouped = (
        df.groupby(["date", "hemisphere"])
        .agg(
            field_mean_raw=("field_mean_raw", "mean"),
            field_mean_center=("field_mean_center", "mean"),
            field_mean_corrected=("field_mean_corrected", "mean"),
            field_mean_abs=("field_mean_abs", "mean"),
            valid_pixel_ratio=("valid_pixel_ratio", "mean"),
            n_obs=("sequence", "count"),
        )
        .reset_index()
    )
    grouped["date"] = pd.to_datetime(grouped["date"])
    return grouped


def aggregate_monthly(df_daily: pd.DataFrame) -> pd.DataFrame:
    """Average daily values into monthly means per hemisphere."""
    df_daily = df_daily.copy()
    df_daily["year"] = df_daily["date"].dt.year
    df_daily["month"] = df_daily["date"].dt.month
    grouped = (
        df_daily.groupby(["year", "month", "hemisphere"])
        .agg(
            field_mean_raw=("field_mean_raw", "mean"),
            field_mean_center=("field_mean_center", "mean"),
            field_mean_corrected=("field_mean_corrected", "mean"),
            field_mean_abs=("field_mean_abs", "mean"),
            n_days=("date", "nunique"),
        )
        .reset_index()
    )
    # Retain signed corrected mean as an exploratory diagnostic, but use the
    # unsigned pixel-level absolute value as the primary proxy.
    grouped["polarity_strength"] = grouped["field_mean_corrected"].abs()
    return grouped


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract polar-cap magnetic field proxy from Huairou SMFT .dat files."
    )
    parser.add_argument(
        "--polar-dir",
        required=True,
        help="Root directory containing YYYY/MON/DD/*.dat files.",
    )
    parser.add_argument(
        "--output",
        default="./data/huairou_polar_precursor_daily.csv",
        help="Output daily CSV path.",
    )
    parser.add_argument(
        "--monthly-output",
        default="./data/huairou_polar_precursor_monthly.csv",
        help="Output monthly CSV path.",
    )
    parser.add_argument(
        "--errors-output",
        default="./artifacts/polar_processing_errors.jsonl",
        help="Path for error log.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel workers.",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=1987,
        help="Start year (inclusive).",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=2001,
        help="End year (inclusive).",
    )
    parser.add_argument(
        "--include-small-view",
        action="store_true",
        help="Also process small-view 's*' frames (default: skipped; their center regions are often invalid).",
    )
    args = parser.parse_args()

    polar_dir = Path(args.polar_dir)
    out_path = Path(args.output)
    monthly_path = Path(args.monthly_output)
    errors_path = Path(args.errors_output)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    monthly_path.parent.mkdir(parents=True, exist_ok=True)
    errors_path.parent.mkdir(parents=True, exist_ok=True)

    candidates = []
    for year in range(args.start_year, args.end_year + 1):
        year_dir = polar_dir / str(year)
        if not year_dir.exists():
            continue
        for path in year_dir.rglob("*.dat"):
            name = path.stem.lower()
            if "npl" not in name and "spl" not in name:
                continue
            # Default to large-view frames only; the 256x512 small-view frames
            # in this archive have saturated/invalid center regions.
            if name.startswith("s") and not args.include_small_view:
                continue
            candidates.append(path)

    print(
        f"Found {len(candidates)} NPL/SPL .dat candidate files for "
        f"{args.start_year}-{args.end_year}"
    )

    records: list[dict] = []
    errors: list[dict] = []

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process_file, p, polar_dir): p for p in candidates}
        for completed_idx, future in enumerate(as_completed(futures), start=1):
            path = futures[future]
            try:
                rec = future.result()
                records.append(rec)
            except Exception as exc:  # pragma: no cover - defensive
                errors.append({"file": str(path), "error": repr(exc)})
            if completed_idx % 500 == 0:
                print(f"Processed {completed_idx}/{len(candidates)} files")

    if errors:
        with errors_path.open("w", encoding="utf-8") as fh:
            for err in errors:
                fh.write(json.dumps(err, ensure_ascii=False) + "\n")
        print(f"Logged {len(errors)} skipped/error files to {errors_path}")

    if not records:
        print("No valid polar-field records extracted.")
        return

    df = pd.DataFrame(records)
    df_daily = aggregate_daily(df)
    df_daily.to_csv(out_path, index=False)
    print(f"Wrote {len(df_daily)} daily rows to {out_path}")

    df_monthly = aggregate_monthly(df_daily)
    df_monthly.to_csv(monthly_path, index=False)
    print(f"Wrote {len(df_monthly)} monthly rows to {monthly_path}")


if __name__ == "__main__":
    main()
